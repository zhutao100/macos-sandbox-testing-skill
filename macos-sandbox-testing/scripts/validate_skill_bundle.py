#!/usr/bin/env python3
"""Validate an Agent Skills-compatible skill directory.

Designed to be dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class SkillFrontmatter:
    name: str
    description: str


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def parse_frontmatter(skill_md: Path) -> SkillFrontmatter:
    raw = _read_text(skill_md)
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError("SKILL.md must start with YAML frontmatter delimited by '---' lines")

    fm = m.group(1)

    def find_value(key: str) -> str | None:
        # Very small YAML subset: `key: value` on its own line.
        # This intentionally ignores complex YAML; the required fields are simple.
        for line in fm.splitlines():
            if line.startswith(f"{key}:"):
                return line.split(":", 1)[1].strip().strip('"')
        return None

    name = find_value("name")
    desc = find_value("description")

    if not name:
        raise ValueError("frontmatter missing required field: name")
    if not desc:
        raise ValueError("frontmatter missing required field: description")

    return SkillFrontmatter(name=name, description=desc)


_NAME_RULE = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def validate_skill_dir(skill_dir: Path) -> None:
    skill_dir = skill_dir.resolve()
    if not skill_dir.is_dir():
        raise ValueError(f"Not a directory: {skill_dir}")

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise ValueError(f"Missing required file: {skill_md}")

    fm = parse_frontmatter(skill_md)

    if fm.name != skill_dir.name:
        raise ValueError(f"frontmatter name '{fm.name}' must match directory name '{skill_dir.name}'")

    if not _NAME_RULE.match(fm.name):
        raise ValueError(
            "frontmatter name must be lowercase letters/numbers with single hyphens (no leading/trailing hyphen)"
        )

    if len(fm.name) > 64:
        raise ValueError("frontmatter name must be <= 64 characters")

    if not (1 <= len(fm.description) <= 1024):
        raise ValueError("frontmatter description must be 1..1024 characters")

    for sub in ("scripts", "references", "assets"):
        p = skill_dir / sub
        if p.exists() and not p.is_dir():
            raise ValueError(f"{p} exists but is not a directory")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", required=True, type=Path)
    args = ap.parse_args()

    validate_skill_dir(args.skill_dir)
    print(f"OK: {args.skill_dir}")


if __name__ == "__main__":
    main()
