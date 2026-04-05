# Node / TypeScript template (native preload)

This template demonstrates how to apply the same in-process Seatbelt sandbox/tripwire used by this repo to a Node/TypeScript test run.

## Why a native addon

Node itself cannot call `sandbox_init_with_parameters` from JavaScript. A native module can.

This template links `SandboxTestingBootstrap.c` into a minimal N-API addon. The bootstrap runs at dylib load time (Mach-O constructor).

## Workflow

1. Copy this template into your Node project (e.g. `sandbox/` plus `binding.gyp`).
2. Build the addon:

```bash
npm install -g node-gyp
node-gyp rebuild
```

3. Run your tests with the preload:

```bash
SEATBELT_SANDBOX_SELFTEST=1 NODE_OPTIONS=--require ./sandbox/preload.js npm test
```

## Notes / caveats

- This is robust for scripted entrypoints (e.g. `npm test` when you control the script). It is not robust for arbitrary `node` invocations.
- If your tests need outbound networking, set `SEATBELT_SANDBOX_NETWORK=allow` or `allowlist`.
- The bootstrap redirects `HOME` and `TMPDIR` by default; set `SEATBELT_SANDBOX_PRESERVE_HOME=1` if that breaks your toolchain.
