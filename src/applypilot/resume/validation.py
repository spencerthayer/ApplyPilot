"""Resume JSON validation — schema checks and security scanning."""

from __future__ import annotations

import re
from typing import Any

from applypilot.jsonresume_schema import JSON_RESUME_SCHEMA_V1

_FORBIDDEN_SECRET_KEY_RE = re.compile(r"(password|secret|token|api[_-]?key)", re.IGNORECASE)


class ResumeJsonError(ValueError):
    """Raised when resume.json is invalid or unusable."""


def _load_jsonschema():
    try:
        from jsonschema import Draft7Validator
    except ImportError as exc:
        raise ResumeJsonError(
            "JSON Resume support requires the 'jsonschema' package. "
            "Install ApplyPilot with updated Python dependencies."
        ) from exc
    return Draft7Validator


def _format_path(parts: list[Any]) -> str:
    if not parts:
        return "$"
    rendered = "$"
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _collect_schema_errors(payload: Any, schema: dict, prefix: list[Any] | None = None) -> list[str]:
    validator_cls = _load_jsonschema()
    validator = validator_cls(schema)
    errors = []
    path_prefix = prefix or []
    for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.path)):
        path = _format_path(path_prefix + list(error.path))
        errors.append(f"{path}: {error.message}")
    return errors


def _find_forbidden_keys(value: Any, prefix: list[Any] | None = None) -> list[str]:
    prefix = prefix or []
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            current = prefix + [key]
            if _FORBIDDEN_SECRET_KEY_RE.search(str(key)):
                findings.append(f"{_format_path(current)}: secrets must stay in .env, not resume.json")
            findings.extend(_find_forbidden_keys(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_forbidden_keys(child, prefix + [index]))
    return findings


def looks_like_resume_json(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if any(key in data for key in ("basics", "work", "skills", "projects")):
        return True
    meta = data.get("meta")
    return isinstance(meta, dict) and any(key in meta for key in ("canonical", "version", "theme", "applypilot"))


def validate_applypilot_meta(data: dict, meta_schema: dict | None = None) -> None:
    meta = data.get("meta", {})
    errors: list[str] = []
    if meta is not None and not isinstance(meta, dict):
        raise ResumeJsonError("Invalid resume.json:\n- $.meta: must be an object")

    if meta_schema is None:
        from applypilot.resume_json import _APPLYPILOT_META_SCHEMA

        meta_schema = _APPLYPILOT_META_SCHEMA

    applypilot_meta = meta.get("applypilot", {}) if isinstance(meta, dict) else {}
    if applypilot_meta is None:
        applypilot_meta = {}
    errors.extend(_collect_schema_errors(applypilot_meta, meta_schema, ["meta", "applypilot"]))
    errors.extend(_find_forbidden_keys(applypilot_meta, ["meta", "applypilot"]))

    work_entries = data.get("work", [])
    if isinstance(work_entries, list):
        for index, entry in enumerate(work_entries):
            if not isinstance(entry, dict):
                continue
            extension = entry.get("x-applypilot")
            if extension is None:
                continue
            from applypilot.resume_json import _WORK_EXTENSION_SCHEMA

            errors.extend(_collect_schema_errors(extension, _WORK_EXTENSION_SCHEMA, ["work", index, "x-applypilot"]))
            errors.extend(_find_forbidden_keys(extension, ["work", index, "x-applypilot"]))

    if errors:
        raise ResumeJsonError("Invalid resume.json ApplyPilot extensions:\n- " + "\n- ".join(errors))


def validate_resume_json(data: dict, meta_schema: dict | None = None) -> None:
    if not isinstance(data, dict):
        raise ResumeJsonError("Invalid resume.json:\n- $: root value must be an object")
    errors = _collect_schema_errors(data, JSON_RESUME_SCHEMA_V1)
    if errors:
        raise ResumeJsonError("Invalid resume.json:\n- " + "\n- ".join(errors))
    validate_applypilot_meta(data, meta_schema)
