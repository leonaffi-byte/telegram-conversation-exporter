from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Optional


@dataclass(slots=True)
class ProcessingError:
    stage: str
    code: str
    message: str


@dataclass(slots=True)
class MediaInfo:
    type: str
    relative_path: Optional[str]
    mime_type: Optional[str] = None
    available: bool = False
    file_size_bytes: Optional[int] = None
    sha256: Optional[str] = None
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass(slots=True)
class ProcessingResult:
    status: str
    text: Optional[str] = None
    language: Optional[str] = None
    confidence: Optional[float] = None
    backend: Optional[str] = None
    error_code: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None


@dataclass(slots=True)
class ReplyPreview:
    message_id: str
    sender_label: Optional[str]
    text: str


@dataclass(slots=True)
class RawTelegramMessage:
    message_id: str
    raw_message_type: str
    sender_key: Optional[str]
    sender_display_name: Optional[str]
    sender_type: str
    timestamp_raw: str
    timestamp_utc: str
    text: Optional[str]
    service_text: Optional[str] = None
    reply_to_message_id: Optional[str] = None
    edited_at: Optional[str] = None
    is_edited: bool = False
    is_forwarded: bool = False
    forwarded_from_sanitized: Optional[str] = None
    media: Optional[MediaInfo] = None
    raw_media_type: Optional[str] = None
    parse_error: Optional[str] = None


@dataclass(slots=True)
class ParsedChat:
    chat_ref: str
    title: str
    source_export_kind: str
    messages: list[RawTelegramMessage]
    participant_keys: list[str]


@dataclass(slots=True)
class Participant:
    participant_id: str
    label: str
    index: int
    message_count: Optional[int] = None


@dataclass(slots=True)
class ExportMessage:
    canonical_message_id: str
    sequence_index: int
    kind: str
    source: dict[str, Any]
    sender: dict[str, Any]
    timestamps: dict[str, Any]
    normalized: dict[str, Any]
    relations: dict[str, Any]
    processing: dict[str, Any]
    media: Optional[dict[str, Any]] = None
    enrichment: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class FeatureRunSummary:
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(slots=True)
class ExportRunSummary:
    total_messages: int
    message_kind_counts: dict[str, int]
    missing_media_files: int
    transcription: FeatureRunSummary
    vision: FeatureRunSummary
    ocr: FeatureRunSummary
    processing_duration_seconds: float


@dataclass(slots=True)
class ConversationExport:
    schema_version: str
    metadata: dict[str, Any]
    participants: list[Participant]
    messages: list[ExportMessage]
    run_summary: ExportRunSummary


def to_plain_data(value: Any) -> Any:
    if is_dataclass(value):
        return {key: to_plain_data(val) for key, val in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain_data(val) for key, val in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    return value
