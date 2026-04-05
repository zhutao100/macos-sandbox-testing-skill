#!/usr/bin/env python3
"""Install macos-sandbox-testing into a SwiftPM package.

SwiftPM targets do not support *mixed* Swift + C sources in a single target.
So instead of dropping `SandboxTestingBootstrap.c` directly into each Swift
target, this installer adds:

1) A dedicated C target (default: `SwiftPMSandboxTestingBootstrap`) under `Sources/`.
   - Contains the Seatbelt + tripwire bootstrap (`SandboxTestingBootstrap.c`).
   - Exposes a tiny `msst_force_link()` symbol so Swift can force-link the
     translation unit (constructors only run if the object file is linked in).
   - Writes a marker file so `uninstall.py` can safely remove the directory.

2) A tiny Swift anchor file (`SwiftPMSandboxTestingAnchor.swift`) into each
   selected executable/test target, which imports the bootstrap module and
   references `msst_force_link()`.

The installer patches `Package.swift` to register the new bootstrap target and
add it as a dependency for the selected targets.

No third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetInfo:
    name: str
    kind: str
    path: Path


_BOOTSTRAP_TARGET_DEFAULT_NAME = "SwiftPMSandboxTestingBootstrap"
_BOOTSTRAP_C_NAME = "SandboxTestingBootstrap.c"
_BOOTSTRAP_H_NAME = "SwiftPMSandboxTestingBootstrap.h"
_BOOTSTRAP_MARKER_NAME = ".macos-sandbox-testing-installed"
_ANCHOR_SWIFT_NAME = "SwiftPMSandboxTestingAnchor.swift"
_MANIFEST_BLOCK_BEGIN = "// macos-sandbox-testing: begin"
_MANIFEST_BLOCK_END = "// macos-sandbox-testing: end"


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


def _all_target_names(package_root: Path) -> set[str]:
    data = _dump_package(package_root)
    targets = data.get("targets", [])
    out: set[str] = set()
    for t in targets:
        name = t.get("name")
        if name:
            out.add(name)
    return out


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


def _looks_like_swift_target(target_dir: Path) -> bool:
    if not target_dir.exists():
        return False
    # SwiftPM target sources can be nested.
    for p in target_dir.rglob("*.swift"):
        if p.is_file():
            return True
    return False


def _skip_swift_string(src: str, i: int) -> int:
    # Very small Swift string skipper for parsing `Package.swift`:
    # - handles "..."
    # - handles """..."""
    if src.startswith('"""', i):
        j = src.find('"""', i + 3)
        if j == -1:
            return len(src)
        return j + 3

    # Normal string literal.
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


