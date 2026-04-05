#!/usr/bin/env python3
"""Verify macos-sandbox-testing is active for a Python venv.

This runs the venv's python with `SEATBELT_SANDBOX_SELFTEST=1`, then checks for:

  <workspace>/.build/macos-sandbox-testing/<run-id>/logs/events.jsonl
"""

from __future__ import annotations

import argparse
import json
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


def _detect_venv(project_root: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    env = os.environ.get("VIRTUAL_ENV")
    if env:
        return Path(env)
    cand = project_root / ".venv"
    if cand.exists():
        return cand
    raise RuntimeError("No venv specified. Pass --venv, or activate a venv, or create .venv/")


def _site_packages(venv_python: Path) -> Path:
    code = (
        "import json, sysconfig; "
        "print(json.dumps(sysconfig.get_paths()[\"purelib\"]))"
    )
    # Use `-S` to avoid executing `.pth` hooks while we probe paths.
    proc = _run([str(venv_python), "-S", "-c", code], cwd=venv_python.parent, env=dict(os.environ))
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to query site-packages: {proc.stderr.strip()}")
    p = json.loads(proc.stdout.strip())
    return Path(p)


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify macos-sandbox-testing is active (Python venv)")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root")
    ap.add_argument("--venv", type=Path, help="Path to venv directory (defaults: $VIRTUAL_ENV or .venv)")
    ap.add_argument(
        "--allow-test-failure",
        action="store_true",
        help="Do not fail verification if python exits non-zero; still require sandbox artifacts to exist.",
    )
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    venv_dir = _detect_venv(project_root, args.venv)
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        print(f"ERROR: venv python not found: {venv_python}", file=sys.stderr)
        sys.exit(2)

    sp = _site_packages(venv_python)
    marker = sp / "macos_sandbox_testing.installed.json"
    if not marker.exists():
        print(f"ERROR: not installed (missing marker): {marker}", file=sys.stderr)
        sys.exit(3)

    env = dict(os.environ)
    env["SEATBELT_SANDBOX_SELFTEST"] = "1"
    env.setdefault("SEATBELT_SANDBOX_LOG_LEVEL", "verbose")
    env["SEATBELT_SANDBOX_WORKSPACE_ROOT"] = str(project_root)

    proc = _run([str(venv_python), "-c", "print('msst')"], cwd=project_root, env=env)
    print(proc.stdout)

    failed = proc.returncode != 0
    if failed:
        print(proc.stderr, file=sys.stderr)
        if not args.allow_test_failure:
            print(f"ERROR: python exited with {proc.returncode}", file=sys.stderr)
            sys.exit(proc.returncode)
        print(f"WARN: python exited with {proc.returncode}; continuing to verify sandbox artifacts.", file=sys.stderr)

    sandbox_parent = project_root / ".build" / "macos-sandbox-testing"
    latest = _latest_subdir(sandbox_parent)
    if not latest:
        print(f"ERROR: no sandbox run directory found under {sandbox_parent}", file=sys.stderr)
        sys.exit(4)

    events = latest / "logs" / "events.jsonl"
    if not events.exists():
        print(f"ERROR: missing bootstrap log: {events}", file=sys.stderr)
        sys.exit(5)

    txt = events.read_text(encoding="utf-8", errors="replace")
    if "\"op\":\"bootstrap\"" not in txt:
        print(f"ERROR: bootstrap marker not found in {events}", file=sys.stderr)
        sys.exit(6)

    print(f"OK: sandbox root: {latest}")
    print(f"OK: events log: {events}")
    if failed:
        print(f"WARN: python exited non-zero ({proc.returncode}), but sandbox appears active.", file=sys.stderr)


if __name__ == "__main__":
    main()
