from __future__ import annotations

import json
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .media import infer_media_type
from .models import MediaInfo, ParsedChat, RawTelegramMessage


class TelegramExportParser:
    def __init__(self, source: Path):
        self.source = Path(source)
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._resolved_source = self._resolve_source_path(self.source)
        self.base_dir = self._resolved_source.parent
        self.payload = json.loads(self._resolved_source.read_text(encoding="utf-8"))

    def export_kind(self) -> str:
        return "full_export_json" if isinstance(self.payload.get("chats"), dict) else "single_chat_json"

    def list_chats(self) -> list[dict[str, str]]:
        if self.export_kind() == "single_chat_json":
            chat = self.payload
            return [{"chat_ref": self._chat_ref(chat), "title": chat.get("name") or chat.get("title") or "Redacted Chat"}]
        chats = self.payload.get("chats", {}).get("list", [])
        return [{"chat_ref": self._chat_ref(chat), "title": chat.get("name") or chat.get("title") or "Redacted Chat"} for chat in chats]

    def parse_chat(self, chat_ref: str) -> ParsedChat:
        chat = self._resolve_chat(chat_ref)
        messages = [self._safe_parse_message(message) for message in chat.get("messages", [])]
        participant_keys = sorted({message.sender_key for message in messages if message.sender_key})
        return ParsedChat(
            chat_ref=self._chat_ref(chat),
            title=chat.get("name") or chat.get("title") or "Redacted Chat",
            source_export_kind=self.export_kind(),
            messages=messages,
            participant_keys=participant_keys,
        )

    def _resolve_source_path(self, source: Path) -> Path:
        if source.suffix.lower() != ".zip":
            return source
        self._temp_dir = tempfile.TemporaryDirectory(prefix="tce-zip-")
        extract_root = Path(self._temp_dir.name)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(extract_root)
        candidates = [
            path
            for path in extract_root.rglob("*.json")
            if path.is_file() and not path.name.startswith("__MACOSX")
        ]
        preferred = [path for path in candidates if path.name.lower() == "result.json"]
        chosen = preferred[0] if preferred else (candidates[0] if len(candidates) == 1 else None)
        if chosen is None:
            json_list = ", ".join(str(path.relative_to(extract_root)) for path in candidates[:10])
            raise FileNotFoundError(
                "Could not determine Telegram export JSON inside ZIP. "
                "Expected result.json or a single JSON file. "
                f"Found: {json_list or 'none'}"
            )
        return chosen

    def _resolve_chat(self, chat_ref: str) -> dict[str, Any]:
        candidates = [self.payload] if self.export_kind() == "single_chat_json" else self.payload.get("chats", {}).get("list", [])
        for chat in candidates:
            if chat_ref in {self._chat_ref(chat), str(chat.get("id", "")), chat.get("name"), chat.get("title")}:
                return chat
        raise KeyError(f"Unknown chat_ref: {chat_ref}")

    def _chat_ref(self, chat: dict[str, Any]) -> str:
        return str(chat.get("id") or chat.get("name") or chat.get("title") or "chat")

    def _safe_parse_message(self, message: dict[str, Any]) -> RawTelegramMessage:
        try:
            return self._parse_message(message)
        except Exception as exc:
            message_id = str(message.get("id") or "unknown")
            raw_type = str(message.get("type") or "unknown")
            return RawTelegramMessage(
                message_id=message_id,
                raw_message_type=raw_type,
                sender_key=None,
                sender_display_name=None,
                sender_type="unknown",
                timestamp_raw=str(message.get("date") or "1970-01-01T00:00:00+00:00"),
                timestamp_utc="1970-01-01T00:00:00+00:00",
                text=f"[Failed to parse Telegram message: {raw_type}]",
                raw_media_type=str(message.get("media_type")) if message.get("media_type") else None,
                parse_error=str(exc),
            )

    def _parse_message(self, message: dict[str, Any]) -> RawTelegramMessage:
        text = self._flatten_text(message.get("text"))
        raw_type = str(message.get("type") or "message")
        sender_name = message.get("from")
        sender_key = self._sender_key(message)
        sender_type = "service" if raw_type == "service" else ("bot" if str(message.get("from_id", "")).startswith("bot") else "participant")
        media_path = (
            message.get("photo")
            or message.get("file")
            or message.get("thumbnail")
            or message.get("media_path")
        )
        raw_media_type = message.get("media_type") or message.get("mime_type") or raw_type
        media = None
        if media_path:
            media = MediaInfo(type=infer_media_type(str(raw_media_type), str(media_path)), relative_path=str(media_path))
        kind = self._classify_kind(raw_type, media)
        service_text = text if kind == "service" else None
        plain_text = None if kind == "service" else text
        forwarded = bool(message.get("forwarded_from") or message.get("forwarded_from_name") or message.get("forwarded_from_id"))
        raw_date = message.get("date")
        timestamp_raw = str(raw_date or "1970-01-01T00:00:00+00:00")
        return RawTelegramMessage(
            message_id=str(message.get("id")),
            raw_message_type=raw_type,
            sender_key=sender_key,
            sender_display_name=sender_name,
            sender_type=sender_type,
            timestamp_raw=timestamp_raw,
            timestamp_utc=self._normalize_timestamp(raw_date),
            text=plain_text,
            service_text=service_text,
            reply_to_message_id=str(message.get("reply_to_message_id")) if message.get("reply_to_message_id") is not None else None,
            edited_at=self._normalize_timestamp(message.get("edited")) if message.get("edited") else None,
            is_edited=bool(message.get("edited")),
            is_forwarded=forwarded,
            forwarded_from_sanitized="forwarded_source_redacted" if forwarded else None,
            media=media,
            raw_media_type=str(raw_media_type) if media else None,
        )

    def _sender_key(self, message: dict[str, Any]) -> str | None:
        if message.get("actor"):
            return str(message["actor"])
        if message.get("from_id"):
            return str(message["from_id"])
        if message.get("from"):
            return str(message["from"])
        return None

    def _flatten_text(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            flattened = "".join(parts).strip()
            return flattened or None
        return str(value)

    def _classify_kind(self, raw_type: str, media: MediaInfo | None) -> str:
        lowered = raw_type.lower()
        if lowered == "service":
            return "service"
        if media and media.type == "voice":
            return "voice"
        if media and media.type == "image":
            return "image"
        if media:
            return "document"
        if lowered in {"message", "text"}:
            return "text"
        return "unsupported"

    def _normalize_timestamp(self, value: Any) -> str:
        if value is None:
            return "1970-01-01T00:00:00+00:00"
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
