// macos-sandbox-testing
//
// Drop this file into a macOS executable or test runner (SwiftPM, Cargo, CMake, node-gyp, etc.).
// It bootstraps a dev/test safety boundary for direct invocations (e.g. `swift test`, `cargo test`, `npm test`):
//   - redirects HOME/TMP into a repo-local sandbox root
//   - applies a Seatbelt write-guard profile via sandbox_init_with_parameters
//   - logs filesystem mutation attempts (operation, path, backtrace) into JSONL
//
// Notes:
//   - This uses deprecated/private sandbox APIs (libsandbox / Seatbelt). Treat as a *dev/test guardrail*.
//   - Keep the constructor path C/POSIX-only (no Foundation / Swift runtime assumptions).

#if defined(__APPLE__)

#include <sandbox.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <stdint.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/un.h>

// sandbox_init_with_parameters is available in libsystem_sandbox, but is not always declared in the public SDK headers.
// Signature matches Chromium's usage.
extern int sandbox_init_with_parameters(const char *profile,
                                        uint64_t flags,
                                        const char *const parameters[],
                                        char **errorbuf);

// sandbox_check is available in libsystem_sandbox, but is not always declared in the public SDK headers.
// Chromium treats sandbox_check(getpid(), NULL, SANDBOX_FILTER_NONE) as the canonical "is sandboxed" check.
// See: chromium/src sandbox/mac/seatbelt.cc
// (We keep the signature minimal and avoid non-portable constants.)
//
// Return semantics used by common tools:
//   - rc == 0 => allowed
//   - rc != 0 => denied
// See: Karol Mazurek, "Sandbox Validator" (uses sandbox_check to validate operations).

enum msst_sandbox_filter_type {
    MSST_SANDBOX_FILTER_NONE = 0,
    MSST_SANDBOX_FILTER_PATH = 1,
};

extern int sandbox_check(pid_t pid, const char *operation, int type, ...);

// -----------------------------
// Configuration (env-driven)
// -----------------------------

typedef enum {
    MSST_MODE_STRICT = 0,
    MSST_MODE_REDIRECT = 1,
    MSST_MODE_REPORT_ONLY = 2,
} msst_mode_t;

static msst_mode_t msst_parse_mode(void) {
    const char *s = getenv("SEATBELT_SANDBOX_MODE");
    if (!s || !*s) return MSST_MODE_STRICT;
    if (strcmp(s, "strict") == 0) return MSST_MODE_STRICT;
    if (strcmp(s, "redirect") == 0) return MSST_MODE_REDIRECT;
    if (strcmp(s, "report-only") == 0) return MSST_MODE_REPORT_ONLY;
    return MSST_MODE_STRICT;
}

