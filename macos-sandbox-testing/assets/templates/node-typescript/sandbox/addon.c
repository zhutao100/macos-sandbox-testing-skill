// Minimal N-API module whose only purpose is to link and load SandboxTestingBootstrap.c.
// The bootstrap applies the Seatbelt sandbox in a Mach-O constructor at dylib load time.

#include <node_api.h>

static napi_value msst_init(napi_env env, napi_value exports) {
    (void)env;
    return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, msst_init)
