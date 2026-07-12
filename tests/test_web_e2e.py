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
            page.select_option("#providerProfileSelect", "default")
            page.fill("#providerMaxTokensField", "30001")
            page.fill("#providerMaxContextTokensField", "150001")
            page.click("#saveProviderConfig")
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('Profile 模型配置已保存')")

            page.select_option("#providerProfileSelect", "scribe")
            assert page.locator("#providerInheritDefaultField").is_checked()
            assert page.locator("#providerMaxTokensField").input_value() == "30001"
            assert page.locator("#providerMaxContextTokensField").input_value() == "150001"
            page.select_option("#providerProfileSelect", "architect")
            assert page.locator("#providerInheritDefaultField").is_checked()
            assert page.locator("#providerMaxTokensField").input_value() == "30001"
            assert page.locator("#providerMaxContextTokensField").input_value() == "150001"

            page.locator("summary", has_text="任务级覆盖").click()
            page.select_option("#providerTaskSelect", "revision")
            page.select_option("#providerTaskThinkingTypeField", "disabled")
            page.fill("#providerTaskReasoningField", "medium")
            page.fill("#providerTaskTemperatureField", "0.4")
            page.click("#saveProviderTaskConfig")
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('Task 覆盖配置已保存')")
            assert "temperature" in page.locator("#providerTaskEffectivePanel").inner_text()
            assert "0.4" in page.locator("#providerTaskEffectivePanel").inner_text()

            page.select_option("#providerProfileSelect", "scribe")
            assert page.locator("#providerThinkingTypeField").count() == 0
            assert page.locator("#providerTemperatureField").count() == 0
            page.uncheck("#providerInheritDefaultField")
            page.select_option("#providerProviderField", "deepseek")
            page.select_option("#providerProviderField", "mock")
            page.fill("#providerModelField", "web-e2e-writer")
            page.fill("#providerMaxTokensField", "12345")
            page.click("#saveProviderConfig")
            page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('Profile 模型配置已保存')")

            page.click("button[data-page='workbenchPage']")
            page.select_option("#provider", "mock")
            page.fill("#instruction", "写第1章，突出雨夜旧车站")
            page.click("#sessionStart")
            page.wait_for_function(
                "() => document.querySelector('#sessionPanel')?.textContent?.includes('awaiting_outline_approval')"
            )
            page.wait_for_function(
                "() => document.querySelector('#outlinePreview')?.textContent?.includes('旧车站')"
            )
            assert "旧车站" in page.locator("#outlinePreview").inner_text()

            page.click("button[data-tab='chapterEditor']")
            page.select_option("#editorTarget", "polished")
            page.fill("#editorSource", "polished.md")
            page.click("#loadEditorFile")
            page.wait_for_function("() => document.querySelector('#chapterEditorText')?.value?.includes('原始正文')")
            page.fill("#chapterEditorText", page.locator("#chapterEditorText").input_value() + "\n新增一行。")
            page.click("#saveEditorVersion")
            page.wait_for_function("() => document.querySelector('#editorSavedPath')?.textContent?.includes('/candidates/candidate_art_')")

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


def test_web_ui_restores_generated_session_without_local_storage(tmp_path: Path) -> None:
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
            page.wait_for_function("() => document.querySelector('#statusPanel')?.textContent?.includes('雨夜旧车站')")
            page.click("button[data-page='workbenchPage']")
            page.select_option("#provider", "mock")
            page.fill("#instruction", "写第1章，突出雨夜旧车站")
            page.click("#sessionStart")
            page.wait_for_function("() => document.querySelector('#sessionPanel')?.textContent?.includes('awaiting_outline_approval')")
            session_id = page.locator("#sessionId").input_value()
            page.click("#sessionApprove")
            page.wait_for_function("() => document.querySelector('#sessionPanel')?.textContent?.includes('ready_to_run')")
            page.click("#sessionRun")
            page.wait_for_function("() => document.querySelector('#sessionPanel')?.textContent?.includes('awaiting_content_review')")
            page.wait_for_function("() => document.querySelector('#chapterProseViewer')?.textContent?.includes('真正沉默')")
            page.close()

            restored_context = browser.new_context()
            restored_page = restored_context.new_page()
            restored_page.goto(f"http://127.0.0.1:{port}/")
            restored_page.fill("#projectPath", str(root))
            restored_page.click("#openProject")
            restored_page.click("button[data-page='workbenchPage']")
            restored_page.wait_for_function(
                f"() => document.querySelector('#sessionId')?.value === {json.dumps(session_id)}"
            )
            restored_page.wait_for_function(
                "() => document.querySelector('#chapterProseViewer')?.textContent?.includes('真正沉默')"
            )
            assert restored_page.locator("#sessionRun").is_disabled()
            assert restored_page.locator("#sessionReviseInstruction").is_enabled()
            restored_context.close()
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Playwright browser binaries are not installed")
        raise
    finally:
        server.shutdown()
        server.server_close()