static bool msst_is_disabled(void) {
    const char *s = getenv("SEATBELT_SANDBOX_DISABLE");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool msst_allow_unenforced(void) {
    const char *s = getenv("SEATBELT_SANDBOX_ALLOW_UNENFORCED");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool msst_selftest_enabled(void) {
    const char *s = getenv("SEATBELT_SANDBOX_SELFTEST");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool msst_verbose(void) {
    const char *s = getenv("SEATBELT_SANDBOX_LOG_LEVEL");
    return (s && strcmp(s, "verbose") == 0);
}

static bool msst_allow_system_tmp(void) {
    const char *s = getenv("SEATBELT_SANDBOX_ALLOW_SYSTEM_TMP");
    if (!s || !*s) return true;
    return strcmp(s, "0") != 0;
}

static bool msst_preserve_home(void) {
    const char *s = getenv("SEATBELT_SANDBOX_PRESERVE_HOME");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool msst_tripwire_enabled(void) {
    const char *s = getenv("SEATBELT_SANDBOX_TRIPWIRE");
    if (!s || !*s) return true;
    return strcmp(s, "0") != 0;
}


// -----------------------------
// Network configuration (env-driven)
// -----------------------------

typedef enum {
    MSST_NET_DENY = 0,       // default: block IP networking (outbound + bind)
    MSST_NET_LOCALHOST = 1,  // allow localhost-only TCP/UDP (best-effort; see docs)
    MSST_NET_ALLOW = 2,      // allow all network (least restrictive)
    MSST_NET_ALLOWLIST = 3,  // allow outbound to a coarse allowlist (see SEATBELT_SANDBOX_NETWORK_ALLOWLIST)
} msst_network_mode_t;

#define MSST_MAX_NET_ALLOWLIST 16
#define MSST_MAX_NET_TOKEN 63

static msst_network_mode_t msst_parse_network_mode(void) {
    const char *s = getenv("SEATBELT_SANDBOX_NETWORK");
    if (!s || !*s) return MSST_NET_DENY;
    if (strcmp(s, "deny") == 0) return MSST_NET_DENY;
    if (strcmp(s, "localhost") == 0) return MSST_NET_LOCALHOST;
    if (strcmp(s, "allow") == 0) return MSST_NET_ALLOW;
    if (strcmp(s, "allowlist") == 0) return MSST_NET_ALLOWLIST;
    // Fail closed for unknown values.
    return MSST_NET_DENY;
}

static bool msst_net_token_is_safe(const char *t) {
    // Allow only a conservative subset used by Seatbelt filters:
    //   localhost:*   localhost:43128   *:443   *:*
    // No quoting or escaping is supported here (dev/test guardrail).
    if (!t || !*t) return false;
    for (const char *p = t; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if ((c >= 'a' && c <= 'z') ||
            (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') ||
            c == '.' || c == ':' || c == '*' || c == '-' || c == '_') {
            continue;
        }
        return false;
    }
    return true;
}

static int msst_parse_network_allowlist(char out[][MSST_MAX_NET_TOKEN + 1], int cap) {
    const char *raw = getenv("SEATBELT_SANDBOX_NETWORK_ALLOWLIST");
    if (!raw || !*raw) return 0;

    // Copy into a local mutable buffer.
    char buf[1024];
    snprintf(buf, sizeof(buf), "%s", raw);

    int n = 0;
    char *save = NULL;
    for (char *tok = strtok_r(buf, ", \t\n", &save); tok && n < cap; tok = strtok_r(NULL, ", \t\n", &save)) {
        size_t len = strlen(tok);
        if (len == 0 || len > MSST_MAX_NET_TOKEN) continue;
        if (!msst_net_token_is_safe(tok)) continue;
        snprintf(out[n], MSST_MAX_NET_TOKEN + 1, "%s", tok);
        n++;
    }
    return n;
}

// -----------------------------
// Global state
// -----------------------------

static char g_workspace_root[PATH_MAX] = {0};
static char g_sandbox_root[PATH_MAX] = {0};
static char g_fake_home[PATH_MAX] = {0};
static char g_fake_tmp[PATH_MAX] = {0};
static char g_log_path[PATH_MAX] = {0};
static char g_orig_home[PATH_MAX] = {0};

static msst_mode_t g_mode = MSST_MODE_STRICT;

static bool g_allow_system_tmp = false;
static msst_network_mode_t g_network_mode = MSST_NET_DENY;
static int g_net_allow_count = 0;
static char g_net_allow[MSST_MAX_NET_ALLOWLIST][MSST_MAX_NET_TOKEN + 1] = {{0}};

static bool g_tripwire_enabled = true;
static bool g_tripwire_active = false;

static _Thread_local int g_in_hook = 0;

// Real function pointers (resolved with dlsym(RTLD_NEXT, ...))
static int (*real_open)(const char *path, int oflag, ...) = NULL;
static int (*real_openat)(int fd, const char *path, int oflag, ...) = NULL;
static int (*real_open_nocancel)(const char *path, int oflag, ...) = NULL;
static int (*real_openat_nocancel)(int fd, const char *path, int oflag, ...) = NULL;
static int (*real_dunder_open)(const char *path, int oflag, ...) = NULL;
static int (*real_dunder_openat)(int fd, const char *path, int oflag, ...) = NULL;
static int (*real_dunder_open_nocancel)(const char *path, int oflag, ...) = NULL;
static int (*real_dunder_openat_nocancel)(int fd, const char *path, int oflag, ...) = NULL;
static int (*real_creat)(const char *path, mode_t mode) = NULL;
static int (*real_unlink)(const char *path) = NULL;
static int (*real_rename)(const char *old, const char *newp) = NULL;
static int (*real_mkdir)(const char *path, mode_t mode) = NULL;
static int (*real_rmdir)(const char *path) = NULL;
static int (*real_truncate)(const char *path, off_t length) = NULL;
static int (*real_ftruncate)(int fd, off_t length) = NULL;
static int (*real_connect)(int sockfd, const struct sockaddr *addr, socklen_t addrlen) = NULL;
static int (*real_bind)(int sockfd, const struct sockaddr *addr, socklen_t addrlen) = NULL;

static void msst_resolve_real_functions(void) {
    if (real_open) return;
    real_open = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, "open");
    real_openat = (int (*)(int, const char *, int, ...))dlsym(RTLD_NEXT, "openat");
    real_open_nocancel = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, "open$NOCANCEL");
    real_openat_nocancel = (int (*)(int, const char *, int, ...))dlsym(RTLD_NEXT, "openat$NOCANCEL");
    if (!real_open_nocancel) real_open_nocancel = real_open;
    if (!real_openat_nocancel) real_openat_nocancel = real_openat;
    real_dunder_open = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, "__open");
    real_dunder_openat = (int (*)(int, const char *, int, ...))dlsym(RTLD_NEXT, "__openat");
    real_dunder_open_nocancel = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, "__open_nocancel");
    real_dunder_openat_nocancel = (int (*)(int, const char *, int, ...))dlsym(RTLD_NEXT, "__openat_nocancel");
    if (!real_dunder_open) real_dunder_open = real_open;
    if (!real_dunder_openat) real_dunder_openat = real_openat;
    if (!real_dunder_open_nocancel) real_dunder_open_nocancel = real_open_nocancel;
    if (!real_dunder_openat_nocancel) real_dunder_openat_nocancel = real_openat_nocancel;
    real_creat = (int (*)(const char *, mode_t))dlsym(RTLD_NEXT, "creat");
    real_unlink = (int (*)(const char *))dlsym(RTLD_NEXT, "unlink");
    real_rename = (int (*)(const char *, const char *))dlsym(RTLD_NEXT, "rename");
    real_mkdir = (int (*)(const char *, mode_t))dlsym(RTLD_NEXT, "mkdir");
    real_rmdir = (int (*)(const char *))dlsym(RTLD_NEXT, "rmdir");
    real_truncate = (int (*)(const char *, off_t))dlsym(RTLD_NEXT, "truncate");
    real_ftruncate = (int (*)(int, off_t))dlsym(RTLD_NEXT, "ftruncate");
    real_connect = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "connect");
    real_bind = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "bind");
}

// -----------------------------
// Small utilities
// -----------------------------

static void msst_stderr(const char *prefix, const char *msg) {
    if (!prefix) prefix = "macos-sandbox-testing";
    if (!msg) msg = "";
    fprintf(stderr, "[%s] %s\n", prefix, msg);
}

static bool msst_path_exists(const char *p) {
    struct stat st;
    return (p && *p && stat(p, &st) == 0);
}

static bool msst_is_dir(const char *p) {
    struct stat st;
    if (!p || !*p) return false;
    if (stat(p, &st) != 0) return false;
    return S_ISDIR(st.st_mode);
}

static bool msst_mkdir_p(const char *path, mode_t mode) {
    if (!path || !*path) return false;
    if (msst_is_dir(path)) return true;

    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", path);

    size_t len = strlen(tmp);
    if (len == 0) return false;
    if (tmp[len - 1] == '/') tmp[len - 1] = 0;

    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            if (!msst_is_dir(tmp)) {
                if (real_mkdir(tmp, mode) != 0 && errno != EEXIST) return false;
            }
            *p = '/';
        }
    }
    if (real_mkdir(tmp, mode) != 0 && errno != EEXIST) return false;
    return true;
}

