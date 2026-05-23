from __future__ import annotations

import json
from pathlib import Path

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request
from novel.web_server import index_html


def test_api_status_endpoint(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "GET",
        "/api/project/status",
        f"path={root}",
        None,
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["status"]["title"] == "雨夜旧车站"  # type: ignore[index]
    assert payload["status"]["inspiration_exists"] is True  # type: ignore[index]


def test_api_response_does_not_leak_api_key_values(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    monkeypatch.setenv("WRITER_API_KEY", "sk-test-secret-never-return")

    status, payload = handle_api_request(
        "GET",
        "/api/project/status",
        f"path={root}",
        None,
    )

    assert status == 200
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "sk-test-secret-never-return" not in serialized
    assert "api_key_env" not in serialized


def test_frontend_basic_render() -> None:
    html = index_html()

    assert 'id="projectPath"' in html
    assert 'id="instruction"' in html
    assert 'id="chapterList"' in html
    assert 'id="planChapter"' in html
    assert 'id="writeChapter"' in html
    assert 'id="polishChapter"' in html
    assert 'id="auditChapter"' in html
    assert 'id="exportMarkdown"' in html
    assert "fetch(" in html


def test_api_triggers_mock_generation_workflow(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "POST",
        "/api/generate-chapter",
        "",
        json.dumps(
            {
                "path": str(root),
                "chapter_number": 1,
                "provider": "mock",
                "instruction": "保持悬疑，不要揭示真相",
            }
        ),
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["status"] == "completed"
    chapter_dir = root / "memory" / "chapters" / "001"
    assert (chapter_dir / "plan.json").is_file()
    assert (chapter_dir / "draft.md").is_file()
    assert (chapter_dir / "polished.md").is_file()
    assert (chapter_dir / "audit.json").is_file()

    chapters_status, chapters_payload = handle_api_request(
        "GET",
        "/api/chapters",
        f"path={root}",
        None,
    )
    assert chapters_status == 200
    assert chapters_payload["chapters"][0]["has_audit"] is True  # type: ignore[index]


def _workspace_ready_for_generation(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    return root
