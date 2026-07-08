from __future__ import annotations

from .deps import (
    difflib,
    json,
    Path,
    cast,
    yaml,
    TASK_TO_PROFILE,
    profile_for_task,
    localize_audit_issue_for_author,
    chapter_memory_freshness_warnings,
    load_project_env,
    load_json,
    load_json_model,
    load_yaml,
    load_management_events,
    resolve_agent_config_source,
    resolve_profile_config_source,
    EmbeddingError,
    resolve_embedding_parameters,
    AgentsConfig,
    AuditReport,
    ChapterMemory,
    ChapterPlan,
    EmbeddingsConfig,
    ProviderFactory,
    summarize_provider_usage,
)

from .common import (
    EDITABLE_PROFILE_NAMES,
    EDITABLE_TASK_NAMES,
    _safe_config_file,
    _agent_config_warnings,
    _safe_json,
    _sanitize_config,
    _safe_workspace_file,
    _locate_quote,
    _is_safe_tree_path,
    _require_workspace,
    _relative,
    _safe_error,
)

from .config import _parameter_capabilities_payload, _profile_config_payload, _profile_parameter_capabilities_payload

def _management_events(root: Path, limit: int = 20) -> dict[str, object]:
    _require_workspace(root)
    return {"events": _management_event_summary(root, limit=limit)}


def _management_event_summary(root: Path, limit: int = 10) -> list[dict[str, object]]:
    return [event.model_dump(mode="json") for event in load_management_events(root, limit=limit)]


def _list_projects(root: Path) -> list[dict[str, str]]:
    base = root.expanduser().resolve()
    candidates = []
    if (base / "project.yaml").exists():
        candidates.append(base)
    if base.exists() and base.is_dir():
        candidates.extend(path for path in base.iterdir() if (path / "project.yaml").exists())
    return [{"path": str(path)} for path in sorted(set(candidates))]


def _file_tree(root: Path) -> list[dict[str, object]]:
    _require_workspace(root)
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        rel = _relative(root, path)
        if not _is_safe_tree_path(rel, path):
            continue
        files.append(
            {
                "path": rel,
                "name": path.name,
                "type": "directory" if path.is_dir() else "file",
                "size": path.stat().st_size if path.is_file() else None,
            }
        )
    return files


def _read_workspace_file(root: Path, rel_path: str) -> dict[str, object]:
    _require_workspace(root)
    path = _safe_workspace_file(root, rel_path)
    if not path.exists():
        raise FileNotFoundError(f"{rel_path} does not exist")
    return {
        "path": _relative(root, path),
        "content": path.read_text(encoding="utf-8"),
        "exists": True,
    }


def _runs_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    runs_dir = root / "runs"
    run_logs: list[dict[str, object]] = []
    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            try:
                data = load_json(path)
            except Exception:
                data = {}
            run_logs.append(
                {
                    "path": _relative(root, path),
                    "run_id": data.get("run_id") if isinstance(data, dict) else None,
                    "task": data.get("task") if isinstance(data, dict) else None,
                    "chapter_number": data.get("chapter_number") if isinstance(data, dict) else None,
                    "status": data.get("status") if isinstance(data, dict) else None,
                    "started_at": data.get("started_at") if isinstance(data, dict) else None,
                    "ended_at": data.get("ended_at") if isinstance(data, dict) else None,
                    "error_count": len(data.get("errors", [])) if isinstance(data, dict) and isinstance(data.get("errors"), list) else 0,
                }
            )
    provider_calls = _provider_call_summary(runs_dir / "provider_calls.jsonl")
    return {
        "run_logs": run_logs,
        "provider_calls": provider_calls,
        "model_io_logs": _model_io_summary(runs_dir / "model_io" / "index.jsonl"),
        "provider_usage": summarize_provider_usage(root).as_dict(),
    }


