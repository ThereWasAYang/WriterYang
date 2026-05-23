from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from novel.core.io import load_json_model, load_yaml_model
from novel.core.provider_config import ProviderOverrides, create_agent_provider, default_agent_config_path
from novel.core.providers import ModelProvider, ModelRequest
from novel.core.prompts import load_prompt_template
from novel.core.schemas import (
    CanonProposal,
    CharactersFile,
    ForeshadowingFile,
    HiddenTruthsFile,
    ItemsFile,
    LocationsFile,
    ProjectConfig,
    WorldFile,
)
from novel.core.validation import ValidationReport, validate_canon, validate_project


class CanonError(RuntimeError):
    """Raised when canon proposal generation or application fails safely."""


@dataclass(frozen=True)
class CanonSuggestOptions:
    root: Path
    output_path: Path | None = None


@dataclass(frozen=True)
class CanonSuggestResult:
    proposal: CanonProposal
    proposal_json: str
    output_path: Path | None


@dataclass(frozen=True)
class CanonApplyResult:
    validation_report: ValidationReport


def suggest_canon(options: CanonSuggestOptions, provider: ModelProvider) -> CanonSuggestResult:
    root = options.root.resolve()
    _require_inspiration(root)
    project = load_yaml_model(root / "project.yaml", ProjectConfig)
    inspiration_md = (root / "memory" / "inspiration.md").read_text(encoding="utf-8")
    inspiration_json = _read_optional_text(root / "memory" / "inspiration.json")
    style_guide = _read_optional_text(root / "memory" / "style_guide.md")
    existing_summary = format_canon_summary(load_canon_files(root))

    response = provider.generate(
        ModelRequest(
            system_prompt=build_canon_system_prompt(),
            user_prompt=build_canon_user_prompt(
                project=project,
                inspiration_md=inspiration_md,
                inspiration_json=inspiration_json,
                style_guide=style_guide,
                existing_summary=existing_summary,
            ),
            context=existing_summary,
            json_schema_name="CanonProposal",
        )
    )
    proposal = parse_canon_proposal(response.content)
    validate_canon_proposal(proposal)
    proposal_json = proposal.model_dump_json(indent=2) + "\n"

    output_path = options.output_path
    if output_path:
        if output_path.exists():
            raise CanonError(f"{output_path} already exists; refusing to overwrite proposal")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(proposal_json, encoding="utf-8")

    return CanonSuggestResult(
        proposal=proposal,
        proposal_json=proposal_json,
        output_path=output_path,
    )


def apply_canon_proposal(root: Path, proposal_path: Path) -> CanonApplyResult:
    root = root.resolve()
    if not proposal_path.exists():
        raise CanonError(f"proposal file is missing: {proposal_path}")
    try:
        proposal = load_json_model(proposal_path, CanonProposal)
    except ValidationError as exc:
        raise CanonError(f"invalid canon proposal: {exc}") from exc
    except Exception as exc:
        raise CanonError(f"could not read proposal JSON: {exc}") from exc

    validate_canon_proposal(proposal)
    canon = load_canon_files(root)
    _check_apply_conflicts(canon, proposal)

    canon.characters.characters.extend(proposal.characters)
    canon.locations.locations.extend(proposal.locations)
    canon.items.items.extend(proposal.items)
    canon.world.world_rules.extend(proposal.world_rules)
    canon.hidden_truths.hidden_truths.extend(proposal.hidden_truths)
    canon.foreshadowing.foreshadowing_threads.extend(proposal.foreshadowing_threads)

    write_canon_files(root, canon)
    return CanonApplyResult(validation_report=validate_project(root))


def load_canon_provider(
    root: Path,
    provider_name: str,
    *,
    agent_config_path: Path | None = None,
    model_name: str | None = None,
) -> ModelProvider:
    return create_agent_provider(
        agent_config_path or default_agent_config_path(root),
        "canon",
        fallback_agents=("inspiration",),
        overrides=ProviderOverrides(provider_name=provider_name, model_name=model_name),
        mock_response=default_mock_canon_proposal_json(),
    )


@dataclass(frozen=True)
class CanonFiles:
    characters: CharactersFile
    locations: LocationsFile
    items: ItemsFile
    world: WorldFile
    hidden_truths: HiddenTruthsFile
    foreshadowing: ForeshadowingFile


