#!/usr/bin/env python3
"""
Repo checks for swiftpm-sandbox-testing-skill.

Designed to be:
- dependency-free (stdlib only)
- runnable on any OS (macOS recommended for runtime smoke tests, but not required here)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    # .../.agents/skills/update-swiftpm-sandbox-testing-skill/scripts/run_repo_checks.py
    # -> repo root is 4 parents up.
    return Path(__file__).resolve().parents[4]


def _run(cmd: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(
            "command failed\n"
            f"  cwd: {cwd}\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  stdout:\n{proc.stdout}\n"
            f"  stderr:\n{proc.stderr}\n"
        )


def _check_no_chatgpt_citations(root: Path) -> None:
    bad: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".md", ".py", ".c", ".h"}:
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "\uE200cite\uE202" in txt:
            bad.append(p)
    if bad:
        joined = "\n".join(f"- {p.relative_to(root)}" for p in bad)
        raise RuntimeError(
            "Found ChatGPT-only citation tokens in repo files (these should never be committed):\n" + joined
        )


def _check_template_invariants(template: Path) -> None:
    txt = template.read_text(encoding="utf-8")

    required_env = [
        "SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP",
        "SWIFTPM_SANDBOX_TRIPWIRE",
        "SWIFTPM_SANDBOX_MODE",
        "SWIFTPM_SANDBOX_SELFTEST",
    ]
    missing = [e for e in required_env if e not in txt]
    if missing:
        raise RuntimeError("Template is missing expected env vars: " + ", ".join(missing))

    # Ensure deny-before-allow ordering inside the SBPL strings.
    # (This check is intentionally simple; it catches accidental reversals.)
    def _check_profile(name: str) -> None:
        m = re.search(rf'static const char \*{re.escape(name)}\s*=\s*(.*?);\n', txt, re.DOTALL)
        if not m:
            raise RuntimeError(f"Could not locate {name} SBPL string in template")
        body = m.group(1)
        deny = body.find('"    "(deny file-write*)')
        # Above string includes escapes and indentation; do a simpler check:
        deny = body.find('(deny file-write*)')
        allow = body.find('(allow file-write*')
        if deny == -1 or allow == -1 or not (deny < allow):
            raise RuntimeError(f"SBPL ordering check failed for {name}: expected deny before allow")

    _check_profile("kSwiftpmstProfileStrict")
    _check_profile("kSwiftpmstProfileCompat")


def main() -> None:
    root = _repo_root()

    main_skill = root / "swiftpm-sandbox-testing"
    internal_skill = root / ".agents" / "skills" / "update-swiftpm-sandbox-testing-skill"

    validator = root / "swiftpm-sandbox-testing" / "scripts" / "validate_skill_bundle.py"
    template = root / "swiftpm-sandbox-testing" / "assets" / "templates" / "SandboxTestingBootstrap.c"

    if not validator.exists():
        raise RuntimeError(f"Missing validator: {validator}")
    if not template.exists():
        raise RuntimeError(f"Missing template: {template}")

    print("1) Validate skill directories…")
    _run([sys.executable, str(validator), "--skill-dir", str(main_skill)], cwd=root)
    _run([sys.executable, str(validator), "--skill-dir", str(internal_skill)], cwd=root)

    print("2) Python compile checks…")
    _run([sys.executable, "-m", "compileall", str(root / "swiftpm-sandbox-testing" / "scripts")], cwd=root)
    _run([sys.executable, "-m", "compileall", str(internal_skill / "scripts")], cwd=root)

    print("3) Citation-token check…")
    _check_no_chatgpt_citations(root)

    print("4) Template invariants…")
    _check_template_invariants(template)

    print("OK: repo checks passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