static bool msst_find_workspace_root_from(const char *start_dir, char out[PATH_MAX]) {
    if (!start_dir || !*start_dir) return false;

    // Workspace marker detection
    //
    // This bootstrap is primarily installed into SwiftPM packages, but the underlying approach
    // (constructor + Seatbelt + tripwire) is language-agnostic. For non-Swift repos, allow
    // auto-detection via other common project markers.
    //
    // Preference order is “package root” markers first, then VCS roots.
    const char *markers_files[] = {
        "Package.swift",     // SwiftPM
        "Cargo.toml",        // Rust/Cargo
        "package.json",      // Node/TypeScript
        "go.mod",            // Go
        "pyproject.toml",    // Python
        "Pipfile",           // Python (pipenv)
        "requirements.txt",  // Python
        "Gemfile",           // Ruby
        NULL
    };

    const char *markers_dirs[] = {
        ".git",
        NULL
    };

    char cur[PATH_MAX];
    snprintf(cur, sizeof(cur), "%s", start_dir);

    for (int depth = 0; depth < 24; depth++) {
        // File markers.
        for (const char **m = markers_files; m && *m; m++) {
            char candidate[PATH_MAX];
            snprintf(candidate, sizeof(candidate), "%s/%s", cur, *m);
            if (msst_path_exists(candidate)) {
                snprintf(out, PATH_MAX, "%s", cur);
                return true;
            }
        }

        // Directory markers.
        for (const char **m = markers_dirs; m && *m; m++) {
            char candidate[PATH_MAX];
            snprintf(candidate, sizeof(candidate), "%s/%s", cur, *m);
            if (msst_is_dir(candidate)) {
                snprintf(out, PATH_MAX, "%s", cur);
                return true;
            }
        }

        // Walk up one directory.
        char *slash = strrchr(cur, '/');
        if (!slash || slash == cur) break;
        *slash = 0;
    }

    return false;
}

static bool msst_determine_workspace_root(char out[PATH_MAX]) {
    // Compatibility aliases:
    // - SEATBELT_SANDBOX_WORKSPACE_ROOT: historical name (SwiftPM-centric)
    // - SANDBOX_WORKSPACE_ROOT: preferred generic alias for other languages
    const char *override_root = getenv("SEATBELT_SANDBOX_WORKSPACE_ROOT");
    if (!override_root || !*override_root) override_root = getenv("SANDBOX_WORKSPACE_ROOT");
    if (override_root && *override_root) {
        snprintf(out, PATH_MAX, "%s", override_root);
        return true;
    }

    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd)) == NULL) return false;
    return msst_find_workspace_root_from(cwd, out);
}

static void msst_build_run_id(char out[64]) {
    time_t t = time(NULL);
    struct tm tmv;
    localtime_r(&t, &tmv);
    pid_t pid = getpid();

    snprintf(out, 64, "%04d%02d%02d-%02d%02d%02d-pid%d",
             tmv.tm_year + 1900,
             tmv.tm_mon + 1,
             tmv.tm_mday,
             tmv.tm_hour,
             tmv.tm_min,
             tmv.tm_sec,
             (int)pid);
}

static bool msst_is_write_open_flags(int oflag) {
    if ((oflag & O_ACCMODE) == O_WRONLY) return true;
    if ((oflag & O_ACCMODE) == O_RDWR) return true;
    if (oflag & O_CREAT) return true;
    if (oflag & O_TRUNC) return true;
    return false;
}

static bool msst_is_within_prefix(const char *path, const char *prefix) {
    if (!path || !prefix) return false;
    size_t n = strlen(prefix);
    if (n == 0) return false;
    if (strncmp(path, prefix, n) != 0) return false;
    // Ensure directory boundary ("/foo" matches "/foo" or "/foo/bar", not "/foobar").
    return (path[n] == 0 || path[n] == '/');
}

static bool msst_write_allowed_path_string(const char *abs_path) {
    if (!abs_path || !*abs_path) return false;

    // Allow common harmless sinks.
    if (strcmp(abs_path, "/dev/null") == 0) return true;

    // Allowed: repo-local sandbox root and repo workspace.
    if (g_sandbox_root[0] && msst_is_within_prefix(abs_path, g_sandbox_root)) return true;
    if (g_workspace_root[0] && msst_is_within_prefix(abs_path, g_workspace_root)) return true;

    // Optional compatibility loosening: allow common system temp locations.
    // Disabled by default to preserve the “workspace-only writes” guarantee.
    if (g_allow_system_tmp) {
        if (msst_is_within_prefix(abs_path, "/tmp")) return true;
        if (msst_is_within_prefix(abs_path, "/private/tmp")) return true;
        if (msst_is_within_prefix(abs_path, "/var/tmp")) return true;
        if (msst_is_within_prefix(abs_path, "/private/var/tmp")) return true;
        if (msst_is_within_prefix(abs_path, "/var/folders")) return true;
        if (msst_is_within_prefix(abs_path, "/private/var/folders")) return true;
    }

    return false;
}

static bool msst_make_abs_path(const char *path, char out[PATH_MAX]) {
    if (!path || !*path) return false;
    if (path[0] == '/') {
        snprintf(out, PATH_MAX, "%s", path);
        return true;
    }

    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd)) == NULL) return false;
    snprintf(out, PATH_MAX, "%s/%s", cwd, path);
    return true;
}

static void msst_json_escape_fd(int fd, const char *s) {
    if (!s) return;
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') {
            dprintf(fd, "\\%c", c);
        } else if (c == '\n') {
            dprintf(fd, "\n");
        } else if (c == '\r') {
            dprintf(fd, "\r");
        } else if (c == '	') {
            dprintf(fd, "\t");
        } else if (c < 0x20) {
            dprintf(fd, "\\u%04x", (unsigned)c);
        } else {
            dprintf(fd, "%c", c);
        }
    }
}

static void msst_log_json_event(
    const char *op,
    const char *path,
    const char *decision,
    int err
) {
    if (g_log_path[0] == 0) return;

    // Avoid recursion (logging uses open/write).
    if (g_in_hook) return;
    g_in_hook = 1;

    msst_resolve_real_functions();

    int fd = real_open(g_log_path, O_CREAT | O_APPEND | O_WRONLY, 0644);
    if (fd >= 0) {
        void *frames[64];
        int nframes = backtrace(frames, 64);
        char **symbols = backtrace_symbols(frames, nframes);

        long long ts = (long long)time(NULL);
        pid_t pid = getpid();

        dprintf(fd, "{\"ts\":%lld,\"pid\":%d,", ts, (int)pid);

        dprintf(fd, "\"op\":");
        if (op) {
            dprintf(fd, "\"");
            msst_json_escape_fd(fd, op);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"path\":");
        if (path) {
            dprintf(fd, "\"");
            msst_json_escape_fd(fd, path);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"decision\":");
        if (decision) {
            dprintf(fd, "\"");
            msst_json_escape_fd(fd, decision);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"errno\":%d,\"bt\":[", err);

        if (symbols) {
            for (int i = 0; i < nframes; i++) {
                if (i) dprintf(fd, ",");
                dprintf(fd, "\"");
                msst_json_escape_fd(fd, symbols[i] ? symbols[i] : "");
                dprintf(fd, "\"");
            }
        }

        dprintf(fd, "]}\n");
        if (symbols) free(symbols);
        close(fd);
    }

    g_in_hook = 0;
}

