---
name: macos-sandbox-testing
description: Use this skill when you need an in-process macOS Seatbelt sandbox/tripwire safety boundary for local dev/test commands (SwiftPM, Cargo, etc.), so direct invocations like `swift test` or `cargo test` cannot accidentally write outside the repo workspace. Installs a Seatbelt (`sandbox_init_with_parameters`) write guard plus optional filesystem/network mutation logging into supported executables and test runners.
license: MIT
compatibility: macOS 15/26+ (Darwin). Requires python3; SwiftPM (`swift`) for Swift packages; Cargo (`cargo`) for Rust packages. Uses deprecated libsandbox Seatbelt APIs for dev/test safety; not intended as a shipped-product security guarantee.
metadata:
  version: "1.2"
  author: "macos-sandbox-testing skill bundle"
  scope: "macOS dev/test host-mutation prevention"
---

## Objective

Ensure that **direct** invocations of:

- `swift run` / `swift test` (SwiftPM)
- `cargo run` / `cargo test` (Cargo)

cannot **destructively mutate host-machine data outside the repo workspace** (and, by default, cannot perform outbound IP networking), without relying on external wrappers.

This skill installs a **self-bootstrapping guard** into supported executables/test runners:

1. **Kernel-enforced write boundary** (Seatbelt sandbox via `sandbox_init_with_parameters`).
2. **Kernel-enforced IP network guard** (Seatbelt `network-*` rules) to deny outbound connections and accidental listeners unless explicitly enabled.
3. **Tripwire logging** for common filesystem mutations and network attempts (dyld interposing + backtrace), writing JSONL events into a repo-local sandbox directory.
4. **Redirection of HOME/TMP** into `.build/…` to reduce accidental persistence in the real user home.

## Supported integrations

These are “works well, no-wrapper” integrations where the sandbox is compiled into the process that runs your code/tests:

- **SwiftPM (Swift):** `scripts/swiftpm_install.py`, `scripts/swiftpm_verify.py`, `scripts/swiftpm_uninstall.py`
- **Rust / Cargo:** `scripts/cargo_install.py`, `scripts/cargo_verify.py`, `scripts/cargo_uninstall.py`

Other ecosystems are discussed in `references/other_languages.md` (often possible, but with weaker guarantees or more awkward bootstrapping).

## When to use

Use when the user asks for any of the following (explicitly or implicitly):

- “in-process macOS sandbox”, “Seatbelt sandbox”, “prevent tests from writing to my home directory”, “host mutation guard”, “SBPL profile”, “honeypot for tests”, “SwiftPM sandbox testing”, “Cargo sandbox testing”.

Do **not** use when:

- the target environment is not macOS,
- the user needs a **supported** App Sandbox / entitlements solution for shipping binaries (different workflow).

## Fast path

### SwiftPM (Swift)

#### 0) Preconditions

- You are operating on a SwiftPM package root (has `Package.swift`).
- You can run commands in the repo.
- SwiftPM targets cannot contain mixed Swift+C sources; this skill installs a dedicated bootstrap C target plus a small Swift anchor file per selected target.

#### 1) Install the guard into the SwiftPM package

Run:

```bash
python3 <skill-path>/scripts/swiftpm_install.py --package-root <PATH_TO_SWIFTPM_PACKAGE>
```

What it does:

