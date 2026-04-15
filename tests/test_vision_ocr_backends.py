from pathlib import Path
from types import SimpleNamespace

from telegram_conversation_exporter import backends as exporter_backends
from telegram_conversation_exporter.backends import (
    BackendResult,
    GoogleCloudVisionOCRBackend,
    GroqVisionBackend,
    StubOCRBackend,
    StubVisionBackend,
    build_ocr_backend,
    build_vision_backend,
)
from telegram_conversation_exporter.config import ExportConfig
from telegram_conversation_exporter import pipeline as exporter_pipeline
from telegram_conversation_exporter.pipeline import ExportPipeline

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_vision_backend_defaults_and_stub_flag(tmp_path):
    config = ExportConfig(
        source=FIXTURES / "simple_private_chat.json",
        chat_ref="chat_simple",
        output_dir=tmp_path,
        describe_images=True,
    )
    backend = build_vision_backend(config)
    assert isinstance(backend, GroqVisionBackend)
    assert backend.provider == "groq"
    assert backend.model == "meta-llama/llama-4-scout-17b-16e-instruct"

    config.stub_vision = True
    backend = build_vision_backend(config)
    assert isinstance(backend, StubVisionBackend)


def test_groq_vision_backend_calls_api_with_expected_defaults(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"description":"Screenshot of a chat app","category":"screenshot"}'))]
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        last_instance = None

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = FakeChat()
            FakeClient.last_instance = self

        def close(self):
            self.closed = True

    monkeypatch.setattr(exporter_backends, "OpenAI", FakeClient)
    monkeypatch.setattr(exporter_backends.os, "getenv", lambda key, default=None: "token" if key == "GROQ_API_KEY" else default)

    backend = GroqVisionBackend()
    result = backend.describe(image_path)

    assert result.description == "Screenshot of a chat app"
    assert result.category == "screenshot"
    assert FakeClient.last_instance.kwargs["api_key"] == "token"
    assert FakeClient.last_instance.kwargs["base_url"] == exporter_backends.GROQ_BASE_URL
    call = FakeClient.last_instance.chat.completions.calls[0]
    assert call["model"] == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert call["messages"][0]["content"][1]["type"] == "image_url"


def test_build_ocr_backend_defaults_and_stub_flag(tmp_path):
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text('{"type":"service_account"}', encoding="utf-8")

    config = ExportConfig(
        source=FIXTURES / "simple_private_chat.json",
        chat_ref="chat_simple",
        output_dir=tmp_path,
        ocr=True,
        google_application_credentials=str(credentials_path),
    )
    backend = build_ocr_backend(config)
    assert isinstance(backend, GoogleCloudVisionOCRBackend)
    assert backend.credentials_path == str(credentials_path)

    config.stub_ocr = True
    backend = build_ocr_backend(config)
    assert isinstance(backend, StubOCRBackend)


def test_google_cloud_vision_ocr_backend_calls_document_text_detection(monkeypatch, tmp_path):
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"fake-image-bytes")
    credentials_path = tmp_path / "service-account.json"
    credentials_path.write_text('{"type":"service_account"}', encoding="utf-8")

    class FakeAnnotatorClient:
        last_image = None

        def document_text_detection(self, image):
            FakeAnnotatorClient.last_image = image
            return SimpleNamespace(
                full_text_annotation=SimpleNamespace(text="HELLO\nWORLD"),
                error=SimpleNamespace(message=""),
                text_annotations=[],
            )

    fake_module = SimpleNamespace(
        ImageAnnotatorClient=lambda: FakeAnnotatorClient(),
        Image=lambda content: SimpleNamespace(content=content),
    )
    monkeypatch.setattr(exporter_backends, "google_cloud_vision", fake_module)

    backend = GoogleCloudVisionOCRBackend(credentials_path=str(credentials_path))
    result = backend.extract(image_path)

    assert result.text == "HELLO\nWORLD"
    assert FakeAnnotatorClient.last_image.content == b"fake-image-bytes"


def test_pipeline_uses_real_vision_and_ocr_backends_by_default(monkeypatch, tmp_path):
    class FakeVisionBackend:
        name = "fake-vision"

        def describe(self, path):
            return BackendResult(description="Screenshot of a payment confirmation", category="screenshot")

    class FakeOCRBackend:
        name = "fake-ocr"

        def extract(self, path):
            return BackendResult(text="Payment received")

    monkeypatch.setattr(exporter_pipeline, "build_vision_backend", lambda config: FakeVisionBackend())
    monkeypatch.setattr(exporter_pipeline, "build_ocr_backend", lambda config: FakeOCRBackend())

    config = ExportConfig(
        source=FIXTURES / "media_chat.json",
        chat_ref="chat_media",
        output_dir=tmp_path,
        describe_images=True,
        ocr=True,
    )
    payload = ExportPipeline(config).run()["conversation"]
    by_id = {message["source"]["message_id"]: message for message in payload["messages"]}
    assert by_id["24"]["enrichment"]["vision"]["status"] == "ok"
    assert by_id["24"]["enrichment"]["vision"]["description"] == "Screenshot of a payment confirmation"
    assert by_id["24"]["enrichment"]["ocr"]["status"] == "ok"
    assert by_id["24"]["enrichment"]["ocr"]["text"] == "Payment received"
