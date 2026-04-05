# Python template (venv hook + dylib preload)

This template demonstrates how to apply the same in-process Seatbelt sandbox/tripwire used by this repo to a Python test run.

## Why a dylib

Re-implementing the full SBPL profile builder and tripwire in Python is error-prone.

Instead, compile `SandboxTestingBootstrap.c` into a small macOS dynamic library and load it very early during interpreter startup.
Loading the dylib triggers the Mach-O constructor in the bootstrap, which applies the Seatbelt policy and starts the tripwire.

## Recommended workflow

Use the installer, which:

- builds `macos_sandbox_testing_bootstrap.dylib` with `clang` and `-lsandbox`,
- installs a `.pth` startup hook into the target venv’s `site-packages` so the dylib loads automatically.

```bash
python3 <skill-path>/scripts/python_install.py --project-root <PATH_TO_PY_PROJECT> --venv <PATH_TO_VENV>
python3 <skill-path>/scripts/python_verify.py --project-root <PATH_TO_PY_PROJECT> --venv <PATH_TO_VENV>
```

## Caveats

- This only applies automatically when you run Python *from the instrumented venv* (e.g. `.venv/bin/python`, `.venv/bin/pytest`).
- It can be bypassed by suppressing `site` initialization (`python -S`) or by using a different interpreter.
