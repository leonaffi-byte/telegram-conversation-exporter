from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
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

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised indirectly in tests via monkeypatching class attr
    OpenAI = None


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


@dataclass(slots=True)
class RealTranscriptionBackend:
    requested_provider: str = "groq"
    model: str | None = DEFAULT_GROQ_MODEL
    language: str | None = "he"
    provider: str = "groq"
    name: str = "groq-whisper"

    def __post_init__(self) -> None:
        self.provider = _normalize_transcription_provider(self.requested_provider)
        self.model = self.model or DEFAULT_GROQ_MODEL
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
        model=getattr(config, "transcription_model", DEFAULT_GROQ_MODEL),
        language=getattr(config, "transcription_language", "he"),
    )
