import json
import zipfile
from pathlib import Path

from telegram_conversation_exporter.telegram_export_parser import TelegramExportParser

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_single_chat_shape_and_message_fields():
    parser = TelegramExportParser(FIXTURES / "media_chat.json")
    chat = parser.parse_chat("chat_media")

    assert chat.chat_ref == "chat_media"
    assert len(chat.messages) == 6
    edited = chat.messages[0]
    assert edited.is_edited is True
    assert edited.edited_at == "2025-01-03T10:05:00+00:00"

    forwarded = chat.messages[1]
    assert forwarded.is_forwarded is True
    assert forwarded.forwarded_from_sanitized == "forwarded_source_redacted"

    voice = chat.messages[2]
    assert voice.media.relative_path == "media/voice_note.ogg"
    assert voice.media.type == "voice"


def test_list_and_select_full_export_chat():
    parser = TelegramExportParser(FIXTURES / "full_export.json")
    chats = parser.list_chats()

    assert {chat['chat_ref'] for chat in chats} == {"chat_one", "chat_two"}
    selected = parser.parse_chat("chat_two")
    assert selected.messages[0].text == "Beta hello"


def test_parse_zip_export_with_result_json_and_media(tmp_path):
    zip_path = tmp_path / "telegram-export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(FIXTURES / "media_chat.json", arcname="result.json")
        archive.write(FIXTURES / "media" / "voice_note.ogg", arcname="media/voice_note.ogg")
        archive.write(FIXTURES / "media" / "screenshot.jpg", arcname="media/screenshot.jpg")

    parser = TelegramExportParser(zip_path)
    chat = parser.parse_chat("chat_media")

    assert chat.chat_ref == "chat_media"
    assert parser.base_dir.name.startswith("tce-zip-")
    assert (parser.base_dir / "media" / "voice_note.ogg").exists()


def test_parse_zip_export_rejects_unsafe_member_paths(tmp_path):
    zip_path = tmp_path / "bad-export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../escape.json", "{}")

    try:
        TelegramExportParser(zip_path)
    except ValueError as exc:
        assert "Unsafe ZIP entry path" in str(exc)
    else:
        raise AssertionError("Expected unsafe ZIP path to be rejected")


def test_parser_normalizes_timestamps_to_utc(tmp_path):
    source = tmp_path / "offset_chat.json"
    source.write_text(
        json.dumps(
            {
                "id": "offset_chat",
                "name": "Offset Chat",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2025-01-01T12:00:00+02:00",
                        "from": "Alice",
                        "from_id": "user_alice",
                        "text": "hello"
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    parser = TelegramExportParser(source)
    chat = parser.parse_chat("offset_chat")
    assert chat.messages[0].timestamp_utc == "2025-01-01T10:00:00+00:00"


def test_parser_best_effort_handles_malformed_message(tmp_path):
    source = tmp_path / "bad_chat.json"
    source.write_text(
        json.dumps(
            {
                "id": "bad_chat",
                "name": "Bad Chat",
                "messages": [
                    {
                        "id": 1,
                        "type": "message",
                        "date": "2025-01-01T10:00:00+00:00",
                        "from": "Alice",
                        "from_id": "user_alice",
                        "text": "ok"
                    },
                    {
                        "id": 2,
                        "type": "message",
                        "date": {"bad": "shape"},
                        "from": "Bob",
                        "from_id": "user_bob",
                        "text": "broken"
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    parser = TelegramExportParser(source)
    chat = parser.parse_chat("bad_chat")
    assert len(chat.messages) == 2
    assert chat.messages[1].parse_error is not None
    assert chat.messages[1].text.startswith("[Failed to parse Telegram message")
