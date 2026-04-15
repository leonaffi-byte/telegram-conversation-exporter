from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tools import transcription_tools


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
    normalized = (provider or "auto").strip().lower()
    if normalized == "grok":
        return "groq"
    return normalized or "auto"


@contextmanager
def _temporary_env(var_name: str, value: str | None):
    original = os.environ.get(var_name)
    try:
        if value is None:
            os.environ.pop(var_name, None)
        else:
            os.environ[var_name] = value
        yield
    finally:
        if original is None:
            os.environ.pop(var_name, None)
        else:
            os.environ[var_name] = original


@dataclass(slots=True)
class RealTranscriptionBackend:
    requested_provider: str = "auto"
    model: str | None = None
    language: str | None = "he"
    provider: str = "auto"
    name: str = "real-transcriber"

    def __post_init__(self) -> None:
        self.requested_provider = _normalize_transcription_provider(self.requested_provider)
        self.provider = self._resolve_provider(self.requested_provider)
        self.name = self._build_name()

    def transcribe(self, path: Path) -> BackendResult:
        validation_error = transcription_tools._validate_audio_file(str(path))
        if validation_error:
            raise RuntimeError(validation_error["error"])

        if self.provider == "local":
            return self._transcribe_local(path)
        if self.provider == "local_command":
            return self._transcribe_local_command(path)
        if self.provider == "groq":
            return self._transcribe_groq(path)
        if self.provider == "openai":
            return self._transcribe_openai(path)
        raise RuntimeError(f"Unsupported transcription provider: {self.provider}")

    def _build_name(self) -> str:
        model_part = self.model or "default"
        language_part = self.language or "auto"
        return f"real-transcriber:{self.provider}:{model_part}:{language_part}"

    def _resolve_provider(self, provider: str) -> str:
        if provider == "stub":
            return "stub"
        if provider == "local":
            if transcription_tools._HAS_FASTER_WHISPER:
                return "local"
            if transcription_tools._has_local_command():
                return "local_command"
            raise RuntimeError("Local transcription provider requested but no local backend is available")
        if provider == "groq":
            if transcription_tools._HAS_OPENAI and os.getenv("GROQ_API_KEY"):
                return "groq"
            raise RuntimeError("Groq transcription provider requested but GROQ_API_KEY or openai package is unavailable")
        if provider == "openai":
            if transcription_tools._HAS_OPENAI and transcription_tools._has_openai_audio_backend():
                return "openai"
            raise RuntimeError("OpenAI transcription provider requested but no audio backend credentials are available")
        if provider == "auto":
            if transcription_tools._HAS_FASTER_WHISPER:
                return "local"
            if transcription_tools._has_local_command():
                return "local_command"
            if transcription_tools._HAS_OPENAI and os.getenv("GROQ_API_KEY"):
                return "groq"
            if transcription_tools._HAS_OPENAI and transcription_tools._has_openai_audio_backend():
                return "openai"
            raise RuntimeError("No transcription provider available for auto mode")
        raise RuntimeError(f"Unsupported transcription provider: {provider}")

    def _transcribe_local(self, path: Path) -> BackendResult:
        if not transcription_tools._HAS_FASTER_WHISPER:
            raise RuntimeError("faster-whisper not installed")

        from faster_whisper import WhisperModel

        model_name = self.model or transcription_tools.DEFAULT_LOCAL_MODEL
        model = transcription_tools._local_model
        model_name_loaded = transcription_tools._local_model_name
        if model is None or model_name_loaded != model_name:
            model = WhisperModel(model_name, device="auto", compute_type="auto")
            transcription_tools._local_model = model
            transcription_tools._local_model_name = model_name

        transcribe_kwargs = {"beam_size": 5}
        if self.language:
            transcribe_kwargs["language"] = self.language
        segments, info = model.transcribe(str(path), **transcribe_kwargs)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return BackendResult(
            text=text,
            language=getattr(info, "language", self.language),
            confidence=getattr(info, "language_probability", None),
        )

    def _transcribe_local_command(self, path: Path) -> BackendResult:
        command_template = transcription_tools._get_local_command_template()
        if not command_template:
            raise RuntimeError(
                f"{transcription_tools.LOCAL_STT_COMMAND_ENV} not configured and no local whisper binary was found"
            )

        configured_language = self.language or transcription_tools.DEFAULT_LOCAL_STT_LANGUAGE
        model_name = transcription_tools._normalize_local_command_model(
            self.model or transcription_tools.DEFAULT_LOCAL_MODEL
        )

        with tempfile.TemporaryDirectory(prefix="hermes-local-stt-") as output_dir:
            prepared_input, prep_error = transcription_tools._prepare_local_audio(str(path), output_dir)
            if prep_error:
                raise RuntimeError(prep_error)
            command = command_template.format(
                input_path=transcription_tools.shlex.quote(prepared_input),
                output_dir=transcription_tools.shlex.quote(output_dir),
                language=transcription_tools.shlex.quote(configured_language),
                model=transcription_tools.shlex.quote(model_name),
            )
            try:
                subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                details = exc.stderr.strip() or exc.stdout.strip() or str(exc)
                raise RuntimeError(f"Local STT failed: {details}") from exc

            txt_files = sorted(Path(output_dir).glob("*.txt"))
            if not txt_files:
                raise RuntimeError("Local STT command completed but did not produce a .txt transcript")
            return BackendResult(text=txt_files[0].read_text(encoding="utf-8").strip(), language=configured_language)

    def _transcribe_groq(self, path: Path) -> BackendResult:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        if not transcription_tools._HAS_OPENAI:
            raise RuntimeError("openai package not installed")

        model_name = self.model or transcription_tools.DEFAULT_GROQ_STT_MODEL
        if model_name in transcription_tools.OPENAI_MODELS:
            model_name = transcription_tools.DEFAULT_GROQ_STT_MODEL

        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=transcription_tools.GROQ_BASE_URL, timeout=30, max_retries=0)
        try:
            with path.open("rb") as audio_file:
                request_kwargs = {
                    "model": model_name,
                    "file": audio_file,
                    "response_format": "verbose_json",
                }
                if self.language:
                    request_kwargs["language"] = self.language
                transcription = client.audio.transcriptions.create(**request_kwargs)
            return BackendResult(
                text=transcription_tools._extract_transcript_text(transcription),
                language=getattr(transcription, "language", None) or self.language,
            )
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _transcribe_openai(self, path: Path) -> BackendResult:
        api_key, base_url = transcription_tools._resolve_openai_audio_client_config()
        if not transcription_tools._HAS_OPENAI:
            raise RuntimeError("openai package not installed")

        model_name = self.model or transcription_tools.DEFAULT_STT_MODEL
        if model_name in transcription_tools.GROQ_MODELS:
            model_name = transcription_tools.DEFAULT_STT_MODEL

        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)
        try:
            with path.open("rb") as audio_file:
                request_kwargs = {
                    "model": model_name,
                    "file": audio_file,
                    "response_format": "json",
                }
                if self.language:
                    request_kwargs["language"] = self.language
                transcription = client.audio.transcriptions.create(**request_kwargs)
            return BackendResult(
                text=transcription_tools._extract_transcript_text(transcription),
                language=getattr(transcription, "language", None) or self.language,
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
        return BackendResult(text=f"Transcript for {path.name}", language="en", confidence=0.99)


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
    provider = _normalize_transcription_provider(getattr(config, "transcription_provider", "auto"))
    if getattr(config, "stub_transcription", False) or provider == "stub":
        return StubTranscriptionBackend()
    return RealTranscriptionBackend(
        requested_provider=provider,
        model=getattr(config, "transcription_model", None),
        language=getattr(config, "transcription_language", "he"),
    )
