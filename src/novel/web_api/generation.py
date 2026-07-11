from __future__ import annotations

from .deps import (
    Path,
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
    parse_chapter_selector,
    load_json_model,
    ChapterMemory,
)
from novel.core.contracts import ProductionExportCommand
from novel.core.providers import ModelProvider

from .common import (
    WebAPIError,
    _require_workspace,
    _root_from_body,
    _chapter_number,
    _optional_string,
    _optional_int,
    _provider_name,
    _truthy,
    _relative,
    _dispatch_web_command,
)


def _export_markdown(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        ProductionExportCommand(
            type="export.markdown",
            chapters=list(parse_chapter_selector(_optional_string(data.get("chapters")))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            output_path=str(data["output"]) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        ),
        confirmed=True,
    )


def _export_docx(data: dict[str, object]) -> dict[str, object]:
    return _dispatch_web_command(
        data,
        ProductionExportCommand(
            type="export.docx",
            chapters=list(parse_chapter_selector(_optional_string(data.get("chapters")))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            output_path=str(data["output"]) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        ),
        confirmed=True,
    )


def _chapter_memory_generate(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    chapter_number = _chapter_number(data)
    force = True if "force" not in data else _truthy(data.get("force"))
    provider, provider_warnings = _load_web_chapter_memory_provider(root, data, chapter_number)
    result = generate_chapter_memory(
        ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=force),
        provider,
        initial_warnings=tuple(provider_warnings),
    )
    return _chapter_memory_result_payload(root, result.memory_path, result.memory, result.warnings)


def _chapter_memory_rebuild(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    _require_workspace(root)
    mode = _optional_string(data.get("mode")) or "missing_or_stale"
    if mode not in {"missing", "missing_or_stale", "all"}:
        raise WebAPIError("invalid_request", "mode must be missing, missing_or_stale, or all", status=400)
    written: list[dict[str, object]] = []
    skipped: list[int] = []
    warnings: list[str] = []
    for chapter_number in accepted_chapter_numbers(root):
        path = chapter_memory_path(root, chapter_number)
        should_generate = mode == "all" or not path.exists()
        if not should_generate and mode == "missing_or_stale":
            try:
                memory = load_json_model(path, ChapterMemory)
                should_generate = bool(chapter_memory_freshness_warnings(root, memory))
            except Exception:
                should_generate = True
        if not should_generate:
            skipped.append(chapter_number)
            continue
        try:
            provider, provider_warnings = _load_web_chapter_memory_provider(root, data, chapter_number)
            result = generate_chapter_memory(
                ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=True),
                provider,
                initial_warnings=tuple(provider_warnings),
            )
            written.append(_chapter_memory_result_payload(root, result.memory_path, result.memory, result.warnings))
            warnings.extend(f"chapter {chapter_number}: {warning}" for warning in result.warnings)
        except Exception as exc:
            warnings.append(f"chapter {chapter_number}: {exc}")
    return {
        "mode": mode,
        "written": written,
        "skipped": skipped,
        "warnings": warnings,
    }


def _load_web_chapter_memory_provider(
    root: Path,
    data: dict[str, object],
    chapter_number: int,
) -> tuple[ModelProvider | None, list[str]]:
    warnings: list[str] = []
    try:
        return (
            load_chapter_memory_provider(root, _provider_name(data.get("provider")), chapter_number=chapter_number),
            warnings,
        )
    except Exception as exc:
        warnings.append(f"chapter memory provider unavailable; using deterministic fallback: {exc}")
        return None, warnings


def _chapter_memory_result_payload(
    root: Path,
    memory_path: Path,
    memory: ChapterMemory,
    warnings: tuple[str, ...],
) -> dict[str, object]:
    return {
        "chapter_number": memory.chapter_number,
        "memory_path": str(memory_path),
        "relative_path": _relative(root, memory_path),
        "generation_status": memory.generation_status,
        "warnings": list(warnings),
    }
