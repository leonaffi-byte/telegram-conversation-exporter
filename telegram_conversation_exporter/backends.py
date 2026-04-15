from __future__ import annotations

import base64
import json
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"
DEFAULT_GROQ_VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_GOOGLE_VISION_CREDENTIALS_PATH = "/root/.hermes/secrets/google-vision-service-account.json"
SUPPORTED_AUDIO_EXTENSIONS = {
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".oga",
    ".ogg",
    ".wav",
    ".webm",
}
SUPPORTED_IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised indirectly in tests via monkeypatching class attr
    OpenAI = None

try:
    from google.cloud import vision as google_cloud_vision
except ImportError:  # pragma: no cover - exercised indirectly in tests via monkeypatching module attr
    google_cloud_vision = None


@dataclass(slots=True)
class BackendResult:
    text: str | None = None
    description: str | None = None
    category: str | None = None
    language: str | None = None
    confidence: float | None = None


class TranscriptionBackend(Protocol):
    name: str

    def transcribe(self, path: Path) -> BackendResult: ...


class VisionBackend(Protocol):
    name: str

    def describe(self, path: Path) -> BackendResult: ...


class OCRBackend(Protocol):
    name: str

    def extract(self, path: Path) -> BackendResult: ...


def _normalize_transcription_provider(provider: str | None) -> str:
    normalized = (provider or "groq").strip().lower()
    if normalized in {"grok", "groq", "auto"}:
        return "groq"
    if normalized == "stub":
        return "stub"
    raise RuntimeError(f"Unsupported transcription provider: {provider}")


def _normalize_vision_provider(provider: str | None) -> str:
    normalized = (provider or "groq").strip().lower()
    if normalized in {"grok", "groq", "auto"}:
        return "groq"
    if normalized == "stub":
        return "stub"
    raise RuntimeError(f"Unsupported vision provider: {provider}")


def _normalize_ocr_provider(provider: str | None) -> str:
    normalized = (provider or "google_cloud_vision").strip().lower()
    aliases = {
        "auto": "google_cloud_vision",
        "gcv": "google_cloud_vision",
        "google": "google_cloud_vision",
        "google_cloud": "google_cloud_vision",
        "google-cloud-vision": "google_cloud_vision",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"google_cloud_vision", "stub"}:
        return normalized
    raise RuntimeError(f"Unsupported OCR provider: {provider}")


def _validate_audio_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Audio file not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"Audio path is not a file: {path}")
    suffix = path.suffix.lower()
    if suffix and suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise RuntimeError(
            f"Unsupported audio format: {suffix}. Supported formats: {', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}"
        )
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Audio file is empty: {path}")


def _validate_image_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Image file not found: {path}")
    if not path.is_file():
        raise RuntimeError(f"Image path is not a file: {path}")
    suffix = path.suffix.lower()
    if suffix and suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise RuntimeError(
            f"Unsupported image format: {suffix}. Supported formats: {', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))}"
        )
    if path.stat().st_size <= 0:
        raise RuntimeError(f"Image file is empty: {path}")


def _extract_transcript_text(transcription) -> str:
    if transcription is None:
        return ""
    if isinstance(transcription, str):
        return transcription.strip()
    if isinstance(transcription, dict):
        return str(transcription.get("text") or "").strip()
    text = getattr(transcription, "text", None)
    if text is not None:
        return str(text).strip()
    raise RuntimeError("Groq transcription response did not include text")


def _extract_message_text(response) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response.strip()
    choices = getattr(response, "choices", None)
    if choices:
        content = getattr(choices[0].message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        parts.append(str(text))
                else:
                    text = getattr(item, "text", None)
                    if text:
                        parts.append(str(text))
            return "\n".join(parts).strip()
    raise RuntimeError("Vision response did not include message content")


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except Exception:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(stripped[start : end + 1])
    raise RuntimeError(f"Expected JSON object from model, got: {text[:200]}")


def _image_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _resolve_google_credentials_path(configured_path: str | None = None) -> str | None:
    candidate = configured_path or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if candidate:
        return candidate
    if os.path.exists(DEFAULT_GOOGLE_VISION_CREDENTIALS_PATH):
        return DEFAULT_GOOGLE_VISION_CREDENTIALS_PATH
    return None


@dataclass(slots=True)
class RealTranscriptionBackend:
    requested_provider: str = "groq"
    model: str | None = DEFAULT_GROQ_TRANSCRIPTION_MODEL
    language: str | None = "he"
    provider: str = "groq"
    name: str = "groq-whisper"

    def __post_init__(self) -> None:
        self.provider = _normalize_transcription_provider(self.requested_provider)
        self.model = self.model or DEFAULT_GROQ_TRANSCRIPTION_MODEL
        language_part = self.language or "auto"
        self.name = f"groq-whisper:{self.model}:{language_part}"

    def transcribe(self, path: Path) -> BackendResult:
        _validate_audio_file(path)
        if self.provider != "groq":
            raise RuntimeError(f"Unsupported transcription provider: {self.provider}")
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        if OpenAI is None:
            raise RuntimeError("openai package not installed")

        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=60, max_retries=1)
        try:
            with path.open("rb") as audio_file:
                request_kwargs = {
                    "model": self.model,
                    "file": audio_file,
                    "response_format": "verbose_json",
                }
                if self.language:
                    request_kwargs["language"] = self.language
                transcription = client.audio.transcriptions.create(**request_kwargs)
            return BackendResult(
                text=_extract_transcript_text(transcription),
                language=getattr(transcription, "language", None) or self.language,
                confidence=getattr(transcription, "language_probability", None),
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


@dataclass(slots=True)
class GroqVisionBackend:
    requested_provider: str = "groq"
    model: str | None = DEFAULT_GROQ_VISION_MODEL
    provider: str = "groq"
    name: str = "groq-vision"

    def __post_init__(self) -> None:
        self.provider = _normalize_vision_provider(self.requested_provider)
        self.model = self.model or DEFAULT_GROQ_VISION_MODEL
        self.name = f"groq-vision:{self.model}"

    def describe(self, path: Path) -> BackendResult:
        _validate_image_file(path)
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        if OpenAI is None:
            raise RuntimeError("openai package not installed")

        prompt = (
            "Analyze this image for downstream conversation analysis. "
            "Return JSON only with keys: description, category. "
            "category must be one of: photo, screenshot, document, other. "
            "description should be concise but useful, mention visible text only if important context."
        )
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=60, max_retries=1)
        try:
            response = client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": _image_data_url(path)}},
                        ],
                    }
                ],
            )
            payload = _extract_json_object(_extract_message_text(response))
            description = str(payload.get("description") or "").strip()
            category = str(payload.get("category") or "other").strip().lower() or "other"
            if category not in {"photo", "screenshot", "document", "other"}:
                category = "other"
            if not description:
                raise RuntimeError("Vision model returned empty description")
            return BackendResult(description=description, category=category)
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()


