#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from urllib import request

from novel.core.timeutil import utc_timestamp


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a minimal Playwright smoke test against the local Web UI.")
    parser.add_argument("--project", default=None, help="Workspace path. Defaults to a temporary workspace.")
    parser.add_argument("--screenshots", default=None, help="Screenshot output directory.")
    parser.add_argument("--port", type=int, default=0, help="Port to use. Defaults to an available port.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned steps without starting a browser.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    if args.project:
        project = Path(args.project).expanduser().resolve()
        cleanup_project = False
    else:
        smoke_title = f"Web UI Smoke {utc_timestamp()}"
        project = Path(tempfile.mkdtemp(prefix="writeryang-webui-")) / smoke_title
        cleanup_project = True
    screenshots = Path(args.screenshots).expanduser().resolve() if args.screenshots else project.parent / "screenshots"
    port = args.port or (8765 if args.dry_run else _free_port())
    url = f"http://127.0.0.1:{port}"
    planned = [
        "start web server",
        "open Web UI",
        "initialize project",
        "generate inspiration",
        "canon suggest/apply",
        "session start/approve/run",
        "capture screenshot",
    ]
    if args.dry_run:
        _print({"ok": True, "dry_run": True, "project": str(project), "url": url, "steps": planned}, args.json)
        return 0

    screenshots.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "novel", "web", "--project", str(project), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(url)
        _run_playwright(url, project, screenshots)
    except Exception as exc:
        payload = {
            "ok": False,
            "project": str(project),
            "url": url,
            "screenshots": str(screenshots),
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
        _print(payload, args.json)
        return 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
    if cleanup_project:
        shutil.rmtree(project, ignore_errors=True)
    _print({"ok": True, "project": str(project), "url": url, "screenshots": str(screenshots)}, args.json)
    return 0


def _run_playwright(url: str, project: Path, screenshots: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is not installed") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url)
        page.fill("#projectParentPath", str(project.parent))
        page.fill("#projectTitle", project.name)
        page.fill("#projectGenre", "悬疑")
        page.click("#initProject")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('项目已初始化')")
        page.click("[data-page='workbenchPage']")
        page.fill("#instruction", "雨夜旧车站传来停播多年的广播声。")
        page.select_option("#provider", "mock")
        page.click("#inspireProject")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('生成灵感完成')")
        page.wait_for_function("() => document.querySelector('#inspirationPreview')?.textContent?.includes('## Weak Outline')")
        page.click("#canonSuggest")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('Canon 建议完成')")
        page.click("#canonApply")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('应用 Canon proposal完成')")
        page.fill("#sessionChapters", "1")
        page.fill("#instruction", "写第1章，建立悬疑感，不揭示真相。")
        page.click("#sessionStart")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('创建 Session 大纲完成')")
        page.click("#sessionApprove")
        page.wait_for_function("() => document.querySelector('#message')?.textContent?.includes('批准 Session 大纲完成')")
        page.screenshot(path=str(screenshots / "webui_smoke.png"), full_page=True)
        browser.close()


def _wait_for_server(url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise TimeoutError(f"server did not become ready: {url}")


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Web UI smoke {'passed' if payload.get('ok') else 'failed'}: {payload.get('url')}")
if __name__ == "__main__":
    raise SystemExit(main())
