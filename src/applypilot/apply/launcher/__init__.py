"""Apply launcher — re-exports from decomposed modules."""

from applypilot.apply.launcher.job_acquirer import (  # noqa: F401
    acquire_job,
    _target_unavailable_reason,
    _load_blocked,
)
from applypilot.apply.launcher.result_tracker import (  # noqa: F401
    mark_result,
    release_lock,
    mark_job,
    reset_failed,
    _is_permanent_failure,
    _fallback_failure_reason,
    PERMANENT_FAILURES,
    PERMANENT_PREFIXES,
)
from applypilot.apply.launcher.orchestrator import (  # noqa: F401
    main,
    worker_loop,
    run_job,
    gen_prompt,
    pre_navigate_to_job,
    _make_mcp_config,
    _stop_event,
    _kill_active_agent_processes,
    _register_agent_process,
    _unregister_agent_process,
    _start_worker_listener,
    _stop_worker_listener,
    _worker_state,
    _worker_state_lock,
    _worker_servers,
    _worker_server_lock,
    _takeover_events,
    _handback_events,
    POLL_INTERVAL,
)

# Re-export chrome/dashboard for backward compat
from applypilot.apply.chrome import (  # noqa: F401
    launch_chrome,
    cleanup_worker,
    kill_all_chrome,
)
from applypilot.apply.dashboard import (  # noqa: F401
    update_state,
    add_event,
    init_worker,
    render_full,
    get_totals,
    get_state,
)
