# Design and rationale

## Problem statement

SwiftPM development workflows (`swift run`, `swift test`) execute native code on the host. In real projects, it is easy for tests or dev runs to accidentally:

- write into `$HOME` / `~/Library/*` (caches, preferences, support)
- mutate shared developer directories (e.g. `~/Developer`, mounted volumes)
- overwrite important files via path bugs

A wrapper is not reliable in agentic workflows: the safety boundary must activate even when tools invoke `swift run` / `swift test` directly.

## Why this approach

### 1) Apply the sandbox from inside the process

The low-level macOS sandbox (“Seatbelt”, via the `sandbox(7)` APIs) cannot be attached to an already-running process. The process must apply the sandbox policy to itself, and (once applied) it cannot remove it; child processes can inherit the sandbox.

Reference overview:

- https://bdash.net.nz/posts/sandboxing-on-macos/

This fits the SwiftPM constraint: we compile the guard into targets so it runs for direct invocations.

### 2) Avoid mixed-language targets in SwiftPM

SwiftPM historically rejects targets that contain both Swift and C-family sources, so a “drop a .c file into every target” approach can fail with:

```
contains mixed language source files; feature not supported
```

Instead, the skill uses:

- a dedicated **Clang (C) target** (`SwiftPMSandboxTestingRuntime`) that contains the Seatbelt + tripwire runtime, and
- a tiny **Swift bootstrap** file injected into each executable/test target.

The bootstrap calls an exported C entrypoint (`swiftpmst_bootstrap()`) and is arranged to run before Swift `main` / before tests.

### 3) Run before `main` / before tests without wrappers

On Darwin (Mach-O), “module initialization functions” run before `main` when referenced via the `__DATA,__mod_init_func` section.

The skill’s Swift bootstrap uses section placement to register an initializer:

- Swift 6.3+: stable `@section` and `@used` attributes (SE-0492)
  - https://swift.org/blog/embedded-swift-improvements-coming-in-swift-6.3/
  - https://forums.swift.org/t/se-0492-section-placement-control/82289
- Swift 6.0–6.2: `@_section` / `@_used` behind the experimental `SymbolLinkageMarkers` feature
  - https://stackoverflow.com/questions/31865872/is-there-a-swift-equivalent-to-gcc-attribute-constructor

This arrangement keeps the package targets pure Swift, while still executing the C runtime early.

### 4) Use `sandbox_init_with_parameters` with a parameterized SBPL profile

Seatbelt policies are expressed in SBPL (a Scheme-like DSL). Parameters allow the policy to be configured at runtime (e.g., workspace root, sandbox root). Chromium maintains a production-quality macOS sandbox implementation that demonstrates:

- the `sandbox_init_with_parameters` signature and the null-terminated `key, value, …, NULL` parameter array
- `sandbox_check(getpid(), NULL, …)` as a canonical “is sandboxed” check

Useful references:

- Chromium `seatbelt.cc` (API usage and signatures): https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/mac/seatbelt.cc
- Chromium “Mac Sandbox V2 Design Doc” (SBPL + parameter example): https://chromium.googlesource.com/chromium/src/+/HEAD/sandbox/mac/seatbelt_sandbox_design.md

### 5) Redirect HOME and TMP into a repo-local honeypot

A large portion of accidental persistence comes from code that uses standard locations:

- home-relative writes (`~/Library/...`) via `NSHomeDirectory()` / `FileManager` / Preferences
- temporary file creation via `NSTemporaryDirectory()`

The injected runtime sets `HOME`, `CFFIXED_USER_HOME`, and `TMPDIR` early, and creates a repo-local sandbox root under:

- `<workspace>/.build/swiftpm-sandbox-testing/<run-id>/`

### 6) Add a tripwire logger for actionable attribution

Seatbelt violations typically fail with `EPERM` and may also be logged by the OS. A repo-local log is often more actionable for developer workflows (it’s co-located with the build artifacts and can include a backtrace).

The runtime uses dyld interposing (`__DATA,__interpose`) to observe common filesystem mutation APIs (open-for-write, rename, unlink, mkdir, …) and writes JSONL events.

References:

- dyld interposing overview + example: https://blog.darlinghq.org/2018/07/mach-o-linking-and-loading-tricks.html
- interpose macro used by projects (same `__DATA,__interpose` technique): https://github.com/facebook/xctool/blob/master/Common/dyld-interposing.h

## Threat model and non-goals

This design is intended to prevent **accidental destructive host mutations** during development/testing.

It is not a guarantee against a malicious target. In-process interposition can be bypassed (direct syscalls, alternate APIs, pre-opened descriptors, etc.). The kernel-enforced Seatbelt policy provides the primary boundary.

## SBPL profile shape and rule ordering

This skill uses a **blacklist-style** profile:

- `(allow default)` so that reads and most system operations continue to work, then
- a broad `(deny file-write*)`, and finally
- an allowlist `(allow file-write* …)` for the repo workspace and the repo-local sandbox root (and optionally system temp locations if explicitly enabled)

Empirically, **rule ordering matters**. A common working pattern is “deny, then allow exceptions” (i.e., allow rules placed after broad denies). See this write-up with concrete `sandbox-exec` examples:

- https://7402.org/blog/2020/macos-sandboxing-of-folder.html

## Safe verification (`sandbox_check`)

The runtime supports `SWIFTPM_SANDBOX_SELFTEST=1`. It uses `sandbox_check()` to verify sandbox presence and whether `file-write*` is permitted for a given path, without performing an unsafe host write.

References:

- Chromium `sandbox_check`: https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/mac/seatbelt.cc
- A small “sandbox_check validator” example and return semantics (`0 == allowed`): https://karol-mazurek.medium.com/sandbox-validator-e760e5d88617
