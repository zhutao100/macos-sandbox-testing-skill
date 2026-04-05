use std::{env, path::PathBuf};

fn main() {
    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os != "macos" {
        return;
    }

    println!("cargo:rerun-if-changed=SandboxTestingBootstrap.c");

    // Compile the bootstrap translation unit into a static archive under OUT_DIR.
    let lib_name = "msst_bootstrap";
    cc::Build::new()
        .file("SandboxTestingBootstrap.c")
        .flag_if_supported("-std=c11")
        .warnings(false)
        .compile(lib_name);

    // Ensure the archive is *force-loaded* so the constructor is not dead-stripped.
    //
    // Cargo will link this archive automatically (the cc crate prints the right link-search/lib lines),
    // but without `-force_load` the linker may discard the object file because nothing references it.
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("OUT_DIR"));
    let archive = out_dir.join(format!("lib{lib_name}.a"));
    println!("cargo:rustc-link-arg=-Wl,-force_load,{}", archive.display());

    // On many setups libSystem is sufficient, but `sandbox_init_with_parameters` is historically
    // provided by libsandbox, so link it explicitly for reliability.
    println!("cargo:rustc-link-lib=sandbox");
}
