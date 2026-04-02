#!/usr/bin/env python3
"""Uninstall swiftpm-sandbox-testing bootstrap sources from a SwiftPM package.

This is a best-effort remover:
- Removes injected `SandboxTestingBootstrap.swift` files from executable/test targets.
- Removes `Sources/SwiftPMSandboxTestingRuntime/` if present.
- Attempts to remove installer-inserted markers from Package.swift.

If Package.swift has been heavily reformatted or manually edited after installation,
manual cleanup may still be required.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from install import (
    RUNTIME_TARGET_NAME,
    TargetInfo,
    enumerate_targets,
    _read_text,
    _write_text,
)

_MARK_RUNTIME_BEGIN = "// swiftpm-sandbox-testing:runtime-target-begin"
_MARK_RUNTIME_END = "// swiftpm-sandbox-testing:runtime-target-end"
_MARK_INLINE = "// swiftpm-sandbox-testing"


def _cleanup_package_swift(pkg: Path) -> None:
    src = _read_text(pkg)

    # Remove runtime target block.
    if _MARK_RUNTIME_BEGIN in src and _MARK_RUNTIME_END in src:
        src = re.sub(
            rf"\n\s*{re.escape(_MARK_RUNTIME_BEGIN)}.*?{re.escape(_MARK_RUNTIME_END)}\n",
            "\n",
            src,
            flags=re.DOTALL,
        )

    # Remove dependency injection lines.
    src = re.sub(
        rf"\s*,?\s*\"{re.escape(RUNTIME_TARGET_NAME)}\"\s*,?\s*{re.escape(_MARK_INLINE)}\s*\n",
        "\n",
        src,
    )

    # Remove swiftSettings injection lines.
    src = re.sub(
        rf"\s*,?\s*\.enableExperimentalFeature\(\"SymbolLinkageMarkers\"\)\s*,?\s*{re.escape(_MARK_INLINE)}\s*\n",
        "\n",
        src,
    )

    _write_text(pkg, src)


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall swiftpm-sandbox-testing from a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    package_root: Path = args.package_root.resolve()
    pkg = package_root / "Package.swift"
    if not pkg.exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    targets: list[TargetInfo] = enumerate_targets(package_root)

    removed = 0
    for t in targets:
        if t.kind not in {"executable", "test"}:
            continue
        dst = t.path / "SandboxTestingBootstrap.swift"
        if not dst.exists():
            continue
        if args.dry_run:
            print(f"DRYRUN {t.kind} {t.name}: would remove {dst}")
            continue
        dst.unlink()
        removed += 1
        print(f"OK {t.kind} {t.name}: removed {dst}")

    runtime_dir = package_root / "Sources" / RUNTIME_TARGET_NAME
    if runtime_dir.exists():
        if args.dry_run:
            print(f"DRYRUN: would remove {runtime_dir}")
        else:
            for p in sorted(runtime_dir.rglob("*"), reverse=True):
                if p.is_file():
                    p.unlink()
                elif p.is_dir():
                    p.rmdir()
            runtime_dir.rmdir()
            print(f"OK: removed {runtime_dir}")

    if args.dry_run:
        print(f"DRYRUN: would patch {pkg} to remove injected markers")
        return

    _cleanup_package_swift(pkg)
    print(f"OK: patched {pkg} (best-effort)")
    print(f"Removed {removed} injected file(s).")


if __name__ == "__main__":
    main()
