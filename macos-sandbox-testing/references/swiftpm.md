# SwiftPM integration (Swift)

This skill supports a **no-wrapper** SwiftPM workflow by patching your package so the sandbox bootstrap is **linked into** the Swift executables and test runners you already invoke directly (`swift run`, `swift test`).

## Install

From the SwiftPM package root (contains `Package.swift`):

```bash
python3 <skill-path>/scripts/swiftpm_install.py --package-root .
```

To limit installation:

```bash
# Only `swift test` (recommended starter)
python3 <skill-path>/scripts/swiftpm_install.py --package-root . --only test

# Only `swift run` / executable targets
python3 <skill-path>/scripts/swiftpm_install.py --package-root . --only executable
```

## Verify (recommended)

```bash
python3 <skill-path>/scripts/swiftpm_verify.py --package-root .
```

This runs `swift test` with `SEATBELT_SANDBOX_SELFTEST=1` and checks that:

- `.build/macos-sandbox-testing/<run-id>/logs/events.jsonl` exists
- the log contains a `bootstrap` marker

## Uninstall

```bash
python3 <skill-path>/scripts/swiftpm_uninstall.py --package-root .
```

## Notes / pitfalls

- SwiftPM targets cannot reliably mix Swift + C sources, so this integration installs a dedicated C bootstrap target plus a per-target Swift “anchor” file to force-link the constructor object.
- For background on that constraint, see `references/swiftpm-mixed-language-and-bootstrap.md`.

## Configuration

See `references/configuration.md` (workspace root, network policy, modes, logs).
