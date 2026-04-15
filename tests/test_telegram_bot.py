import asyncio
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from telegram_conversation_exporter.telegram_bot import (
    BOT_API_DOWNLOAD_LIMIT_BYTES,
    DEFAULT_BOT_WORKDIR,
    PendingUpload,
    TelegramBotConfig,
    TelegramConversationExporterBot,
    build_export_config,
    create_result_bundle,
    format_size_mb,
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
    assert config.upload_base_url.endswith("/tce-upload")


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


def test_format_size_mb_formats_expected_value():
    assert format_size_mb(BOT_API_DOWNLOAD_LIMIT_BYTES) == "20.0 MB"
    assert format_size_mb(None) == "unknown size"


def test_build_upload_url_uses_config_base(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path, upload_base_url="https://example.com/tce-upload")
    bot = TelegramConversationExporterBot(config)

    assert bot.build_upload_url("abc123") == "https://example.com/tce-upload/abc123"


def test_create_web_upload_ticket_stores_expected_metadata(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path, upload_token_ttl_seconds=600)
    bot = TelegramConversationExporterBot(config)

    ticket = bot._create_web_upload_ticket(user_id=11, chat_id=22, reply_to_message_id=33, message_thread_id=44)

    assert ticket.user_id == 11
    assert ticket.chat_id == 22
    assert ticket.reply_to_message_id == 33
    assert ticket.message_thread_id == 44
    assert bot.web_upload_tickets[ticket.token].expires_at > ticket.created_at


def test_prune_expired_state_removes_expired_pending_upload_and_cleans_files(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path, pending_upload_ttl_seconds=10)
    bot = TelegramConversationExporterBot(config)
    source_dir = tmp_path / "55" / "upload-1"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "export.zip"
    source_path.write_text("x", encoding="utf-8")
    bot.pending_uploads[55] = PendingUpload(
        source_path=source_path,
        original_name="export.zip",
        chat_refs=[{"chat_ref": "chat_one", "title": "Chat One"}],
        created_at=100,
    )

    bot._prune_expired_state(now=111)

    assert 55 not in bot.pending_uploads
    assert not source_dir.exists()


def test_prune_expired_state_removes_expired_web_ticket(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path)
    bot = TelegramConversationExporterBot(config)
    ticket = bot._create_web_upload_ticket(user_id=1, chat_id=2, reply_to_message_id=None, message_thread_id=None)

    bot._prune_expired_state(now=ticket.expires_at + 1)

    assert ticket.token not in bot.web_upload_tickets


def test_drop_pending_upload_cleans_previous_source(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path)
    bot = TelegramConversationExporterBot(config)
    source_dir = tmp_path / "77" / "upload-old"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "old.zip"
    source_path.write_text("x", encoding="utf-8")
    bot.pending_uploads[77] = PendingUpload(
        source_path=source_path,
        original_name="old.zip",
        chat_refs=[{"chat_ref": "chat_one", "title": "Chat One"}],
    )

    bot._drop_pending_upload(77)

    assert 77 not in bot.pending_uploads
    assert not source_dir.exists()


def test_render_upload_form_includes_progress_ui(tmp_path):
    config = TelegramBotConfig(bot_token="123:abc", workdir=tmp_path)
    bot = TelegramConversationExporterBot(config)
    ticket = bot._create_web_upload_ticket(user_id=1, chat_id=2, reply_to_message_id=None, message_thread_id=None)
    request = SimpleNamespace(match_info={"token": ticket.token})

    response = asyncio.run(bot.render_upload_form(request))
    text = response.text

    assert 'id="upload-progress"' in text
    assert 'id="progress-speed"' in text
    assert 'id="progress-remaining"' in text
    assert "XMLHttpRequest" in text
