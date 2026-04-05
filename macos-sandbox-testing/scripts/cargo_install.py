#!/usr/bin/env python3
"""Install macos-sandbox-testing into a Rust/Cargo workspace.

This installer adds an in-process macOS Seatbelt sandbox/tripwire to Rust test
and dev executables, so direct invocations like `cargo test` and `cargo run`
cannot accidentally write outside the repo workspace.

It does this by:
- copying the `macos-seatbelt-testing` helper crate template into the workspace,
- patching Cargo manifests to add a macOS-only path dependency, and
- patching Rust sources to ensure the helper crate is linked (so the bootstrap
  constructor runs before `main` / before the Rust test harness starts).

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import os
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
_CARGO_DEP_MARKER = "# macos-sandbox-testing"
_CARGO_ENV_MARKER = "# macos-sandbox-testing"

_HELPER_CRATE_NAME = "macos-seatbelt-testing"
_HELPER_RUST_IDENT = "macos_seatbelt_testing"

_MACOS_TARGET_DEPS_HEADER = "[target.'cfg(target_os = \"macos\")'.dependencies]"
_MACOS_TARGET_DEPS_HEADER_ALT = "[target.\"cfg(target_os = \\\"macos\\\")\".dependencies]"


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


def _relpath(from_dir: Path, to_dir: Path) -> str:
    rel = os.path.relpath(str(to_dir), str(from_dir))
    # Cargo.toml prefers forward slashes even on Windows; we're on macOS but keep it stable.
    return Path(rel).as_posix()


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
        raise RuntimeError(
            "No Cargo packages found to patch.\n"
            f"- If this is a workspace-only Cargo.toml, add [workspace] members.\n"
            f"- If this is a package, ensure it has a [package] table.\n"
            f"Manifest: {manifest}"
        )

    return CargoWorkspace(root=project_root, package_dirs=sorted(package_dirs))


def _copy_helper_crate(*, workspace_root: Path, install_dir: Path, force: bool, dry_run: bool) -> list[str]:
    dst = workspace_root / install_dir
    marker = dst / _MARKER_FILE

    if dst.exists() and not marker.exists() and not force:
        raise RuntimeError(
            f"Refusing to overwrite existing directory (missing marker {marker}).\n"
            f"Pass --force to overwrite: {dst}"
        )

    templates_root = Path(__file__).resolve().parent.parent / "assets" / "templates"
    rust_template = templates_root / "rust-cargo"
    core_bootstrap = templates_root / "SandboxTestingBootstrap.c"

    expected = {
        rust_template / "Cargo.toml": dst / "Cargo.toml",
        rust_template / "README.md": dst / "README.md",
        rust_template / "build.rs": dst / "build.rs",
        rust_template / "src" / "lib.rs": dst / "src" / "lib.rs",
        core_bootstrap: dst / "SandboxTestingBootstrap.c",
    }

    out: list[str] = []
    for src, dest in expected.items():
        if not src.exists():
            raise RuntimeError(f"Missing template file: {src}")
        if dest.exists() and not force:
            out.append(f"SKIP: already exists ({dest})")
            continue
        if dry_run:
            out.append(f"DRYRUN: would write {dest}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        out.append(f"OK: wrote {dest}")

    # Marker for safe removal.
    if marker.exists() and not force:
        out.append(f"SKIP: marker already exists ({marker})")
    else:
        _write_text(marker, f"{_HELPER_CRATE_NAME} installed\n", dry_run=dry_run)
        out.append(f"{'DRYRUN:' if dry_run else 'OK:'} wrote {marker}")

    return out


def _patch_cargo_config_env(workspace_root: Path, *, dry_run: bool) -> str:
    cfg = workspace_root / ".cargo" / "config.toml"
    desired = f'SEATBELT_SANDBOX_WORKSPACE_ROOT = {{ value = ".", relative = true, force = true }} {_CARGO_ENV_MARKER}\n'

    if cfg.exists():
        txt = _read_text(cfg)
        if "SEATBELT_SANDBOX_WORKSPACE_ROOT" in txt:
            return f"SKIP: {cfg} already sets SEATBELT_SANDBOX_WORKSPACE_ROOT"
        if "[env]" in txt:
            # Insert under the first [env] header.
            lines = txt.splitlines(keepends=True)
            for i, ln in enumerate(lines):
                if ln.strip() == "[env]":
                    insert_at = i + 1
                    while insert_at < len(lines) and (lines[insert_at].strip() == "" or lines[insert_at].lstrip().startswith("#")):
                        insert_at += 1
                    lines.insert(insert_at, desired)
                    _write_text(cfg, "".join(lines), dry_run=dry_run)
                    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {cfg}"
        # No [env] section: append.
        new_txt = txt
        if not new_txt.endswith("\n"):
            new_txt += "\n"
        new_txt += "\n[env]\n" + desired
        _write_text(cfg, new_txt, dry_run=dry_run)
        return f"{'DRYRUN:' if dry_run else 'OK:'} patched {cfg}"

    # Create new config.
    new_txt = "[env]\n" + desired
    _write_text(cfg, new_txt, dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} wrote {cfg}"


def _ensure_macos_target_dep_section(txt: str) -> tuple[str, int]:
    lines = txt.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.strip() in {_MACOS_TARGET_DEPS_HEADER, _MACOS_TARGET_DEPS_HEADER_ALT}:
            return ("".join(lines), i)
    # Not found: append a new section header at EOF.
    new_txt = txt
    if not new_txt.endswith("\n"):
        new_txt += "\n"
    if new_txt.strip():
        new_txt += "\n"
    new_txt += _MACOS_TARGET_DEPS_HEADER + "\n"
    return (new_txt, len(new_txt.splitlines()) - 1)


def _patch_package_manifest_add_dep(
    package_dir: Path, *, helper_dir: Path, dry_run: bool
) -> str:
    manifest = package_dir / "Cargo.toml"
    if not manifest.exists():
        return f"SKIP: missing {manifest}"

    txt = _read_text(manifest)
    if _HELPER_CRATE_NAME in txt:
        return f"SKIP: {manifest} already references {_HELPER_CRATE_NAME}"

    rel = _relpath(package_dir, helper_dir)
    dep_line = f'{_HELPER_CRATE_NAME} = {{ path = "{rel}" }} {_CARGO_DEP_MARKER}\n'

    new_txt, _ = _ensure_macos_target_dep_section(txt)
    lines = new_txt.splitlines(keepends=True)

    # Find the header line index again (text may have changed).
    header_line = None
    for i, ln in enumerate(lines):
        if ln.strip() in {_MACOS_TARGET_DEPS_HEADER, _MACOS_TARGET_DEPS_HEADER_ALT}:
            header_line = i
            break
    if header_line is None:
        return f"ERROR: failed to locate target macOS deps header in {manifest}"

    insert_at = header_line + 1
    while insert_at < len(lines) and (lines[insert_at].strip() == "" or lines[insert_at].lstrip().startswith("#")):
        insert_at += 1
    lines.insert(insert_at, dep_line)

    _write_text(manifest, "".join(lines), dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {manifest}"


def _insert_rust_anchor(txt: str) -> tuple[str, bool]:
    if _RUST_ANCHOR_BEGIN in txt and _RUST_ANCHOR_END in txt:
        return (txt, False)
    if f"use {_HELPER_RUST_IDENT} as _;" in txt:
        return (txt, False)

    anchor = (
        f"{_RUST_ANCHOR_BEGIN}\n"
        f"#[cfg(target_os = \"macos\")]\n"
        f"use {_HELPER_RUST_IDENT} as _;\n"
        f"{_RUST_ANCHOR_END}\n\n"
    )

    lines = txt.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "" or stripped.startswith("//") or stripped.startswith("#![") or stripped.startswith("//!"):
            i += 1
            continue
        break
    lines.insert(i, anchor)
    return ("".join(lines), True)


def _patch_rust_file(p: Path, *, dry_run: bool) -> str:
    if not p.exists():
        return f"SKIP: missing {p}"
    txt = _read_text(p)
    new_txt, changed = _insert_rust_anchor(txt)
    if not changed:
        return f"SKIP: already patched ({p})"
    _write_text(p, new_txt, dry_run=dry_run)
    return f"{'DRYRUN:' if dry_run else 'OK:'} patched {p}"


def _patch_package_sources(package_dir: Path, *, dry_run: bool) -> list[str]:
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

    if not candidates:
        out.append(f"WARN: no Rust sources found to patch under {package_dir} (expected src/lib.rs or src/main.rs)")
        return out

    for p in candidates:
        out.append(_patch_rust_file(p, dry_run=dry_run))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Install macos-sandbox-testing into a Rust/Cargo workspace")
    ap.add_argument("--project-root", required=True, type=Path, help="Workspace root containing Cargo.toml")
    ap.add_argument(
        "--install-dir",
        type=Path,
        default=Path("tools") / _HELPER_CRATE_NAME,
        help="Where to copy the helper crate (relative to --project-root)",
    )
    ap.add_argument("--force", action="store_true", help="Overwrite existing installed files when safe")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    install_dir: Path = args.install_dir
    if install_dir.is_absolute():
        print("ERROR: --install-dir must be a relative path", file=sys.stderr)
        sys.exit(2)

    ws = _discover_workspace(project_root)
    helper_dir = project_root / install_dir

    for msg in _copy_helper_crate(workspace_root=project_root, install_dir=install_dir, force=args.force, dry_run=args.dry_run):
        print(msg)

    print(_patch_cargo_config_env(project_root, dry_run=args.dry_run))

    for pkg in ws.package_dirs:
        print(_patch_package_manifest_add_dep(pkg, helper_dir=helper_dir, dry_run=args.dry_run))
        for msg in _patch_package_sources(pkg, dry_run=args.dry_run):
            print(msg)

    if args.dry_run:
        return

    print(
        "\nNext steps:\n"
        "- git status\n"
        "- cargo test (optionally with SEATBELT_SANDBOX_SELFTEST=1)\n"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
