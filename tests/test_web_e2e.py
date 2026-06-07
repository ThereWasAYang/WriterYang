from __future__ import annotations

import json
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
    _write_chapter_and_audit_fixture(root)
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
            page.wait_for_function("() => document.querySelector('#statusPanel')?.textContent?.includes('雨夜旧车站')")
            assert "雨夜旧车站" in page.locator("#statusPanel").inner_text()
            page.click("button[data-page='logsPage']")
            page.click("button[data-tab='projectFiles']")
            page.wait_for_selector("#fileTree .file-row")
            assert "project.yaml" in page.locator("#fileTree").inner_text()

            page.click("button[data-page='configPage']")
            page.wait_for_function(
                "() => document.querySelector('#providerConfigPanel')?.textContent?.includes('api_key_env')"
            )
            assert "api_key_env" in (page.locator("#providerConfigPanel").text_content() or "")
            page.select_option("#providerAgentSelect", "writer")
            page.select_option("#providerProviderField", "mock")
            page.fill("#providerModelField", "web-e2e-writer")
            page.select_option("#providerThinkingTypeField", "disabled")
            page.click("#saveProviderConfig")
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('Agent 模型配置已保存')")

            page.click("button[data-page='workbenchPage']")
            page.select_option("#provider", "mock")
            page.click("#workbenchPage details summary")
            page.click("#planChapter")
            page.wait_for_function(
                "() => document.querySelector('#chapterList')?.textContent?.includes('plan')"
            )
            page.click("button[data-tab='chapterCompare']")
            page.click("#loadCompare")
            page.wait_for_function(
                "() => !document.querySelector('#planViewer')?.textContent?.includes('未加载')"
            )
            assert "旧车站" in page.locator("#planViewer").inner_text()

            page.click("button[data-tab='chapterEditor']")
            page.select_option("#editorTarget", "polished")
            page.fill("#editorSource", "polished.md")
            page.click("#loadEditorFile")
            page.wait_for_function("() => document.querySelector('#chapterEditorText')?.value?.includes('原始正文')")
            page.fill("#chapterEditorText", page.locator("#chapterEditorText").input_value() + "\n新增一行。")
            page.click("#saveEditorVersion")
            page.wait_for_function("() => document.querySelector('#editorSavedPath')?.textContent?.includes('polished.v2.md')")

            page.click("button[data-tab='auditLocate']")
            page.click("#loadAuditAnnotations")
            page.wait_for_function("() => document.querySelector('#auditIssueList')?.textContent?.includes('audit_issue_001')")
            page.click(".issue-button")
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('已定位')")

            page.click("button[data-page='memoryPage']")
            page.wait_for_function("() => document.querySelector('#stateTimelinePanel')?.textContent?.includes('Timeline by chapter')")
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Playwright browser binaries are not installed")
        raise
    finally:
        server.shutdown()
        server.server_close()


def test_web_ui_initializes_project_under_custom_parent_directory(tmp_path: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    parent = tmp_path / "custom-parent"
    root = parent / "雨夜旧车站"
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
            page.fill("#projectParentPath", str(parent))
            page.fill("#projectTitle", "雨夜旧车站")
            page.fill("#projectGenre", "悬疑")
            page.wait_for_function(
                f"() => document.querySelector('#projectInitPathPreview')?.textContent?.includes({json.dumps(str(root))})"
            )
            page.click("#initProject")
            page.wait_for_function(
                f"() => document.querySelector('#projectPath')?.value === {json.dumps(str(root))}"
            )
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('项目已初始化')")
            assert (root / "project.yaml").is_file()
            assert (root / "config" / "agents.yaml").is_file()
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


def _write_chapter_and_audit_fixture(root: Path) -> None:
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / "polished.md").write_text(
        "---\nchapter_number: 1\ntitle: 雨夜旧车站\nstatus: polished\n---\n\n原始正文里有定位短语。\n",
        encoding="utf-8",
    )
    (chapter_dir / "audit.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "audited_file": "polished.md",
                "overall_status": "needs_revision",
                "summary": "fixture issue",
                "issues": [
                    {
                        "id": "audit_issue_001",
                        "severity": "medium",
                        "type": "continuity_issue",
                        "description": "定位测试。",
                        "evidence": [{"source": "polished.md", "quote": "定位短语"}],
                        "suggested_fix": "修正相关正文。",
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
