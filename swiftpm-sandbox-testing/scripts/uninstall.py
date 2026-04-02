#!/usr/bin/env python3
"""Remove swiftpm-sandbox-testing bootstrap sources from a SwiftPM package."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from install import enumerate_targets


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall swiftpm-sandbox-testing bootstrap from a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    package_root: Path = args.package_root.resolve()
    if not (package_root / "Package.swift").exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    targets = enumerate_targets(package_root)
    removed = 0

    for t in targets:
        dst = t.path / "SandboxTestingBootstrap.c"
        if not dst.exists():
            print(f"SKIP {t.kind} {t.name}: not installed")
            continue
        if args.dry_run:
            print(f"DRYRUN {t.kind} {t.name}: would remove {dst}")
            continue
        dst.unlink()
        removed += 1
        print(f"OK {t.kind} {t.name}: removed {dst}")

    if not args.dry_run:
        print(f"\nRemoved {removed} file(s).")


if __name__ == "__main__":
    main()
