// swiftpm-sandbox-testing
// Public header for the bootstrap Clang target.
//
// The only purpose of this symbol is to force the static library to be linked,
// ensuring the constructor in SandboxTestingBootstrap.c is not dropped by the linker.

#pragma once

#ifdef __cplusplus
extern "C" {
#endif

void swiftpmst_force_link(void);

#ifdef __cplusplus
} // extern "C"
#endif