def test_workbench_instruction_bar_stays_visible_while_scrolling(tmp_path: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
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
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("button[data-page='workbenchPage']")
            long_instruction = (
                "请写第1章，开场要有雨夜、旧车站、停播广播和主角的迟疑。"
                "语言要克制，节奏要慢，隐藏真相不要提前揭示，只留下可回收的伏笔。"
                "如果需要修改大纲，请优先保持人物动机清晰，并避免解释性独白。"
                "主角进入候车厅时要先观察环境，再被一个细节触发记忆，但不要直接说明记忆是真是假。"
                "对白要短，环境描写要承担压迫感，结尾留下下一章追查广播来源的动力。"
            )
            assert len(long_instruction) > 150
            page.fill("#instruction", long_instruction)
            assert page.locator("#instruction").input_value() == long_instruction
            assert page.locator("#instruction").evaluate("node => node.clientHeight") >= 200

            page.eval_on_selector("#sessionWorkflowPanel", "node => node.scrollIntoView({block: 'start'})")
            _wait_for_instruction_and_target_visible(page, "#sessionWorkflowPanel")

            page.click("#settingChangeDetails > summary")
            page.eval_on_selector("#settingChangeDetails", "node => node.scrollIntoView({block: 'center'})")
            _wait_for_instruction_and_target_visible(page, "#settingChangeDetails")
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Playwright browser binaries are not installed")
        raise
    finally:
        server.shutdown()
        server.server_close()


def test_workbench_session_status_scrolls_with_content(tmp_path: Path) -> None:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
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
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/")
            page.click("button[data-page='workbenchPage']")

            assert page.locator(".workbench-session-status").evaluate(
                "node => window.getComputedStyle(node).position"
            ) == "static"
            page.eval_on_selector("#rejectedTextViewer", "node => node.scrollIntoView({block: 'center'})")
            page.wait_for_function(
                """() => {
                    const commandBar = document.querySelector("#workbenchCommandBar")?.getBoundingClientRect();
                    const statusNode = document.querySelector(".workbench-session-status");
                    const status = statusNode?.getBoundingClientRect();
                    const rejectedText = document.querySelector("#rejectedTextViewer")?.getBoundingClientRect();
                    if (!commandBar || !statusNode || !status || !rejectedText) return false;
                    const statusPosition = window.getComputedStyle(statusNode).position;
                    const rejectedTextVisible = rejectedText.top < window.innerHeight && rejectedText.bottom > commandBar.bottom + 8;
                    const statusScrolledWithPage = status.top < commandBar.bottom;
                    const statusAboveTarget = status.bottom < rejectedText.top;
                    return statusPosition === "static" && rejectedTextVisible && statusScrolledWithPage && statusAboveTarget;
                }"""
            )
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


def _wait_for_instruction_and_target_visible(page, target_selector: str) -> None:
    page.wait_for_function(
        """(selector) => {
            const bar = document.querySelector("#workbenchCommandBar")?.getBoundingClientRect();
            const input = document.querySelector("#instruction")?.getBoundingClientRect();
            const target = document.querySelector(selector)?.getBoundingClientRect();
            if (!bar || !input || !target) return false;
            const inputVisible = input.top >= 0 && input.bottom <= window.innerHeight && input.height >= 200;
            const targetVisible = target.top < window.innerHeight && target.bottom > bar.bottom + 8;
            return inputVisible && targetVisible;
        }""",
        arg=target_selector,
    )
