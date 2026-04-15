from __future__ import annotations

import asyncio
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import ExportConfig, RangeConfig
from .pipeline import ExportPipeline
from .telegram_export_parser import TelegramExportParser

DEFAULT_BOT_WORKDIR = Path("/tmp/tce-telegram-bot")
CHAT_PICK_PREFIX = "pickchat:"


@dataclass(slots=True)
class TelegramBotConfig:
    bot_token: str
    workdir: Path = DEFAULT_BOT_WORKDIR
    allowed_user_ids: set[int] = field(default_factory=set)
    transcribe_voice: bool = True
    describe_images: bool = True
    ocr: bool = True
    transcription_provider: str = "groq"
    transcription_model: str = "whisper-large-v3-turbo"
    transcription_language: str = "he"
    vision_provider: str = "groq"
    vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    ocr_provider: str = "google_cloud_vision"
    google_application_credentials: Optional[str] = None
    strict: bool = False
    max_media_items: Optional[int] = None
    media_size_limit_mb: int = 20

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
        allowed_raw = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
        allowed_users = {int(part.strip()) for part in allowed_raw.split(",") if part.strip()} if allowed_raw else set()
        workdir = Path(os.getenv("TCE_TELEGRAM_WORKDIR", str(DEFAULT_BOT_WORKDIR))).expanduser()
        google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or None
        max_media_items = os.getenv("TCE_MAX_MEDIA_ITEMS")
        return cls(
            bot_token=token,
            workdir=workdir,
            allowed_user_ids=allowed_users,
            transcribe_voice=_env_bool("TCE_TRANSCRIBE_VOICE", True),
            describe_images=_env_bool("TCE_DESCRIBE_IMAGES", True),
            ocr=_env_bool("TCE_ENABLE_OCR", True),
            transcription_provider=os.getenv("TCE_TRANSCRIPTION_PROVIDER", "groq"),
            transcription_model=os.getenv("TCE_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"),
            transcription_language=os.getenv("TCE_TRANSCRIPTION_LANGUAGE", "he"),
            vision_provider=os.getenv("TCE_VISION_PROVIDER", "groq"),
            vision_model=os.getenv("TCE_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            ocr_provider=os.getenv("TCE_OCR_PROVIDER", "google_cloud_vision"),
            google_application_credentials=google_creds,
            strict=_env_bool("TCE_STRICT", False),
            max_media_items=int(max_media_items) if max_media_items else None,
            media_size_limit_mb=int(os.getenv("TCE_MEDIA_SIZE_LIMIT_MB", "20")),
        )


@dataclass(slots=True)
class PendingUpload:
    source_path: Path
    original_name: str
    chat_refs: list[dict[str, str]]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_export_config(bot_config: TelegramBotConfig, source: Path, output_dir: Path, chat_ref: str) -> ExportConfig:
    return ExportConfig(
        source=source,
        chat_ref=chat_ref,
        output_dir=output_dir,
        range=RangeConfig(mode="full_chat"),
        transcribe_voice=bot_config.transcribe_voice,
        transcription_provider=bot_config.transcription_provider,
        transcription_model=bot_config.transcription_model,
        transcription_language=bot_config.transcription_language,
        describe_images=bot_config.describe_images,
        vision_provider=bot_config.vision_provider,
        vision_model=bot_config.vision_model,
        ocr=bot_config.ocr,
        ocr_provider=bot_config.ocr_provider,
        google_application_credentials=bot_config.google_application_credentials,
        strict=bot_config.strict,
        media_size_limit_mb=bot_config.media_size_limit_mb,
        max_media_items=bot_config.max_media_items,
    )


def create_result_bundle(output_dir: Path) -> Path:
    bundle_path = output_dir / "conversation_export_bundle.zip"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ["conversation.json", "conversation.md"]:
            path = output_dir / name
            if path.exists():
                archive.write(path, arcname=name)
    return bundle_path


def list_export_chats(source_path: Path) -> list[dict[str, str]]:
    return TelegramExportParser(source_path).list_chats()


class TelegramConversationExporterBot:
    def __init__(self, config: TelegramBotConfig):
        self.config = config
        self.config.workdir.mkdir(parents=True, exist_ok=True)
        self.pending_uploads: dict[int, PendingUpload] = {}
        self.user_locks: dict[int, asyncio.Lock] = {}

    def build_application(self) -> Application:
        app = ApplicationBuilder().token(self.config.bot_token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(CommandHandler("pick", self.pick_command))
        app.add_handler(CallbackQueryHandler(self.chat_pick_callback, pattern=f"^{CHAT_PICK_PREFIX}"))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        return app

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        text = (
            "Send me a Telegram Desktop export ZIP.\n\n"
            "Best format: one ZIP containing result.json and the media folder.\n"
            "If the ZIP contains one chat, I will process it immediately.\n"
            "If it contains multiple chats, I will ask you which chat to export."
        )
        await update.effective_message.reply_text(text)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        await update.effective_message.reply_text(
            "Usage:\n"
            "1. Export a Telegram chat from Telegram Desktop\n"
            "2. ZIP the export JSON together with its media folder\n"
            "3. Send the ZIP here\n"
            "4. I will return conversation.json, conversation.md, and a bundle ZIP"
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        creds_configured = bool(self.config.google_application_credentials or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        await update.effective_message.reply_text(
            "Current bot configuration:\n"
            f"- voice transcription: {'on' if self.config.transcribe_voice else 'off'}\n"
            f"- image description: {'on' if self.config.describe_images else 'off'}\n"
            f"- OCR: {'on' if self.config.ocr else 'off'}\n"
            f"- transcription model: {self.config.transcription_model}\n"
            f"- vision model: {self.config.vision_model}\n"
            f"- OCR provider: {self.config.ocr_provider}\n"
            f"- Google credentials configured: {'yes' if creds_configured else 'no'}"
        )

    async def pick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        if not context.args:
            await update.effective_message.reply_text("Usage: /pick <chat_ref>")
            return
        user_id = update.effective_user.id
        pending = self.pending_uploads.get(user_id)
        if pending is None:
            await update.effective_message.reply_text("No pending ZIP upload found. Send the ZIP first.")
            return
        chat_ref = context.args[0]
        if chat_ref not in {item['chat_ref'] for item in pending.chat_refs}:
            await update.effective_message.reply_text("Unknown chat_ref for the current pending ZIP.")
            return
        await update.effective_message.reply_text(f"Processing chat '{chat_ref}' now...")
        await self._process_and_deliver(update, context, pending.source_path, pending.original_name, chat_ref)
        self.pending_uploads.pop(user_id, None)

    async def handle_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        message = update.effective_message
        document = message.document
        if document is None:
            return
        if not document.file_name or not document.file_name.lower().endswith(".zip"):
            await message.reply_text("Please send a .zip file containing the Telegram export JSON and media.")
            return

        user_id = update.effective_user.id
        lock = self.user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            upload_dir = self.config.workdir / str(user_id) / str(document.file_unique_id)
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            source_path = upload_dir / document.file_name
            telegram_file = await context.bot.get_file(document.file_id)
            await telegram_file.download_to_drive(custom_path=str(source_path))

            try:
                chats = await asyncio.to_thread(list_export_chats, source_path)
            except Exception as exc:
                await message.reply_text(f"Could not read that ZIP as a Telegram export: {exc}")
                return

            if not chats:
                await message.reply_text("I couldn't find any chats in that export.")
                return

            if len(chats) == 1:
                await message.reply_text(f"Found 1 chat: {chats[0]['title']}. Processing now...")
                await self._process_and_deliver(update, context, source_path, document.file_name, chats[0]["chat_ref"])
                return

            self.pending_uploads[user_id] = PendingUpload(source_path=source_path, original_name=document.file_name, chat_refs=chats)
            buttons = [
                [InlineKeyboardButton(chat["title"][:64], callback_data=f"{CHAT_PICK_PREFIX}{chat['chat_ref']}")]
                for chat in chats[:20]
            ]
            extra_instruction = ""
            if len(chats) > 20:
                extra_instruction = (
                    "\n\nOnly the first 20 chats are shown as buttons. "
                    "You can also use /pick <chat_ref>. Available chat_refs:\n- "
                    + "\n- ".join(chat["chat_ref"] for chat in chats)
                )
            await message.reply_text(
                f"I found {len(chats)} chats in this ZIP. Choose which one to process:{extra_instruction}",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    async def chat_pick_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not await self._authorize(update, callback_mode=True):
            return
        await query.answer()
        user_id = query.from_user.id
        pending = self.pending_uploads.get(user_id)
        if pending is None:
            await query.edit_message_text("No pending upload found. Please send the ZIP again.")
            return
        chat_ref = query.data[len(CHAT_PICK_PREFIX):]
        if chat_ref not in {item['chat_ref'] for item in pending.chat_refs}:
            await query.edit_message_text("That chat selection is not valid for the current pending ZIP.")
            return
        await query.edit_message_text(f"Processing chat '{chat_ref}' now...")
        await self._process_and_deliver(update, context, pending.source_path, pending.original_name, chat_ref)
        self.pending_uploads.pop(user_id, None)

    async def _process_and_deliver(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        source_path: Path,
        original_name: str,
        chat_ref: str,
    ) -> None:
        message = update.effective_message or update.callback_query.message
        user_id = update.effective_user.id
        job_dir = self.config.workdir / str(user_id) / f"job-{chat_ref}"
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        output_dir = job_dir / "out"
        export_config = build_export_config(self.config, source_path, output_dir, chat_ref)

        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
        try:
            try:
                result = await asyncio.to_thread(ExportPipeline(export_config).run)
            except Exception as exc:
                await message.reply_text(f"Export failed: {exc}")
                return

            payload = result["conversation"]
            bundle_path = create_result_bundle(output_dir)
            summary = (
                f"Done.\n"
                f"- chat_ref: {chat_ref}\n"
                f"- messages: {payload['run_summary']['total_messages']}\n"
                f"- missing media files: {payload['run_summary']['missing_media_files']}\n"
                f"- transcription ok: {payload['run_summary']['transcription']['succeeded']}\n"
                f"- vision ok: {payload['run_summary']['vision']['succeeded']}\n"
                f"- OCR ok: {payload['run_summary']['ocr']['succeeded']}"
            )
            await message.reply_text(summary)
            with result["json_path"].open("rb") as f:
                await message.reply_document(document=f, filename="conversation.json")
            with result["markdown_path"].open("rb") as f:
                await message.reply_document(document=f, filename="conversation.md")
            with bundle_path.open("rb") as f:
                await message.reply_document(document=f, filename=f"{Path(original_name).stem}-{chat_ref}-outputs.zip")
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _authorize(self, update: Update, callback_mode: bool = False) -> bool:
        user = update.effective_user
        if user is None:
            return False
        if self.config.allowed_user_ids and user.id not in self.config.allowed_user_ids:
            if callback_mode and update.callback_query:
                await update.callback_query.answer("Not authorized", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("You are not authorized to use this bot.")
            return False
        return True


def main() -> None:
    config = TelegramBotConfig.from_env()
    bot = TelegramConversationExporterBot(config)
    application = bot.build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
