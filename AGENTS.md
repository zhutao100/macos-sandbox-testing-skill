# Agent operating guide

This repository is a **single-skill bundle**. Keep changes:

- compatible with Agent Skills conventions (`SKILL.md` + optional `scripts/`, `references/`, `assets/`)
- `macos-sandbox-testing/SKILL.md` YAML frontmatter valid (`name:` matches directory name)
- deterministic and non-interactive (clear diagnostics; non-zero on failure)

## Fast checks

Run the repo checks (cross-platform; macOS recommended for runtime smokes):

```bash
python3 .agents/skills/update-macos-sandbox-testing-skill/scripts/run_repo_checks.py
```

## When editing the injected bootstrap code

The injected template (`macos-sandbox-testing/assets/templates/SandboxTestingBootstrap.c`) is intended to be compiled/linked into **many** toolchains. Keep it:

- **network-conscious** (coarse-but-reliable restrictions; don’t accidentally break AF_UNIX IPC)
- **self-contained** (no third-party dependencies)
- **C-only in the constructor path** (avoid Foundation/Swift runtime assumptions)
- **namespaced** (prefix symbols; avoid exporting non-static symbols unless interposing)
- **fail-loud by default** (strict mode returns `EPERM` + logs)

Repo checks enforce:

- required env var knobs
- deny/allow SBPL ordering invariants
- no broad `(deny network*)` footguns
- template variant sync (byte-for-byte copies in integration templates)

## Docs hygiene

- Keep `macos-sandbox-testing/SKILL.md` executive and language-agnostic; put per-toolchain steps in `macos-sandbox-testing/references/`.
- Keep repo-maintainer research notes under `.agents/skills/update-macos-sandbox-testing-skill/references/`.

## macOS runtime smoke tests

Use the internal maintenance skill checklist:

- `.agents/skills/update-macos-sandbox-testing-skill/SKILL.md`

## Commits

- Use Conventional Commits.
- Redact local paths in committed docs (`/Users/...` → `$HOME`/`~`).
