# SwiftPM mixed-language targets and early bootstrapping (research notes)

This note exists to justify the installation strategy used by this skill.

## 1) SwiftPM and mixed-language targets

SwiftPM has historically treated a target as a single “language mode” (Swift *or* C-family) and will emit an error when Swift and C-family sources coexist in the same target, e.g.:

```
contains mixed language source files; feature not supported
```

A recurring workaround pattern is to split code into multiple targets:

- a Clang target (C/C++/ObjC) that exposes a module via an umbrella header, and
- a Swift target that depends on it.

Swift’s C++ interop documentation for SwiftPM explicitly demonstrates this cross-target structure for Swift → C++ usage:

- https://www.swift.org/documentation/cxx-interop/project-build-setup/

## 2) “Maybe Swift 6 fixed it?” (SE-0403)

Swift Evolution proposal SE-0403 proposed support for mixed-language targets in SwiftPM, but (as of its last published state) it was returned for revision and described significant limitations.

- https://github.com/swiftlang/swift-evolution/blob/main/proposals/0403-swiftpm-mixed-language-targets.md

Given these constraints and the need to reliably cover both `swift run` and `swift test` on stable toolchains, this skill does not rely on SE-0403.

## 3) Running code before Swift `main` / before tests

To avoid wrapper scripts, the guard must initialize as early as possible.

On Mach-O, C/Clang “constructors” (functions with `__attribute__((constructor))`) run at load time before `main` — **as long as the object file containing the constructor is actually linked into the final binary**.

SwiftPM builds Clang targets as static libraries; linkers may omit object files from static libraries unless a symbol is referenced. This is why the bootstrap translation unit exports a tiny symbol (`swiftpmst_force_link()`), and a small Swift “anchor” file references it.

As an alternative (future-facing), Swift can also register Mach-O module initializers via the `__DATA,__mod_init_func` section. Swift 6.3 introduced stable section placement (`@section` / `@used`) via SE-0492, and older toolchains may support the underscored `@_section` / `@_used` form behind an experimental feature.

## 4) Strategy used by this skill

This skill installs:

1. A dedicated Clang target (default name: `SwiftPMSandboxTestingBootstrap`) containing:
   - `SandboxTestingBootstrap.c` (constructor-based Seatbelt + tripwire bootstrap)
   - `include/SwiftPMSandboxTestingBootstrap.h`
2. A Swift anchor file injected into each selected executable/test target:
   - `SwiftPMSandboxTestingAnchor.swift`
   - imports the bootstrap module and references `swiftpmst_force_link()` so the bootstrap object file is not dropped by the linker.
3. Marker-based `Package.swift` edits (target declaration + per-target dependency wiring) so uninstall can safely revert changes.

This avoids mixed-language targets while still providing “runs even on direct `swift run` / `swift test`” behavior.
