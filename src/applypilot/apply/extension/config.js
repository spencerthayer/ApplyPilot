// ApplyPilot Extension — shared constants
// Single source of truth for all extension scripts.

const APPLYPILOT = {
    BASE_PORT: 7380,
    MAX_WORKERS: 5,
    POLL_MS: 3000,
    API_TIMEOUT_MS: 1500,
    WORKER_COLORS: ['#3b82f6', '#22c55e', '#f97316', '#a855f7', '#ef4444'],
};

// Per-worker config — overwritten by setup_worker_profile() with the correct workerId.
// Fallback (workerId: null) used when loaded from source directory without deployment.
globalThis.WORKER_CONFIG = {workerId: null, serverPort: APPLYPILOT.BASE_PORT};
