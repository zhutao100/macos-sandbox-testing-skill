#!/usr/bin/env python3
"""Verify swiftpm-sandbox-testing is active in a SwiftPM package.

This runs `swift test` with SWIFTPM_SANDBOX_SELFTEST=1, then checks that the
repo-local sandbox root, bootstrap marker, and (when network is expected to be restricted)
a network self-test marker log were produced.
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
    ap = argparse.ArgumentParser(description="Verify swiftpm-sandbox-testing is active")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--configuration", choices=["debug", "release"], default="debug")
    ap.add_argument(
        "--allow-test-failure",
        action="store_true",
        help="Do not fail verification if `swift test` exits non-zero; still require sandbox artifacts to exist.",
    )
    args = ap.parse_args()

    package_root: Path = args.package_root.resolve()
    if not (package_root / "Package.swift").exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    # Preflight: best-effort signal that install.py patched Package.swift.
    pkg_text = (package_root / "Package.swift").read_text(encoding="utf-8", errors="replace")
    if "swiftpm-sandbox-testing" not in pkg_text:
        print(
            "WARN: Package.swift does not appear to be patched by swiftpm-sandbox-testing (no markers found).",
            file=sys.stderr,
        )
        print(
            "      If swift test does not produce a sandbox log, re-run install.py and inspect its Package.swift patch output.",
            file=sys.stderr,
        )

    env = dict(os.environ)
    env["SWIFTPM_SANDBOX_SELFTEST"] = "1"
    env.setdefault("SWIFTPM_SANDBOX_LOG_LEVEL", "verbose")

    cmd = ["swift", "test", "-c", args.configuration]
    proc = _run(cmd, cwd=package_root, env=env)

    print(proc.stdout)
    test_failed = proc.returncode != 0
    if test_failed:
        print(proc.stderr, file=sys.stderr)
        if not args.allow_test_failure:
            print(f"ERROR: swift test failed with exit code {proc.returncode}", file=sys.stderr)
            sys.exit(proc.returncode)
        print(
            f"WARN: swift test failed with exit code {proc.returncode}; continuing to verify sandbox artifacts.",
            file=sys.stderr,
        )

    # Locate sandbox root.
    sandbox_parent = package_root / ".build" / "swiftpm-sandbox-testing"
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
        # Accept either compact or spaced JSON. (Template is compact.)
        print(f"ERROR: bootstrap marker not found in {events}", file=sys.stderr)
        sys.exit(5)

    if '"op":"selftest.network"' not in txt:
        print(
            f"WARN: network self-test marker not found in {events} (network may be enabled, allowlisted, or tripwire disabled).",
            file=sys.stderr,
        )
    else:
        print("OK: network self-test marker found")

    print(f"OK: sandbox root: {latest}")
    print(f"OK: events log: {events}")
    if test_failed:
        print(f"WARN: swift test exited non-zero ({proc.returncode}), but sandbox appears active.", file=sys.stderr)


if __name__ == "__main__":
    main()
