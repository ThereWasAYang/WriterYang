from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

from novel.core.workspace import InitOptions, init_workspace


SCRIPTS = (
    "scripts/install_writeryang.py",
    "scripts/check_local.py",
    "scripts/smoke_session.py",
    "scripts/debug_bundle.py",
    "scripts/provider_ping.py",
    "scripts/webui_smoke.py",
    "scripts/project_health.py",
    "scripts/install_git_hooks.py",
    "scripts/capture_webui_guide_screenshots.py",
)

DOCS_AND_TESTS_WITHOUT_REMOVED_SKILL_LAYER = (
    "README.md",
    "docs/DEVELOPER_GUIDE.md",
    "docs/CODEBASE_REFERENCE.md",
    "tests/test_workflow_tools.py",
    "tests/test_packaging.py",
    "tests/test_developer_docs.py",
)


def test_workflow_scripts_have_help() -> None:
    for rel_path in SCRIPTS:
        completed = _run_script(rel_path, "--help")
        assert completed.returncode == 0
        assert "usage:" in completed.stdout


def test_repository_does_not_restore_removed_external_agent_skill_layer() -> None:
    assert not Path("skills").exists()


def test_docs_and_tests_do_not_reference_removed_external_agent_skill_layer() -> None:
    forbidden_phrases = (
        "skills" + "/",
        "SKILL" + ".md",
        "渐进" + "式披露",
        "Workflow " + "Skills",
        "workflow " + "skill",
    )

    for rel_path in DOCS_AND_TESTS_WITHOUT_REMOVED_SKILL_LAYER:
        text = Path(rel_path).read_text(encoding="utf-8")
        for phrase in forbidden_phrases:
            assert phrase not in text, f"{rel_path} still references removed external Agent skill layer"


def test_check_local_dry_run_lists_quality_gate() -> None:
    completed = _run_script("scripts/check_local.py", "--dry-run", "--json", "--skip-build")
    payload = json.loads(completed.stdout)
    names = {item["name"] for item in payload["checks"]}
    checks = {item["name"]: item for item in payload["checks"]}

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert {"pytest", "ruff", "mypy", "secret-scan"} <= names
    assert checks["mypy"]["blocking"] is True
    assert checks["mypy"]["command"][-2:] == ["src", "scripts"]
    assert "build" not in names


