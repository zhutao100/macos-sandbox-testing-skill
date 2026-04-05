#!/usr/bin/env python3
"""Install macos-sandbox-testing into a Node/TypeScript project.

Goal: make **direct** `npm test` (or equivalent) run under an in-process
macOS Seatbelt sandbox/tripwire by preloading a native addon that links
`SandboxTestingBootstrap.c`.

This is robust for controlled entrypoints (npm/pnpm/yarn scripts). It is not
a universal guarantee for arbitrary `node ...` invocations.

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


_INSTALL_MARKER = ".macos-sandbox-testing-installed.json"


@dataclass(frozen=True)
class Backup:
    original_scripts: dict[str, str]
    install_dir: str


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _write_text(p: Path, s: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _load_json(p: Path) -> object:
    return json.loads(_read_text(p))


def _dump_json(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _relpath(from_dir: Path, to_path: Path) -> str:
    return os.path.relpath(str(to_path), str(from_dir)).replace(os.sep, "/")


def _copy_template(*, dst: Path, force: bool, dry_run: bool) -> list[str]:
    templates_root = Path(__file__).resolve().parent.parent / "assets" / "templates" / "node-typescript"
    if not templates_root.exists():
        raise RuntimeError(f"Missing template directory: {templates_root}")

    marker = dst / _INSTALL_MARKER
    if dst.exists() and not marker.exists() and not force:
        raise RuntimeError(
            f"Refusing to overwrite existing directory (missing marker {marker}).\n"
            f"Pass --force to overwrite: {dst}"
        )

    out: list[str] = []
    if dry_run:
        out.append(f"DRYRUN: would ensure directory {dst}")
    else:
        dst.mkdir(parents=True, exist_ok=True)

    # Copy only the addon project (binding.gyp + sandbox/* + README). The template
    # package.json is an example; we patch the project's real package.json.
    to_copy = [
        templates_root / "binding.gyp",
        templates_root / "README.md",
        templates_root / "sandbox",
    ]

    for src in to_copy:
        if not src.exists():
            raise RuntimeError(f"Missing template path: {src}")
        dest = dst / src.name

        if dest.exists() and not force:
            out.append(f"SKIP: already exists ({dest})")
            continue

        if dry_run:
            out.append(f"DRYRUN: would write {dest}")
            continue

        if src.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        out.append(f"OK: wrote {dest}")

    # Marker is written later with backup data.
    return out


def _patch_package_json(*, project_root: Path, install_dir: Path, force: bool, dry_run: bool) -> list[str]:
    pkg_json = project_root / "package.json"
    if not pkg_json.exists():
        raise RuntimeError(f"package.json not found under {project_root}")

    doc = _load_json(pkg_json)
    if not isinstance(doc, dict):
        raise RuntimeError("package.json is not a JSON object")

    scripts = doc.get("scripts")
    if scripts is None:
        scripts = {}
        doc["scripts"] = scripts
    if not isinstance(scripts, dict):
        raise RuntimeError("package.json scripts must be an object")

    # Backup fields we may touch.
    original: dict[str, str] = {}
    for k in ("test", "pretest", "build:macos-sandbox-testing"):
        v = scripts.get(k)
        if isinstance(v, str):
            original[k] = v

    preload = _relpath(project_root, install_dir / "sandbox" / "preload.js")
    # Note: NODE_OPTIONS values containing spaces require quoting; avoid that by
    # using the `--require=...` form.
    node_options_prefix = f"NODE_OPTIONS=--require=./{preload} "

    build_cmd = f"cd {_relpath(project_root, install_dir)} && node-gyp rebuild"

    msgs: list[str] = []

    # Ensure we can restore later.
    marker = install_dir / _INSTALL_MARKER
    if marker.exists() and not force:
        msgs.append(f"SKIP: {marker} already exists (use --force to re-install)")
        return msgs

    # Add/overwrite build script.
    scripts["build:macos-sandbox-testing"] = build_cmd
    msgs.append(f"OK: set scripts.build:macos-sandbox-testing")

    # Ensure addon is built before test.
    pretest = scripts.get("pretest")
    if isinstance(pretest, str) and pretest.strip():
        if "build:macos-sandbox-testing" not in pretest:
            scripts["pretest"] = pretest + " && npm run build:macos-sandbox-testing"
            msgs.append("OK: patched scripts.pretest")
        else:
            msgs.append("SKIP: scripts.pretest already references build:macos-sandbox-testing")
    else:
        scripts["pretest"] = "npm run build:macos-sandbox-testing"
        msgs.append("OK: set scripts.pretest")

    test = scripts.get("test")
    if isinstance(test, str) and test.strip():
        if "NODE_OPTIONS=" in test and "--require" in test and "preload.js" in test:
            msgs.append("SKIP: scripts.test already uses a preload")
        elif test.startswith(node_options_prefix):
            msgs.append("SKIP: scripts.test already patched")
        else:
            scripts["test"] = node_options_prefix + test
            msgs.append("OK: patched scripts.test to set NODE_OPTIONS preload")
    else:
        raise RuntimeError(
            "package.json is missing a non-empty scripts.test. "
            "This integration is designed to sandbox a scripted test entrypoint (e.g. `npm test`). "
            "Add a test script first, then re-run the installer."
        )

    # Persist package.json.
    _write_text(pkg_json, _dump_json(doc), dry_run=dry_run)
    msgs.append(f"{'DRYRUN:' if dry_run else 'OK:'} wrote {pkg_json}")

    backup = Backup(
        original_scripts=original,
        install_dir=_relpath(project_root, install_dir),
    )
    _write_text(marker, _dump_json(backup.__dict__), dry_run=dry_run)
    msgs.append(f"{'DRYRUN:' if dry_run else 'OK:'} wrote {marker}")

    return msgs


def main() -> None:
    ap = argparse.ArgumentParser(description="Install macos-sandbox-testing into a Node/TypeScript project")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root containing package.json")
    ap.add_argument(
        "--install-dir",
        type=Path,
        default=Path("tools") / "macos-sandbox-testing-node",
        help="Where to install the native preload addon (relative to --project-root)",
    )
    ap.add_argument("--force", action="store_true", help="Overwrite existing installed files when safe")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    install_dir: Path = args.install_dir
    if install_dir.is_absolute():
        print("ERROR: --install-dir must be a relative path", file=sys.stderr)
        sys.exit(2)

    if shutil.which("node-gyp") is None:
        raise RuntimeError(
            "node-gyp not found in PATH. Install it first, for example:\n"
            "- npm install -g node-gyp\n"
            "Then re-run this installer."
        )

    # Validate preconditions early to avoid leaving partial installs behind.
    pkg_json = project_root / "package.json"
    doc = _load_json(pkg_json)
    if not isinstance(doc, dict):
        raise RuntimeError("package.json is not a JSON object")
    scripts = doc.get("scripts")
    if not isinstance(scripts, dict):
        scripts = {}
    test = scripts.get("test")
    if not (isinstance(test, str) and test.strip()):
        raise RuntimeError(
            "package.json is missing a non-empty scripts.test. "
            "This integration is designed to sandbox a scripted test entrypoint (e.g. `npm test`). "
            "Add a test script first, then re-run the installer."
        )

    addon_dir = project_root / install_dir

    for msg in _copy_template(dst=addon_dir, force=args.force, dry_run=args.dry_run):
        print(msg)

    for msg in _patch_package_json(project_root=project_root, install_dir=addon_dir, force=args.force, dry_run=args.dry_run):
        print(msg)

    if args.dry_run:
        return

    print(
        "\nNext steps:\n"
        "- git status\n"
        "- npm test (or pnpm/yarn equivalent)\n"
        "- For validation: SEATBELT_SANDBOX_SELFTEST=1 SEATBELT_SANDBOX_LOG_LEVEL=verbose npm test\n"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
