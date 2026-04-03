// swiftpm-sandbox-testing
//
// Drop this file into a SwiftPM executable or test target (under Sources/<Target>/ or Tests/<Target>/).
// It bootstraps a dev/test safety boundary for direct `swift run` / `swift test` executions:
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

enum swiftpmst_sandbox_filter_type {
    SWIFTPMST_SANDBOX_FILTER_NONE = 0,
    SWIFTPMST_SANDBOX_FILTER_PATH = 1,
};

extern int sandbox_check(pid_t pid, const char *operation, int type, ...);

// -----------------------------
// Configuration (env-driven)
// -----------------------------

typedef enum {
    SWIFTPMST_MODE_STRICT = 0,
    SWIFTPMST_MODE_REDIRECT = 1,
    SWIFTPMST_MODE_REPORT_ONLY = 2,
} swiftpmst_mode_t;

static swiftpmst_mode_t swiftpmst_parse_mode(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_MODE");
    if (!s || !*s) return SWIFTPMST_MODE_STRICT;
    if (strcmp(s, "strict") == 0) return SWIFTPMST_MODE_STRICT;
    if (strcmp(s, "redirect") == 0) return SWIFTPMST_MODE_REDIRECT;
    if (strcmp(s, "report-only") == 0) return SWIFTPMST_MODE_REPORT_ONLY;
    return SWIFTPMST_MODE_STRICT;
}

static bool swiftpmst_is_disabled(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_DISABLE");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool swiftpmst_allow_unenforced(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_ALLOW_UNENFORCED");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool swiftpmst_selftest_enabled(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_SELFTEST");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool swiftpmst_verbose(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_LOG_LEVEL");
    return (s && strcmp(s, "verbose") == 0);
}

static bool swiftpmst_allow_system_tmp(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_ALLOW_SYSTEM_TMP");
    if (!s || !*s) return true;
    return strcmp(s, "0") != 0;
}

static bool swiftpmst_preserve_home(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_PRESERVE_HOME");
    return (s && *s && strcmp(s, "0") != 0);
}

static bool swiftpmst_tripwire_enabled(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_TRIPWIRE");
    if (!s || !*s) return true;
    return strcmp(s, "0") != 0;
}


// -----------------------------
// Network configuration (env-driven)
// -----------------------------

typedef enum {
    SWIFTPMST_NET_DENY = 0,       // default: block IP networking (outbound + bind)
    SWIFTPMST_NET_LOCALHOST = 1,  // allow localhost-only TCP/UDP (best-effort; see docs)
    SWIFTPMST_NET_ALLOW = 2,      // allow all network (least restrictive)
    SWIFTPMST_NET_ALLOWLIST = 3,  // allow outbound to a coarse allowlist (see SWIFTPM_SANDBOX_NETWORK_ALLOWLIST)
} swiftpmst_network_mode_t;

#define SWIFTPMST_MAX_NET_ALLOWLIST 16
#define SWIFTPMST_MAX_NET_TOKEN 63

static swiftpmst_network_mode_t swiftpmst_parse_network_mode(void) {
    const char *s = getenv("SWIFTPM_SANDBOX_NETWORK");
    if (!s || !*s) return SWIFTPMST_NET_DENY;
    if (strcmp(s, "deny") == 0) return SWIFTPMST_NET_DENY;
    if (strcmp(s, "localhost") == 0) return SWIFTPMST_NET_LOCALHOST;
    if (strcmp(s, "allow") == 0) return SWIFTPMST_NET_ALLOW;
    if (strcmp(s, "allowlist") == 0) return SWIFTPMST_NET_ALLOWLIST;
    // Fail closed for unknown values.
    return SWIFTPMST_NET_DENY;
}

