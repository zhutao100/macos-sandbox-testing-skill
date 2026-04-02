#!/usr/bin/env python3
"""Install SwiftPM sandbox-testing bootstrap into a SwiftPM package.

Background:
- SwiftPM targets historically cannot mix Swift with C-family sources in a single target.
  Attempting to drop a .c file into a Swift target triggers:
    "target ... contains mixed language source files; feature not supported"

This installer avoids mixed-language targets by:
1) Creating (once) a dedicated *Clang* target in the package:
     Sources/SwiftPMSandboxTestingBootstrap/
       include/SwiftPMSandboxTestingBootstrap.h
       SandboxTestingBootstrap.c
   This target contains the constructor-based bootstrap that applies the Seatbelt sandbox
   and optional mutation tripwire logging.

2) Adding that target as a dependency of each Swift executable/test target, and

3) Writing a small Swift shim into each such target:
     SwiftPMSandboxTestingBootstrap.swift
   The shim references a symbol (swiftpmst_force_link) so the bootstrap object file is
   not dropped by the linker (static libraries can otherwise be dead-stripped).

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


BOOTSTRAP_TARGET_DEFAULT = "SwiftPMSandboxTestingBootstrap"
BOOTSTRAP_DIR_DEFAULT = Path("Sources") / BOOTSTRAP_TARGET_DEFAULT
BOOTSTRAP_MARKER = ".swiftpm-sandbox-testing-installed"
DEP_MARKER = "swiftpm-sandbox-testing"


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


# -----------------------------------------------------------------------------
# Package.swift patching helpers (best-effort, marker-based)
# -----------------------------------------------------------------------------

class _SwiftScanner:
    """A minimal Swift-aware scanner to balance brackets while skipping strings/comments."""

    def __init__(self, text: str) -> None:
        self.t = text
        self.n = len(text)

    def _is_escaped(self, i: int) -> bool:
        # Count preceding backslashes.
        j = i - 1
        cnt = 0
        while j >= 0 and self.t[j] == "\\":
            cnt += 1
            j -= 1
        return (cnt % 2) == 1

    def find_matching(self, start: int, open_ch: str, close_ch: str) -> int:
        assert self.t[start] == open_ch
        depth = 1
        i = start + 1

        in_line_comment = False
        in_block_comment = 0
        in_string: str | None = None  # " or """


        while i < self.n:
            ch = self.t[i]

            # Comments
            if in_line_comment:
                if ch == "\n":
                    in_line_comment = False
                i += 1
                continue

            if in_block_comment > 0:
                if ch == "/" and i + 1 < self.n and self.t[i + 1] == "*":
                    in_block_comment += 1
                    i += 2
                    continue
                if ch == "*" and i + 1 < self.n and self.t[i + 1] == "/":
                    in_block_comment -= 1
                    i += 2
                    continue
                i += 1
                continue

            # Strings (Swift "..." and """...""")
            if in_string is not None:
                if in_string == '"""':
                    if ch == '"' and self.t[i:i+3] == '"""':
                        in_string = None
                        i += 3
                        continue
                    i += 1
                    continue
                # normal string
                if ch == '"' and not self._is_escaped(i):
                    in_string = None
                    i += 1
                    continue
                i += 1
                continue

            # Enter comment/string
            if ch == "/" and i + 1 < self.n and self.t[i + 1] == "/":
                in_line_comment = True
                i += 2
                continue
            if ch == "/" and i + 1 < self.n and self.t[i + 1] == "*":
                in_block_comment = 1
                i += 2
                continue
            if ch == '"' and self.t[i:i+3] == '"""':
                in_string = '"""'
                i += 3
                continue
            if ch == '"':
                in_string = '"'
                i += 1
                continue

            # Balance
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return i
            i += 1

        raise RuntimeError(f"unbalanced {open_ch}{close_ch} starting at index {start}")


