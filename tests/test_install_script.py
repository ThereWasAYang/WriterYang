from __future__ import annotations

from datetime import datetime
import importlib.util
from pathlib import Path
import subprocess
import sys


def _load_installer():
    path = Path("scripts/install_writeryang.py")
    spec = importlib.util.spec_from_file_location("install_writeryang", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_unique_env_name_without_conflict() -> None:
    installer = _load_installer()

    assert installer.unique_name("WriterYang_260531", set()) == "WriterYang_260531"


def test_unique_env_name_adds_suffix_for_conflict() -> None:
    installer = _load_installer()

    assert (
        installer.unique_name("WriterYang_260531", {"WriterYang_260531", "WriterYang_26053101"})
        == "WriterYang_26053102"
    )


def test_conda_plan_is_preferred_when_conda_exists(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.mode == "conda"
    assert plan.env_name == "WriterYang_260531"
    assert plan.commands[0] == ["/opt/conda/bin/conda", "create", "-n", "WriterYang_260531", "python=3.12", "-y"]
    assert plan.commands[1] == [
        "/opt/conda/envs/WriterYang_260531/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "pip",
    ]
    assert plan.commands[2][0] == "/opt/conda/envs/WriterYang_260531/bin/python"
    assert plan.commands[-1][-2:] == ["-e", "."]
    assert plan.activation_command == "conda activate WriterYang_260531"
    assert plan.web_url == "http://127.0.0.1:8765"
    assert plan.web_command == [
        "/opt/conda/envs/WriterYang_260531/bin/novel",
        "web",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    assert plan.launcher_path == tmp_path / "WriterYang_WebUI.command"
    assert plan.launcher_command == plan.web_command
    assert plan.activate_shell is not None
    assert plan.activate_shell.command[-1] == "-i"
    assert plan.activate_shell.env is not None
    assert plan.activate_shell.env["CONDA_DEFAULT_ENV"] == "WriterYang_260531"
    assert plan.activate_shell.env["CONDA_PREFIX"] == "/opt/conda/envs/WriterYang_260531"
    assert plan.activate_shell.env["PATH"].startswith("/opt/conda/envs/WriterYang_260531/bin")


def test_conda_plan_uses_suffix_when_env_exists(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)
    monkeypatch.setattr(
        installer,
        "existing_conda_env_names",
        lambda conda: {"WriterYang_260531", "WriterYang_26053101"},
    )

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.env_name == "WriterYang_26053102"


def test_venv_plan_when_conda_missing(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: None)
    monkeypatch.setattr(installer, "find_python312", lambda: "/usr/bin/python3.12")
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.mode == "venv"
    assert plan.env_name == "WriterYang_260531"
    assert plan.commands[0] == ["/usr/bin/python3.12", "-m", "venv", str((tmp_path / ".venv" / "WriterYang_260531").resolve())]
    assert plan.commands[-1][-2:] == ["-e", "."]
    assert "activate" in plan.activation_command
    assert plan.web_url == "http://127.0.0.1:8765"
    assert plan.web_command == [
        str((tmp_path / ".venv" / "WriterYang_260531" / "bin" / "novel").resolve()),
        "web",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
    ]
    assert plan.launcher_command == plan.web_command
    assert plan.activate_shell is not None
    assert plan.activate_shell.command[-1] == "-i"
    assert plan.activate_shell.env is not None
    assert plan.activate_shell.env["VIRTUAL_ENV"] == str((tmp_path / ".venv" / "WriterYang_260531").resolve())


def test_dev_plan_installs_dev_extra(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=True,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.commands[-1][-2:] == ["-e", ".[dev]"]


def test_installer_uses_editable_install_args() -> None:
    installer = _load_installer()

    assert installer.editable_install_args(dev=False) == ["-e", "."]
    assert installer.editable_install_args(dev=True) == ["-e", ".[dev]"]


def test_web_port_uses_next_available_port(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: port == 8766)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.web_url == "http://127.0.0.1:8766"
    assert plan.web_command[-1] == "8766"


def test_web_port_can_start_from_custom_port(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: port == 9001)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
        web_port=9000,
    )

    assert plan.web_url == "http://127.0.0.1:9001"
    assert plan.web_command[-1] == "9001"


def test_no_web_keeps_launcher_and_activate_shell(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
        start_web=False,
    )

    assert plan.web_url == "http://127.0.0.1:8765"
    assert plan.web_command is None
    assert plan.launcher_command is not None
    assert plan.activate_shell is not None


def test_no_activate_shell_disables_shell_launch(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=False,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
        activate_shell=False,
    )

    assert plan.activate_shell is None


def test_write_web_launcher_creates_executable_command_file(tmp_path: Path) -> None:
    installer = _load_installer()
    launcher = tmp_path / "WriterYang_WebUI.command"
    command = ["/opt/conda/envs/WriterYang_260531/bin/novel", "web", "--port", "8765"]

    installer._write_web_launcher(launcher, command, cwd=tmp_path, url="http://127.0.0.1:8765")

    content = launcher.read_text(encoding="utf-8")
    assert "WriterYang_260531" in content
    assert "novel web" in content
    assert "http://127.0.0.1:8765" in content
    assert "/dev/tcp/$WRITERYANG_WEB_HOST/$WRITERYANG_WEB_PORT" in content
    assert launcher.stat().st_mode & 0o111


def test_dry_run_does_not_execute_create_or_install(monkeypatch, capsys) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    code = installer.main(["--dry-run"])

    assert code == 0
    assert calls == []
    assert "[dry-run]" in capsys.readouterr().out


def test_no_open_web_starts_server_without_browser(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)
    opened: list[str] = []
    calls: list[list[str]] = []
    web_calls: list[list[str]] = []
    monkeypatch.setattr(installer.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(installer, "_wait_for_web_server", lambda url: True)

    def fake_run(command, *, cwd):
        calls.append(command)

    class FakeProcess:
        def wait(self):
            return 0

    def fake_popen(command, *, cwd):
        web_calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(installer, "_run", fake_run)
    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)

    code = installer.main(["--no-open-web", "--launcher-path", str(tmp_path / "WriterYang_WebUI.command")])

    assert code == 0
    assert opened == []
    assert web_calls[-1][-4:] == ["--host", "127.0.0.1", "--port", "8765"]


def test_default_install_opens_browser_after_server_is_ready(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)
    opened: list[str] = []
    calls: list[list[str]] = []
    events: list[str] = []

    def fake_open(url):
        events.append("open")
        opened.append(url)

    def fake_wait_for_web_server(url):
        events.append("ready")
        return True

    monkeypatch.setattr(installer.webbrowser, "open", fake_open)
    monkeypatch.setattr(installer, "_wait_for_web_server", fake_wait_for_web_server)

    def fake_run(command, *, cwd):
        calls.append(command)

    class FakeProcess:
        def wait(self):
            events.append("wait")
            return 0

    def fake_popen(command, *, cwd):
        events.append("popen")
        calls.append(command)
        return FakeProcess()

    monkeypatch.setattr(installer, "_run", fake_run)
    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)

    code = installer.main(["--launcher-path", str(tmp_path / "WriterYang_WebUI.command")])

    assert code == 0
    assert opened == ["http://127.0.0.1:8765"]
    assert calls[-1][-4:] == ["--host", "127.0.0.1", "--port", "8765"]
    assert events[-4:] == ["popen", "ready", "open", "wait"]


def test_interactive_no_web_enters_new_environment_shell(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())
    monkeypatch.setattr(installer, "is_port_available", lambda host, port: True)
    monkeypatch.setattr(installer, "is_interactive_terminal", lambda env: True)
    calls: list[list[str]] = []
    envs: list[dict[str, str] | None] = []

    def fake_run(command, *, cwd, env=None):
        calls.append(command)
        envs.append(env)

    monkeypatch.setattr(installer, "_run", fake_run)

    code = installer.main(["--no-web", "--launcher-path", str(tmp_path / "WriterYang_WebUI.command")])

    assert code == 0
    assert calls[-1][-1] == "-i"
    assert envs[-1] is not None
    assert envs[-1]["CONDA_DEFAULT_ENV"].startswith("WriterYang_")
    assert envs[-1]["PATH"].startswith("/opt/conda/envs/")
