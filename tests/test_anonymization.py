from pathlib import Path

from telegram_conversation_exporter.anonymization import build_participant_map
from telegram_conversation_exporter.telegram_export_parser import TelegramExportParser

FIXTURES = Path(__file__).parent / "fixtures"


def test_participant_labels_are_deterministic_at_chat_scope():
    parser = TelegramExportParser(FIXTURES / "media_chat.json")
    chat = parser.parse_chat("chat_media")

    mapping_one = build_participant_map(chat.messages)
    mapping_two = build_participant_map(chat.messages[:2])

    assert mapping_one["user_alice"].label == "Participant 1"
    assert mapping_one["user_bob"].label == "Participant 2"
    assert mapping_one["user_alice"].participant_id == mapping_two["user_alice"].participant_id


def test_service_actors_are_excluded_from_participant_mapping():
    parser = TelegramExportParser(FIXTURES / "simple_private_chat.json")
    chat = parser.parse_chat("chat_simple")
    mapping = build_participant_map(chat.messages)

    assert set(mapping) == {"user_alice", "user_bob"}