// -----------------------------
// SBPL profile
// -----------------------------

// Write-guard + optional network-guard profile.
//
// Pattern:
// - Start permissive (allow default) to avoid needing an enormous allowlist for reads/frameworks.
// - Deny writes by default (deny file-write*), then allow writes only in known-safe roots.
// - Optionally deny IP networking (deny network-*) with narrow filters so AF_UNIX local IPC can keep working.
//
// NOTE: Rule ordering matters in practice for SBPL. A common working pattern is "broad deny, then allow exceptions".

static void msst_sbpl_append(char *out, size_t cap, size_t *pos, const char *s) {
    if (!out || !pos || !s) return;
    size_t n = strlen(s);
    if (*pos + n + 1 >= cap) return;
    memcpy(out + *pos, s, n);
    *pos += n;
    out[*pos] = 0;
}

static void msst_build_profile(char out[8192]) {
    size_t pos = 0;
    out[0] = 0;

    msst_sbpl_append(out, 8192, &pos, "(version 1)\n");
    msst_sbpl_append(out, 8192, &pos, "(allow default)\n");

    // Filesystem write restrictions (primary goal).
    msst_sbpl_append(out, 8192, &pos, "(deny file-write*)\n");
    msst_sbpl_append(out, 8192, &pos,
        "(allow file-write-data (require-all (path \"/dev/null\") (vnode-type CHARACTER-DEVICE)))\n");

    msst_sbpl_append(out, 8192, &pos, "(allow file-write*\n");
    msst_sbpl_append(out, 8192, &pos, "  (subpath (param \"WORKSPACE_ROOT\"))\n");
    msst_sbpl_append(out, 8192, &pos, "  (subpath (param \"SANDBOX_ROOT\"))\n");
    if (g_allow_system_tmp) {
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/tmp\")\n");
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/tmp\")\n");
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/var/tmp\")\n");
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/var/tmp\")\n");
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/var/folders\")\n");
        msst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/var/folders\")\n");
    }
    msst_sbpl_append(out, 8192, &pos, ")\n");

    // Network restrictions (optional).
    //
    // We restrict IP networking using the ip-based filters, so AF_UNIX sockets are unaffected unless you
    // add explicit unix-socket rules. This is important for local IPC patterns (ssh-agent, docker, etc.).
    //
    // Modes:
    // - deny (default): block IP outbound + bind + inbound.
    // - localhost: allow only localhost IP networking; deny everything else.
    // - allow: no network restrictions.
    // - allowlist: block outbound except for the allowlist; also deny bind (no servers).
    if (g_network_mode != MSST_NET_ALLOW) {
        msst_sbpl_append(out, 8192, &pos, "\n; Network\n");

        // Deny outbound IP connections by default.
        msst_sbpl_append(out, 8192, &pos, "(deny network-outbound (remote ip \"*:*\"))\n");

        // Deny binding/listening on IP sockets by default (prevents accidental servers).
        msst_sbpl_append(out, 8192, &pos, "(deny network-bind (local ip \"*:*\"))\n");

        if (g_network_mode == MSST_NET_DENY || g_network_mode == MSST_NET_LOCALHOST) {
            // Deny inbound socket reads by default for non-local interfaces (defense in depth).
            msst_sbpl_append(out, 8192, &pos, "(deny network-inbound (local ip \"*:*\"))\n");
        }

        if (g_network_mode == MSST_NET_LOCALHOST) {
            // Allow loopback-only networking.
            msst_sbpl_append(out, 8192, &pos, "(allow network-outbound (remote ip \"localhost:*\"))\n");
            msst_sbpl_append(out, 8192, &pos, "(allow network-bind (local ip \"localhost:*\"))\n");
            msst_sbpl_append(out, 8192, &pos, "(allow network-inbound (local ip \"localhost:*\"))\n");
        } else if (g_network_mode == MSST_NET_ALLOWLIST) {
            for (int i = 0; i < g_net_allow_count; i++) {
                // Allow coarse allowlist entries, e.g. "localhost:43128" or "*:443".
                msst_sbpl_append(out, 8192, &pos, "(allow network-outbound (remote ip \"");
                msst_sbpl_append(out, 8192, &pos, g_net_allow[i]);
                msst_sbpl_append(out, 8192, &pos, "\"))\n");
            }
        }
    }
}

static void msst_apply_seatbelt_or_fail(void) {
    // Apply sandbox profile.
    const char *params[] = {
        "WORKSPACE_ROOT", g_workspace_root,
        "SANDBOX_ROOT", g_sandbox_root,
        NULL
    };

    char *errbuf = NULL;

#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    char profile[8192];
    msst_build_profile(profile);
    int rc = sandbox_init_with_parameters(profile, 0, params, &errbuf);
#pragma clang diagnostic pop

    if (rc != 0) {
        if (errbuf) {
            msst_stderr("macos-sandbox-testing", errbuf);
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
            sandbox_free_error(errbuf);
#pragma clang diagnostic pop
        } else {
            msst_stderr("macos-sandbox-testing", "sandbox_init_with_parameters failed");
        }

        if (!msst_allow_unenforced()) {
            // Fail closed by default (a silent failure defeats the purpose).
            _exit(197);
        }
    }
}


// -----------------------------
// Self-test (safe: uses sandbox_check + denied non-blocking connect)
// -----------------------------

