from pathlib import Path

from telegram_conversation_exporter import backends as exporter_backends
from telegram_conversation_exporter.backends import (
    BackendResult,
    RealTranscriptionBackend,
    StubTranscriptionBackend,
    build_transcription_backend,
)
from telegram_conversation_exporter.config import ExportConfig
from telegram_conversation_exporter import pipeline as exporter_pipeline
from telegram_conversation_exporter.pipeline import ExportPipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_transcription_backend_defaults_to_russian_and_grok_alias(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "simple_private_chat.json",
        chat_ref="chat_simple",
        output_dir=tmp_path,
        transcribe_voice=True,
    )
    backend = build_transcription_backend(config)
    assert isinstance(backend, RealTranscriptionBackend)
    assert backend.provider == "groq"
    assert backend.language == "ru"
    assert backend.model == "whisper-large-v3-turbo"

    config.transcription_provider = "grok"
    backend = build_transcription_backend(config)
    assert isinstance(backend, RealTranscriptionBackend)
    assert backend.provider == "groq"


def test_build_transcription_backend_honors_stub_flag(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "simple_private_chat.json",
        chat_ref="chat_simple",
        output_dir=tmp_path,
        transcribe_voice=True,
        stub_transcription=True,
    )
    backend = build_transcription_backend(config)
    assert isinstance(backend, StubTranscriptionBackend)


def test_real_transcription_backend_calls_groq_with_expected_defaults(monkeypatch, tmp_path):
    audio_path = tmp_path / "sample.ogg"
    audio_path.write_bytes(b"fake-audio")

    class FakeTranscriptions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return {"text": "שלום עולם", "language": "he"}

    class FakeAudio:
        def __init__(self):
            self.transcriptions = FakeTranscriptions()

    class FakeClient:
        last_instance = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.audio = FakeAudio()
            FakeClient.last_instance = self

        def close(self):
            self.closed = True

    monkeypatch.setattr(exporter_backends, "OpenAI", FakeClient)
    monkeypatch.setattr(exporter_backends.os, "getenv", lambda key, default=None: "token" if key == "GROQ_API_KEY" else default)

    backend = RealTranscriptionBackend()
    result = backend.transcribe(audio_path)

    assert result.text == "שלום עולם"
    assert result.language == "ru"
    assert FakeClient.last_instance.kwargs["api_key"] == "token"
    assert FakeClient.last_instance.kwargs["base_url"] == exporter_backends.GROQ_BASE_URL
    call = FakeClient.last_instance.audio.transcriptions.calls[0]
    assert call["model"] == "whisper-large-v3-turbo"
    assert call["language"] == "ru"
    assert call["response_format"] == "verbose_json"


def test_pipeline_uses_real_backend_by_default(monkeypatch, tmp_path):
    class FakeBackend:
        name = "fake-real-transcriber"

        def transcribe(self, path):
            return BackendResult(text="שלום עולם", language="he", confidence=0.98)

    monkeypatch.setattr(exporter_pipeline, "build_transcription_backend", lambda config: FakeBackend())

    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        transcribe_voice=True,
    )
    pipeline = ExportPipeline(config)
    payload = pipeline.run()["conversation"]
    by_id = {message["source"]["message_id"]: message for message in payload["messages"]}
    assert by_id["22"]["enrichment"]["transcription"]["status"] == "ok"
    assert by_id["22"]["enrichment"]["transcription"]["text"] == "שלום עולם"
    assert by_id["22"]["enrichment"]["transcription"]["language"] == "he"


def test_pipeline_real_backend_failure_is_best_effort(tmp_path):
    class FailingBackend:
        name = "failing-real-transcriber"

        def transcribe(self, path):
            raise RuntimeError("transcriber unavailable")

    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        transcribe_voice=True,
    )
    payload = ExportPipeline(config, transcription_backend=FailingBackend()).run()["conversation"]
    by_id = {message["source"]["message_id"]: message for message in payload["messages"]}
    assert by_id["22"]["enrichment"]["transcription"]["status"] == "failed"
    assert by_id["22"]["processing"]["export_status"] == "partial"
    assert by_id["22"]["processing"]["errors"][0]["stage"] == "transcription"
