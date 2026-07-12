from __future__ import annotations

from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from novel.cli import main
from novel.cli_shared import _dispatch_cli_command
from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.command_bus import (
    COMMAND_HANDLERS,
    DomainError,
    dispatch_command,
    new_command_envelope,
)
from novel.core.contracts import (
    ProductionExportCommand,
    SessionCommand,
    SessionStartCommand,
    Surface,
)
from novel.core.workspace import InitOptions, init_workspace
from novel.core.session import SessionStartOptions, start_session
from novel.web_api import handle_api_request
from novel.web_api.common import _dispatch_web_query_command
from novel.web_api.router import _post_routes


def test_command_contracts_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SessionStartCommand.model_validate(
            {
                "user_intent": "写第一章",
                "chapter_range": [1],
                "unexpected": True,
            }
        )


def test_every_public_command_type_has_exactly_one_handler() -> None:
    command_types = {
        "session.start",
        "session.show",
        "session.revise_outline",
        "session.approve_outline",
        "session.run",
        "session.revise_content",
        "session.revise_audit",
        "session.retry_rewrite",
        "session.undo_rewrite",
        "session.accept",
        "session.archive",
        "session.cancel",
        "revision.blocks",
        "revision.start",
        "revision.show",
        "revision.run",
        "revision.accept",
        "revision.cancel",
        "export.markdown",
        "export.docx",
        "preview.package",
        "memory_repair.suggest",
        "memory_repair.apply",
        "setting_change.suggest",
        "setting_change.answer",
        "setting_change.apply",
        "project.status",
        "project.init",
        "project.validate",
        "project.show",
        "search",
        "schema.export",
        "inspiration.generate",
        "canon.suggest",
        "canon.apply",
        "chapter_memory.generate",
        "chapter_memory.rebuild",
        "index.rebuild",
        "index.refresh",
        "style_guide.save",
        "style_guide.generate",
        "chapter_candidate.save",
        "agent_config.update",
        "setup.default_provider",
        "setup.embedding_provider",
        "setup.project_web_port",
        "setup.web_launcher",
    }
    assert set(COMMAND_HANDLERS) == command_types


def test_confirmation_required_command_is_rejected_before_handler(tmp_path: Path) -> None:
    root = _workspace_with_chapter(tmp_path)
    envelope = new_command_envelope(
        surface=Surface.CLI,
        project_root=root,
        command=ProductionExportCommand(type="export.markdown", chapters=[1]),
    )

    with pytest.raises(DomainError) as caught:
        dispatch_command(envelope)

    assert caught.value.code == "confirmation_required"
    assert caught.value.recoverable is True


def test_cli_and_web_adapters_return_same_domain_result(tmp_path: Path) -> None:
    root = _workspace_with_chapter(tmp_path)
    session = start_session(
        SessionStartOptions(
            root=root,
            user_intent="写第一章",
            chapter_range=(1,),
            provider_name="mock",
        )
    ).session
    command = SessionCommand(type="session.show", session_id=session.session_id)

    cli_payload = _dispatch_cli_command(Namespace(), root, command)
    web_payload = _dispatch_web_query_command({"path": str(root)}, command)

    for payload in (cli_payload, web_payload):
        payload.pop("command_id")
        payload.pop("request_id")
        payload.pop("workflow_run_id")
    assert cli_payload == web_payload


def test_session_domain_errors_are_structured_without_traceback(tmp_path: Path) -> None:
    root = _workspace_with_chapter(tmp_path)
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            [
                "session",
                "show",
                "session_missing",
                "--path",
                str(root),
                "--json",
            ]
        )

    assert code == 1
    cli_payload = json.loads(stdout.getvalue())
    assert cli_payload["ok"] is False
    assert cli_payload["error"]["type"] == "session_error"
    assert "Traceback" not in stdout.getvalue() + stderr.getvalue()

    status, payload = handle_api_request(
        "POST",
        "/api/session/approve-outline",
        "",
        json.dumps({"path": str(root), "session_id": "session_missing"}),
    )
    assert status == 400
    assert payload["error"]["code"] == "session_error"  # type: ignore[index]
    assert "Traceback" not in json.dumps(payload)


@pytest.mark.parametrize(
    "path",
    [
        "/api/plan-chapter",
        "/api/write-chapter",
        "/api/polish-chapter",
        "/api/audit-chapter",
        "/api/generate-chapter",
    ],
)
def test_low_level_web_generation_routes_are_not_public(tmp_path: Path, path: str) -> None:
    root = _workspace_with_chapter(tmp_path)
    status, payload = handle_api_request(
        "POST",
        path,
        "",
        json.dumps({"path": str(root), "chapter_number": 1}),
    )
    assert status == 404
    assert payload["error"]["code"] == "not_found"  # type: ignore[index]


def test_web_mutation_routes_delegate_locking_to_command_bus() -> None:
    routes = _post_routes()
    command_backed_paths = {
        "/api/export/markdown",
        "/api/export/docx",
        "/api/preview/package",
        "/api/save-chapter-file",
        "/api/style-guide",
        "/api/style-guide/generate",
        "/api/provider-config",
        "/api/index/refresh",
        "/api/setup/default-provider",
        "/api/setup/embedding",
        "/api/setup/web-port",
        "/api/inspire",
        "/api/canon/suggest",
        "/api/canon/apply",
        "/api/settings/change/suggest",
        "/api/settings/change/answer",
        "/api/settings/change/apply",
        "/api/chapter-memory/generate",
        "/api/chapter-memory/rebuild",
        "/api/session/start",
        "/api/session/revise-outline",
        "/api/session/approve-outline",
        "/api/session/run",
        "/api/session/cancel",
        "/api/session/revise-content",
        "/api/session/revise-audit",
        "/api/session/retry-rewrite",
        "/api/session/undo-rewrite",
        "/api/session/accept",
        "/api/session/archive",
        "/api/revision-session/start",
        "/api/revision-session/run",
        "/api/revision-session/accept",
    }

    assert command_backed_paths <= routes.keys()
    for path in command_backed_paths:
        assert routes[path][2] is False, path


def _workspace_with_chapter(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    proposal = root / "memory" / "canon" / "proposal.json"
    proposal.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    apply_canon_proposal(root, proposal)
    chapter_dir = root / "memory" / "chapters" / "001"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir.joinpath("polished.md").write_text(
        "---\nchapter_number: 1\ntitle: 雨夜旧车站\nstatus: polished\n---\n\n雨落在站台上。\n\n林夏抬头看见旧钟。\n",
        encoding="utf-8",
    )
    return root
