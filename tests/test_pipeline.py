import json
from pathlib import Path

import pytest

from telegram_conversation_exporter.config import ExportConfig, RangeConfig
from telegram_conversation_exporter.pipeline import ExportPipeline
from telegram_conversation_exporter.schema import validate_export

FIXTURES = Path(__file__).parent / "fixtures"


def test_pipeline_exports_json_and_markdown_with_best_effort_media(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        range=RangeConfig(mode="full_chat"),
        transcribe_voice=True,
        stub_transcription=True,
        describe_images=True,
        stub_vision=True,
        ocr=True,
        stub_ocr=True,
    )
    pipeline = ExportPipeline(config)
    result = pipeline.run()

    payload = result["conversation"]
    validate_export(payload)
    assert result["json_path"].exists()
    assert result["markdown_path"].exists()
    assert payload["run_summary"]["missing_media_files"] == 2

    by_id = {message["source"]["message_id"]: message for message in payload["messages"]}
    assert by_id["22"]["enrichment"]["transcription"]["status"] == "ok"
    assert by_id["23"]["processing"]["export_status"] == "partial"
    assert by_id["24"]["enrichment"]["vision"]["status"] == "ok"
    assert by_id["25"]["processing"]["export_status"] == "partial"
    assert payload["metadata"]["participant_count"] == 2
    assert by_id["21"]["source"]["is_forwarded"] is True

    markdown = result["markdown_path"].read_text(encoding="utf-8")
    assert "Transcript:" in markdown
    assert "Image:" in markdown
    assert "[Forwarded message]" in markdown


def test_dry_run_summary_reports_counts_without_outputs(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        transcribe_voice=True,
        stub_transcription=True,
        describe_images=True,
    )
    summary = ExportPipeline(config).dry_run_summary()

    assert summary["message_count"] == 6
    assert summary["media_counts"]["voice"] == 2
    assert summary["media_counts"]["image"] == 2
    assert summary["missing_media_files"] == 2
    assert summary["estimated_enrichment_work"]["transcription"] == 2
    assert not (tmp_path / "conversation.json").exists()


def test_pipeline_marks_unsupported_messages_and_strict_mode_fails(tmp_path):
    source = tmp_path / "unsupported_chat.json"
    source.write_text(
        json.dumps(
            {
                "id": "unsupported_chat",
                "name": "Unsupported Chat",
                "messages": [
                    {
                        "id": 1,
                        "type": "poll",
                        "date": "2025-01-01T10:00:00+00:00",
                        "from": "Alice",
                        "from_id": "user_alice",
                        "text": "Favorite color?"
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config = ExportConfig(source=source, chat_ref="unsupported_chat", output_dir=tmp_path)
    payload = ExportPipeline(config).run()["conversation"]
    assert payload["messages"][0]["kind"] == "unsupported"
    assert payload["messages"][0]["normalized"]["plain_text"] == "Favorite color?"

    strict_config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path / "strict",
        strict=True,
        transcribe_voice=True,
        stub_transcription=True,
        describe_images=True,
        stub_vision=True,
        ocr=True,
        stub_ocr=True,
    )
    with pytest.raises(RuntimeError, match="Strict mode aborted export"):
        ExportPipeline(strict_config).run()


def test_pipeline_respects_max_media_items_and_creates_cache(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        transcribe_voice=True,
        stub_transcription=True,
        describe_images=True,
        stub_vision=True,
        ocr=True,
        stub_ocr=True,
        max_media_items=1,
    )
    payload = ExportPipeline(config).run()["conversation"]
    by_id = {message["source"]["message_id"]: message for message in payload["messages"]}
    assert by_id["22"]["enrichment"]["transcription"]["status"] == "ok"
    assert by_id["24"]["enrichment"]["vision"]["status"] == "skipped"
    cache_files = list((tmp_path / ".tce_cache").glob("*.json"))
    assert cache_files

    service_payload = ExportPipeline(
        ExportConfig(
            source=FIXTURES / "simple_private_chat.json",
            chat_ref="chat_simple",
            output_dir=tmp_path / "simple",
        )
    ).run()["conversation"]
    service_message = next(message for message in service_payload["messages"] if message["kind"] == "service")
    assert service_payload["metadata"]["participant_count"] == 2
    assert service_message["sender"]["participant_label"] is None
