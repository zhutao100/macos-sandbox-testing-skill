// macos-sandbox-testing (Xcode)
//
// C/C++ linker anchor for SandboxTestingBootstrap.c.
//
// Use when the bootstrap is packaged as a static library/framework and might be
// dead-stripped unless a symbol is referenced.
//
// If you add SandboxTestingBootstrap.c directly to your target's Compile Sources,
// you typically do NOT need this file.

#if defined(__APPLE__)

void msst_force_link(void);

__attribute__((used))
static void (*const _msst_anchor)(void) = msst_force_link;

#endif
