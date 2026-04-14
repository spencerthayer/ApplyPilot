from __future__ import annotations

import logging

from applypilot.discovery import greenhouse, smartextract, workday


def test_greenhouse_logs_omit_search_text(caplog, monkeypatch) -> None:
    monkeypatch.setattr(greenhouse, "fetch_jobs_api", lambda *args, **kwargs: {"jobs": []})

    caplog.set_level(logging.INFO, logger="applypilot.discovery.greenhouse")
    greenhouse.search_employer("safe-board", {"name": "Safe Co"}, "Highly Sensitive Query")

    assert "Highly Sensitive Query" not in caplog.text


def test_smartextract_does_not_log_job_samples(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        smartextract,
        "collect_page_intelligence",
        lambda url, headless=True: {
            "url": url,
            "json_ld": [],
            "api_responses": [],
            "data_testids": [],
            "page_title": "",
            "dom_stats": {},
            "card_candidates": [],
            "full_html": "",
        },
    )
    monkeypatch.setattr(smartextract, "format_strategy_briefing", lambda intel: "brief")
    monkeypatch.setattr(
        smartextract,
        "ask_llm",
        lambda prompt: ("{}", 0.1, {"response_chars": 2}),
    )
    monkeypatch.setattr(
        smartextract,
        "extract_json",
        lambda raw: {"strategy": "json_ld", "reasoning": "ok", "extraction": {}},
    )
    monkeypatch.setattr(
        smartextract,
        "execute_json_ld",
        lambda intel, plan: [{"title": "Secret Role", "location": "Secret City", "salary": "$999"}],
    )

    caplog.set_level(logging.INFO, logger="applypilot.discovery.smartextract")
    result = smartextract._run_one_site("Example", "https://example.com")

    assert result["status"] == "PASS"
    assert "Secret Role" not in caplog.text
    assert "Secret City" not in caplog.text
    assert "$999" not in caplog.text


def test_smartextract_logs_omit_raw_exception_text(caplog, monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("secret-token-value")

    monkeypatch.setattr(smartextract, "collect_page_intelligence", _boom)

    caplog.set_level(logging.ERROR, logger="applypilot.discovery.smartextract")
    smartextract._run_one_site("Example", "https://example.com")

    assert "secret-token-value" not in caplog.text
    assert "RuntimeError" in caplog.text
