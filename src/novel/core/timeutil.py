from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def utc_now_precise() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat().replace("+00:00", "Z")


def new_request_id(prefix: str) -> str:
    return f"{prefix}_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")


def utc_timestamp(format_string: str = "%Y%m%d_%H%M%S_%f") -> str:
    return datetime.now(UTC).strftime(format_string)
