"""Single-user profile — wraps all state under ~/.applypilot/.

V1: one profile per installation. Architecture allows future multi-profile
via --profile flag without refactoring (all state is path-scoped).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from applypilot.config import (
    APP_DIR,
)

log = logging.getLogger(__name__)


@dataclass
class Profile:
    """Encapsulates a single user's ApplyPilot state.

    All paths are derived from root_dir. Future multi-profile support
    would just change root_dir to ~/.applypilot/profiles/<name>/.
    """

    root_dir: Path = field(default_factory=lambda: APP_DIR)

    @property
    def db_path(self) -> Path:
        return self.root_dir / "applypilot.db"

    @property
    def profile_path(self) -> Path:
        return self.root_dir / "profile.json"

    @property
    def resume_json_path(self) -> Path:
        return self.root_dir / "resume.json"

    @property
    def resume_txt_path(self) -> Path:
        return self.root_dir / "resume.txt"

    @property
    def env_path(self) -> Path:
        return self.root_dir / ".env"

    @property
    def tailored_dir(self) -> Path:
        return self.root_dir / "tailored_resumes"

    @property
    def cover_letter_dir(self) -> Path:
        return self.root_dir / "cover_letters"

    @property
    def log_dir(self) -> Path:
        return self.root_dir / "logs"

    @property
    def is_initialized(self) -> bool:
        return self.resume_json_path.exists() or self.resume_txt_path.exists()

    @property
    def has_profile(self) -> bool:
        return self.profile_path.exists()

    def ensure_dirs(self) -> None:
        """Create all required directories."""
        for d in (self.root_dir, self.tailored_dir, self.cover_letter_dir, self.log_dir):
            d.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict:
        """Load profile.json (settings only, not resume data)."""
        if not self.profile_path.exists():
            return {}
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def load_resume_json(self) -> dict | None:
        """Load resume.json if it exists."""
        if not self.resume_json_path.exists():
            return None
        return json.loads(self.resume_json_path.read_text(encoding="utf-8"))

    def summary(self) -> dict:
        """Quick status for doctor/status commands."""
        resume = self.load_resume_json()
        name = resume.get("basics", {}).get("name", "unknown") if resume else "not set"
        return {
            "root": str(self.root_dir),
            "initialized": self.is_initialized,
            "name": name,
            "has_profile": self.has_profile,
            "db_exists": self.db_path.exists(),
        }


# Singleton for the default profile
_default: Profile | None = None


def get_profile() -> Profile:
    """Return the default (single-user) profile."""
    global _default
    if _default is None:
        _default = Profile()
    return _default