def _split_top_level_array_items(array_body: str) -> list[tuple[str, str]]:
    """Split items in a Swift array literal body into (item, delimiter) pairs.

    delimiter includes the comma and any trailing whitespace/newlines after the comma.
    The last item has delimiter "".
    """
    t = array_body
    n = len(t)
    i = 0
    start = 0

    depth_paren = 0
    depth_brack = 0
    depth_brace = 0

    in_line_comment = False
    in_block_comment = 0
    in_string: str | None = None

    def is_escaped(pos: int) -> bool:
        j = pos - 1
        cnt = 0
        while j >= 0 and t[j] == "\\":
            cnt += 1
            j -= 1
        return (cnt % 2) == 1

    out: list[tuple[str, str]] = []

    while i < n:
        ch = t[i]

        # Comments
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        if in_block_comment > 0:
            if ch == "/" and i + 1 < n and t[i + 1] == "*":
                in_block_comment += 1
                i += 2
                continue
            if ch == "*" and i + 1 < n and t[i + 1] == "/":
                in_block_comment -= 1
                i += 2
                continue
            i += 1
            continue

        # Strings
        if in_string is not None:
            if in_string == '"""':
                if ch == '"' and t[i:i+3] == '"""':
                    in_string = None
                    i += 3
                    continue
                i += 1
                continue
            if ch == '"' and not is_escaped(i):
                in_string = None
                i += 1
                continue
            i += 1
            continue

        # Enter comment/string
        if ch == "/" and i + 1 < n and t[i + 1] == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and i + 1 < n and t[i + 1] == "*":
            in_block_comment = 1
            i += 2
            continue
        if ch == '"' and t[i:i+3] == '"""':
            in_string = '"""'
            i += 3
            continue
        if ch == '"':
            in_string = '"'
            i += 1
            continue

        # Balance
        if ch == "(":
            depth_paren += 1
        elif ch == ")":
            depth_paren -= 1
        elif ch == "[":
            depth_brack += 1
        elif ch == "]":
            depth_brack -= 1
        elif ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1

        # Top-level comma for the array
        if ch == "," and depth_paren == 0 and depth_brack == 0 and depth_brace == 0:
            # Capture delimiter: comma + trailing whitespace
            j = i + 1
            while j < n and t[j].isspace():
                j += 1
            item = t[start:i]
            delim = t[i:j]
            out.append((item, delim))
            start = j
            i = j
            continue

        i += 1

    out.append((t[start:], ""))
    return out


def _ensure_bootstrap_target_name(existing_target_names: set[str]) -> str:
    if BOOTSTRAP_TARGET_DEFAULT not in existing_target_names:
        return BOOTSTRAP_TARGET_DEFAULT
    # Avoid collisions.
    for i in range(2, 100):
        cand = f"{BOOTSTRAP_TARGET_DEFAULT}{i}"
        if cand not in existing_target_names:
            return cand
    raise RuntimeError("unable to find a free bootstrap target name")


