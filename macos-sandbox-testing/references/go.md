# Go integration

This skill supports a **no-wrapper** Go test workflow (`go test ./...`) by using a small cgo helper package that links the sandbox bootstrap into every generated `go test` binary.

## Preconditions

- You run tests with cgo enabled (`CGO_ENABLED=1`), which is the default on macOS for most Go toolchains.
- Your repo is a Go module root (contains `go.mod`).

## Install

```bash
python3 <skill-path>/scripts/go_install.py --project-root .
```

## Verify (recommended)

```bash
python3 <skill-path>/scripts/go_verify.py --project-root .
```

## Uninstall

```bash
python3 <skill-path>/scripts/go_uninstall.py --project-root .
```

## What changes to expect

- Installs a helper package under `tools/` that compiles/links `SandboxTestingBootstrap.c`.
- Generates a per-package `zz_macos_sandbox_testing_bootstrap_test.go` file that blank-imports the helper so the bootstrap is linked into each `go test` binary.

## Configuration

See `references/configuration.md` (workspace root, network policy, modes, logs).
