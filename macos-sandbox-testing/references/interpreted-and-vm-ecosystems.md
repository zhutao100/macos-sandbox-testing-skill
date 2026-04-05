# Interpreted / VM ecosystems on macOS (notes)

This skill is built around a simple, reliable core idea:

- apply a **kernel-enforced Seatbelt policy** from **inside the process** as early as possible, and
- (optionally) add an in-process “tripwire” logger via dyld interposing for actionable attribution.

For compiled ecosystems (SwiftPM, Cargo, C/C++), “in-process + no wrapper” is straightforward because you can link the bootstrap translation unit into the final executable/test runner.

For interpreted/VM ecosystems, the difficulty is usually **bootstrapping**: you must ensure Seatbelt is applied **inside** the interpreter process **before** application/test code runs, and you must do it in a way that developers/agents can’t accidentally bypass.

## Node / TypeScript

### Option A: Node Permission Model (often simplest for “prevent accidents”)

Node has a built-in Permission Model (see Node's "Permissions" docs) that can be enabled via flags. This can be a practical way to prevent accidental file writes/network access during tests without relying on private macOS APIs.

Important: treat it as an *accident-prevention* mechanism, not a high-assurance sandbox. The Node project has published multiple security advisories for Permission Model bypasses in recent releases.

Tradeoffs to be explicit about:

- This is **not Seatbelt** (different enforcement model and scope).
- It is only as strong as your ability to **control invocation** (scripts/CI). “Random `node …`” can bypass it.
- It may not cover all early startup behavior.

When it’s a fit, it tends to be low-friction and cross-platform.

### Option B: Seatbelt via native preload (stronger, but more discipline required)

If you want the same kernel-enforced boundary this skill uses for SwiftPM/Cargo, you need native code to call `sandbox_init_with_parameters` in the Node process.

This repo includes a template and an installer:

- Template: `assets/templates/node-typescript/`
- Installer: `scripts/node_install.py` (plus `scripts/node_verify.py` / `scripts/node_uninstall.py`)

It uses:

- an N-API addon that links `SandboxTestingBootstrap.c` (constructor runs at dylib load time), and
- a preload (`NODE_OPTIONS=--require=./sandbox/preload.js`) to load the addon before tests.

Key caveats:

- This is robust for controlled entrypoints (e.g. `npm test`, `npm run …`) but not for arbitrary Node invocations unless you also control them.
- Network restrictions can break common tooling (registries, telemetry, update checks). Use `SEATBELT_SANDBOX_NETWORK=allow`/`allowlist` as needed.

## Python

Python can apply Seatbelt “in-process”, but robust bootstrapping depends on controlling interpreter startup.

### Preferred: venv startup hook + dylib preload

This skill provides an installer that instruments a venv so that **running Python from that venv** automatically loads a small dylib at interpreter startup.

- `scripts/python_install.py` / `scripts/python_verify.py` / `scripts/python_uninstall.py`
- Template notes: `assets/templates/python-venv/`

Mechanism:

- Compile `SandboxTestingBootstrap.c` into `macos_sandbox_testing_bootstrap.dylib`.
- Install a `.pth` startup hook into the venv’s `site-packages` so Python imports a tiny loader module that loads the dylib.
- Loading the dylib triggers the Mach-O constructor in the bootstrap, applying Seatbelt and starting the tripwire.

Limitations:

- Applies automatically only when running the instrumented venv interpreter (`.venv/bin/python`, `.venv/bin/pytest`, etc.).
- It can be bypassed by suppressing `site` initialization (`python -S`) or by using a different interpreter/runtime.

### Alternative: `sitecustomize.py` + `ctypes`

Calling `sandbox_init_with_parameters` directly from Python via `ctypes` can work, but tends to drift from the C bootstrap logic and is easier to misconfigure. If you go this route, keep the sandbox apply as early as possible, and be explicit about bypass modes (`-S`, embedded interpreters, alternate runtimes).

## Why `sandbox-exec` isn’t the main integration story

The `sandbox-exec` CLI remains a useful debugging tool, but it is:

- deprecated,
- wrapper-based (easy to bypass), and
- not a great fit for “agent-safe-by-construction” requirements.

## About dyld injection tricks

You may see approaches based on `DYLD_INSERT_LIBRARIES` to inject a library that runs a constructor and applies Seatbelt.

This can be convenient in some local workflows, but it is fragile:

- hardened runtime / signing requirements can disable injection,
- behavior differs across toolchains and invocation paths.

For this skill, prefer approaches that compile/link the bootstrap into the actual binary (compiled ecosystems) or use a native preload mechanism you can reliably control (Node).