static void msst_run_selftest(void) {
    // 1) Confirm sandbox is present.
    int is_sandboxed = sandbox_check(getpid(), NULL, MSST_SANDBOX_FILTER_NONE);
    if (is_sandboxed == 0) {
        msst_stderr("macos-sandbox-testing", "SELFTEST FAILED: process is not sandboxed");
        _exit(198);
    }

    // 2) Confirm file-write* is allowed for the sandbox root (the guard would be unusable otherwise).
    {
        int rc = sandbox_check(getpid(), "file-write*", MSST_SANDBOX_FILTER_PATH, g_sandbox_root);
        if (rc != 0) {
            msst_stderr("macos-sandbox-testing", "SELFTEST FAILED: file-write* appears denied for sandbox root");
            _exit(199);
        }
    }

    // 2) If we have an original HOME, confirm file-write* to that path is denied.
    if (g_orig_home[0]) {
        int rc = sandbox_check(getpid(), "file-write*", MSST_SANDBOX_FILTER_PATH, g_orig_home);
        if (rc == 0) {
            msst_stderr("macos-sandbox-testing", "SELFTEST FAILED: file-write* appears allowed for original HOME");
            _exit(199);
        }
    }

    // 3) Optional network check when network is expected to be restricted.
    // Use a non-blocking connect() to a public IP and require a fast denial. This should be denied by
    // the kernel sandbox (and/or a higher-level sandbox) and should not transmit packets.
    if (g_network_mode == MSST_NET_DENY || g_network_mode == MSST_NET_LOCALHOST) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd >= 0) {
            int fl = fcntl(fd, F_GETFL, 0);
            if (fl >= 0) (void)fcntl(fd, F_SETFL, fl | O_NONBLOCK);

            struct sockaddr_in sa;
            memset(&sa, 0, sizeof(sa));
            sa.sin_family = AF_INET;
            sa.sin_port = htons(443);
            sa.sin_addr.s_addr = htonl(0x01010101u); // 1.1.1.1

            // Bypass our own connect() interposer so we exercise the kernel policy.
            int saved = g_in_hook;
            g_in_hook = 1;
            errno = 0;
            int rc = connect(fd, (const struct sockaddr *)&sa, (socklen_t)sizeof(sa));
            int e = errno;
            g_in_hook = saved;

            close(fd);

            if (rc == 0 || e == EINPROGRESS) {
                msst_stderr("macos-sandbox-testing", "SELFTEST FAILED: outbound connect was not denied as expected");
                _exit(200);
            }
            if (e != EPERM && e != EACCES) {
                msst_stderr("macos-sandbox-testing", "SELFTEST FAILED: unexpected error from outbound connect (expected EPERM/EACCES)");
                _exit(200);
            }

            msst_log_json_event("selftest.network", "inet:1.1.1.1:443", "deny", e);
        }
    }

    if (msst_verbose()) {
        msst_stderr("macos-sandbox-testing", "SELFTEST OK");
    }
}

// -----------------------------
// Path redirection for redirect mode
// -----------------------------

static void msst_basename(const char *path, char out[NAME_MAX]) {
    if (!path || !*path) {
        snprintf(out, NAME_MAX, "file");
        return;
    }
    const char *slash = strrchr(path, '/');
    const char *base = slash ? slash + 1 : path;
    if (!*base) base = "file";
    snprintf(out, NAME_MAX, "%s", base);
}

static uint64_t msst_fnv1a64(const char *s) {
    // Simple, dependency-free hash for redirect-path disambiguation.
    const uint64_t prime = 1099511628211ULL;
    uint64_t h = 1469598103934665603ULL;
    if (!s) return h;
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        h ^= (uint64_t)(*p);
        h *= prime;
    }
    return h;
}

static void msst_hex64(uint64_t v, char out[17]) {
    static const char *hex = "0123456789abcdef";
    for (int i = 0; i < 16; i++) {
        int shift = (15 - i) * 4;
        out[i] = hex[(v >> shift) & 0xF];
    }
    out[16] = 0;
}

static bool msst_make_redirect_path(const char *orig_abs, char out[PATH_MAX]) {
    if (!g_sandbox_root[0]) return false;

    char base[NAME_MAX];
    msst_basename(orig_abs, base);

    char hex[17];
    msst_hex64(msst_fnv1a64(orig_abs), hex);

    // Keep the basename for readability; prefix with a stable hash to avoid collisions.
    snprintf(out, PATH_MAX, "%s/redirect/%s-%s", g_sandbox_root, hex, base);

    // Ensure parent exists.
    char parent[PATH_MAX];
    snprintf(parent, sizeof(parent), "%s/redirect", g_sandbox_root);
    if (!msst_mkdir_p(parent, 0755)) return false;
    return true;
}

// -----------------------------
// Interposed functions
// -----------------------------

typedef int (*msst_open_fn_t)(const char *path, int oflag, ...);
typedef int (*msst_openat_fn_t)(int fd, const char *path, int oflag, ...);

static int msst_open_impl_with(
    const char *op,
    msst_open_fn_t real_fn,
    const char *path,
    int oflag,
    mode_t mode,
    bool has_mode
) {
    msst_resolve_real_functions();
    if (!real_fn) real_fn = real_open;

    if (g_in_hook) {
        if (has_mode) return real_fn(path, oflag, mode);
        return real_fn(path, oflag);
    }

    if (!g_tripwire_active || !g_tripwire_enabled) {
        if (has_mode) return real_fn(path, oflag, mode);
        return real_fn(path, oflag);
    }

    g_in_hook = 1;

    const bool is_write = msst_is_write_open_flags(oflag);
    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (is_write && abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event(op, abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r;
                if (has_mode) r = real_fn(redir, oflag, mode);
                else r = real_fn(redir, oflag);
                g_in_hook = 0;
                return r;
            }
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }

        if (msst_verbose()) {
            fprintf(stderr, "[macos-sandbox-testing] deny %s(write): %s\n", op ? op : "open", abs);
        }
    }

    int r;
    if (has_mode) r = real_fn(path, oflag, mode);
    else r = real_fn(path, oflag);

    g_in_hook = 0;
    return r;
}

