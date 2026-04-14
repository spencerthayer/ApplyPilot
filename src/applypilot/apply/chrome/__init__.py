"""Chrome lifecycle management — re-exports from decomposed modules."""

from applypilot.apply.chrome.lifecycle import (  # noqa: F401
    BASE_CDP_PORT,
    HITL_CDP_PORT,
    HITL_WORKER_ID,
    _chrome_procs,
    _AdoptedChromeProcess,
    _kill_process_tree,
    _kill_on_port,
    probe_existing_chrome,
    launch_chrome,
    cleanup_worker,
    kill_all_chrome,
    reset_worker_dir,
    cleanup_on_exit,
)
from applypilot.apply.chrome.session import (  # noqa: F401
    detect_ats,
    get_ats_session_path,
    save_ats_session,
    clear_ats_session,
    list_ats_sessions,
)
from applypilot.apply.chrome.profile import (  # noqa: F401
    setup_worker_profile,
    _copy_auth_files,
    _refresh_session_files,
    _init_clean_profile,
    _remove_singleton_locks,
    _suppress_restore_nag,
    _get_real_user_agent,
)
from applypilot.apply.chrome.window import (  # noqa: F401
    compute_tile,
    prevent_focus_stealing,
    restore_focus_mode,
    _pick_viewport,
    get_worker_viewport,
    bring_to_foreground,
    bring_to_foreground_cdp,
    bring_to_foreground_pid,
    _raise_x11_window,
    _get_screen_size,
    _find_chrome_pid_for_port,
)
