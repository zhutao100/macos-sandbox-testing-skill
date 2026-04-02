# Debugging and policy iteration

## 1) Confirm the guard is installed

In the target SwiftPM package, a typical installation includes:

- `Sources/SwiftPMSandboxTestingRuntime/` (a C/Clang target)
- `SandboxTestingBootstrap.swift` injected into each executable/test target’s source directory
- `Package.swift` patched to wire the runtime target and, when needed, enable section-placement support

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

## 3) If `swift test` fails early

A tight write-boundary can break framework behaviors. Typical symptoms:

- sudden `EPERM` failures inside Foundation / CoreFoundation
- missing caches / preferences failures

Mitigations (in increasing order of looseness):

1. Keep Seatbelt strict, but collect more evidence:
   - `SWIFTPM_SANDBOX_LOG_LEVEL=verbose`
   - and (optionally) `SWIFTPM_SANDBOX_SELFTEST=1`

2. Use `redirect` mode so common mutation calls are rewritten into the repo-local sandbox root:

```bash
SWIFTPM_SANDBOX_MODE=redirect swift test
```

3. Allow common system temp locations (this weakens the “workspace-only writes” guarantee; use only if required):

```bash
SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP=1 swift test
```

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

The runtime fails closed by default (exit code 197) because continuing without a sandbox defeats the objective. Use `SWIFTPM_SANDBOX_ALLOW_UNENFORCED=1` only during bring-up.

### B) “Writes still appear to succeed outside the workspace”

First, ensure you are not running with the explicit escape hatches:

- `SWIFTPM_SANDBOX_DISABLE=1`
- `SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP=1`

Then use self-test:

```bash
SWIFTPM_SANDBOX_SELFTEST=1 swift test
```

Self-test uses `sandbox_check()` to validate sandbox presence and whether `file-write*` is allowed for the original HOME vs the sandbox root.