static int msst_openat_impl_with(
    const char *op,
    msst_openat_fn_t real_fn,
    int fd,
    const char *path,
    int oflag,
    mode_t mode,
    bool has_mode
) {
    msst_resolve_real_functions();
    if (!real_fn) real_fn = real_openat;

    if (g_in_hook) {
        if (has_mode) return real_fn(fd, path, oflag, mode);
        return real_fn(fd, path, oflag);
    }

    if (!g_tripwire_active || !g_tripwire_enabled) {
        if (has_mode) return real_fn(fd, path, oflag, mode);
        return real_fn(fd, path, oflag);
    }

    g_in_hook = 1;

    const bool is_write = msst_is_write_open_flags(oflag);
    // Best-effort resolution: if relative, treat as cwd-relative (dirfd-specific resolution is complex).
    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (is_write && abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event(op, abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r;
                if (has_mode) r = real_fn(fd, redir, oflag, mode);
                else r = real_fn(fd, redir, oflag);
                g_in_hook = 0;
                return r;
            }
        } else if (g_mode == MSST_MODE_STRICT) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }

        if (msst_verbose()) {
            fprintf(stderr, "[macos-sandbox-testing] deny %s(write): %s\n", op ? op : "openat", abs);
        }
    }

    int r;
    if (has_mode) r = real_fn(fd, path, oflag, mode);
    else r = real_fn(fd, path, oflag);

    g_in_hook = 0;
    return r;
}

int msst_open(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_open_impl_with("open", real_open, path, oflag, mode, has_mode);
}

int msst_open_nocancel(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_open_impl_with("open$NOCANCEL", real_open_nocancel, path, oflag, mode, has_mode);
}

int msst_dunder_open(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_open_impl_with("__open", real_dunder_open, path, oflag, mode, has_mode);
}

int msst_dunder_open_nocancel(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_open_impl_with("__open_nocancel", real_dunder_open_nocancel, path, oflag, mode, has_mode);
}

int msst_openat(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_openat_impl_with("openat", real_openat, fd, path, oflag, mode, has_mode);
}

int msst_openat_nocancel(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_openat_impl_with("openat$NOCANCEL", real_openat_nocancel, fd, path, oflag, mode, has_mode);
}

int msst_dunder_openat(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_openat_impl_with("__openat", real_dunder_openat, fd, path, oflag, mode, has_mode);
}

int msst_dunder_openat_nocancel(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return msst_openat_impl_with(
        "__openat_nocancel",
        real_dunder_openat_nocancel,
        fd,
        path,
        oflag,
        mode,
        has_mode
    );
}

int msst_creat(const char *path, mode_t mode) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_creat(path, mode);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_creat(path, mode);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event("creat", abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r = real_creat(redir, mode);
                g_in_hook = 0;
                return r;
            }
        } else if (g_mode == MSST_MODE_STRICT) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_creat(path, mode);
    g_in_hook = 0;
    return r;
}

int msst_unlink(const char *path) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_unlink(path);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_unlink(path);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event("unlink", abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r = real_unlink(redir);
                if (r != 0 && errno == ENOENT) r = 0;
                g_in_hook = 0;
                return r;
            }
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_unlink(path);
    g_in_hook = 0;
    return r;
}

int msst_rename(const char *old, const char *newp) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_rename(old, newp);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_rename(old, newp);
    g_in_hook = 1;

    char abs_old[PATH_MAX];
    bool abs_old_ok = msst_make_abs_path(old, abs_old);

    char abs_new[PATH_MAX];
    bool abs_new_ok = msst_make_abs_path(newp, abs_new);

    const bool old_oob = abs_old_ok && !msst_write_allowed_path_string(abs_old);
    const bool new_oob = abs_new_ok && !msst_write_allowed_path_string(abs_new);

    if (old_oob || new_oob) {
        msst_log_json_event(
            "rename",
            abs_new_ok ? abs_new : newp,
            (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny",
            EPERM
        );

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            const char *old_arg = old;
            const char *new_arg = newp;

            char redir_old[PATH_MAX];
            char redir_new[PATH_MAX];

            if (old_oob && abs_old_ok && msst_make_redirect_path(abs_old, redir_old)) {
                old_arg = redir_old;
            }
            if (new_oob && abs_new_ok && msst_make_redirect_path(abs_new, redir_new)) {
                new_arg = redir_new;
            }

            int r = real_rename(old_arg, new_arg);
            g_in_hook = 0;
            return r;
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_rename(old, newp);
    g_in_hook = 0;
    return r;
}

int msst_mkdir(const char *path, mode_t mode) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_mkdir(path, mode);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_mkdir(path, mode);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event("mkdir", abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r = real_mkdir(redir, mode);
                g_in_hook = 0;
                return r;
            }
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_mkdir(path, mode);
    g_in_hook = 0;
    return r;
}

int msst_rmdir(const char *path) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_rmdir(path);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_rmdir(path);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event("rmdir", abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r = real_rmdir(redir);
                if (r != 0 && errno == ENOENT) r = 0;
                g_in_hook = 0;
                return r;
            }
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_rmdir(path);
    g_in_hook = 0;
    return r;
}

int msst_truncate(const char *path, off_t length) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_truncate(path, length);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_truncate(path, length);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = msst_make_abs_path(path, abs);

    if (abs_ok && !msst_write_allowed_path_string(abs)) {
        msst_log_json_event("truncate", abs, (g_mode == MSST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == MSST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == MSST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (msst_make_redirect_path(abs, redir)) {
                int r = real_truncate(redir, length);
                g_in_hook = 0;
                return r;
            }
        } else {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_truncate(path, length);
    g_in_hook = 0;
    return r;
}

int msst_ftruncate(int fd, off_t length) {
    msst_resolve_real_functions();

    if (g_in_hook) return real_ftruncate(fd, length);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_ftruncate(fd, length);
    g_in_hook = 1;

    // For simplicity, do not attempt to map fd -> path; rely on Seatbelt for true enforcement.
    // Still log the mutation attempt.
    msst_log_json_event("ftruncate", "<fd>", (g_mode == MSST_MODE_STRICT) ? "allow" : "allow", 0);

    int r = real_ftruncate(fd, length);
    g_in_hook = 0;
    return r;
}


// -----------------------------
// Network tripwire (dyld interposing)
// -----------------------------

static bool msst_sockaddr_is_loopback(const struct sockaddr *sa) {
    if (!sa) return false;
    if (sa->sa_family == AF_INET) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)sa;
        // 127.0.0.0/8
        uint32_t a = ntohl(sin->sin_addr.s_addr);
        return ((a & 0xFF000000u) == 0x7F000000u);
    }
    if (sa->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 = (const struct sockaddr_in6 *)sa;
        if (IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) return true;
        if (IN6_IS_ADDR_V4MAPPED(&sin6->sin6_addr)) {
            // ::ffff:127.x.y.z
            const uint8_t *b = (const uint8_t *)&sin6->sin6_addr;
            return (b[12] == 127);
        }
        return false;
    }
    return false;
}

