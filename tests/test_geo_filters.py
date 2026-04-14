"""Tests for geographic and work mode filtering (RUN-09, RUN-10)."""

import pytest
from applypilot.discovery.jobspy.filters import (
    _location_ok,
    _load_location_config,
    classify_work_mode,
    work_mode_ok,
)


class TestLocationOk:
    def test_remote_always_accepted(self):
        assert _location_ok("Remote - US", [], []) is True
        assert _location_ok("Work from home", [], ["US"]) is True

    def test_unknown_location_accepted(self):
        assert _location_ok(None, ["US"], []) is True
        assert _location_ok("", ["US"], []) is True

    def test_include_only_mode(self):
        assert _location_ok("San Francisco, CA", ["California", "CA"], [], "include_only") is True
        assert _location_ok("London, UK", ["California"], [], "include_only") is False

    def test_exclude_mode(self):
        assert _location_ok("San Francisco", [], ["India"], "exclude") is True
        assert _location_ok("Bangalore, India", [], ["India"], "exclude") is False

    def test_worldwide_mode(self):
        assert _location_ok("Tokyo, Japan", [], [], "worldwide") is True
        assert _location_ok("Bangalore, India", [], ["India"], "worldwide") is False  # reject still applies

    def test_no_accept_list_accepts_all(self):
        assert _location_ok("Anywhere", [], [], "include_only") is True


class TestLoadLocationConfig:
    def test_defaults(self):
        accept, reject, mode = _load_location_config({})
        assert accept == []
        assert reject == []
        assert mode == "include_only"

    def test_from_location_section(self):
        cfg = {"location": {"accept_patterns": ["US"], "reject_patterns": ["India"], "mode": "exclude"}}
        accept, reject, mode = _load_location_config(cfg)
        assert accept == ["US"]
        assert reject == ["India"]
        assert mode == "exclude"

    def test_invalid_mode_defaults(self):
        cfg = {"location": {"mode": "bogus"}}
        _, _, mode = _load_location_config(cfg)
        assert mode == "include_only"


class TestClassifyWorkMode:
    def test_remote(self):
        assert classify_work_mode("Remote") == "remote"
        assert classify_work_mode("Work from home - US") == "remote"

    def test_hybrid(self):
        assert classify_work_mode("Hybrid - NYC") == "hybrid"

    def test_onsite(self):
        assert classify_work_mode("San Francisco, CA") == "onsite"

    def test_unknown(self):
        assert classify_work_mode(None) == "unknown"
        assert classify_work_mode("") == "unknown"

    def test_description_fallback(self):
        assert classify_work_mode("NYC", "This is a fully remote position") == "remote"


class TestWorkModeOk:
    def test_no_filter(self):
        assert work_mode_ok("NYC", None, None) is True
        assert work_mode_ok("NYC", None, []) is True

    def test_filter_matches(self):
        assert work_mode_ok("Remote", None, ["remote"]) is True
        assert work_mode_ok("NYC", None, ["remote"]) is False

    def test_unknown_passes(self):
        assert work_mode_ok(None, None, ["remote"]) is True
