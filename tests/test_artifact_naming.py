from __future__ import annotations

import hashlib

from applypilot.scoring import artifact_naming, tailor


def _job(url: str) -> dict:
    return {
        "url": url,
        "title": "Network Engineer V",
        "site": "LinkedIn",
    }


def test_build_artifact_prefix_distinguishes_same_title_site_by_url() -> None:
    first = artifact_naming.build_artifact_prefix(_job("https://www.linkedin.com/jobs/view/111"))
    second = artifact_naming.build_artifact_prefix(_job("https://www.linkedin.com/jobs/view/222"))

    assert first != second
    assert first.endswith("_111")
    assert second.endswith("_222")


def test_build_artifact_prefix_prefers_query_job_id() -> None:
    prefix = artifact_naming.build_artifact_prefix(_job("https://www.linkedin.com/jobs/view/4383377387?jk=abc123"))

    assert prefix == "LinkedIn_Network_Engineer_V_abc123"


def test_build_artifact_prefix_falls_back_to_hash_when_url_has_no_job_id() -> None:
    url = "https://example.com"
    expected_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]

    prefix = artifact_naming.build_artifact_prefix(_job(url))

    assert prefix == f"LinkedIn_Network_Engineer_V_{expected_hash}"


def test_tailor_prefix_wrapper_delegates_to_shared_builder() -> None:
    job = _job("https://www.linkedin.com/jobs/view/4383377387")

    assert tailor._build_tailored_prefix(job) == artifact_naming.build_artifact_prefix(job)


# ── Organized job directory ───────────────────────────────────────────────


class TestOrganizedJobDir:
    def test_creates_folder(self, tmp_path):
        from applypilot.config.paths import organized_job_dir

        d = organized_job_dir(tmp_path, "Stripe", "Android Engineer")
        assert d.exists()
        assert d == tmp_path / "Stripe" / "Android Engineer"

    def test_sanitizes_special_chars(self, tmp_path):
        from applypilot.config.paths import organized_job_dir

        d = organized_job_dir(tmp_path, "HN: Oscilar.com", "Sr/Staff Engineers & PM")
        assert d.exists()
        assert "/" not in d.name

    def test_truncates_long_names(self, tmp_path):
        from applypilot.config.paths import organized_job_dir

        d = organized_job_dir(tmp_path, "A" * 100, "B" * 100)
        assert len(d.parent.name) <= 40
        assert len(d.name) <= 60

    def test_handles_empty_names(self, tmp_path):
        from applypilot.config.paths import organized_job_dir

        d = organized_job_dir(tmp_path, "", "")
        assert d.exists()
        assert "Unknown" in str(d)

    def test_idempotent(self, tmp_path):
        from applypilot.config.paths import organized_job_dir

        d1 = organized_job_dir(tmp_path, "Uber", "SDE 2")
        d2 = organized_job_dir(tmp_path, "Uber", "SDE 2")
        assert d1 == d2