def load_canon_files(root: Path) -> CanonFiles:
    canon_dir = root / "memory" / "canon"
    return CanonFiles(
        characters=load_json_model(canon_dir / "characters.json", CharactersFile),
        locations=load_json_model(canon_dir / "locations.json", LocationsFile),
        items=load_json_model(canon_dir / "items.json", ItemsFile),
        world=load_json_model(canon_dir / "world.json", WorldFile),
        hidden_truths=load_json_model(canon_dir / "hidden_truths.json", HiddenTruthsFile),
        foreshadowing=load_json_model(canon_dir / "foreshadowing.json", ForeshadowingFile),
    )


def write_canon_files(root: Path, canon: CanonFiles) -> None:
    canon_dir = root / "memory" / "canon"
    _write_json(canon_dir / "characters.json", {"characters": canon.characters.characters})
    _write_json(canon_dir / "locations.json", {"locations": canon.locations.locations})
    _write_json(canon_dir / "items.json", {"items": canon.items.items})
    _write_json(canon_dir / "world.json", {"world_rules": canon.world.world_rules})
    _write_json(canon_dir / "hidden_truths.json", {"hidden_truths": canon.hidden_truths.hidden_truths})
    _write_json(
        canon_dir / "foreshadowing.json",
        {"foreshadowing_threads": canon.foreshadowing.foreshadowing_threads},
    )


def format_canon_summary(canon: CanonFiles) -> str:
    lines = [
        "Canon:",
        f"- Characters: {len(canon.characters.characters)}",
        f"- Locations: {len(canon.locations.locations)}",
        f"- Items: {len(canon.items.items)}",
        f"- World rules: {len(canon.world.world_rules)}",
        f"- Hidden truths: {len(canon.hidden_truths.hidden_truths)}",
        f"- Foreshadowing threads: {len(canon.foreshadowing.foreshadowing_threads)}",
    ]
    _extend_named(lines, "Characters", ((item.id, item.name) for item in canon.characters.characters))
    _extend_named(lines, "Locations", ((item.id, item.name) for item in canon.locations.locations))
    _extend_named(lines, "Items", ((item.id, item.name) for item in canon.items.items))
    _extend_named(lines, "World Rules", ((item.id, item.name) for item in canon.world.world_rules))
    _extend_named(lines, "Hidden Truths", ((item.id, item.title) for item in canon.hidden_truths.hidden_truths))
    _extend_named(
        lines,
        "Foreshadowing",
        ((item.id, item.title) for item in canon.foreshadowing.foreshadowing_threads),
    )
    return "\n".join(lines)


def format_canon_validation_report(report: ValidationReport) -> str:
    lines = []
    for message in report.messages:
        path = message.path
        try:
            path = path.relative_to(report.root)
        except ValueError:
            pass
        lines.append(f"{message.level}: {path}: {message.message}")
    if report.ok:
        lines.append(f"Canon validation passed: {len(report.warnings)} warning(s)")
    else:
        lines.append(
            f"Canon validation failed: {len(report.errors)} error(s), "
            f"{len(report.warnings)} warning(s)"
        )
    return "\n".join(lines)


def build_canon_system_prompt() -> str:
    return load_prompt_template("canon_system")


def build_canon_user_prompt(
    *,
    project: ProjectConfig,
    inspiration_md: str,
    inspiration_json: str,
    style_guide: str,
    existing_summary: str,
) -> str:
    return (
        f"项目：{project.title}\n"
        f"语言：{project.language}\n"
        f"类型：{', '.join(project.genre)}\n\n"
        "请输出严格 JSON，结构如下：\n"
        "{\n"
        '  "characters": [],\n'
        '  "locations": [],\n'
        '  "items": [],\n'
        '  "world_rules": [],\n'
        '  "hidden_truths": [],\n'
        '  "foreshadowing_threads": [],\n'
        '  "notes": []\n'
        "}\n\n"
        "要求：\n"
        "- 不要输出 Markdown。\n"
        "- ID 使用稳定前缀：char_, loc_, item_, rule_, truth_, thread_。\n"
        "- reader_visible_summary 只写读者可见信息。\n"
        "- hidden_truths 不得混入 reader_visible_summary。\n"
        "- foreshadowing_threads 应关联 hidden_truth_id 或 related_entity_ids。\n\n"
        f"已有 canon 摘要：\n{existing_summary}\n\n"
        f"style guide：\n{style_guide}\n\n"
        f"inspiration.md：\n{inspiration_md}\n\n"
        f"inspiration.json：\n{inspiration_json}\n"
    )


