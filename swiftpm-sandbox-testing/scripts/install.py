#!/usr/bin/env python3
"""Install swiftpm-sandbox-testing into a SwiftPM package.

This installer avoids SwiftPM's historical limitation that a *single* target cannot contain
both Swift and C-family sources by:

1) Creating a dedicated C target (SwiftPM Clang target) named `SwiftPMSandboxTestingRuntime`.
2) Injecting a Swift bootstrap source file into each executable/test target that triggers the
   C runtime at load time (before Swift main / before tests).
3) Patching Package.swift to add:
   - the runtime target,
   - a dependency on it from each executable/test target,
   - and (when needed) enabling section-placement attributes.

The injected runtime applies the Seatbelt sandbox + optional tripwire logging.

Designed to be dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RUNTIME_TARGET_NAME = "SwiftPMSandboxTestingRuntime"

_MARK_RUNTIME_BEGIN = "// swiftpm-sandbox-testing:runtime-target-begin"
_MARK_RUNTIME_END = "// swiftpm-sandbox-testing:runtime-target-end"
_MARK_INLINE = "// swiftpm-sandbox-testing"


@dataclass(frozen=True)
class TargetInfo:
    name: str
    kind: str
    path: Path


def _skill_root() -> Path:
    # .../swiftpm-sandbox-testing/scripts/install.py -> skill dir is 2 parents up.
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def dump_package_json(package_root: Path) -> dict:
    proc = _run(["swift", "package", "dump-package"], cwd=package_root)
    if proc.returncode != 0:
        raise RuntimeError(f"swift package dump-package failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def enumerate_targets(package_root: Path) -> list[TargetInfo]:
    data = dump_package_json(package_root)
    out: list[TargetInfo] = []
    for t in data.get("targets", []):
        kind = t.get("type") or ""
        name = t.get("name") or ""
        if not name:
            continue
        # `path` may be absent for some targets; fall back to conventional layout.
        rel = t.get("path")
        if rel:
            p = (package_root / rel).resolve()
        else:
            if kind == "test":
                p = (package_root / "Tests" / name).resolve()
            else:
                p = (package_root / "Sources" / name).resolve()
        out.append(TargetInfo(name=name, kind=kind, path=p))
    return out


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _scan_matching(src: str, *, start: int, open_ch: str, close_ch: str) -> int:
    """Return the index of the matching closing delimiter for src[start] == open_ch.

    Handles:
    - string literals ("...") with escapes
    - line comments (//)
    - block comments (/* ... */)

    This is not a full Swift parser, but is good enough for typical Package.swift manifests.
    """
    if src[start] != open_ch:
        raise ValueError("start does not point at open delimiter")

    depth = 1
    i = start + 1
    in_str = False
    in_line_comment = False
    in_block_comment = False

    while i < len(src):
        ch = src[i]

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            if ch == "*" and i + 1 < len(src) and src[i + 1] == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue

        # not in string/comment
        if ch == '"':
            in_str = True
            i += 1
            continue
        if ch == "/" and i + 1 < len(src):
            nxt = src[i + 1]
            if nxt == "/":
                in_line_comment = True
                i += 2
                continue
            if nxt == "*":
                in_block_comment = True
                i += 2
                continue

        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1

    raise ValueError("no matching delimiter found")


def _insert_runtime_target(src: str) -> str:
    if _MARK_RUNTIME_BEGIN in src:
        return src

    m = re.search(r"\btargets\s*:\s*\[", src)
    if not m:
        raise RuntimeError("Could not locate `targets: [` in Package.swift; please add runtime target manually.")

    open_idx = src.find("[", m.end() - 1)
    close_idx = _scan_matching(src, start=open_idx, open_ch="[", close_ch="]")

    insertion = (
        "\n"
        f"        {_MARK_RUNTIME_BEGIN}\n"
        "        .target(\n"
        f"            name: \"{RUNTIME_TARGET_NAME}\",\n"
        f"            path: \"Sources/{RUNTIME_TARGET_NAME}\",\n"
        "            publicHeadersPath: \"include\"\n"
        "        ),\n"
        f"        {_MARK_RUNTIME_END}\n"
    )

    # Insert right before the closing `]` of the targets array.
    return src[:close_idx] + insertion + src[close_idx:]


def _ensure_dep_array_has_runtime(dep_array: str) -> str:
    if RUNTIME_TARGET_NAME in dep_array:
        return dep_array

    # Find the closing bracket of the dependency array.
    open_idx = dep_array.find("[")
    close_idx = _scan_matching(dep_array, start=open_idx, open_ch="[", close_ch="]")
    inner = dep_array[open_idx + 1 : close_idx].strip()

    # Determine whether list is empty.
    if not inner:
        insert = f"\n                \"{RUNTIME_TARGET_NAME}\", {_MARK_INLINE}\n            "
        return dep_array[: open_idx + 1] + insert + dep_array[close_idx:]

    insert = f",\n                \"{RUNTIME_TARGET_NAME}\", {_MARK_INLINE}\n            "
    return dep_array[:close_idx] + insert + dep_array[close_idx:]


def _ensure_swiftsettings_array_has_feature(arr: str) -> str:
    needle = 'enableExperimentalFeature("SymbolLinkageMarkers")'
    if needle in arr or "SymbolLinkageMarkers" in arr:
        return arr

    open_idx = arr.find("[")
    close_idx = _scan_matching(arr, start=open_idx, open_ch="[", close_ch="]")
    inner = arr[open_idx + 1 : close_idx].strip()

    feature = f".enableExperimentalFeature(\"SymbolLinkageMarkers\")"  # string form to avoid needing enum in older manifests

    if not inner:
        insert = f"\n                {feature}, {_MARK_INLINE}\n            "
        return arr[: open_idx + 1] + insert + arr[close_idx:]

    insert = f",\n                {feature}, {_MARK_INLINE}\n            "
    return arr[:close_idx] + insert + arr[close_idx:]


def _patch_target_call(call: str) -> str:
    # Ensure dependency on runtime target.
    if re.search(r"\bdependencies\s*:", call):
        # Rewrite the dependencies: [...] array in-place.
        m = re.search(r"\bdependencies\s*:\s*\[", call)
        if m:
            open_idx = call.find("[", m.end() - 1)
            close_idx = _scan_matching(call, start=open_idx, open_ch="[", close_ch="]")
            dep_array = call[m.start() : close_idx + 1]
            call = call[: m.start()] + _ensure_dep_array_has_runtime(dep_array) + call[close_idx + 1 :]
    else:
        # Add a dependencies argument before final `)`.
        close_paren = call.rfind(")")
        if close_paren == -1:
            return call
        insertion = f",\n            dependencies: [\"{RUNTIME_TARGET_NAME}\"], {_MARK_INLINE}\n"
        call = call[:close_paren] + insertion + call[close_paren:]

    # Ensure swiftSettings enables section placement on older toolchains.
    if re.search(r"\bswiftSettings\s*:", call):
        m = re.search(r"\bswiftSettings\s*:\s*\[", call)
        if m:
            open_idx = call.find("[", m.end() - 1)
            close_idx = _scan_matching(call, start=open_idx, open_ch="[", close_ch="]")
            arr = call[m.start() : close_idx + 1]
            call = call[: m.start()] + _ensure_swiftsettings_array_has_feature(arr) + call[close_idx + 1 :]
    else:
        close_paren = call.rfind(")")
        if close_paren != -1:
            insertion = (
                ",\n            swiftSettings: [\n"
                "                .enableExperimentalFeature(\"SymbolLinkageMarkers\"), // swiftpm-sandbox-testing\n"
                "            ]\n"
            )
            call = call[:close_paren] + insertion + call[close_paren:]

    return call


def patch_package_swift(package_root: Path, target_names: set[str]) -> None:
    pkg = package_root / "Package.swift"
    src = _read_text(pkg)

    src = _insert_runtime_target(src)

    # Patch target declarations.
    pattern = re.compile(r"\.(?:target|executableTarget|testTarget)\s*\(")
    i = 0
    while True:
        m = pattern.search(src, i)
        if not m:
            break
        call_start = m.start()
        paren_open = src.find("(", m.end() - 1)
        if paren_open == -1:
            i = m.end()
            continue
        paren_close = _scan_matching(src, start=paren_open, open_ch="(", close_ch=")")
        call_text = src[call_start : paren_close + 1]

        name_m = re.search(r"\bname\s*:\s*\"([^\"]+)\"", call_text)
        if not name_m:
            i = paren_close + 1
            continue

        name = name_m.group(1)
        if name not in target_names:
            i = paren_close + 1
            continue

        patched = _patch_target_call(call_text)
        if patched != call_text:
            src = src[:call_start] + patched + src[paren_close + 1 :]
            i = call_start + len(patched)
        else:
            i = paren_close + 1

    _write_text(pkg, src)


def ensure_runtime_target_sources(skill_root: Path, package_root: Path) -> None:
    runtime_dir = package_root / "Sources" / RUNTIME_TARGET_NAME
    include_dir = runtime_dir / "include"

    c_template = _read_text(skill_root / "assets" / "templates" / "SandboxTestingBootstrap.c")
    h_template = _read_text(skill_root / "assets" / "templates" / "SwiftPMSandboxTestingRuntime.h")

    _write_text(runtime_dir / "SandboxTestingBootstrap.c", c_template)
    _write_text(include_dir / f"{RUNTIME_TARGET_NAME}.h", h_template)


def install_bootstrap_sources(skill_root: Path, package_root: Path, targets: list[TargetInfo]) -> None:
    swift_template = _read_text(skill_root / "assets" / "templates" / "SandboxTestingBootstrap.swift")

    for t in targets:
        if t.kind not in {"executable", "test"}:
            continue
        t.path.mkdir(parents=True, exist_ok=True)
        dst = t.path / "SandboxTestingBootstrap.swift"
        _write_text(dst, swift_template)


def main() -> None:
    ap = argparse.ArgumentParser(description="Install swiftpm-sandbox-testing into a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    skill_root = _skill_root()
    package_root: Path = args.package_root.resolve()

    if not (package_root / "Package.swift").exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    targets = enumerate_targets(package_root)
    install_targets = [t for t in targets if t.kind in {"executable", "test"}]

    if not install_targets:
        print("No executable/test targets found; nothing to install.")
        return

    if args.dry_run:
        print("DRY RUN: would perform the following actions:")
        print(f"- Create/update Sources/{RUNTIME_TARGET_NAME}/ (C runtime target)")
        print(f"- Patch Package.swift to add {RUNTIME_TARGET_NAME} and wire dependencies/swiftSettings")
        for t in install_targets:
            print(f"- Write {t.path/'SandboxTestingBootstrap.swift'}")
        return

    ensure_runtime_target_sources(skill_root, package_root)
    patch_package_swift(package_root, {t.name for t in install_targets})
    install_bootstrap_sources(skill_root, package_root, install_targets)

    print("OK: swiftpm-sandbox-testing installed")
    print(f"- Runtime target: Sources/{RUNTIME_TARGET_NAME}/")
    print(f"- Patched: {package_root/'Package.swift'}")
    for t in install_targets:
        print(f"- Bootstrap: {t.path/'SandboxTestingBootstrap.swift'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
