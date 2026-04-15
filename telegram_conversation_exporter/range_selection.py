from __future__ import annotations

from datetime import datetime

from .config import RangeConfig
from .models import RawTelegramMessage


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def select_messages(messages: list[RawTelegramMessage], range_config: RangeConfig) -> list[RawTelegramMessage]:
    if range_config.mode == "full_chat":
        return list(messages)

    if range_config.mode == "message_id_range":
        start = int(range_config.start_message_id) if range_config.start_message_id is not None else None
        end = int(range_config.end_message_id) if range_config.end_message_id is not None else None
        return [
            message
            for message in messages
            if (start is None or int(message.message_id) >= start)
            and (end is None or int(message.message_id) <= end)
        ]

    if range_config.mode == "time_range":
        start_time = _parse_dt(range_config.start_time_utc) if range_config.start_time_utc else None
        end_time = _parse_dt(range_config.end_time_utc) if range_config.end_time_utc else None
        return [
            message
            for message in messages
            if (start_time is None or _parse_dt(message.timestamp_utc) >= start_time)
            and (end_time is None or _parse_dt(message.timestamp_utc) <= end_time)
        ]

    raise ValueError(f"Unsupported range mode: {range_config.mode}")


def resolve_reply_status(message: RawTelegramMessage, selected_ids: set[str], all_ids: set[str]) -> str:
    if not message.reply_to_message_id:
        return "unknown"
    if message.reply_to_message_id in selected_ids:
        return "in_range"
    if message.reply_to_message_id in all_ids:
        return "out_of_range"
    return "missing"
