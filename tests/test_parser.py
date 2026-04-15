import json
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
