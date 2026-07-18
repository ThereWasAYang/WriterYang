from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from novel.core.artifact_store import ArtifactStore
from novel.core.contracts import ArtifactKind
from novel.core.io import (
    atomic_write_model_json,
    atomic_write_text,
    backup_if_exists,
    load_json,
    load_json_model,
)
from novel.core.schemas import RevisionLog, RevisionRecord
from novel.core.timeutil import new_request_id, utc_now

STYLE_GUIDE_RELATIVE_PATH = "memory/style_guide.md"


class WorkspaceMutationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StyleGuideSaveResult:
    path: Path
    backup_path: Path | None
    content: str


@dataclass(frozen=True)
class ChapterCandidateSaveResult:
    output_path: Path
    revision_log_path: Path
    record: RevisionRecord


def save_style_guide(root: Path, content: str) -> StyleGuideSaveResult:
    root = root.expanduser().resolve()
    normalized = content.rstrip() + "\n"
    if not content.strip():
        raise WorkspaceMutationError("invalid_request", "content must not be empty")
    path = root / STYLE_GUIDE_RELATIVE_PATH
    backup_path = backup_if_exists(path, reason="style_guide")
    atomic_write_text(path, normalized)
    return StyleGuideSaveResult(path=path, backup_path=backup_path, content=normalized)


def save_chapter_candidate(
    root: Path,
    *,
    chapter_number: int,
    target: str,
    source_file: str,
    content: str,
    instruction: str | None,
) -> ChapterCandidateSaveResult:
    root = root.expanduser().resolve()
    if target not in {"draft", "polished"}:
        raise WorkspaceMutationError("invalid_request", "target must be draft or polished")
    if not content.strip():
        raise WorkspaceMutationError("invalid_request", "content must not be empty")
    if source_file != f"{target}.md":
        raise WorkspaceMutationError("forbidden_file", "source_file is not an editable working chapter file")
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    source_path = chapter_dir / source_file
    if not source_path.exists():
        raise FileNotFoundError(f"{source_file} does not exist")
    if _is_archived_chapter(root, chapter_number):
        raise WorkspaceMutationError(
            "archived_content_read_only",
            "archived chapter content is read-only; create a new revision session instead",
        )
    output_ref = ArtifactStore(root).create(
        chapter_number=chapter_number,
        kind=ArtifactKind.CANDIDATE,
        content=(content.rstrip() + "\n").encode("utf-8"),
        suffix=".md",
        authority="user",
        policy_version="user-editor-v3",
    )
    output_path = root / output_ref.path
    record = RevisionRecord(
        id=new_request_id("revision"),
        chapter_number=chapter_number,
        target=target,  # type: ignore[arg-type]
        source_file=source_file,
        output_file=output_ref.path,
        instruction=instruction or "Web editor save as immutable candidate",
        from_audit=False,
        audit_file="audit.json" if (chapter_dir / "audit.json").exists() else None,
        audit_issue_ids=[],
        created_at=utc_now(),
        provider="web_editor",
    )
    log_path = chapter_dir / "revision_log.json"
    _append_revision_log(log_path, chapter_number, record)
    return ChapterCandidateSaveResult(
        output_path=output_path,
        revision_log_path=log_path,
        record=record,
    )


def _append_revision_log(path: Path, chapter_number: int, record: RevisionRecord) -> None:
    if path.exists():
        log = load_json_model(path, RevisionLog)
        if log.chapter_number != chapter_number:
            raise WorkspaceMutationError("invalid_revision_log", "revision_log chapter_number does not match")
    else:
        log = RevisionLog(chapter_number=chapter_number, revisions=[])
    updated = log.model_copy(update={"revisions": [*log.revisions, record]})
    backup_if_exists(path, reason="revision_log")
    atomic_write_model_json(path, updated)


def _is_archived_chapter(root: Path, chapter_number: int) -> bool:
    archive_dir = root / "memory" / "archive"
    if not archive_dir.exists():
        return False
    chapter_fragment = f"chapters/{chapter_number:03d}/"
    for manifest_path in archive_dir.glob("session_*/manifest.json"):
        try:
            data = load_json(manifest_path)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        entries = data.get("entries")
        if isinstance(entries, list) and any(chapter_fragment in str(item) for item in entries):
            return True
        if chapter_fragment in json.dumps(data, ensure_ascii=False):
            return True
    return False
