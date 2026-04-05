#!/usr/bin/env python3
"""Uninstall macos-sandbox-testing from a Python venv."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


_INSTALL_MARKER = "macos_sandbox_testing.installed.json"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _site_packages(venv_python: Path) -> Path:
    code = (
        "import json, sysconfig; "
        "print(json.dumps(sysconfig.get_paths()[\"purelib\"]))"
    )
    # Use `-S` so the uninstall can run even if a `.pth` hook is currently installed.
    proc = _run([str(venv_python), "-S", "-c", code])
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to query site-packages: {proc.stderr.strip()}")
    return Path(json.loads(proc.stdout.strip()))


def _detect_venv(project_root: Path, explicit: Path | None) -> Path:
    if explicit:
        p = explicit
        if not p.is_absolute():
            p = project_root / p
        return p.resolve()
    env = os.environ.get("VIRTUAL_ENV")
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = project_root / p
        return p.resolve()
    cand = project_root / ".venv"
    if cand.exists():
        return cand.resolve()
    raise RuntimeError("No venv specified. Pass --venv, or activate a venv, or create .venv/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall macos-sandbox-testing from a Python venv")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root")
    ap.add_argument("--venv", type=Path, help="Path to venv directory (defaults: $VIRTUAL_ENV or .venv)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    venv_dir = _detect_venv(project_root, args.venv)
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        raise SystemExit(f"ERROR: venv python not found: {venv_python}")

    sp = _site_packages(venv_python)
    marker = sp / _INSTALL_MARKER
    if not marker.exists():
        raise SystemExit(f"ERROR: marker not found (not installed?): {marker}")

    doc = json.loads(marker.read_text(encoding="utf-8", errors="replace"))
    files = doc.get("installed_files") if isinstance(doc, dict) else None
    if not isinstance(files, list):
        raise SystemExit(f"ERROR: invalid marker JSON: {marker}")

    for f in files:
        if not isinstance(f, str):
            continue
        cand = Path(f)
        if cand.is_absolute() or ".." in cand.parts:
            print(f"SKIP: refusing suspicious path in marker: {f}", file=sys.stderr)
            continue
        p = sp / cand
        if not p.exists():
            continue
        if args.dry_run:
            print(f"DRYRUN: would remove {p}")
        else:
            p.unlink()
            print(f"OK: removed {p}")

    if args.dry_run:
        return


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
