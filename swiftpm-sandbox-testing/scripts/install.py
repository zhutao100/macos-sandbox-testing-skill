#!/usr/bin/env python3
"""Install SwiftPM sandbox-testing bootstrap sources into a SwiftPM package.

This modifies the target repository by adding a single C source file to each
executable and test target so the sandbox/tripwire activates for direct
`swift run` and `swift test`.

No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetInfo:
    name: str
    kind: str
    path: Path


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _dump_package(package_root: Path) -> dict:
    proc = _run(["swift", "package", "dump-package"], cwd=package_root)
    if proc.returncode != 0:
        raise RuntimeError(
            "swift package dump-package failed\n"
            f"cwd: {package_root}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}\n"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"failed to parse dump-package JSON: {e}") from e


def _default_target_path(name: str, kind: str) -> Path:
    if kind == "test":
        return Path("Tests") / name
    return Path("Sources") / name


def enumerate_targets(package_root: Path) -> list[TargetInfo]:
    data = _dump_package(package_root)
    targets = data.get("targets", [])
    out: list[TargetInfo] = []

    for t in targets:
        name = t.get("name")
        kind = t.get("type")
        if not name or not kind:
            continue

        # SwiftPM types we care about.
        if kind not in {"executable", "test"}:
            continue

        rel_path = t.get("path")
        target_path = Path(rel_path) if rel_path else _default_target_path(name, kind)
        out.append(TargetInfo(name=name, kind=kind, path=(package_root / target_path)))

    return out


def install_into_target(target: TargetInfo, *, template: str, force: bool, dry_run: bool) -> str:
    target_dir = target.path
    if not target_dir.exists():
        return f"SKIP {target.kind} {target.name}: target path not found: {target_dir}"

    dst = target_dir / "SandboxTestingBootstrap.c"
    if dst.exists() and not force:
        return f"SKIP {target.kind} {target.name}: already installed ({dst})"

    if dry_run:
        return f"DRYRUN {target.kind} {target.name}: would write {dst}"

    dst.write_text(template, encoding="utf-8")
    return f"OK {target.kind} {target.name}: wrote {dst}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Install swiftpm-sandbox-testing bootstrap into a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="Overwrite existing SandboxTestingBootstrap.c")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--only",
        choices=["all", "executable", "test"],
        default="all",
        help="Limit installation to executable or test targets",
    )

    args = ap.parse_args()
    package_root: Path = args.package_root.resolve()

    if not (package_root / "Package.swift").exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    template_path = Path(__file__).resolve().parent.parent / "assets" / "templates" / "SandboxTestingBootstrap.c"
    template = template_path.read_text(encoding="utf-8")

    targets = enumerate_targets(package_root)
    if args.only != "all":
        targets = [t for t in targets if t.kind == args.only]

    if not targets:
        print("No executable/test targets found. Nothing to install.")
        return

    for t in targets:
        print(install_into_target(t, template=template, force=args.force, dry_run=args.dry_run))

    if args.dry_run:
        return

    print("\nNext steps:\n- git status\n- swift test (optionally with SWIFTPM_SANDBOX_SELFTEST=1)\n")


if __name__ == "__main__":
    main()
