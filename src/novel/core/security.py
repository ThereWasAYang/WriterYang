from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

RAW_SECRET_PATTERNS = (
    re.compile(r"\bsk-proj-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]{20,}\b", re.IGNORECASE),
)
AUTHORIZATION_BEARER_PATTERN = re.compile(
    r"(?i)\b(authorization\s*:\s*)bearer\s+[A-Za-z0-9._\-]+"
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}\b")
API_KEY_ASSIGNMENT_PATTERN = re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;}]+")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
SECRET_KEY_WORDS = ("api_key", "apikey", "secret", "token", "access_key")
IGNORED_TRACKED_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".docx", ".sqlite"}


@dataclass(frozen=True)
class SecurityFinding:
    code: str
    path: Path
    message: str
    line: int | None = None


@dataclass(frozen=True)
class SecurityScanResult:
    root: Path
    findings: tuple[SecurityFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings


def scan_security(root: Path, *, tracked_only: bool = True) -> SecurityScanResult:
    root = root.expanduser().resolve()
    findings: list[SecurityFinding] = []
    for path in _iter_scan_files(root, tracked_only=tracked_only):
        findings.extend(_scan_file(root, path))
    env_example = root / ".env.example"
    if env_example.exists():
        findings.extend(validate_env_example(env_example))
    for rel in (
        "config/agents.yaml",
        "config/embeddings.yaml",
    ):
        path = root / rel
        if path.exists():
            findings.extend(validate_secret_config_file(path))
    return SecurityScanResult(root=root, findings=tuple(findings))


def validate_env_example(path: Path) -> tuple[SecurityFinding, ...]:
    findings: list[SecurityFinding] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            findings.append(
                SecurityFinding("invalid_env_example", path, "env example line must be NAME= with an empty value", line_number)
            )
            continue
        name, value = stripped.split("=", 1)
        if not ENV_NAME_PATTERN.match(name) or value.strip():
            findings.append(
                SecurityFinding("invalid_env_example", path, "env example must contain only variable names with empty values", line_number)
            )
    return tuple(findings)


def validate_secret_config_file(path: Path) -> tuple[SecurityFinding, ...]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return ()
    findings: list[SecurityFinding] = []
    _scan_config_value(path, data, (), findings)
    return tuple(findings)


def redact_secret_text(text: str, *, extra_secrets: tuple[str, ...] = ()) -> str:
    redacted = text
    for secret in extra_secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
            redacted = redacted.replace(f"Bearer {secret}", "Bearer [redacted]")
    redacted = AUTHORIZATION_BEARER_PATTERN.sub(r"\1Bearer [redacted]", redacted)
    redacted = BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    redacted = API_KEY_ASSIGNMENT_PATTERN.sub(r"\1[redacted]", redacted)
    for pattern in RAW_SECRET_PATTERNS:
        redacted = pattern.sub("[redacted-secret]", redacted)
    return redacted


def _iter_scan_files(root: Path, *, tracked_only: bool) -> list[Path]:
    if tracked_only:
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=root,
                check=True,
                text=True,
                capture_output=True,
            )
        except Exception:
            return _walk_files(root)
        files: list[Path] = []
        for rel in result.stdout.splitlines():
            path = root / rel
            if rel and path.is_file() and _should_scan_path(Path(rel)):
                files.append(path)
        return files
    return _walk_files(root)


def _walk_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and _should_scan_path(path.relative_to(root)):
            files.append(path)
    return files


def _should_scan_path(rel_path: Path) -> bool:
    parts = set(rel_path.parts)
    if parts & {".git", ".pytest_cache", "__pycache__", ".mypy_cache", ".ruff_cache"}:
        return False
    if rel_path.name.startswith(".") and rel_path.name not in {".env.example", ".gitignore"}:
        return False
    return rel_path.suffix.lower() not in IGNORED_TRACKED_SUFFIXES


def _scan_file(root: Path, path: Path) -> tuple[SecurityFinding, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ()
    findings: list[SecurityFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if "sk-test-secret" in line or "sk-test-" in line:
            continue
        for pattern in RAW_SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecurityFinding(
                        "secret_detected",
                        path,
                        "raw secret-looking value detected",
                        line_number,
                    )
                )
                break
        if _looks_like_raw_secret_assignment(line):
            findings.append(
                SecurityFinding(
                    "secret_detected",
                    path,
                    "secret-like assignment contains a non-empty literal value",
                    line_number,
                )
            )
    return tuple(findings)


def _looks_like_raw_secret_assignment(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    match = re.match(r"^([A-Z0-9_\-]*(?:API[_-]?KEY|TOKEN|SECRET|ACCESS[_-]?KEY)[A-Z0-9_\-]*)\s*[:=]\s*(.+)$", stripped)
    if not match:
        return False
    name = match.group(1)
    if name.endswith(("_PATTERN", "_PATTERNS", "_WORDS")):
        return False
    value = match.group(2).strip().rstrip(",").strip().strip("\"'")
    if not value or value.startswith("${") or value.startswith("$") or value.startswith(("(", "[", "{")):
        return False
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return False
    if ENV_NAME_PATTERN.match(value):
        return False
    if value.lower() in {"changeme", "placeholder", "your-api-key", "your_api_key", "test"}:
        return False
    return len(value) >= 8


def _scan_config_value(
    path: Path,
    value: object,
    keys: tuple[str, ...],
    findings: list[SecurityFinding],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _scan_config_value(path, item, (*keys, str(key)), findings)
        return
    if isinstance(value, list):
        for item in value:
            _scan_config_value(path, item, keys, findings)
        return
    if not isinstance(value, str):
        return
    key = keys[-1].lower() if keys else ""
    if key.endswith("_env"):
        if not ENV_NAME_PATTERN.match(value):
            findings.append(
                SecurityFinding("unsafe_config_secret", path, f"{'.'.join(keys)} must contain an env var name, not a literal value")
            )
        return
    if any(word in key for word in SECRET_KEY_WORDS) and value and not ENV_NAME_PATTERN.match(value):
        findings.append(
            SecurityFinding("unsafe_config_secret", path, f"{'.'.join(keys)} must not contain a literal secret")
        )
    if any(pattern.search(value) for pattern in RAW_SECRET_PATTERNS):
        findings.append(
            SecurityFinding("unsafe_config_secret", path, f"{'.'.join(keys)} contains a raw secret-looking value")
        )
