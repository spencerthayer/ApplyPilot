from __future__ import annotations

import sys
import types
from pathlib import Path

from applypilot.scoring import pdf


class _FakePage:
    def set_content(self, _html: str, wait_until: str = "networkidle") -> None:
        del wait_until

    def pdf(self, path: str, **_kwargs) -> None:  # noqa: ANN003
        Path(path).write_bytes(b"%PDF-1.4 fake\n")


class _FakeBrowser:
    def __init__(self) -> None:
        self._page = _FakePage()

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        return None


class _FakeChromium:
    def launch(self, headless: bool = True) -> _FakeBrowser:
        del headless
        return _FakeBrowser()


class _FakePlaywright:
    chromium = _FakeChromium()


class _FakePlaywrightContext:
    def start(self) -> _FakePlaywright:
        return _FakePlaywright()

    def stop(self) -> None:
        return None

    def __enter__(self) -> _FakePlaywright:
        return _FakePlaywright()

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc_type, exc, tb
        return False


def _install_fake_playwright(monkeypatch) -> None:
    sync_api_module = types.ModuleType("playwright.sync_api")
    sync_api_module.sync_playwright = lambda: _FakePlaywrightContext()
    playwright_module = types.ModuleType("playwright")
    playwright_module.sync_api = sync_api_module
    monkeypatch.setitem(sys.modules, "playwright", playwright_module)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    # Clear any cached shared browser from previous tests
    from applypilot.scoring.pdf import pdf_renderer
    pdf_renderer.close_shared_browser()


def _write_resume_txt(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Alex Example",
                "Senior Engineer",
                "alex@example.com | 555-111-2222",
                "",
                "SUMMARY",
                "Built and shipped reliable systems.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_batch_convert_limit_zero_converts_all_candidates(monkeypatch, tmp_path: Path) -> None:
    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(pdf, "TAILORED_DIR", tmp_path)

    _write_resume_txt(tmp_path / "resume_a.txt")
    _write_resume_txt(tmp_path / "resume_b.txt")
    _write_resume_txt(tmp_path / "resume_c.txt")

    converted = pdf.batch_convert(limit=0)

    assert converted == 3
    assert len(list(tmp_path.glob("*.pdf"))) == 3


def test_batch_convert_positive_limit_caps_conversions(monkeypatch, tmp_path: Path) -> None:
    _install_fake_playwright(monkeypatch)
    monkeypatch.setattr(pdf, "TAILORED_DIR", tmp_path)

    _write_resume_txt(tmp_path / "resume_a.txt")
    _write_resume_txt(tmp_path / "resume_b.txt")
    _write_resume_txt(tmp_path / "resume_c.txt")

    converted = pdf.batch_convert(limit=2)

    assert converted == 2
    assert len(list(tmp_path.glob("*.pdf"))) == 2