def _provider_config_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    agents_path = root / "config" / "agents.yaml"
    agents = _safe_config_file(agents_path)
    agents["warnings"] = _agent_config_warnings(root / "config" / "agents.yaml")
    return {
        "agents": agents,
        "embeddings": _safe_config_file(root / "config" / "embeddings.yaml"),
        "effective_profiles": _effective_profile_config_summary(agents_path),
        "effective_tasks": _effective_task_config_summary(agents_path),
        "embedding_api": _embedding_api_config_summary(root),
    }


def _embedding_api_config_summary(root: Path) -> dict[str, object]:
    path = root / "config" / "embeddings.yaml"
    if not path.exists():
        return {
            "configured": False,
            "status": "not_configured",
            "active_provider": None,
            "provider": None,
            "model": None,
            "env_missing": [],
        }
    try:
        config = EmbeddingsConfig.model_validate(load_yaml(path))
    except Exception as exc:
        return {
            "configured": False,
            "status": "invalid_config",
            "active_provider": None,
            "provider": None,
            "model": None,
            "env_missing": [],
            "message": _safe_error(str(exc)),
        }
    selected = config.providers.get(config.active_provider)
    if selected is None:
        return {
            "configured": False,
            "status": "not_configured",
            "active_provider": config.active_provider,
            "provider": None,
            "model": None,
            "env_missing": [],
        }
    provider = selected.provider.lower()
    if provider == "local_hash":
        return {
            "configured": False,
            "status": "test_only",
            "active_provider": config.active_provider,
            "provider": provider,
            "model": selected.model,
            "dimensions": selected.dimensions,
            "batch_size": selected.batch_size,
            "env_missing": [],
        }
    env = load_project_env(root)
    missing: list[str] = []
    if selected.api_key_env and not env.get(selected.api_key_env):
        missing.append(selected.api_key_env)
    if provider == "openai_compatible" and selected.base_url_env and not env.get(selected.base_url_env):
        missing.append(selected.base_url_env)
    if not selected.api_key_env:
        missing.append("api_key_env")
    base_url = env.get(selected.base_url_env) if selected.base_url_env else None
    effective_dimensions = selected.dimensions
    effective_batch_size = selected.batch_size
    effective_provider = provider
    warnings: list[str] = []
    try:
        resolved_dimensions, resolved_batch_size, capability = resolve_embedding_parameters(
            provider,
            selected.model,
            base_url=base_url,
            dimensions=selected.dimensions,
            batch_size=selected.batch_size,
            clamp_batch_size=True,
        )
        effective_dimensions = resolved_dimensions
        effective_batch_size = resolved_batch_size
        effective_provider = capability.canonical_provider
    except EmbeddingError as exc:
        warnings.append(_safe_error(str(exc)))
    if effective_batch_size != selected.batch_size:
        warnings.append(f"当前 provider 实际请求 batch_size 会限制为 {effective_batch_size}")
    return {
        "configured": not missing,
        "status": "env_missing" if missing else "configured",
        "active_provider": config.active_provider,
        "provider": provider,
        "effective_provider": effective_provider,
        "model": selected.model,
        "dimensions": selected.dimensions if selected.dimensions is not None else effective_dimensions,
        "batch_size": selected.batch_size,
        "effective_dimensions": effective_dimensions,
        "effective_batch_size": effective_batch_size,
        "api_key_env": selected.api_key_env,
        "base_url_env": selected.base_url_env,
        "env_missing": list(dict.fromkeys(missing)),
        "warnings": warnings,
    }


