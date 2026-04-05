// macos-sandbox-testing (Xcode)
//
// Swift linker anchor for SandboxTestingBootstrap.c.
//
// Use when the bootstrap is packaged as a static library/framework and might be
// dead-stripped unless a symbol is referenced.
//
// If you add SandboxTestingBootstrap.c directly to your target's Compile Sources,
// you typically do NOT need this file.

#if os(macOS)

@_silgen_name("msst_force_link")
private func msst_force_link()

// Keep a reference to msst_force_link alive even under dead-strip.
//
// Notes:
// - This uses underscored attributes, which are stable enough for dev/test
//   tooling but should not be treated as an ABI contract.
// - If you prefer to avoid underscored Swift attributes, use the C anchor
//   (`SandboxTestingAnchor.c`) instead.
@_used
@_cdecl("msst_swift_force_link")
public func msst_swift_force_link() {
    msst_force_link()
}

#endif