static uint16_t msst_sockaddr_port(const struct sockaddr *sa) {
    if (!sa) return 0;
    if (sa->sa_family == AF_INET) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)sa;
        return ntohs(sin->sin_port);
    }
    if (sa->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 = (const struct sockaddr_in6 *)sa;
        return ntohs(sin6->sin6_port);
    }
    return 0;
}

static void msst_format_sockaddr(const struct sockaddr *sa, socklen_t salen, char out[256]) {
    if (!out) return;
    out[0] = 0;
    if (!sa) {
        snprintf(out, 256, "<null>");
        return;
    }

    if (sa->sa_family == AF_INET && salen >= (socklen_t)sizeof(struct sockaddr_in)) {
        const struct sockaddr_in *sin = (const struct sockaddr_in *)sa;
        char ip[INET_ADDRSTRLEN] = {0};
        inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip));
        snprintf(out, 256, "inet:%s:%u", ip, (unsigned)ntohs(sin->sin_port));
        return;
    }

    if (sa->sa_family == AF_INET6 && salen >= (socklen_t)sizeof(struct sockaddr_in6)) {
        const struct sockaddr_in6 *sin6 = (const struct sockaddr_in6 *)sa;
        char ip[INET6_ADDRSTRLEN] = {0};
        inet_ntop(AF_INET6, &sin6->sin6_addr, ip, sizeof(ip));
        snprintf(out, 256, "inet6:[%s]:%u", ip, (unsigned)ntohs(sin6->sin6_port));
        return;
    }

    if (sa->sa_family == AF_UNIX && salen >= (socklen_t)sizeof(struct sockaddr_un)) {
        const struct sockaddr_un *sun = (const struct sockaddr_un *)sa;
        if (sun->sun_path[0]) {
            snprintf(out, 256, "unix:%s", sun->sun_path);
        } else {
            snprintf(out, 256, "unix:<anonymous>");
        }
        return;
    }

    snprintf(out, 256, "family:%d", (int)sa->sa_family);
    (void)salen;
}

static bool msst_net_match_allow_token(const char *tok, bool is_loopback, uint16_t port) {
    if (!tok || !*tok) return false;

    if (strcmp(tok, "*:*") == 0) return true;

    if (strncmp(tok, "localhost:", 10) == 0) {
        if (!is_loopback) return false;
        const char *ps = tok + 10;
        if (strcmp(ps, "*") == 0) return true;
        long p = strtol(ps, NULL, 10);
        if (p <= 0 || p > 65535) return false;
        return port == (uint16_t)p;
    }

    if (tok[0] == '*' && tok[1] == ':') {
        const char *ps = tok + 2;
        if (strcmp(ps, "*") == 0) return true;
        long p = strtol(ps, NULL, 10);
        if (p <= 0 || p > 65535) return false;
        return port == (uint16_t)p;
    }

    return false;
}

static bool msst_net_outbound_allowed(const struct sockaddr *sa) {
    if (!sa) return false;

    // Always allow AF_UNIX unless the caller opted into "deny-all" (not implemented here).
    if (sa->sa_family == AF_UNIX) return true;

    // Only gate IP families.
    if (sa->sa_family != AF_INET && sa->sa_family != AF_INET6) return true;

    if (g_network_mode == MSST_NET_ALLOW) return true;

    const bool is_loopback = msst_sockaddr_is_loopback(sa);
    const uint16_t port = msst_sockaddr_port(sa);

    if (g_network_mode == MSST_NET_LOCALHOST) {
        return is_loopback;
    }

    if (g_network_mode == MSST_NET_ALLOWLIST) {
        for (int i = 0; i < g_net_allow_count; i++) {
            if (msst_net_match_allow_token(g_net_allow[i], is_loopback, port)) return true;
        }
        return false;
    }

    // Default: deny.
    return false;
}

static bool msst_net_bind_allowed(const struct sockaddr *sa) {
    if (!sa) return false;

    // Always allow AF_UNIX binds by default.
    if (sa->sa_family == AF_UNIX) return true;

    if (sa->sa_family != AF_INET && sa->sa_family != AF_INET6) return true;

    if (g_network_mode == MSST_NET_ALLOW) return true;
    if (g_network_mode == MSST_NET_LOCALHOST) return msst_sockaddr_is_loopback(sa);

    // Deny bind in deny + allowlist modes (treat as "no servers").
    return false;
}

int msst_connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
    msst_resolve_real_functions();
    if (!real_connect) {
        errno = ENOSYS;
        return -1;
    }

    if (g_in_hook) return real_connect(sockfd, addr, addrlen);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_connect(sockfd, addr, addrlen);

    g_in_hook = 1;

    bool allow = msst_net_outbound_allowed(addr);

    if (!allow) {
        char ep[256];
        msst_format_sockaddr(addr, addrlen, ep);
        msst_log_json_event("net.connect", ep, "deny", EPERM);

        if (g_mode != MSST_MODE_REPORT_ONLY) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_connect(sockfd, addr, addrlen);
    g_in_hook = 0;
    return r;
}

int msst_bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
    msst_resolve_real_functions();
    if (!real_bind) {
        errno = ENOSYS;
        return -1;
    }

    if (g_in_hook) return real_bind(sockfd, addr, addrlen);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_bind(sockfd, addr, addrlen);

    g_in_hook = 1;

    bool allow = msst_net_bind_allowed(addr);

    if (!allow) {
        char ep[256];
        msst_format_sockaddr(addr, addrlen, ep);
        msst_log_json_event("net.bind", ep, "deny", EPERM);

        if (g_mode != MSST_MODE_REPORT_ONLY) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_bind(sockfd, addr, addrlen);
    g_in_hook = 0;
    return r;
}

// dyld interposing table
struct msst_interpose {
    const void *replacement;
    const void *replacee;
};

// Some Darwin libc/kernel entrypoints use `$` in the symbol name (e.g. `_open$NOCANCEL`).
// C identifiers cannot contain `$`, so we declare replacee aliases with an asm label.
extern int msst_replacee_open_nocancel(const char *path, int oflag, ...)
    __attribute__((weak_import))
    __asm__("_open$NOCANCEL");
