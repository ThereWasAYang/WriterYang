from __future__ import annotations

from pathlib import Path

from novel.core import web_launcher
from novel.core.artifact_store import resolve_project_path
from novel.core.canon import (
    CanonSuggestOptions,
    apply_canon_proposal,
    load_canon_provider,
    suggest_canon,
)
from novel.core.chapter_memory import (
    ChapterMemoryOptions,
    accepted_chapter_numbers,
    chapter_memory_freshness_warnings,
    chapter_memory_path,
    generate_chapter_memory,
    load_chapter_memory_provider,
)
from novel.core.command_bus import DomainError, _handler, _result
from novel.core.config_mutations import update_agent_config
from novel.core.contracts import (
    AgentConfigUpdateCommand,
    CanonApplyCommand,
    CanonSuggestCommand,
    ChapterCandidateSaveCommand,
    ChapterMemoryGenerateCommand,
    ChapterMemoryRebuildCommand,
    CommandEnvelope,
    CommandResult,
    DefaultProviderSetupCommand,
    EmbeddingProviderSetupCommand,
    IndexUpdateCommand,
    InspirationGenerateCommand,
    ProjectWebPortSetupCommand,
    SearchCommand,
    StyleGuideGenerateCommand,
    StyleGuideSaveCommand,
    Surface,
    WebLauncherConfigCommand,
)
from novel.core.inspiration import (
    InspirationOptions,
    load_inspiration_provider,
    run_inspiration_agent,
)
from novel.core.io import load_json_model
from novel.core.providers import ModelProvider
from novel.core.schemas import ChapterMemory
from novel.core.search import (
    rebuild_search_index,
    refresh_search_index,
    search_index_status,
    search_project,
)
from novel.core.security import redact_secret_text
from novel.core.setup_guide import (
    configure_default_provider,
    configure_embedding_provider,
    configure_web_port,
)
from novel.core.style_guide import (
    StyleGuideGenerationOptions,
    generate_style_guide,
    load_style_guide_provider,
)
from novel.core.workspace import (
    is_default_inspiration_placeholder,
)
from novel.core.workspace_mutations import (
    STYLE_GUIDE_RELATIVE_PATH,
    save_chapter_candidate,
    save_style_guide,
)


@_handler("search")
def _handle_search(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, SearchCommand):
        raise DomainError("invalid_command", "search payload type mismatch")
    results = search_project(
        root,
        command.query,
        search_type=command.search_type,
        limit=command.limit,
        chapter_number=command.chapter_number,
        highlight=command.highlight,
        use_vector=command.use_vector,
        embedding_provider_name=command.embedding_provider_name,
        embedding_config_path=(Path(command.embedding_config_path) if command.embedding_config_path else None),
    )
    return _result(
        envelope,
        result={
            "query": command.query,
            "results": [
                {
                    "id": item.id,
                    "type": item.type,
                    "path": item.path,
                    "title": item.title,
                    "score": item.score,
                    "matched_terms": list(item.matched_terms),
                    "excerpt": item.excerpt,
                    "highlighted_excerpt": item.highlighted_excerpt,
                    "metadata": item.metadata,
                }
                for item in results
            ],
        },
    )

@_handler("inspiration.generate")
def _handle_inspiration_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, InspirationGenerateCommand):
        raise DomainError("invalid_command", "inspiration.generate payload type mismatch")
    provider = load_inspiration_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = run_inspiration_agent(
        InspirationOptions(
            root=root,
            source_text=command.source_text,
            source_type=command.source_type,
            write_json=command.write_json,
            overwrite=command.overwrite
            or (
                command.allow_default_placeholder
                and is_default_inspiration_placeholder(root / "memory" / "inspiration.md")
            ),
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
        ),
        provider,
    )
    changed_paths = [value.markdown_path.relative_to(root).as_posix()]
    if value.json_path:
        changed_paths.append(value.json_path.relative_to(root).as_posix())
    return _result(
        envelope,
        result={
            "markdown_path": str(value.markdown_path),
            "json_path": str(value.json_path) if value.json_path else None,
            "context_report_path": str(value.context_report_path) if value.context_report_path else None,
        },
        changed_paths=changed_paths,
    )


@_handler("canon.suggest")
def _handle_canon_suggest(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, CanonSuggestCommand):
        raise DomainError("invalid_command", "canon.suggest payload type mismatch")
    output_path = _resolve_command_file(envelope, root, command.output_path) if command.output_path else None
    provider = load_canon_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = suggest_canon(
        CanonSuggestOptions(
            root=root,
            output_path=output_path,
            use_search_context=command.use_search_context,
            use_vector_context=command.use_vector_context,
        ),
        provider,
    )
    return _result(
        envelope,
        result={
            "output_path": str(value.output_path) if value.output_path else None,
            "relative_path": _changed_path(root, value.output_path) if value.output_path else None,
            "proposal": value.proposal.model_dump(mode="json"),
            "proposal_json": value.proposal_json,
            "context_report_path": str(value.context_report_path) if value.context_report_path else None,
        },
        next_allowed_commands=["canon.apply"] if value.output_path else [],
        changed_paths=[_changed_path(root, value.output_path)] if value.output_path else [],
    )


