{
  "targets": [
    {
      "target_name": "msst_bootstrap",
      "sources": [
        "sandbox/addon.c",
        "sandbox/SandboxTestingBootstrap.c"
      ],
      "cflags": ["-std=c11"],
      "libraries": ["-lsandbox"]
    }
  ]
}
