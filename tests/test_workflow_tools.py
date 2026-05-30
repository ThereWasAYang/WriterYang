from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from novel.core.workspace import InitOptions, init_workspace


SCRIPTS = (
    "scripts/install_writeryang.py",
    "scripts/check_local.py",
    "scripts/smoke_session.py",
    "scripts/debug_bundle.py",
    "scripts/provider_ping.py",
    "scripts/webui_smoke.py",
    "scripts/project_health.py",
)

SKILLS = (
    "skills/writeryang-maintainer/SKILL.md",
    "skills/writeryang-workflow-debug/SKILL.md",
    "skills/writeryang-real-api-smoke/SKILL.md",
    "skills/writeryang-web-ui-qa/SKILL.md",
    "skills/writeryang-release/SKILL.md",
)

AGENT_SKILLS = (
    "skills/writeryang-agent-orchestrator/SKILL.md",
    "skills/writeryang-agent-inspiration/SKILL.md",
    "skills/writeryang-agent-canon/SKILL.md",
    "skills/writeryang-agent-plot/SKILL.md",
    "skills/writeryang-agent-writer/SKILL.md",
    "skills/writeryang-agent-polish/SKILL.md",
    "skills/writeryang-agent-audit/SKILL.md",
    "skills/writeryang-agent-state-update/SKILL.md",
    "skills/writeryang-agent-revision/SKILL.md",
)

ALL_SKILLS = (*SKILLS, *AGENT_SKILLS)


def test_workflow_scripts_have_help() -> None:
    for rel_path in SCRIPTS:
        completed = _run_script(rel_path, "--help")
        assert completed.returncode == 0
        assert "usage:" in completed.stdout


def test_check_local_dry_run_lists_quality_gate() -> None:
    completed = _run_script("scripts/check_local.py", "--dry-run", "--json", "--skip-build")
    payload = json.loads(completed.stdout)
    names = {item["name"] for item in payload["checks"]}

    assert completed.returncode == 0
    assert payload["ok"] is True
    assert {"pytest", "ruff", "mypy", "secret-scan"} <= names
    assert "build" not in names


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


def test_skills_have_frontmatter_and_reference_tools() -> None:
    for rel_path in ALL_SKILLS:
        text = Path(rel_path).read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "\nname:" in text
        assert "\ndescription:" in text
    combined = "\n".join(Path(path).read_text(encoding="utf-8") for path in SKILLS)
    assert "scripts/check_local.py" in combined
    assert "scripts/debug_bundle.py" in combined
    assert "scripts/provider_ping.py" in combined


def test_agent_skills_are_isolated_by_agent() -> None:
    agent_names = {Path(path).parent.name.removeprefix("writeryang-agent-") for path in AGENT_SKILLS}

    for rel_path in AGENT_SKILLS:
        path = Path(rel_path)
        agent = path.parent.name.removeprefix("writeryang-agent-")
        text = path.read_text(encoding="utf-8")
        lower_text = text.lower()

        assert f"name: writeryang-agent-{agent}" in text
        assert "Use this skill only for" in text
        assert text.count("# WriterYang") == 1

        for other in agent_names - {agent}:
            other_title = other.replace("-", " ").title()
            assert f"# writeryang {other_title} agent".lower() not in lower_text
            assert f"use this skill only for the {other_title}".lower() not in lower_text


def test_creative_agent_skills_do_not_prescribe_creative_templates() -> None:
    creative_skill_paths = (
        "skills/writeryang-agent-inspiration/SKILL.md",
        "skills/writeryang-agent-plot/SKILL.md",
        "skills/writeryang-agent-writer/SKILL.md",
    )
    banned_phrases = (
        "必须按三幕式",
        "固定剧情模板",
        "三幕式模板",
        "角色弧光公式",
        "如何写出好看正文",
        "如何设计精彩剧情",
        "创造人物弧光",
    )

    for rel_path in creative_skill_paths:
        text = Path(rel_path).read_text(encoding="utf-8")
        for phrase in banned_phrases:
            assert phrase not in text


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
