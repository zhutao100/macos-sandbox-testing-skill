# Cargo integration (Rust)

This skill supports a **no-wrapper** Cargo workflow by patching your workspace so the sandbox bootstrap is **linked into** your Rust executables and test harnesses (`cargo run`, `cargo test`).

## Install

From the Cargo workspace/package root (contains `Cargo.toml`):

```bash
python3 <skill-path>/scripts/cargo_install.py --project-root .
```

## Verify (recommended)

```bash
python3 <skill-path>/scripts/cargo_verify.py --project-root .
```

## Uninstall

```bash
python3 <skill-path>/scripts/cargo_uninstall.py --project-root .
```

## What changes to expect

- Installs a helper crate (default: `tools/macos-seatbelt-testing/`) that compiles/links `SandboxTestingBootstrap.c`.
- Patches `Cargo.toml` to add a macOS-only path dependency on the helper crate.
- Patches Rust sources to ensure the helper crate is linked (so the constructor runs).
- Writes `.cargo/config.toml` `[env]` to set `SEATBELT_SANDBOX_WORKSPACE_ROOT` consistently.

## Configuration

See `references/configuration.md` (network policy, modes, logs).