static bool swiftpmst_net_token_is_safe(const char *t) {
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

static int swiftpmst_parse_network_allowlist(char out[][SWIFTPMST_MAX_NET_TOKEN + 1], int cap) {
    const char *raw = getenv("SWIFTPM_SANDBOX_NETWORK_ALLOWLIST");
    if (!raw || !*raw) return 0;

    // Copy into a local mutable buffer.
    char buf[1024];
    snprintf(buf, sizeof(buf), "%s", raw);

    int n = 0;
    char *save = NULL;
    for (char *tok = strtok_r(buf, ", \t\n", &save); tok && n < cap; tok = strtok_r(NULL, ", \t\n", &save)) {
        size_t len = strlen(tok);
        if (len == 0 || len > SWIFTPMST_MAX_NET_TOKEN) continue;
        if (!swiftpmst_net_token_is_safe(tok)) continue;
        snprintf(out[n], SWIFTPMST_MAX_NET_TOKEN + 1, "%s", tok);
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

static swiftpmst_mode_t g_mode = SWIFTPMST_MODE_STRICT;

static bool g_allow_system_tmp = false;
static swiftpmst_network_mode_t g_network_mode = SWIFTPMST_NET_DENY;
static int g_net_allow_count = 0;
static char g_net_allow[SWIFTPMST_MAX_NET_ALLOWLIST][SWIFTPMST_MAX_NET_TOKEN + 1] = {{0}};

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

static void swiftpmst_resolve_real_functions(void) {
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

static void swiftpmst_stderr(const char *prefix, const char *msg) {
    if (!prefix) prefix = "swiftpm-sandbox-testing";
    if (!msg) msg = "";
    fprintf(stderr, "[%s] %s\n", prefix, msg);
}

static bool swiftpmst_path_exists(const char *p) {
    struct stat st;
    return (p && *p && stat(p, &st) == 0);
}

static bool swiftpmst_is_dir(const char *p) {
    struct stat st;
    if (!p || !*p) return false;
    if (stat(p, &st) != 0) return false;
    return S_ISDIR(st.st_mode);
}

static bool swiftpmst_mkdir_p(const char *path, mode_t mode) {
    if (!path || !*path) return false;
    if (swiftpmst_is_dir(path)) return true;

    char tmp[PATH_MAX];
    snprintf(tmp, sizeof(tmp), "%s", path);

    size_t len = strlen(tmp);
    if (len == 0) return false;
    if (tmp[len - 1] == '/') tmp[len - 1] = 0;

    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            if (!swiftpmst_is_dir(tmp)) {
                if (real_mkdir(tmp, mode) != 0 && errno != EEXIST) return false;
            }
            *p = '/';
        }
    }
    if (real_mkdir(tmp, mode) != 0 && errno != EEXIST) return false;
    return true;
}

static bool swiftpmst_find_workspace_root_from(const char *start_dir, char out[PATH_MAX]) {
    if (!start_dir || !*start_dir) return false;

    char cur[PATH_MAX];
    snprintf(cur, sizeof(cur), "%s", start_dir);

    for (int depth = 0; depth < 12; depth++) {
        char candidate[PATH_MAX];
        snprintf(candidate, sizeof(candidate), "%s/Package.swift", cur);
        if (swiftpmst_path_exists(candidate)) {
            snprintf(out, PATH_MAX, "%s", cur);
            return true;
        }

        // Walk up one directory.
        char *slash = strrchr(cur, '/');
        if (!slash || slash == cur) break;
        *slash = 0;
    }

    return false;
}

static bool swiftpmst_determine_workspace_root(char out[PATH_MAX]) {
    const char *override_root = getenv("SWIFTPM_SANDBOX_WORKSPACE_ROOT");
    if (override_root && *override_root) {
        snprintf(out, PATH_MAX, "%s", override_root);
        return true;
    }

    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd)) == NULL) return false;
    return swiftpmst_find_workspace_root_from(cwd, out);
}