extern int msst_replacee_openat_nocancel(int fd, const char *path, int oflag, ...)
    __attribute__((weak_import))
    __asm__("_openat$NOCANCEL");

// libsystem_kernel also exports underscored syscall wrappers (e.g. `__open_nocancel`).
extern int __open(const char *path, int oflag, ...);
extern int __open_nocancel(const char *path, int oflag, ...);
extern int __openat(int fd, const char *path, int oflag, ...);
extern int __openat_nocancel(int fd, const char *path, int oflag, ...);

#define MSST_INTERPOSE(_replacement, _replacee) \
    __attribute__((used)) static const struct msst_interpose msst_interpose_##_replacee \
        __attribute__((section("__DATA,__interpose"))) = { \
            (const void *)(unsigned long)&_replacement, \
            (const void *)(unsigned long)&_replacee \
        };

MSST_INTERPOSE(msst_open, open)
MSST_INTERPOSE(msst_openat, openat)
MSST_INTERPOSE(msst_open_nocancel, msst_replacee_open_nocancel)
MSST_INTERPOSE(msst_openat_nocancel, msst_replacee_openat_nocancel)
MSST_INTERPOSE(msst_dunder_open, __open)
MSST_INTERPOSE(msst_dunder_open_nocancel, __open_nocancel)
MSST_INTERPOSE(msst_dunder_openat, __openat)
MSST_INTERPOSE(msst_dunder_openat_nocancel, __openat_nocancel)
MSST_INTERPOSE(msst_creat, creat)
MSST_INTERPOSE(msst_unlink, unlink)
MSST_INTERPOSE(msst_rename, rename)
MSST_INTERPOSE(msst_mkdir, mkdir)
MSST_INTERPOSE(msst_rmdir, rmdir)
MSST_INTERPOSE(msst_truncate, truncate)
MSST_INTERPOSE(msst_ftruncate, ftruncate)
MSST_INTERPOSE(msst_connect, connect)
MSST_INTERPOSE(msst_bind, bind)

// -----------------------------
// Bootstrap (constructor)
// -----------------------------

__attribute__((constructor))
static void msst_bootstrap(void) {
    if (msst_is_disabled()) return;

    msst_resolve_real_functions();

    g_mode = msst_parse_mode();
    g_allow_system_tmp = msst_allow_system_tmp();
    g_network_mode = msst_parse_network_mode();
    g_net_allow_count = msst_parse_network_allowlist(g_net_allow, MSST_MAX_NET_ALLOWLIST);
    if (g_net_allow_count > 0 && g_network_mode != MSST_NET_ALLOW && g_network_mode != MSST_NET_LOCALHOST) {
        // If an allowlist is present, prefer allowlist mode unless the user explicitly asked for full allow.
        g_network_mode = MSST_NET_ALLOWLIST;
    }
    g_tripwire_enabled = msst_tripwire_enabled();

    // Determine workspace root.
    if (!msst_determine_workspace_root(g_workspace_root)) {
        msst_stderr(
            "macos-sandbox-testing",
            "could not determine workspace root (no project marker found). Set SEATBELT_SANDBOX_WORKSPACE_ROOT or SANDBOX_WORKSPACE_ROOT to override."
        );
        if (!msst_allow_unenforced()) _exit(196);
        return;
    }

    // Capture original HOME before redirecting.
    const char *home = getenv("HOME");
    if (home && *home) snprintf(g_orig_home, sizeof(g_orig_home), "%s", home);

    // Build sandbox root under .build.
    char run_id[64];
    msst_build_run_id(run_id);
    snprintf(g_sandbox_root, sizeof(g_sandbox_root), "%s/.build/macos-sandbox-testing/%s", g_workspace_root, run_id);
    snprintf(g_fake_home, sizeof(g_fake_home), "%s/home", g_sandbox_root);
    snprintf(g_fake_tmp, sizeof(g_fake_tmp), "%s/tmp", g_sandbox_root);
    snprintf(g_log_path, sizeof(g_log_path), "%s/logs/events.jsonl", g_sandbox_root);

    // Create directories (before applying sandbox).
    if (!msst_mkdir_p(g_sandbox_root, 0755) ||
        !msst_mkdir_p(g_fake_home, 0755) ||
        !msst_mkdir_p(g_fake_tmp, 0755)) {
        msst_stderr("macos-sandbox-testing", "failed to create sandbox directories under .build");
        if (!msst_allow_unenforced()) _exit(195);
        return;
    }
    {
        char logs_dir[PATH_MAX];
        snprintf(logs_dir, sizeof(logs_dir), "%s/logs", g_sandbox_root);
        (void)msst_mkdir_p(logs_dir, 0755);
    }

    // Redirect home and temp.
    if (!msst_preserve_home()) {
        setenv("HOME", g_fake_home, 1);
        setenv("CFFIXED_USER_HOME", g_fake_home, 1);
    }
    setenv("TMPDIR", g_fake_tmp, 1);

    if (msst_verbose()) {
        fprintf(stderr, "[macos-sandbox-testing] workspace=%s\n", g_workspace_root);
        fprintf(stderr, "[macos-sandbox-testing] sandbox=%s\n", g_sandbox_root);
    }

    // Activate tripwire enforcement for subsequent filesystem mutations.
    g_tripwire_active = true;

    // Apply Seatbelt sandbox (kernel write-guard).
    msst_apply_seatbelt_or_fail();

    // Always emit a startup marker so the sandbox root is discoverable.
    msst_log_json_event("bootstrap", g_sandbox_root, "ok", 0);

    // Optional self-test (safe: sandbox_check only).
    if (msst_selftest_enabled()) {
        msst_run_selftest();
    }
}

#else

// Non-Apple platforms: no-op.
__attribute__((constructor))
static void msst_bootstrap_nonapple(void) {}

#endif

// Linker anchor:
// SwiftPM compiles C targets into static libraries, and the linker will only pull in
// object files that satisfy referenced symbols. The sandbox bootstrap is driven by a
// constructor in this translation unit, so we expose a tiny symbol that Swift code
// can reference to force this object file to be linked in.
void msst_force_link(void) {}