def parse_canon_proposal(content: str) -> CanonProposal:
    json_text = _extract_json_object(content)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise CanonError(f"provider did not return valid CanonProposal JSON: {exc}") from exc
    try:
        return CanonProposal.model_validate(data)
    except ValidationError as exc:
        raise CanonError(f"provider returned invalid CanonProposal: {exc}") from exc


def validate_canon_proposal(proposal: CanonProposal) -> None:
    _require_unique_ids([item.id for item in proposal.characters], "character")
    _require_unique_ids([item.id for item in proposal.locations], "location")
    _require_unique_ids([item.id for item in proposal.items], "item")
    _require_unique_ids([item.id for item in proposal.world_rules], "world rule")
    _require_unique_ids([item.id for item in proposal.hidden_truths], "hidden truth")
    _require_unique_ids([item.id for item in proposal.foreshadowing_threads], "foreshadowing thread")
    _require_unique_ids(_proposal_all_ids(proposal), "cross-type canon")

    entity_ids = (
        {item.id for item in proposal.characters}
        | {item.id for item in proposal.locations}
        | {item.id for item in proposal.items}
    )
    truth_ids = {item.id for item in proposal.hidden_truths}
    thread_ids = {item.id for item in proposal.foreshadowing_threads}

    for truth in proposal.hidden_truths:
        for thread_id in truth.foreshadowing_ids:
            if thread_id not in thread_ids:
                raise CanonError(
                    f"hidden truth {truth.id} references missing foreshadowing thread: {thread_id}"
                )
        for entity_id in truth.related_entity_ids:
            if entity_id not in entity_ids:
                raise CanonError(f"hidden truth {truth.id} references missing entity: {entity_id}")

    for thread in proposal.foreshadowing_threads:
        if thread.hidden_truth_id and thread.hidden_truth_id not in truth_ids:
            raise CanonError(
                f"foreshadowing thread {thread.id} references missing hidden truth: {thread.hidden_truth_id}"
            )
        for entity_id in thread.related_entity_ids:
            if entity_id not in entity_ids:
                raise CanonError(f"foreshadowing thread {thread.id} references missing entity: {entity_id}")

    _ensure_hidden_truths_not_reader_visible(proposal)


def default_mock_canon_proposal_json() -> str:
    return json.dumps(
        {
            "characters": [
                {
                    "id": "char_lin_che",
                    "name": "林澈",
                    "aliases": ["阿澈"],
                    "role": "protagonist",
                    "reader_visible_summary": "年轻的旧物修复师，性格沉静，习惯从旧物痕迹里寻找答案。",
                    "private_author_notes": "他与旧车站过去的异常事件有关，但本人记忆并不完整。",
                    "tags": ["主角", "旧物修复", "追查者"],
                }
            ],
            "locations": [
                {
                    "id": "loc_old_station",
                    "name": "旧车站",
                    "type": "交通设施",
                    "reader_visible_summary": "废弃多年的郊区车站，雨夜里偶尔会出现不合时宜的广播声。",
                    "private_author_notes": "这里与隐藏的时间异常有关。",
                    "tags": ["核心地点", "异常空间"],
                }
            ],
            "items": [
                {
                    "id": "item_broken_ticket",
                    "name": "破损车票",
                    "type": "线索",
                    "reader_visible_summary": "一张被雨水泡皱的旧车票，只剩半截日期。",
                    "private_author_notes": "完整日期指向林澈失去记忆的关键夜晚。",
                    "tags": ["线索", "伏笔"],
                }
            ],
            "world_rules": [
                {
                    "id": "rule_memory_residue",
                    "name": "旧物残响",
                    "description": "强烈情绪会残留在旧物中，并被少数敏感者感知为碎片。",
                    "visibility": "partially_revealed",
                    "limitations": ["只能感知碎片", "不能直接改变过去"],
                    "known_by_character_ids": ["char_lin_che"],
                }
            ],
            "hidden_truths": [
                {
                    "id": "truth_station_overlap",
                    "title": "旧车站是时间交叠点",
                    "description": "旧车站在特定雨夜会短暂连接过去的时间层。",
                    "related_entity_ids": ["loc_old_station", "char_lin_che", "item_broken_ticket"],
                    "visibility": "hidden",
                    "planned_reveal": {"chapter": 28, "method": "通过旧广播和车票日期逐步揭示"},
                    "foreshadowing_ids": ["thread_station_broadcast"],
                    "importance": "critical",
                }
            ],
            "foreshadowing_threads": [
                {
                    "id": "thread_station_broadcast",
                    "type": "伏笔",
                    "title": "停播多年的广播声",
                    "introduced_in_chapter": 1,
                    "description": "旧车站雨夜响起已经停播多年的广播。",
                    "reader_visible": True,
                    "hidden_truth_id": "truth_station_overlap",
                    "hidden_truth": "广播来自过去的时间层。",
                    "status": "unresolved",
                    "planned_payoff": {"chapter": 12, "description": "主角确认广播并非录音。"},
                    "related_entity_ids": ["loc_old_station"],
                    "importance": "high",
                }
            ],
            "notes": ["Mock proposal for MVP canon generation tests."],
        },
        ensure_ascii=False,
    )


