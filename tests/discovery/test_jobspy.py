from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from applypilot.discovery import jobspy


@pytest.fixture(autouse=True)
def _mock_bootstrap(monkeypatch):
    """Mock get_app so jobspy tests don't need a real DB."""
    mock_repo = MagicMock()
    mock_repo.get_pipeline_counts.return_value = {"total": 0}
    monkeypatch.setattr(
        "applypilot.bootstrap.get_app",
        lambda: SimpleNamespace(
            container=SimpleNamespace(job_repo=mock_repo),
        ),
    )


class _FakeZipRecruiterPage:
    def __init__(self, payloads: list[dict], *, raise_on_wait: bool = False) -> None:
        self._payloads = list(payloads)
        self.raise_on_wait = raise_on_wait
        self.goto_calls: list[dict] = []
        self.wait_calls: list[dict] = []
        self.load_state_calls: list[dict] = []

    def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
        self.goto_calls.append({"url": url, "timeout": timeout, "wait_until": wait_until})

    def wait_for_selector(self, selector: str, *, state: str | None = None, timeout: int | None = None) -> None:
        self.wait_calls.append({"selector": selector, "state": state, "timeout": timeout})
        if self.raise_on_wait:
            raise TimeoutError("selector not attached")

    def wait_for_load_state(self, state: str, *, timeout: int | None = None) -> None:
        self.load_state_calls.append({"state": state, "timeout": timeout})

    def evaluate(self, _script: str) -> dict:
        if not self._payloads:
            raise AssertionError("No fake ZipRecruiter payloads remaining")
        return self._payloads.pop(0)


def _install_fake_ziprecruiter_browser(
    monkeypatch: pytest.MonkeyPatch,
    payloads: list[dict],
    *,
    raise_on_wait: bool = False,
) -> _FakeZipRecruiterPage:
    page = _FakeZipRecruiterPage(payloads, raise_on_wait=raise_on_wait)

    class _FakeContext:
        def add_init_script(self, _script: str) -> None:
            return None

        def new_page(self) -> _FakeZipRecruiterPage:
            return page

    class _FakeBrowser:
        def new_context(self, *, user_agent: str) -> _FakeContext:
            assert user_agent == "fake-user-agent"
            return _FakeContext()

        def close(self) -> None:
            return None

    class _FakeChromium:
        def launch(self, **_kwargs) -> _FakeBrowser:
            return _FakeBrowser()

    class _FakePlaywright:
        chromium = _FakeChromium()

        def __enter__(self) -> "_FakePlaywright":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: _FakePlaywright())
    monkeypatch.setattr("applypilot.apply.chrome._get_real_user_agent", lambda: "fake-user-agent")
    monkeypatch.setattr("applypilot.enrichment.browser_config._STEALTH_INIT_SCRIPT", "window.__stealth = true;")
    monkeypatch.setattr(jobspy.config, "get_chrome_path", lambda: "/tmp/fake-chrome")
    return page


def test_ziprecruiter_search_url_applies_radius_only_for_non_remote() -> None:
    local_url = jobspy._ziprecruiter_search_url(
        "Backend Engineer",
        "Austin, TX",
        False,
        page_number=1,
        distance=25,
    )
    local_params = parse_qs(urlparse(local_url).query)
    assert local_params["radius"] == ["25"]
    assert "refine_by_location_type" not in local_params

    remote_url = jobspy._ziprecruiter_search_url(
        "Backend Engineer",
        "Austin, TX",
        True,
        page_number=1,
        distance=25,
    )
    remote_params = parse_qs(urlparse(remote_url).query)
    assert remote_params["refine_by_location_type"] == ["only_remote"]
    assert "radius" not in remote_params


def test_apply_local_hours_filter_uses_date_posted_when_available() -> None:
    now = datetime.now(timezone.utc)
    df = pd.DataFrame(
        [
            {"title": "fresh", "date_posted": now.date()},
            {"title": "stale", "date_posted": (now - timedelta(days=10)).date()},
            {"title": "unknown", "date_posted": None},
        ]
    )

    filtered, removed, enforced = jobspy._apply_local_hours_filter(df, hours_old=72)

    assert enforced is True
    assert removed == 1
    assert list(filtered["title"]) == ["fresh", "unknown"]


def test_apply_local_hours_filter_is_noop_without_supported_column() -> None:
    df = pd.DataFrame([{"title": "job", "location": "Remote"}])

    filtered, removed, enforced = jobspy._apply_local_hours_filter(df, hours_old=72)

    assert filtered.equals(df)
    assert removed == 0
    assert enforced is False


def test_merge_ziprecruiter_page_data_prefers_itemlist_urls() -> None:
    merged = jobspy._merge_ziprecruiter_page_data(
        item_list=[
            {"name": "Systems Architect", "url": "https://www.ziprecruiter.com/c/Foo/Job/Systems-Architect?jid=123"},
            {"name": "Backend Engineer", "url": "https://www.ziprecruiter.com/c/Bar/Job/Backend-Engineer?jid=456"},
        ],
        cards=[
            {"title": "Systems Architect", "company": "Foo", "location": "Remote, US · Remote", "salary": "$100K/yr"},
            {"title": "Backend Engineer", "company": "Bar", "location": "Austin, TX · On-site +1", "salary": ""},
        ],
    )

    assert merged[0]["job_url"].endswith("jid=123")
    assert merged[0]["is_remote"] is True
    assert merged[1]["title"] == "Backend Engineer"
    assert merged[1]["salary"] is None
