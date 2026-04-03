# Debugging and policy iteration

## 1) Confirm the guard is installed

In the target SwiftPM package:

- `Sources/SwiftPMSandboxTestingBootstrap*/` should exist (bootstrap C target; name may be suffixed if there was a conflict).
  - It should contain `.swiftpm-sandbox-testing-installed` (marker file).
- Each selected executable/test target directory should contain `SwiftPMSandboxTestingAnchor.swift` (force-link anchor).
- `swift test` should create `.build/swiftpm-sandbox-testing/<run-id>/…`.

If you used the bundled verifier:

```bash
python3 <skill-path>/scripts/verify.py --package-root <repo>
```

## 2) Find the sandbox run directory

Each run produces:

- `.build/swiftpm-sandbox-testing/<run-id>/home`
- `.build/swiftpm-sandbox-testing/<run-id>/tmp`
- `.build/swiftpm-sandbox-testing/<run-id>/logs/events.jsonl`

`events.jsonl` contains one JSON object per logged event (bootstrap marker plus any intercepted mutations).

Notes:

- The tripwire logger records **out-of-bounds** mutation attempts (and always emits a `bootstrap` marker). If your run never attempts an out-of-bounds write, the log may contain only the `bootstrap` event.
- Seatbelt applies to child processes, but the tripwire logger is **in-process**: helper binaries (e.g. SwiftPM’s `swiftpm-xctest-helper`) will not emit `events.jsonl` unless they also include the injected bootstrap code.

## 3) If `swift test` fails early

A tight write-boundary can break framework behaviors. Typical symptoms:

- sudden `EPERM` failures inside Foundation / CoreFoundation
- missing caches / preferences failures

Mitigations (in increasing order of looseness):

1. Keep Seatbelt strict, but collect more evidence:
   - `SWIFTPM_SANDBOX_LOG_LEVEL=verbose`
   - and (optionally) `SWIFTPM_SANDBOX_SELFTEST=1`

2. Use `redirect` mode so common mutation calls are rewritten into the repo-local sandbox root (still denied by Seatbelt if not rewritten):

```bash
SWIFTPM_SANDBOX_MODE=redirect swift test
```

3. Allow common system temp locations (default; required for SwiftPM XCTest on macOS):

```bash
SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP=1 swift test
```

If you need the strict “workspace-only writes” profile, set `SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP=0` (expect some `swift test` invocations to fail due to SwiftPM runner temp writes).

4. If you are bringing up the sandbox and need the process to continue even if applying Seatbelt fails (not recommended), allow running unenforced:

```bash
SWIFTPM_SANDBOX_ALLOW_UNENFORCED=1 swift test
```

## 4) System-level sandbox logs (unified logging)

Seatbelt violations usually fail with `EPERM`, and the OS may log a sandbox denial (often via `sandboxd`). Mark Rowe’s overview describes both the `EPERM` behavior and the logging / reporting behavior:

- https://bdash.net.nz/posts/sandboxing-on-macos/

On macOS, you can often inspect sandbox deny messages via unified logging, e.g.:

```bash
log stream --style syslog --predicate 'eventMessage CONTAINS "deny" AND (process == "<your-binary-name>")'
```

(Exact predicates vary by OS version and subsystem.)


## Network troubleshooting

### 1) Confirm the default “no network” behavior

By default, the injected bootstrap blocks **IP networking** (outbound connections + bind/listen) using Seatbelt `network-*` rules.

Quick check:

```bash
SWIFTPM_SANDBOX_NETWORK=deny swift test
```

If the test target attempts an outbound connection, you should see:

- a `net.connect` (or `net.bind`) event in `events.jsonl`, and/or
- a `sandboxd` denial in unified logging.

### 2) Allow localhost-only networking (loopback)

If tests need a local database or test server:

```bash
SWIFTPM_SANDBOX_NETWORK=localhost swift test
```

Notes:

- Some runtimes bind dual-stack sockets and can hit IPv4-mapped IPv6 (`::ffff:127.0.0.1`) edge cases; if you see unexpected denies, prefer explicit loopback binds (`127.0.0.1` / `::1`) in your test harness.

### 3) Allow a narrow loopback proxy hole

A common pattern is to keep the sandbox “no network”, but allow only a loopback proxy port:

```bash
SWIFTPM_SANDBOX_NETWORK=allowlist \
SWIFTPM_SANDBOX_NETWORK_ALLOWLIST="localhost:43128" \
swift test
```

(Seatbelt allowlists are intentionally coarse; it can’t directly match arbitrary domains.)

### 4) System-level logs for network denies (unified logging)

Sandbox denials often include `deny network-outbound` / `deny network-bind` messages:

```bash
log stream --style syslog --predicate 'eventMessage CONTAINS "deny network" AND (process == "<your-binary-name>")'
```

The exact predicate varies by OS version and process naming.


## 5) Interpreting `events.jsonl`

Each event includes:

- `op`: e.g. `open`, `unlink`, `rename`, `mkdir`, …
- `path`: absolute path observed
- `decision`: `allow`, `deny`, or `redirect`
- `errno`: usually `EPERM` for denials
- `bt`: a best-effort backtrace captured in-process (if available)

For triage:

1. Identify the first denied mutation (often the root cause).
2. Decide if that mutation should:
   - be fixed in code (preferable), or
   - be redirected into the sandbox root (`SWIFTPM_SANDBOX_MODE=redirect`), or
   - be explicitly allowed by policy (last resort).

## 6) Common failure modes

### A) `sandbox_init_with_parameters` fails

This can happen if:

- the process is already sandboxed (e.g., App Sandbox entitlement), or
- the profile fails to compile, or
- Apple changes behavior around the deprecated APIs.

The bootstrap fails closed by default (exit code 197) because continuing without a sandbox defeats the objective. Use `SWIFTPM_SANDBOX_ALLOW_UNENFORCED=1` only during bring-up.

### B) “Writes still appear to succeed outside the workspace”

First, ensure you are not running with the explicit escape hatches:

- `SWIFTPM_SANDBOX_DISABLE=1`
- `SWIFTPM_SANDBOX_ALLOW_UNENFORCED=1`

Note: `SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP` defaults to `1` for SwiftPM XCTest compatibility; set it to `0` if you need the strict “workspace-only writes” profile.

Then use self-test:

```bash
SWIFTPM_SANDBOX_SELFTEST=1 swift test
```

Self-test uses `sandbox_check()` to validate sandbox presence and whether `file-write*` is allowed for the original HOME vs the sandbox root.

Reference semantics:

- Chromium `sandbox_check`: https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/mac/seatbelt.cc
- `sandbox_check` return values (`0 == allowed`): https://karol-mazurek.medium.com/sandbox-validator-e760e5d88617
