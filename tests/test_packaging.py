from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
from importlib import metadata
from io import StringIO
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import novel
from novel.core.io import load_yaml
from novel.cli import build_parser, main
from novel.core.validation import validate_project
from novel.core.workspace import InitOptions, init_workspace


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


def test_initialized_template_project_validates(tmp_path: Path) -> None:
    root = tmp_path / "template-project"
    init_workspace(InitOptions(title="模板校验", root=root))

    report = validate_project(root)

    assert report.ok, [message.message for message in report.messages]


def test_initialized_template_project_validates_from_cli(tmp_path: Path) -> None:
    root = tmp_path / "template-project"
    init_code, init_stdout, init_stderr = _run_cli(
        ["init", "模板校验", "--path", str(root), "--no-guide", "--json", "--quiet"]
    )
    code, stdout, stderr = _run_cli(["validate", "--path", str(root), "--json", "--quiet"])

    assert init_code == 0
    assert init_stderr == ""
    assert '"ok": true' in init_stdout
    assert code == 0
    assert stderr == ""
    assert '"ok": true' in stdout


def test_initialized_template_configs_include_provider_defaults(tmp_path: Path) -> None:
    root = tmp_path / "template-project"
    init_workspace(InitOptions(title="模板校验", root=root))

    agents_config = load_yaml(root / "config" / "agents.yaml")
    embeddings_config = load_yaml(root / "config" / "embeddings.yaml")

    default = agents_config["default"]
    scribe = agents_config["profiles"]["scribe"]
    clerk = agents_config["profiles"]["clerk"]
    assert default["provider"] == "openai_compatible"
    assert default["base_url_env"] == "OPENAI_BASE_URL"
    assert default["api_key_env"] == "OPENAI_API_KEY"
    assert default["json_response_format"] == "auto"
    assert default["max_tokens"] == 24000
    assert default["max_context_tokens"] == 128000
    assert default["timeout_seconds"] == 120
    assert "temperature" not in default
    assert "reasoning" not in default
    assert "thinking" not in default
    assert scribe["inherit_default"] is True
    assert "provider" not in scribe
    assert "model" not in scribe
    assert "temperature" not in scribe
    assert "reasoning" not in scribe
    assert "thinking" not in scribe
    assert clerk["inherit_default"] is True
    assert "provider" not in clerk
    assert "model" not in clerk
    assert embeddings_config["active_provider"] == "dashscope"
    assert embeddings_config["providers"]["test_local_hash"]["provider"] == "local_hash"
    assert embeddings_config["providers"]["dashscope"]["api_key_env"] == "DASHSCOPE_API_KEY"


def test_readme_documents_all_profile_model_roles() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for profile_name in (
        "scribe",
        "architect",
        "loremaster",
        "clerk",
    ):
        assert f"| `{profile_name}` |" in readme