def _effective_profile_config_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = load_yaml(path)
        config = AgentsConfig.model_validate(raw)
    except Exception as exc:
        return {
            name: {"source": "unresolved", "source_label": "unresolved", "error": _safe_error(str(exc))}
            for name in sorted(EDITABLE_PROFILE_NAMES)
        }
    raw_profiles = raw.get("profiles") if isinstance(raw, dict) else {}
    if not isinstance(raw_profiles, dict):
        raw_profiles = {}
    names = ["default", *sorted(set(EDITABLE_PROFILE_NAMES) | set(raw_profiles))]
    resolver = ProviderFactory(env={})
    summaries: dict[str, object] = {}
    for name in names:
        if name == "default":
            if config.default is None:
                summaries[name] = {"source": "unresolved", "source_label": "unresolved", "has_override": False}
                continue
            summaries[name] = {
                "source": "default",
                "source_label": "default",
                "has_override": False,
                "inherit_default": False,
                "inherits_default": False,
                "override_fields": [],
                "config": _profile_config_payload(config.default),
                "parameter_capabilities": _profile_parameter_capabilities_payload(config.default),
            }
            continue
        has_config_entry = name in config.profiles
        selected = config.profiles.get(name)
        explicit_inherit = bool(getattr(selected, "inherit_default", False)) if selected is not None else False
        raw_override: dict[str, object] = (
            selected.model_dump(mode="json", exclude_unset=True, exclude_none=True, exclude={"inherit_default"})
            if selected is not None
            else {}
        )
        override = cast(dict[str, object], _sanitize_config(raw_override))
        try:
            resolved = resolver.resolve_profile_config(config, name)
        except Exception as exc:
            summaries[name] = {
                "source": "unresolved",
                "source_label": "unresolved",
                "has_override": has_config_entry and not explicit_inherit,
                "inherit_default": explicit_inherit,
                "inherits_default": explicit_inherit,
                "override_fields": sorted(override),
                "override": override,
                "error": _safe_error(str(exc)),
            }
            continue
        has_override = has_config_entry and (bool(override) or not explicit_inherit)
        source = resolve_profile_config_source(path, name)
        summaries[name] = {
            "source": source,
            "source_label": source,
            "has_override": has_override,
            "inherit_default": explicit_inherit or (not has_config_entry and config.default is not None),
            "inherits_default": explicit_inherit or (not has_config_entry and config.default is not None),
            "override_fields": sorted(override),
            "override": override,
            "config": _profile_config_payload(resolved),
            "parameter_capabilities": _profile_parameter_capabilities_payload(resolved),
        }
    return summaries


