#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Sequence
from urllib import request


SCREENSHOTS = (
    "overview.png",
    "project_setup.png",
    "left_session_controls.png",
    "chapter_compare.png",
    "chapter_editor.png",
    "audit_locate.png",
    "run_logs.png",
    "provider_config.png",
    "state_timeline.png",
    "rewrite_events.png",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture stable screenshots for the Web UI user guide.")
    parser.add_argument(
        "--output",
        default="docs/assets/web-ui-guide",
        help="Screenshot output directory. Defaults to docs/assets/web-ui-guide.",
    )
    parser.add_argument("--project", default=None, help="Temporary guide project path. Defaults to a temp directory.")
    parser.add_argument("--port", type=int, default=0, help="Web UI port. Defaults to an available port.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned screenshots without starting Web UI.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    output = Path(args.output).expanduser().resolve()
    project = (
        Path(args.project).expanduser().resolve()
        if args.project
        else Path(tempfile.mkdtemp(prefix="writeryang-webui-guide-")) / "guide_project"
    )
    port = args.port or (8765 if args.dry_run else _free_port())
    url = f"http://127.0.0.1:{port}"
    planned = {
        "ok": True,
        "dry_run": args.dry_run,
        "project": str(project),
        "output": str(output),
        "url": url,
        "screenshots": list(SCREENSHOTS),
    }
    if args.dry_run:
        _print(planned, args.json)
        return 0

    output.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "novel", "web", "--host", "127.0.0.1", "--port", str(port)],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(url)
        _run_playwright(url, project, output)
    except Exception as exc:
        server_output = _collect_server_output(server)
        payload = {
            "ok": False,
            "project": str(project),
            "output": str(output),
            "url": url,
            "error_type": exc.__class__.__name__,
            "error": _playwright_help(str(exc)),
            "server_stdout": server_output.get("stdout", ""),
            "server_stderr": server_output.get("stderr", ""),
        }
        _print(payload, args.json)
        return 1
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
    _print(planned | {"dry_run": False}, args.json)
    return 0


def _run_playwright(url: str, project: Path, output: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is not installed. Run: python -m pip install playwright") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.goto(url)
        page.wait_for_selector("#projectPath")
        page.fill("#projectPath", str(project))
        page.fill("#projectTitle", "图文指南示例")
        page.fill("#projectGenre", "武侠, 悬疑")
        page.screenshot(path=str(output / "overview.png"), full_page=True)

        page.click("#initProject")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('初始化项目')")
        page.locator("#setupGuidePanel").screenshot(path=str(output / "project_setup.png"))

        page.click('[data-page="workbenchPage"]')
        page.select_option("#provider", "mock")
        page.fill("#instruction", "雨夜山城里，旧钟声让年轻刀客想起一桩未解旧案。")
        _click_and_wait_message(page, "#inspireProject", "生成灵感")
        _click_and_wait_message(page, "#canonSuggest", "Canon 建议")
        _click_and_wait_message(page, "#canonApply", "应用 Canon proposal")
        _click_and_wait_message(page, "#sessionStart", "创建 Session 大纲")
        _click_and_wait_message(page, "#sessionApprove", "批准 Session 大纲")

        _seed_guide_files(project)
        page.evaluate("refreshAll({ silent: false })")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('项目已刷新')")
        page.fill("#sessionId", GUIDE_SESSION_ID)
        page.evaluate(
            """
            async () => {
              const data = await apiGet('/api/session', { path: projectPath(), session_id: $('sessionId').value });
              renderSessionSummary(data);
              renderNextStep(data);
            }
            """
        )

        page.locator("#workbenchPage .workspace-grid > .stack").first.screenshot(
            path=str(output / "left_session_controls.png")
        )
        page.locator("#rewriteEventsPanel").screenshot(path=str(output / "rewrite_events.png"))

        page.click("#loadCompare")
        page.wait_for_function("() => document.querySelector('#planViewer')?.textContent?.includes('雨夜山城')")
        page.evaluate("$('fileViewer').textContent = '选择章节文件后查看。'")
        page.locator("#chapterCompare").screenshot(path=str(output / "chapter_compare.png"))

        _show_tab(page, "chapterEditor")
        page.wait_for_function("() => document.querySelector('#chapterEditorText')?.value?.includes('旧钟在雨里敲了三下')")
        page.locator("#chapterEditor").screenshot(path=str(output / "chapter_editor.png"))

        _show_tab(page, "auditLocate")
        page.wait_for_function("() => document.querySelector('#auditIssueList')?.textContent?.includes('guide_issue_clock')")
        page.locator("#auditLocate").screenshot(path=str(output / "audit_locate.png"))

        page.click('[data-page="logsPage"]')
        _show_tab(page, "runLogs")
        page.wait_for_function("() => document.querySelector('#runLogPanel')?.textContent?.includes('guide_run')")
        page.locator("#runLogs").screenshot(path=str(output / "run_logs.png"))

        page.click('[data-page="configPage"]')
        page.wait_for_function("() => document.querySelector('#providerConfigPanel')?.textContent?.includes('agents')")
        page.locator("#configPage").screenshot(path=str(output / "provider_config.png"))

        page.click('[data-page="memoryPage"]')
        page.wait_for_function("() => document.querySelector('#stateTimelinePanel')?.textContent?.includes('旧钟楼')")
        page.locator("#memoryPage").screenshot(path=str(output / "state_timeline.png"))
        browser.close()


GUIDE_SESSION_ID = "session_20260531_120000_000001"


def _seed_guide_files(project: Path) -> None:
    now = "2026-05-31T12:00:00Z"
    (project / ".env").write_text("DASHSCOPE_API_KEY=placeholder\n", encoding="utf-8")
    chapter_dir = project / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        chapter_dir / "plan.json",
        {
            "schema_version": 2,
            "chapter_number": 1,
            "title": "雨夜山城",
            "goal": "让主角进入山城，并建立旧案疑云。",
            "summary": "主角在雨夜听见旧钟声，发现旧案仍在影响山城。",
            "required_context": {
                "canon_entity_ids": ["lin_shen", "old_clock_tower", "broken_bell_token"],
                "state_entity_ids": ["lin_shen", "broken_bell_token"],
                "timeline_event_ids": ["event_old_bell"],
            },
            "scenes": [
                {
                    "scene_number": 1,
                    "location_id": "old_clock_tower",
                    "participant_ids": ["lin_shen"],
                    "purpose": "建立悬疑入口。",
                    "summary": "主角抵达旧钟楼，听见不该响起的钟声。",
                    "emotional_beat": "克制、警觉。",
                    "plot_points": ["旧钟在雨里敲了三下", "主角发现破铃牌"],
                }
            ],
            "must_include": ["旧钟声", "破铃牌", "山城雨夜"],
            "must_avoid": ["直接揭示旧案真相"],
            "expected_state_changes": ["主角获得破铃牌线索"],
            "ending_hook": "钟声停止后，楼下出现一串新鲜脚印。",
        },
    )
    (chapter_dir / "draft.md").write_text(_chapter_markdown("draft", now), encoding="utf-8")
    (chapter_dir / "polished.md").write_text(_chapter_markdown("polished", now), encoding="utf-8")
    (chapter_dir / "polished.v2.md").write_text(
        _chapter_markdown("polished", now).replace("旧钟在雨里敲了三下", "旧钟在雨里低低响了三下"),
        encoding="utf-8",
    )
    _write_json(
        chapter_dir / "audit.json",
        {
            "schema_version": 2,
            "chapter_number": 1,
            "audited_file": "polished.md",
            "overall_status": "needs_revision",
            "summary": "示例审核：一个中等级别问题用于展示定位和修订入口。",
            "issues": [
                {
                    "id": "guide_issue_clock",
                    "severity": "medium",
                    "type": "continuity_issue",
                    "description": "旧钟声出现后，正文没有交代主角为何判断它与旧案有关。",
                    "evidence": [{"source": "polished.md", "quote": "旧钟在雨里敲了三下"}],
                    "suggested_fix": "补一句主角认出旧钟只在掌门死夜响过。",
                }
            ],
            "passed_checks": ["front matter readable", "plan file exists"],
            "created_at": now,
        },
    )
    _seed_canon_state_timeline(project)
    _seed_session(project, now)
    _seed_runs(project, now)


def _chapter_markdown(status: str, now: str) -> str:
    return f"""---
chapter_number: 1
title: 雨夜山城
status: {status}
created_by: guide_fixture
based_on: plan.json
created_at: {now}
---

# 第一章 雨夜山城

雨落在山城的石阶上。林深停在旧钟楼前，抬头看见檐角的灯影一明一灭。

旧钟在雨里敲了三下。他知道这不该发生，因为那口钟已经二十年无人敲响。

他在门槛下拾起一枚破铃牌。铜边很冷，像刚从某个人的掌心里放下。
"""


def _seed_canon_state_timeline(project: Path) -> None:
    canon = project / "memory" / "canon"
    state_dir = project / "memory" / "state"
    canon.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        canon / "characters.json",
        {
            "schema_version": 2,
            "characters": [
                {
                    "id": "lin_shen",
                    "name": "林深",
                    "role": "主角",
                    "reader_visible_summary": "沉默的年轻刀客，来到山城调查旧案。",
                }
            ],
        },
    )
    _write_json(
        canon / "locations.json",
        {
            "schema_version": 2,
            "locations": [
                {
                    "id": "old_clock_tower",
                    "name": "旧钟楼",
                    "type": "建筑",
                    "reader_visible_summary": "山城废弃钟楼，二十年来无人敲响。",
                }
            ],
        },
    )
    _write_json(
        canon / "items.json",
        {
            "schema_version": 2,
            "items": [
                {
                    "id": "broken_bell_token",
                    "name": "破铃牌",
                    "type": "线索",
                    "reader_visible_summary": "一枚裂开的铜牌，边缘有旧钟纹。",
                }
            ],
        },
    )
    _write_json(
        state_dir / "current_state.json",
        {
            "schema_version": 2,
            "story_position": {"latest_chapter": 1, "in_story_time": "雨夜", "summary": "主角刚进入山城。"},
            "character_states": [
                {
                    "entity_id": "lin_shen",
                    "location_id": "old_clock_tower",
                    "health": "轻微疲惫",
                    "mental_state": "警觉",
                    "knowledge": ["旧钟声异常"],
                    "goals": ["查明旧案"],
                    "possessions": ["broken_bell_token"],
                    "last_updated_chapter": 1,
                }
            ],
            "item_states": [
                {
                    "entity_id": "broken_bell_token",
                    "holder_id": "lin_shen",
                    "location_id": None,
                    "condition": "裂开",
                    "known_properties": ["铜边寒冷"],
                    "last_updated_chapter": 1,
                }
            ],
            "location_states": [
                {
                    "entity_id": "old_clock_tower",
                    "accessibility": "可进入",
                    "condition": "废弃漏雨",
                    "active_events": ["event_old_bell"],
                    "last_updated_chapter": 1,
                }
            ],
        },
    )
    _write_json(
        state_dir / "timeline.json",
        {
            "schema_version": 2,
            "events": [
                {
                    "id": "event_old_bell",
                    "chapter": 1,
                    "scene": 1,
                    "in_story_time": "雨夜",
                    "summary": "旧钟楼在无人敲击时响了三下。",
                    "reader_visible": True,
                    "narrative_position": {"chapter": 1, "scene": 1, "sequence": 1},
                    "story_position": {"time_label": "雨夜", "order": 1, "thread_id": "main", "certainty": "certain"},
                    "event_role": "current_action",
                    "location_id": "old_clock_tower",
                    "participant_ids": ["lin_shen"],
                    "state_change_ids": ["broken_bell_token"],
                }
            ],
        },
    )


