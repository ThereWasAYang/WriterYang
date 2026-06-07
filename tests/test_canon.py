from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from novel.cli import main
from novel.core.canon import (
    CanonApplyLog,
    CanonError,
    CanonSuggestOptions,
    apply_canon_proposal,
    default_mock_canon_proposal_json,
    load_canon_applied_proposals,
    load_canon_files,
    parse_canon_proposal,
    suggest_canon,
    validate_canon_proposal,
)
from novel.core.providers import MockProvider
from novel.core.validation import ValidationReport
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


def test_canon_suggest_can_receive_search_context(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    provider = MockProvider(fake_response=default_mock_canon_proposal_json())

    result = suggest_canon(CanonSuggestOptions(root=root, use_search_context=True), provider)

    assert "Context bundle" in provider.requests[0].user_prompt
    assert result.context_report_path is not None
    assert result.context_report_path.is_file()


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


def test_canon_suggest_repairs_low_risk_shape_errors(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    malformed = json.loads(default_mock_canon_proposal_json())
    malformed["characters"][0]["relationships"] = {}
    malformed["characters"][0]["aliases"] = "旧物修复师"
    malformed["locations"][0].pop("type")
    malformed["items"][0].pop("type")
    malformed["items"][0]["special_properties"] = [{"property": "无法损坏"}]
    malformed["world_rules"][0].pop("visibility")
    malformed["hidden_truths"][0]["summary"] = malformed["hidden_truths"][0].pop("title")
    malformed["foreshadowing_threads"][0].pop("status")
    provider = MockProvider(fake_response=[json.dumps(malformed, ensure_ascii=False)])

    result = suggest_canon(CanonSuggestOptions(root=root), provider)

    assert result.proposal.characters[0].relationships == []
    assert result.proposal.characters[0].aliases == ["旧物修复师"]
    assert result.proposal.locations[0].type == "unspecified"
    assert result.proposal.items[0].type == "unspecified"
    assert result.proposal.items[0].special_properties[0].description == "无法损坏"
    assert result.proposal.items[0].special_properties[0].visibility == "hidden"
    assert result.proposal.world_rules[0].visibility == "reader_visible"
    assert result.proposal.hidden_truths[0].title == "旧车站是时间交叠点"
    assert result.proposal.foreshadowing_threads[0].status == "active"


def test_canon_proposal_allows_foreshadowing_to_reference_world_rule() -> None:
    data = json.loads(default_mock_canon_proposal_json())
    rule_id = data["world_rules"][0]["id"]
    data["foreshadowing_threads"][0]["related_entity_ids"] = [rule_id]

    proposal = parse_canon_proposal(json.dumps(data, ensure_ascii=False))

    validate_canon_proposal(proposal)


def test_canon_proposal_normalizes_unknown_planned_chapters() -> None:
    data = json.loads(default_mock_canon_proposal_json())
    data["hidden_truths"][0]["planned_reveal"]["chapter"] = 0
    data["foreshadowing_threads"][0]["introduced_in_chapter"] = 0
    data["foreshadowing_threads"][0]["planned_payoff"]["chapter"] = "待定"

    proposal = parse_canon_proposal(json.dumps(data, ensure_ascii=False))

    assert proposal.hidden_truths[0].planned_reveal is None
    assert proposal.foreshadowing_threads[0].introduced_in_chapter == 1
    assert proposal.foreshadowing_threads[0].planned_payoff is None


def test_canon_proposal_can_reference_existing_canon_ids(tmp_path: Path) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    apply_canon_proposal(root, proposal_path)
    data = {
        "characters": [],
        "locations": [],
        "items": [],
        "world_rules": [],
        "hidden_truths": [
            {
                "id": "truth_new_signal",
                "title": "新信号只在旧车站出现",
                "description": "旧车站会触发新的隐藏信号。",
                "visibility": "hidden",
                "importance": "medium",
                "related_entity_ids": ["loc_old_station"],
                "foreshadowing_ids": [],
            }
        ],
        "foreshadowing_threads": [],
        "notes": [],
    }
    proposal = parse_canon_proposal(json.dumps(data, ensure_ascii=False))

    try:
        validate_canon_proposal(proposal)
    except CanonError as exc:
        assert "references missing entity: loc_old_station" in str(exc)
    else:
        raise AssertionError("expected missing existing canon reference without existing_canon")

    validate_canon_proposal(proposal, existing_canon=load_canon_files(root))


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
    records = load_canon_applied_proposals(root)
    assert len(records) == 1
    apply_log = records[0].apply_log
    assert isinstance(apply_log, CanonApplyLog)
    assert apply_log.status == "applied"
    assert apply_log.original_proposal_path == str(proposal_path)
    assert apply_log.proposal_counts.characters == 1
    assert apply_log.proposal_counts.locations == 1
    assert apply_log.proposal_counts.items == 1
    assert apply_log.proposal_counts.world_rules == 1
    assert apply_log.proposal_counts.hidden_truths == 1
    assert apply_log.proposal_counts.foreshadowing_threads == 1
    snapshot_path = root / apply_log.proposal_snapshot_path
    assert snapshot_path.is_file()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["characters"][0]["id"] == "char_lin_che"
    events_text = (root / "memory" / "management_events.jsonl").read_text(encoding="utf-8")
    assert "canon_proposal_applied" in events_text


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


def test_canon_apply_rolls_back_when_post_write_validation_fails(tmp_path: Path, monkeypatch) -> None:
    root = _workspace_with_inspiration(tmp_path)
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    original_characters = (root / "memory" / "canon" / "characters.json").read_text(encoding="utf-8")

    from novel.core import canon as canon_module

    def failing_validate_canon(_root: Path) -> ValidationReport:
        report = ValidationReport(root=_root)
        report.error(_root / "memory" / "canon" / "characters.json", "forced canon validation failure")
        return report

    monkeypatch.setattr(canon_module, "validate_canon", failing_validate_canon)

    try:
        apply_canon_proposal(root, proposal_path)
    except CanonError as exc:
        assert "forced canon validation failure" in str(exc)
    else:
        raise AssertionError("expected CanonError")

    assert (root / "memory" / "canon" / "characters.json").read_text(encoding="utf-8") == original_characters
    assert load_canon_applied_proposals(root) == []
    events_path = root / "memory" / "management_events.jsonl"
    if events_path.exists():
        assert "canon_proposal_applied" not in events_path.read_text(encoding="utf-8")


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