def _effective_task_config_summary(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = load_yaml(path)
        config = AgentsConfig.model_validate(raw)
    except Exception as exc:
        return {
            name: {"source": "unresolved", "source_label": "unresolved", "error": _safe_error(str(exc))}
            for name in sorted(EDITABLE_TASK_NAMES)
        }
    raw_tasks = raw.get("tasks") if isinstance(raw, dict) else {}
    if not isinstance(raw_tasks, dict):
        raw_tasks = {}
    names = sorted(set(EDITABLE_TASK_NAMES) | set(raw_tasks))
    resolver = ProviderFactory(env={})
    summaries: dict[str, object] = {}
    for name in names:
        has_config_entry = name in config.tasks
        selected = config.tasks.get(name)
        raw_override: dict[str, object] = (
            selected.model_dump(mode="json", exclude_unset=True, exclude_none=True, exclude={"inherit_default"})
            if selected is not None
            else {}
        )
        override = cast(dict[str, object], _sanitize_config(raw_override))
        try:
            resolved = resolver.resolve_agent_config(config, name)
        except Exception as exc:
            summaries[name] = {
                "source": "unresolved",
                "source_label": "unresolved",
                "profile": TASK_TO_PROFILE.get(name),
                "has_override": has_config_entry,
                "override_fields": sorted(override),
                "override": override,
                "error": _safe_error(str(exc)),
            }
            continue
        source = resolve_agent_config_source(path, name)
        summaries[name] = {
            "source": source,
            "source_label": source,
            "profile": profile_for_task(name),
            "has_override": has_config_entry and bool(override),
            "override_fields": sorted(override),
            "override": override,
            "config": _sanitize_config(resolved.model_dump(mode="json", exclude_none=True, exclude={"inherit_default"})),
            "parameter_capabilities": _parameter_capabilities_payload(resolved),
        }
    return summaries


def _state_timeline_summary(root: Path) -> dict[str, object]:
    _require_workspace(root)
    state = _safe_json(root / "memory" / "state" / "current_state.json")
    timeline = _safe_json(root / "memory" / "state" / "timeline.json")
    canon = {
        "characters": _safe_json(root / "memory" / "canon" / "characters.json"),
        "locations": _safe_json(root / "memory" / "canon" / "locations.json"),
        "items": _safe_json(root / "memory" / "canon" / "items.json"),
    }
    visual = _state_timeline_visual_summary(state, timeline, canon)
    return {
        "state": state,
        "timeline": timeline,
        "visual": visual,
        "summary": {
            "character_state_count": len(state.get("character_states", [])) if isinstance(state, dict) else 0,
            "item_state_count": len(state.get("item_states", [])) if isinstance(state, dict) else 0,
            "location_state_count": len(state.get("location_states", [])) if isinstance(state, dict) else 0,
            "timeline_event_count": len(timeline.get("events", [])) if isinstance(timeline, dict) else 0,
        },
    }


def _audit_annotations(root: Path, query: dict[str, str]) -> dict[str, object]:
    _require_workspace(root)
    chapter_number = int(query.get("chapter", "0"))
    audited_file = query.get("file") or "polished.md"
    if chapter_number < 1 or audited_file not in {"draft.md", "polished.md"}:
        raise ValueError("invalid chapter or audited file")
    chapter_dir = root / "memory" / "chapters" / f"{chapter_number:03d}"
    audit_path = chapter_dir / "audit.json"
    text_path = chapter_dir / audited_file
    if not audit_path.exists():
        raise FileNotFoundError("audit.json does not exist")
    if not text_path.exists():
        raise FileNotFoundError(f"{audited_file} does not exist")
    report = load_json_model(audit_path, AuditReport)
    content = text_path.read_text(encoding="utf-8")
    issues = []
    for issue in report.issues:
        matches = []
        for evidence in issue.evidence:
            quote = evidence.quote.strip()
            location = _locate_quote(content, quote)
            matches.append(
                {
                    "source": evidence.source,
                    "quote": quote,
                    "matched": location is not None,
                    **(location or {}),
                }
            )
        localized = localize_audit_issue_for_author(issue)
        issues.append(
            {
                "id": localized.id,
                "severity": localized.severity,
                "type": localized.type,
                "description": localized.description,
                "suggested_fix": localized.suggested_fix,
                "matches": matches,
            }
        )
    return {
        "audit_path": _relative(root, audit_path),
        "audited_file": audited_file,
        "issues": issues,
    }


def _workspace_diff(root: Path, left: str, right: str) -> dict[str, object]:
    left_path = _safe_workspace_file(root, left)
    right_path = _safe_workspace_file(root, right)
    if not left_path.exists() or not right_path.exists():
        raise FileNotFoundError("both diff files must exist")
    left_lines = left_path.read_text(encoding="utf-8").splitlines(keepends=True)
    right_lines = right_path.read_text(encoding="utf-8").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=_relative(root, left_path),
            tofile=_relative(root, right_path),
        )
    )
    return {"left": _relative(root, left_path), "right": _relative(root, right_path), "diff": diff}


def _list_chapters(root: Path) -> list[dict[str, object]]:
    chapters_dir = root / "memory" / "chapters"
    chapters: list[dict[str, object]] = []
    if not chapters_dir.exists():
        return chapters
    for child in sorted(chapters_dir.iterdir()):
        if not child.is_dir() or not child.name.isdigit():
            continue
        chapter_number = int(child.name)
        entry: dict[str, object] = {
            "chapter_number": chapter_number,
            "has_plan": (child / "plan.json").exists(),
            "has_draft": (child / "draft.md").exists(),
            "has_polished": (child / "polished.md").exists(),
            "has_audit": (child / "audit.json").exists(),
            "has_chapter_memory": (child / "chapter_memory.json").exists(),
            "status": None,
            "title": None,
            "audit_status": None,
            "chapter_memory_stale": None,
        }
        _merge_plan_metadata(child / "plan.json", entry)
        _merge_polished_metadata(child / "polished.md", entry)
        if (child / "audit.json").exists():
            data = load_json(child / "audit.json")
            if isinstance(data, dict):
                entry["audit_status"] = data.get("overall_status")
        if (child / "chapter_memory.json").exists():
            try:
                memory = load_json_model(child / "chapter_memory.json", ChapterMemory)
                entry["chapter_memory_stale"] = bool(chapter_memory_freshness_warnings(root, memory))
            except Exception:
                entry["chapter_memory_stale"] = True
        chapters.append(entry)
    return chapters