- Uses `swift package dump-package` to enumerate targets.
- Adds a dedicated C target under `Sources/` (default name: `SwiftPMSandboxTestingBootstrap`; suffixed if the name already exists) containing `SandboxTestingBootstrap.c` (constructor-based bootstrap) and a public header.
- Patches `Package.swift` to:
  - register the bootstrap target, and
  - add it as a dependency of selected **executable**/**test** targets.
- For each selected target, writes `SwiftPMSandboxTestingAnchor.swift` to force-link the bootstrap module so its constructor runs before Swift `main` / before XCTest begins executing tests.

#### 2) Verify that the boundary works (recommended)

Run:

```bash
python3 <skill-path>/scripts/swiftpm_verify.py --package-root <PATH_TO_SWIFTPM_PACKAGE>
```

Verification:

- Runs `swift test` with `SEATBELT_SANDBOX_SELFTEST=1`.
- Confirms a sandbox run directory is created under `.build/macos-sandbox-testing/<run-id>/`.
- Confirms a JSONL events log is produced.

#### 3) Uninstall (optional)

```bash
python3 <skill-path>/scripts/swiftpm_uninstall.py --package-root <PATH_TO_SWIFTPM_PACKAGE>
```

### Rust / Cargo

#### 0) Preconditions

- You are operating on a Cargo workspace or package root (has `Cargo.toml`).
- You can run commands in the repo.

#### 1) Install the guard into the Cargo workspace

```bash
python3 <skill-path>/scripts/cargo_install.py --project-root <PATH_TO_CARGO_WORKSPACE>
```

What it does:

- Copies a small helper crate (`macos-seatbelt-testing`) into `tools/` by default.
- Patches `Cargo.toml` files to add a **macOS-only** path dependency on the helper crate.
- Patches Rust sources to ensure the helper crate is linked so the bootstrap constructor runs before `main` / before the Rust test harness.
- Writes `.cargo/config.toml` `[env]` so Cargo-run processes consistently use the workspace root (`SEATBELT_SANDBOX_WORKSPACE_ROOT`).

#### 2) Verify that the boundary works (recommended)

```bash
python3 <skill-path>/scripts/cargo_verify.py --project-root <PATH_TO_CARGO_WORKSPACE>
```

#### 3) Uninstall (optional)

```bash
python3 <skill-path>/scripts/cargo_uninstall.py --project-root <PATH_TO_CARGO_WORKSPACE>
```

### Interpreted / VM ecosystems (notes)

For some ecosystems, “no wrapper” is hard because you don’t control the final linked binary or interpreter startup.

- **Node / TypeScript:** the kernel Seatbelt approach is possible with a native preload, but only robust when you control entrypoints (e.g. `npm test`). Consider Node’s built-in Permission Model as a practical “accident prevention” alternative. See `references/other_languages.md`.
- **Python:** you can apply Seatbelt via `sitecustomize.py` + `ctypes`, but it’s relatively easy to bypass (`python -S`, alternate runtimes, etc.). See `references/other_languages.md`.
  - See also `references/interpreted-and-vm-ecosystems.md` for more detailed notes.

## Commit the changes

This guard is intended to live in the repo so that:

- developers and agents cannot bypass it by “forgetting a wrapper”, and
- new environments running `swift test` / `cargo test` inherit the same safety boundary.

## Configuration knobs (runtime)

The injected bootstrap supports these environment variables:

### Hard switches

- `SEATBELT_SANDBOX_DISABLE=1`
  - Disables the guard entirely (explicit escape hatch; not recommended).

- `SEATBELT_SANDBOX_ALLOW_UNENFORCED=1`
  - If applying Seatbelt fails (e.g., profile compilation error), continue execution instead of exiting.
  - Intended only for bring-up; it weakens the guarantee.

### Mode (tripwire behavior)

- `SEATBELT_SANDBOX_MODE=strict|redirect|report-only` (default: `strict`)
  - `strict`: userland interposition denies out-of-bounds mutations immediately (`EPERM`) and logs.
  - `redirect`: for selected operations (open/openat/creat/unlink/rename/mkdir/rmdir/truncate), rewrite the target path into the sandbox root and proceed; still logs.
  - `report-only`: do not deny/redirect in userland; only log.

**Important:** Seatbelt remains the primary enforcement mechanism in all modes (unless you disable the guard). `report-only` affects the *tripwire* only.

### Workspace root

- `SEATBELT_SANDBOX_WORKSPACE_ROOT=/abs/path`
- `SANDBOX_WORKSPACE_ROOT=/abs/path` (alias; useful for non-Swift repos)
  - Overrides auto-detection of the workspace root (otherwise derived from `cwd` by searching upward for a project marker such as `Package.swift`, `Cargo.toml`, `package.json`, `go.mod`, `pyproject.toml`, or `.git`).

### Optional compatibility loosening

- `SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=0|1` (default: `1`)
  - Allows write access to common system temp locations (e.g., `/tmp`, `/private/var/folders`).
  - Default is **on** because SwiftPM’s XCTest runner (`swiftpm-xctest-helper`) writes a temp output file under `/var/folders/.../T` as part of `swift test` execution on macOS.
  - Set to `0` for the strict “workspace-only writes” profile (expect some `swift test` invocations to fail on macOS unless the toolchain’s temp behavior is also addressed).


### Network access (IP networking)

- `SEATBELT_SANDBOX_NETWORK=deny|localhost|allow|allowlist` (default: `deny`)
  - `deny`: block outbound IP connections and binding/listening on IP sockets.
  - `localhost`: allow loopback-only IP networking (best-effort; see debugging notes about IPv6 dual-stack edge cases).
  - `allow`: do not apply any Seatbelt network restrictions (least restrictive).
  - `allowlist`: block outbound IP connections except for a coarse allowlist (and still deny bind/listen).

- `SEATBELT_SANDBOX_NETWORK_ALLOWLIST="localhost:43128,*:443"`
  - Comma/whitespace-separated allowlist entries used when `SEATBELT_SANDBOX_NETWORK=allowlist` (or when this variable is set and `SEATBELT_SANDBOX_NETWORK` is not `allow`/`localhost`).
  - The allowlist is intentionally coarse because Seatbelt can’t match arbitrary domains; practical entries are typically:
    - `localhost:<port>` for a loopback proxy, or
    - `*:<port>` to allow any host on a specific port (use sparingly).


### Diagnostic knobs

- `SEATBELT_SANDBOX_PRESERVE_HOME=1`
  - Do not redirect `HOME` / `CFFIXED_USER_HOME` into the sandbox root.
  - Useful to surface which code paths would have written into the real home directory; typically pair with `SEATBELT_SANDBOX_MODE=report-only` or `SEATBELT_SANDBOX_MODE=redirect` so you can collect tripwire events without immediately returning `EPERM`.

### Logging & self-test

- `SEATBELT_SANDBOX_LOG_LEVEL=quiet|normal|verbose` (default: `normal`)
  - Controls stderr verbosity. JSONL logs are still written.

- `SEATBELT_SANDBOX_SELFTEST=1`
  - Runs a safe self-test at startup using `sandbox_check()` (no host write required) and, when network is expected to be restricted, a denied non-blocking `connect()`.

- `SEATBELT_SANDBOX_TRIPWIRE=0`
  - Disables interposition logging/denying/redirecting. Seatbelt still enforces.

## Where artifacts go

- Sandbox root: `<workspace>/.build/macos-sandbox-testing/<run-id>/`
- Logs: `<sandbox-root>/logs/events.jsonl`
  - The log always includes a `bootstrap` marker.
  - Additional events appear when out-of-bounds mutations are attempted (filesystem) and when denied network operations occur (e.g. `net.connect`, `net.bind`, plus an optional `selftest.network` marker when self-test is enabled).
  - Seatbelt applies to child processes, but the tripwire log is emitted only by processes that include the injected bootstrap code.

## References

- `references/design.md` for rationale and threat model.
- `references/debugging.md` for common macOS/Seatbelt failure modes and log collection.
- `references/swiftpm-mixed-language-and-bootstrap.md` for SwiftPM mixed-language context and why the bootstrap target + anchor is required.
- `references/upstream_examples.md` for upstream code/examples (Chromium, SBPL patterns, dyld interposing).
- `references/network_research.md` for a short narrative of viable network IO approaches and why Seatbelt SBPL was chosen.
- `references/other_languages.md` for porting notes and non-Swift integrations.
- `references/interpreted-and-vm-ecosystems.md` for Node/Python notes and tradeoffs.