static void swiftpmst_build_run_id(char out[64]) {
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

static bool swiftpmst_is_write_open_flags(int oflag) {
    if ((oflag & O_ACCMODE) == O_WRONLY) return true;
    if ((oflag & O_ACCMODE) == O_RDWR) return true;
    if (oflag & O_CREAT) return true;
    if (oflag & O_TRUNC) return true;
    return false;
}

static bool swiftpmst_is_within_prefix(const char *path, const char *prefix) {
    if (!path || !prefix) return false;
    size_t n = strlen(prefix);
    if (n == 0) return false;
    if (strncmp(path, prefix, n) != 0) return false;
    // Ensure directory boundary ("/foo" matches "/foo" or "/foo/bar", not "/foobar").
    return (path[n] == 0 || path[n] == '/');
}

static bool swiftpmst_write_allowed_path_string(const char *abs_path) {
    if (!abs_path || !*abs_path) return false;

    // Allow common harmless sinks.
    if (strcmp(abs_path, "/dev/null") == 0) return true;

    // Allowed: repo-local sandbox root and repo workspace.
    if (g_sandbox_root[0] && swiftpmst_is_within_prefix(abs_path, g_sandbox_root)) return true;
    if (g_workspace_root[0] && swiftpmst_is_within_prefix(abs_path, g_workspace_root)) return true;

    // Optional compatibility loosening: allow common system temp locations.
    // Disabled by default to preserve the “workspace-only writes” guarantee.
    if (g_allow_system_tmp) {
        if (swiftpmst_is_within_prefix(abs_path, "/tmp")) return true;
        if (swiftpmst_is_within_prefix(abs_path, "/private/tmp")) return true;
        if (swiftpmst_is_within_prefix(abs_path, "/var/tmp")) return true;
        if (swiftpmst_is_within_prefix(abs_path, "/private/var/tmp")) return true;
        if (swiftpmst_is_within_prefix(abs_path, "/var/folders")) return true;
        if (swiftpmst_is_within_prefix(abs_path, "/private/var/folders")) return true;
    }

    return false;
}

static bool swiftpmst_make_abs_path(const char *path, char out[PATH_MAX]) {
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

static void swiftpmst_json_escape_fd(int fd, const char *s) {
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

static void swiftpmst_log_json_event(
    const char *op,
    const char *path,
    const char *decision,
    int err
) {
    if (g_log_path[0] == 0) return;

    // Avoid recursion (logging uses open/write).
    if (g_in_hook) return;
    g_in_hook = 1;

    swiftpmst_resolve_real_functions();

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
            swiftpmst_json_escape_fd(fd, op);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"path\":");
        if (path) {
            dprintf(fd, "\"");
            swiftpmst_json_escape_fd(fd, path);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"decision\":");
        if (decision) {
            dprintf(fd, "\"");
            swiftpmst_json_escape_fd(fd, decision);
            dprintf(fd, "\"");
        } else {
            dprintf(fd, "null");
        }

        dprintf(fd, ",\"errno\":%d,\"bt\":[", err);

        if (symbols) {
            for (int i = 0; i < nframes; i++) {
                if (i) dprintf(fd, ",");
                dprintf(fd, "\"");
                swiftpmst_json_escape_fd(fd, symbols[i] ? symbols[i] : "");
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

static void swiftpmst_sbpl_append(char *out, size_t cap, size_t *pos, const char *s) {
    if (!out || !pos || !s) return;
    size_t n = strlen(s);
    if (*pos + n + 1 >= cap) return;
    memcpy(out + *pos, s, n);
    *pos += n;
    out[*pos] = 0;
}

static void swiftpmst_build_profile(char out[8192]) {
    size_t pos = 0;
    out[0] = 0;

    swiftpmst_sbpl_append(out, 8192, &pos, "(version 1)\n");
    swiftpmst_sbpl_append(out, 8192, &pos, "(allow default)\n");

    // Filesystem write restrictions (primary goal).
    swiftpmst_sbpl_append(out, 8192, &pos, "(deny file-write*)\n");
    swiftpmst_sbpl_append(out, 8192, &pos,
        "(allow file-write-data (require-all (path \"/dev/null\") (vnode-type CHARACTER-DEVICE)))\n");

    swiftpmst_sbpl_append(out, 8192, &pos, "(allow file-write*\n");
    swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath (param \"WORKSPACE_ROOT\"))\n");
    swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath (param \"SANDBOX_ROOT\"))\n");
    if (g_allow_system_tmp) {
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/tmp\")\n");
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/tmp\")\n");
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/var/tmp\")\n");
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/var/tmp\")\n");
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/var/folders\")\n");
        swiftpmst_sbpl_append(out, 8192, &pos, "  (subpath \"/private/var/folders\")\n");
    }
    swiftpmst_sbpl_append(out, 8192, &pos, ")\n");

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
    if (g_network_mode != SWIFTPMST_NET_ALLOW) {
        swiftpmst_sbpl_append(out, 8192, &pos, "\n; Network\n");

        // Deny outbound IP connections by default.
        swiftpmst_sbpl_append(out, 8192, &pos, "(deny network-outbound (remote ip \"*:*\"))\n");

        // Deny binding/listening on IP sockets by default (prevents accidental servers).
        swiftpmst_sbpl_append(out, 8192, &pos, "(deny network-bind (local ip \"*:*\"))\n");

        if (g_network_mode == SWIFTPMST_NET_DENY || g_network_mode == SWIFTPMST_NET_LOCALHOST) {
            // Deny inbound socket reads by default for non-local interfaces (defense in depth).
            swiftpmst_sbpl_append(out, 8192, &pos, "(deny network-inbound (local ip \"*:*\"))\n");
        }

        if (g_network_mode == SWIFTPMST_NET_LOCALHOST) {
            // Allow loopback-only networking.
            swiftpmst_sbpl_append(out, 8192, &pos, "(allow network-outbound (remote ip \"localhost:*\"))\n");
            swiftpmst_sbpl_append(out, 8192, &pos, "(allow network-bind (local ip \"localhost:*\"))\n");
            swiftpmst_sbpl_append(out, 8192, &pos, "(allow network-inbound (local ip \"localhost:*\"))\n");
        } else if (g_network_mode == SWIFTPMST_NET_ALLOWLIST) {
            for (int i = 0; i < g_net_allow_count; i++) {
                // Allow coarse allowlist entries, e.g. "localhost:43128" or "*:443".
                swiftpmst_sbpl_append(out, 8192, &pos, "(allow network-outbound (remote ip \"");
                swiftpmst_sbpl_append(out, 8192, &pos, g_net_allow[i]);
                swiftpmst_sbpl_append(out, 8192, &pos, "\"))\n");
            }
        }
    }
}

