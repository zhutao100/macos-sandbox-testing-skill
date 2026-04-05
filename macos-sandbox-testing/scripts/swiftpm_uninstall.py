#!/usr/bin/env python3
"""Remove macos-sandbox-testing from a SwiftPM package.

This uninstalls:
- The Swift anchor file from selected executable/test targets
- The bootstrap C target under `Sources/<SwiftPMSandboxTestingBootstrap*>`
- The `Package.swift` wiring (target declaration + dependencies)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from install import (
    _ANCHOR_SWIFT_NAME,
    _BOOTSTRAP_C_NAME,
    _BOOTSTRAP_H_NAME,
    _BOOTSTRAP_MARKER_NAME,
    _MANIFEST_BLOCK_BEGIN,
    _MANIFEST_BLOCK_END,
    enumerate_targets,
)


def _discover_bootstrap_target_decls(manifest: str) -> list[tuple[str, Path]]:
    """Return list of (target_name, target_rel_path)."""
    blocks = re.findall(
        rf"^[ \t]*{re.escape(_MANIFEST_BLOCK_BEGIN)}\n.*?^[ \t]*{re.escape(_MANIFEST_BLOCK_END)}\n",
        manifest,
        flags=re.MULTILINE | re.DOTALL,
    )
    out: list[tuple[str, Path]] = []
    for b in blocks:
        m_name = re.search(r'\bname\s*:\s*"([^"]+)"', b)
        if not m_name:
            continue
        name = m_name.group(1)
        m_path = re.search(r'\bpath\s*:\s*"([^"]+)"', b)
        rel_path = Path(m_path.group(1)) if m_path else (Path("Sources") / name)
        out.append((name, rel_path))
    return out


def _looks_like_installed_bootstrap_dir(dir_path: Path) -> bool:
    if (dir_path / _BOOTSTRAP_MARKER_NAME).exists():
        return True
    if not (dir_path / _BOOTSTRAP_C_NAME).exists():
        return False
    if not (dir_path / "include" / _BOOTSTRAP_H_NAME).exists():
        return False
    return True


def _skip_swift_string(src: str, i: int) -> int:
    if src.startswith('"""', i):
        j = src.find('"""', i + 3)
        if j == -1:
            return len(src)
        return j + 3

    i += 1
    while i < len(src):
        ch = src[i]
        if ch == "\\":
            i += 2
            continue
        if ch == '"':
            return i + 1
        i += 1
    return len(src)


def _find_matching(src: str, open_idx: int, open_ch: str, close_ch: str) -> int:
    if src[open_idx] != open_ch:
        raise ValueError(f"expected {open_ch!r} at {open_idx}")

    depth = 1
    i = open_idx + 1
    while i < len(src):
        ch = src[i]
        if ch == '"':
            i = _skip_swift_string(src, i)
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1

    raise ValueError(f"unterminated {open_ch}{close_ch} region starting at {open_idx}")