def _discover_installed_bootstrap_target_name(manifest: str) -> str | None:
    if _MANIFEST_BLOCK_BEGIN not in manifest or _MANIFEST_BLOCK_END not in manifest:
        return None

    # Keep it simple: this marker block is inserted by this installer and is expected
    # to contain exactly one `.target(name: "...", ...)` declaration.
    m = re.search(
        rf"^[ \t]*{re.escape(_MANIFEST_BLOCK_BEGIN)}\n(.*?)^[ \t]*{re.escape(_MANIFEST_BLOCK_END)}\n",
        manifest,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        return None

    block = m.group(1)
    m_name = re.search(r'\bname\s*:\s*"([^"]+)"', block)
    return m_name.group(1) if m_name else None


def _choose_bootstrap_target_name(manifest: str, *, existing_target_names: set[str]) -> str:
    # If already installed (marker block), keep stable to avoid name churn.
    installed = _discover_installed_bootstrap_target_name(manifest)
    if installed:
        return installed

    if _BOOTSTRAP_TARGET_DEFAULT_NAME not in existing_target_names:
        return _BOOTSTRAP_TARGET_DEFAULT_NAME

    # Name conflict: pick a suffixed variant.
    for i in range(1, 1000):
        cand = f"{_BOOTSTRAP_TARGET_DEFAULT_NAME}{i}"
        if cand not in existing_target_names:
            return cand

    raise RuntimeError("could not find a free bootstrap target name (too many conflicts?)")


def _find_targets_array_span(manifest: str) -> tuple[int, int]:
    # `Package.swift` contains many `targets:` labels (e.g. product declarations),
    # so only match the package-level `targets: [...]` argument at the top level
    # of the `Package(...)` call.
    m = re.search(r"\bPackage\s*\(", manifest)
    if not m:
        raise ValueError("could not locate `Package(` in Package.swift")

    pkg_open = manifest.find("(", m.start())
    pkg_close = _find_matching(manifest, pkg_open, "(", ")")

    i = pkg_open + 1
    paren_depth = 1
    while i < pkg_close:
        ch = manifest[i]
        if ch == '"':
            i = _skip_swift_string(manifest, i)
            continue
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth -= 1
            i += 1
            continue

        if paren_depth == 1 and manifest.startswith("targets", i):
            prev = manifest[i - 1] if i > 0 else ""
            nxt = manifest[i + len("targets")] if (i + len("targets")) < len(manifest) else ""
            if (prev.isalnum() or prev == "_") or (nxt.isalnum() or nxt == "_"):
                i += 1
                continue

            j = i + len("targets")
            while j < pkg_close and manifest[j].isspace():
                j += 1
            if j >= pkg_close or manifest[j] != ":":
                i += 1
                continue

            j += 1
            while j < pkg_close and manifest[j].isspace():
                j += 1
            if j >= pkg_close or manifest[j] != "[":
                i += 1
                continue

            open_idx = j
            close_idx = _find_matching(manifest, open_idx, "[", "]")
            return open_idx, close_idx

        i += 1

    raise ValueError("could not locate package-level `targets: [` in Package.swift")


def _iter_target_call_spans(manifest: str) -> list[tuple[int, int]]:
    # Spans for `.target(...)`, `.executableTarget(...)`, `.testTarget(...)`.
    out: list[tuple[int, int]] = []
    for m in re.finditer(r"\.(?:target|executableTarget|testTarget)\s*\(", manifest):
        open_paren = manifest.find("(", m.start())
        close_paren = _find_matching(manifest, open_paren, "(", ")")
        out.append((m.start(), close_paren + 1))
    return out


def _extract_target_name(call_text: str) -> str | None:
    m = re.search(r'\bname\s*:\s*"([^"]+)"', call_text)
    return m.group(1) if m else None


def _indent_of_line(text: str, idx: int) -> str:
    line_start = text.rfind("\n", 0, idx) + 1
    m = re.match(r"[ \t]*", text[line_start:])
    return m.group(0) if m else ""


def _ensure_bootstrap_target_decl(manifest: str, *, bootstrap_target_name: str) -> tuple[str, bool]:
    if _MANIFEST_BLOCK_BEGIN in manifest and _MANIFEST_BLOCK_END in manifest:
        return manifest, False

    if f'name: "{bootstrap_target_name}"' in manifest:
        return manifest, False

    open_idx, close_idx = _find_targets_array_span(manifest)

    # Ensure the last existing target entry is comma-terminated so we can safely
    # append a new element without depending on the manifest's trailing-comma style.
    last_call: tuple[int, int] | None = None
    for start, end in _iter_target_call_spans(manifest):
        if open_idx < start < close_idx:
            if not last_call or start > last_call[0]:
                last_call = (start, end)

    if last_call:
        _, last_end = last_call
        j = last_end
        while j < close_idx and manifest[j].isspace():
            j += 1
        if j >= close_idx or manifest[j] != ",":
            manifest = manifest[:last_end] + "," + manifest[last_end:]
            close_idx += 1

    insertion = (
        f"        {_MANIFEST_BLOCK_BEGIN}\n"
        "        .target(\n"
        f"            name: \"{bootstrap_target_name}\",\n"
        f"            path: \"{(Path('Sources') / bootstrap_target_name).as_posix()}\",\n"
        "            publicHeadersPath: \"include\"\n"
        "        ),\n"
        f"        {_MANIFEST_BLOCK_END}\n"
    )
    insert_at = manifest.rfind("\n", 0, close_idx) + 1
    return manifest[:insert_at] + insertion + manifest[insert_at:], True


def _ensure_dependency_in_target_call(call_text: str, *, bootstrap_target_name: str) -> tuple[str, bool]:
    if f"\"{bootstrap_target_name}\"" in call_text:
        return call_text, False

    dep_marker_block = "/* macos-sandbox-testing */"
    dep_line_suffix = " // macos-sandbox-testing"

    m = re.search(r"\bdependencies\s*:\s*\[", call_text)
    if m:
        open_idx = call_text.find("[", m.start())
        close_idx = _find_matching(call_text, open_idx, "[", "]")
        content = call_text[open_idx + 1 : close_idx]

        if "\n" in content:
            deps_indent = _indent_of_line(call_text, m.start())
            item_indent = deps_indent + " " * 4
            insert = f"\n{item_indent}\"{bootstrap_target_name}\",{dep_line_suffix}"
            return call_text[: open_idx + 1] + insert + content + call_text[close_idx:], True

        # One-line dependencies array: keep it one-line and use a block comment marker.
        trimmed = content.strip()
        if trimmed:
            new_content = f" \"{bootstrap_target_name}\", {dep_marker_block} {trimmed} "
        else:
            new_content = f" \"{bootstrap_target_name}\" {dep_marker_block} "
        return call_text[: open_idx + 1] + new_content + call_text[close_idx:], True

    # No dependencies argument: insert after the `name:` line.
    m_name = re.search(r'\bname\s*:\s*"[^"]+"\s*,?', call_text)
    if not m_name:
        return call_text, False

    indent = _indent_of_line(call_text, m_name.start())
    insert_pos = call_text.find("\n", m_name.end())
    if insert_pos == -1:
        insert_pos = m_name.end()
        prefix = "\n"
    else:
        insert_pos += 1
        prefix = ""

    block = (
        f"{prefix}{indent}dependencies: [\n"
        f"{indent}    \"{bootstrap_target_name}\",{dep_line_suffix}\n"
        f"{indent}],\n"
    )
    return call_text[:insert_pos] + block + call_text[insert_pos:], True


def _patch_manifest_dependencies(manifest: str, *, target_names: set[str], bootstrap_target_name: str) -> tuple[str, int]:
    spans = _iter_target_call_spans(manifest)
    replacements: list[tuple[int, int, str]] = []

    for start, end in spans:
        call_text = manifest[start:end]
        name = _extract_target_name(call_text)
        if not name or name not in target_names:
            continue
        if name == bootstrap_target_name:
            continue
        new_call, changed = _ensure_dependency_in_target_call(call_text, bootstrap_target_name=bootstrap_target_name)
        if changed:
            replacements.append((start, end, new_call))

    if not replacements:
        return manifest, 0

    for start, end, new_call in sorted(replacements, key=lambda r: r[0], reverse=True):
        manifest = manifest[:start] + new_call + manifest[end:]

    return manifest, len(replacements)


def _write_file(dst: Path, *, contents: str, force: bool, dry_run: bool) -> str:
    if dst.exists() and not force:
        return f"SKIP: already exists ({dst})"
    if dry_run:
        return f"DRYRUN: would write {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(contents, encoding="utf-8")
    return f"OK: wrote {dst}"


def _install_bootstrap_target(
    package_root: Path,
    *,
    bootstrap_target_name: str,
    c_template: str,
    h_template: str,
    force: bool,
    dry_run: bool,
) -> list[str]:
    bootstrap_rel_dir = Path("Sources") / bootstrap_target_name
    bootstrap_rel_include_dir = bootstrap_rel_dir / "include"
    c_dst = package_root / bootstrap_rel_dir / _BOOTSTRAP_C_NAME
    h_dst = package_root / bootstrap_rel_include_dir / _BOOTSTRAP_H_NAME
    marker_dst = package_root / bootstrap_rel_dir / _BOOTSTRAP_MARKER_NAME
    return [
        _write_file(c_dst, contents=c_template, force=force, dry_run=dry_run),
        _write_file(h_dst, contents=h_template, force=force, dry_run=dry_run),
        _write_file(marker_dst, contents="macos-sandbox-testing installed\n", force=False, dry_run=dry_run),
    ]


def _install_anchor_into_target(target: TargetInfo, *, template: str, force: bool, dry_run: bool) -> str:
    target_dir = target.path
    if not target_dir.exists():
        return f"SKIP {target.kind} {target.name}: target path not found: {target_dir}"

    dst = target_dir / _ANCHOR_SWIFT_NAME
    if dst.exists() and not force:
        return f"SKIP {target.kind} {target.name}: anchor already installed ({dst})"

    if dry_run:
        return f"DRYRUN {target.kind} {target.name}: would write {dst}"

    dst.write_text(template, encoding="utf-8")
    return f"OK {target.kind} {target.name}: wrote {dst}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Install macos-sandbox-testing bootstrap into a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="Overwrite previously installed bootstrap sources/anchors")
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

    templates_dir = Path(__file__).resolve().parent.parent / "assets" / "templates"
    c_template = (templates_dir / _BOOTSTRAP_C_NAME).read_text(encoding="utf-8")
    h_template = (templates_dir / "swiftpm" / _BOOTSTRAP_H_NAME).read_text(encoding="utf-8")
    anchor_template = (templates_dir / "swiftpm" / _ANCHOR_SWIFT_NAME).read_text(encoding="utf-8")

    manifest_path = package_root / "Package.swift"
    manifest = manifest_path.read_text(encoding="utf-8")
    bootstrap_target_name = _choose_bootstrap_target_name(manifest, existing_target_names=_all_target_names(package_root))

    targets = enumerate_targets(package_root)
    if args.only != "all":
        targets = [t for t in targets if t.kind == args.only]
    targets = [t for t in targets if _looks_like_swift_target(t.path)]

    if not targets:
        print("No executable/test targets found. Nothing to install.")
        return

    # 1) Install bootstrap target sources.
    for msg in _install_bootstrap_target(
        package_root,
        bootstrap_target_name=bootstrap_target_name,
        c_template=c_template,
        h_template=h_template,
        force=args.force,
        dry_run=args.dry_run,
    ):
        print(msg)

    # 2) Patch Package.swift (add target + wire dependencies).
    anchor_template = anchor_template.replace("SwiftPMSandboxTestingBootstrap", bootstrap_target_name)

    manifest, added_target = _ensure_bootstrap_target_decl(manifest, bootstrap_target_name=bootstrap_target_name)
    manifest, dep_patches = _patch_manifest_dependencies(
        manifest,
        target_names={t.name for t in targets},
        bootstrap_target_name=bootstrap_target_name,
    )

    if args.dry_run:
        if added_target:
            print(f"DRYRUN: would patch {manifest_path}: add bootstrap target")
        if dep_patches:
            print(f"DRYRUN: would patch {manifest_path}: add bootstrap dependency to {dep_patches} target(s)")
    else:
        if added_target or dep_patches:
            manifest_path.write_text(manifest, encoding="utf-8")
            print(f"OK: patched {manifest_path}")
        else:
            print(f"SKIP: no changes needed in {manifest_path}")

    # 3) Install Swift anchor into selected targets (forces bootstrap to link).
    for t in targets:
        print(_install_anchor_into_target(t, template=anchor_template, force=args.force, dry_run=args.dry_run))

    if args.dry_run:
        return

    print("\nNext steps:\n- git status\n- swift test (optionally with SEATBELT_SANDBOX_SELFTEST=1)\n")


if __name__ == "__main__":
    main()