@_handler("canon.apply")
def _handle_canon_apply(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, CanonApplyCommand):
        raise DomainError("invalid_command", "canon.apply payload type mismatch")
    proposal_path = _resolve_command_file(envelope, root, command.proposal_path)
    value = apply_canon_proposal(root, proposal_path)
    changed_paths = [
        value.apply_log_path.relative_to(root).as_posix(),
        value.proposal_snapshot_path.relative_to(root).as_posix(),
        *value.apply_log.target_files,
    ]
    return _result(
        envelope,
        result={
            "proposal_path": str(proposal_path),
            "apply_log": value.apply_log.model_dump(mode="json"),
            "apply_log_path": str(value.apply_log_path),
            "apply_log_relative_path": value.apply_log_path.relative_to(root).as_posix(),
            "proposal_snapshot_path": str(value.proposal_snapshot_path),
            "proposal_snapshot_relative_path": value.proposal_snapshot_path.relative_to(root).as_posix(),
            "validation_ok": value.validation_report.ok,
            "errors": [message.message for message in value.validation_report.errors],
            "warnings": [message.message for message in value.validation_report.warnings],
        },
        warnings=[message.message for message in value.validation_report.warnings],
        changed_paths=list(dict.fromkeys(changed_paths)),
    )


def _resolve_command_file(envelope: CommandEnvelope, root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
        if envelope.surface != Surface.CLI and not resolved.is_relative_to(root):
            raise DomainError("forbidden_file", "command path escapes project root", recoverable=True)
        return resolved
    return resolve_project_path(root, value)


def _changed_path(root: Path, path: Path) -> str:
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix() if resolved.is_relative_to(root) else str(resolved)


@_handler("chapter_memory.generate")
def _handle_chapter_memory_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterMemoryGenerateCommand):
        raise DomainError("invalid_command", "chapter_memory.generate payload type mismatch")
    provider, provider_warnings = _chapter_memory_provider(root, command, command.chapter_number)
    value = generate_chapter_memory(
        ChapterMemoryOptions(
            root=root,
            chapter_number=command.chapter_number,
            force=command.force,
        ),
        provider,
        initial_warnings=tuple(provider_warnings),
    )
    return _result(
        envelope,
        result={
            "chapter_number": value.memory.chapter_number,
            "memory_path": str(value.memory_path),
            "relative_path": value.memory_path.relative_to(root).as_posix(),
            "generation_status": value.memory.generation_status,
            "warnings": list(value.warnings),
        },
        warnings=list(value.warnings),
        changed_paths=[value.memory_path.relative_to(root).as_posix()],
    )


@_handler("chapter_memory.rebuild")
def _handle_chapter_memory_rebuild(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterMemoryRebuildCommand):
        raise DomainError("invalid_command", "chapter_memory.rebuild payload type mismatch")
    written: list[dict[str, object]] = []
    skipped: list[int] = []
    warnings: list[str] = []
    changed_paths: list[str] = []
    for chapter_number in accepted_chapter_numbers(root):
        path = chapter_memory_path(root, chapter_number)
        should_generate = command.mode == "all" or not path.exists()
        if not should_generate and command.mode == "missing_or_stale":
            try:
                should_generate = bool(chapter_memory_freshness_warnings(root, load_json_model(path, ChapterMemory)))
            except Exception:
                should_generate = True
        if not should_generate:
            skipped.append(chapter_number)
            continue
        try:
            provider, provider_warnings = _chapter_memory_provider(root, command, chapter_number)
            value = generate_chapter_memory(
                ChapterMemoryOptions(root=root, chapter_number=chapter_number, force=True),
                provider,
                initial_warnings=tuple(provider_warnings),
            )
            relative_path = value.memory_path.relative_to(root).as_posix()
            written.append(
                {
                    "chapter_number": value.memory.chapter_number,
                    "memory_path": str(value.memory_path),
                    "relative_path": relative_path,
                    "generation_status": value.memory.generation_status,
                    "warnings": list(value.warnings),
                }
            )
            changed_paths.append(relative_path)
            warnings.extend(f"chapter {chapter_number}: {warning}" for warning in value.warnings)
        except Exception as exc:
            warnings.append(f"chapter {chapter_number}: {redact_secret_text(str(exc))}")
    return _result(
        envelope,
        result={"mode": command.mode, "written": written, "skipped": skipped, "warnings": warnings},
        warnings=warnings,
        changed_paths=changed_paths,
    )


