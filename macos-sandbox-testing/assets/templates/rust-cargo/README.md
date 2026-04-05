# macos-seatbelt-testing (template)

This is a **Cargo integration template** for the same in-process Seatbelt sandbox/tripwire used by this skill.

## How it works

- `build.rs` compiles `SandboxTestingBootstrap.c` into a static archive.
- It adds `-Wl,-force_load,<archive>` so the bootstrap object is linked even if nothing references it.
- The bootstrap’s Mach-O constructor applies the sandbox before `main`.

## How to use in a Cargo workspace

1. Copy this directory into your repo (commonly under `tools/`), e.g.:

   - `tools/macos-seatbelt-testing/`

2. Add it as a dependency of any crate that produces an executable and/or is linked by your test harness:

```toml
# <your-crate>/Cargo.toml

[dependencies]
macos-seatbelt-testing = { path = "../tools/macos-seatbelt-testing" }
```

3. Ensure it is linked by referencing it from code:

```rust
// src/main.rs and/or src/lib.rs
#[cfg(target_os = "macos")]
use macos_seatbelt_testing as _;
```

4. Run as usual:

- `cargo run`
- `cargo test`

The sandbox directory and JSONL logs will be created under:

- `<workspace>/.build/macos-sandbox-testing/<run-id>/logs/events.jsonl`

## Common knobs

- Disable: `SEATBELT_SANDBOX_DISABLE=1`
- Preserve real HOME: `SEATBELT_SANDBOX_PRESERVE_HOME=1`
- Allow networking: `SEATBELT_SANDBOX_NETWORK=allow`
