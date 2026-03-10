"""Pinned JSON Resume schema snapshot used for local validation."""

from __future__ import annotations


JSON_RESUME_SCHEMA_V1: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "JSON Resume v1 snapshot",
    "type": "object",
    "properties": {
        "basics": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "label": {"type": "string"},
                "image": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "url": {"type": "string"},
                "summary": {"type": "string"},
                "location": {
                    "type": "object",
                    "properties": {
                        "address": {"type": "string"},
                        "postalCode": {"type": "string"},
                        "city": {"type": "string"},
                        "countryCode": {"type": "string"},
                        "region": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
                "profiles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "network": {"type": "string"},
                            "username": {"type": "string"},
                            "url": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                },
            },
            "additionalProperties": True,
        },
        "work": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "location": {"type": "string"},
                    "description": {"type": "string"},
                    "position": {"type": "string"},
                    "url": {"type": "string"},
                    "startDate": {"type": "string"},
                    "endDate": {"type": "string"},
                    "summary": {"type": "string"},
                    "highlights": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "volunteer": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "organization": {"type": "string"},
                    "position": {"type": "string"},
                    "url": {"type": "string"},
                    "startDate": {"type": "string"},
                    "endDate": {"type": "string"},
                    "summary": {"type": "string"},
                    "highlights": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "education": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "institution": {"type": "string"},
                    "url": {"type": "string"},
                    "area": {"type": "string"},
                    "studyType": {"type": "string"},
                    "startDate": {"type": "string"},
                    "endDate": {"type": "string"},
                    "score": {"type": "string"},
                    "courses": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "awards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string"},
                    "awarder": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "certificates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "date": {"type": "string"},
                    "issuer": {"type": "string"},
                    "url": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "publications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "publisher": {"type": "string"},
                    "releaseDate": {"type": "string"},
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "level": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "languages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "language": {"type": "string"},
                    "fluency": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "interests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
        },
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "reference": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "highlights": {"type": "array", "items": {"type": "string"}},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "startDate": {"type": "string"},
                    "endDate": {"type": "string"},
                    "url": {"type": "string"},
                    "roles": {"type": "array", "items": {"type": "string"}},
                    "entity": {"type": "string"},
                    "type": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "meta": {
            "type": "object",
            "properties": {
                "canonical": {"type": "string"},
                "version": {"type": "string"},
                "lastModified": {"type": "string"},
                "theme": {"type": "string"},
                "themeOptions": {"type": "object"},
                "applypilot": {"type": "object"},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}
