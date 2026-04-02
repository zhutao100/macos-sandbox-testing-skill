// swiftpm-sandbox-testing bootstrap for Swift targets.
//
// This file is injected into SwiftPM executable and test targets.
// It triggers the C runtime bootstrap at *load time* (before Swift main / before tests).
//
// For Swift 6.3+ (SE-0492), use the stable @section/@used attributes.
// For older toolchains that support SymbolLinkageMarkers, fall back to @_section/@_used.
// Otherwise, provide an explicit bootstrap function that must be called early.

#if canImport(SwiftPMSandboxTestingRuntime)
import SwiftPMSandboxTestingRuntime
#endif

#if swift(>=6.3)
@used
@section("__DATA,__mod_init_func")
private let _swiftpmst_init: @convention(c) () -> Void = {
    swiftpmst_bootstrap()
}

#elseif hasFeature(SymbolLinkageMarkers)
@_used
@_section("__DATA,__mod_init_func")
private let _swiftpmst_init: @convention(c) () -> Void = {
    swiftpmst_bootstrap()
}

#else
public enum SwiftPMSandboxTesting {
    /// Legacy fallback: call this as early as possible in your executable and test entrypoints.
    public static func bootstrap() {
        swiftpmst_bootstrap()
    }
}
#endif
