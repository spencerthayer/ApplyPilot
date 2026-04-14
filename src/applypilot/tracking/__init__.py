"""ApplyPilot tracking — re-exports from decomposed modules."""

from applypilot.tracking.display import show_action_items  # noqa: F401
from applypilot.tracking.email_processor import _process_classified_email  # noqa: F401
from applypilot.tracking.stubs import remap_stubs, relabel_all_tracked  # noqa: F401
from applypilot.tracking.pipeline import run_tracking  # noqa: F401
from applypilot.tracking._compat import (  # noqa: F401
    get_applied_jobs,
    get_action_items,
    get_tracking_stats,
    get_tracking_emails,
    get_tracking_people,
    update_tracking_status,
    store_tracking_email,
    store_tracking_person,
    email_already_tracked,
    update_job_tracking_fields,
    create_stub_job,
    get_connection,
)
