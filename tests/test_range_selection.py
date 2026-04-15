from pathlib import Path

from telegram_conversation_exporter.config import RangeConfig
from telegram_conversation_exporter.range_selection import resolve_reply_status, select_messages
from telegram_conversation_exporter.telegram_export_parser import TelegramExportParser

FIXTURES = Path(__file__).parent / "fixtures"


def test_message_id_range_selection_and_reply_statuses():
    parser = TelegramExportParser(FIXTURES / "replies_private_chat.json")
    chat = parser.parse_chat("chat_replies")
    selected = select_messages(chat.messages, RangeConfig(mode="message_id_range", start_message_id="11", end_message_id="12"))

    assert [message.message_id for message in selected] == ["11", "12"]
    selected_ids = {message.message_id for message in selected}
    all_ids = {message.message_id for message in chat.messages}
    assert resolve_reply_status(selected[0], selected_ids, all_ids) == "out_of_range"
    assert resolve_reply_status(selected[1], selected_ids, all_ids) == "missing"


def test_time_range_selection():
    parser = TelegramExportParser(FIXTURES / "replies_private_chat.json")
    chat = parser.parse_chat("chat_replies")
    selected = select_messages(chat.messages, RangeConfig(mode="time_range", start_time_utc="2025-01-02T10:01:00+00:00", end_time_utc="2025-01-02T10:02:00+00:00"))

    assert [message.message_id for message in selected] == ["11", "12"]
