# Python venv integration

This skill supports a **no-wrapper** Python workflow by instrumenting a virtual environment (venv) so running Python from that venv loads a small dylib at startup which applies Seatbelt in-process.

## Preconditions

- You run tests using a Python venv.
- You invoke Python from that venv (e.g. `.venv/bin/python`, `.venv/bin/pytest`).

## Install

```bash
python3 <skill-path>/scripts/python_install.py --project-root . --venv <PATH_TO_VENV>
```

If `--venv` is omitted, the installer uses `$VIRTUAL_ENV` or falls back to `.venv`.

## Verify (recommended)

```bash
python3 <skill-path>/scripts/python_verify.py --project-root . --venv <PATH_TO_VENV>
```

## Uninstall

```bash
python3 <skill-path>/scripts/python_uninstall.py --project-root . --venv <PATH_TO_VENV>
```

## Notes / pitfalls

- The guard applies automatically only when using the instrumented venv interpreter.
- It can be bypassed by suppressing `site` initialization (`python -S`) or using a different interpreter/runtime.

## Configuration

See `references/configuration.md` (network policy, modes, logs).