def test_install_git_hooks_dry_run_reports_pre_push_hook() -> None:
    completed = _run_script("scripts/install_git_hooks.py", "--dry-run", "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["hooks_path"] == ".githooks"
    assert payload["command"] == ["git", "config", "core.hooksPath", ".githooks"]


def test_pre_push_hook_supports_explicit_skip() -> None:
    completed = subprocess.run(
        ["sh", ".githooks/pre-push"],
        text=True,
        capture_output=True,
        env={**os.environ, "WRITERYANG_SKIP_PRE_PUSH": "1"},
    )

    assert completed.returncode == 0
    assert "skipped" in completed.stderr


def test_pre_push_hook_runs_mock_check_command() -> None:
    completed = subprocess.run(
        ["sh", ".githooks/pre-push"],
        text=True,
        capture_output=True,
        env={**os.environ, "WRITERYANG_PRE_PUSH_CHECK_COMMAND": f"{sys.executable} -c 'import sys; sys.exit(0)'"},
    )

    assert completed.returncode == 0
    assert "pre-push check command" in completed.stderr


def test_smoke_session_dry_run_lists_session_flow(tmp_path: Path) -> None:
    completed = _run_script(
        "scripts/smoke_session.py",
        "--project",
        str(tmp_path / "novel"),
        "--dry-run",
        "--json",
    )
    payload = json.loads(completed.stdout)
    flattened = " ".join(" ".join(command) for command in payload["commands"])

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert "session start" in flattened
    assert "canon apply" in flattened
    assert "--project" in flattened
    assert "--path" not in flattened


def test_smoke_session_dry_run_without_project_does_not_create_workspace(tmp_path: Path) -> None:
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    completed = _run_script(
        "scripts/smoke_session.py",
        "--dry-run",
        "--json",
        env={"TMPDIR": str(temp_root)},
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert list(temp_root.iterdir()) == []


def test_smoke_session_config_provider_uses_real_env_names_only(tmp_path: Path, monkeypatch) -> None:
    root = _workspace(tmp_path)
    module = _load_script_module("scripts/smoke_session.py")
    monkeypatch.setenv("WRITERYANG_REAL_API_KEY", "secret-real-key")
    monkeypatch.setenv("WRITERYANG_REAL_PROVIDER", "deepseek")

    module._patch_default_api_config(root, provider="config", model="deepseek-v4-pro")

    text = (root / "config" / "agents.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["default"]["provider"] == "deepseek"
    assert data["default"]["api_key_env"] == "WRITERYANG_REAL_API_KEY"
    assert data["default"]["base_url_env"] == "WRITERYANG_REAL_BASE_URL"
    assert data["default"]["model"] == "deepseek-v4-pro"
    assert "secret-real-key" not in text


def test_webui_smoke_dry_run_does_not_bind_port() -> None:
    completed = _run_script("scripts/webui_smoke.py", "--dry-run", "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["url"] == "http://127.0.0.1:8765"


def test_provider_ping_mock_does_not_need_real_api(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    completed = _run_script(
        "scripts/provider_ping.py",
        "--project",
        str(root),
        "--provider",
        "mock",
        "--agent",
        "writer",
        "--json",
    )
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert payload["agents"][0]["status"] == "success"
    assert "api_key_env_set" in payload["agents"][0]


def test_debug_bundle_redacts_env_secret(tmp_path: Path, monkeypatch) -> None:
    root = _workspace(tmp_path)
    secret = "super-secret-value-for-debug-bundle"
    monkeypatch.setenv("WRITERYANG_TEST_API_KEY", secret)
    model_io = root / "runs" / "model_io"
    model_io.mkdir(parents=True)
    (model_io / "secret.json").write_text(
        json.dumps({"request": {"user_prompt": secret}}, ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "bundle"

    completed = _run_script(
        "scripts/debug_bundle.py",
        "--project",
        str(root),
        "--output",
        str(output),
        "--json",
        env={"WRITERYANG_TEST_API_KEY": secret},
    )

    assert completed.returncode == 0
    assert secret not in output.joinpath("runs/model_io/secret.json").read_text(encoding="utf-8")
    assert secret not in completed.stdout


def test_project_health_json_reports_workspace(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    completed = _run_script("scripts/project_health.py", "--project", str(root), "--json")
    payload = json.loads(completed.stdout)

    assert completed.returncode == 0
    assert payload["project"] == str(root)
    assert payload["validate"]["ok"] is True


def test_workflow_scripts_use_project_alias_for_internal_cli_calls() -> None:
    scripts = (
        "scripts/smoke_session.py",
        "scripts/debug_bundle.py",
        "scripts/webui_smoke.py",
        "scripts/project_health.py",
    )
    for rel_path in scripts:
        text = Path(rel_path).read_text(encoding="utf-8")
        assert '"--project"' in text
        assert '"--path"' not in text


def test_tool_scripts_do_not_directly_call_creative_services() -> None:
    forbidden_calls = (
        "run_inspiration_agent(",
        "suggest_canon(",
        "apply_canon_proposal(",
        "plan_chapter(",
        "write_chapter_draft(",
        "polish_chapter(",
        "audit_chapter(",
        "propose_state_update(",
        "revise_chapter(",
    )

    for rel_path in SCRIPTS:
        text = Path(rel_path).read_text(encoding="utf-8")
        for call in forbidden_calls:
            assert call not in text, f"{rel_path} should not call {call} directly"


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="脚本测试", root=root))
    return root


def _run_script(rel_path: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    script = Path(rel_path)
    full_env = os.environ.copy()
    full_env["PYTHONPATH"] = str(Path.cwd() / "src") + os.pathsep + full_env.get("PYTHONPATH", "")
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(script), *args],
        text=True,
        capture_output=True,
        env=full_env,
    )


def _load_script_module(rel_path: str):
    spec = importlib.util.spec_from_file_location("writeryang_script_under_test", Path(rel_path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
