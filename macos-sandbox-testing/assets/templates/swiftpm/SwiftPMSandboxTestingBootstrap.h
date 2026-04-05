#ifndef SEATBELT_SANDBOX_TESTING_BOOTSTRAP_H
#define SEATBELT_SANDBOX_TESTING_BOOTSTRAP_H

#ifdef __cplusplus
extern "C" {
#endif

// Linker anchor.
//
// The sandbox bootstrap runs from a constructor in `SandboxTestingBootstrap.c`.
// SwiftPM builds C targets as static libraries, so Swift code should reference
// this symbol to ensure the bootstrap translation unit is linked into the final
// executable.
void msst_force_link(void);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // SEATBELT_SANDBOX_TESTING_BOOTSTRAP_H
