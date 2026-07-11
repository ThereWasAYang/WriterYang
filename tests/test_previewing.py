from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest

from novel.cli import main
from novel.core.contracts import PreviewManifest
from novel.core.io import load_json_model
from novel.core.previewing import PreviewError, PreviewPackageOptions, build_preview_package
from novel.core.workspace import InitOptions, init_workspace
from novel.web_api import handle_api_request


def test_preview_packages_working_candidate_without_production_manifest(tmp_path: Path) -> None:
    root = _workspace_with_working_chapter(tmp_path)
    production_manifest = root / "exports" / "export_manifest.json"
    production_manifest.write_bytes(b"production-manifest-sentinel\n")

    result = build_preview_package(PreviewPackageOptions(root=root, chapters=(1,)))

    assert production_manifest.read_bytes() == b"production-manifest-sentinel\n"
    assert result.package_dir.parent == root / "exports" / "previews"
    assert result.manifest.package_kind == "preview"
    assert result.manifest.production_eligible is False
    assert result.manifest.source_chapters[0].source_kind == "polished"
    assert result.manifest.source_chapters[0].artifact_ref is None
    content = result.content_path.read_text(encoding="utf-8")
    assert content.startswith("# [PREVIEW] 雨夜旧车站")
    assert "非正式导出" in content
    assert "working candidate" in content
    assert load_json_model(result.manifest_path, PreviewManifest) == result.manifest


def test_preview_rejects_missing_selected_source(tmp_path: Path) -> None:
    root = _workspace_with_working_chapter(tmp_path)

    with pytest.raises(PreviewError, match="no working draft.md"):
        build_preview_package(
            PreviewPackageOptions(root=root, chapters=(1,), source_kind="draft")
        )


def test_preview_cli_and_web_share_non_production_result_shape(tmp_path: Path) -> None:
    root = _workspace_with_working_chapter(tmp_path)

    code, stdout, stderr = _run_cli(
        ["preview", "package", "--path", str(root), "--chapters", "1"]
    )
    assert code == 0
    assert stderr == ""
    assert "not production eligible" in stdout

    status, payload = handle_api_request(
        "POST",
        "/api/preview/package",
        "",
        json.dumps({"path": str(root), "chapters": "1", "source": "polished"}),
    )
    assert status == 200
    data = payload["data"]  # type: ignore[index]
    assert data["production_eligible"] is False
    assert data["chapters"] == [1]
    assert Path(data["package_dir"]).parent == root / "exports" / "previews"


def _workspace_with_working_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.joinpath("polished.md").write_text(
        "---\n"
        "chapter_number: 1\n"
        "title: 雨夜旧车站\n"
        "status: polished\n"
        "created_by: writer_agent\n"
        "based_on: draft.md\n"
        "created_at: 2026-07-11T00:00:00Z\n"
        "---\n\n"
        "# 第一章 雨夜旧车站\n\n"
        "working candidate\n",
        encoding="utf-8",
    )
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