def _merge_plan_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    try:
        plan = load_json_model(path, ChapterPlan)
    except Exception:
        return
    entry["title"] = plan.title


def _read_chapter_file(root: Path, query: dict[str, str]) -> dict[str, object]:
    chapter_number = int(query.get("chapter", "0"))
    file_type = query.get("file", "")
    mapping = {
        "plan": "plan.json",
        "draft": "draft.md",
        "polished": "polished.md",
        "audit": "audit.json",
        "chapter_memory": "chapter_memory.json",
    }
    if chapter_number < 1 or file_type not in mapping:
        raise ValueError("invalid chapter or file type")
    rel_path = f"memory/chapters/{chapter_number:03d}/{mapping[file_type]}"
    path = root / rel_path
    if not path.exists():
        return {"path": str(path), "relative_path": rel_path, "content": "", "exists": False}
    return {
        "path": str(path),
        "relative_path": rel_path,
        "content": path.read_text(encoding="utf-8"),
        "exists": True,
    }


def _merge_polished_metadata(path: Path, entry: dict[str, object]) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return
    try:
        _, metadata_text, _ = content.split("---\n", 2)
        metadata = yaml.safe_load(metadata_text) or {}
    except Exception:
        return
    if isinstance(metadata, dict):
        entry["status"] = metadata.get("status")
        entry["title"] = metadata.get("title")


