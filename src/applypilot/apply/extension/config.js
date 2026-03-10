// Per-worker config — overwritten by setup_worker_profile() with the correct workerId.
// This fallback (workerId: null) is used when the extension is loaded from the source
// directory without going through the deployment step.
globalThis.WORKER_CONFIG = { workerId: null, serverPort: 7380 };
