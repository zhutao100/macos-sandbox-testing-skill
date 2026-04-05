#!/usr/bin/env python3
"""Verify macos-sandbox-testing is active for `npm test` (Node/TypeScript).

This runs `npm test` with `SEATBELT_SANDBOX_SELFTEST=1`, then checks for:

  <workspace>/.build/macos-sandbox-testing/<run-id>/logs/events.jsonl
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _latest_subdir(root: Path) -> Path | None:
    if not root.exists():
        return None
    subs = [p for p in root.iterdir() if p.is_dir()]
    if not subs:
        return None
    subs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return subs[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify macos-sandbox-testing is active (npm test)")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root containing package.json")
    ap.add_argument(
        "--allow-test-failure",
        action="store_true",
        help="Do not fail verification if `npm test` exits non-zero; still require sandbox artifacts to exist.",
    )
    ap.add_argument("--npm", default="npm", help="npm executable to use (default: npm)")
    ap.add_argument("--npm-args", nargs=argparse.REMAINDER, help="Extra args passed to `npm test`")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    if not (project_root / "package.json").exists():
        print(f"ERROR: package.json not found under {project_root}", file=sys.stderr)
        sys.exit(2)

    env = dict(os.environ)
    env["SEATBELT_SANDBOX_SELFTEST"] = "1"
    env.setdefault("SEATBELT_SANDBOX_LOG_LEVEL", "verbose")
    env["SEATBELT_SANDBOX_WORKSPACE_ROOT"] = str(project_root)

    cmd = [args.npm, "test"]
    if args.npm_args:
        cmd.extend(args.npm_args)

    proc = _run(cmd, cwd=project_root, env=env)

    print(proc.stdout)
    test_failed = proc.returncode != 0
    if test_failed:
        print(proc.stderr, file=sys.stderr)
        if not args.allow_test_failure:
            print(f"ERROR: npm test failed with exit code {proc.returncode}", file=sys.stderr)
            sys.exit(proc.returncode)
        print(
            f"WARN: npm test failed with exit code {proc.returncode}; continuing to verify sandbox artifacts.",
            file=sys.stderr,
        )

    sandbox_parent = project_root / ".build" / "macos-sandbox-testing"
    latest = _latest_subdir(sandbox_parent)
    if not latest:
        print(f"ERROR: no sandbox run directory found under {sandbox_parent}", file=sys.stderr)
        sys.exit(3)

    events = latest / "logs" / "events.jsonl"
    if not events.exists():
        print(f"ERROR: missing bootstrap log: {events}", file=sys.stderr)
        sys.exit(4)

    txt = events.read_text(encoding="utf-8", errors="replace")
    if "\"op\":\"bootstrap\"" not in txt:
        print(f"ERROR: bootstrap marker not found in {events}", file=sys.stderr)
        sys.exit(5)

    print(f"OK: sandbox root: {latest}")
    print(f"OK: events log: {events}")
    if test_failed:
        print(f"WARN: npm test exited non-zero ({proc.returncode}), but sandbox appears active.", file=sys.stderr)


if __name__ == "__main__":
    main()
