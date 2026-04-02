# Agent operating guide

This repository is a **single-skill bundle**. Changes should preserve:

- Agent Skills directory conventions (`SKILL.md`, `scripts/`, `references/`, `assets/`).
- `SKILL.md` YAML frontmatter validity and the name/dir-name match.
- Deterministic, non-interactive scripts (print clear diagnostics; exit non-zero on failure).

## Repository layout

- `swiftpm-sandbox-testing/`
  - `SKILL.md`: primary instructions and entry points.
  - `scripts/`: install/uninstall/verify utilities.
  - `assets/templates/`: source templates injected into target SwiftPM repos.
  - `references/`: longer background, rationale, and debugging notes.

## When editing the injected bootstrap code

The injected template (`assets/templates/SandboxTestingBootstrap.c`) is intended to be copied into **each SwiftPM executable and test target**. Keep it:

- **self-contained** (no third-party dependencies)
- **C-only in the constructor path** (avoid Foundation/Swift runtime assumptions)
- **namespaced** (prefix symbols; avoid exporting non-static symbols unless interposing)
- **fail-loud by default** (strict mode returns `EPERM` + logs)

## Local checks to run (repo)

- Validate the skill bundle is structurally correct:

```bash
python3 swiftpm-sandbox-testing/scripts/validate_skill_bundle.py --skill-dir swiftpm-sandbox-testing
```

- Run formatting checks (if any are added later):

```bash
python3 -m compileall swiftpm-sandbox-testing/scripts
```

## Smoke test against a sample SwiftPM package (macOS only)

On a macOS machine with Xcode + SwiftPM:

1. Create a throwaway package:

```bash
rm -rf /tmp/spm-sandbox-smoke && mkdir -p /tmp/spm-sandbox-smoke
cd /tmp/spm-sandbox-smoke
swift package init --type executable
swift test
```

2. Install the guard into it:

```bash
python3 <path-to-this-repo>/swiftpm-sandbox-testing/scripts/install.py --package-root .
```

3. Run verification:

```bash
python3 <path-to-this-repo>/swiftpm-sandbox-testing/scripts/verify.py --package-root .
```

If verification fails due to a denied operation required by system frameworks, use the debugging guidance in:

- `swiftpm-sandbox-testing/references/debugging.md`


## Internal maintenance skill

For repo-internal upkeep, use:

- `.agents/skills/update-swiftpm-sandbox-testing-skill/SKILL.md`

This internal skill provides a checklist for refreshing web references, re-validating SBPL/Seatbelt behaviors, and ensuring this repo remains compliant with skill standards.