def patch_package_swift(
    package_swift: Path,
    *,
    bootstrap_target: str,
    bootstrap_dir: Path,
    patch_targets: list[str],
    dry_run: bool,
) -> str:
    """Patch Package.swift (best-effort) using marker-based insertions.

    - Insert a new C target for the bootstrap (marked block).
    - Add bootstrap dependency to specified targets (marked element).
    """
    text = package_swift.read_text(encoding="utf-8")
    if DEP_MARKER in text and bootstrap_target in text:
        # Might already be patched, but we still verify dependencies below.
        pass

    # Locate targets: [ ... ]
    m = re.search(r"\btargets\s*:\s*\[", text)
    if not m:
        return "SKIP Package.swift: could not find 'targets: [' (manual edit required)"

    open_idx = m.end() - 1  # points to '['
    scanner = _SwiftScanner(text)
    close_idx = scanner.find_matching(open_idx, "[", "]")

    body = text[open_idx + 1 : close_idx]
    items = _split_top_level_array_items(body)

    existing_names: set[str] = set()
    for it, _delim in items:
        nm = re.search(r'\bname\s*:\s*"([^"]+)"', it)
        if nm:
            existing_names.add(nm.group(1))

    # 1) Insert bootstrap target entry if missing
    if bootstrap_target not in existing_names:
        # Derive indentation from the first non-empty item.
        indent = "        "
        for it, _delim in items:
            mm = re.search(r"\n([ \t]*)\.", it)
            if mm:
                indent = mm.group(1)
                break

        block = (
            f"\n{indent}// {DEP_MARKER}:begin\n"
            f"{indent}.target(\n"
            f"{indent}    name: \"{bootstrap_target}\",\n"
            f"{indent}    path: \"{bootstrap_dir.as_posix()}\"\n"
            f"{indent}),\n"
            f"{indent}// {DEP_MARKER}:end\n"
        )
        items.insert(0, (block, ""))

    # 2) Patch dependencies for specified targets
    patch_set = set(patch_targets)

    def patch_one_entry(entry: str) -> str:
        nm = re.search(r'\bname\s*:\s*"([^"]+)"', entry)
        if not nm:
            return entry
        name = nm.group(1)
        if name not in patch_set:
            return entry
        if name == bootstrap_target:
            return entry
        if f'"{bootstrap_target}"' in entry or bootstrap_target in entry and DEP_MARKER in entry:
            return entry

        # If dependencies: [...] exists, append element.
        dm = re.search(r"\bdependencies\s*:\s*\[", entry)
        if dm:
            deps_open = dm.end() - 1  # '['
            # Find matching ] within the entry slice. Use local scanner.
            local = _SwiftScanner(entry)
            deps_close = local.find_matching(deps_open, "[", "]")
            deps_body = entry[deps_open + 1 : deps_close]

            has_newline = "\n" in deps_body
            # Determine whether array is empty / ends with comma
            pre = deps_body.rstrip()
            needs_comma = bool(pre) and not pre.endswith(",")
            # Determine element indentation
            elem_indent = None
            if has_newline:
                mm = re.search(r"\n([ \t]*)\S", deps_body)
                if mm:
                    elem_indent = mm.group(1)
            if elem_indent is None:
                # Fallback: align with "dependencies:" column
                mm = re.search(r"\n([ \t]*)dependencies\s*:", entry[:deps_open])
                elem_indent = (mm.group(1) + "    ") if mm else "        "

            sep = "\n" if has_newline else " "
            insertion = ""
            if deps_body.strip() == "":
                insertion = f"{sep}{elem_indent}\"{bootstrap_target}\" /* {DEP_MARKER} */"
            else:
                insertion = f"{',' if needs_comma else ''}{sep}{elem_indent}\"{bootstrap_target}\" /* {DEP_MARKER} */"

            return entry[:deps_close] + insertion + entry[deps_close:]

        # No dependencies argument: insert one after name: "..."
        # Find indentation of the name: line for nice formatting.
        name_line_indent = None
        mm = re.search(r"\n([ \t]*)name\s*:\s*\"" + re.escape(name) + r"\"", entry)
        if mm:
            name_line_indent = mm.group(1)
        else:
            # Single-line entry: infer from leading whitespace.
            m0 = re.match(r"([ \t]*)\.", entry)
            name_line_indent = (m0.group(1) + "    ") if m0 else "        "

        # Insert after the name argument's trailing comma (or end of name string).
        after = nm.end()
        # Try to find the comma that terminates the name argument.
        comma_idx = entry.find(",", after)
        if comma_idx == -1:
            comma_idx = after
            comma = ","
        else:
            comma = ""
        insertion = (
            f"{comma}\n{name_line_indent}dependencies: [\"{bootstrap_target}\" /* {DEP_MARKER} */],"
        )
        return entry[:comma_idx + 1] + insertion + entry[comma_idx + 1 :]

    new_items: list[tuple[str, str]] = []
    for it, delim in items:
        patched = patch_one_entry(it)
        new_items.append((patched, delim))

    new_body = "".join(it + delim for it, delim in new_items)
    new_text = text[: open_idx + 1] + new_body + text[close_idx:]

    if dry_run:
        return "DRYRUN Package.swift: would patch targets/dependencies"
    package_swift.write_text(new_text, encoding="utf-8")
    return "OK Package.swift: patched targets/dependencies"


# -----------------------------------------------------------------------------
# Installation steps
# -----------------------------------------------------------------------------

