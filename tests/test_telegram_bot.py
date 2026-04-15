import json
import os
import zipfile
from pathlib import Path

from telegram_conversation_exporter.telegram_bot import (
    DEFAULT_BOT_WORKDIR,
    TelegramBotConfig,
    build_export_config,
    create_result_bundle,
    list_export_chats,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_bot_config_from_env_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "1, 2")
    monkeypatch.setenv("TCE_TELEGRAM_WORKDIR", str(tmp_path / "bot-work"))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/google.json")

    config = TelegramBotConfig.from_env()

    assert config.bot_token == "123:abc"
    assert config.allowed_user_ids == {1, 2}
    assert config.workdir == tmp_path / "bot-work"
    assert config.google_application_credentials == "/tmp/google.json"
    assert config.transcribe_voice is True
    assert config.describe_images is True
    assert config.ocr is True
    assert config.transcription_language == "ru"


def test_build_export_config_uses_bot_defaults(tmp_path):
    bot_config = TelegramBotConfig(
        bot_token="123:abc",
        workdir=DEFAULT_BOT_WORKDIR,
        google_application_credentials="/tmp/google.json",
    )
    source = FIXTURES / "media_chat.json"
    output_dir = tmp_path / "out"

    config = build_export_config(bot_config, source, output_dir, "chat_media")

    assert config.source == source
    assert config.chat_ref == "chat_media"
    assert config.output_dir == output_dir
    assert config.transcribe_voice is True
    assert config.describe_images is True
    assert config.ocr is True
    assert config.transcription_model == "whisper-large-v3-turbo"
    assert config.vision_model == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert config.google_application_credentials == "/tmp/google.json"


def test_create_result_bundle_contains_json_and_markdown(tmp_path):
    (tmp_path / "conversation.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (tmp_path / "conversation.md").write_text("# hi\n", encoding="utf-8")

    bundle = create_result_bundle(tmp_path)

    assert bundle.exists()
    with zipfile.ZipFile(bundle) as archive:
        assert set(archive.namelist()) == {"conversation.json", "conversation.md"}


def test_list_export_chats_reads_zip_source(tmp_path):
    zip_path = tmp_path / "telegram-export.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(FIXTURES / "full_export.json", arcname="result.json")

    chats = list_export_chats(zip_path)

    assert {chat["chat_ref"] for chat in chats} == {"chat_one", "chat_two"}
