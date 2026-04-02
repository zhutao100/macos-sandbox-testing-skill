#!/usr/bin/env python3
"""Remove swiftpm-sandbox-testing bootstrap sources from a SwiftPM package.

This uninstaller is marker-based and best-effort:
- Removes Swift shims from Swift executable/test targets.
- Removes the dedicated bootstrap Clang target directory (if marker file present).
- Reverts Package.swift edits that were added by install.py (marker comments).

It is safe to run multiple times.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from install import BOOTSTRAP_MARKER, DEP_MARKER, enumerate_targets


def _discover_bootstrap_targets(package_swift_text: str) -> list[str]:
    # Prefer explicit marker blocks inserted by install.py.
    blocks = re.findall(
        r"//\s*" + re.escape(DEP_MARKER) + r":begin.*?//\s*" + re.escape(DEP_MARKER) + r":end",
        package_swift_text,
        flags=re.DOTALL,
    )
    out: list[str] = []
    for b in blocks:
        m = re.search(r'\bname\s*:\s*"([^"]+)"', b)
        if m:
            out.append(m.group(1))
    return out


def _patch_package_swift(package_swift: Path, *, dry_run: bool) -> str:
    text = package_swift.read_text(encoding="utf-8")
    if DEP_MARKER not in text:
        return "SKIP Package.swift: no swiftpm-sandbox-testing markers found"

    original = text

    # 1) Remove dependency arguments that were inserted as a standalone line.
    text = re.sub(
        r"\n[ \t]*dependencies\s*:\s*\[\s*\"[A-Za-z0-9_]+\"\s*/\*\s*"
        + re.escape(DEP_MARKER)
        + r"\s*\*/\s*\],",
        "",
        text,
    )

    # 2) Remove any dependency array elements marked with /* swiftpm-sandbox-testing */.
    # - handle both leading-comma and trailing-comma forms.
    text = re.sub(
        r",\s*\"[A-Za-z0-9_]+\"\s*/\*\s*" + re.escape(DEP_MARKER) + r"\s*\*/",
        "",
        text,
    )
    text = re.sub(
        r"\"[A-Za-z0-9_]+\"\s*/\*\s*" + re.escape(DEP_MARKER) + r"\s*\*/\s*,",
        "",
        text,
    )
    text = re.sub(
        r"\"[A-Za-z0-9_]+\"\s*/\*\s*" + re.escape(DEP_MARKER) + r"\s*\*/",
        "",
        text,
    )

    # 3) Remove the bootstrap target block(s).
    text = re.sub(
        r"\n[ \t]*//\s*" + re.escape(DEP_MARKER) + r":begin.*?\n[ \t]*//\s*" + re.escape(DEP_MARKER) + r":end\n",
        "\n",
        text,
        flags=re.DOTALL,
    )

    if text == original:
        return "SKIP Package.swift: no changes needed"

    if dry_run:
        return "DRYRUN Package.swift: would remove swiftpm-sandbox-testing patches"

    package_swift.write_text(text, encoding="utf-8")
    return "OK Package.swift: removed swiftpm-sandbox-testing patches"


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall swiftpm-sandbox-testing bootstrap from a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    package_root: Path = args.package_root.resolve()
    package_swift = package_root / "Package.swift"
    if not package_swift.exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    removed_files = 0

    # Remove per-target files.
    for t in enumerate_targets(package_root):
        if not t.path.exists():
            continue

        # New Swift shim
        shim = t.path / "SwiftPMSandboxTestingBootstrap.swift"
        if shim.exists():
            if args.dry_run:
                print(f"DRYRUN {t.kind} {t.name}: would remove {shim}")
            else:
                shim.unlink()
                removed_files += 1
                print(f"OK {t.kind} {t.name}: removed {shim}")

        # Legacy C bootstrap (from older versions of the skill)
        legacy = t.path / "SandboxTestingBootstrap.c"
        if legacy.exists():
            if args.dry_run:
                print(f"DRYRUN {t.kind} {t.name}: would remove {legacy}")
            else:
                legacy.unlink()
                removed_files += 1
                print(f"OK {t.kind} {t.name}: removed {legacy}")

    # Remove bootstrap target directory if we can identify it and if marker is present.
    text = package_swift.read_text(encoding="utf-8")
    bootstrap_targets = _discover_bootstrap_targets(text)

    for bt in bootstrap_targets:
        bt_dir = package_root / "Sources" / bt
        marker = bt_dir / BOOTSTRAP_MARKER
        if bt_dir.exists() and marker.exists():
            if args.dry_run:
                print(f"DRYRUN bootstrap: would remove {bt_dir}")
            else:
                shutil.rmtree(bt_dir)
                print(f"OK bootstrap: removed {bt_dir}")

    # Patch Package.swift to remove markers.
    print(_patch_package_swift(package_swift, dry_run=args.dry_run))

    if not args.dry_run:
        print(f"\nRemoved {removed_files} file(s).")


if __name__ == "__main__":
    main()
