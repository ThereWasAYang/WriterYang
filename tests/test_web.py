from __future__ import annotations

import json
import errno
from datetime import datetime, timezone
from pathlib import Path

from novel.cli import _resolve_web_port
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.io import atomic_write_model_json, load_json_model, load_yaml
from novel.core.planning import default_mock_chapter_plan_json
from novel.core.provider_config import resolve_agent_config
from novel.core.schemas import (
    AuditReport,
    ChapterMemory,
    CreationSession,
    SessionProgress,
    SessionRewriteEvent,
    SessionRewriteEvents,
    TimelineFile,
)
from novel.core.session import SessionResult
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request
from novel.web_server import WebServerError, index_html, run_web_server, static_asset_bytes


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


def test_api_runtime_endpoint_reports_environment_without_secrets(monkeypatch) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "WriterYang_260531")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-never-return")

    status, payload = handle_api_request("GET", "/api/runtime", "", None)

    assert status == 200
    assert payload["ok"] is True
    runtime = payload["data"]["runtime"]  # type: ignore[index]
    assert runtime["environment"] == "WriterYang_260531"
    assert runtime["managed_install"] is True
    assert runtime["version"]
    assert "sk-test-secret-never-return" not in json.dumps(payload, ensure_ascii=False)


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
    monkeypatch.setenv("DASHSCOPE_EMBEDDING_BASE_URL", "https://dashscope-secret-never-return.example/v1")

    status, payload = handle_api_request("GET", "/api/provider-config", f"path={root}", None)

    assert status == 200
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "sk-test-secret-never-return" not in serialized
    assert "dashscope-secret-never-return" not in serialized
    assert "OPENAI_API_KEY" in serialized
    assert payload["data"]["agents"]["warnings"] == []  # type: ignore[index]
    assert payload["data"]["embedding_api"]["configured"] is True  # type: ignore[index]
    assert payload["data"]["embedding_api"]["provider"] == "dashscope"  # type: ignore[index]
    assert payload["data"]["embedding_api"]["model"] == "text-embedding-v4"  # type: ignore[index]
    assert payload["data"]["effective_agents"]["writer"]["source_label"] == "default + agent override"  # type: ignore[index]
    assert payload["data"]["effective_agents"]["writer"]["config"]["api_key_env"] == "OPENAI_API_KEY"  # type: ignore[index]
    env_entries = payload["data"]["agents"]["env"]  # type: ignore[index]
    assert any(item["name"] == "OPENAI_API_KEY" and item["exists"] is True for item in env_entries)


