---
name: macos-sandbox-testing
description: In-process macOS Seatbelt sandbox/tripwire safety boundary for local dev/test commands (SwiftPM, Xcode/xcodebuild, Cargo, Go, Node, Python venv), so direct invocations like `swift test`, `xcodebuild test`, or `cargo test` cannot accidentally write outside the repo workspace. Installs a Seatbelt (`sandbox_init_with_parameters`) write guard plus optional filesystem/network mutation logging into supported executables and test runners.
license: MIT
compatibility: macOS 15/26+ (Darwin). Requires python3. Optional toolchains per integration; SwiftPM (`swift`), Xcode (`xcodebuild`), Cargo (`cargo`), Go (`go` with cgo), Node (`node`/`npm` + `node-gyp`), Python venv (`python -m venv` + `clang`). Uses deprecated libsandbox Seatbelt APIs for dev/test safety; not intended as a shipped-product security guarantee.
metadata:
  version: "1.5"
  author: "macos-sandbox-testing skill bundle"
  scope: "macOS dev/test host-mutation prevention"
---

## Objective

Ensure that direct invocations of local dev/test commands (for example `swift test`, `xcodebuild test`, `cargo test`, `go test`, `npm test`) cannot **destructively mutate host-machine data outside the repo workspace** (and, by default, cannot perform outbound IP networking), without relying on wrapper scripts.

## What it does (high level)

When linked into a supported executable/test runner, the injected bootstrap:

1. Creates a repo-local sandbox root under `<workspace>/.build/macos-sandbox-testing/<run-id>/`.
2. Optionally redirects `HOME`/`TMPDIR` into that sandbox root.
3. Applies a **kernel-enforced Seatbelt** sandbox (SBPL) to deny out-of-workspace writes and (by default) deny IP networking.
4. Optionally installs a tripwire logger (dyld interposing) that records out-of-bounds filesystem/network attempts into `logs/events.jsonl`.

## Supported integrations (pick one)

These are “works well, no-wrapper” integrations where the sandbox is compiled into the process that runs your code/tests:

- **SwiftPM (Swift):** `scripts/swiftpm_install.py` / `scripts/swiftpm_verify.py` / `scripts/swiftpm_uninstall.py` → `references/swiftpm.md`
- **Xcode (`xcodebuild` / Xcode Product → Test):** `scripts/xcode_prepare.py` (+ add files to target membership) → `references/xcode.md`
- **Rust / Cargo:** `scripts/cargo_install.py` / `scripts/cargo_verify.py` / `scripts/cargo_uninstall.py` → `references/cargo.md`
- **Go:** `scripts/go_install.py` / `scripts/go_verify.py` / `scripts/go_uninstall.py` → `references/go.md`
- **Node / TypeScript:** `scripts/node_install.py` / `scripts/node_verify.py` / `scripts/node_uninstall.py` → `references/node.md`
- **Python (venv):** `scripts/python_install.py` / `scripts/python_verify.py` / `scripts/python_uninstall.py` → `references/python.md`

## Quick start

1. Open the doc for your toolchain above and run its installer script.
2. Run the verifier script (recommended) or run your tests with `SEATBELT_SANDBOX_SELFTEST=1`.
3. Commit the injected files/patches into the target repo so direct invocations stay guarded.

## When to use

Use when the user needs an in-process macOS sandbox (“Seatbelt”) boundary for local dev/test commands and wants to prevent accidental host mutations.

Do **not** use when:

- the target environment is not macOS, or
- the user needs a supported App Sandbox / entitlements solution for shipping binaries (different workflow).

## Configuration

See `references/configuration.md` for the full set of runtime knobs. Common starters:

- `SEATBELT_SANDBOX_WORKSPACE_ROOT=/abs/path` (or `SANDBOX_WORKSPACE_ROOT=/abs/path`)
- `SEATBELT_SANDBOX_SELFTEST=1`
- `SEATBELT_SANDBOX_NETWORK=deny|localhost|allow|allowlist`

## Troubleshooting / deeper background

- `references/debugging.md`
- `references/design.md`
- `references/other_languages.md` (porting notes beyond first-class integrations)
- `references/interpreted-and-vm-ecosystems.md` (Node/Python bootstrapping tradeoffs)
- `references/swiftpm-mixed-language-and-bootstrap.md` (SwiftPM-specific background)
