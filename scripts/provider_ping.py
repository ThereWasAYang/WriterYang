#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Sequence


DEFAULT_AGENTS = ("orchestrator", "inspiration", "canon", "plot", "writer", "polish", "audit", "state_update")

if TYPE_CHECKING:
    from novel.core.provider_config import ProviderOverrides


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely inspect or ping WriterYang model providers.")
    parser.add_argument("--project", required=True, help="Novel workspace path.")
    parser.add_argument("--env-file", default=None, help="Optional .env file to load into this process.")
    parser.add_argument("--agent", action="append", help="Agent name to check. Defaults to known agents in config.")
    parser.add_argument("--provider", default="config", help="Provider override passed through ProviderFactory.")
    parser.add_argument("--model", default=None, help="Temporary model override.")
    parser.add_argument("--allow-network", action="store_true", help="Actually call non-mock providers.")
    parser.add_argument("--embedding-provider", action="append", help="Embedding provider to check, e.g. config/local_hash.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    args = parser.parse_args(argv)

    root = Path(args.project).expanduser().resolve()
    if args.env_file:
        _load_env_file(Path(args.env_file).expanduser().resolve())
    agents = _agent_names(root, args.agent)
    from novel.core.provider_config import ProviderOverrides

    overrides = ProviderOverrides(provider_name=args.provider, model_name=args.model)

    agent_results = [_check_agent(root, agent, overrides=overrides, allow_network=args.allow_network) for agent in agents]
    embedding_results = [
        _check_embedding(root, provider_name=name, allow_network=args.allow_network)
        for name in (args.embedding_provider or [])
    ]
    ok = all(item["ok"] or item["status"] == "skipped" for item in [*agent_results, *embedding_results])
    payload = {
        "ok": ok,
        "project": str(root),
        "allow_network": args.allow_network,
        "generated_at": _utc_now(),
        "agents": agent_results,
        "embeddings": embedding_results,
    }
    _print(payload, args.json)
    return 0 if ok else 1


def _agent_names(root: Path, requested: list[str] | None) -> list[str]:
    if requested:
        return requested
    config_path = root / "config" / "agents.yaml"
    if not config_path.exists():
        return list(DEFAULT_AGENTS)
    from novel.core.provider_config import load_agents_config

    config = load_agents_config(config_path)
    configured = [name for name in DEFAULT_AGENTS if name in config.agents]
    return configured or sorted(config.agents)


def _check_agent(root: Path, agent: str, *, overrides: ProviderOverrides, allow_network: bool) -> dict[str, object]:
    config_path = root / "config" / "agents.yaml"
    try:
        from novel.core.provider_config import create_agent_provider, describe_agent_provider
        from novel.core.providers import ModelRequest

        descriptor = describe_agent_provider(config_path, agent, overrides=overrides)
        result: dict[str, object] = {
            "agent": agent,
            "provider": descriptor.provider,
            "model": descriptor.model,
            "api_key_env": descriptor.api_key_env,
            "api_key_env_set": bool(os.environ.get(descriptor.api_key_env)),
            "base_url_env": descriptor.base_url_env,
            "base_url_env_set": bool(os.environ.get(descriptor.base_url_env)) if descriptor.base_url_env else None,
            "thinking": descriptor.thinking,
            "status": "skipped",
            "ok": True,
        }
        if descriptor.provider != "mock" and not allow_network:
            result["message"] = "network call skipped; pass --allow-network to ping real provider"
            return result
        provider = create_agent_provider(config_path, agent, overrides=overrides, mock_response="OK")
        response = provider.generate(
            ModelRequest(
                system_prompt="You are a WriterYang provider health check. Return exactly OK.",
                user_prompt="Return OK.",
                request_id=f"provider_ping_{agent}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}",
            )
        )
        result.update(
            {
                "status": "success",
                "ok": True,
                "content_preview": response.content[:80],
                "token_usage": response.token_usage.__dict__ if response.token_usage else None,
            }
        )
        return result
    except Exception as exc:
        return {
            "agent": agent,
            "status": "failed",
            "ok": False,
            "error_type": exc.__class__.__name__,
            "error": _redact(str(exc)),
        }


def _check_embedding(root: Path, *, provider_name: str, allow_network: bool) -> dict[str, object]:
    try:
        from novel.core.embeddings import create_embedding_provider

        provider = create_embedding_provider(root, provider_name=provider_name)
        result: dict[str, object] = {
            "provider": provider.provider_name,
            "model": provider.model,
            "status": "skipped",
            "ok": True,
        }
        if provider.provider_name != "local_hash" and not allow_network:
            result["message"] = "network call skipped; pass --allow-network to ping real embedding provider"
            return result
        response = provider.embed_texts(["WriterYang embedding provider ping"])
        result.update(
            {
                "status": "success",
                "ok": True,
                "vector_count": len(response.vectors),
                "dimension": len(response.vectors[0]) if response.vectors else 0,
            }
        )
        return result
    except Exception as exc:
        return {
            "provider": provider_name,
            "status": "failed",
            "ok": False,
            "error_type": exc.__class__.__name__,
            "error": _redact(str(exc)),
        }


def _load_env_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ[key] = value


def _redact(text: str) -> str:
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[redacted-api-key]", text)
    for key, value in os.environ.items():
        if value and ("KEY" in key or "TOKEN" in key or "SECRET" in key):
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def _print(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"Provider ping {'passed' if payload['ok'] else 'failed'}")
    for item in payload["agents"]:  # type: ignore[index]
        print(f"- {item['agent']}: {item['status']} ({item.get('provider')}/{item.get('model')})")
    for item in payload["embeddings"]:  # type: ignore[index]
        print(f"- embedding {item['provider']}: {item['status']}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
