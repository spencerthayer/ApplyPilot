"""Profile and resume loading — canonical JSON Resume + legacy profile support."""

from __future__ import annotations

import json
from pathlib import Path

import applypilot.config as _config
from applypilot.resume_json import (
    CanonicalResumeSource,
    ResumeJsonError,
    build_resume_text_from_json,
    load_resume_json_from_path,
    merge_resume_json_with_legacy_profile,
    normalize_legacy_profile,
    normalize_profile_from_resume_json,
    normalize_profile_settings,
    settings_from_resume_json,
)


def get_resume_source() -> CanonicalResumeSource:
    """Report whether ApplyPilot is using canonical, legacy, or missing resume artifacts."""
    if _config.RESUME_JSON_PATH.exists():
        try:
            load_resume_json_from_path(_config.RESUME_JSON_PATH)
        except (ResumeJsonError, FileNotFoundError) as exc:
            return CanonicalResumeSource(mode="canonical_invalid", path=_config.RESUME_JSON_PATH, detail=str(exc))
        return CanonicalResumeSource(
            mode="canonical", path=_config.RESUME_JSON_PATH, detail="Using canonical resume.json"
        )
    if _config.RESUME_PATH.exists():
        return CanonicalResumeSource(mode="legacy", path=_config.RESUME_PATH)
    return CanonicalResumeSource(mode="missing", path=None)


def load_resume_json(path: Path | None = None) -> dict:
    """Load and validate a canonical JSON Resume document."""
    candidate = Path(path) if path is not None else _config.RESUME_JSON_PATH
    return load_resume_json_from_path(candidate)


def _write_profile_payload(profile: dict) -> None:
    _config.PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _config.PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_resume_payload(data: dict) -> None:
    _config.RESUME_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    _config.RESUME_JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _backfill_profile_from_resume_json() -> dict:
    resume_data = load_resume_json(_config.RESUME_JSON_PATH)
    profile_settings = settings_from_resume_json(resume_data)
    _write_profile_payload(profile_settings)
    return normalize_profile_from_resume_json(resume_data, settings=profile_settings)


def load_profile() -> dict:
    """Load normalized user profile data from profile.json."""
    if _config.RESUME_JSON_PATH.exists():
        resume_data = load_resume_json(_config.RESUME_JSON_PATH)
        if not _config.PROFILE_PATH.exists():
            return _backfill_profile_from_resume_json()
        try:
            payload = json.loads(_config.PROFILE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Malformed profile JSON at {_config.PROFILE_PATH}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ) from exc
        repaired_resume, resume_changed = merge_resume_json_with_legacy_profile(resume_data, payload)
        if resume_changed:
            resume_data = repaired_resume
            _write_resume_payload(resume_data)
        profile_settings = normalize_profile_settings(payload)
        if payload != profile_settings:
            _write_profile_payload(profile_settings)
        return normalize_profile_from_resume_json(resume_data, settings=profile_settings)

    if not _config.PROFILE_PATH.exists():
        raise FileNotFoundError(f"Profile not found at {_config.PROFILE_PATH}. Run `applypilot init` first.")
    try:
        payload = json.loads(_config.PROFILE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Malformed profile JSON at {_config.PROFILE_PATH}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    return normalize_legacy_profile(payload)


def load_resume_text(path: Path | None = None) -> str:
    """Load deterministic resume text from canonical or legacy storage."""
    if path is not None:
        candidate = Path(path)
        if candidate.suffix.lower() == ".json":
            return build_resume_text_from_json(load_resume_json(candidate))
        return candidate.read_text(encoding="utf-8")
    source = get_resume_source()
    if source.mode == "canonical":
        return build_resume_text_from_json(load_resume_json(source.path))
    if source.mode == "canonical_invalid":
        raise ResumeJsonError(source.detail)
    if _config.RESUME_PATH.exists():
        return _config.RESUME_PATH.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Resume text not found at {_config.RESUME_PATH}. Run `applypilot init` first.")
