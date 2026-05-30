from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from importlib import metadata
from io import StringIO
import subprocess
import sys
from pathlib import Path

import novel
from novel.core.io import load_yaml
from novel.cli import build_parser, main
from novel.core.validation import validate_project


def test_novel_version_command_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "novel", "--version"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == f"novel {novel.__version__}"
    assert result.stderr == ""


def test_example_project_validates() -> None:
    report = validate_project(Path("examples/rain_station"))

    assert report.ok, [message.message for message in report.messages]


def test_wuxia_example_project_validates() -> None:
    report = validate_project(Path("examples/wuxia_mountain_sect"))

    assert report.ok, [message.message for message in report.messages]


def test_example_project_validates_from_cli() -> None:
    code, stdout, stderr = _run_cli(["validate", "--path", "examples/rain_station", "--json"])

    assert code == 0
    assert stderr == ""
    assert '"ok": true' in stdout


def test_wuxia_example_project_validates_from_cli() -> None:
    code, stdout, stderr = _run_cli(["validate", "--path", "examples/wuxia_mountain_sect", "--json"])

    assert code == 0
    assert stderr == ""
    assert '"ok": true' in stdout


def test_example_agent_configs_include_real_and_mock_templates() -> None:
    real_config = load_yaml(Path("examples/rain_station/config/agents.yaml"))
    mock_config = load_yaml(Path("examples/rain_station/config/agents.mock.yaml"))

    default = real_config["default"]
    writer = real_config["agents"]["writer"]
    assert default["provider"] == "deepseek"
    assert default["thinking"]["type"] == "disabled"
    assert default["base_url_env"] == "WRITERYANG_REAL_BASE_URL"
    assert default["api_key_env"] == "WRITERYANG_REAL_API_KEY"
    assert writer["temperature"] == 0.9
    assert "provider" not in writer

    assert mock_config["agents"]["writer"]["provider"] == "mock"


def test_readme_core_commands_match_cli() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    parser_help = build_parser().format_help()

    for command in (
        "novel init",
        "novel validate",
        "novel migrate",
        "novel schema export",
        "novel status",
        "novel inspire",
        "novel canon suggest",
        "novel plan-chapter",
        "novel write-chapter",
        "novel polish-chapter",
        "novel audit-chapter",
        "novel accept-chapter",
        "novel export markdown",
        "novel --version",
        "novel validate --path examples/wuxia_mountain_sect",
        "./install.sh",
        "python scripts/install_writeryang.py --dry-run",
    ):
        assert command in readme
    for parser_command in (
        "init",
        "validate",
        "migrate",
        "schema",
        "status",
        "inspire",
        "canon",
        "plan-chapter",
        "write-chapter",
        "polish-chapter",
        "audit-chapter",
        "generate-chapter",
        "export",
    ):
        assert parser_command in parser_help


def test_user_docs_exist_and_mention_core_workflow() -> None:
    docs = {
        "docs/WEB_UI_USER_GUIDE.md": (
            "Web UI",
            "Session",
            "修改大纲",
            "按 Audit 修订内容",
            "项目检查",
            "导出 Markdown",
            "使用搜索上下文",
            "使用 embedding 语义检索",
            "刷新关键词索引",
            "刷新语义向量索引",
            "Provider 配置",
            "状态 / 时间线",
            "Rewrite Event ID",
            "后台管理动态",
        ),
        "docs/QUICKSTART.md": ("novel inspire", "novel canon apply", "novel export markdown"),
        "docs/MEMORY_EDITING.md": ("hidden_truths.json", "reader_visible_summary", "novel validate"),
        "docs/MODEL_CONFIG_BEST_PRACTICES.md": ("provider", "thinking", "temperature"),
    }
    for rel_path, expected in docs.items():
        text = Path(rel_path).read_text(encoding="utf-8")
        for phrase in expected:
            assert phrase in text


def test_readme_links_web_ui_user_guide() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "docs/WEB_UI_USER_GUIDE.md" in readme
    assert "Web UI 的 Session 流程" in readme
    assert readme.index("## 环境配置") < readme.index("docs/WEB_UI_USER_GUIDE.md")
    assert readme.index("## 安装") < readme.index("docs/WEB_UI_USER_GUIDE.md")


def test_user_docs_use_generic_environment_setup() -> None:
    docs = [
        Path("README.md"),
        Path("docs/QUICKSTART.md"),
        Path("docs/RELEASE.md"),
        Path("CONTRIBUTING.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    assert "conda create -n writeryang" in combined
    assert "python=3.12" in combined
    local_env_name = "py" + "312"
    local_conda_run = "conda run " + "-n " + local_env_name
    assert local_conda_run not in combined
    assert local_env_name not in combined


def test_readme_mentions_workflow_skills_and_scripts() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for phrase in (
        "skills/",
        "scripts/check_local.py",
        "scripts/smoke_session.py",
        "scripts/debug_bundle.py",
        "scripts/provider_ping.py",
    ):
        assert phrase in readme


def test_github_workflows_cover_quality_build_and_release() -> None:
    tests_workflow = Path(".github/workflows/tests.yml").read_text(encoding="utf-8")
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for phrase in ("pytest", "python -m build", "twine check", "ruff check", "mypy src", "scan_security"):
        assert phrase in tests_workflow
    for phrase in ("tags:", "v*", "softprops/action-gh-release", "dist/*", "scan_security"):
        assert phrase in release_workflow


def test_package_console_script_entry_point_is_declared() -> None:
    entry_points = metadata.entry_points(group="console_scripts")
    novel_points = [entry for entry in entry_points if entry.name == "novel"]

    assert novel_points
    assert novel_points[0].value == "novel.cli:main"


def test_packaging_metadata_version_matches_package() -> None:
    assert metadata.version("writeryang") == novel.__version__


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
