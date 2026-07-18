from __future__ import annotations

import ipaddress


def is_loopback_host(host: str) -> bool:
    value = host.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def require_loopback_host(host: str) -> str:
    value = host.strip().strip("[]")
    if not is_loopback_host(value):
        raise ValueError(
            "WriterYang 当前仅支持可信本机访问；Web host 必须是 localhost、127.0.0.1 或 ::1"
        )
    return value


def url_host(host: str) -> str:
    value = require_loopback_host(host)
    return f"[{value}]" if ":" in value else value