def _provider_call_summary(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-50:]
    calls: list[dict[str, object]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            calls.append(
                {
                    "request_id": data.get("request_id"),
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "endpoint": data.get("endpoint"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "duration_ms": data.get("duration_ms"),
                    "attempt_count": data.get("attempt_count"),
                    "error_type": data.get("error_type"),
                    "http_status": data.get("http_status"),
                    "model_io_path": data.get("model_io_path"),
                }
            )
    return calls


def _state_timeline_visual_summary(state: object, timeline: object, canon: dict[str, object]) -> dict[str, object]:
    character_names = _name_map(canon.get("characters"), "characters")
    location_names = _name_map(canon.get("locations"), "locations")
    item_names = _name_map(canon.get("items"), "items")
    characters = []
    items = []
    locations = []
    conflicts = []
    if isinstance(state, dict):
        for character in state.get("character_states", []):
            if not isinstance(character, dict):
                continue
            entity_id = str(character.get("entity_id") or "")
            characters.append(
                {
                    "id": entity_id,
                    "name": character_names.get(entity_id, entity_id),
                    "location_id": character.get("location_id"),
                    "location_name": location_names.get(str(character.get("location_id") or ""), character.get("location_id")),
                    "health": character.get("health"),
                    "possessions": character.get("possessions") or [],
                    "knowledge_count": len(character.get("knowledge", [])) if isinstance(character.get("knowledge"), list) else 0,
                }
            )
        possession_owner: dict[str, str] = {}
        for character in state.get("character_states", []):
            if not isinstance(character, dict):
                continue
            for item_id in character.get("possessions", []) if isinstance(character.get("possessions"), list) else []:
                if item_id in possession_owner and possession_owner[item_id] != character.get("entity_id"):
                    conflicts.append(f"item {item_id} appears in possessions of multiple characters")
                possession_owner[str(item_id)] = str(character.get("entity_id") or "")
        for item in state.get("item_states", []):
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            holder_id = str(item.get("holder_id") or "")
            location_id = str(item.get("location_id") or "")
            if holder_id and location_id:
                conflicts.append(f"item {entity_id} has both holder and location")
            if holder_id and possession_owner.get(entity_id) and possession_owner[entity_id] != holder_id:
                conflicts.append(f"item {entity_id} holder conflicts with character possessions")
            items.append(
                {
                    "id": entity_id,
                    "name": item_names.get(entity_id, entity_id),
                    "holder_id": holder_id or None,
                    "holder_name": character_names.get(holder_id, holder_id) if holder_id else None,
                    "location_id": location_id or None,
                    "location_name": location_names.get(location_id, location_id) if location_id else None,
                    "condition": item.get("condition"),
                }
            )
        for location in state.get("location_states", []):
            if not isinstance(location, dict):
                continue
            entity_id = str(location.get("entity_id") or "")
            locations.append(
                {
                    "id": entity_id,
                    "name": location_names.get(entity_id, entity_id),
                    "accessibility": location.get("accessibility"),
                    "condition": location.get("condition"),
                    "active_events": location.get("active_events") or [],
                }
            )
    events = []
    by_chapter: dict[str, list[dict[str, object]]] = {}
    edges = []
    if isinstance(timeline, dict):
        for event in timeline.get("events", []):
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            narrative = event.get("narrative_position") if isinstance(event.get("narrative_position"), dict) else {}
            story = event.get("story_position") if isinstance(event.get("story_position"), dict) else {}
            chapter = narrative.get("chapter")
            chapter_label = f"第 {chapter} 章" if chapter else "背景（未揭示）"
            chapter_group = str(chapter) if chapter else "background"
            entry = {
                "id": event_id,
                "chapter": chapter,
                "chapter_label": chapter_label,
                "chapter_group": chapter_group,
                "scene": narrative.get("scene"),
                "sequence": narrative.get("sequence"),
                "story_time": story.get("time_label"),
                "story_order": story.get("order"),
                "story_thread_id": story.get("thread_id"),
                "event_role": event.get("event_role"),
                "summary": event.get("summary"),
                "location_id": event.get("location_id"),
                "location_name": location_names.get(str(event.get("location_id") or ""), event.get("location_id")),
                "participant_ids": event.get("participant_ids") or [],
                "participant_names": [
                    character_names.get(str(item), str(item))
                    for item in event.get("participant_ids", [])
                    if isinstance(event.get("participant_ids"), list)
                ],
            }
            events.append(entry)
            by_chapter.setdefault(chapter_group, []).append(entry)
            for cause in event.get("causes", []) if isinstance(event.get("causes"), list) else []:
                edges.append({"from": cause, "to": event_id, "type": "cause"})
            for effect in event.get("effects", []) if isinstance(event.get("effects"), list) else []:
                edges.append({"from": event_id, "to": effect, "type": "effect"})
    return {
        "characters": characters,
        "items": items,
        "locations": locations,
        "timeline_by_chapter": by_chapter,
        "timeline_events": events,
        "timeline_edges": edges,
        "conflicts": conflicts,
    }


def _name_map(data: object, key: str) -> dict[str, str]:
    if not isinstance(data, dict):
        return {}
    values = data.get(key)
    if not isinstance(values, list):
        return {}
    result: dict[str, str] = {}
    for item in values:
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = str(item.get("name") or item["id"])
    return result


def _model_io_summary(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-50:]
    logs: list[dict[str, object]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            logs.append(
                {
                    "request_id": data.get("request_id"),
                    "agent_name": data.get("agent_name"),
                    "provider": data.get("provider"),
                    "model": data.get("model"),
                    "status": data.get("status"),
                    "started_at": data.get("started_at"),
                    "ended_at": data.get("ended_at"),
                    "stream": data.get("stream"),
                    "json_schema_name": data.get("json_schema_name"),
                    "model_io_path": data.get("model_io_path"),
                }
            )
    return logs