def _iter_target_call_spans(manifest: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for m in re.finditer(r"\.(?:target|executableTarget|testTarget)\s*\(", manifest):
        open_paren = manifest.find("(", m.start())
        close_paren = _find_matching(manifest, open_paren, "(", ")")
        out.append((m.start(), close_paren + 1))
    return out


def _extract_target_name(call_text: str) -> str | None:
    m = re.search(r'\bname\s*:\s*"([^"]+)"', call_text)
    return m.group(1) if m else None


def _remove_bootstrap_from_dependencies(call_text: str, *, bootstrap_target_names: set[str]) -> tuple[str, bool]:
    if not any(f"\"{n}\"" in call_text for n in bootstrap_target_names):
        return call_text, False

    m = re.search(r"\bdependencies\s*:\s*\[", call_text)
    if not m:
        return call_text, False

    open_idx = call_text.find("[", m.start())
    close_idx = _find_matching(call_text, open_idx, "[", "]")
    content = call_text[open_idx + 1 : close_idx]

    if "\n" in content:
        lines = content.splitlines(keepends=True)
        new_lines = [
            ln
            for ln in lines
            if "macos-sandbox-testing" not in ln and not any(f"\"{n}\"" in ln for n in bootstrap_target_names)
        ]
        new_content = "".join(new_lines)
        return call_text[: open_idx + 1] + new_content + call_text[close_idx:], True

    # One-line array: remove marker and the bootstrap dependency entry.
    trimmed = content.strip()
    trimmed = re.sub(r"/\*\s*macos-sandbox-testing\s*\*/", "", trimmed)
    for name in bootstrap_target_names:
        trimmed = trimmed.replace(f"\"{name}\"", "")
    trimmed = re.sub(r",\s*,", ",", trimmed)
    trimmed = trimmed.strip(" ,")
    new_content = f" {trimmed} " if trimmed else ""
    return call_text[: open_idx + 1] + new_content + call_text[close_idx:], True


def _patch_manifest_remove_bootstrap(
    manifest: str,
    *,
    target_names: set[str],
    bootstrap_target_names: set[str],
    remove_target_block: bool,
) -> tuple[str, int, bool]:
    removed_target_block = False
    if remove_target_block and _MANIFEST_BLOCK_BEGIN in manifest and _MANIFEST_BLOCK_END in manifest:
        manifest, n = re.subn(
            rf"^[ \t]*{re.escape(_MANIFEST_BLOCK_BEGIN)}\n.*?^[ \t]*{re.escape(_MANIFEST_BLOCK_END)}\n",
            "",
            manifest,
            flags=re.MULTILINE | re.DOTALL,
        )
        removed_target_block = n > 0

    spans = _iter_target_call_spans(manifest)
    replacements: list[tuple[int, int, str]] = []

    for start, end in spans:
        call_text = manifest[start:end]
        name = _extract_target_name(call_text)
        if not name or name not in target_names:
            continue
        if name in bootstrap_target_names:
            continue
        new_call, changed = _remove_bootstrap_from_dependencies(call_text, bootstrap_target_names=bootstrap_target_names)
        if changed:
            replacements.append((start, end, new_call))

    for start, end, new_call in sorted(replacements, key=lambda r: r[0], reverse=True):
        manifest = manifest[:start] + new_call + manifest[end:]

    return manifest, len(replacements), removed_target_block


def main() -> None:
    ap = argparse.ArgumentParser(description="Uninstall macos-sandbox-testing bootstrap from a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--only",
        choices=["all", "executable", "test"],
        default="all",
        help="Limit uninstallation to executable or test targets",
    )
    args = ap.parse_args()

    package_root: Path = args.package_root.resolve()
    if not (package_root / "Package.swift").exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    manifest_path = package_root / "Package.swift"
    manifest = manifest_path.read_text(encoding="utf-8", errors="replace")
    bootstrap_decls = _discover_bootstrap_target_decls(manifest)
    bootstrap_target_names = {n for n, _ in bootstrap_decls}
    remove_target_block = args.only == "all"

    targets = enumerate_targets(package_root)
    if args.only != "all":
        targets = [t for t in targets if t.kind == args.only]
    removed = 0

    for t in targets:
        for rel in (
            _ANCHOR_SWIFT_NAME,
            _BOOTSTRAP_C_NAME, # legacy mixed-language install
        ):
            dst = t.path / rel
            if not dst.exists():
                continue
            if args.dry_run:
                print(f"DRYRUN {t.kind} {t.name}: would remove {dst}")
                continue
            dst.unlink()
            removed += 1
            print(f"OK {t.kind} {t.name}: removed {dst}")

    # Remove bootstrap target directory.
    for name, rel_path in bootstrap_decls:
        if not remove_target_block:
            continue
        if rel_path.is_absolute():
            print(f"SKIP bootstrap {name}: absolute path in Package.swift markers: {rel_path}", file=sys.stderr)
            continue
        bootstrap_dir = (package_root / rel_path).resolve()
        if not bootstrap_dir.exists():
            continue
        if package_root not in bootstrap_dir.parents:
            print(f"SKIP bootstrap {name}: path escapes package root: {bootstrap_dir}", file=sys.stderr)
            continue
        if not _looks_like_installed_bootstrap_dir(bootstrap_dir):
            print(f"SKIP bootstrap {name}: does not look like an installed bootstrap dir: {bootstrap_dir}", file=sys.stderr)
            continue

        if args.dry_run:
            print(f"DRYRUN: would remove {bootstrap_dir}")
        else:
            shutil.rmtree(bootstrap_dir)
            print(f"OK: removed {bootstrap_dir}")

    # Patch Package.swift.
    manifest, dep_patches, removed_target_block = _patch_manifest_remove_bootstrap(
        manifest,
        target_names={t.name for t in targets},
        bootstrap_target_names=bootstrap_target_names,
        remove_target_block=remove_target_block,
    )

    if args.dry_run:
        if removed_target_block:
            print(f"DRYRUN: would patch {manifest_path}: remove bootstrap target block")
        if dep_patches:
            print(f"DRYRUN: would patch {manifest_path}: remove bootstrap dependency from {dep_patches} target(s)")
    else:
        if removed_target_block or dep_patches:
            manifest_path.write_text(manifest, encoding="utf-8")
            print(f"OK: patched {manifest_path}")
        else:
            print(f"SKIP: no changes needed in {manifest_path}")

    if not args.dry_run:
        print(f"\nRemoved {removed} file(s).")


if __name__ == "__main__":
    main()
