from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_VERSION = "1.0.0"
PIPELINE_VERSION = "1.0.0"
SCHEMA_PATH = Path(__file__).resolve().parent / "canonical_conversation_export.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_export(payload: dict[str, Any]) -> None:
    jsonschema.validate(payload, load_schema())
