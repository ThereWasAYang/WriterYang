from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.cli import main
from novel.core.canon import (
    CanonError,
    CanonSuggestOptions,
    default_mock_canon_proposal_json,
    parse_canon_proposal,
    suggest_canon,
    validate_canon_proposal,
)
from novel.core.providers import MockProvider
from novel.core.workspace import InitOptions, init_workspace


def test_mock_provider_generates_canon_proposal(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    provider = MockProvider(fake_response=default_mock_canon_proposal_json())

    result = suggest_canon(CanonSuggestOptions(root=root), provider)

    assert result.proposal.characters[0].id == "char_lin_che"
    assert result.proposal.locations[0].id == "loc_old_station"
    assert result.proposal.hidden_truths[0].id == "truth_station_overlap"
    assert provider.requests[0].json_schema_name == "CanonProposal"
    assert "hidden_truths 不得混入 reader_visible_summary" in provider.requests[0].user_prompt


def test_canon_suggest_does_not_overwrite_canon_files(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    original = (root / "memory" / "canon" / "characters.json").read_text(encoding="utf-8")

    code, stdout, stderr = _run_cli(["canon", "suggest", "--path", str(root), "--provider", "mock"])

    assert code == 0
    assert stderr == ""
    assert '"characters"' in stdout
    assert (root / "memory" / "canon" / "characters.json").read_text(encoding="utf-8") == original


def test_canon_suggest_can_save_proposal_file(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"

    code, stdout, stderr = _run_cli(
        [
            "canon",
            "suggest",
            "--path",
            str(root),
            "--provider",
            "mock",
            "--output",
            str(proposal_path),
        ]
    )

    assert code == 0
    assert stderr == ""
    assert "Wrote canon proposal:" in stdout
    data = json.loads(proposal_path.read_text(encoding="utf-8"))
    assert data["characters"][0]["id"] == "char_lin_che"


def test_canon_apply_merges_proposal_into_empty_canon(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")

    code, stdout, stderr = _run_cli(["canon", "apply", str(proposal_path), "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Canon validation passed" in stdout
    characters = json.loads((root / "memory" / "canon" / "characters.json").read_text(encoding="utf-8"))
    hidden_truths = json.loads(
        (root / "memory" / "canon" / "hidden_truths.json").read_text(encoding="utf-8")
    )
    foreshadowing = json.loads(
        (root / "memory" / "canon" / "foreshadowing.json").read_text(encoding="utf-8")
    )
    assert characters["characters"][0]["id"] == "char_lin_che"
    assert hidden_truths["hidden_truths"][0]["id"] == "truth_station_overlap"
    assert foreshadowing["foreshadowing_threads"][0]["hidden_truth_id"] == "truth_station_overlap"


def test_canon_apply_fails_on_duplicate_id(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")

    first, _, _ = _run_cli(["canon", "apply", str(proposal_path), "--path", str(root)])
    second, stdout, stderr = _run_cli(["canon", "apply", str(proposal_path), "--path", str(root)])

    assert first == 0
    assert second == 1
    assert stdout == ""
    assert "character id conflict: char_lin_che" in stderr


def test_canon_proposal_rejects_cross_type_duplicate_ids() -> None:
    data = json.loads(default_mock_canon_proposal_json())
    data["locations"][0]["id"] = data["characters"][0]["id"]

    try:
        validate_canon_proposal(parse_canon_proposal(json.dumps(data, ensure_ascii=False)))
    except CanonError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected CanonError")

    assert "duplicate cross-type canon id" in message


def test_canon_validate_finds_invalid_canon(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    (root / "memory" / "canon" / "characters.json").write_text(
        json.dumps({"characters": [{"id": "char_bad", "name": "坏数据"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    code, stdout, stderr = _run_cli(["canon", "validate", "--path", str(root)])

    assert code == 1
    assert stderr == ""
    assert "Canon validation failed" in stdout
    assert "reader_visible_summary" in stdout


def test_canon_validate_only_reads_canon_files(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    (root / "memory" / "state" / "current_state.json").unlink()
    (root / "config" / "agents.yaml").unlink()

    code, stdout, stderr = _run_cli(["canon", "validate", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Canon validation passed" in stdout


def test_show_canon_displays_summary(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    _run_cli(["canon", "apply", str(proposal_path), "--path", str(root)])

    code, stdout, stderr = _run_cli(["show", "canon", "--path", str(root)])

    assert code == 0
    assert stderr == ""
    assert "Characters: 1" in stdout
    assert "Locations: 1" in stdout
    assert "Hidden Truths:" in stdout
    assert "旧车站是时间交叠点 [truth_station_overlap]" in stdout


def test_hidden_truths_are_not_allowed_in_reader_visible_summary() -> None:
    data = json.loads(default_mock_canon_proposal_json())
    data["characters"][0]["reader_visible_summary"] = data["hidden_truths"][0]["description"]
    proposal = parse_canon_proposal(json.dumps(data, ensure_ascii=False))

    try:
        validate_canon_proposal(proposal)
    except CanonError as exc:
        assert "appears in reader_visible_summary" in str(exc)
    else:
        raise AssertionError("expected CanonError")


def _workspace_with_inspiration(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# Inspiration\n\n## Weak Outline\n\n雨夜旧车站传来停播多年的广播声。\n",
        encoding="utf-8",
    )
    return root


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()