def test_readme_core_commands_match_cli() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    command_docs = Path("docs/CLI_COMMANDS.md").read_text(encoding="utf-8")
    combined_docs = readme + "\n" + command_docs
    parser_help = build_parser().format_help()

    for command in (
        "novel init",
        "novel validate",
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
        'tmp_project="$(mktemp -d)/writeryang-template"',
        'novel init "模板校验" --path "$tmp_project" --no-guide',
        "./install.sh",
        "python scripts/install_writeryang.py --dry-run",
        "python scripts/install_writeryang.py --web-port 9000",
        "python scripts/install_writeryang.py --no-web",
    ):
        assert command in combined_docs
    assert "Windows 适配暂缓" in readme
    assert ".\\install.ps1" not in readme
    assert "install.bat" not in readme
    for parser_command in (
        "init",
        "validate",
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
            "Profile 模型配置",
            "状态和时间线",
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
    assert "Web UI 小白图文使用指南" in readme
    assert readme.index("## 环境配置") < readme.index("docs/WEB_UI_USER_GUIDE.md")
    assert readme.index("## 安装") < readme.index("docs/WEB_UI_USER_GUIDE.md")


def test_web_ui_user_guide_references_existing_screenshots() -> None:
    guide_path = Path("docs/WEB_UI_USER_GUIDE.md")
    guide = guide_path.read_text(encoding="utf-8")
    image_paths = re.findall(r"!\[[^\]]+\]\((assets/web-ui-guide/[^)]+\.png)\)", guide)

    assert len(image_paths) >= 8
    for image_path in image_paths:
        assert (guide_path.parent / image_path).exists(), image_path


def test_manifest_includes_user_and_developer_docs() -> None:
    manifest = Path("MANIFEST.in").read_text(encoding="utf-8")

    for expected in (
        "include install.sh",
        "include install.ps1",
        "include install.bat",
        "include scripts/install_writeryang.py",
        "include docs/CLI_COMMANDS.md",
        "include docs/WEB_UI_USER_GUIDE.md",
        "include docs/DEVELOPER_GUIDE.md",
        "include docs/CODEBASE_REFERENCE.md",
        "include docs/AGENT_PROMPT_ASSEMBLY.md",
        "include docs/DEBUGGING_AND_REFACTORING.md",
        "recursive-include docs/assets/web-ui-guide *.png",
    ):
        assert expected in manifest


def test_github_workflows_include_web_e2e_and_blocking_mypy() -> None:
    tests_workflow = Path(".github/workflows/tests.yml").read_text(encoding="utf-8")
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for version in ('"3.11"', '"3.12"', '"3.13"'):
        assert version in tests_workflow
    for workflow in (tests_workflow, release_workflow):
        assert "Type check" in workflow
        assert "mypy src scripts" in workflow
        assert "continue-on-error: true" not in workflow
        assert "python -m playwright install chromium" in workflow
        assert "pytest -m web_e2e -q" in workflow


def test_web_ui_guide_screenshot_script_dry_run_does_not_write(tmp_path: Path, capsys) -> None:
    path = Path("scripts/capture_webui_guide_screenshots.py")
    spec = importlib.util.spec_from_file_location("capture_webui_guide_screenshots", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    output = tmp_path / "screenshots"

    code = module.main(["--dry-run", "--output", str(output), "--json"])

    assert code == 0
    assert not output.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert "overview.png" in payload["screenshots"]


def test_user_docs_use_generic_environment_setup() -> None:
    docs = [
        Path("README.md"),
        Path("docs/QUICKSTART.md"),
        Path("docs/RELEASE.md"),
        Path("CONTRIBUTING.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)

    assert "conda create -n writeryang" in combined
    assert "支持 Python 3.11-3.13" in combined
    assert "推荐 3.12" in combined
    assert "python>=3.11,<3.14" in combined
    assert "python=3.12" not in combined
    assert "python3.12 -m venv" not in combined
    local_env_name = "py" + "312"
    local_conda_run = "conda run " + "-n " + local_env_name
    assert local_conda_run not in combined
    assert local_env_name not in combined


def test_user_docs_document_prelaunch_platform_and_runtime_limits() -> None:
    docs = [
        Path("README.md"),
        Path("docs/QUICKSTART.md"),
        Path("docs/WEB_UI_USER_GUIDE.md"),
        Path("docs/RELEASE.md"),
        Path("docs/DEBUGGING_AND_REFACTORING.md"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs)
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")

    assert "Windows 适配暂缓" in combined
    assert "推广初期" in combined
    assert "WRITERYANG_WEB_MAX_BODY_BYTES" in combined
    assert "model_io" in changelog
    assert "默认保留最近 500 份" in changelog


def test_tracked_markdown_docs_do_not_reference_local_only_internal_docs() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "*.md"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    local_only_paths = (
        "AGENTS" + ".md",
        "docs/" + "PRODUCT_SPEC" + ".md",
        "docs/" + "ARCHITECTURE" + ".md",
        "docs/" + "DATA_SCHEMA" + ".md",
        "docs/" + "WORKFLOW" + ".md",
        "docs/" + "ROADMAP" + ".md",
    )

    for rel_path in completed.stdout.splitlines():
        text = Path(rel_path).read_text(encoding="utf-8")
        for local_only_path in local_only_paths:
            assert local_only_path not in text, f"{rel_path} references local-only internal docs"


def test_readme_mentions_workflow_scripts() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for phrase in (
        "scripts/check_local.py",
        "scripts/smoke_session.py",
        "scripts/debug_bundle.py",
        "scripts/provider_ping.py",
    ):
        assert phrase in readme


def test_github_workflows_cover_quality_build_and_release() -> None:
    tests_workflow = Path(".github/workflows/tests.yml").read_text(encoding="utf-8")
    release_workflow = Path(".github/workflows/release.yml").read_text(encoding="utf-8")

    for phrase in ("pytest", "python -m build", "twine check", "ruff check", "mypy src scripts", "scan_security"):
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


def test_project_license_metadata_is_declared() -> None:
    license_path = Path("LICENSE")
    readme = Path("README.md").read_text(encoding="utf-8")
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert license_path.exists()
    assert "Copyright 2026 ThereWasAYang." in readme
    assert pyproject["project"]["license"] == "Apache-2.0"
    assert pyproject["project"]["license-files"] == ["LICENSE"]


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
