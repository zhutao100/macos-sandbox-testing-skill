# Agent operating guide

This repository is a **single-skill bundle**. Changes should preserve:

- Agent Skills directory conventions (`SKILL.md`, `scripts/`, `references/`, `assets/`).
- `SKILL.md` YAML frontmatter validity and the name/dir-name match.
- Deterministic, non-interactive scripts (print clear diagnostics; exit non-zero on failure).

## Repository layout

- `macos-sandbox-testing/`
  - `SKILL.md`: primary instructions and entry points.
  - `scripts/`: install/uninstall/verify utilities.
  - `assets/templates/`: source templates injected into target repos (SwiftPM, Cargo, etc.).
  - `references/`: longer background, rationale, and debugging notes.

## When editing the injected bootstrap code

The injected template (`assets/templates/SandboxTestingBootstrap.c`) is intended to be compiled/linked into **each supported executable and test runner** (SwiftPM, Cargo, etc.). Keep it:

- **network-conscious** (ensure network restrictions remain coarse-but-reliable on modern macOS, and don’t accidentally break AF_UNIX IPC unless explicitly intended)

- **self-contained** (no third-party dependencies)
- **C-only in the constructor path** (avoid Foundation/Swift runtime assumptions)
- **namespaced** (prefix symbols; avoid exporting non-static symbols unless interposing)
- **fail-loud by default** (strict mode returns `EPERM` + logs)

## Local checks to run (repo)

- Validate the skill bundle is structurally correct:

```bash
python3 macos-sandbox-testing/scripts/validate_skill_bundle.py --skill-dir macos-sandbox-testing
```

- Run formatting checks (if any are added later):

```bash
python3 -m compileall macos-sandbox-testing/scripts
```

## Smoke tests against throwaway packages (macOS only)

### SwiftPM

On a macOS machine with Xcode + SwiftPM:

1. Create a throwaway package:

```bash
rm -rf /tmp/spm-sandbox-smoke && mkdir -p /tmp/spm-sandbox-smoke
cd /tmp/spm-sandbox-smoke
swift package init --type executable
swift test
```

Note: `swift package init --type executable` does not always create a test target on modern SwiftPM toolchains. If `swift test` has nothing to run, create a minimal test target or use the more explicit smoke test in `.agents/skills/update-macos-sandbox-testing-skill/SKILL.md`.

2. Install the guard into it:

```bash
python3 <path-to-this-repo>/macos-sandbox-testing/scripts/swiftpm_install.py --package-root .
```

3. Run verification:

```bash
python3 <path-to-this-repo>/macos-sandbox-testing/scripts/swiftpm_verify.py --package-root .
```

If verification fails due to a denied operation required by system frameworks, use the debugging guidance in:

- `macos-sandbox-testing/references/debugging.md`

### Cargo

On a macOS machine with Rust/Cargo:

```bash
rm -rf /tmp/cargo-sandbox-smoke
cargo new /tmp/cargo-sandbox-smoke
cd /tmp/cargo-sandbox-smoke
cargo test

python3 <path-to-this-repo>/macos-sandbox-testing/scripts/cargo_install.py --project-root .
python3 <path-to-this-repo>/macos-sandbox-testing/scripts/cargo_verify.py --project-root .
```

## Internal maintenance skill

For repo-internal upkeep, use:

- `.agents/skills/update-macos-sandbox-testing-skill/SKILL.md`

This internal skill provides a checklist for refreshing web references, re-validating SBPL/Seatbelt behaviors, and ensuring this repo remains compliant with skill standards.
