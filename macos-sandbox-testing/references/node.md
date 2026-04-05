# Node / TypeScript integration

This skill supports a **no-wrapper** Node workflow by ensuring `npm test` (or an equivalent scripted entrypoint) starts Node with a native preload that applies Seatbelt in-process.

## Preconditions

- Your repo has a scripted test entrypoint (a non-empty `scripts.test` in `package.json`).
- You can build a native addon (`node-gyp` available).

## Install

From the Node project root (contains `package.json`):

```bash
python3 <skill-path>/scripts/node_install.py --project-root .
```

## Verify (recommended)

```bash
python3 <skill-path>/scripts/node_verify.py --project-root .
```

## Uninstall

```bash
python3 <skill-path>/scripts/node_uninstall.py --project-root .
```

## Notes / pitfalls

- This is robust for controlled entrypoints (`npm test`, `npm run …`). Arbitrary `node …` invocations can bypass it unless you also control those entrypoints.
- For an alternative “accident prevention” mechanism (not Seatbelt), see `references/interpreted-and-vm-ecosystems.md` for Node’s Permission Model tradeoffs.

## Configuration

See `references/configuration.md` (network policy, modes, logs).
