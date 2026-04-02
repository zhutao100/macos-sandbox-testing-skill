---
name: update-swiftpm-sandbox-testing-skill
description: Internal maintenance skill for this repo. Use it to refresh web references, validate SBPL/Seatbelt assumptions for modern macOS, update the injected bootstrap template, and keep the skill bundle compliant with Codex/Open Agent skill standards.
license: MIT
compatibility: macOS 15/26+ (for sandbox behavior verification). Requires python3 for repo checks; optionally Xcode Command Line Tools for smoke tests.
metadata:
  version: "1.0"
  author: "swiftpm-sandbox-testing maintainers"
  scope: "repo-internal maintenance"
---

## Objective

Keep the `swiftpm-sandbox-testing` skill accurate, current, and standards-compliant as macOS and toolchains evolve.

This internal skill is intended to be used by future agentic sessions working *inside this repository*.

## Quick checks (no macOS required)

Run the bundled repo checks:

```bash
python3 .agents/skills/update-swiftpm-sandbox-testing-skill/scripts/run_repo_checks.py
```

This validates:

- required skill structure (`SKILL.md` + optional `scripts/`, `references/`, `assets/`)
- YAML frontmatter name/dir-name match
- Python scripts compile (`compileall`)
- no ChatGPT-only citation tokens accidentally committed (e.g. ChatGPT-only inline citation markers)
- template invariants (presence of required env vars, deny/allow SBPL ordering)

## macOS verification (recommended on each meaningful update)

### 1) Smoke-test against a throwaway SwiftPM package

On a macOS machine with Xcode CLT:

```bash
rm -rf /tmp/spm-sandbox-smoke && mkdir -p /tmp/spm-sandbox-smoke
cd /tmp/spm-sandbox-smoke
swift package init --type executable

python3 <path-to-this-repo>/swiftpm-sandbox-testing/scripts/install.py --package-root .
python3 <path-to-this-repo>/swiftpm-sandbox-testing/scripts/verify.py --package-root .
```

If `verify.py` fails due to denied operations, triage with:

- `swiftpm-sandbox-testing/references/debugging.md`

### 2) Re-validate SBPL assumptions against upstream examples

This repo relies on behaviors that Apple does not treat as “public API stable”. Re-validate periodically:

- `sandbox_init_with_parameters` usage and parameter array shape
- SBPL rule ordering behavior
- `sandbox_check()` return semantics
- dyld interposing mechanics (`__DATA,__interpose`)

Start from:

- `swiftpm-sandbox-testing/references/upstream_examples.md`

## Web research checklist (update sources and examples)

Perform broad, up-to-date checks (prefer primary sources):

1. Chromium Seatbelt code (`seatbelt.cc`) and SBPL design doc:
   - confirm signatures and examples still match current Chromium
   - note any behavioral footnotes for recent macOS releases

2. Mark Rowe’s “Sandboxing on macOS”:
   - confirm high-level statements used by this skill remain correct

3. OpenAI Codex seatbelt policies:
   - compare patterns for `/dev/null`, process-exec, etc.

4. Any macOS release notes / security discussions that indicate Seatbelt behavior changes (especially around path canonicalization and action modifiers).

When changes are required:

- update the template at `swiftpm-sandbox-testing/assets/templates/SandboxTestingBootstrap.c`
- update `references/*.md` with corrected links and explanation
- bump `metadata.version` in `swiftpm-sandbox-testing/SKILL.md`

## Standard compliance

After edits:

- re-run `run_repo_checks.py`
- ensure `swiftpm-sandbox-testing/SKILL.md` `name:` equals directory name
- ensure scripts remain deterministic and do not require network access during basic validation
