"""Load the macos-sandbox-testing bootstrap dylib as early as possible.

This module is intended to be imported via a `.pth` file installed into a Python venv.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path


def _is_disabled() -> bool:
    v = os.environ.get("SEATBELT_SANDBOX_DISABLE")
    return bool(v and v != "0")


if not _is_disabled():
    dylib = Path(__file__).with_name("macos_sandbox_testing_bootstrap.dylib")
    # Loading the dylib triggers the Mach-O constructor in SandboxTestingBootstrap.c.
    ctypes.CDLL(str(dylib), mode=ctypes.RTLD_LOCAL)
