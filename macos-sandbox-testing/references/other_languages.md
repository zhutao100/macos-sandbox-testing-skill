# Porting the in-process Seatbelt sandbox/tripwire to other languages (macOS 15/26+)

This repo’s core mechanism (the `SandboxTestingBootstrap.c` template) is **not Swift-specific**.

SwiftPM is a primary integration target because it is common and has a strong need for “no-wrapper” safety on `swift run` / `swift test`. Cargo/Rust is also a good fit. The same pattern can be reused in other language ecosystems.

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
| **Go** | cgo linking of the bootstrap + Go `init()` to ensure linkage | Medium | Works, but cgo introduces toolchain constraints. |
| **Node / TypeScript** | Load a native addon very early (preload) that links the bootstrap | Medium | Works for `npm test`/scripts. “Direct `node`” can bypass unless you control entrypoints. |
| **Python** | `sitecustomize.py` + `ctypes` to call `sandbox_init_with_parameters` early | Low–Medium | Can be bypassed with `python -S`, different site behavior, or alt runtimes. |

## Best general solution

For modern macOS dev/test safety, the best reusable solution is:

- **Reuse `SandboxTestingBootstrap.c` unchanged**, and
- **compile/link it directly into the process you want to constrain** (test runner and any dev executable), so the sandbox is applied *in-process* and inherited by child processes.

The only integration-specific work is ensuring the bootstrap’s translation unit is *not* dead-stripped.

## Concrete workflow templates

This repo includes templates under `assets/templates/` for Rust/Cargo, Go, Node/TypeScript, and Python (venv).

### Rust / Cargo (recommended)

Template directory:

- `assets/templates/rust-cargo/`

What it provides:

- A small helper crate (`macos-seatbelt-testing`) that:
  - compiles `SandboxTestingBootstrap.c` via the `cc` build dependency,
  - uses the macOS linker’s `-Wl,-force_load,<lib>` to ensure the constructor object is linked,
  - links `-lsandbox` explicitly.

Integration (recommended, no copy/paste):

- Run the installer:

```bash
python3 <skill-path>/scripts/cargo_install.py --project-root <PATH_TO_CARGO_WORKSPACE>
```

- Verify:

```bash
python3 <skill-path>/scripts/cargo_verify.py --project-root <PATH_TO_CARGO_WORKSPACE>
```

Manual integration (for context / when you need more control):

1. Copy the template crate into your repo, e.g. `tools/macos-seatbelt-testing/`.
2. Add it as a dependency of your crates that produce **executables** and/or your **workspace test harness**.
3. Ensure it is linked by referencing it (one line) from code.

Minimal code usage pattern:

```rust
// In src/main.rs and/or src/lib.rs (for test harness linkage)
#[cfg(target_os = "macos")]
use macos_seatbelt_testing as _;
```

That single import is enough to ensure the crate is linked; the Seatbelt sandbox is applied by the C constructor.

Operational knobs are the same environment variables described in `SKILL.md`:

- `SEATBELT_SANDBOX_MODE=strict|redirect|report-only`
- `SEATBELT_SANDBOX_NETWORK=deny|localhost|allow|allowlist`
- `SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=0|1`
- `SEATBELT_SANDBOX_DISABLE=1`

### Go (recommended when cgo is acceptable)

Template directory:

- `assets/templates/go/`

Installer workflow (no wrapper):

```bash
python3 <skill-path>/scripts/go_install.py --project-root <PATH_TO_GO_MODULE>
python3 <skill-path>/scripts/go_verify.py --project-root <PATH_TO_GO_MODULE>
```

What the installer does:

- Installs a cgo-enabled helper package under `tools/macos-seatbelt-testing-go/` that compiles and links `SandboxTestingBootstrap.c`.
- Generates a per-package `_test.go` file that blank-imports the helper package so the bootstrap is linked into **every** `go test` binary.

Caveats:

- Requires `CGO_ENABLED=1` (default on macOS, but some repos intentionally disable it).
- If your repo has complex build tags, the generated file may need adjustment.

### Node / TypeScript (workable for `npm` scripts)

Template directory:

- `assets/templates/node-typescript/`

Installer workflow:

```bash
python3 <skill-path>/scripts/node_install.py --project-root <PATH_TO_NODE_PROJECT>
python3 <skill-path>/scripts/node_verify.py --project-root <PATH_TO_NODE_PROJECT>
```

What it provides:

- A minimal N-API addon compiled with `node-gyp` that links `SandboxTestingBootstrap.c`.
- A preload script (`sandbox/preload.js`) that loads the addon as early as possible.
- A `package.json` script pattern that sets `NODE_OPTIONS=--require=./sandbox/preload.js` so **`npm test`** runs sandboxed.

Caveats:

- This is robust for scripted entrypoints (`npm test`, `npm run <script>`). It is not robust for arbitrary `node` invocations unless you also control those entrypoints.
- If your tests need outbound networking (registry access, telemetry, etc.), set `SEATBELT_SANDBOX_NETWORK=allow` or `allowlist`.

Alternative (often simpler): Node’s Permission Model

If your goal is “prevent accidents during tests” rather than “kernel-enforced macOS sandboxing”, Node’s built-in Permission Model can be a good fit for many TypeScript repos.

Tradeoffs:

- It is not the same as Seatbelt (different enforcement model / scope).
- It can be bypassed if you don’t control how Node is invoked.
- It may not cover all early startup behavior.

### Python (venv preload)

If you run tests from a virtual environment (venv), you can apply the sandbox by loading a small dylib at interpreter startup.

Template directory:

- `assets/templates/python-venv/`

Installer workflow:

```bash
python3 <skill-path>/scripts/python_install.py --project-root <PATH_TO_PY_PROJECT> --venv <PATH_TO_VENV>
python3 <skill-path>/scripts/python_verify.py --project-root <PATH_TO_PY_PROJECT> --venv <PATH_TO_VENV>
```

This approach:

- compiles `SandboxTestingBootstrap.c` into `macos_sandbox_testing_bootstrap.dylib`, and
- installs a `.pth` startup hook into the venv’s `site-packages` so the dylib loads automatically.

Limitations:

- Applies automatically only when using the instrumented venv interpreter.
- Can be bypassed by suppressing `site` initialization (`python -S`) or using a different interpreter.


## “sandbox-exec” wrappers are not the goal here

`sandbox-exec` (and wrapper tools built atop it) can still work on modern macOS, but it is:

- deprecated,
- wrapper-based (easy to bypass), and
- more fragile for developer workflows that expect *direct* invocations.

For the “agent-safe-by-construction” requirement, embedding the sandbox into the process remains the most reliable pattern.
