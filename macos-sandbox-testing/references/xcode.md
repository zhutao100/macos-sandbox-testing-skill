# Xcode integration (Swift / ObjC / C / C++)

This note describes how to apply the `macos-sandbox-testing` in-process Seatbelt sandbox/tripwire to **Xcode-built** projects, including:

- Swift apps and command-line tools
- ObjC / C / C++ executables
- Unit test bundles executed via Xcode or `xcodebuild test`

The core requirement is unchanged:

- `SandboxTestingBootstrap.c` must be **linked into the process you want to constrain**, so its Mach-O constructor runs before your code/tests.

## What is and is not viable on modern macOS

### Viable

- **Compile and link the bootstrap into your own targets** (app/CLI/framework/test bundle).
- Use Xcode schemes / test plans to set environment variables (workspace root override, mode, network policy).

### Usually not viable

- **DYLD_* injection** (for example `DYLD_INSERT_LIBRARIES`) into Apple-signed tooling like `xcodebuild` or `xctest`.
  - On modern macOS with SIP, Apple “restricted” binaries ignore DYLD environment variables.
  - Hardened runtime / library validation also blocks or constrains injection.

The practical implication:

- Treat `xcodebuild`/`xctest` as non-instrumentable.
- Instrument your test host **or** your `.xctest` bundle.

## Recommended pattern

### 1) Copy the integration files into your repo

Keep a stable, reviewable copy checked into your project. The easiest way is to run:

```bash
python3 <skill-path>/scripts/xcode_prepare.py --project-root .
```

By default this writes:

- `Tools/macos-sandbox-testing-xcode/SandboxTestingBootstrap.c`
- `Tools/macos-sandbox-testing-xcode/SandboxTestingAnchor.c` (optional)
- `Tools/macos-sandbox-testing-xcode/SandboxTestingAnchor.swift` (optional)
- `Tools/macos-sandbox-testing-xcode/README.md`

Manual alternative:

- bootstrap: `macos-sandbox-testing/assets/templates/SandboxTestingBootstrap.c`
- optional anchors: `macos-sandbox-testing/assets/templates/xcode/`

### 2) Add it to your target

In Xcode:

1. Drag `Tools/macos-sandbox-testing-xcode/SandboxTestingBootstrap.c` into the project navigator.
2. In the file inspector, enable **Target Membership** for:
   - your **app/CLI target** (preferred), and optionally
   - the **unit test bundle target**.
3. Confirm it appears under **Build Phases → Compile Sources**.

If the bootstrap is directly compiled as a source file of the target, no additional force-load tricks are typically required.

### 3) If you link the bootstrap via a static library/framework

If you packaged the bootstrap into a static library/framework, the linker may dead-strip the object unless a symbol is referenced.

Use one of:

- **Force load** the static library:
  - Target Build Settings → `OTHER_LDFLAGS`:
    - `-Wl,-force_load,$(SRCROOT)/path/to/libSandboxTestingBootstrap.a`
- **Reference a symbol** from the bootstrap:
  - Add `Tools/macos-sandbox-testing-xcode/SandboxTestingAnchor.c` (or the Swift variant) to the target.

## Unit tests: where to inject

Xcode can execute tests in different host processes depending on platform and test style.

### Best case: application-hosted tests

If your tests run inside a host app process (common for iOS, and possible for macOS):

- add `SandboxTestingBootstrap.c` to the **host app** target.

This applies the sandbox from the first instruction of the test host process, before the test bundle is loaded.

### macOS “logic tests”: tests hosted by `xctest`

For macOS test bundles executed by Apple’s `xctest` runner:

- add `SandboxTestingBootstrap.c` to the **test bundle** target (`.xctest`).

The sandbox will be applied when the `.xctest` bundle is loaded.

This still blocks most unsafe mutations from test code, but it does not constrain the very earliest `xctest` startup.

## Scheme configuration (recommended)

Although the bootstrap can auto-detect the workspace root by walking up from `cwd`, Xcode’s working directory is not always the repo root.

Set an explicit workspace root in the scheme:

- Scheme → Test (or Run) → Arguments → Environment Variables:
  - `SEATBELT_SANDBOX_WORKSPACE_ROOT = $(PROJECT_DIR)`

Common companion settings:

- `SEATBELT_SANDBOX_SELFTEST = 1` (validation)
- `SEATBELT_SANDBOX_LOG_LEVEL = verbose`
- `SEATBELT_SANDBOX_NETWORK = deny|allow|allowlist`

For the full set of knobs (including redirect/report-only modes and allowlists), see `references/configuration.md`.

## Xcode-specific footguns

### 1) Writes to system temp

Some Apple tooling writes small transient files to system temp paths (for example under `/private/var/folders/...`).

This skill’s default profile allows system temp writes (`SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=1`) for compatibility.

If you need a hard “workspace-only writes” policy, set:

- `SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=0`

…but expect some Xcode/SwiftPM-related flows to fail.

### 2) Non-deterministic working directories

Prefer setting `SEATBELT_SANDBOX_WORKSPACE_ROOT` explicitly rather than relying on current working directory.

## Minimal validation checklist

1. Run tests with:
   - `SEATBELT_SANDBOX_SELFTEST=1` and `SEATBELT_SANDBOX_LOG_LEVEL=verbose`.
2. Confirm that a run directory is created under:
   - `<workspace>/.build/macos-sandbox-testing/<run-id>/`
3. Confirm that `logs/events.jsonl` contains:
   - a `bootstrap` marker, and
   - (when selftest enabled) `selftest.*` markers.
