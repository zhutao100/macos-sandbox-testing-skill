# Xcode integration templates (Swift / C / C++ / ObjC)

These templates show how to apply the **same in-process Seatbelt sandbox/tripwire** used by this skill (via `SandboxTestingBootstrap.c`) to **Xcode-built** targets.

## Why this is different from SwiftPM

SwiftPM cannot compile mixed Swift+C sources in one target, so the SwiftPM integration uses a dedicated C target plus a Swift “anchor” file to force-link the bootstrap.

In **Xcode**, mixed-language targets are normal. The simplest and most reliable integration is:

- **Add `SandboxTestingBootstrap.c` directly to the target’s Compile Sources**.

That makes the bootstrap part of the Mach-O image, and the constructor executes early.

## Files

- `SandboxTestingBootstrap.c`
  - Use the skill’s canonical copy at `assets/templates/SandboxTestingBootstrap.c`.
- `SandboxTestingAnchor.swift`
  - Optional. Use when you link the bootstrap as a **static library/framework** and need to prevent dead-stripping.
- `SandboxTestingAnchor.c`
  - Optional. Same concept as above, for C/C++/ObjC targets.

## Practical recommendations

### App / CLI target (preferred)

If your unit tests are **application-hosted** (common for iOS, and possible on macOS), add the bootstrap to the **host app/CLI** target so the sandbox is applied from the first instruction of the test host process.

### Logic tests / xctest-hosted tests (macOS)

If your tests run in Apple’s `xctest` runner process, you typically cannot inject code into `xctest` itself (SIP + hardened runtime/library validation constraints). In that case:

- Add the bootstrap to the **test bundle target**.

The sandbox will be applied when the `.xctest` bundle is loaded. This still blocks most unsafe mutations from test code, but it does not protect the very earliest runner startup before bundle load.

See `references/xcode.md` for a full integration guide.
