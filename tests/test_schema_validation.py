from pathlib import Path

from telegram_conversation_exporter.config import ExportConfig
from telegram_conversation_exporter.pipeline import ExportPipeline
from telegram_conversation_exporter.schema import SCHEMA_PATH, load_schema, validate_export

FIXTURES = Path(__file__).parent / "fixtures"


def test_schema_loads_from_packaged_path():
    schema = load_schema()
    assert SCHEMA_PATH.exists()
    assert SCHEMA_PATH.name == "canonical_conversation_export.schema.json"
    assert schema["title"] == "CanonicalConversationExport"


def test_export_payload_validates_against_schema(tmp_path):
    payload = ExportPipeline(
        ExportConfig(source=FIXTURES / "simple_private_chat.json", chat_ref="chat_simple", output_dir=tmp_path)
    ).run()["conversation"]

    validate_export(payload)
    assert payload["schema_version"] == "1.0.0"
    assert payload["metadata"]["source_export_kind"] == "single_chat_json"
