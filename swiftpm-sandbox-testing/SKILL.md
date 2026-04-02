---
name: swiftpm-sandbox-testing
description: Use this skill when you need to add or verify an in-process sandbox/tripwire safety boundary for SwiftPM packages on macOS, so direct `swift run` and `swift test` cannot accidentally write outside the repo workspace. Installs a Seatbelt (`sandbox_init_with_parameters`) write guard plus optional filesystem-mutation logging into SwiftPM executable and test targets.
license: MIT
compatibility: macOS 15/26+ (Darwin). Requires SwiftPM (`swift`), Xcode Command Line Tools. Uses deprecated libsandbox Seatbelt APIs for dev/test safety; not intended as a shipped-product security guarantee.
metadata:
  version: "1.0"
  author: "swiftpm-sandbox-testing skill bundle"
  scope: "SwiftPM dev/test host-mutation prevention"
---

## Objective

Ensure that **direct** invocations of:

- `swift run` (development executable)
- `swift test` (XCTest runner)

cannot **destructively mutate host-machine data outside the repo workspace**, without relying on external wrappers.

This skill installs a **self-bootstrapping guard** into SwiftPM targets:

1. **Kernel-enforced write boundary** (Seatbelt sandbox via `sandbox_init_with_parameters`).
2. **Tripwire logging** for common filesystem mutations (dyld interposing + backtrace), writing JSONL events into a repo-local sandbox directory.
3. **Redirection of HOME/TMP** into `.build/…` to reduce accidental persistence in the real user home.

## When to use

Use when the user asks for any of the following (explicitly or implicitly):

- “SwiftPM sandbox testing”, “SwiftPM in-process sandbox”, “prevent swift test from writing to my home directory”, “host mutation guard”, “Seatbelt sandbox”, “SBPL profile”, “honeypot for tests”.

Do **not** use when:

- the project is not SwiftPM-based,
- the target environment is not macOS,
- the user needs a **supported** App Sandbox / entitlements solution for shipping binaries (different workflow).

## Fast path

### 0) Preconditions

- You are operating on a SwiftPM package root (has `Package.swift`).
- You can run commands in the repo.
- SwiftPM targets cannot contain mixed Swift+C sources; this skill installs a dedicated bootstrap C target plus a small Swift anchor file per selected target.

### 1) Install the guard into the SwiftPM package

Run:

```bash
python3 <skill-path>/scripts/install.py --package-root <PATH_TO_SWIFTPM_PACKAGE>
```

What it does:

- Uses `swift package dump-package` to enumerate targets.
- Adds a dedicated C target `SwiftPMSandboxTestingBootstrap` under `Sources/` containing `SandboxTestingBootstrap.c` (constructor-based bootstrap) and a public header.
- Patches `Package.swift` to:
  - register the bootstrap target, and
  - add it as a dependency of selected **executable**/**test** targets.
- For each selected target, writes `SwiftPMSandboxTestingAnchor.swift` to force-link the bootstrap module so its constructor runs before Swift `main` / before XCTest begins executing tests.

### 2) Verify that the boundary works (recommended)

Run:

```bash
python3 <skill-path>/scripts/verify.py --package-root <PATH_TO_SWIFTPM_PACKAGE>
```

Verification:

- Runs `swift test` with `SWIFTPM_SANDBOX_SELFTEST=1`.
- Confirms a sandbox run directory is created under `.build/swiftpm-sandbox-testing/<run-id>/`.
- Confirms a JSONL events log is produced.

### 3) Commit the changes

This guard is intended to live in the repo so that:

- developers and agents cannot bypass it by “forgetting a wrapper”, and
- new environments running `swift test` inherit the same safety boundary.

## Configuration knobs (runtime)

The injected bootstrap supports these environment variables:

### Hard switches

- `SWIFTPM_SANDBOX_DISABLE=1`
  - Disables the guard entirely (explicit escape hatch; not recommended).

- `SWIFTPM_SANDBOX_ALLOW_UNENFORCED=1`
  - If applying Seatbelt fails (e.g., profile compilation error), continue execution instead of exiting.
  - Intended only for bring-up; it weakens the guarantee.

### Mode (tripwire behavior)

- `SWIFTPM_SANDBOX_MODE=strict|redirect|report-only` (default: `strict`)
  - `strict`: userland interposition denies out-of-bounds mutations immediately (`EPERM`) and logs.
  - `redirect`: for selected operations (open/unlink/rename/mkdir/…), rewrite the target path into the sandbox root and proceed; still logs.
  - `report-only`: do not deny/redirect in userland; only log.

**Important:** Seatbelt remains the primary enforcement mechanism in all modes (unless you disable the guard). `report-only` affects the *tripwire* only.

### Workspace root

- `SWIFTPM_SANDBOX_WORKSPACE_ROOT=/abs/path`
  - Overrides auto-detection of the workspace root (otherwise derived from `cwd` by searching upward for `Package.swift`).

### Optional compatibility loosening

- `SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP=1`
  - Allows write access to common system temp locations (e.g., `/tmp`, `/private/var/folders`).
  - Default is **off** to preserve the “workspace-only writes” guarantee.
  - Enable only if required for a specific dependency/framework.

### Logging & self-test

- `SWIFTPM_SANDBOX_LOG_LEVEL=quiet|normal|verbose` (default: `normal`)
  - Controls stderr verbosity. JSONL logs are still written.

- `SWIFTPM_SANDBOX_SELFTEST=1`
  - Runs a safe self-test at startup using `sandbox_check()` (no host write required).

- `SWIFTPM_SANDBOX_TRIPWIRE=0`
  - Disables interposition logging/denying/redirecting. Seatbelt still enforces.

## Where artifacts go

- Sandbox root: `<workspace>/.build/swiftpm-sandbox-testing/<run-id>/`
- Logs: `<sandbox-root>/logs/events.jsonl`

## References

- `references/design.md` for rationale and threat model.
- `references/debugging.md` for common macOS/Seatbelt failure modes and log collection.
- `references/upstream_examples.md` for upstream code/examples (Chromium, SBPL patterns, dyld interposing).
