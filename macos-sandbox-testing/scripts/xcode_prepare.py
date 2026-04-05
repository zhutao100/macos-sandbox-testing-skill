#!/usr/bin/env python3
"""Prepare macos-sandbox-testing files for Xcode integration.

This script intentionally does **not** attempt to edit `.xcodeproj` / `.pbxproj`
files. Those formats are easy to corrupt and are frequently customized.

Instead, it:
- copies the canonical `SandboxTestingBootstrap.c` template into your repo, and
- copies optional linker-anchor helpers and README notes.

You then add the files to targets in Xcode.

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _copy(src: Path, dst: Path, *, force: bool) -> str:
    if dst.exists() and not force:
        return f"SKIP: already exists ({dst})"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"OK: wrote {dst}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Prepare macos-sandbox-testing files for Xcode integration")
    ap.add_argument("--project-root", required=True, type=Path, help="Repo root (where you want to commit the prepared files)")
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("Tools") / "macos-sandbox-testing-xcode",
        help="Destination directory (relative to --project-root)",
    )
    ap.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    out_dir: Path = args.out_dir
    if out_dir.is_absolute():
        raise SystemExit("ERROR: --out-dir must be relative")

    skill_root = Path(__file__).resolve().parent.parent
    templates_root = skill_root / "assets" / "templates"

    src_bootstrap = templates_root / "SandboxTestingBootstrap.c"
    src_xcode_dir = templates_root / "xcode"

    if not src_bootstrap.exists():
        raise SystemExit(f"ERROR: missing template: {src_bootstrap}")
    if not src_xcode_dir.exists():
        raise SystemExit(f"ERROR: missing template dir: {src_xcode_dir}")

    dst_dir = project_root / out_dir

    print(_copy(src_bootstrap, dst_dir / "SandboxTestingBootstrap.c", force=args.force))
    for name in ("README.md", "SandboxTestingAnchor.c", "SandboxTestingAnchor.swift"):
        print(_copy(src_xcode_dir / name, dst_dir / name, force=args.force))

    print(
        "\nNext steps (in Xcode):\n"
        "1) Drag the prepared files into the Xcode project.\n"
        "2) Enable Target Membership for the targets you want sandboxed (app/CLI, and/or the test bundle).\n"
        "3) In the scheme, set SEATBELT_SANDBOX_WORKSPACE_ROOT=$(PROJECT_DIR) (recommended).\n"
        "4) Run tests with SEATBELT_SANDBOX_SELFTEST=1 to validate.\n"
    )


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # Allow piping output to tools like `head`.
        sys.exit(0)
