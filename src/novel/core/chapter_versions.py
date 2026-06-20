from __future__ import annotations

from pathlib import Path
import re


def is_allowed_chapter_version_name(file_name: str, target: str) -> bool:
    return bool(re.fullmatch(rf"{re.escape(target)}(?:\.v[0-9]+)?\.md", file_name))


def latest_chapter_version_path(chapter_dir: Path, target: str) -> Path:
    latest_version = 0
    latest_path = chapter_dir / f"{target}.md"
    pattern = re.compile(rf"^{re.escape(target)}\.v([0-9]+)\.md$")
    for path in chapter_dir.glob(f"{target}.v*.md"):
        match = pattern.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        if version > latest_version:
            latest_version = version
            latest_path = path
    return latest_path


def next_chapter_version_path(chapter_dir: Path, target: str) -> Path:
    existing_versions = [1]
    pattern = re.compile(rf"^{re.escape(target)}\.v([0-9]+)\.md$")
    for path in chapter_dir.glob(f"{target}.v*.md"):
        match = pattern.match(path.name)
        if match:
            existing_versions.append(int(match.group(1)))
    return chapter_dir / f"{target}.v{max(existing_versions) + 1}.md"
