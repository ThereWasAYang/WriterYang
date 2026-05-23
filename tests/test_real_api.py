from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from novel.core.canon import apply_canon_proposal, default_mock_canon_proposal_json
from novel.core.planning import ChapterPlanningOptions, plan_chapter
from novel.core.providers import ModelRequest, ProviderFactory
from novel.core.schemas import AgentConfig
from novel.core.workspace import InitOptions, init_workspace


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_REAL_ENV = (
    "WRITERYANG_REAL_BASE_URL",
    "WRITERYANG_REAL_API_KEY",
    "WRITERYANG_REAL_MODEL",
)
DEEPSEEK_ENV = (
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_V4PRO_MODEL",
)
ZAI_ENV = (
    "ZAI_BASE_URL",
    "ZAI_API_KEY",
    "ZAI_MODEL",
)


pytestmark = pytest.mark.real_api


def test_real_provider_smoke() -> None:
    env = _real_env_or_skip()
    provider = ProviderFactory(env=env).create(
        AgentConfig(
            provider=env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
            base_url_env="WRITERYANG_REAL_BASE_URL",
            api_key_env="WRITERYANG_REAL_API_KEY",
            model=env["WRITERYANG_REAL_MODEL"],
            thinking={"type": "disabled"},
            temperature=0,
            timeout_seconds=60,
            max_retries=1,
        )
    )

    response = provider.generate(
        ModelRequest(
            system_prompt="你是一个连接测试助手。请严格只回复 OK。",
            user_prompt="回复 OK",
        )
    )

    assert response.content.strip()


def test_real_plan_chapter_smoke(tmp_path: Path) -> None:
    env = _real_env_or_skip()
    root = _real_project(tmp_path, env)
    provider = ProviderFactory(env=env).create(
        AgentConfig(
            provider=env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
            base_url_env="WRITERYANG_REAL_BASE_URL",
            api_key_env="WRITERYANG_REAL_API_KEY",
            model=env["WRITERYANG_REAL_MODEL"],
            thinking={"type": "disabled"},
            temperature=0.2,
            timeout_seconds=120,
            max_retries=1,
        )
    )

    result = plan_chapter(
        ChapterPlanningOptions(
            root=root,
            chapter_number=1,
            instruction="真实 API 冒烟测试：只生成一个短小、保守、符合 schema 的章节计划。",
        ),
        provider,
    )

    assert result.plan.chapter_number == 1
    assert result.plan_json_path.is_file()
    assert result.plan_markdown_path.is_file()


def _real_env_or_skip() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_read_env_file(ROOT / ".env.real"))
    if not all(env.get(name) for name in REQUIRED_REAL_ENV) and all(env.get(name) for name in DEEPSEEK_ENV):
        env["WRITERYANG_REAL_BASE_URL"] = env["DEEPSEEK_BASE_URL"]
        env["WRITERYANG_REAL_API_KEY"] = env["DEEPSEEK_API_KEY"]
        env["WRITERYANG_REAL_MODEL"] = env["DEEPSEEK_V4PRO_MODEL"]
        env["WRITERYANG_REAL_PROVIDER"] = "deepseek"
    if not all(env.get(name) for name in REQUIRED_REAL_ENV) and all(env.get(name) for name in ZAI_ENV):
        env["WRITERYANG_REAL_BASE_URL"] = env["ZAI_BASE_URL"]
        env["WRITERYANG_REAL_API_KEY"] = env["ZAI_API_KEY"]
        env["WRITERYANG_REAL_MODEL"] = env["ZAI_MODEL"]
        env["WRITERYANG_REAL_PROVIDER"] = "zai"
    missing = [name for name in REQUIRED_REAL_ENV if not env.get(name)]
    if missing:
        pytest.skip(f"missing real API environment variables: {', '.join(missing)}")
    return env


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip("'").strip('"')
        if name:
            values[name] = value
    return values


def _real_project(tmp_path: Path, env: dict[str, str]) -> Path:
    root = tmp_path / "real_project"
    init_workspace(InitOptions(title="雨夜旧车站", root=root))
    (root / "memory" / "inspiration.md").write_text(
        "# 灵感\n\n雨夜旧车站里传来已经停播多年的广播声，主角循声进入候车厅。\n",
        encoding="utf-8",
    )
    proposal_path = tmp_path / "canon_proposal.json"
    proposal_path.write_text(default_mock_canon_proposal_json(), encoding="utf-8")
    assert apply_canon_proposal(root, proposal_path).validation_report.ok
    _write_real_agents_config(root / "config" / "agents.yaml", env)
    return root


def _write_real_agents_config(path: Path, env: dict[str, str]) -> None:
    data = {
        "agents": {
            "plot": {
                "provider": env.get("WRITERYANG_REAL_PROVIDER", "openai_compatible"),
                "base_url_env": "WRITERYANG_REAL_BASE_URL",
                "api_key_env": "WRITERYANG_REAL_API_KEY",
                "model": env["WRITERYANG_REAL_MODEL"],
                "reasoning": "low",
                "thinking": {"type": "disabled"},
                "max_context_tokens": 64000,
                "temperature": 0.2,
                "timeout_seconds": 120,
                "max_retries": 1,
            }
        }
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
