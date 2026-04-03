# SwiftPM sandbox testing skill

This repository contains an **agent skill** for adding a **self-bootstrapping, in-process host-mutation guard** to **SwiftPM** packages on **modern macOS (15/26+)**.

The goal is to make **direct** `swift run` and `swift test` invocations safer by:

- applying a **kernel-enforced Seatbelt sandbox** in-process (via `sandbox_init_with_parameters`) to deny filesystem writes outside the repo workspace, and to optionally restrict IP networking, and
- installing an optional **tripwire/interposition layer** that logs (and can deny/redirect) common filesystem mutation APIs, plus denies/logs outbound IP connections and bind attempts, into a repo-local JSONL log.

It is designed for **development and test environments**, where preventing accidental destructive writes to the host is more important than using only Apple-supported APIs.

## Install into Codex CLI / skills-compatible agents

Copy or symlink the skill directory into a skills-discovery location, for example:

- Per-repo: `<your-repo>/.agents/skills/swiftpm-sandbox-testing/`
- Per-user: `~/.agents/skills/swiftpm-sandbox-testing/`

The primary skill directory is:

- `swiftpm-sandbox-testing/`

## Use

Start here:

- `swiftpm-sandbox-testing/SKILL.md`

## Repo maintenance (internal)

This repo includes an internal self-update skill for maintaining the skill content over time:

- `.agents/skills/update-swiftpm-sandbox-testing-skill/`

It is intended for future agentic sessions that need to refresh web references, validate correctness, and keep the repo compliant with skill standards.

## License

MIT. See `LICENSE`.