def test_api_setup_default_provider_writes_env_and_does_not_leak_secret(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    secret = "sk-test-secret-never-return"

    status, payload = handle_api_request(
        "POST",
        "/api/setup/default-provider",
        "",
        json.dumps(
            {
                "path": str(root),
                "base_url": "https://api.example.test/v1",
                "api_key": secret,
                "model": "example-model",
                "ping": False,
            }
        ),
    )

    assert status == 200
    serialized = json.dumps(payload, ensure_ascii=False)
    assert secret not in serialized
    assert (root / ".env").read_text(encoding="utf-8").count("WRITERYANG_DEFAULT_API_KEY") == 1
    agents_yaml = (root / "config" / "agents.yaml").read_text(encoding="utf-8")
    assert "WRITERYANG_DEFAULT_API_KEY" in agents_yaml
    assert secret not in agents_yaml


def test_api_setup_embedding_can_be_skipped_or_saved(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    skipped_status, skipped_payload = handle_api_request(
        "POST",
        "/api/setup/embedding",
        "",
        json.dumps({"path": str(root), "skip": True}),
    )
    saved_status, saved_payload = handle_api_request(
        "POST",
        "/api/setup/embedding",
        "",
        json.dumps(
            {
                "path": str(root),
                "provider": "openai_compatible",
                "base_url": "https://embed.example.test/v1",
                "api_key": "embedding-secret",
                "model": "embedding-model",
                "ping": False,
            }
        ),
    )

    assert skipped_status == 200
    assert skipped_payload["data"]["skipped"] is True  # type: ignore[index]
    assert saved_status == 200
    serialized = json.dumps(saved_payload, ensure_ascii=False)
    assert "embedding-secret" not in serialized
    assert saved_payload["data"]["embedding_api"]["configured"] is True  # type: ignore[index]
    assert saved_payload["data"]["embedding_api"]["status"] == "configured"  # type: ignore[index]
    assert saved_payload["data"]["embedding_api"]["provider"] == "openai_compatible"  # type: ignore[index]
    assert saved_payload["data"]["embedding_api"]["model"] == "embedding-model"  # type: ignore[index]
    assert saved_payload["data"]["embedding_api"]["api_key_env"] == "WRITERYANG_EMBEDDING_API_KEY"  # type: ignore[index]
    assert saved_payload["data"]["embedding_api"]["base_url_env"] == "WRITERYANG_EMBEDDING_BASE_URL"  # type: ignore[index]
    assert "WRITERYANG_EMBEDDING_API_KEY" in (root / "config" / "embeddings.yaml").read_text(encoding="utf-8")


def test_api_setup_embedding_requires_complete_config(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    cases = [
        ("base_url", "", "base_url is required"),
        ("api_key", "", "api_key is required"),
        ("model", "", "model is required"),
    ]

    for field, value, message in cases:
        request = {
            "path": str(root),
            "provider": "openai_compatible",
            "base_url": "https://embed.example.test/v1",
            "api_key": "embedding-secret",
            "model": "embedding-model",
            "ping": False,
        }
        request[field] = value
        status, payload = handle_api_request("POST", "/api/setup/embedding", "", json.dumps(request))

        assert status == 400
        assert payload["ok"] is False
        assert payload["error"]["code"] == "invalid_request"  # type: ignore[index]
        assert message in payload["error"]["message"]  # type: ignore[index]
        assert "embedding-secret" not in json.dumps(payload, ensure_ascii=False)


def test_api_setup_recommend_and_save_port(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    monkeypatch.setattr("novel.web_api.find_available_port", lambda start, host="127.0.0.1": int(start))
    monkeypatch.setattr("novel.core.setup_guide.is_port_available", lambda port, host="127.0.0.1": True)

    recommend_status, recommend_payload = handle_api_request(
        "GET",
        "/api/setup/recommend-port",
        "start_port=8765",
        None,
    )
    selected = recommend_payload["data"]["selected_port"]  # type: ignore[index]
    save_status, save_payload = handle_api_request(
        "POST",
        "/api/setup/web-port",
        "",
        json.dumps({"path": str(root), "port": selected}),
    )

    assert recommend_status == 200
    assert save_status == 200
    assert save_payload["data"]["selected_port"] == selected  # type: ignore[index]
    assert f":{selected}" in save_payload["data"]["url"]  # type: ignore[operator]


def test_api_provider_config_warns_without_default(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    (root / "config" / "agents.yaml").write_text(
        "\n".join(
            [
                "agents:",
                "  writer:",
                '    provider: "openai_compatible"',
                '    api_key_env: "WRITER_API_KEY"',
                '    model: "writer-model"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    status, payload = handle_api_request("GET", "/api/provider-config", f"path={root}", None)

    assert status == 200
    warnings = payload["data"]["agents"]["warnings"]  # type: ignore[index]
    assert any("default API config is missing" in warning for warning in warnings)


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


def test_api_provider_config_save_updates_default_config(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "POST",
        "/api/provider-config",
        "",
        json.dumps({"path": str(root), "default": {"model": "web-default-model"}}),
    )

    assert status == 200
    assert payload["ok"] is True
    content = payload["data"]["config"]["content"]  # type: ignore[index]
    assert content["default"]["model"] == "web-default-model"  # type: ignore[index]


def test_api_provider_config_save_can_add_known_agent_override(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "POST",
        "/api/provider-config",
        "",
        json.dumps({"path": str(root), "agents": {"revision": {"model": "web-revision-model"}}}),
    )

    assert status == 200
    assert payload["ok"] is True
    content = payload["data"]["config"]["content"]  # type: ignore[index]
    assert content["agents"]["revision"]["model"] == "web-revision-model"  # type: ignore[index]


def test_api_provider_config_clear_agent_override_restores_default(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    config_path = root / "config" / "agents.yaml"
    before = resolve_agent_config(config_path, "writer")
    assert before.max_tokens == 24000

    status, payload = handle_api_request(
        "POST",
        "/api/provider-config",
        "",
        json.dumps({"path": str(root), "clear_agents": ["writer"]}),
    )

    assert status == 200
    assert payload["ok"] is True
    assert payload["data"]["cleared_agents"] == ["writer"]  # type: ignore[index]
    assert "writer" not in payload["data"]["config"]["content"]["agents"]  # type: ignore[index]
    after = resolve_agent_config(config_path, "writer")
    assert after.model == "model-name"
    assert after.max_tokens == 8192
    assert payload["data"]["effective_agents"]["writer"]["source_label"] == "default"  # type: ignore[index]
    assert list((root / "config").glob("agents.yaml.bak_*"))


def test_api_provider_config_clear_rejects_default_and_unknown_agent(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    before = load_yaml(root / "config" / "agents.yaml")

    for agent_name, message in [
        ("default", "default config cannot be cleared"),
        ("not_an_agent", "unknown agent: not_an_agent"),
    ]:
        status, payload = handle_api_request(
            "POST",
            "/api/provider-config",
            "",
            json.dumps({"path": str(root), "clear_agents": [agent_name]}),
        )
        assert status == 400
        assert message in payload["error"]["message"]  # type: ignore[index]
        assert load_yaml(root / "config" / "agents.yaml") == before


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


def test_api_search_status_and_refresh_do_not_leak_env_values(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("UNRELATED_SECRET_VALUE", "dashscope-secret-never-return")

    status_before, payload_before = handle_api_request("GET", "/api/search-status", f"path={root}", None)
    refresh_status, refresh_payload = handle_api_request(
        "POST",
        "/api/index/refresh",
        "",
        json.dumps({"path": str(root), "with_embeddings": False}),
    )
    status_after, payload_after = handle_api_request("GET", "/api/search-status", f"path={root}", None)

    assert status_before == 200
    assert payload_before["data"]["search"]["fts_status"] in {"missing", "stale", "indexed"}  # type: ignore[index]
    assert refresh_status == 200
    assert refresh_payload["data"]["with_embeddings"] is False  # type: ignore[index]
    assert status_after == 200
    assert payload_after["data"]["search"]["fts_status"] == "indexed"  # type: ignore[index]
    serialized = json.dumps([payload_before, refresh_payload, payload_after], ensure_ascii=False)
    assert "dashscope-secret-never-return" not in serialized
    assert "DASHSCOPE_API_KEY" in serialized


def test_api_search_endpoint_uses_fts_and_returns_results(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    status, payload = handle_api_request(
        "GET",
        "/api/search",
        f"path={root}&query=Weak&type=all&limit=5&highlight=1",
        None,
    )

    assert status == 200
    data = payload["data"]
    assert data["query"] == "Weak"  # type: ignore[index]
    assert data["use_vector"] is False  # type: ignore[index]
    assert data["results"]  # type: ignore[index]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "api_key" not in serialized.lower()


def test_api_migration_status_and_apply_endpoint(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    project_path = root / "project.yaml"
    project_yaml = load_yaml(project_path)
    project_yaml.pop("schema_version", None)
    project_path.write_text(json.dumps(project_yaml, ensure_ascii=False), encoding="utf-8")

    dry_status, dry_payload = handle_api_request("GET", "/api/migration-status", f"path={root}", None)
    apply_status, apply_payload = handle_api_request(
        "POST",
        "/api/migrate",
        "",
        json.dumps({"path": str(root)}),
    )

    assert dry_status == 200
    assert dry_payload["data"]["changed"] is True  # type: ignore[index]
    assert "project.yaml" in dry_payload["data"]["updated_files"]  # type: ignore[operator]
    assert apply_status == 200
    assert apply_payload["data"]["changed"] is True  # type: ignore[index]
    assert apply_payload["data"]["validation"]["error_count"] == 0  # type: ignore[index]
    assert "schema_version" in project_path.read_text(encoding="utf-8")


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


def test_api_session_progress_idle_and_cancel_requested(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    start_status, start_payload = handle_api_request(
        "POST",
        "/api/session/start",
        "",
        json.dumps({"path": str(root), "intent": "写第1章", "chapters": "1", "provider": "mock"}),
    )
    session_id = start_payload["data"]["session"]["session_id"]  # type: ignore[index]

    idle_status, idle_payload = handle_api_request(
        "GET",
        "/api/session/progress",
        f"path={root}&session_id={session_id}",
        None,
    )

    assert start_status == 200
    assert idle_status == 200
    assert idle_payload["data"]["progress"]["status"] == "idle"  # type: ignore[index]

    secret = "sk-test-progress-secret"
    monkeypatch.setenv("WRITERYANG_TEST_API_KEY", secret)
    now = datetime.now(timezone.utc)
    atomic_write_model_json(
        root / "memory" / "sessions" / session_id / "progress.json",
        SessionProgress(
            session_id=session_id,
            status="running",
            current_stage="draft",
            current_message=f"调用模型 {secret}",
            started_at=now,
            updated_at=now,
            error=f"api_key={secret}",
        ),
    )

    running_status, running_payload = handle_api_request(
        "GET",
        "/api/session/progress",
        f"path={root}&session_id={session_id}",
        None,
    )
    cancel_status, cancel_payload = handle_api_request(
        "POST",
        "/api/session/cancel",
        "",
        json.dumps({"path": str(root), "session_id": session_id}),
    )

    assert running_status == 200
    running_serialized = json.dumps(running_payload, ensure_ascii=False)
    assert secret not in running_serialized
    assert "[redacted]" in running_serialized or "[redacted-api-key]" in running_serialized
    assert cancel_status == 200
    progress = cancel_payload["data"]["progress"]  # type: ignore[index]
    assert progress["status"] == "cancel_requested"
    assert progress["cancel_requested_at"]


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
    apply_data = apply_payload["data"]  # type: ignore[assignment]
    assert str(apply_data["apply_log_relative_path"]).startswith("memory/canon/applied_proposals/")
    assert str(apply_data["proposal_snapshot_relative_path"]).startswith("memory/canon/applied_proposals/")
    applied_status, applied_payload = handle_api_request(
        "GET",
        "/api/canon/applied-proposals",
        f"path={root}",
        None,
    )
    assert applied_status == 200
    records = applied_payload["data"]["applied_proposals"]  # type: ignore[index]
    assert records[0]["proposal_snapshot_path"] == apply_data["proposal_snapshot_relative_path"]
    assert records[0]["proposal_counts"]["characters"] == 1
    read_status, read_payload = handle_api_request(
        "GET",
        "/api/read-file",
        f"path={root}&file={apply_data['proposal_snapshot_relative_path']}",
        None,
    )
    assert read_status == 200
    assert "char_lin_che" in read_payload["data"]["content"]  # type: ignore[index]


def test_api_canon_apply_reports_domain_error_on_conflict(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="Web Canon Conflict", root=root))
    proposal_path = root / "runs" / "canon_proposal_test.json"
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")

    first_status, first_payload = handle_api_request(
        "POST",
        "/api/canon/apply",
        "",
        json.dumps({"path": str(root), "proposal_file": "runs/canon_proposal_test.json"}),
    )
    second_status, second_payload = handle_api_request(
        "POST",
        "/api/canon/apply",
        "",
        json.dumps({"path": str(root), "proposal_file": "runs/canon_proposal_test.json"}),
    )

    assert first_status == 200
    assert first_payload["ok"] is True
    assert second_status == 400
    assert second_payload["ok"] is False
    assert second_payload["error"]["code"] == "canon_error"  # type: ignore[index]
    assert "id conflict" in second_payload["error"]["message"]  # type: ignore[index]
    assert "char_lin_che" in second_payload["error"]["message"]  # type: ignore[index]


def test_api_inspire_overwrites_default_placeholder_without_force(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="Web Inspiration", root=root))

    status, payload = handle_api_request(
        "POST",
        "/api/inspire",
        "",
        json.dumps({"path": str(root), "text": "写一个江湖雨夜故事。", "provider": "mock"}),
    )

    assert status == 200
    assert payload["ok"] is True
    assert "## Weak Outline" in (root / "memory" / "inspiration.md").read_text(encoding="utf-8")
    assert not (root / "memory" / "inspiration.json").exists()


def test_api_inspire_refuses_to_overwrite_user_inspiration_without_force(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="Web Inspiration", root=root))
    inspiration_path = root / "memory" / "inspiration.md"
    inspiration_path.write_text("# Inspiration\n\n用户已经写好的灵感。\n", encoding="utf-8")

    status, payload = handle_api_request(
        "POST",
        "/api/inspire",
        "",
        json.dumps({"path": str(root), "text": "写一个江湖雨夜故事。", "provider": "mock"}),
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["code"] == "operation_failed"  # type: ignore[index]
    assert "用户已经写好的灵感" in inspiration_path.read_text(encoding="utf-8")


def test_api_memory_repair_suggest_apply_and_management_events(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    timeline_path = root / "memory" / "state" / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "event_wrong_current",
                        "chapter": 2,
                        "scene": 1,
                        "in_story_time": "多年前",
                        "event_role": "current_action",
                        "summary": "实际是回忆。",
                        "reader_visible": True,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    suggest_status, suggest_payload = handle_api_request(
        "POST",
        "/api/orchestrator/memory-repair/suggest",
        "",
        json.dumps(
                {
                    "path": str(root),
                    "request": "第2章 event_wrong_current 这个事件其实是回忆，不是当前行动",
                    "provider": "mock",
                }
            ),
        )
    proposal_path = suggest_payload["data"]["proposal_relative_path"]  # type: ignore[index]
    apply_status, apply_payload = handle_api_request(
        "POST",
        "/api/orchestrator/memory-repair/apply",
        "",
        json.dumps({"path": str(root), "proposal_path": proposal_path}),
    )
    events_status, events_payload = handle_api_request("GET", "/api/management-events", f"path={root}", None)

    assert suggest_status == 200
    assert apply_status == 200
    assert apply_payload["data"]["apply_log"]["status"] == "applied"  # type: ignore[index]
    timeline = TimelineFile.model_validate(json.loads(timeline_path.read_text(encoding="utf-8")))
    assert timeline.events[0].event_role == "flashback"
    assert events_status == 200
    serialized_events = json.dumps(events_payload, ensure_ascii=False)
    assert "memory_repair_proposed" in serialized_events
    assert "memory_repair_applied" in serialized_events


def test_api_setting_change_suggest_apply_syncs_outline_session(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    start_status, start_payload = handle_api_request(
        "POST",
        "/api/session/start",
        "",
        json.dumps({"path": str(root), "intent": "写第1章", "chapters": "1", "provider": "mock"}),
    )
    session_id = start_payload["data"]["session"]["session_id"]  # type: ignore[index]

    suggest_status, suggest_payload = handle_api_request(
        "POST",
        "/api/settings/change/suggest",
        "",
        json.dumps(
            {
                "path": str(root),
                "request": "新增人物沈微",
                "provider": "mock",
                "session_id": session_id,
                "source_stage": "outline_discussion",
            }
        ),
    )
    proposal_path = suggest_payload["data"]["proposal_relative_path"]  # type: ignore[index]
    apply_status, apply_payload = handle_api_request(
        "POST",
        "/api/settings/change/apply",
        "",
        json.dumps(
            {
                "path": str(root),
                "proposal_path": proposal_path,
                "provider": "mock",
                "session_id": session_id,
                "sync_session": True,
            }
        ),
    )

    assert start_status == 200
    assert suggest_status == 200
    assert suggest_payload["data"]["proposal"]["change_kind"] == "setting_change"  # type: ignore[index]
    assert suggest_payload["data"]["proposal"]["impact"]["affected_sessions"] == [session_id]  # type: ignore[index]
    assert apply_status == 200
    assert apply_payload["data"]["apply_log"]["status"] == "applied"  # type: ignore[index]
    assert apply_payload["data"]["sync_result"]["status"] == "synced"  # type: ignore[index]
    assert apply_payload["data"]["sync_result"]["action"] == "revise_outline"  # type: ignore[index]


def test_api_setting_change_suggest_can_request_and_answer_clarification(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)

    suggest_status, suggest_payload = handle_api_request(
        "POST",
        "/api/settings/change/suggest",
        "",
        json.dumps(
            {
                "path": str(root),
                "request": "把某个人物改一下",
                "provider": "mock",
                "source_stage": "outline_discussion",
            }
        ),
    )
    clarification_id = suggest_payload["data"]["clarification_id"]  # type: ignore[index]
    answer_status, answer_payload = handle_api_request(
        "POST",
        "/api/settings/change/answer",
        "",
        json.dumps(
            {
                "path": str(root),
                "clarification_id": clarification_id,
                "answer": "新增人物沈微",
                "provider": "mock",
            }
        ),
    )

    assert suggest_status == 200
    assert suggest_payload["data"]["status"] == "needs_clarification"  # type: ignore[index]
    assert suggest_payload["data"]["questions"]  # type: ignore[index]
    assert answer_status == 200
    assert answer_payload["data"]["status"] == "proposal_ready"  # type: ignore[index]
    assert answer_payload["data"]["proposal"]["change_kind"] == "setting_change"  # type: ignore[index]
    assert answer_payload["data"]["proposal_relative_path"]  # type: ignore[index]


def test_api_setting_change_syncs_content_review_with_revise_content(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    session_id = "session_20260529_010101_000004"
    session_dir = root / "memory" / "sessions" / session_id
    session_dir.mkdir(parents=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    session = CreationSession(
        session_id=session_id,
        scope_type="chapters",
        chapter_range=[1],
        user_intent="写第1章",
        status="needs_user_review",
        outline_status="approved",
        content_status="needs_user_review",
        approved_outline_path=f"memory/sessions/{session_id}/approved_outline.json",
        final_output_paths=["memory/chapters/001/polished.md"],
        created_at=now,
        updated_at=now,
    )
    atomic_write_model_json(session_dir / "session.json", session)
    captured: dict[str, object] = {}

    def fake_revise_content(options) -> SessionResult:
        captured["instruction"] = options.instruction
        return SessionResult(session=session, session_path=session_dir / "session.json", message="synced content")

    def fail_revise_outline(*args: object, **kwargs: object) -> None:
        raise AssertionError("content review setting changes must not revise outline")

    monkeypatch.setattr("novel.web_api.revise_content", fake_revise_content)
    monkeypatch.setattr("novel.web_api.revise_outline", fail_revise_outline)

    suggest_status, suggest_payload = handle_api_request(
        "POST",
        "/api/settings/change/suggest",
        "",
        json.dumps(
            {
                "path": str(root),
                "request": "新增人物沈微",
                "provider": "mock",
                "session_id": session_id,
                "source_stage": "content_review",
            }
        ),
    )
    proposal_path = suggest_payload["data"]["proposal_relative_path"]  # type: ignore[index]
    apply_status, apply_payload = handle_api_request(
        "POST",
        "/api/settings/change/apply",
        "",
        json.dumps(
            {
                "path": str(root),
                "proposal_path": proposal_path,
                "provider": "mock",
                "session_id": session_id,
                "sync_session": True,
            }
        ),
    )

    assert suggest_status == 200
    assert apply_status == 200
    assert apply_payload["data"]["sync_result"]["status"] == "synced"  # type: ignore[index]
    assert apply_payload["data"]["sync_result"]["action"] == "revise_content"  # type: ignore[index]
    assert "原始设定变更请求" in str(captured["instruction"])


def test_frontend_basic_render() -> None:
    html = index_html()
    app_css = (static_asset_bytes("/static/app.css") or (b"", ""))[0].decode("utf-8")
    app_js = (static_asset_bytes("/static/app.js") or (b"", ""))[0].decode("utf-8")
    frontend = f"{html}\n{app_css}\n{app_js}"

    assert 'id="projectPath"' in html
    assert 'id="runtimePanel"' in html
    assert '<link rel="stylesheet" href="/static/app.css">' in html
    assert '<script src="/static/app.js"></script>' in html
    assert "<style>" not in html
    assert "<script>\n" not in html
    assert "runtime-panel" in frontend
    assert ".config-layout" in frontend
    assert ".provider-form-grid" in frontend
    assert 'id="currentProjectSummary"' in html
    assert 'id="currentValidationSummary"' in html
    assert 'data-page="homePage"' in html
    assert 'data-page="workbenchPage"' in html
    assert 'data-page="memoryPage"' in html
    assert 'data-page="configPage"' in html
    assert 'data-page="logsPage"' in html
    assert 'id="homePage"' in html
    assert 'id="workbenchPage"' in html
    assert 'id="memoryPage"' in html
    assert 'id="configPage"' in html
    assert 'id="logsPage"' in html
    assert "创作工作台" in html
    assert "小说状态管理" in html
    assert "模型与检索配置" in html
    assert "运行日志 / 项目文件" in html
    assert 'id="instruction"' in html
    assert 'id="memoryRepairInstruction"' in html
    assert 'id="fileTree"' in html
    assert 'id="projectFiles"' in html
    assert 'id="projectFileCurrent"' in html
    assert 'id="projectFileViewer"' in html
    assert 'id="chapterList"' in html
    assert 'id="compareGrid"' in html
    assert 'id="chapterEditor"' in html
    assert 'id="chapterEditorText"' in html
    assert 'id="auditLocate"' in html
    assert 'id="auditIssueList"' in html
    assert 'id="runLogs"' in html
    assert 'id="projectSearch"' in html
    assert 'id="searchQuery"' in html
    assert 'id="searchProjectContent"' in html
    assert 'id="searchUseVector"' in html
    assert 'id="usageStats"' in html
    assert 'id="loadUsage"' in html
    assert 'id="usagePanel"' in html
    assert 'id="migrationStatusPanel"' in html
    assert 'id="runMigration"' in html
    assert 'id="providerConfig"' in html
    assert "config-layout" in html
    assert "provider-config-grid" in html
    assert "provider-form-grid" in html
    assert "provider-field-wide" in html
    assert "Agent 模型配置" in html
    assert 'id="providerProviderField"' in html
    assert '<input id="providerModelField"' in html
    assert '<textarea id="providerModelField"' not in html
    assert 'id="providerBaseUrlEnvField"' in html
    assert 'id="providerApiKeyEnvField"' in html
    assert 'id="providerThinkingTypeField"' in html
    assert 'id="providerTemperatureField"' in html
    assert 'id="providerMaxTokensField"' in html
    assert 'id="providerMaxContextTokensField"' in html
    assert 'id="providerTimeoutSecondsField"' in html
    assert 'id="providerMaxRetriesField"' in html
    assert 'id="providerFieldEditor"' in html
    assert 'id="providerConfigWarnings"' in html
    assert 'id="clearProviderAgentConfig"' in html
    assert 'id="providerEffectivePanel"' in html
    assert "当前 Agent 生效配置" in html
    assert "完整脱敏调试 JSON" in html
    assert '<option value="config">config</option>' in html
    assert '<option value="mock">mock（仅测试）</option>' in html
    assert 'id="stateTimeline"' in html
    assert 'id="diffViewer"' in html
    assert 'id="sessionStart"' in html
    assert 'id="sessionRun"' in html
    assert 'id="sessionPanel"' in html
    assert 'id="sessionProgressPanel"' in html
    assert 'id="cancelSessionTask"' in html
    assert 'id="recentOperationsPanel"' in html
    assert 'id="workbenchNextStepPanel"' in html
    assert 'id="rewriteEventsPanel"' in html
    assert "本次修改路由" in app_js
    assert "/api/session/progress" in app_js
    assert "/api/session/cancel" in app_js
    assert "startSessionProgressPolling" in app_js
    assert "renderSessionProgress" in app_js
    assert "cancelSessionTask" in app_js
    assert "beforeunload" in app_js
    assert "metaKey" in app_js
    assert "ctrlKey" in app_js
    assert "buttons.forEach((button) => { button.disabled = true; });" not in app_js
    assert 'id="rejectedTextViewer"' in html
    assert 'id="sessionReviseAudit"' in html
    assert 'id="sessionReviseInstruction"' in html
    assert 'id="sessionReviseOutline"' in html
    assert 'id="validateProject"' in html
    assert 'id="validationStatusPanel"' in html
    assert 'id="nextStepPanel"' in html
    assert 'id="toggleProjectInit"' in html
    assert 'id="projectInitPanel"' in html
    assert 'id="initProject"' in html
    assert 'id="setupGuidePanel"' in html
    assert 'id="setupDefaultProvider"' in html
    assert 'id="setupEmbedding"' in html
    assert 'id="setupWebPort"' in html
    assert 'id="setupOpenWeb"' in html
    assert 'id="inspireProject"' in html
    assert 'id="inspirationPreviewPanel"' in html
    assert 'id="inspirationPreviewMeta"' in html
    assert 'id="inspirationPreview"' in html
    assert 'id="regenerateInspiration"' in html
    assert 'id="openInspirationFile"' in html
    assert ".inspiration-preview button" in app_css
    assert "white-space: nowrap" in app_css
    assert "重新生成会覆盖 memory/inspiration.md。确认继续吗？" in app_js
    assert "loadInspirationPreview" in app_js
    assert "readWorkspaceFile(inspirationPreviewPath)" in app_js
    assert 'id="canonSuggest"' in html
    assert 'id="canonApply"' in html
    assert 'id="canonAppliedProposalPanel"' in html
    assert 'id="viewLatestCanonProposal"' in html
    assert "请先点击“Canon 建议”，生成 Canon proposal 文件后再应用。" in app_js
    assert "/api/canon/applied-proposals" in app_js
    assert "renderCanonAppliedProposals" in app_js
    assert "latestCanonProposalSnapshotPath" in app_js
    assert 'id="memoryRepairSuggest"' in html
    assert 'id="memoryRepairApply"' in html
    assert 'id="memoryRepairClarificationAnswer"' in html
    assert 'id="memoryRepairClarificationSubmit"' in html
    assert 'id="rebuildChapterMemory"' in html
    assert 'id="memoryRepairProposalPath"' in html
    assert 'id="managementEventsPanel"' in html
    assert 'id="embeddingConfigPanel"' in html
    assert 'id="embeddingConfigSummary"' in html
    assert 'id="embeddingConfigForm"' in html
    assert 'id="configEmbeddingBaseUrl"' in html
    assert 'id="configEmbeddingApiKey"' in html
    assert 'id="configEmbeddingModel"' in html
    assert 'id="saveEmbeddingConfig"' in html
    assert 'id="embeddingConfigStatus"' in html
    assert 'id="searchStatusPanel"' in html
    assert 'id="refreshFtsIndex"' in html
    assert 'id="refreshEmbeddingIndex"' in html
    assert 'id="useSearchContext"' in html
    assert 'id="useVectorContext"' in html
    assert 'id="forceWrites"' in html
    assert 'id="planChapter"' in html
    assert 'id="writeChapter"' in html
    assert 'id="polishChapter"' in html
    assert 'id="auditChapter"' in html
    assert 'id="exportMarkdown"' in html
    assert "/api/save-chapter-file" in app_js
    assert "/api/audit-annotations" in app_js
    assert "/api/init-project" in app_js
    assert "/api/setup/default-provider" in app_js
    assert "/api/setup/embedding" in app_js
    assert "/api/setup/recommend-port" in app_js
    assert "/api/setup/web-port" in app_js
    assert "/api/setup/open-web" in app_js
    assert "/api/inspire" in app_js
    assert "/api/read-file" in app_js
    assert "/api/canon/suggest" in app_js
    assert "/api/validate" in app_js
    assert "/api/runtime" in app_js
    assert "/api/search" in app_js
    assert "/api/migration-status" in app_js
    assert "/api/migrate" in app_js
    assert "/api/usage" in app_js
    assert "/api/session/revise-outline" in app_js
    assert "/api/session/revise-content" in app_js
    assert "/api/session/revise-audit" in app_js
    assert "/api/session/retry-rewrite" in app_js
    assert "/api/session/undo-rewrite" in app_js
    assert "/api/session/rewrite-events" in app_js
    assert "/api/orchestrator/memory-repair/suggest" in app_js
    assert "/api/orchestrator/memory-repair/apply" in app_js
    assert "/api/settings/change/suggest" in app_js
    assert "/api/settings/change/answer" in app_js
    assert "/api/settings/change/apply" in app_js
    assert 'id="settingChangeWorkbenchSuggest"' in html
    assert 'id="settingChangeClarificationAnswer"' in html
    assert 'id="settingChangeClarificationSubmit"' in html
    assert 'id="auditIssueAsSettingChange"' in html
    assert "/api/chapter-memory/generate" in app_js
    assert "/api/chapter-memory/rebuild" in app_js
    assert "/api/management-events" in app_js
    assert "/api/search-status" in app_js
    assert "/api/index/refresh" in app_js
    assert "纠正 Audit 理解并重新审核" in html
    assert "撤回本次打回" in html
    assert "查看被打回原文" in app_js
    assert "from_audit" in app_js
    assert "renderNextStep" in app_js
    assert "refreshAll({ silent: true })" in app_js
    assert "includeSessionId: false" in app_js
    assert "write_json: false" in app_js
    assert "clearProviderAgentConfig" in app_js
    assert "clear_agents" in app_js
    assert "backendVersionMismatchMessage" in app_js
    assert "warnBackendVersionMismatch" in app_js
    assert "Web UI 后台版本不匹配" in app_js
    assert "renderProviderEffectivePanel" in app_js
    assert "全部继承 default" in app_js
    assert "resizeTextareaToContent" not in app_js
    assert "saveEmbeddingConfig" in app_js
    assert 'hasResponseField(setup, "embedding_api")' in app_js
    assert "embeddingConfigEditing" in app_js
    assert "renderEmbeddingConfigPanel" in app_js
    assert "editEmbeddingConfig" in app_js
    assert 'status === "backend_mismatch"' in app_js
    assert "Embedding API 已配置" in app_js
    assert "模型：" in app_js
    assert "configEmbeddingBaseUrl" in app_js
    assert '$("configEmbeddingBaseUrl").value = ""' in app_js
    assert '$("configEmbeddingModel").value = ""' in app_js
    assert "with_embeddings: true" in app_js
    assert "Embedding API 已保存，但语义向量索引刷新失败" in app_js
    assert "vectorContextMode" in html
    assert "vector_context" in app_js
    assert "use_vector_context" in app_js
    assert "autoPolish" in html
    assert 'polish_mode: $("autoPolish").checked ? "auto" : "single_pass"' in app_js
    assert "当前无法使用基于 embedding 的语义检索" in app_js
    assert "fetch(" in app_js
    assert "loadRuntime" in app_js


def test_static_assets_are_served_from_web_static() -> None:
    css = static_asset_bytes("/static/app.css")
    js = static_asset_bytes("/static/app.js")
    missing = static_asset_bytes("/static/missing.js")
    traversal = static_asset_bytes("/static/../web_server.py")

    assert css is not None
    assert css[1] == "text/css; charset=utf-8"
    assert ".app-header" in css[0].decode("utf-8")
    assert js is not None
    assert js[1] == "application/javascript; charset=utf-8"
    assert "loadRuntime" in js[0].decode("utf-8")
    assert missing is None
    assert traversal is None


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


def test_api_session_rewrite_events_returns_summary_and_snapshot_path(tmp_path: Path) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    session_id = "session_20260529_010101_000002"
    session_dir = root / "memory" / "sessions" / session_id
    session_dir.mkdir(parents=True)
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
    snapshot = session_dir / "rejections" / "chapter_001_round_1_before.md"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("被打回原文", encoding="utf-8")
    atomic_write_model_json(session_dir / "session.json", session)
    atomic_write_model_json(
        session_dir / "rewrite_events.json",
        SessionRewriteEvents(
            events=[
                SessionRewriteEvent(
                    event_id="rewrite_ch001_round1_revision_rewrite_20260529_010101_000002",
                    session_id=session_id,
                    chapter_number=1,
                    round_number=1,
                    action="revision_rewrite",
                    status="unresolved",
                    trigger_audit_path="memory/chapters/001/audit.json",
                    rejected_text_snapshot_path=f"memory/sessions/{session_id}/rejections/chapter_001_round_1_before.md",
                    before_output_path="memory/chapters/001/polished.md",
                    after_output_path="memory/chapters/001/polished.md",
                    blocking_issues=[
                        {
                            "id": "audit_001_medium",
                            "severity": "medium",
                            "type": "state_conflict",
                            "description": "物品位置冲突。",
                            "evidence": [{"source": "polished.md", "quote": "错误位置"}],
                            "suggested_fix": "修正文。",
                        }
                    ],
                    created_at=now,
                    updated_at=now,
                )
            ]
        ),
    )

    status, payload = handle_api_request(
        "GET",
        "/api/session/rewrite-events",
        f"path={root}&session_id={session_id}",
        None,
    )

    assert status == 200
    assert payload["ok"] is True
    events = payload["data"]["rewrite_events"]  # type: ignore[index]
    assert events[0]["action"] == "revision_rewrite"
    assert events[0]["can_undo"] is True
    assert events[0]["undo_status"] == "not_requested"
    assert events[0]["status"] == "unresolved"
    assert events[0]["blocking_issues"][0]["description"] == "物品位置冲突。"

    read_status, read_payload = handle_api_request(
        "GET",
        "/api/read-file",
        f"path={root}&file=memory/sessions/{session_id}/rejections/chapter_001_round_1_before.md",
        None,
    )
    assert read_status == 200
    assert read_payload["data"]["content"] == "被打回原文"  # type: ignore[index]


def test_api_session_rewrite_control_endpoints_pass_event_id(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_ready_for_generation(tmp_path)
    session_id = "session_20260529_010101_000003"
    event_id = "rewrite_ch001_round1_revision_rewrite_20260529_010101_000003"
    session_dir = root / "memory" / "sessions" / session_id
    session_dir.mkdir(parents=True)
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
    atomic_write_model_json(session_dir / "session.json", session)
    captured: dict[str, object] = {}

    def fake_control(options) -> SessionResult:
        captured["session_id"] = options.session_id
        captured["event_id"] = options.event_id
        captured["instruction"] = options.instruction
        return SessionResult(session=session, session_path=session_dir / "session.json", message="controlled")

    monkeypatch.setattr("novel.web_api.revise_audit", fake_control)
    status, payload = handle_api_request(
        "POST",
        "/api/session/revise-audit",
        "",
        json.dumps(
            {
                "path": str(root),
                "session_id": session_id,
                "event_id": event_id,
                "instruction": "Audit 误解了回忆段落",
                "provider": "mock",
            }
        ),
    )

    assert status == 200
    assert payload["ok"] is True
    assert captured["session_id"] == session_id
    assert captured["event_id"] == event_id
    assert captured["instruction"] == "Audit 误解了回忆段落"


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


def test_api_chapter_memory_generate_endpoint(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter_memory_sources(tmp_path, [1])

    status, payload = handle_api_request(
        "POST",
        "/api/chapter-memory/generate",
        "",
        json.dumps({"path": str(root), "chapter_number": 1, "provider": "mock", "force": True}),
    )

    assert status == 200
    assert payload["ok"] is True
    data = payload["data"]
    assert data["generation_status"] == "model_generated"  # type: ignore[index]
    memory_path = root / "memory" / "chapters" / "001" / "chapter_memory.json"
    memory = load_json_model(memory_path, ChapterMemory)
    assert memory.source.polished_sha256 != "0" * 64
    chapters_status, chapters_payload = handle_api_request("GET", "/api/chapters", f"path={root}", None)
    assert chapters_status == 200
    chapter = chapters_payload["data"]["chapters"][0]  # type: ignore[index]
    assert chapter["has_chapter_memory"] is True
    assert chapter["chapter_memory_stale"] is False


def test_api_chapter_memory_rebuild_handles_missing_and_stale(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter_memory_sources(tmp_path, [1, 2])
    handle_api_request(
        "POST",
        "/api/chapter-memory/generate",
        "",
        json.dumps({"path": str(root), "chapter_number": 1, "provider": "mock", "force": True}),
    )
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text(polished_path.read_text(encoding="utf-8") + "\n补写一句。\n", encoding="utf-8")

    status, payload = handle_api_request(
        "POST",
        "/api/chapter-memory/rebuild",
        "",
        json.dumps({"path": str(root), "provider": "mock", "mode": "missing_or_stale"}),
    )

    assert status == 200
    assert payload["ok"] is True
    written = payload["data"]["written"]  # type: ignore[index]
    assert {item["chapter_number"] for item in written} == {1, 2}
    chapters_status, chapters_payload = handle_api_request("GET", "/api/chapters", f"path={root}", None)
    assert chapters_status == 200
    chapters = chapters_payload["data"]["chapters"]  # type: ignore[index]
    assert all(chapter["has_chapter_memory"] for chapter in chapters)
    assert all(chapter["chapter_memory_stale"] is False for chapter in chapters)


def test_api_chapters_marks_memory_stale_for_freshness_warnings(tmp_path: Path) -> None:
    root = _workspace_with_accepted_chapter_memory_sources(tmp_path, [1])
    handle_api_request(
        "POST",
        "/api/chapter-memory/generate",
        "",
        json.dumps({"path": str(root), "chapter_number": 1, "provider": "mock", "force": True}),
    )
    polished_path = root / "memory" / "chapters" / "001" / "polished.md"
    polished_path.write_text(
        polished_path.read_text(encoding="utf-8").replace("status: accepted", "status: polished", 1),
        encoding="utf-8",
    )

    status, payload = handle_api_request("GET", "/api/chapters", f"path={root}", None)

    assert status == 200
    chapter = payload["data"]["chapters"][0]  # type: ignore[index]
    assert chapter["has_chapter_memory"] is True
    assert chapter["chapter_memory_stale"] is True


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


def _workspace_with_accepted_chapter_memory_sources(tmp_path: Path, chapter_numbers: list[int]) -> Path:
    root = _workspace_ready_for_generation(tmp_path)
    for chapter_number in chapter_numbers:
        chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
        chapter_dir.mkdir(parents=True, exist_ok=True)
        (chapter_dir / "plan.json").write_text(default_mock_chapter_plan_json(chapter_number), encoding="utf-8")
        (chapter_dir / "polished.md").write_text(
            "---\n"
            f"chapter_number: {chapter_number}\n"
            f"title: 第 {chapter_number} 章\n"
            "status: accepted\n"
            "---\n\n"
            f"第 {chapter_number} 章正文。雨声更深，旧车站像在夜里醒来。\n",
            encoding="utf-8",
        )
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