def _chapter_memory_provider(
    root: Path,
    command: ChapterMemoryGenerateCommand | ChapterMemoryRebuildCommand,
    chapter_number: int,
) -> tuple[ModelProvider | None, list[str]]:
    try:
        return (
            load_chapter_memory_provider(
                root,
                command.provider_name,
                chapter_number=chapter_number,
                agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
                model_name=command.model_name,
            ),
            [],
        )
    except Exception as exc:
        return None, [
            "chapter memory provider unavailable; using deterministic fallback: "
            f"{redact_secret_text(str(exc))}"
        ]


@_handler("index.rebuild", "index.refresh")
def _handle_index_update(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, IndexUpdateCommand):
        raise DomainError("invalid_command", "index update payload type mismatch")
    embedding_config_path = Path(command.embedding_config_path) if command.embedding_config_path else None
    if command.type == "index.rebuild":
        value = rebuild_search_index(
            root,
            embedding_provider_name=command.embedding_provider_name,
            embedding_config_path=embedding_config_path,
            with_embeddings=command.with_embeddings,
        )
        counts: dict[str, object] = {}
    else:
        value = refresh_search_index(
            root,
            embedding_provider_name=command.embedding_provider_name,
            embedding_config_path=embedding_config_path,
            with_embeddings=command.with_embeddings,
        )
        counts = {
            "refreshed_count": value.refreshed_count,
            "deleted_count": value.deleted_count,
        }
    changed_paths = [
        value.index_path.relative_to(root).as_posix(),
        value.sqlite_path.relative_to(root).as_posix(),
        value.manifest_path.relative_to(root).as_posix(),
    ]
    return _result(
        envelope,
        result={
            "index_path": str(value.index_path),
            "sqlite_path": str(value.sqlite_path),
            "manifest_path": str(value.manifest_path),
            "document_count": value.document_count,
            "embedding_document_count": value.embedding_document_count,
            "with_embeddings": value.with_embeddings,
            **counts,
            "search": search_index_status(root).as_dict(),
        },
        changed_paths=changed_paths,
    )


@_handler("style_guide.save")
def _handle_style_guide_save(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, StyleGuideSaveCommand):
        raise DomainError("invalid_command", "style_guide.save payload type mismatch")
    value = save_style_guide(root, command.content)
    return _result(
        envelope,
        result={
            "path": STYLE_GUIDE_RELATIVE_PATH,
            "backup_path": value.backup_path.relative_to(root).as_posix() if value.backup_path else None,
            "content": value.content,
        },
        changed_paths=[STYLE_GUIDE_RELATIVE_PATH],
    )


@_handler("style_guide.generate")
def _handle_style_guide_generate(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, StyleGuideGenerateCommand):
        raise DomainError("invalid_command", "style_guide.generate payload type mismatch")
    provider = load_style_guide_provider(
        root,
        command.provider_name,
        agent_config_path=Path(command.agent_config_path) if command.agent_config_path else None,
        model_name=command.model_name,
    )
    value = generate_style_guide(
        StyleGuideGenerationOptions(
            root=root,
            instruction=command.instruction,
            include_project_context=command.include_project_context,
            include_existing_style=command.include_existing_style,
        ),
        provider,
    )
    return _result(
        envelope,
        result={
            "path": STYLE_GUIDE_RELATIVE_PATH,
            "content": value.content,
            "warnings": list(value.warnings),
        },
        warnings=list(value.warnings),
    )


@_handler("chapter_candidate.save")
def _handle_chapter_candidate_save(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ChapterCandidateSaveCommand):
        raise DomainError("invalid_command", "chapter_candidate.save payload type mismatch")
    value = save_chapter_candidate(
        root,
        chapter_number=command.chapter_number,
        target=command.target,
        source_file=command.source_file,
        content=command.content,
        instruction=command.instruction,
    )
    output_path = value.output_path.relative_to(root).as_posix()
    log_path = value.revision_log_path.relative_to(root).as_posix()
    return _result(
        envelope,
        result={
            "output_path": str(value.output_path),
            "relative_path": output_path,
            "revision_log_path": str(value.revision_log_path),
            "record": value.record.model_dump(mode="json"),
        },
        changed_paths=[output_path, log_path],
    )


