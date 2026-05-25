from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Iterable

from pydantic import ValidationError

from novel.core.io import atomic_write_text, backup_file, load_json_model, load_yaml_model
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
from novel.core.validation import ValidationReport, validate_canon


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

    user_prompt = build_canon_user_prompt(
        project=project,
        inspiration_md=inspiration_md,
        inspiration_json=inspiration_json,
        style_guide=style_guide,
        existing_summary=existing_summary,
    )
    proposal = _generate_canon_proposal_with_repair(provider, user_prompt, existing_summary)
    proposal_json = proposal.model_dump_json(indent=2) + "\n"

    output_path = options.output_path
    if output_path:
        if output_path.exists():
            raise CanonError(f"{output_path} already exists; refusing to overwrite proposal")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(output_path, proposal_json)

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

    backups = _backup_existing_canon_files(root)
    try:
        write_canon_files(root, canon, backup=False)
        report = validate_canon(root)
        if not report.ok:
            raise CanonError(format_canon_validation_report(report))
    except Exception:
        _restore_backups(backups)
        raise
    return CanonApplyResult(validation_report=report)


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


def write_canon_files(root: Path, canon: CanonFiles, *, backup: bool = True) -> None:
    canon_dir = root / "memory" / "canon"
    _write_json(canon_dir / "characters.json", {"characters": canon.characters.characters}, backup=backup)
    _write_json(canon_dir / "locations.json", {"locations": canon.locations.locations}, backup=backup)
    _write_json(canon_dir / "items.json", {"items": canon.items.items}, backup=backup)
    _write_json(canon_dir / "world.json", {"world_rules": canon.world.world_rules}, backup=backup)
    _write_json(canon_dir / "hidden_truths.json", {"hidden_truths": canon.hidden_truths.hidden_truths}, backup=backup)
    _write_json(
        canon_dir / "foreshadowing.json",
        {"foreshadowing_threads": canon.foreshadowing.foreshadowing_threads},
        backup=backup,
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
        '  "characters": [{"id": "char_x", "name": "姓名", "role": "protagonist", "reader_visible_summary": "读者可见摘要", "aliases": [], "relationships": [], "tags": []}],\n'
        '  "locations": [{"id": "loc_x", "name": "地点名", "type": "station", "reader_visible_summary": "读者可见摘要", "connected_location_ids": [], "rules": [], "tags": []}],\n'
        '  "items": [{"id": "item_x", "name": "物品名", "type": "clue", "reader_visible_summary": "读者可见摘要", "special_properties": [], "tags": []}],\n'
        '  "world_rules": [{"id": "rule_x", "name": "规则名", "description": "规则说明", "visibility": "reader_visible", "limitations": [], "known_by_character_ids": []}],\n'
        '  "hidden_truths": [{"id": "truth_x", "title": "隐藏真相标题", "description": "只给作者看的隐藏背景", "visibility": "hidden", "importance": "medium", "related_entity_ids": [], "foreshadowing_ids": []}],\n'
        '  "foreshadowing_threads": [{"id": "thread_x", "type": "clue", "title": "伏笔标题", "introduced_in_chapter": 1, "description": "伏笔说明", "status": "active", "importance": "medium", "hidden_truth_id": "truth_x", "related_entity_ids": []}],\n'
        '  "notes": []\n'
        "}\n\n"
        "要求：\n"
        "- 不要输出 Markdown。\n"
        "- ID 使用稳定前缀：char_, loc_, item_, rule_, truth_, thread_。\n"
        "- 所有 id 必须匹配 ^[a-z0-9_]+$。\n"
        "- characters 每项必须包含 id, name, role, reader_visible_summary；relationships 必须是数组。\n"
        "- locations 每项必须包含 id, name, type, reader_visible_summary。\n"
        "- items 每项必须包含 id, name, type, reader_visible_summary。\n"
        "- world_rules 每项必须包含 id, name, description, visibility；visibility 只能是 reader_visible/hidden/partially_revealed。\n"
        "- hidden_truths 每项必须包含 id, title, description, visibility, importance；importance 只能是 low/medium/high/critical。\n"
        "- foreshadowing_threads 每项必须包含 id, type, title, introduced_in_chapter, description, status, importance；status 只能是 active/inactive/resolved/unresolved/deprecated。\n"
        "- aliases, tags, relationships, limitations, related_entity_ids, foreshadowing_ids 必须是数组，不能是对象或字符串。\n"
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
        return CanonProposal.model_validate(_normalize_canon_proposal_data(data))
    except ValidationError as exc:
        raise CanonError(f"provider returned invalid CanonProposal: {exc}") from exc


def _generate_canon_proposal_with_repair(
    provider: ModelProvider,
    user_prompt: str,
    existing_summary: str,
) -> CanonProposal:
    response = provider.generate(
        ModelRequest(
            system_prompt=build_canon_system_prompt(),
            user_prompt=user_prompt,
            context=existing_summary,
            json_schema_name="CanonProposal",
        )
    )
    try:
        proposal = parse_canon_proposal(response.content)
        validate_canon_proposal(proposal)
        return proposal
    except CanonError as exc:
        repair_response = provider.generate(
            ModelRequest(
                system_prompt=build_canon_system_prompt(),
                user_prompt=_repair_prompt(
                    schema_name="CanonProposal",
                    original_prompt=user_prompt,
                    invalid_output=response.content,
                    error=str(exc),
                ),
                context=existing_summary,
                json_schema_name="CanonProposal",
            )
        )
        proposal = parse_canon_proposal(repair_response.content)
        validate_canon_proposal(proposal)
        return proposal


def _repair_prompt(*, schema_name: str, original_prompt: str, invalid_output: str, error: str) -> str:
    return (
        f"{original_prompt}\n\n"
        f"上一次输出不是合法的 {schema_name}。请只输出修复后的严格 JSON，不要解释。\n"
        f"错误摘要：\n{error[:4000]}\n\n"
        f"上一次输出：\n{invalid_output[:12000]}\n"
    )


def _normalize_canon_proposal_data(data: object) -> object:
    if not isinstance(data, dict):
        return data
    normalized = dict(data)
    list_fields = {
        "aliases",
        "tags",
        "relationships",
        "abilities",
        "secrets",
        "rules",
        "connected_location_ids",
        "special_properties",
        "limitations",
        "known_by_character_ids",
        "related_entity_ids",
        "foreshadowing_ids",
    }
    for collection_name in (
        "characters",
        "locations",
        "items",
        "world_rules",
        "hidden_truths",
        "foreshadowing_threads",
        "notes",
    ):
        value = normalized.get(collection_name)
        if value is None:
            continue
        if not isinstance(value, list):
            normalized[collection_name] = []
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            _normalize_canon_item(collection_name, item)
            for field in list_fields:
                if field in item:
                    item[field] = _normalize_list_field(item[field])
    return normalized


def _normalize_canon_item(collection_name: str, item: dict[str, object]) -> None:
    if collection_name in {"locations", "items"}:
        item.setdefault("type", "unspecified")
    if collection_name == "world_rules":
        item.setdefault("visibility", "reader_visible")
    if collection_name == "hidden_truths":
        _copy_first_present(item, "title", ("name", "summary", "label"))
        _copy_first_present(item, "description", ("content", "summary", "truth", "private_author_notes", "notes"))
        item.setdefault("visibility", "hidden")
        item.setdefault("importance", "medium")
    if collection_name == "foreshadowing_threads":
        item.setdefault("type", "clue")
        _copy_first_present(item, "title", ("name", "summary", "description"))
        item.setdefault("introduced_in_chapter", 1)
        item.setdefault("status", "active")
        item.setdefault("importance", "medium")


def _copy_first_present(item: dict[str, object], target: str, aliases: tuple[str, ...]) -> None:
    if item.get(target):
        return
    for alias in aliases:
        value = item.get(alias)
        if isinstance(value, str) and value.strip():
            item[target] = value
            return


def _normalize_list_field(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return []
    if value is None:
        return []
    return [value]


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


def _write_json(path: Path, data: object, *, backup: bool = False) -> None:
    if backup:
        backup_file(path, reason="canon_apply")
    atomic_write_text(path, _to_json(data))


def _canon_file_paths(root: Path) -> tuple[Path, ...]:
    canon_dir = root / "memory" / "canon"
    return (
        canon_dir / "characters.json",
        canon_dir / "locations.json",
        canon_dir / "items.json",
        canon_dir / "world.json",
        canon_dir / "hidden_truths.json",
        canon_dir / "foreshadowing.json",
    )


def _backup_existing_canon_files(root: Path) -> dict[Path, Path]:
    backups: dict[Path, Path] = {}
    for path in _canon_file_paths(root):
        if path.exists():
            backups[path] = backup_file(path, reason="canon_apply")
    return backups


def _restore_backups(backups: dict[Path, Path]) -> None:
    for path, backup_path in backups.items():
        shutil.copy2(backup_path, path)


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
