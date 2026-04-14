"""Tests for resume merge during init --resume-json (bug: profiles lost on overwrite)."""

import pytest
from applypilot.wizard.resume_setup import _merge_incoming_resume


class TestMergeIncomingResume:
    def test_preserves_existing_github_profile(self):
        existing = {
            "basics": {
                "name": "Test User",
                "email": "test@example.com",
                "phone": "+1234567890",
                "profiles": [
                    {"network": "GitHub", "username": "testuser", "url": "https://github.com/testuser"},
                    {"network": "LinkedIn", "username": "testuser", "url": "https://linkedin.com/in/testuser"},
                ],
            },
            "meta": {"applypilot": {"target_role": "SDE"}},
        }
        incoming = {
            "basics": {
                "name": "Test User",
                "profiles": [
                    {"network": "LinkedIn", "username": "testuser", "url": "https://linkedin.com/in/testuser"},
                ],
            },
        }
        merged = _merge_incoming_resume(incoming, existing)

        # GitHub profile from existing should be preserved
        networks = [p["network"] for p in merged["basics"]["profiles"]]
        assert "GitHub" in networks
        assert "LinkedIn" in networks

    def test_preserves_existing_contact_info(self):
        existing = {"basics": {"email": "old@test.com", "phone": "+1111"}}
        incoming = {"basics": {"name": "New Name"}}
        merged = _merge_incoming_resume(incoming, existing)
        assert merged["basics"]["email"] == "old@test.com"
        assert merged["basics"]["phone"] == "+1111"

    def test_incoming_contact_wins(self):
        existing = {"basics": {"email": "old@test.com"}}
        incoming = {"basics": {"email": "new@test.com"}}
        merged = _merge_incoming_resume(incoming, existing)
        assert merged["basics"]["email"] == "new@test.com"

    def test_preserves_existing_meta(self):
        existing = {"meta": {"applypilot": {"target_role": "SDE", "salary": "100k"}}}
        incoming = {"meta": {"applypilot": {"target_role": "Backend"}}}
        merged = _merge_incoming_resume(incoming, existing)
        assert merged["meta"]["applypilot"]["target_role"] == "Backend"
        assert merged["meta"]["applypilot"]["salary"] == "100k"

    def test_no_existing_profiles(self):
        existing = {"basics": {}}
        incoming = {"basics": {"profiles": [{"network": "GitHub", "url": "https://github.com/x"}]}}
        merged = _merge_incoming_resume(incoming, existing)
        assert len(merged["basics"]["profiles"]) == 1

    def test_empty_existing(self):
        existing = {}
        incoming = {"basics": {"name": "Test"}, "work": []}
        merged = _merge_incoming_resume(incoming, existing)
        assert merged["basics"]["name"] == "Test"