def _seed_session(project: Path, now: str) -> None:
    session_dir = project / "memory" / "sessions" / GUIDE_SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        session_dir / "session.json",
        {
            "schema_version": 2,
            "session_id": GUIDE_SESSION_ID,
            "scope_type": "chapters",
            "chapter_range": [1],
            "user_intent": "写第1章，建立山城旧案悬疑。",
            "status": "needs_revision",
            "outline_status": "approved",
            "content_status": "needs_revision",
            "approved_outline_path": "memory/sessions/session_20260531_120000_000001/approved_outline.md",
            "final_output_paths": ["memory/chapters/001/polished.md"],
            "audit_history": ["memory/chapters/001/audit.json"],
            "revision_history": [],
            "archive_paths": [],
            "created_at": now,
            "updated_at": now,
            "max_auto_revision_rounds": 3,
        },
    )
    rejection = session_dir / "rejections" / "chapter_001_round_1_before.md"
    rejection.parent.mkdir(parents=True, exist_ok=True)
    rejection.write_text(_chapter_markdown("polished", now), encoding="utf-8")
    _write_json(
        session_dir / "rewrite_events.json",
        {
            "schema_version": 2,
            "events": [
                {
                    "event_id": "rewrite_guide_round_1",
                    "session_id": GUIDE_SESSION_ID,
                    "chapter_number": 1,
                    "round_number": 1,
                    "action": "revision_rewrite",
                    "status": "unresolved",
                    "trigger_audit_path": "memory/chapters/001/audit.json",
                    "blocking_issues": [
                        {
                            "id": "guide_issue_clock",
                            "severity": "medium",
                            "type": "continuity_issue",
                            "description": "旧钟声和旧案关联交代不足。",
                            "evidence": [{"source": "polished.md", "quote": "旧钟在雨里敲了三下"}],
                            "suggested_fix": "补充主角识别旧钟声的依据。",
                        }
                    ],
                    "rejected_text_snapshot_path": "memory/sessions/session_20260531_120000_000001/rejections/chapter_001_round_1_before.md",
                    "before_output_path": "memory/chapters/001/polished.md",
                    "after_output_path": None,
                    "can_undo": True,
                    "undo_status": "not_requested",
                    "audit_revision_history": [],
                    "created_at": now,
                    "updated_at": now,
                }
            ],
        },
    )


