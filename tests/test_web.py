from __future__ import annotations

import json
import errno
from datetime import datetime, timezone
from pathlib import Path

from novel.cli import _resolve_web_port
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import atomic_write_model_json
from novel.core.schemas import AuditReport, CreationSession
from novel.core.session import SessionResult
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request
from novel.web_server import WebServerError, index_html, run_web_server


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
    assert payload["data"]["status"]["title"] == "雨夜旧车站"  # type: ignore[index]
    assert payload["data"]["status"]["inspiration_exists"] is True  # type: ignore[index]
    assert "error" not in payload


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


def test_api_error_response_has_stable_shape_and_redacts_keys(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "super-secret-value")

    status, payload = handle_api_request(
        "GET",
        "/api/read-file",
        "path=/not-a-workspace&file=super-secret-value",
        None,
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_project"  # type: ignore[index]
    assert payload["error"]["request_id"]  # type: ignore[index]
    assert "super-secret-value" not in json.dumps(payload, ensure_ascii=False)


def test_api_file_tree_excludes_env_and_search_index(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    (root / ".env.real").write_text("SHOULD_NOT_APPEAR=1\n", encoding="utf-8")
    (root / "memory" / "search_index.json").write_text("{}", encoding="utf-8")
    (root / "memory" / "chapters" / "001").mkdir(parents=True)
    (root / "memory" / "chapters" / "001" / "draft.md").write_text("draft", encoding="utf-8")

    status, payload = handle_api_request("GET", "/api/file-tree", f"path={root}", None)

    assert status == 200
    paths = [item["path"] for item in payload["data"]["files"]]  # type: ignore[index]
    assert "memory/chapters/001/draft.md" in paths
    assert ".env.real" not in paths
    assert "memory/search_index.json" not in paths


def test_api_read_file_uses_workspace_whitelist(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")

    ok_status, ok_payload = handle_api_request(
        "GET",
        "/api/read-file",
        f"path={root}&file=project.yaml",
        None,
    )
    bad_status, bad_payload = handle_api_request(
        "GET",
        "/api/read-file",
        f"path={root}&file=.env",
        None,
    )

    assert ok_status == 200
    assert ok_payload["data"]["exists"] is True  # type: ignore[index]
    assert bad_status == 403
    assert bad_payload["error"]["code"] == "forbidden_file"  # type: ignore[index]


def test_api_provider_config_is_read_only_and_does_not_leak_values(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-never-return")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-secret-never-return")

    status, payload = handle_api_request("GET", "/api/provider-config", f"path={root}", None)

    assert status == 200
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "sk-test-secret-never-return" not in serialized
    assert "dashscope-secret-never-return" not in serialized
    assert "OPENAI_API_KEY" in serialized
    env_entries = payload["data"]["agents"]["env"]  # type: ignore[index]
    assert any(item["name"] == "OPENAI_API_KEY" and item["exists"] is True for item in env_entries)


def test_api_runs_and_state_timeline_endpoints(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    handle_api_request(
        "POST",
        "/api/generate-chapter",
        "",
        json.dumps({"path": str(root), "chapter_number": 1, "provider": "mock"}),
    )

    runs_status, runs_payload = handle_api_request("GET", "/api/runs", f"path={root}", None)
    state_status, state_payload = handle_api_request("GET", "/api/state-timeline", f"path={root}", None)

    assert runs_status == 200
    assert runs_payload["data"]["run_logs"]  # type: ignore[index]
    assert "provider_usage" in runs_payload["data"]  # type: ignore[operator]
    usage_status, usage_payload = handle_api_request("GET", "/api/usage", f"path={root}", None)
    assert usage_status == 200
    assert usage_payload["data"]["usage"]["total"]["call_count"] == 0  # type: ignore[index]
    assert state_status == 200
    assert "timeline_event_count" in state_payload["data"]["summary"]  # type: ignore[index]
    assert "visual" in state_payload["data"]  # type: ignore[operator]
    assert "timeline_events" in state_payload["data"]["visual"]  # type: ignore[index]


def test_api_diff_endpoint_returns_unified_diff(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "polished.md").write_text("旧文本\n", encoding="utf-8")
    (chapter_dir / "polished.v2.md").write_text("新文本\n", encoding="utf-8")

    status, payload = handle_api_request(
        "GET",
        "/api/diff",
        f"path={root}&left=memory/chapters/001/polished.md&right=memory/chapters/001/polished.v2.md",
        None,
    )

    assert status == 200
    assert "--- memory/chapters/001/polished.md" in payload["data"]["diff"]  # type: ignore[index]
    assert "+新文本" in payload["data"]["diff"]  # type: ignore[index]


def test_api_save_chapter_file_creates_version_and_revision_log(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    _write_chapter_file(root, "polished.md", "原始正文")

    status, payload = handle_api_request(
        "POST",
        "/api/save-chapter-file",
        "",
        json.dumps(
            {
                "path": str(root),
                "chapter_number": 1,
                "target": "polished",
                "source_file": "polished.md",
                "content": "---\nchapter_number: 1\ntitle: 雨夜旧车站\nstatus: polished_revision\n---\n\n新正文\n",
            }
        ),
    )

    assert status == 200
    assert payload["data"]["relative_path"] == "memory/chapters/001/polished.v2.md"  # type: ignore[index]
    assert (root / "memory" / "chapters" / "001" / "polished.v2.md").is_file()
    log = json.loads((root / "memory" / "chapters" / "001" / "revision_log.json").read_text(encoding="utf-8"))
    assert log["revisions"][0]["provider"] == "web_editor"


def test_api_save_accepted_chapter_does_not_overwrite_base_file(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    polished_path = _write_chapter_file(root, "polished.md", "原始正文")
    original = polished_path.read_text(encoding="utf-8")

    status, payload = handle_api_request(
        "POST",
        "/api/save-chapter-file",
        "",
        json.dumps(
            {
                "path": str(root),
                "chapter_number": 1,
                "target": "polished",
                "source_file": "polished.md",
                "content": original.replace("原始正文", "新版本正文"),
            }
        ),
    )

    assert status == 200
    assert polished_path.read_text(encoding="utf-8") == original
    assert payload["data"]["relative_path"] == "memory/chapters/001/polished.v2.md"  # type: ignore[index]


def test_api_audit_annotations_locate_evidence_quote(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    _write_chapter_file(root, "polished.md", "林澈突然知道了隐藏真相。")
    audit_path = root / "memory" / "chapters" / "001" / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "overall_status": "needs_revision",
                "summary": "发现问题。",
                "issues": [
                    {
                        "id": "audit_issue_001",
                        "severity": "high",
                        "type": "premature_reveal",
                        "description": "角色知道了不该知道的信息。",
                        "evidence": [{"source": "polished.md", "quote": "突然知道了隐藏真相"}],
                        "suggested_fix": "改成怀疑而非知道。",
                    }
                ],
                "passed_checks": [],
                "created_at": "2026-05-22T00:00:00Z",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    status, payload = handle_api_request(
        "GET",
        "/api/audit-annotations",
        f"path={root}&chapter=1&file=polished.md",
        None,
    )

    assert status == 200
    issue = payload["data"]["issues"][0]  # type: ignore[index]
    assert issue["matches"][0]["matched"] is True
    assert issue["matches"][0]["line"] >= 1


def test_api_provider_config_save_updates_non_secret_fields(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "POST",
        "/api/provider-config",
        "",
        json.dumps(
            {
                "path": str(root),
                "agents": {
                    "writer": {
                        "provider": "mock",
                        "model": "web-writer-model",
                        "temperature": 0.3,
                        "thinking": {"type": "disabled"},
                    }
                },
            }
        ),
    )

    assert status == 200
    assert payload["ok"] is True
    agents = json.loads(json.dumps(payload["data"]["config"]["content"], ensure_ascii=False))  # type: ignore[index]
    assert agents["agents"]["writer"]["model"] == "web-writer-model"
    assert list((root / "config").glob("agents.yaml.bak_*"))


def test_api_provider_config_rejects_raw_api_key_without_leaking(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    secret = "sk-test-secret-never-return"

    status, payload = handle_api_request(
        "POST",
        "/api/provider-config",
        "",
        json.dumps({"path": str(root), "agents": {"writer": {"api_key_env": secret}}}),
    )

    assert status == 400
    serialized = json.dumps(payload, ensure_ascii=False)
    assert secret not in serialized


def test_api_session_start_endpoint_creates_outline(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "POST",
        "/api/session/start",
        "",
        json.dumps({"path": str(root), "intent": "写第1章", "chapters": "1", "provider": "mock"}),
    )

    assert status == 200
    assert payload["ok"] is True
    session = payload["data"]["session"]  # type: ignore[index]
    assert session["status"] == "outline_proposed"
    assert (root / "memory" / "sessions" / session["session_id"] / "outline_proposal.md").is_file()


def test_api_validate_endpoint_returns_project_report(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request("GET", "/api/validate", f"path={root}", None)

    assert status == 200
    assert payload["ok"] is True
    data = payload["data"]
    assert data["valid"] is True  # type: ignore[index]
    assert data["error_count"] == 0  # type: ignore[index]
    assert isinstance(data["warnings"], list)  # type: ignore[index]


def test_api_session_revise_outline_keeps_session_id(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    start_status, start_payload = handle_api_request(
        "POST",
        "/api/session/start",
        "",
        json.dumps({"path": str(root), "intent": "写第1章", "chapters": "1", "provider": "mock"}),
    )
    session_id = start_payload["data"]["session"]["session_id"]  # type: ignore[index]

    revise_status, revise_payload = handle_api_request(
        "POST",
        "/api/session/revise-outline",
        "",
        json.dumps(
            {
                "path": str(root),
                "session_id": session_id,
                "instruction": "把开场改得更克制",
                "provider": "mock",
                "force": True,
            }
        ),
    )

    assert start_status == 200
    assert revise_status == 200
    assert revise_payload["data"]["session"]["session_id"] == session_id  # type: ignore[index]
    assert (root / "memory" / "sessions" / session_id / "outline_proposal.md").is_file()


def test_api_init_project_endpoint_creates_workspace(tmp_path: Path) -> None:
    root = tmp_path / "web-created"

    status, payload = handle_api_request(
        "POST",
        "/api/init-project",
        "",
        json.dumps({"path": str(root), "title": "新武侠", "genre": "武侠, 悬疑"}),
    )

    assert status == 200
    assert payload["ok"] is True
    assert (root / "project.yaml").is_file()
    assert (root / "config" / "agents.yaml").is_file()


def test_api_inspire_and_canon_web_endpoints(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="Web Canon", root=root))
    inspiration_path = root / "memory" / "inspiration.md"
    inspiration_path.unlink()

    inspire_status, inspire_payload = handle_api_request(
        "POST",
        "/api/inspire",
        "",
        json.dumps({"path": str(root), "text": "写一个江湖雨夜故事。", "provider": "mock", "write_json": True}),
    )
    suggest_status, suggest_payload = handle_api_request(
        "POST",
        "/api/canon/suggest",
        "",
        json.dumps({"path": str(root), "provider": "mock"}),
    )
    proposal_path = suggest_payload["data"]["relative_path"]  # type: ignore[index]
    apply_status, apply_payload = handle_api_request(
        "POST",
        "/api/canon/apply",
        "",
        json.dumps({"path": str(root), "proposal_file": proposal_path}),
    )

    assert inspire_status == 200
    assert inspire_payload["ok"] is True
    assert (root / "memory" / "inspiration.json").is_file()
    assert suggest_status == 200
    assert suggest_payload["ok"] is True
    assert str(proposal_path).startswith("runs/canon_proposal_")
    assert apply_status == 200
    assert apply_payload["data"]["validation_ok"] is True  # type: ignore[index]


def test_frontend_basic_render() -> None:
    html = index_html()

    assert 'id="projectPath"' in html
    assert 'id="instruction"' in html
    assert 'id="fileTree"' in html
    assert 'id="chapterList"' in html
    assert 'id="compareGrid"' in html
    assert 'id="chapterEditor"' in html
    assert 'id="chapterEditorText"' in html
    assert 'id="auditLocate"' in html
    assert 'id="auditIssueList"' in html
    assert 'id="runLogs"' in html
    assert 'id="providerConfig"' in html
    assert 'id="providerFieldEditor"' in html
    assert 'id="stateTimeline"' in html
    assert 'id="diffViewer"' in html
    assert 'id="sessionStart"' in html
    assert 'id="sessionRun"' in html
    assert 'id="sessionPanel"' in html
    assert 'id="sessionReviseAudit"' in html
    assert 'id="sessionReviseInstruction"' in html
    assert 'id="sessionReviseOutline"' in html
    assert 'id="validateProject"' in html
    assert 'id="nextStepPanel"' in html
    assert 'id="initProject"' in html
    assert 'id="inspireProject"' in html
    assert 'id="canonSuggest"' in html
    assert 'id="canonApply"' in html
    assert 'id="forceWrites"' in html
    assert 'id="planChapter"' in html
    assert 'id="writeChapter"' in html
    assert 'id="polishChapter"' in html
    assert 'id="auditChapter"' in html
    assert 'id="exportMarkdown"' in html
    assert "/api/save-chapter-file" in html
    assert "/api/audit-annotations" in html
    assert "/api/init-project" in html
    assert "/api/inspire" in html
    assert "/api/canon/suggest" in html
    assert "/api/validate" in html
    assert "/api/session/revise-outline" in html
    assert "/api/session/revise-content" in html
    assert "from_audit" in html
    assert "renderNextStep" in html
    assert "refreshAll({ silent: true })" in html
    assert "includeSessionId: false" in html
    assert "fetch(" in html


def test_api_session_revise_content_passes_from_audit_and_returns_audit_summary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    session_id = "session_20260529_010101_000001"
    session_dir = root / "memory" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_model_json(
        chapter_dir / "audit.json",
        AuditReport.model_validate(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "overall_status": "needs_revision",
                "summary": "仍有阻断问题。",
                "issues": [
                    {
                        "id": "audit_001_medium",
                        "severity": "medium",
                        "type": "state_conflict",
                        "description": "物品位置冲突。",
                        "evidence": [{"source": "polished.md", "quote": "错误位置"}],
                        "suggested_fix": "修正文。",
                    }
                ],
                "created_at": "2026-05-22T00:00:00Z",
            }
        ),
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    session = CreationSession(
        session_id=session_id,
        scope_type="chapters",
        chapter_range=[1],
        user_intent="写第1章",
        status="needs_revision",
        outline_status="approved",
        content_status="needs_revision",
        approved_outline_path=f"memory/sessions/{session_id}/approved_outline.json",
        created_at=now,
        updated_at=now,
    )
    captured: dict[str, object] = {}

    def fake_revise_content(options) -> SessionResult:
        captured["from_audit"] = options.from_audit
        captured["instruction"] = options.instruction
        return SessionResult(
            session=session,
            session_path=session_dir / "session.json",
            message="fake revised",
        )

    monkeypatch.setattr("novel.web_api.revise_content", fake_revise_content)

    status, payload = handle_api_request(
        "POST",
        "/api/session/revise-content",
        "",
        json.dumps(
            {
                "path": str(root),
                "session_id": session_id,
                "instruction": "按审核修",
                "provider": "mock",
                "from_audit": True,
            }
        ),
    )

    assert status == 200
    assert payload["ok"] is True
    assert captured["from_audit"] is True
    assert captured["instruction"] == "按审核修"
    audit_summary = payload["data"]["audit_summary"]  # type: ignore[index]
    assert audit_summary[0]["overall_status"] == "needs_revision"
    assert audit_summary[0]["blocking_issue_count"] == 1


def test_web_port_can_be_read_from_project_config(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    project_path = root / "project.yaml"
    project_path.write_text(
        project_path.read_text(encoding="utf-8") + "\nweb:\n  default_port: 9012\n",
        encoding="utf-8",
    )

    assert _resolve_web_port(str(root), None) == 9012
    assert _resolve_web_port(str(root), 7777) == 7777


def test_web_server_reports_port_conflict(monkeypatch) -> None:
    def raise_port_conflict(*args: object, **kwargs: object) -> object:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr("novel.web_server.ThreadingHTTPServer", raise_port_conflict)

    try:
        run_web_server(host="127.0.0.1", port=9012)
    except WebServerError as exc:
        message = str(exc)
    else:  # pragma: no cover - this would block if the server started unexpectedly
        raise AssertionError("expected port conflict")

    assert "端口 9012 已被占用" in message
    assert "novel web --port" in message


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
    assert payload["data"]["status"] == "completed"  # type: ignore[index]
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
    assert chapters_payload["data"]["chapters"][0]["has_audit"] is True  # type: ignore[index]


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


def _write_chapter_file(root: Path, file_name: str, body: str) -> Path:
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_dir / file_name
    path.write_text(
        "---\nchapter_number: 1\ntitle: 雨夜旧车站\nstatus: polished\n---\n\n" + body + "\n",
        encoding="utf-8",
    )
    return path
