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
    assert plan.commands[-1][-1] == "."
    assert plan.activation_command == "conda activate WriterYang_260531"


def test_conda_plan_uses_suffix_when_env_exists(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
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
    assert plan.commands[-1][-1] == "."
    assert "activate" in plan.activation_command


def test_dev_plan_installs_dev_extra(monkeypatch, tmp_path: Path) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())

    plan = installer.build_install_plan(
        repo_root=tmp_path,
        dev=True,
        venv_root=tmp_path / ".venv",
        env={},
        now=datetime(2026, 5, 31),
    )

    assert plan.commands[-1][-1] == ".[dev]"


def test_dry_run_does_not_execute_create_or_install(monkeypatch, capsys) -> None:
    installer = _load_installer()
    monkeypatch.setattr(installer, "find_conda", lambda env: "/opt/conda/bin/conda")
    monkeypatch.setattr(installer, "existing_conda_env_names", lambda conda: set())

    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    code = installer.main(["--dry-run"])

    assert code == 0
    assert calls == []
    assert "[dry-run]" in capsys.readouterr().out
