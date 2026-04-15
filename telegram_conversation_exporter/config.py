from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class RangeConfig:
    mode: str = "full_chat"
    start_message_id: Optional[str] = None
    end_message_id: Optional[str] = None
    start_time_utc: Optional[str] = None
    end_time_utc: Optional[str] = None


@dataclass(slots=True)
class ExportConfig:
    source: Path
    chat_ref: str
    output_dir: Path
    range: RangeConfig = field(default_factory=RangeConfig)
    transcribe_voice: bool = False
    transcription_provider: str = "groq"
    transcription_model: Optional[str] = "whisper-large-v3-turbo"
    transcription_language: Optional[str] = "ru"
    stub_transcription: bool = False
    describe_images: bool = False
    vision_provider: str = "groq"
    vision_model: Optional[str] = "meta-llama/llama-4-scout-17b-16e-instruct"
    stub_vision: bool = False
    ocr: bool = False
    ocr_provider: str = "google_cloud_vision"
    google_application_credentials: Optional[str] = None
    stub_ocr: bool = False
    dry_run: bool = False
    strict: bool = False
    media_size_limit_mb: int = 20
    max_messages: Optional[int] = None
    max_media_items: Optional[int] = None

    @property
    def media_size_limit_bytes(self) -> int:
        return self.media_size_limit_mb * 1024 * 1024

    @property
    def cache_dir(self) -> Path:
        return self.output_dir / ".tce_cache"
