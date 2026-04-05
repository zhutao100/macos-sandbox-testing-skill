#!/usr/bin/env python3
"""Uninstall macos-sandbox-testing from a Node/TypeScript project."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


_INSTALL_MARKER = ".macos-sandbox-testing-installed.json"


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _write_text(p: Path, s: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    p.write_text(s, encoding="utf-8")


def _load_json(p: Path) -> object:
    return json.loads(_read_text(p))


def _dump_json(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _relpath(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(str(to_path), str(from_dir)).replace(os.sep, "/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall macos-sandbox-testing from a Node/TypeScript project")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root containing package.json")
    ap.add_argument(
        "--install-dir",
        type=Path,
        default=Path("tools") / "macos-sandbox-testing-node",
        help="Install dir used during installation (relative to --project-root)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    install_dir = args.install_dir
    if install_dir.is_absolute():
        print("ERROR: --install-dir must be a relative path", file=sys.stderr)
        sys.exit(2)

    addon_dir = project_root / install_dir
    marker = addon_dir / _INSTALL_MARKER
    if not marker.exists():
        raise SystemExit(f"ERROR: marker not found (not installed?): {marker}")

    backup = _load_json(marker)
    if not isinstance(backup, dict):
        raise SystemExit(f"ERROR: invalid marker JSON: {marker}")

    pkg_json = project_root / "package.json"
    doc = _load_json(pkg_json)
    if not isinstance(doc, dict):
        raise SystemExit("ERROR: package.json is not a JSON object")
    scripts = doc.get("scripts")
    if scripts is None:
        scripts = {}
        doc["scripts"] = scripts
    if not isinstance(scripts, dict):
        raise SystemExit("ERROR: package.json scripts must be an object")

    original_scripts = backup.get("original_scripts")
    if not isinstance(original_scripts, dict):
        original_scripts = {}

    # Restore touched keys.
    touched = {"test", "pretest", "build:macos-sandbox-testing"}
    for k in touched:
        if k in original_scripts and isinstance(original_scripts[k], str):
            scripts[k] = original_scripts[k]
        else:
            scripts.pop(k, None)

    _write_text(pkg_json, _dump_json(doc), dry_run=args.dry_run)
    print(f"{'DRYRUN:' if args.dry_run else 'OK:'} wrote {pkg_json}")

    if args.dry_run:
        return

    shutil.rmtree(addon_dir)
    print(f"OK: removed {addon_dir}")


if __name__ == "__main__":
    main()
