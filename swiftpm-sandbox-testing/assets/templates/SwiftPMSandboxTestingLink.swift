// swiftpm-sandbox-testing
//
// Drop this file into a SwiftPM Swift target (Sources/<Target>/ or Tests/<Target>/).
// It forces the SwiftPM sandbox bootstrap Clang target to be linked into the
// final binary / test bundle, which ensures the bootstrap constructor runs.
//
// Why this file exists:
// - The bootstrap lives in a separate Clang target (to avoid SwiftPM mixed-language targets).
// - Linkers may omit object files from static libraries unless a symbol is referenced.
// - Referencing swiftpmst_force_link() prevents that omission.

#if os(macOS)
import SwiftPMSandboxTestingBootstrap

@_silgen_name("swiftpmst_force_link")
private func swiftpmst_force_link()

// Runs during module initialization (before Swift main / before XCTest begins executing tests).
private let _swiftpmstForceLinkOnce: Void = {
    swiftpmst_force_link()
}()
#endif
