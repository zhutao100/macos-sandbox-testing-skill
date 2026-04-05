# Runtime configuration (environment variables)

This skill is configured entirely via environment variables read by the injected bootstrap (`SandboxTestingBootstrap.c`).

## Core switches

- `SEATBELT_SANDBOX_DISABLE=1`
  - Disable the guard entirely (escape hatch; not recommended).

- `SEATBELT_SANDBOX_ALLOW_UNENFORCED=1`
  - If applying the Seatbelt sandbox fails, continue execution instead of exiting.
  - Intended only for bring-up; it weakens the guarantee.

## Workspace root

- `SEATBELT_SANDBOX_WORKSPACE_ROOT=/abs/path`
- `SANDBOX_WORKSPACE_ROOT=/abs/path` (alias; handy for non-Swift repos)
  - Overrides auto-detection of the workspace root (otherwise derived from `cwd` by searching upward for a project marker such as `Package.swift`, `Cargo.toml`, `package.json`, `go.mod`, `pyproject.toml`, or `.git`).

## Mode (tripwire behavior)

- `SEATBELT_SANDBOX_MODE=strict|redirect|report-only` (default: `strict`)
  - `strict`: interposition denies out-of-bounds mutations immediately (`EPERM`) and logs.
  - `redirect`: for selected operations (open/openat/creat/unlink/rename/mkdir/rmdir/truncate), rewrite the target path into the sandbox root and proceed; still logs.
  - `report-only`: do not deny/redirect in userland; only log.

Seatbelt remains the primary enforcement mechanism in all modes (unless you disable the guard). `report-only` affects the *tripwire* only.

## Optional compatibility loosening

- `SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=0|1` (default: `1`)
  - Allow writes to common system temp locations (e.g., `/tmp`, `/private/var/folders`).
  - Default is **on** because some toolchains (notably SwiftPM XCTest runners) write temp outputs under `/var/folders/.../T` during `swift test`.
  - Set to `0` for the strict “workspace-only writes” profile (expect some flows to fail unless toolchain temp behavior is addressed).

## Network access (IP networking)

- `SEATBELT_SANDBOX_NETWORK=deny|localhost|allow|allowlist` (default: `deny`)
  - `deny`: block outbound IP connections and binding/listening on IP sockets.
  - `localhost`: allow loopback-only IP networking (best-effort; see `references/debugging.md` for IPv6 dual-stack notes).
  - `allow`: do not apply any Seatbelt network restrictions (least restrictive).
  - `allowlist`: block outbound IP connections except for a coarse allowlist (and still deny bind/listen).

- `SEATBELT_SANDBOX_NETWORK_ALLOWLIST="localhost:43128,*:443"`
  - Comma/whitespace-separated allowlist entries used when `SEATBELT_SANDBOX_NETWORK=allowlist` (or when this variable is set and `SEATBELT_SANDBOX_NETWORK` is not `allow`/`localhost`).
  - Seatbelt allowlists are intentionally coarse (Seatbelt can’t match arbitrary domains); practical entries are typically:
    - `localhost:<port>` for a loopback proxy, or
    - `*:<port>` to allow any host on a specific port (use sparingly).

## Diagnostic knobs

- `SEATBELT_SANDBOX_PRESERVE_HOME=1`
  - Do not redirect `HOME` / `CFFIXED_USER_HOME` into the sandbox root.
  - Useful to surface which code paths would have written into the real home directory; typically pair with `SEATBELT_SANDBOX_MODE=report-only` or `redirect`.

## Logging & self-test

- `SEATBELT_SANDBOX_LOG_LEVEL=quiet|normal|verbose` (default: `normal`)
  - Controls stderr verbosity. JSONL logs are still written.

- `SEATBELT_SANDBOX_SELFTEST=1`
  - Runs a safe self-test at startup using `sandbox_check()` (no host write required) and, when network is expected to be restricted, a denied non-blocking `connect()`.

- `SEATBELT_SANDBOX_TRIPWIRE=0`
  - Disable interposition logging/denying/redirecting. Seatbelt still enforces.

## Where artifacts go

Each run produces a repo-local sandbox root:

- `<workspace>/.build/macos-sandbox-testing/<run-id>/`

Common contents:

- `home/` and `tmp/` (redirection targets when enabled)
- `logs/events.jsonl` (JSONL event log; always contains a `bootstrap` marker)

Notes:

- Seatbelt applies to child processes, but the tripwire log is emitted only by processes that include the injected bootstrap code.
