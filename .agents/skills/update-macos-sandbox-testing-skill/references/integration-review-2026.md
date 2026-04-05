# Integration review (refresh: 2026)

This note summarizes a 2026 refresh of integration approaches for the `macos-sandbox-testing` bootstrap (`SandboxTestingBootstrap.c`) across common macOS toolchains.

The review goal is pragmatic:

- prefer **in-process** enforcement (Seatbelt applies to the current process and inherits to children),
- avoid **wrapper-only** solutions that are easy to bypass in agentic workflows,
- be explicit about modern macOS constraints (SIP, restricted binaries, hardened runtime/library validation).

## Seatbelt APIs: current reality

- `sandbox_init_with_parameters` and `sandbox_check` are **undocumented/private** but remain present in the system sandbox library.
- Public SDK headers do not reliably declare these symbols; practical integrations typically declare them manually.

Reference mirrors that include the private prototypes:

- https://github.com/rpetrich/LightMessaging/blob/master/sandbox.h

Chromium continues to use these APIs for production sandboxing:

- https://chromium.googlesource.com/chromium/src/+/HEAD/sandbox/mac/seatbelt.cc

## SBPL profile semantics

A robust profile pattern remains:

- `(allow default)` then broad denies, then explicit allows

because rule ordering is significant and later matching rules can override earlier ones (practical “last rule wins”).

Background references:

- https://7402.org/blog/2020/macos-sandboxing-of-folder.html
- https://8ksec.io/reading-ios-sandbox-profiles/

## Toolchain-by-toolchain assessment

### SwiftPM (`swift test`, `swift run`)

**Current approach (keep):** bootstrap as a dedicated C target + per-target Swift anchor.

Why it remains the best option:

- SwiftPM targets cannot contain mixed Swift+C sources.
- The linker will often drop unused objects from static libraries; the anchor is required to force-link the constructor object.

### Xcode / `xcodebuild`

**New support (added):** compile and link `SandboxTestingBootstrap.c` into the Xcode target(s) you want constrained.

Why this is the best option:

- Xcode targets are commonly mixed-language; compiling the bootstrap into the target is straightforward.
- DYLD injection approaches are unreliable for Apple tooling on modern macOS due to SIP “restricted” binaries and hardened runtime/library validation.

See `references/xcode.md` and `assets/templates/xcode/`.

### Rust / Cargo (`cargo test`, `cargo run`)

**Current approach (keep):** helper crate compiled via `cc`, force-linked via a referenced symbol.

Alternatives considered:

- `.cargo/config.toml` linker-arg injection (fragile across target graphs and not as reviewable)

The helper crate remains the cleanest “commit-and-forget” integration.

### Go (`go test`)

**Current approach (keep):** cgo helper package + generated per-package `_test.go` blank import.

Alternatives considered:

- building custom test mains or `go test -c` workflows (too workflow-specific)
- pure-Go reimplementation of sandbox_init (not feasible; the API is C)

Tradeoff:

- requires `CGO_ENABLED=1`.

### Node / TypeScript (`npm test` / scripted entrypoints)

**Current approach (keep):** N-API addon + preload via `NODE_OPTIONS=--require=...`.

Alternatives considered:

- Node’s built-in Permission Model (`--experimental-permission`, `--allow-fs-*`, `--allow-net`, ...)

Pragmatic guidance:

- Use the Permission Model when you want cross-platform “accident prevention” and you control invocation.
- Use the Seatbelt preload when you specifically want the macOS kernel-enforced boundary.

Node Permission Model reference:

- https://nodejs.org/api/permissions.html

### Python (venv)

**Current approach (keep):** venv `site-packages` startup hook (`.pth`) imports a loader that `dlopen`s a small dylib built from `SandboxTestingBootstrap.c`.

Alternatives considered:

- `sitecustomize.py` (similar bypass story; clobbers user configuration)
- `DYLD_INSERT_LIBRARIES` (fragile; ignored for restricted binaries and complicated by Python provenance)
- replacing the venv interpreter binary with a launcher (can apply Seatbelt earlier, but loses the dyld-interpose tripwire across `exec`)

The `.pth` approach remains the best “low intrusion” compromise that preserves the tripwire logger.

## Outcome

- Keep the existing SwiftPM/Cargo/Go/Node/Python designs.
- Add first-class Xcode guidance and templates.
- Strengthen docs on modern macOS constraints around DYLD injection and SBPL ordering.
