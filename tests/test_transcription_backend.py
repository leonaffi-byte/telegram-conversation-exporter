from pathlib import Path

from telegram_conversation_exporter import backends as exporter_backends
from telegram_conversation_exporter.backends import BackendResult, RealTranscriptionBackend, StubTranscriptionBackend, build_transcription_backend
from telegram_conversation_exporter.config import ExportConfig
from telegram_conversation_exporter import pipeline as exporter_pipeline
from telegram_conversation_exporter.pipeline import ExportPipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_transcription_backend_defaults_to_hebrew_and_grok_alias(monkeypatch, tmp_path):
    monkeypatch.setattr(exporter_backends.transcription_tools, "_HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(exporter_backends.transcription_tools, "_has_local_command", lambda: False)
    monkeypatch.setattr(exporter_backends.transcription_tools, "_has_openai_audio_backend", lambda: False)

    config = ExportConfig(
        source=FIXTURES / "simple_private_chat.json",
        chat_ref="chat_simple",
        output_dir=tmp_path,
        transcribe_voice=True,
    )
    backend = build_transcription_backend(config)
    assert isinstance(backend, RealTranscriptionBackend)
    assert backend.provider == "local"
    assert backend.language == "he"

    config.transcription_provider = "grok"
    monkeypatch.setattr(exporter_backends.transcription_tools, "_HAS_FASTER_WHISPER", False)
    monkeypatch.setattr(exporter_backends.os, "getenv", lambda key, default=None: "token" if key == "GROQ_API_KEY" else default)
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