@dataclass(slots=True)
class GoogleCloudVisionOCRBackend:
    credentials_path: str | None = None
    provider: str = "google_cloud_vision"
    name: str = "google-cloud-vision-ocr"

    def __post_init__(self) -> None:
        self.credentials_path = _resolve_google_credentials_path(self.credentials_path)
        if self.credentials_path:
            self.name = f"google-cloud-vision-ocr:{Path(self.credentials_path).name}"

    def extract(self, path: Path) -> BackendResult:
        _validate_image_file(path)
        if google_cloud_vision is None:
            raise RuntimeError("google-cloud-vision package not installed")
        if not self.credentials_path:
            raise RuntimeError("Google Cloud Vision credentials not configured")
        credentials_file = Path(self.credentials_path)
        if not credentials_file.exists():
            raise RuntimeError(f"Google Cloud Vision credentials file not found: {credentials_file}")

        previous = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_file)
        try:
            client = google_cloud_vision.ImageAnnotatorClient()
            image = google_cloud_vision.Image(content=path.read_bytes())
            response = client.document_text_detection(image=image)
            error = getattr(response, "error", None)
            error_message = getattr(error, "message", None)
            if error_message:
                raise RuntimeError(error_message)
            text = getattr(getattr(response, "full_text_annotation", None), "text", None)
            if not text:
                text_annotations = getattr(response, "text_annotations", None) or []
                if text_annotations:
                    text = getattr(text_annotations[0], "description", None)
            return BackendResult(text=(text or "").strip())
        finally:
            if previous is None:
                os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
            else:
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = previous


class StubTranscriptionBackend:
    name = "stub-transcriber"

    def transcribe(self, path: Path) -> BackendResult:
        if "fail" in path.name:
            raise RuntimeError("forced transcription failure")
        return BackendResult(text=f"Transcript for {path.name}", language="he", confidence=0.99)


class StubVisionBackend:
    name = "stub-vision"

    def describe(self, path: Path) -> BackendResult:
        if "fail" in path.name:
            raise RuntimeError("forced vision failure")
        return BackendResult(description="Non-identifying test image description", category="screenshot")


class StubOCRBackend:
    name = "stub-ocr"

    def extract(self, path: Path) -> BackendResult:
        if "fail" in path.name:
            raise RuntimeError("forced ocr failure")
        return BackendResult(text=f"OCR text for {path.name}")


def build_transcription_backend(config) -> TranscriptionBackend:
    provider = _normalize_transcription_provider(getattr(config, "transcription_provider", "groq"))
    if getattr(config, "stub_transcription", False) or provider == "stub":
        return StubTranscriptionBackend()
    return RealTranscriptionBackend(
        requested_provider=provider,
        model=getattr(config, "transcription_model", DEFAULT_GROQ_TRANSCRIPTION_MODEL),
        language=getattr(config, "transcription_language", "he"),
    )


def build_vision_backend(config) -> VisionBackend:
    provider = _normalize_vision_provider(getattr(config, "vision_provider", "groq"))
    if getattr(config, "stub_vision", False) or provider == "stub":
        return StubVisionBackend()
    return GroqVisionBackend(
        requested_provider=provider,
        model=getattr(config, "vision_model", DEFAULT_GROQ_VISION_MODEL),
    )


def build_ocr_backend(config) -> OCRBackend:
    provider = _normalize_ocr_provider(getattr(config, "ocr_provider", "google_cloud_vision"))
    if getattr(config, "stub_ocr", False) or provider == "stub":
        return StubOCRBackend()
    return GoogleCloudVisionOCRBackend(
        credentials_path=getattr(config, "google_application_credentials", None),
    )
