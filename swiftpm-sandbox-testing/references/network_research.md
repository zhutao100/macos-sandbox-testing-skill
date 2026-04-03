# Network IO sandboxing research (macOS 15/26+)

This note summarizes viable approaches to **preventing and logging unexpected network activity** from `swift run` / `swift test` executables, with a bias toward solutions that:

- are **in-process** (cannot be bypassed by forgetting a wrapper),
- apply to **child processes** (inheritance),
- work on **modern macOS** without requiring privileged installs.

## Viable: Seatbelt (SBPL) network rules (chosen)

Seatbelt SBPL supports network operations such as:

- `network-outbound`
- `network-bind`
- `network-inbound`

and matchers like `(remote ip "localhost:43128")` or `(remote ip "*:443")`.

This is the same underlying mechanism used by long-lived macOS sandboxes (Chromium-style Seatbelt usage) and modern “agent sandbox” toolchains.

### Why this is the best fit for this repo

- **Kernel enforced**: the policy is enforced by XNU’s sandbox layer.
- **Inherited by children**: once applied, restrictions propagate to child processes.
- **Works in-process**: compatible with the repo’s “no wrapper” requirement.
- **Auditable**: SBPL profile text is explicit; failures show up as sandbox denials.

### Important limitations

- **No domain-based allowlisting**: practical host matching in `remote ip` rules is coarse (typically `*` or `localhost`). This makes fine-grained allowlisting by hostname impractical in SBPL alone.
- **Unix-domain socket caveat**: overly broad rules like `(deny network*)` can break local IPC that uses AF_UNIX sockets. Prefer IP-filtered rules (deny only `remote ip` / `local ip`) unless you explicitly want to block AF_UNIX.
- **IPv6 dual-stack edge cases**: loopback-only policies can be sensitive to IPv4-mapped IPv6 behavior (`::ffff:127.0.0.1`). Prefer explicit loopback bindings when possible.

### Concrete upstream examples

- SBPL syntax and discussion of network filters and coarse host matching:
  - https://lucaswiman.github.io/blog/2023-06-04--macos-sandbox/

- A large SBPL generator that includes:
  - `network-outbound` / `network-bind` / `network-inbound`
  - localhost/proxy patterns
  - unix-socket allowances
  - notes about loopback matching pitfalls
  - https://github.com/anthropic-experimental/sandbox-runtime/blob/main/src/sandbox/macos-sandbox-utils.ts

- Practical “kernel blocks everything except loopback; proxy outside the sandbox logs domains” approach:
  - https://github.com/michaelneale/agent-seatbelt-sandbox

- Example of a modern Seatbelt network-related policy snippet (TLS cache/mach services):
  - https://raw.githubusercontent.com/openai/codex/main/codex-rs/core/src/seatbelt_network_policy.sbpl

## Complementary: in-process tripwire logging

Seatbelt denials are visible in unified logging (`sandboxd`), but the repo also maintains a local JSONL audit log.

This skill extends the tripwire to interpose and log:

- `connect()` (`net.connect`)
- `bind()` (`net.bind`)

to provide **deterministic, repo-local attribution** even when system logs are unavailable.

## Not a good fit (for this repo)

### Network Extensions / content filters (NEFilter*)

Network extension based filtering (per-process, domain-aware) can be powerful, but it generally requires:

- a signed extension,
- entitlements,
- installation/approval by the user/admin.

This is incompatible with the skill’s goal of “drop-in dev/test safety” for arbitrary SwiftPM packages.

### EndpointSecurity for network event monitoring

EndpointSecurity can provide deep visibility, but it requires:

- a system extension,
- special entitlements, and often elevated privileges.

Again, unsuitable for a drop-in skill repo.

### PF, system firewalls, or third-party tools (Little Snitch)

These can control outbound connections, but they are **external** to the process:

- not “safe-by-construction” for `swift run` / `swift test`,
- often interactive, and
- not consistently automatable across developer machines.

They can be complementary, but they do not replace an in-process boundary.
