from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.providers import MockProvider
from novel.core.revision import ChapterRevisionOptions, RevisionError, revise_chapter
from novel.core.schemas import RevisionLog
from novel.core.session import SessionActionOptions, SessionRunOptions, SessionStartOptions, approve_outline, run_session, start_session
from novel.core.workspace import InitOptions, init_workspace


def test_revise_chapter_creates_immutable_candidate(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    original = polished_path.read_text(encoding="utf-8")

    result = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, instruction="加强悬疑感"),
        MockProvider(fake_response="修订后的正文保留广播与车票，并让语气更克制。"),
    )

    assert result.output_path.parent.name == "candidates"
    assert result.output_path.name.startswith("candidate_art_")
    assert "status: polished_revision" in result.output_path.read_text(encoding="utf-8")
    assert polished_path.read_text(encoding="utf-8") == original
    assert not list(polished_path.parent.glob("polished.v*.md"))


def test_revise_chapter_missing_style_guide_injects_chinese_fallback(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    (root / "memory" / "style_guide.md").unlink()
    provider = MockProvider(fake_response="修订后的正文保留广播与车票，并让语气更克制。")

    result = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, instruction="压低解释性文字"),
        provider,
    )

    assert "memory/style_guide.md is missing" in result.warnings[0]
    prompt = provider.requests[0].user_prompt
    assert "# 文风设置" in prompt
    assert "## 整体风格" in prompt
    assert "# Style Guide" not in prompt


def test_revise_chapter_from_audit_records_immutable_candidate(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    result = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, from_audit=True),
        MockProvider(fake_response="根据审核意见修订后的正文。"),
    )

    log = _revision_log(root)
    assert result.output_path.is_file()
    assert log.revisions[0].from_audit is True
    assert log.revisions[0].audit_file == "audit.json"
    assert log.revisions[0].output_file == result.output_path.relative_to(root).as_posix()


def test_revise_chapter_requires_instruction_or_audit(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)

    with pytest.raises(RevisionError, match="provide --instruction"):
        revise_chapter(
            ChapterRevisionOptions(root=root, chapter_number=1),
            MockProvider(fake_response="不会使用"),
        )


def test_revision_log_appends_distinct_immutable_candidates(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    provider = MockProvider(fake_response="修订后的正文。")

    first = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, instruction="第一轮修订"),
        provider,
    )
    second = revise_chapter(
        ChapterRevisionOptions(root=root, chapter_number=1, instruction="第二轮修订"),
        provider,
    )

    log = _revision_log(root)
    assert len(log.revisions) == 2
    assert first.output_path != second.output_path
    assert all(item.source_file == "polished.md" for item in log.revisions)
    assert all("/candidates/candidate_art_" in item.output_file for item in log.revisions)


def test_revise_chapter_search_context_protects_hidden_truth(tmp_path: Path) -> None:
    root = _workspace_with_generated_chapter(tmp_path)
    provider = MockProvider(fake_response="修订后的正文仍保留广播与车票，只让林澈意识到雨夜还有未解之事。")

    result = revise_chapter(
        ChapterRevisionOptions(
            root=root,
            chapter_number=1,
            instruction="保持悬疑",
            use_search_context=True,
        ),
        provider,
    )

    prompt = provider.requests[0].user_prompt
    assert "Context bundle" in prompt
    assert "旧车站在特定雨夜会短暂连接过去的时间层" not in prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


def _workspace_with_generated_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    started = start_session(
        SessionStartOptions(root=root, user_intent="写第1章", chapter_range=(1,), provider_name="mock")
    )
    approve_outline(SessionActionOptions(root=root, session_id=started.session.session_id))
    result = run_session(SessionRunOptions(root=root, session_id=started.session.session_id, provider_name="mock"))
    assert result.session.content_status == "needs_user_review"
    return root


def _revision_log(root: Path) -> RevisionLog:
    path = root / "memory" / "chapters" / "001" / "revision_log.json"
    assert path.is_file()
    return RevisionLog.model_validate(json.loads(path.read_text(encoding="utf-8")))
