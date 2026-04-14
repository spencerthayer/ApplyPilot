"""ProfileService — wraps wizard/init.py and profile loading."""

from __future__ import annotations

import logging

from applypilot.services.base import ServiceResult

log = logging.getLogger(__name__)


class ProfileService:
    """Facade over profile/resume init and loading."""

    def run_wizard(self, *, resume_json=None, resume_pdfs=None) -> ServiceResult:
        from applypilot.wizard.init import run_wizard

        try:
            run_wizard(resume_json=resume_json, resume_pdfs=resume_pdfs)
            return ServiceResult(data={"action": "wizard_complete"})
        except Exception as e:
            log.exception("Wizard failed: %s", e)
            return ServiceResult(success=False, error=str(e))

    def load_profile(self) -> ServiceResult:
        from applypilot.config import load_profile

        try:
            profile = load_profile()
            return ServiceResult(data={"profile": profile})
        except (FileNotFoundError, ValueError) as e:
            return ServiceResult(success=False, error=str(e))

    def get_resume_text(self, path=None) -> ServiceResult:
        from applypilot.config import load_resume_text

        try:
            text = load_resume_text(path)
            return ServiceResult(data={"resume_text": text})
        except FileNotFoundError as e:
            return ServiceResult(success=False, error=str(e))