def _ensure_bootstrap_sources(
    package_root: Path,
    *,
    bootstrap_target: str,
    force: bool,
    dry_run: bool,
) -> str:
    dest_dir = package_root / "Sources" / bootstrap_target
    c_src = dest_dir / "SandboxTestingBootstrap.c"
    h_dir = dest_dir / "include"
    h_src = h_dir / "SwiftPMSandboxTestingBootstrap.h"
    marker = dest_dir / BOOTSTRAP_MARKER

    template_c = (Path(__file__).resolve().parent.parent / "assets" / "templates" / "SandboxTestingBootstrap.c").read_text(
        encoding="utf-8"
    )
    template_h = (
        Path(__file__).resolve().parent.parent / "assets" / "templates" / "SwiftPMSandboxTestingBootstrap.h"
    ).read_text(encoding="utf-8")

    if dry_run:
        return f"DRYRUN bootstrap: would create {dest_dir} (C target)"

    dest_dir.mkdir(parents=True, exist_ok=True)
    h_dir.mkdir(parents=True, exist_ok=True)

    if c_src.exists() and not force:
        pass
    else:
        c_src.write_text(template_c, encoding="utf-8")

    if h_src.exists() and not force:
        pass
    else:
        h_src.write_text(template_h, encoding="utf-8")

    marker.write_text("swiftpm-sandbox-testing installed\n", encoding="utf-8")
    return f"OK bootstrap: ensured {dest_dir}"


def install_into_target(
    target: TargetInfo,
    *,
    template_swift: str,
    force: bool,
    dry_run: bool,
) -> str:
    target_dir = target.path
    if not target_dir.exists():
        return f"SKIP {target.kind} {target.name}: target path not found: {target_dir}"

    if not _looks_like_swift_target(target_dir):
        return f"SKIP {target.kind} {target.name}: target has no Swift sources (not patching)"

    dst = target_dir / "SwiftPMSandboxTestingBootstrap.swift"
    if dst.exists() and not force:
        return f"SKIP {target.kind} {target.name}: already installed ({dst.name})"

    if dry_run:
        return f"DRYRUN {target.kind} {target.name}: would write {dst}"

    dst.write_text(template_swift, encoding="utf-8")
    return f"OK {target.kind} {target.name}: wrote {dst}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Install swiftpm-sandbox-testing bootstrap into a SwiftPM package")
    ap.add_argument("--package-root", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="Overwrite existing installed files/patches")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--only",
        choices=["all", "executable", "test"],
        default="all",
        help="Limit installation to executable or test targets",
    )

    args = ap.parse_args()
    package_root: Path = args.package_root.resolve()

    package_swift = package_root / "Package.swift"
    if not package_swift.exists():
        print(f"ERROR: Package.swift not found under {package_root}", file=sys.stderr)
        sys.exit(2)

    targets = enumerate_targets(package_root)
    if args.only != "all":
        targets = [t for t in targets if t.kind == args.only]

    if not targets:
        print("No executable/test targets found. Nothing to install.")
        return

    # Determine a non-colliding bootstrap target name.
    dump = _dump_package(package_root)
    existing = {d.get("name") for d in dump.get("targets", []) if isinstance(d, dict) and d.get("name")}
    bootstrap_target = _ensure_bootstrap_target_name(set(existing))

    template_swift = (
        Path(__file__).resolve().parent.parent / "assets" / "templates" / "SwiftPMSandboxTestingLink.swift"
    ).read_text(encoding="utf-8")
    template_swift = template_swift.replace("import SwiftPMSandboxTestingBootstrap", f"import {bootstrap_target}")

    # Patch sources
    print(_ensure_bootstrap_sources(package_root, bootstrap_target=bootstrap_target, force=args.force, dry_run=args.dry_run))
    for t in targets:
        print(install_into_target(t, template_swift=template_swift, force=args.force, dry_run=args.dry_run))

    # Patch manifest (best effort).
    patched = patch_package_swift(
        package_swift,
        bootstrap_target=bootstrap_target,
        bootstrap_dir=(Path("Sources") / bootstrap_target),
        patch_targets=[t.name for t in targets if _looks_like_swift_target(t.path)],
        dry_run=args.dry_run,
    )
    print(patched)

    if args.dry_run:
        return

    print("\nNext steps:\n- git status\n- swift test (optionally with SWIFTPM_SANDBOX_SELFTEST=1)\n")


if __name__ == "__main__":
    main()
