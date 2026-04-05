#!/usr/bin/env python3
"""Uninstall macos-sandbox-testing from a Go module."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


_INSTALL_MARKER = ".macos-sandbox-testing-installed.json"
_GENERATED_MARKER_LINE = "// macos-sandbox-testing: generated"


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _load_json(p: Path) -> object:
    return json.loads(_read_text(p))


def _relpath(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(str(to_path), str(from_dir)).replace(os.sep, "/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall macos-sandbox-testing from a Go module")
    ap.add_argument("--project-root", required=True, type=Path, help="Go module root containing go.mod")
    ap.add_argument(
        "--install-dir",
        type=Path,
        default=Path("tools") / "macos-seatbelt-testing-go",
        help="Install dir used during installation (relative to --project-root)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    if not (project_root / "go.mod").exists():
        raise SystemExit(f"ERROR: go.mod not found under {project_root}")

    install_dir = args.install_dir
    if install_dir.is_absolute():
        raise SystemExit("ERROR: --install-dir must be a relative path")

    helper_dir = project_root / install_dir
    marker = helper_dir / _INSTALL_MARKER
    if not marker.exists():
        raise SystemExit(f"ERROR: marker not found (not installed?): {marker}")

    doc = _load_json(marker)
    if not isinstance(doc, dict):
        raise SystemExit(f"ERROR: invalid marker JSON: {marker}")

    files = doc.get("generated_files")
    if not isinstance(files, list):
        files = []

    for rel in files:
        if not isinstance(rel, str):
            continue
        p = project_root / rel
        if not p.exists():
            continue
        txt = _read_text(p)
        if _GENERATED_MARKER_LINE not in txt:
            print(f"SKIP: refusing to remove non-generated file: {p}")
            continue
        if args.dry_run:
            print(f"DRYRUN: would remove {p}")
        else:
            p.unlink()
            print(f"OK: removed {p}")

    if args.dry_run:
        return

    shutil.rmtree(helper_dir)
    print(f"OK: removed {helper_dir}")


if __name__ == "__main__":
    main()
