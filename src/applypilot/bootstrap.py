"""Application bootstrap — single entry point for initializing the runtime.

Creates the DI container, profile, and all services. Every CLI command
calls get_app() to get the wired application context.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from applypilot.db.container import Container
from applypilot.profile import Profile, get_profile
from applypilot.runtime_config import RuntimeConfig
from applypilot.services.analytics_service import AnalyticsService
from applypilot.services.apply_service import ApplyService
from applypilot.services.job_service import JobService
from applypilot.services.profile_service import ProfileService
from applypilot.services.resume_service import ResumeService
from applypilot.services.scoring_service import ScoringService

from applypilot.analytics.observer import AnalyticsObserver

log = logging.getLogger(__name__)


@dataclass
class App:
    """Wired application context — holds profile, container, and all services."""

    profile: Profile
    container: Container
    config: RuntimeConfig

    # Services
    profile_svc: ProfileService
    job_svc: JobService
    scoring_svc: ScoringService
    resume_svc: ResumeService
    apply_svc: ApplyService
    analytics_svc: AnalyticsService
    observer: AnalyticsObserver | None = None


_app: App | None = None
_app_lock = threading.Lock()


def get_app() -> App:
    """Return the singleton App, creating it on first call. Thread-safe."""
    global _app
    if _app is not None:
        return _app

    with _app_lock:
        if _app is not None:
            return _app

    # Legacy bootstrap — loads .env, creates dirs
    from applypilot.config import ensure_dirs, load_env

    load_env()
    ensure_dirs()

    # New layer — creates all tables from DTOs + migrates columns
    profile = get_profile()
    profile.ensure_dirs()
    container = Container()  # auto_init=True runs schema_from_dto + migrate_from_dto + migrations
    config = RuntimeConfig.load()

    # Recover from crashes — release stale in_progress locks
    stale = container.job_repo.reset_stale_in_progress(timeout_minutes=5)
    if stale:
        log.info("Reset %d stale in_progress jobs", stale)

    _app = App(
        profile=profile,
        container=container,
        config=config,
        profile_svc=ProfileService(),
        job_svc=JobService(container.job_repo),
        scoring_svc=ScoringService(container.job_repo, container.llm_cache_repo),
        resume_svc=ResumeService(container.piece_repo, container.overlay_repo, container.track_repo),
        apply_svc=ApplyService(container.job_repo, container.analytics_repo),
        analytics_svc=AnalyticsService(container.analytics_repo),
        observer=AnalyticsObserver(container.analytics_repo),
    )
    _app.observer.start()
    return _app


def reset_app() -> None:
    """Reset singleton — used in tests."""
    global _app
    if _app and _app.observer:
        _app.observer.stop()
    _app = None
