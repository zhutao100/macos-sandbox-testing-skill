# Porting the in-process Seatbelt sandbox/tripwire to other languages (macOS 15/26+)

This repo’s core mechanism (the `SandboxTestingBootstrap.c` template) is **language-agnostic**.

## Mechanism recap (what the bootstrap actually does)

`SandboxTestingBootstrap.c` uses a Mach-O load-time initializer (`__attribute__((constructor))`) to execute *before* `main` / before test frameworks run. In that constructor it:

1. **Derives a workspace root** by walking up from `cwd` looking for project markers (e.g. `Package.swift`, `Cargo.toml`, `package.json`, `go.mod`, `pyproject.toml`, `.git`).
2. Creates a repo-local sandbox directory under:
   - `<workspace>/.build/macos-sandbox-testing/<run-id>/`
3. Optionally **redirects HOME/TMPDIR** into that sandbox directory.
4. Applies a **kernel-enforced Seatbelt sandbox** using `sandbox_init_with_parameters` with an SBPL profile that:
   - denies `file-write*` except under the workspace and sandbox roots (and optionally common system temp locations),
   - optionally denies IP networking (`network-outbound`, `network-bind`, `network-inbound`) except for loopback / allowlists.
5. Installs a **tripwire** via `__DATA,__interpose` for common mutation syscalls (`open*`, `unlink`, `rename`, `mkdir`, `truncate`, `connect`, `bind`, …), emitting deterministic JSONL logs.

SwiftPM adds one extra wrinkle: because Clang targets are usually linked as static libraries, the Swift side must “anchor” a symbol so the bootstrap object file is not dropped by the linker. Other build systems have an equivalent “force link / force load” problem.

## Applicability matrix

| Ecosystem | Viable “no wrapper” approach | Robustness | Notes |
|---|---|---:|---|
| **Rust / Cargo** | Link `SandboxTestingBootstrap.c` into every bin/test harness; ensure the object is **force-loaded** | High | Practical and clean. See template below. |
| **C/C++/ObjC** | Compile and link the bootstrap translation unit into your executable/test binary | High | Use `-Wl,-force_load` when linking via static libs. |
| **Xcode (Swift/C/C++/ObjC)** | Add `SandboxTestingBootstrap.c` to the Xcode target(s) you want sandboxed | High | See `references/xcode.md` for guidance on test hosts and scheme env vars. |
| **Go** | cgo linking of the bootstrap + Go `init()` to ensure linkage | Medium | Works, but cgo introduces toolchain constraints. |
| **Node / TypeScript** | Load a native addon very early (preload) that links the bootstrap | Medium | Works for `npm test`/scripts. “Direct `node`” can bypass unless you control entrypoints. |
| **Python** | `sitecustomize.py` + `ctypes` to call `sandbox_init_with_parameters` early | Low–Medium | Can be bypassed with `python -S`, different site behavior, or alt runtimes. |

## Best general solution

For modern macOS dev/test safety, the best reusable solution is:

- **Reuse `SandboxTestingBootstrap.c` unchanged**, and
- **compile/link it directly into the process you want to constrain** (test runner and any dev executable), so the sandbox is applied *in-process* and inherited by child processes.

The only integration-specific work is ensuring the bootstrap’s translation unit is *not* dead-stripped.

## Concrete workflow templates

This repo includes ready-to-install templates and scripts for common ecosystems:

- SwiftPM: `references/swiftpm.md` and `assets/templates/swiftpm/`
- Xcode: `references/xcode.md`, `scripts/xcode_prepare.py`, and `assets/templates/xcode/`
- Cargo: `references/cargo.md` and `assets/templates/rust-cargo/`
- Go: `references/go.md` and `assets/templates/go/`
- Node/TypeScript: `references/node.md` and `assets/templates/node-typescript/`
- Python (venv): `references/python.md` and `assets/templates/python-venv/`

## Porting checklist (new ecosystem)

When adding a new integration, the core constraints are the same:

1. **Get the bootstrap translation unit linked** into the process that runs your tests/dev commands.
2. Ensure it is not dead-stripped (use force-load flags or an “anchor” reference).
3. Set `SEATBELT_SANDBOX_WORKSPACE_ROOT` explicitly if the toolchain’s working directory is not the repo root.
4. Validate with `SEATBELT_SANDBOX_SELFTEST=1` and confirm:
   - `<workspace>/.build/macos-sandbox-testing/<run-id>/logs/events.jsonl` exists
   - it contains a `bootstrap` marker


## “sandbox-exec” wrappers are not the goal here

`sandbox-exec` (and wrapper tools built atop it) can still work on modern macOS, but it is:

- deprecated,
- wrapper-based (easy to bypass), and
- more fragile for developer workflows that expect *direct* invocations.

For the “agent-safe-by-construction” requirement, embedding the sandbox into the process remains the most reliable pattern.
