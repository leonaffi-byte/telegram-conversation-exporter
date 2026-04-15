from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from .models import MediaInfo, ProcessingError

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VOICE_EXTENSIONS = {".ogg", ".oga", ".mp3", ".wav", ".m4a"}


def infer_media_type(raw_type: str | None, file_name: str | None) -> str:
    raw = (raw_type or "").lower()
    suffix = Path(file_name or "").suffix.lower()
    if "voice" in raw or suffix in VOICE_EXTENSIONS:
        return "voice"
    if any(token in raw for token in ("photo", "image", "picture")) or suffix in IMAGE_EXTENSIONS:
        return "image"
    if raw:
        return "document"
    return "unknown"


def validate_media(base_dir: Path, media: Optional[MediaInfo], size_limit_bytes: int) -> tuple[Optional[MediaInfo], list[ProcessingError]]:
    if media is None or not media.relative_path:
        return media, []

    errors: list[ProcessingError] = []
    path = base_dir / media.relative_path
    if not path.exists():
        media.available = False
        errors.append(ProcessingError(stage="validation", code="missing_media", message=f"Missing media file: {media.relative_path}"))
        return media, errors

    size = path.stat().st_size
    media.file_size_bytes = size
    media.available = size > 0
    if size == 0:
        errors.append(ProcessingError(stage="validation", code="empty_media", message=f"Empty media file: {media.relative_path}"))
        return media, errors
    if size > size_limit_bytes:
        media.available = False
        errors.append(ProcessingError(stage="validation", code="media_too_large", message=f"Media exceeds size limit: {media.relative_path}"))
        return media, errors

    media.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    media.mime_type = media.mime_type or _guess_mime(path)
    return media, errors


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix in {".ogg", ".oga"}:
        return "audio/ogg"
    if suffix == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"
