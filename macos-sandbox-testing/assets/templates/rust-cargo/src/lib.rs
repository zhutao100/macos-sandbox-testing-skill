//! macos-seatbelt-testing
//!
//! Add this crate as a dependency and ensure it is linked (e.g. `use macos_seatbelt_testing as _;`).
//! The C bootstrap applies a Seatbelt sandbox in a Mach-O constructor before `main`.
//!
//! Environment variables are shared with the `macos-sandbox-testing` skill:
//! - SEATBELT_SANDBOX_MODE=strict|redirect|report-only
//! - SEATBELT_SANDBOX_NETWORK=deny|localhost|allow|allowlist
//! - SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP=0|1
//! - SEATBELT_SANDBOX_DISABLE=1

#![no_std]

// Linker anchor.
//
// This crate's build script compiles `SandboxTestingBootstrap.c` into a static archive, and then
// the Rust side references the `msst_force_link()` symbol so the object file (and its constructor)
// cannot be dead-stripped.
#[cfg(target_os = "macos")]
extern "C" {
    fn msst_force_link();
}

#[cfg(target_os = "macos")]
#[used]
static _MSST_FORCE_LINK: unsafe extern "C" fn() = msst_force_link;
