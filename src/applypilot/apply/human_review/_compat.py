"""HITL compat — delegates to repo."""


def get_needs_human_jobs() -> list[dict]:
    from applypilot.bootstrap import get_app

    return get_app().container.job_repo.get_needs_human()