static void swiftpmst_apply_seatbelt_or_fail(void) {
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
    swiftpmst_build_profile(profile);
    int rc = sandbox_init_with_parameters(profile, 0, params, &errbuf);
#pragma clang diagnostic pop

    if (rc != 0) {
        if (errbuf) {
            swiftpmst_stderr("swiftpm-sandbox-testing", errbuf);
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
            sandbox_free_error(errbuf);
#pragma clang diagnostic pop
        } else {
            swiftpmst_stderr("swiftpm-sandbox-testing", "sandbox_init_with_parameters failed");
        }

        if (!swiftpmst_allow_unenforced()) {
            // Fail closed by default (a silent failure defeats the purpose).
            _exit(197);
        }
    }
}


// -----------------------------
// Self-test (safe: uses sandbox_check + denied non-blocking connect)
// -----------------------------

static void swiftpmst_run_selftest(void) {
    // 1) Confirm sandbox is present.
    int is_sandboxed = sandbox_check(getpid(), NULL, SWIFTPMST_SANDBOX_FILTER_NONE);
    if (is_sandboxed == 0) {
        swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST FAILED: process is not sandboxed");
        _exit(198);
    }

    // 2) Confirm file-write* is allowed for the sandbox root (the guard would be unusable otherwise).
    {
        int rc = sandbox_check(getpid(), "file-write*", SWIFTPMST_SANDBOX_FILTER_PATH, g_sandbox_root);
        if (rc != 0) {
            swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST FAILED: file-write* appears denied for sandbox root");
            _exit(199);
        }
    }

    // 2) If we have an original HOME, confirm file-write* to that path is denied.
    if (g_orig_home[0]) {
        int rc = sandbox_check(getpid(), "file-write*", SWIFTPMST_SANDBOX_FILTER_PATH, g_orig_home);
        if (rc == 0) {
            swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST FAILED: file-write* appears allowed for original HOME");
            _exit(199);
        }
    }

    // 3) Optional network check when network is expected to be restricted.
    // Use a non-blocking connect() to a public IP and require a fast denial. This should be denied by
    // the kernel sandbox (and/or a higher-level sandbox) and should not transmit packets.
    if (g_network_mode == SWIFTPMST_NET_DENY || g_network_mode == SWIFTPMST_NET_LOCALHOST) {
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
                swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST FAILED: outbound connect was not denied as expected");
                _exit(200);
            }
            if (e != EPERM && e != EACCES) {
                swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST FAILED: unexpected error from outbound connect (expected EPERM/EACCES)");
                _exit(200);
            }

            swiftpmst_log_json_event("selftest.network", "inet:1.1.1.1:443", "deny", e);
        }
    }

    if (swiftpmst_verbose()) {
        swiftpmst_stderr("swiftpm-sandbox-testing", "SELFTEST OK");
    }
}

// -----------------------------
// Path redirection for redirect mode
// -----------------------------

static void swiftpmst_basename(const char *path, char out[NAME_MAX]) {
    if (!path || !*path) {
        snprintf(out, NAME_MAX, "file");
        return;
    }
    const char *slash = strrchr(path, '/');
    const char *base = slash ? slash + 1 : path;
    if (!*base) base = "file";
    snprintf(out, NAME_MAX, "%s", base);
}

