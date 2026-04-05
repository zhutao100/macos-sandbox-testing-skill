# Upstream examples to read

This skill intentionally mirrors patterns used by long-lived macOS sandbox deployments (Chromium, etc.), but adapted for SwiftPM dev/test safety.

## 1) Chromium Seatbelt integration (C++)

- `sandbox_init_with_parameters` signature + parameter array must be NULL-terminated.
- `sandbox_check(getpid(), NULL, …)` as “is sandboxed”.

Source:

- https://chromium.googlesource.com/chromium/src/+/lkgr/sandbox/mac/seatbelt.cc

## 2) Chromium SBPL + parameters example (design doc)

Contains an end-to-end example of:

- an SBPL profile using `(param "USER_HOME_DIR")`
- a `const char* parameters[] = { "USER_HOME_DIR", home_dir, NULL };` array
- calling `sandbox_init_with_parameters(profile, 0, parameters, NULL)`

Source:

- https://chromium.googlesource.com/chromium/src/+/HEAD/sandbox/mac/seatbelt_sandbox_design.md

## 3) SBPL rule ordering gotchas (practical)

A concrete exploration showing that rule ordering matters and that:

- `(allow default)` + `(deny file-write*)` + `(allow file-write* (subpath "..."))` can work as expected

Source:

- https://7402.org/blog/2020/macos-sandboxing-of-folder.html

## 4) `sandbox_check()` return values

A small validator tool description demonstrating the common convention:

- return `0` means allowed, non-zero means denied

Source:

- https://karol-mazurek.medium.com/sandbox-validator-e760e5d88617

## 5) dyld interposing (`__DATA,__interpose`)

Background on how dyld processes the `__DATA,__interpose` section:

- https://blog.darlinghq.org/2018/07/mach-o-linking-and-loading-tricks.html

A widely-copied header macro (same technique):

- https://github.com/facebook/xctool/blob/master/Common/dyld-interposing.h

## 6) OpenAI Codex Seatbelt base policy (SBPL)

An actively maintained Seatbelt policy used by a modern agentic toolchain. Useful to compare patterns such as allowing `/dev/null` writes.

Source:

- https://github.com/openai/codex/blob/main/codex-rs/core/src/seatbelt_base_policy.sbpl


## Concrete snippets (copy/paste patterns)

### `sandbox_init_with_parameters` parameter array shape (pattern)

```c
// Key/value pairs terminated by NULL.
const char *const parameters[] = {
  "WORKSPACE_ROOT", workspace_root,
  "SANDBOX_ROOT", sandbox_root,
  NULL
};

char *errbuf = NULL;
int rc = sandbox_init_with_parameters(profile_sbpl, 0, parameters, &errbuf);
```

### dyld interposing table shape (pattern)

```c
struct interpose_pair { const void *replacement; const void *replacee; };

__attribute__((used, section("__DATA,__interpose")))
static const struct interpose_pair interpose_open = {
  (const void *)(unsigned long)&my_open,
  (const void *)(unsigned long)&open
};
```

## 7) SBPL network rules (modern examples)

Useful for understanding the practical shape of Seatbelt network restrictions and their limitations:

- SBPL network syntax overview + note that `remote ip` host matching is typically limited to `*` or `localhost`:
  - https://lucaswiman.github.io/blog/2023-06-04--macos-sandbox/

- A large, actively maintained SBPL generator showing:
  - `network-outbound`, `network-bind`, `network-inbound`
  - unix-socket allowances
  - loopback/IPv6 dual-stack edge cases
  - localhost proxy “hole punching” patterns
  - https://github.com/anthropic-experimental/sandbox-runtime/blob/main/src/sandbox/macos-sandbox-utils.ts

- A practical “kernel blocks all, proxy outside” pattern used for agent sandboxes:
  - https://github.com/michaelneale/agent-seatbelt-sandbox

### Network allowlist snippet patterns

```scheme
; deny all outbound IP
(deny network-outbound (remote ip "*:*"))

; allow loopback proxy port only
(allow network-outbound (remote ip "localhost:43128"))
```
