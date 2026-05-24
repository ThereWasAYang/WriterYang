from __future__ import annotations

import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.workspace import InitOptions, init_workspace
from novel.web_server import _handler_class


pytestmark = pytest.mark.web_e2e


def test_web_ui_can_load_workspace_and_trigger_mock_workflow(tmp_path: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    root = _workspace_ready_for_generation(tmp_path)
    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("local port binding is not permitted in this sandbox")
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/")
            page.fill("#projectPath", str(root))
            page.click("#openProject")
            page.wait_for_selector("#fileTree .file-row")
            assert "雨夜旧车站" in page.locator("#statusPanel").inner_text()
            assert "project.yaml" in page.locator("#fileTree").inner_text()

            page.click("button[data-tab='providerConfig']")
            page.wait_for_function(
                "() => document.querySelector('#providerConfigPanel')?.textContent?.includes('api_key_env')"
            )
            assert "api_key_env" in page.locator("#providerConfigPanel").inner_text()

            page.click("button[data-tab='runLogs']")
            page.click("#planChapter")
            page.wait_for_function(
                "() => document.querySelector('#chapterList')?.textContent?.includes('plan')"
            )
            page.click("#refreshProject")
            page.click("button[data-tab='chapterCompare']")
            page.click("#loadCompare")
            page.wait_for_function(
                "() => !document.querySelector('#planViewer')?.textContent?.includes('未加载')"
            )
            assert "旧车站" in page.locator("#planViewer").inner_text()
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Playwright browser binaries are not installed")
        raise
    finally:
        server.shutdown()
        server.server_close()


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