static uint64_t swiftpmst_fnv1a64(const char *s) {
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

static void swiftpmst_hex64(uint64_t v, char out[17]) {
    static const char *hex = "0123456789abcdef";
    for (int i = 0; i < 16; i++) {
        int shift = (15 - i) * 4;
        out[i] = hex[(v >> shift) & 0xF];
    }
    out[16] = 0;
}

static bool swiftpmst_make_redirect_path(const char *orig_abs, char out[PATH_MAX]) {
    if (!g_sandbox_root[0]) return false;

    char base[NAME_MAX];
    swiftpmst_basename(orig_abs, base);

    char hex[17];
    swiftpmst_hex64(swiftpmst_fnv1a64(orig_abs), hex);

    // Keep the basename for readability; prefix with a stable hash to avoid collisions.
    snprintf(out, PATH_MAX, "%s/redirect/%s-%s", g_sandbox_root, hex, base);

    // Ensure parent exists.
    char parent[PATH_MAX];
    snprintf(parent, sizeof(parent), "%s/redirect", g_sandbox_root);
    if (!swiftpmst_mkdir_p(parent, 0755)) return false;
    return true;
}

// -----------------------------
// Interposed functions
// -----------------------------

typedef int (*swiftpmst_open_fn_t)(const char *path, int oflag, ...);
typedef int (*swiftpmst_openat_fn_t)(int fd, const char *path, int oflag, ...);

static int swiftpmst_open_impl_with(
    const char *op,
    swiftpmst_open_fn_t real_fn,
    const char *path,
    int oflag,
    mode_t mode,
    bool has_mode
) {
    swiftpmst_resolve_real_functions();
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

    const bool is_write = swiftpmst_is_write_open_flags(oflag);
    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (is_write && abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event(op, abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
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

        if (swiftpmst_verbose()) {
            fprintf(stderr, "[swiftpm-sandbox-testing] deny %s(write): %s\n", op ? op : "open", abs);
        }
    }

    int r;
    if (has_mode) r = real_fn(path, oflag, mode);
    else r = real_fn(path, oflag);

    g_in_hook = 0;
    return r;
}

static int swiftpmst_openat_impl_with(
    const char *op,
    swiftpmst_openat_fn_t real_fn,
    int fd,
    const char *path,
    int oflag,
    mode_t mode,
    bool has_mode
) {
    swiftpmst_resolve_real_functions();
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

    const bool is_write = swiftpmst_is_write_open_flags(oflag);
    // Best-effort resolution: if relative, treat as cwd-relative (dirfd-specific resolution is complex).
    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (is_write && abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event(op, abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
                int r;
                if (has_mode) r = real_fn(fd, redir, oflag, mode);
                else r = real_fn(fd, redir, oflag);
                g_in_hook = 0;
                return r;
            }
        } else if (g_mode == SWIFTPMST_MODE_STRICT) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }

        if (swiftpmst_verbose()) {
            fprintf(stderr, "[swiftpm-sandbox-testing] deny %s(write): %s\n", op ? op : "openat", abs);
        }
    }

    int r;
    if (has_mode) r = real_fn(fd, path, oflag, mode);
    else r = real_fn(fd, path, oflag);

    g_in_hook = 0;
    return r;
}

int swiftpmst_open(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_open_impl_with("open", real_open, path, oflag, mode, has_mode);
}

int swiftpmst_open_nocancel(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_open_impl_with("open$NOCANCEL", real_open_nocancel, path, oflag, mode, has_mode);
}

int swiftpmst_dunder_open(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_open_impl_with("__open", real_dunder_open, path, oflag, mode, has_mode);
}

int swiftpmst_dunder_open_nocancel(const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_open_impl_with("__open_nocancel", real_dunder_open_nocancel, path, oflag, mode, has_mode);
}

int swiftpmst_openat(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_openat_impl_with("openat", real_openat, fd, path, oflag, mode, has_mode);
}

int swiftpmst_openat_nocancel(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_openat_impl_with("openat$NOCANCEL", real_openat_nocancel, fd, path, oflag, mode, has_mode);
}

int swiftpmst_dunder_openat(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_openat_impl_with("__openat", real_dunder_openat, fd, path, oflag, mode, has_mode);
}

int swiftpmst_dunder_openat_nocancel(int fd, const char *path, int oflag, ...) {
    mode_t mode = 0;
    bool has_mode = false;
    if (oflag & O_CREAT) {
        va_list ap;
        va_start(ap, oflag);
        mode = (mode_t)va_arg(ap, int);
        va_end(ap);
        has_mode = true;
    }
    return swiftpmst_openat_impl_with(
        "__openat_nocancel",
        real_dunder_openat_nocancel,
        fd,
        path,
        oflag,
        mode,
        has_mode
    );
}

int swiftpmst_creat(const char *path, mode_t mode) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_creat(path, mode);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_creat(path, mode);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event("creat", abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
                int r = real_creat(redir, mode);
                g_in_hook = 0;
                return r;
            }
        } else if (g_mode == SWIFTPMST_MODE_STRICT) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_creat(path, mode);
    g_in_hook = 0;
    return r;
}

