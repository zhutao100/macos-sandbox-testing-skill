#!/usr/bin/env python3
"""Uninstall macos-sandbox-testing from a Rust/Cargo workspace.

This removes what `cargo_install.py` added:
- the helper crate directory (only if the marker file is present),
- the Cargo manifest dependency entries marked with `# macos-sandbox-testing`,
- the Rust source anchor block between `// macos-sandbox-testing: begin/end`,
- the `.cargo/config.toml` env override line marked with `# macos-sandbox-testing`.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as e:  # pragma: no cover (python < 3.11)
    raise SystemExit("ERROR: python3 is missing stdlib tomllib (requires Python 3.11+)") from e


_MARKER_FILE = ".macos-sandbox-testing-installed"
_RUST_ANCHOR_BEGIN = "// macos-sandbox-testing: begin"
_RUST_ANCHOR_END = "// macos-sandbox-testing: end"
_MARKER_COMMENT = "# macos-sandbox-testing"

_HELPER_CRATE_NAME = "macos-seatbelt-testing"


@dataclass(frozen=True)
class CargoWorkspace:
    root: Path
    package_dirs: list[Path]


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _write_text(p: Path, s: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _load_toml(p: Path) -> dict:
    return tomllib.loads(_read_text(p))


def _is_package_manifest(doc: dict) -> bool:
    return isinstance(doc.get("package"), dict)


def _workspace_members(doc: dict) -> tuple[list[str], list[str]]:
    ws = doc.get("workspace")
    if not isinstance(ws, dict):
        return ([], [])
    members = ws.get("members", [])
    exclude = ws.get("exclude", [])
    if not isinstance(members, list):
        members = []
    if not isinstance(exclude, list):
        exclude = []
    members = [m for m in members if isinstance(m, str)]
    exclude = [e for e in exclude if isinstance(e, str)]
    return (members, exclude)


def _expand_workspace_globs(root: Path, patterns: list[str]) -> set[Path]:
    out: set[Path] = set()
    for pat in patterns:
        for hit in root.glob(pat):
            if hit.is_dir():
                if (hit / "Cargo.toml").exists():
                    out.add(hit)
            elif hit.is_file() and hit.name == "Cargo.toml":
                out.add(hit.parent)
    return out


def _discover_workspace(project_root: Path) -> CargoWorkspace:
    manifest = project_root / "Cargo.toml"
    if not manifest.exists():
        raise RuntimeError(f"Cargo.toml not found under {project_root}")

    doc = _load_toml(manifest)
    package_dirs: set[Path] = set()

    if _is_package_manifest(doc):
        package_dirs.add(project_root)

    members, exclude = _workspace_members(doc)
    if members:
        member_dirs = _expand_workspace_globs(project_root, members)
        exclude_dirs = _expand_workspace_globs(project_root, exclude)
        package_dirs.update(member_dirs - exclude_dirs)

    if not package_dirs:
        return CargoWorkspace(root=project_root, package_dirs=[])

    return CargoWorkspace(root=project_root, package_dirs=sorted(package_dirs))


def _remove_lines_marked(txt: str, *, needle: str) -> tuple[str, bool]:
    lines = txt.splitlines(keepends=True)
    new_lines = [ln for ln in lines if needle not in ln]
    if len(new_lines) == len(lines):
        return (txt, False)
    return ("".join(new_lines), True)


def _remove_rust_anchor_block(txt: str) -> tuple[str, bool]:
    if _RUST_ANCHOR_BEGIN not in txt or _RUST_ANCHOR_END not in txt:
        return (txt, False)
    lines = txt.splitlines(keepends=True)
    begin_i = end_i = None
    for i, ln in enumerate(lines):
        if ln.rstrip("\n") == _RUST_ANCHOR_BEGIN:
            begin_i = i
            continue
        if begin_i is not None and ln.rstrip("\n") == _RUST_ANCHOR_END:
            end_i = i
            break
    if begin_i is None or end_i is None or end_i < begin_i:
        return (txt, False)

    del lines[begin_i : end_i + 1]
    # Trim one extra blank line if we left double-spacing.
    if begin_i < len(lines) and lines[begin_i].strip() == "":
        del lines[begin_i]
    return ("".join(lines), True)


def _patch_rust_file_remove_anchor(p: Path, *, dry_run: bool) -> str:
    if not p.exists():
        return f"SKIP: missing {p}"
    txt = _read_text(p)
    new_txt, changed = _remove_rust_anchor_block(txt)
    if not changed:
        return f"SKIP: no anchor block ({p})"
    _write_text(p, new_txt, dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {p}"


def _patch_package_sources_remove_anchor(package_dir: Path, *, dry_run: bool) -> list[str]:
    out: list[str] = []

    candidates: list[Path] = []
    for rel in ("src/lib.rs", "src/main.rs"):
        p = package_dir / rel
        if p.exists():
            candidates.append(p)

    bin_dir = package_dir / "src" / "bin"
    if bin_dir.exists():
        for p in sorted(bin_dir.rglob("*.rs")):
            if p.is_file():
                candidates.append(p)

    for p in candidates:
        out.append(_patch_rust_file_remove_anchor(p, dry_run=dry_run))
    return out


def _patch_package_manifest_remove_dep(package_dir: Path, *, dry_run: bool) -> str:
    manifest = package_dir / "Cargo.toml"
    if not manifest.exists():
        return f"SKIP: missing {manifest}"
    txt = _read_text(manifest)
    new_txt, changed = _remove_lines_marked(txt, needle=_MARKER_COMMENT)
    if not changed:
        return f"SKIP: no marked entries in {manifest}"
    _write_text(manifest, new_txt, dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {manifest}"


def _patch_cargo_config_remove_env(project_root: Path, *, dry_run: bool) -> str:
    cfg = project_root / ".cargo" / "config.toml"
    if not cfg.exists():
        return f"SKIP: missing {cfg}"
    txt = _read_text(cfg)
    new_txt, changed = _remove_lines_marked(txt, needle=_MARKER_COMMENT)
    if not changed:
        return f"SKIP: no marked entries in {cfg}"
    _write_text(cfg, new_txt, dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {cfg}"


def _remove_helper_crate(workspace_root: Path, install_dir: Path, *, dry_run: bool) -> str:
    helper_dir = workspace_root / install_dir
    marker = helper_dir / _MARKER_FILE
    if not helper_dir.exists():
        return f"SKIP: missing {helper_dir}"
    if not marker.exists():
        return f"SKIP: refusing to remove {helper_dir} (missing marker {marker})"
    if dry_run:
        return f"DRYRUN: would remove {helper_dir}"
    shutil.rmtree(helper_dir)
    return f"OK: removed {helper_dir}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall macos-sandbox-testing from a Rust/Cargo workspace")
    ap.add_argument("--project-root", required=True, type=Path, help="Workspace root containing Cargo.toml")
    ap.add_argument(
        "--install-dir",
        type=Path,
        default=Path("tools") / _HELPER_CRATE_NAME,
        help="Where the helper crate was installed (relative to --project-root)",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    install_dir: Path = args.install_dir
    if install_dir.is_absolute():
        print("ERROR: --install-dir must be a relative path", file=sys.stderr)
        sys.exit(2)

    ws = _discover_workspace(project_root)

    print(_patch_cargo_config_remove_env(project_root, dry_run=args.dry_run))
    for pkg in ws.package_dirs:
        print(_patch_package_manifest_remove_dep(pkg, dry_run=args.dry_run))
        for msg in _patch_package_sources_remove_anchor(pkg, dry_run=args.dry_run):
            print(msg)

    print(_remove_helper_crate(project_root, install_dir, dry_run=args.dry_run))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
