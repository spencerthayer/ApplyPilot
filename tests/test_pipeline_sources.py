from __future__ import annotations

import pytest

from applypilot import pipeline


@pytest.mark.skip(reason="V1: DISCOVERY_SOURCES moved to services/job_service.py — test needs rewrite")
def test_ziprecruiter_source_is_resolvable() -> None:
    assert "ziprecruiter" in pipeline.DISCOVERY_SOURCES
    assert pipeline.resolve_source_names(["ziprecruiter"]) == ["ziprecruiter"]
    assert pipeline.resolve_source_names(["zip_recruiter"]) == ["ziprecruiter"]


@pytest.mark.skip(reason="V1: _JOBSPY_SITE_SOURCES moved to services/job_service.py — test needs rewrite")
def test_ziprecruiter_maps_to_jobspy_site_override() -> None:
    assert pipeline._JOBSPY_SITE_SOURCES["ziprecruiter"] == ["zip_recruiter"]