@_handler("agent_config.update")
def _handle_agent_config_update(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, AgentConfigUpdateCommand):
        raise DomainError("invalid_command", "agent_config.update payload type mismatch")
    value = update_agent_config(
        root,
        default_update=command.default,
        profiles_update=command.profiles,
        tasks_update=command.tasks,
        clear_profiles=command.clear_profiles,
        clear_tasks=command.clear_tasks,
    )
    config_path = value.path.relative_to(root).as_posix()
    return _result(
        envelope,
        result={
            "path": str(value.path),
            "backup_path": str(value.backup_path) if value.backup_path else None,
            "cleared_profiles": list(value.cleared_profiles),
            "cleared_tasks": list(value.cleared_tasks),
            "config_data": value.config.model_dump(mode="json", exclude_none=True),
        },
        changed_paths=[config_path],
    )


@_handler("setup.default_provider")
def _handle_default_provider_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, DefaultProviderSetupCommand):
        raise DomainError("invalid_command", "setup.default_provider payload type mismatch")
    value = configure_default_provider(
        root,
        provider=command.provider,
        base_url=command.base_url,
        api_key=command.api_key,
        model=command.model,
        max_context_tokens=command.max_context_tokens,
        max_tokens=command.max_tokens,
        timeout_seconds=command.timeout_seconds,
        max_retries=command.max_retries,
        ping=command.ping,
    )
    return _result(
        envelope,
        result={
            "config_path": str(value.config_path),
            "env_path": str(value.env_path),
            "provider": value.provider,
            "model": value.model,
            "api_key_env": value.api_key_env,
            "base_url_env": value.base_url_env,
            "ping_ok": value.ping_ok,
            "ping_message": value.ping_message,
        },
        changed_paths=[
            value.config_path.relative_to(root).as_posix(),
            value.env_path.relative_to(root).as_posix(),
        ],
    )


@_handler("setup.embedding_provider")
def _handle_embedding_provider_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, EmbeddingProviderSetupCommand):
        raise DomainError("invalid_command", "setup.embedding_provider payload type mismatch")
    if command.skip:
        return _result(
            envelope,
            result={"skipped": True, "message": "已跳过 embedding API 配置；关键词/FTS 检索仍可用。"},
        )
    value = configure_embedding_provider(
        root,
        provider=command.provider,
        provider_name=command.provider_name,
        base_url=command.base_url,
        api_key=command.api_key,
        model=command.model,
        dimensions=command.dimensions,
        batch_size=command.batch_size,
        timeout_seconds=command.timeout_seconds,
        max_retries=command.max_retries,
        ping=command.ping,
    )
    return _result(
        envelope,
        result={
            "config_path": str(value.config_path),
            "env_path": str(value.env_path),
            "active_provider": value.active_provider,
            "provider": value.provider,
            "model": value.model,
            "dimensions": value.dimensions,
            "batch_size": value.batch_size,
            "api_key_env": value.api_key_env,
            "base_url_env": value.base_url_env,
            "ping_ok": value.ping_ok,
            "ping_message": value.ping_message,
        },
        changed_paths=[
            value.config_path.relative_to(root).as_posix(),
            value.env_path.relative_to(root).as_posix(),
        ],
    )


@_handler("setup.project_web_port")
def _handle_project_web_port_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, ProjectWebPortSetupCommand):
        raise DomainError("invalid_command", "setup.project_web_port payload type mismatch")
    value = configure_web_port(root, requested_port=command.requested_port, host=command.host)
    return _result(
        envelope,
        result={
            "project_path": str(value.project_path),
            "host": value.host,
            "requested_port": value.requested_port,
            "selected_port": value.selected_port,
            "url": f"http://{value.host}:{value.selected_port}",
        },
        changed_paths=[value.project_path.relative_to(root).as_posix()],
    )


@_handler("setup.web_launcher")
def _handle_web_launcher_setup(envelope: CommandEnvelope, root: Path) -> CommandResult:
    command = envelope.command
    if not isinstance(command, WebLauncherConfigCommand):
        raise DomainError("invalid_command", "setup.web_launcher payload type mismatch")
    config_path = web_launcher.launcher_config_path_from_env()
    value = web_launcher.save_web_launcher_port_config(
        config_path,
        host=command.host,
        requested_port=command.requested_port,
        current_host=command.current_host,
        current_port=command.current_port,
    )
    launcher_path: Path | None = web_launcher.launcher_path_from_env()
    try:
        if launcher_path is not None:
            web_launcher.write_web_launcher_command(
                launcher_path,
                config_path=value.config_path,
                cwd=Path.cwd(),
            )
    except OSError:
        launcher_path = None
    return _result(
        envelope,
        result={
            "launcher_config_path": str(value.config_path),
            "launcher_path": str(launcher_path) if launcher_path else "",
            "host": value.host,
            "requested_port": value.requested_port,
            "selected_port": value.selected_port,
            "available": value.requested_port == value.selected_port,
            "url": value.url,
        },
        changed_paths=[str(value.config_path), *([str(launcher_path)] if launcher_path else [])],
    )
