from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
from urllib.parse import parse_qs

from novel.core.auditing import ChapterAuditOptions, audit_chapter, load_audit_provider
from novel.core.drafting import ChapterDraftingOptions, load_drafting_provider, write_chapter_draft
from novel.core.exporting import MarkdownExportOptions, export_markdown, parse_chapter_selector
from novel.core.inspection import format_canon, get_project_status
from novel.core.io import load_json, load_json_model
from novel.core.planning import ChapterPlanningOptions, load_planning_provider, plan_chapter
from novel.core.polishing import ChapterPolishingOptions, load_polishing_provider, polish_chapter
from novel.core.schemas import ChapterPlan
from novel.core.workflow import GenerateChapterOptions, generate_chapter


APIResponse = tuple[int, dict[str, object]]


def handle_api_request(
    method: str,
    path: str,
    query_string: str = "",
    body: bytes | str | None = None,
) -> APIResponse:
    query = {key: values[-1] for key, values in parse_qs(query_string).items()}
    try:
        if method == "GET" and path == "/api/projects":
            return 200, {"ok": True, "projects": _list_projects(Path(query.get("root", ".")))}
        if method == "GET" and path == "/api/project/status":
            root = _root_from_query(query)
            status = get_project_status(root)
            payload = asdict(status)
            payload["latest_run_log"] = str(status.latest_run_log) if status.latest_run_log else None
            return 200, {"ok": True, "status": payload}
        if method == "GET" and path == "/api/canon":
            return 200, {"ok": True, "summary": format_canon(_root_from_query(query))}
        if method == "GET" and path == "/api/chapters":
            return 200, {"ok": True, "chapters": _list_chapters(_root_from_query(query))}
        if method == "GET" and path == "/api/chapter-file":
            payload = _read_chapter_file(_root_from_query(query), query)
            payload["ok"] = True
            return 200, payload

        data = _json_body(body)
        if method == "POST" and path == "/api/plan-chapter":
            return 200, _plan_chapter(data)
        if method == "POST" and path == "/api/write-chapter":
            return 200, _write_chapter(data)
        if method == "POST" and path == "/api/polish-chapter":
            return 200, _polish_chapter(data)
        if method == "POST" and path == "/api/audit-chapter":
            return 200, _audit_chapter(data)
        if method == "POST" and path == "/api/export/markdown":
            return 200, _export_markdown(data)
        if method == "POST" and path == "/api/generate-chapter":
            return 200, _generate_chapter(data)
    except Exception as exc:
        return 400, {"ok": False, "error": _safe_error(exc)}
    return 404, {"ok": False, "error": "not found"}


def _plan_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    provider = load_planning_provider(
        root,
        str(data.get("provider") or "mock"),
        chapter_number=chapter_number,
    )
    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=chapter_number,
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {
        "ok": True,
        "plan_json_path": str(result.plan_json_path),
        "plan_markdown_path": str(result.plan_markdown_path),
        "validation_ok": result.validation_report.ok,
    }


def _write_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_drafting_provider(root, str(data.get("provider") or "mock"))
    result = write_chapter_draft(
        ChapterDraftingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {"ok": True, "draft_path": str(result.draft_path), "warnings": list(result.warnings)}


def _polish_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    provider = load_polishing_provider(root, str(data.get("provider") or "mock"))
    result = polish_chapter(
        ChapterPolishingOptions(
            root=root,
            chapter_number=_chapter_number(data),
            instruction=_optional_string(data.get("instruction")),
            force=bool(data.get("force")),
            style_note=_optional_string(data.get("style_note")),
            keep_length=bool(data.get("keep_length")),
            edit_mode=str(data.get("edit_mode") or "normal"),  # type: ignore[arg-type]
        ),
        provider,
    )
    return {"ok": True, "polished_path": str(result.polished_path), "warnings": list(result.warnings)}


def _audit_chapter(data: dict[str, object]) -> dict[str, object]:
    root = _root_from_body(data)
    chapter_number = _chapter_number(data)
    audited_file = str(data.get("audited_file") or "polished.md")
    provider = load_audit_provider(
        root,
        str(data.get("provider") or "mock"),
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
            focus=tuple(str(item) for item in data.get("focus", []) if item)  # type: ignore[union-attr]
            if isinstance(data.get("focus"), list)
            else (),
            audited_file=audited_file,  # type: ignore[arg-type]
            use_search_context=bool(data.get("use_search_context")),
        ),
        provider,
    )
    return {
        "ok": True,
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
        "ok": True,
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
            provider_name=str(data.get("provider") or "mock"),
            target_words=_optional_int(data.get("target_words")),
            style_note=_optional_string(data.get("style_note")),
            skip_polish=bool(data.get("skip_polish")),
            skip_audit=bool(data.get("skip_audit")),
            stop_after=_optional_string(data.get("stop_after")),  # type: ignore[arg-type]
        )
    )
    return {
        "ok": True,
        "message": result.message,
        "run_log_path": str(result.run_log_path),
        "status": result.run_log.status,
    }


