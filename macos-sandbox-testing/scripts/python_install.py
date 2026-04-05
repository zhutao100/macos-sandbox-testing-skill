#!/usr/bin/env python3
"""Install macos-sandbox-testing into a Python venv (macOS).

Goal: when running Python from the instrumented venv (e.g. `.venv/bin/python`,
`.venv/bin/pytest`), the interpreter process preloads a small dylib that links
`SandboxTestingBootstrap.c`, applying the same in-process Seatbelt sandbox/
tripwire used by SwiftPM and Cargo integrations.

We avoid clobbering user `sitecustomize.py` by installing:

- `macos_sandbox_testing.pth`  (startup hook)
- `macos_sandbox_testing_bootstrap.py` (loads dylib)
- `macos_sandbox_testing_bootstrap.dylib` (compiled from SandboxTestingBootstrap.c)

No third-party dependencies (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


_INSTALL_MARKER = "macos_sandbox_testing.installed.json"
_PTH_NAME = "macos_sandbox_testing.pth"
_PY_NAME = "macos_sandbox_testing_bootstrap.py"
_DYLIB_NAME = "macos_sandbox_testing_bootstrap.dylib"


@dataclass(frozen=True)
class InstallRecord:
    installed_files: list[str]


def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _write_text(p: Path, s: str, *, dry_run: bool) -> None:
    if dry_run:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _dump_json(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _detect_venv(project_root: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    env = os.environ.get("VIRTUAL_ENV")
    if env:
        return Path(env)
    cand = project_root / ".venv"
    if cand.exists():
        return cand
    raise RuntimeError("No venv specified. Pass --venv, or activate a venv, or create .venv/")


def _site_packages(venv_python: Path) -> Path:
    code = (
        "import json, sysconfig; "
        "print(json.dumps(sysconfig.get_paths()[\"purelib\"]))"
    )
    # Use `-S` to avoid triggering venv/user site hooks while we probe paths.
    proc = _run([str(venv_python), "-S", "-c", code])
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to query site-packages: {proc.stderr.strip()}")
    raw = proc.stdout.strip()
    try:
        p = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse site-packages output: {raw}") from e
    if not isinstance(p, str) or not p:
        raise RuntimeError(f"Unexpected site-packages path: {raw}")
    return Path(p)


def _compile_dylib(*, out: Path, bootstrap_c: Path, dry_run: bool) -> None:
    if dry_run:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "clang",
        "-dynamiclib",
        "-std=c11",
        "-O2",
        "-fPIC",
        "-o",
        str(out),
        str(bootstrap_c),
        "-lsandbox",
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            "Failed to compile bootstrap dylib.\n"
            f"Command: {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Install macos-sandbox-testing into a Python venv")
    ap.add_argument("--project-root", required=True, type=Path, help="Project root (used for workspace root detection)")
    ap.add_argument("--venv", type=Path, help="Path to venv directory (defaults: $VIRTUAL_ENV or .venv)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    project_root: Path = args.project_root.resolve()
    venv_dir = _detect_venv(project_root, args.venv)
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        raise RuntimeError(f"venv python not found: {venv_python}")

    sp = _site_packages(venv_python)
    marker = sp / _INSTALL_MARKER

    if marker.exists():
        raise RuntimeError(f"Already installed (marker exists): {marker}")

    templates_root = Path(__file__).resolve().parent.parent / "assets" / "templates" / "python-venv"
    bootstrap_py = templates_root / _PY_NAME
    bootstrap_pth = templates_root / _PTH_NAME

    core_bootstrap = Path(__file__).resolve().parent.parent / "assets" / "templates" / "SandboxTestingBootstrap.c"
    if not core_bootstrap.exists():
        raise RuntimeError(f"Missing bootstrap C template: {core_bootstrap}")

    # Install files.
    dest_py = sp / _PY_NAME
    dest_pth = sp / _PTH_NAME
    dest_dylib = sp / _DYLIB_NAME

    _write_text(dest_py, _read_text(bootstrap_py), dry_run=args.dry_run)
    print(f"{'DRYRUN:' if args.dry_run else 'OK:'} wrote {dest_py}")

    _write_text(dest_pth, _read_text(bootstrap_pth), dry_run=args.dry_run)
    print(f"{'DRYRUN:' if args.dry_run else 'OK:'} wrote {dest_pth}")

    if args.dry_run:
        print(f"DRYRUN: would compile {dest_dylib}")
    else:
        tmp = project_root / ".build" / "macos-sandbox-testing" / "python" / _DYLIB_NAME
        _compile_dylib(out=tmp, bootstrap_c=core_bootstrap, dry_run=False)
        shutil.copy2(tmp, dest_dylib)
        print(f"OK: wrote {dest_dylib}")

    rec = InstallRecord(installed_files=[_PY_NAME, _PTH_NAME, _DYLIB_NAME, _INSTALL_MARKER])
    _write_text(marker, _dump_json(rec.__dict__), dry_run=args.dry_run)
    print(f"{'DRYRUN:' if args.dry_run else 'OK:'} wrote {marker}")

    if args.dry_run:
        return

    print(
        "\nNext steps:\n"
        f"- Run tests from this venv: {venv_python}\n"
        "- For validation: SEATBELT_SANDBOX_SELFTEST=1 SEATBELT_SANDBOX_LOG_LEVEL=verbose python -c 'print(1)'\n"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
