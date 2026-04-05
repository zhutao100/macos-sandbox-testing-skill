# Go template (cgo + per-package test file)

This template shows how to apply the same in-process Seatbelt sandbox/tripwire used by this repo to `go test`.

## Approach

Go test binaries are built **per package**. To ensure the bootstrap is linked into *every* test binary, you need two pieces:

1. A small Go package that enables cgo and includes `SandboxTestingBootstrap.c`.
2. A generated `_test.go` file in each Go package directory that blank-imports the bootstrap package.

This repo’s installer automates both steps.

## Workflow (installer)

```bash
python3 <skill-path>/scripts/go_install.py --project-root <PATH_TO_GO_MODULE>
python3 <skill-path>/scripts/go_verify.py --project-root <PATH_TO_GO_MODULE>
```

## Caveats

- Requires `CGO_ENABLED=1` (the default on macOS in most toolchains). If your workflow intentionally disables cgo, this integration will not apply.
- If your repo has complex build tags, the generated `_test.go` file may need adjustment.