def _require_inspiration(root: Path) -> None:
    path = root / "memory" / "inspiration.md"
    if not path.exists():
        raise CanonError("memory/inspiration.md is missing; run novel inspire first")
    if not path.read_text(encoding="utf-8").strip():
        raise CanonError("memory/inspiration.md is empty; run novel inspire first")


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _write_json(path: Path, data: object) -> None:
    path.write_text(_to_json(data), encoding="utf-8")


def _to_json(data: object) -> str:
    if isinstance(data, dict) and "schema_version" not in data:
        data = {"schema_version": 1, **data}

    def default(value: object) -> object:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        raise TypeError(f"unsupported JSON value: {value!r}")

    return json.dumps(data, ensure_ascii=False, indent=2, default=default) + "\n"


def _extend_named(lines: list[str], title: str, entries: Iterable[tuple[str, str]]) -> None:
    entries = list(entries)
    if not entries:
        return
    lines.append(f"{title}:")
    for entity_id, name in entries:
        lines.append(f"- {name} [{entity_id}]")


def _extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise CanonError("provider response does not contain a JSON object")
    return stripped[start : end + 1]


def _require_unique_ids(values: list[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise CanonError(f"duplicate {label} id(s) in proposal: {', '.join(sorted(duplicates))}")


def _check_apply_conflicts(canon: CanonFiles, proposal: CanonProposal) -> None:
    _check_conflict([item.id for item in canon.characters.characters], [item.id for item in proposal.characters], "character")
    _check_conflict([item.id for item in canon.locations.locations], [item.id for item in proposal.locations], "location")
    _check_conflict([item.id for item in canon.items.items], [item.id for item in proposal.items], "item")
    _check_conflict([item.id for item in canon.world.world_rules], [item.id for item in proposal.world_rules], "world rule")
    _check_conflict([item.id for item in canon.hidden_truths.hidden_truths], [item.id for item in proposal.hidden_truths], "hidden truth")
    _check_conflict(
        [item.id for item in canon.foreshadowing.foreshadowing_threads],
        [item.id for item in proposal.foreshadowing_threads],
        "foreshadowing thread",
    )
    _check_conflict(_canon_all_ids(canon), _proposal_all_ids(proposal), "cross-type canon")


def _canon_all_ids(canon: CanonFiles) -> list[str]:
    return [
        *[item.id for item in canon.characters.characters],
        *[item.id for item in canon.locations.locations],
        *[item.id for item in canon.items.items],
        *[item.id for item in canon.world.world_rules],
        *[item.id for item in canon.hidden_truths.hidden_truths],
        *[item.id for item in canon.foreshadowing.foreshadowing_threads],
    ]


def _proposal_all_ids(proposal: CanonProposal) -> list[str]:
    return [
        *[item.id for item in proposal.characters],
        *[item.id for item in proposal.locations],
        *[item.id for item in proposal.items],
        *[item.id for item in proposal.world_rules],
        *[item.id for item in proposal.hidden_truths],
        *[item.id for item in proposal.foreshadowing_threads],
    ]


def _check_conflict(existing: list[str], incoming: list[str], label: str) -> None:
    conflicts = sorted(set(existing) & set(incoming))
    if conflicts:
        raise CanonError(f"{label} id conflict: {', '.join(conflicts)}")


def _ensure_hidden_truths_not_reader_visible(proposal: CanonProposal) -> None:
    summaries = []
    summaries.extend((item.id, item.reader_visible_summary) for item in proposal.characters)
    summaries.extend((item.id, item.reader_visible_summary) for item in proposal.locations)
    summaries.extend((item.id, item.reader_visible_summary) for item in proposal.items)
    for truth in proposal.hidden_truths:
        fragments = [truth.title.strip(), truth.description.strip()]
        for entity_id, summary in summaries:
            for fragment in fragments:
                if fragment and fragment in summary:
                    raise CanonError(
                        f"hidden truth {truth.id} appears in reader_visible_summary for {entity_id}"
                    )
