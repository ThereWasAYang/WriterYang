from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def new_request_id(prefix: str) -> str:
    return f"{prefix}_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
