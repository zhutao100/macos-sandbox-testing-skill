# SwiftPM mixed-language targets and early-bootstrapping (research notes)

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

Swift Evolution proposal SE-0403 proposed experimental support for mixed-language targets in SwiftPM, but (as of its last published state) it was returned for revision and described significant limitations (notably, it did not cover executable targets).

- https://github.com/swiftlang/swift-evolution/blob/main/proposals/0403-swiftpm-mixed-language-targets.md

Given these constraints and the need to reliably cover both `swift run` and `swift test`, the skill does not rely on SE-0403.

## 3) Running code before Swift `main` / before tests

To avoid wrapper scripts, the guard must initialize as early as possible.

On Mach-O, functions referenced via the `__DATA,__mod_init_func` section are executed by the loader before `main`.

Swift 6.3 introduced stable section-placement attributes via SE-0492 (“Section Placement Control”):

- https://forums.swift.org/t/se-0492-section-placement-control/82289
- https://swift.org/blog/embedded-swift-improvements-coming-in-swift-6.3/

Before Swift 6.3, the same capability existed behind the experimental `SymbolLinkageMarkers` feature, using underscored attributes `@_section` / `@_used`.

A concise pattern (also referencing `__DATA,__mod_init_func`) is described here:

- https://stackoverflow.com/questions/31865872/is-there-a-swift-equivalent-to-gcc-attribute-constructor

## 4) Strategy used by this skill

This skill installs:

1. A dedicated Clang target (`SwiftPMSandboxTestingRuntime`) that contains the Seatbelt + tripwire runtime.
2. A Swift bootstrap file injected into each executable/test target.
3. A load-time initializer in the Swift bootstrap placed into `__DATA,__mod_init_func`, which calls the C runtime entrypoint.

This avoids mixed-language targets while still providing “runs even on direct `swift run` / `swift test`” behavior.