int swiftpmst_unlink(const char *path) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_unlink(path);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_unlink(path);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event("unlink", abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
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

int swiftpmst_rename(const char *old, const char *newp) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_rename(old, newp);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_rename(old, newp);
    g_in_hook = 1;

    char abs_old[PATH_MAX];
    bool abs_old_ok = swiftpmst_make_abs_path(old, abs_old);

    char abs_new[PATH_MAX];
    bool abs_new_ok = swiftpmst_make_abs_path(newp, abs_new);

    const bool old_oob = abs_old_ok && !swiftpmst_write_allowed_path_string(abs_old);
    const bool new_oob = abs_new_ok && !swiftpmst_write_allowed_path_string(abs_new);

    if (old_oob || new_oob) {
        swiftpmst_log_json_event(
            "rename",
            abs_new_ok ? abs_new : newp,
            (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny",
            EPERM
        );

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            const char *old_arg = old;
            const char *new_arg = newp;

            char redir_old[PATH_MAX];
            char redir_new[PATH_MAX];

            if (old_oob && abs_old_ok && swiftpmst_make_redirect_path(abs_old, redir_old)) {
                old_arg = redir_old;
            }
            if (new_oob && abs_new_ok && swiftpmst_make_redirect_path(abs_new, redir_new)) {
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

int swiftpmst_mkdir(const char *path, mode_t mode) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_mkdir(path, mode);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_mkdir(path, mode);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event("mkdir", abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
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

int swiftpmst_rmdir(const char *path) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_rmdir(path);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_rmdir(path);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event("rmdir", abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
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

int swiftpmst_truncate(const char *path, off_t length) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_truncate(path, length);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_truncate(path, length);
    g_in_hook = 1;

    char abs[PATH_MAX];
    bool abs_ok = swiftpmst_make_abs_path(path, abs);

    if (abs_ok && !swiftpmst_write_allowed_path_string(abs)) {
        swiftpmst_log_json_event("truncate", abs, (g_mode == SWIFTPMST_MODE_REDIRECT) ? "redirect" : "deny", EPERM);

        if (g_mode == SWIFTPMST_MODE_REPORT_ONLY) {
            // Fall through.
        } else if (g_mode == SWIFTPMST_MODE_REDIRECT) {
            char redir[PATH_MAX];
            if (swiftpmst_make_redirect_path(abs, redir)) {
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

int swiftpmst_ftruncate(int fd, off_t length) {
    swiftpmst_resolve_real_functions();

    if (g_in_hook) return real_ftruncate(fd, length);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_ftruncate(fd, length);
    g_in_hook = 1;

    // For simplicity, do not attempt to map fd -> path; rely on Seatbelt for true enforcement.
    // Still log the mutation attempt.
    swiftpmst_log_json_event("ftruncate", "<fd>", (g_mode == SWIFTPMST_MODE_STRICT) ? "allow" : "allow", 0);

    int r = real_ftruncate(fd, length);
    g_in_hook = 0;
    return r;
}


// -----------------------------
// Network tripwire (dyld interposing)
// -----------------------------

static bool swiftpmst_sockaddr_is_loopback(const struct sockaddr *sa) {
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

static uint16_t swiftpmst_sockaddr_port(const struct sockaddr *sa) {
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

static void swiftpmst_format_sockaddr(const struct sockaddr *sa, socklen_t salen, char out[256]) {
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

static bool swiftpmst_net_match_allow_token(const char *tok, bool is_loopback, uint16_t port) {
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

static bool swiftpmst_net_outbound_allowed(const struct sockaddr *sa) {
    if (!sa) return false;

    // Always allow AF_UNIX unless the caller opted into "deny-all" (not implemented here).
    if (sa->sa_family == AF_UNIX) return true;

    // Only gate IP families.
    if (sa->sa_family != AF_INET && sa->sa_family != AF_INET6) return true;

    if (g_network_mode == SWIFTPMST_NET_ALLOW) return true;

    const bool is_loopback = swiftpmst_sockaddr_is_loopback(sa);
    const uint16_t port = swiftpmst_sockaddr_port(sa);

    if (g_network_mode == SWIFTPMST_NET_LOCALHOST) {
        return is_loopback;
    }

    if (g_network_mode == SWIFTPMST_NET_ALLOWLIST) {
        for (int i = 0; i < g_net_allow_count; i++) {
            if (swiftpmst_net_match_allow_token(g_net_allow[i], is_loopback, port)) return true;
        }
        return false;
    }

    // Default: deny.
    return false;
}

static bool swiftpmst_net_bind_allowed(const struct sockaddr *sa) {
    if (!sa) return false;

    // Always allow AF_UNIX binds by default.
    if (sa->sa_family == AF_UNIX) return true;

    if (sa->sa_family != AF_INET && sa->sa_family != AF_INET6) return true;

    if (g_network_mode == SWIFTPMST_NET_ALLOW) return true;
    if (g_network_mode == SWIFTPMST_NET_LOCALHOST) return swiftpmst_sockaddr_is_loopback(sa);

    // Deny bind in deny + allowlist modes (treat as "no servers").
    return false;
}

int swiftpmst_connect(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
    swiftpmst_resolve_real_functions();
    if (!real_connect) {
        errno = ENOSYS;
        return -1;
    }

    if (g_in_hook) return real_connect(sockfd, addr, addrlen);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_connect(sockfd, addr, addrlen);

    g_in_hook = 1;

    bool allow = swiftpmst_net_outbound_allowed(addr);

    if (!allow) {
        char ep[256];
        swiftpmst_format_sockaddr(addr, addrlen, ep);
        swiftpmst_log_json_event("net.connect", ep, "deny", EPERM);

        if (g_mode != SWIFTPMST_MODE_REPORT_ONLY) {
            errno = EPERM;
            g_in_hook = 0;
            return -1;
        }
    }

    int r = real_connect(sockfd, addr, addrlen);
    g_in_hook = 0;
    return r;
}

int swiftpmst_bind(int sockfd, const struct sockaddr *addr, socklen_t addrlen) {
    swiftpmst_resolve_real_functions();
    if (!real_bind) {
        errno = ENOSYS;
        return -1;
    }

    if (g_in_hook) return real_bind(sockfd, addr, addrlen);
    if (!g_tripwire_active || !g_tripwire_enabled) return real_bind(sockfd, addr, addrlen);

    g_in_hook = 1;

    bool allow = swiftpmst_net_bind_allowed(addr);

    if (!allow) {
        char ep[256];
        swiftpmst_format_sockaddr(addr, addrlen, ep);
        swiftpmst_log_json_event("net.bind", ep, "deny", EPERM);

        if (g_mode != SWIFTPMST_MODE_REPORT_ONLY) {
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
struct swiftpmst_interpose {
    const void *replacement;
    const void *replacee;
};

// Some Darwin libc/kernel entrypoints use `$` in the symbol name (e.g. `_open$NOCANCEL`).
// C identifiers cannot contain `$`, so we declare replacee aliases with an asm label.
extern int swiftpmst_replacee_open_nocancel(const char *path, int oflag, ...)
    __attribute__((weak_import))
    __asm__("_open$NOCANCEL");
extern int swiftpmst_replacee_openat_nocancel(int fd, const char *path, int oflag, ...)
    __attribute__((weak_import))
    __asm__("_openat$NOCANCEL");

// libsystem_kernel also exports underscored syscall wrappers (e.g. `__open_nocancel`).
extern int __open(const char *path, int oflag, ...);
extern int __open_nocancel(const char *path, int oflag, ...);
extern int __openat(int fd, const char *path, int oflag, ...);
extern int __openat_nocancel(int fd, const char *path, int oflag, ...);

#define SWIFTPMST_INTERPOSE(_replacement, _replacee) \
    __attribute__((used)) static const struct swiftpmst_interpose swiftpmst_interpose_##_replacee \
        __attribute__((section("__DATA,__interpose"))) = { \
            (const void *)(unsigned long)&_replacement, \
            (const void *)(unsigned long)&_replacee \
        };

SWIFTPMST_INTERPOSE(swiftpmst_open, open)
SWIFTPMST_INTERPOSE(swiftpmst_openat, openat)
SWIFTPMST_INTERPOSE(swiftpmst_open_nocancel, swiftpmst_replacee_open_nocancel)
SWIFTPMST_INTERPOSE(swiftpmst_openat_nocancel, swiftpmst_replacee_openat_nocancel)
SWIFTPMST_INTERPOSE(swiftpmst_dunder_open, __open)
SWIFTPMST_INTERPOSE(swiftpmst_dunder_open_nocancel, __open_nocancel)
SWIFTPMST_INTERPOSE(swiftpmst_dunder_openat, __openat)
SWIFTPMST_INTERPOSE(swiftpmst_dunder_openat_nocancel, __openat_nocancel)
SWIFTPMST_INTERPOSE(swiftpmst_creat, creat)
SWIFTPMST_INTERPOSE(swiftpmst_unlink, unlink)
SWIFTPMST_INTERPOSE(swiftpmst_rename, rename)
SWIFTPMST_INTERPOSE(swiftpmst_mkdir, mkdir)
SWIFTPMST_INTERPOSE(swiftpmst_rmdir, rmdir)
SWIFTPMST_INTERPOSE(swiftpmst_truncate, truncate)
SWIFTPMST_INTERPOSE(swiftpmst_ftruncate, ftruncate)
SWIFTPMST_INTERPOSE(swiftpmst_connect, connect)
SWIFTPMST_INTERPOSE(swiftpmst_bind, bind)

// -----------------------------
// Bootstrap (constructor)
// -----------------------------

__attribute__((constructor))
static void swiftpmst_bootstrap(void) {
    if (swiftpmst_is_disabled()) return;

    swiftpmst_resolve_real_functions();

    g_mode = swiftpmst_parse_mode();
    g_allow_system_tmp = swiftpmst_allow_system_tmp();
    g_network_mode = swiftpmst_parse_network_mode();
    g_net_allow_count = swiftpmst_parse_network_allowlist(g_net_allow, SWIFTPMST_MAX_NET_ALLOWLIST);
    if (g_net_allow_count > 0 && g_network_mode != SWIFTPMST_NET_ALLOW && g_network_mode != SWIFTPMST_NET_LOCALHOST) {
        // If an allowlist is present, prefer allowlist mode unless the user explicitly asked for full allow.
        g_network_mode = SWIFTPMST_NET_ALLOWLIST;
    }
    g_tripwire_enabled = swiftpmst_tripwire_enabled();

    // Determine workspace root.
    if (!swiftpmst_determine_workspace_root(g_workspace_root)) {
        swiftpmst_stderr("swiftpm-sandbox-testing", "could not determine workspace root (Package.swift not found). Set SWIFTPM_SANDBOX_WORKSPACE_ROOT to override.");
        if (!swiftpmst_allow_unenforced()) _exit(196);
        return;
    }

    // Capture original HOME before redirecting.
    const char *home = getenv("HOME");
    if (home && *home) snprintf(g_orig_home, sizeof(g_orig_home), "%s", home);

    // Build sandbox root under .build.
    char run_id[64];
    swiftpmst_build_run_id(run_id);
    snprintf(g_sandbox_root, sizeof(g_sandbox_root), "%s/.build/swiftpm-sandbox-testing/%s", g_workspace_root, run_id);
    snprintf(g_fake_home, sizeof(g_fake_home), "%s/home", g_sandbox_root);
    snprintf(g_fake_tmp, sizeof(g_fake_tmp), "%s/tmp", g_sandbox_root);
    snprintf(g_log_path, sizeof(g_log_path), "%s/logs/events.jsonl", g_sandbox_root);

    // Create directories (before applying sandbox).
    if (!swiftpmst_mkdir_p(g_sandbox_root, 0755) ||
        !swiftpmst_mkdir_p(g_fake_home, 0755) ||
        !swiftpmst_mkdir_p(g_fake_tmp, 0755)) {
        swiftpmst_stderr("swiftpm-sandbox-testing", "failed to create sandbox directories under .build");
        if (!swiftpmst_allow_unenforced()) _exit(195);
        return;
    }
    {
        char logs_dir[PATH_MAX];
        snprintf(logs_dir, sizeof(logs_dir), "%s/logs", g_sandbox_root);
        (void)swiftpmst_mkdir_p(logs_dir, 0755);
    }

    // Redirect home and temp.
    if (!swiftpmst_preserve_home()) {
        setenv("HOME", g_fake_home, 1);
        setenv("CFFIXED_USER_HOME", g_fake_home, 1);
    }
    setenv("TMPDIR", g_fake_tmp, 1);

    if (swiftpmst_verbose()) {
        fprintf(stderr, "[swiftpm-sandbox-testing] workspace=%s\n", g_workspace_root);
        fprintf(stderr, "[swiftpm-sandbox-testing] sandbox=%s\n", g_sandbox_root);
    }

    // Activate tripwire enforcement for subsequent filesystem mutations.
    g_tripwire_active = true;

    // Apply Seatbelt sandbox (kernel write-guard).
    swiftpmst_apply_seatbelt_or_fail();

    // Always emit a startup marker so the sandbox root is discoverable.
    swiftpmst_log_json_event("bootstrap", g_sandbox_root, "ok", 0);

    // Optional self-test (safe: sandbox_check only).
    if (swiftpmst_selftest_enabled()) {
        swiftpmst_run_selftest();
    }
}

#else

// Non-Apple platforms: no-op.
__attribute__((constructor))
static void swiftpmst_bootstrap_nonapple(void) {}

#endif

// Linker anchor:
// SwiftPM compiles C targets into static libraries, and the linker will only pull in
// object files that satisfy referenced symbols. The sandbox bootstrap is driven by a
// constructor in this translation unit, so we expose a tiny symbol that Swift code
// can reference to force this object file to be linked in.
void swiftpmst_force_link(void) {}
