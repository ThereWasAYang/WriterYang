# mypy: ignore-errors
# ruff: noqa: F403,F405
from __future__ import annotations

from .deps import *
from .common import *

def _plan_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    provider = load_planning_provider(
        root,
        str(data.get("provider") or "config"),
        chapter_number=chapter_number,
    )
    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "plan_json_path": str(result.plan_json_path),
        "plan_markdown_path": str(result.plan_markdown_path),
        "validation_ok": result.validation_report.ok,
    }


def _write_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_drafting_provider(root, str(data.get("provider") or "config"))
    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {"draft_path": str(result.draft_path), "warnings": list(result.warnings)}


def _polish_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_polishing_provider(root, str(data.get("provider") or "config"))
    result = polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            style_note=_optional_string(data.get("style_note")),
            keep_length=bool(data.get("keep_length")),
            edit_mode=str(data.get("edit_mode") or "normal"),  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {"polished_path": str(result.polished_path), "warnings": list(result.warnings)}


def _audit_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    audited_file = str(data.get("audited_file") or "polished.md")
    provider = load_audit_provider(
        root,
        str(data.get("provider") or "config"),
        chapter_number=chapter_number,
        audited_file=audited_file,  # type: ignore[arg-type]
    )
    result = audit_chapter(
        ChapterAuditOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            strict=bool(data.get("strict")),
            focus=_audit_focus(data.get("focus")),
            audited_file=audited_file,  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
            use_vector_context=_vector_context_mode(data),
        ),
        provider,
    )
    return {
        "audit_path": str(result.audit_path),
        "overall_status": result.report.overall_status,
        "issue_count": len(result.report.issues),
        "warnings": list(result.warnings),
    }


def _export_markdown(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = export_markdown(
        MarkdownExportOptions(
            root=root,
            chapters=parse_chapter_selector(_optional_string(data.get("chapters"))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            include_unaccepted=bool(data.get("include_unaccepted")),
            output_path=Path(str(data["output"])) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        )
    )
    return {
        "output_path": str(result.output_path),
        "manifest_path": str(result.manifest_path),
        "chapters": list(result.exported_chapters),
        "warnings": list(result.warnings),
    }


def _export_docx(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = export_docx(
        DocxExportOptions(
            root=root,
            chapters=parse_chapter_selector(_optional_string(data.get("chapters"))),
            from_chapter=_optional_int(data.get("from_chapter")),
            to_chapter=_optional_int(data.get("to_chapter")),
            include_unaccepted=bool(data.get("include_unaccepted")),
            output_path=Path(str(data["output"])) if data.get("output") else None,
            title=_optional_string(data.get("title")),
            force=bool(data.get("force")),
        )
    )
    return {
        "output_path": str(result.output_path),
        "manifest_path": str(result.manifest_path),
        "chapters": list(result.exported_chapters),
        "warnings": list(result.warnings),
    }


def _generate_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    result = generate_chapter(
        GenerateChapterOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            provider_name=_provider_name(data.get("provider")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            polish_mode=_polish_mode(data),
            skip_polish=bool(data.get("skip_polish")),
            skip_audit=bool(data.get("skip_audit")),
            stop_after=_optional_string(data.get("stop_after")),  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context", True)),
            use_vector_context=_vector_context_mode(data),
        )
    )
    return {
        "message": result.message,
        "run_log_path": str(result.run_log_path),
        "status": result.run_log.status,
    }


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


def _load_web_chapter_memory_provider(root: Path, data: dict[str, object], chapter_number: int):
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
