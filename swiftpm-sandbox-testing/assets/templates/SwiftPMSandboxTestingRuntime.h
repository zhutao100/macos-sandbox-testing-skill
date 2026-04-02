#ifndef SWIFTPM_SANDBOX_TESTING_RUNTIME_H
#define SWIFTPM_SANDBOX_TESTING_RUNTIME_H

#ifdef __cplusplus
extern "C" {
#endif

// Exported entrypoint: applies environment redirects, tripwire, and Seatbelt sandbox.
// Safe to call multiple times (idempotent).
void swiftpmst_bootstrap(void);

#ifdef __cplusplus
} // extern "C"
#endif

#endif // SWIFTPM_SANDBOX_TESTING_RUNTIME_H