def _list_projects(root: Path) -> list[dict[str, str]]:
    base = root.expanduser().resolve()
    candidates = []
    if (base / "project.yaml").exists():
        candidates.append(base)
    if base.exists() and base.is_dir():
        candidates.extend(path for path in base.iterdir() if (path / "project.yaml").exists())
    return [{"path": str(path)} for path in sorted(set(candidates))]


def _list_chapters(root: Path) -> list[dict[str, object]]:
    chapters_dir = root / "memory" / "chapters"
    chapters: list[dict[str, object]] = []
    if not chapters_dir.exists():
        return chapters
    for child in sorted(chapters_dir.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        chapter_number = int(child.name)
        entry: dict[str, object] = {
            "chapter_number": chapter_number,
            "has_plan": (child / "plan.json").exists(),
            "has_draft": (child / "draft.md").exists(),
            "has_polished": (child / "polished.md").exists(),
            "has_audit": (child / "audit.json").exists(),
            "status": None,
            "title": None,
            "audit_status": None,
        }
        _merge_plan_metadata(child / "plan.json", entry)
        _merge_polished_metadata(child / "polished.md", entry)
        if (child / "audit.json").exists():
            data = load_json(child / "audit.json")
            if isinstance(data, dict):
                entry["audit_status"] = data.get("overall_status")
        chapters.append(entry)
    return chapters


def _merge_plan_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    try:
        plan = load_json_model(path, ChapterPlan)
    except Exception:
        return
    entry["title"] = plan.title


def _read_chapter_file(root: Path, query: dict[str, str]) -> dict[str, object]:
    chapter_number = int(query.get("chapter", "0"))
    file_type = query.get("file", "")
    mapping = {
        "plan": "plan.json",
        "draft": "draft.md",
        "polished": "polished.md",
        "audit": "audit.json",
    }
    if chapter_number < 1 or file_type not in mapping:
        raise ValueError("invalid chapter or file type")
    path = root / "memory" / "chapters" / f"{chapter_number:03d}" / mapping[file_type]
    if not path.exists():
        return {"path": str(path), "content": "", "exists": False}
    return {"path": str(path), "content": path.read_text(encoding="utf-8"), "exists": True}


def _merge_polished_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return
    try:
        import yaml

        _, metadata_text, _ = content.split("---\n", 2)
        metadata = yaml.safe_load(metadata_text) or {}
    except Exception:
        return
    if isinstance(metadata, dict):
        entry["status"] = metadata.get("status")
        entry["title"] = metadata.get("title")


def _json_body(body: bytes | str | None) -> dict[str, object]:
    if not body:
        return {}
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def _root_from_query(query: dict[str, str]) -> Path:
    return Path(query.get("path") or ".").expanduser().resolve()


def _root_from_body(data: dict[str, object]) -> Path:
    return Path(str(data.get("path") or ".")).expanduser().resolve()


def _chapter_number(data: dict[str, object]) -> int:
    value = int(data.get("chapter_number") or 0)
    if value < 1:
        raise ValueError("chapter_number must be a positive integer")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", message)
    redacted = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", redacted)
    for key, value in __import__("os").environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted
