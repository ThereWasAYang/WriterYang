from __future__ import annotations

from pathlib import Path

from novel.cli import build_parser


DEVELOPER_DOCS = (
    "docs/DEVELOPER_GUIDE.md",
    "docs/CODEBASE_REFERENCE.md",
    "docs/AGENT_PROMPT_ASSEMBLY.md",
    "docs/DEBUGGING_AND_REFACTORING.md",
)


def test_readme_links_developer_docs() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for rel_path in DEVELOPER_DOCS:
        assert rel_path in readme


def test_codebase_reference_covers_python_entrypoints_and_core_modules() -> None:
    reference = Path("docs/CODEBASE_REFERENCE.md").read_text(encoding="utf-8")
    expected_paths = [
        "src/novel/cli.py",
        "src/novel/web_api.py",
        "src/novel/web_server.py",
        "src/novel/web_static/index.html",
        "src/novel/web_static/app.css",
        "src/novel/web_static/app.js",
        *[
            str(path)
            for path in sorted(Path("src/novel/core").glob("*.py"))
            if path.name != "__init__.py"
        ],
    ]

    for rel_path in expected_paths:
        assert rel_path in reference


def test_agent_prompt_assembly_covers_agents_and_prompt_files() -> None:
    doc = Path("docs/AGENT_PROMPT_ASSEMBLY.md").read_text(encoding="utf-8")
    expected_agents = (
        "Inspiration Agent",
        "Canon Agent",
        "Plot / Chapter Planning Agent",
        "Writer Agent",
        "Polish Agent",
        "Audit Agent",
        "State Update Agent",
        "Revision Agent",
        "Orchestrator",
        "Creation Session",
    )
    expected_prompt_files = (
        "prompts/inspiration_system.txt",
        "prompts/canon_system.txt",
        "prompts/planning_system.txt",
        "prompts/writer_system.txt",
        "prompts/polish_system.txt",
        "prompts/audit_system.txt",
        "prompts/state_update_system.txt",
        "prompts/revision_system.txt",
    )

    for phrase in (*expected_agents, *expected_prompt_files):
        assert phrase in doc


def test_developer_docs_reference_existing_core_commands() -> None:
    parser_help = build_parser().format_help()
    combined_docs = "\n".join(Path(path).read_text(encoding="utf-8") for path in DEVELOPER_DOCS)

    for command in (
        "novel validate",
        "novel doctor",
        "novel usage",
        "novel session",
        "novel plan-chapter",
        "novel write-chapter",
        "novel polish-chapter",
        "novel audit-chapter",
        "novel propose-state-update",
        "novel apply-state-update",
        "novel export markdown",
    ):
        assert command in combined_docs
        assert command.split()[1] in parser_help


def test_developer_docs_cover_memory_search_errors_and_skill_loading() -> None:
    combined_docs = "\n".join(Path(path).read_text(encoding="utf-8") for path in DEVELOPER_DOCS)

    for phrase in (
        "记忆分层",
        "memory/search_index_manifest.json",
        "FTS",
        "真实 embedding",
        "/api/search",
        "/api/migration-status",
        "/api/migrate",
        "/api/usage",
        "_get_routes()",
        "_post_routes()",
        "--strict-mypy",
        "渐进式披露",
        "Agent 输出不合约",
        "ProviderError",
        "EmbeddingError",
        "ContextBundle.render_for_prompt()",
        "narrative_position",
        "story_position",
    ):
        assert phrase in combined_docs