def _seed_runs(project: Path, now: str) -> None:
    runs = project / "runs"
    model_io = runs / "model_io"
    model_io.mkdir(parents=True, exist_ok=True)
    _write_json(
        runs / "run_guide.json",
        {
            "run_id": "guide_run",
            "task": "session_run",
            "chapter_number": 1,
            "status": "failed",
            "started_at": now,
            "ended_at": now,
            "errors": ["示例：Audit 发现中等级别连续性问题。"],
        },
    )
    (runs / "provider_calls.jsonl").write_text(
        json.dumps(
            {
                "request_id": "guide_request_1",
                "agent_name": "writer",
                "provider": "mock",
                "model": "mock-model",
                "status": "success",
                "started_at": now,
                "model_io_path": "runs/model_io/guide_request_1.json",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (model_io / "index.jsonl").write_text(
        json.dumps(
            {
                "request_id": "guide_request_1",
                "agent_name": "writer",
                "provider": "mock",
                "model": "mock-model",
                "status": "success",
                "started_at": now,
                "model_io_path": "runs/model_io/guide_request_1.json",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _click_and_wait_message(page, selector: str, phrase: str) -> None:
    page.click(selector)
    page.wait_for_function(
        f"() => document.querySelector('#message')?.textContent?.includes({json.dumps(phrase, ensure_ascii=False)})"
    )


def _show_tab(page, tab_id: str) -> None:
    page.click(f'[data-tab="{tab_id}"]')


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wait_for_server(url: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"server did not become ready: {url}")


def _collect_server_output(server: subprocess.Popen[str]) -> dict[str, str]:
    if server.poll() is None:
        server.terminate()
    try:
        stdout, stderr = server.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        stdout, stderr = server.communicate()
    return {"stdout": stdout or "", "stderr": stderr or ""}


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _playwright_help(message: str) -> str:
    if "Executable doesn't exist" in message or "playwright install" in message:
        return message + "\nInstall browser binaries with: python -m playwright install chromium"
    return message


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "passed" if payload.get("ok") else "failed"
        print(f"Web UI guide screenshot capture {status}: {payload.get('output')}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
