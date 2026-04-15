from __future__ import annotations

import asyncio
import html
import logging
import os
import secrets
import shutil
import signal
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Coroutine, Optional

from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest
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
DEFAULT_UPLOAD_BASE_URL = "https://amznl.cc/tce-upload"
CHAT_PICK_PREFIX = "pickchat:"
BOT_API_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


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
    transcription_language: str = "ru"
    vision_provider: str = "groq"
    vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    ocr_provider: str = "google_cloud_vision"
    google_application_credentials: Optional[str] = None
    strict: bool = False
    max_media_items: Optional[int] = None
    media_size_limit_mb: int = 20
    upload_base_url: str = DEFAULT_UPLOAD_BASE_URL
    upload_host: str = "127.0.0.1"
    upload_port: int = 8091
    upload_token_ttl_seconds: int = 3600
    pending_upload_ttl_seconds: int = 7200
    cleanup_interval_seconds: int = 600
    upload_max_size_mb: int = 512

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
            transcription_language=os.getenv("TCE_TRANSCRIPTION_LANGUAGE", "ru"),
            vision_provider=os.getenv("TCE_VISION_PROVIDER", "groq"),
            vision_model=os.getenv("TCE_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
            ocr_provider=os.getenv("TCE_OCR_PROVIDER", "google_cloud_vision"),
            google_application_credentials=google_creds,
            strict=_env_bool("TCE_STRICT", False),
            max_media_items=int(max_media_items) if max_media_items else None,
            media_size_limit_mb=int(os.getenv("TCE_MEDIA_SIZE_LIMIT_MB", "20")),
            upload_base_url=os.getenv("TCE_UPLOAD_BASE_URL", DEFAULT_UPLOAD_BASE_URL).rstrip("/"),
            upload_host=os.getenv("TCE_UPLOAD_HOST", "127.0.0.1"),
            upload_port=int(os.getenv("TCE_UPLOAD_PORT", "8091")),
            upload_token_ttl_seconds=int(os.getenv("TCE_UPLOAD_TOKEN_TTL_SECONDS", "3600")),
            pending_upload_ttl_seconds=int(os.getenv("TCE_PENDING_UPLOAD_TTL_SECONDS", "7200")),
            cleanup_interval_seconds=int(os.getenv("TCE_CLEANUP_INTERVAL_SECONDS", "600")),
            upload_max_size_mb=int(os.getenv("TCE_UPLOAD_MAX_SIZE_MB", "512")),
        )


@dataclass(slots=True)
class PendingUpload:
    source_path: Path
    original_name: str
    chat_refs: list[dict[str, str]]
    created_at: float = field(default_factory=time.time)
    chat_id: Optional[int] = None
    reply_to_message_id: Optional[int] = None
    message_thread_id: Optional[int] = None


@dataclass(slots=True)
class WebUploadTicket:
    token: str
    user_id: int
    chat_id: int
    reply_to_message_id: Optional[int]
    message_thread_id: Optional[int]
    created_at: float
    expires_at: float
    uploading: bool = False
    consumed: bool = False


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


def format_size_mb(size_bytes: Optional[int]) -> str:
    if size_bytes is None:
        return "unknown size"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


class TelegramConversationExporterBot:
    def __init__(self, config: TelegramBotConfig):
        self.config = config
        self.config.workdir.mkdir(parents=True, exist_ok=True)
        self.pending_uploads: dict[int, PendingUpload] = {}
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.web_upload_tickets: dict[str, WebUploadTicket] = {}
        self.application: Optional[Application] = None
        self.upload_runner: Optional[web.AppRunner] = None
        self.cleanup_task: Optional[asyncio.Task] = None
        self.background_tasks: set[asyncio.Task] = set()

    def build_application(self) -> Application:
        app = ApplicationBuilder().token(self.config.bot_token).build()
        app.add_handler(CommandHandler("start", self.start_command))
        app.add_handler(CommandHandler("help", self.help_command))
        app.add_handler(CommandHandler("status", self.status_command))
        app.add_handler(CommandHandler("pick", self.pick_command))
        app.add_handler(CallbackQueryHandler(self.chat_pick_callback, pattern=f"^{CHAT_PICK_PREFIX}"))
        app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document))
        app.add_error_handler(self.handle_error)
        self.application = app
        return app

    def build_upload_web_app(self) -> web.Application:
        app = web.Application(client_max_size=self.config.upload_max_size_mb * 1024 * 1024)
        app.router.add_get("/tce-upload/{token}", self.render_upload_form)
        app.router.add_post("/tce-upload/{token}", self.handle_upload_post)
        return app

    async def start_upload_server(self) -> None:
        app = self.build_upload_web_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.config.upload_host, self.config.upload_port)
        await site.start()
        self.upload_runner = runner
        LOGGER.info(
            "Upload server listening on %s:%d (base: %s)",
            self.config.upload_host,
            self.config.upload_port,
            self.config.upload_base_url,
        )

    async def stop_upload_server(self) -> None:
        if self.upload_runner is not None:
            await self.upload_runner.cleanup()
            self.upload_runner = None

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._authorize(update):
            return
        text = (
            "Send me a Telegram Desktop export ZIP.\n\n"
            "Best format: one ZIP containing result.json and the media folder.\n"
            "If the ZIP contains one chat, I will process it immediately.\n"
            "If it contains multiple chats, I will ask you which chat to export.\n"
            "If the ZIP is too large for Telegram bot upload, I will generate a one-time web upload link."
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
            "4. If Telegram rejects it as too large, use the one-time upload link I send\n"
            "5. I will return conversation.json, conversation.md, and a bundle ZIP"
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
            f"- upload fallback base URL: {self.config.upload_base_url}\n"
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
        if pending is None or self._is_pending_upload_expired(pending):
            if pending is not None:
                self._drop_pending_upload(user_id)
            await update.effective_message.reply_text("No pending ZIP upload found. Send the ZIP first.")
            return
        chat_ref = context.args[0]
        if chat_ref not in {item['chat_ref'] for item in pending.chat_refs}:
            await update.effective_message.reply_text("Unknown chat_ref for the current pending ZIP.")
            return
        await update.effective_message.reply_text(f"Processing chat '{chat_ref}' now...")
        await self._process_and_deliver_chat(
            chat_id=update.effective_message.chat_id,
            reply_to_message_id=update.effective_message.message_id,
            message_thread_id=getattr(update.effective_message, "message_thread_id", None),
            user_id=user_id,
            source_path=pending.source_path,
            original_name=pending.original_name,
            chat_ref=chat_ref,
            cleanup_source=True,
        )
        self._drop_pending_upload(user_id)

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

        file_size = document.file_size
        if file_size is not None and file_size > BOT_API_DOWNLOAD_LIMIT_BYTES:
            ticket = self._create_web_upload_ticket(
                user_id=update.effective_user.id,
                chat_id=message.chat_id,
                reply_to_message_id=message.message_id,
                message_thread_id=getattr(message, "message_thread_id", None),
            )
            await message.reply_text(
                "That ZIP is too large for the standard Telegram Bot API download limit.\n"
                f"- your file: {format_size_mb(file_size)}\n"
                f"- current bot limit: {format_size_mb(BOT_API_DOWNLOAD_LIMIT_BYTES)}\n\n"
                "Use this one-time upload link instead:\n"
                f"{self.build_upload_url(ticket.token)}\n\n"
                f"The link expires in {self.config.upload_token_ttl_seconds // 60} minutes and becomes inactive after one successful upload."
            )
            return

        user_id = update.effective_user.id
        lock = self.user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
            upload_dir = self.config.workdir / str(user_id) / str(document.file_unique_id)
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            source_path = upload_dir / Path(document.file_name).name
            try:
                telegram_file = await context.bot.get_file(document.file_id)
                await telegram_file.download_to_drive(custom_path=str(source_path))
            except BadRequest as exc:
                if "file is too big" in str(exc).lower():
                    ticket = self._create_web_upload_ticket(
                        user_id=user_id,
                        chat_id=message.chat_id,
                        reply_to_message_id=message.message_id,
                        message_thread_id=getattr(message, "message_thread_id", None),
                    )
                    await message.reply_text(
                        "Telegram refused the ZIP because it is too large for bot download via the standard Bot API.\n"
                        f"- your file: {format_size_mb(file_size)}\n"
                        f"- current bot limit: {format_size_mb(BOT_API_DOWNLOAD_LIMIT_BYTES)}\n\n"
                        "Use this one-time upload link instead:\n"
                        f"{self.build_upload_url(ticket.token)}"
                    )
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    return
                raise

            try:
                chats = await asyncio.to_thread(list_export_chats, source_path)
            except Exception as exc:
                await message.reply_text(f"Could not read that ZIP as a Telegram export: {exc}")
                shutil.rmtree(upload_dir, ignore_errors=True)
                return

            if not chats:
                await message.reply_text("I couldn't find any chats in that export.")
                shutil.rmtree(upload_dir, ignore_errors=True)
                return

            if len(chats) == 1:
                await message.reply_text(f"Found 1 chat: {chats[0]['title']}. Processing now...")
                await self._process_and_deliver_chat(
                    chat_id=message.chat_id,
                    reply_to_message_id=message.message_id,
                    message_thread_id=getattr(message, "message_thread_id", None),
                    user_id=user_id,
                    source_path=source_path,
                    original_name=document.file_name,
                    chat_ref=chats[0]["chat_ref"],
                    cleanup_source=True,
                )
                return

            self._drop_pending_upload(user_id)
            self.pending_uploads[user_id] = PendingUpload(
                source_path=source_path,
                original_name=document.file_name,
                chat_refs=chats,
                chat_id=message.chat_id,
                reply_to_message_id=message.message_id,
                message_thread_id=getattr(message, "message_thread_id", None),
            )
            await self._send_chat_selection(
                chat_id=message.chat_id,
                user_id=user_id,
                chats=chats,
                reply_to_message_id=message.message_id,
                message_thread_id=getattr(message, "message_thread_id", None),
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
        if pending is None or self._is_pending_upload_expired(pending):
            if pending is not None:
                self._drop_pending_upload(user_id)
            await query.edit_message_text("No pending upload found. Please send the ZIP again.")
            return
        chat_ref = query.data[len(CHAT_PICK_PREFIX):]
        if chat_ref not in {item['chat_ref'] for item in pending.chat_refs}:
            await query.edit_message_text("That chat selection is not valid for the current pending ZIP.")
            return
        await query.edit_message_text(f"Processing chat '{chat_ref}' now...")
        await self._process_and_deliver_chat(
            chat_id=query.message.chat_id,
            reply_to_message_id=query.message.message_id,
            message_thread_id=getattr(query.message, "message_thread_id", None),
            user_id=user_id,
            source_path=pending.source_path,
            original_name=pending.original_name,
            chat_ref=chat_ref,
            cleanup_source=True,
        )
        self._drop_pending_upload(user_id)

    async def _send_chat_selection(
        self,
        *,
        chat_id: int,
        user_id: int,
        chats: list[dict[str, str]],
        reply_to_message_id: Optional[int],
        message_thread_id: Optional[int],
    ) -> None:
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
        await self._send_message(
            chat_id=chat_id,
            text=f"I found {len(chats)} chats in this ZIP. Choose which one to process:{extra_instruction}",
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    async def _process_and_deliver_chat(
        self,
        *,
        chat_id: int,
        reply_to_message_id: Optional[int],
        message_thread_id: Optional[int],
        user_id: int,
        source_path: Path,
        original_name: str,
        chat_ref: str,
        cleanup_source: bool,
    ) -> None:
        job_dir = self.config.workdir / str(user_id) / f"job-{chat_ref}"
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        output_dir = job_dir / "out"
        export_config = build_export_config(self.config, source_path, output_dir, chat_ref)

        try:
            await self._send_chat_action(chat_id=chat_id, action=ChatAction.TYPING, message_thread_id=message_thread_id)
            try:
                result = await asyncio.to_thread(ExportPipeline(export_config).run)
            except Exception as exc:
                await self._send_message(
                    chat_id=chat_id,
                    text=f"Export failed: {exc}",
                    reply_to_message_id=reply_to_message_id,
                    message_thread_id=message_thread_id,
                )
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
            await self._send_message(
                chat_id=chat_id,
                text=summary,
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            )
            await self._send_document(
                chat_id=chat_id,
                path=result["json_path"],
                filename="conversation.json",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            )
            await self._send_document(
                chat_id=chat_id,
                path=result["markdown_path"],
                filename="conversation.md",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            )
            await self._send_document(
                chat_id=chat_id,
                path=bundle_path,
                filename=f"{Path(original_name).stem}-{chat_ref}-outputs.zip",
                reply_to_message_id=reply_to_message_id,
                message_thread_id=message_thread_id,
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            if cleanup_source:
                self._cleanup_source_path(source_path)

    async def render_upload_form(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        ticket = self._get_active_web_upload_ticket(token)
        if ticket is None:
            return self._html_response(
                "Upload link inactive",
                "This upload link is invalid, expired, or has already been used.",
                status=410,
            )
        minutes = max(1, int((ticket.expires_at - time.time()) // 60))
        body = (
            "<p>Upload the Telegram export ZIP here.</p>"
            f"<p>This one-time link expires in about {minutes} minute(s) and becomes inactive after a successful upload.</p>"
            f"<p>Maximum upload size configured on the server: {self.config.upload_max_size_mb} MB.</p>"
            '<form id="upload-form" method="post" enctype="multipart/form-data">'
            '<input id="file-input" type="file" name="file" accept=".zip,application/zip" required>'
            '<button id="upload-button" type="submit">Upload ZIP</button>'
            "</form>"
            '<div id="upload-progress-panel" style="display:none;margin-top:20px;">'
            '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;">'
            '<strong id="progress-label">Preparing upload…</strong>'
            '<span id="progress-percent">0%</span>'
            "</div>"
            '<progress id="upload-progress" value="0" max="100" style="width:100%;height:22px;margin-top:10px;"></progress>'
            '<div style="margin-top:10px;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;">'
            '<div><strong>Uploaded</strong><br><span id="progress-uploaded">0 MB</span></div>'
            '<div><strong>Remaining</strong><br><span id="progress-remaining">100%</span></div>'
            '<div><strong>Speed</strong><br><span id="progress-speed">0 MB/s</span></div>'
            "</div>"
            '<p id="progress-status" style="margin-top:12px;color:#444;">Waiting to start…</p>'
            "</div>"
            "<script>"
            "const form = document.getElementById('upload-form');"
            "const fileInput = document.getElementById('file-input');"
            "const uploadButton = document.getElementById('upload-button');"
            "const panel = document.getElementById('upload-progress-panel');"
            "const bar = document.getElementById('upload-progress');"
            "const percentEl = document.getElementById('progress-percent');"
            "const uploadedEl = document.getElementById('progress-uploaded');"
            "const remainingEl = document.getElementById('progress-remaining');"
            "const speedEl = document.getElementById('progress-speed');"
            "const statusEl = document.getElementById('progress-status');"
            "const labelEl = document.getElementById('progress-label');"
            "function formatBytes(bytes){"
            "if(!Number.isFinite(bytes) || bytes <= 0) return '0 B';"
            "const units=['B','KB','MB','GB','TB'];"
            "let value=bytes; let unit=0;"
            "while(value>=1024 && unit<units.length-1){ value/=1024; unit+=1; }"
            "return `${value.toFixed(value>=10||unit===0?0:1)} ${units[unit]}`;"
            "}"
            "form.addEventListener('submit', (event) => {"
            "event.preventDefault();"
            "const file = fileInput.files && fileInput.files[0];"
            "if(!file){ statusEl.textContent='Please choose a ZIP file first.'; return; }"
            "const xhr = new XMLHttpRequest();"
            "const data = new FormData(form);"
            "const startedAt = Date.now();"
            "panel.style.display='block';"
            "uploadButton.disabled = true;"
            "fileInput.disabled = true;"
            "labelEl.textContent = 'Uploading…';"
            "statusEl.textContent = 'Starting upload…';"
            "xhr.open('POST', window.location.href);"
            "xhr.upload.addEventListener('progress', (e) => {"
            "if(!e.lengthComputable) return;"
            "const percent = e.total > 0 ? (e.loaded / e.total) * 100 : 0;"
            "const elapsed = Math.max((Date.now() - startedAt) / 1000, 0.1);"
            "const speed = e.loaded / elapsed;"
            "const remainingPercent = Math.max(0, 100 - percent);"
            "bar.value = percent;"
            "percentEl.textContent = `${percent.toFixed(1)}%`;"
            "uploadedEl.textContent = `${formatBytes(e.loaded)} of ${formatBytes(e.total)}`;"
            "remainingEl.textContent = `${remainingPercent.toFixed(1)}% remaining`;"
            "speedEl.textContent = `${formatBytes(speed)}/s`;"
            "statusEl.textContent = 'Uploading securely to the VPS…';"
            "});"
            "xhr.addEventListener('load', () => {"
            "document.open();"
            "document.write(xhr.responseText);"
            "document.close();"
            "});"
            "xhr.addEventListener('error', () => {"
            "labelEl.textContent = 'Upload failed';"
            "statusEl.textContent = 'Network error during upload. Please request a fresh link in Telegram and try again.';"
            "uploadButton.disabled = false;"
            "fileInput.disabled = false;"
            "});"
            "xhr.addEventListener('abort', () => {"
            "labelEl.textContent = 'Upload cancelled';"
            "statusEl.textContent = 'The upload was cancelled.';"
            "uploadButton.disabled = false;"
            "fileInput.disabled = false;"
            "});"
            "xhr.send(data);"
            "});"
            "</script>"
        )
        return self._html_response("Telegram export upload", body)

    async def handle_upload_post(self, request: web.Request) -> web.Response:
        token = request.match_info["token"]
        ticket = self._get_active_web_upload_ticket(token)
        if ticket is None:
            return self._html_response(
                "Upload link inactive",
                "This upload link is invalid, expired, or has already been used.",
                status=410,
            )
        if ticket.uploading:
            return self._html_response("Upload already in progress", "An upload is already in progress for this link.", status=409)

        ticket.uploading = True
        upload_dir = self.config.workdir / str(ticket.user_id) / f"web-{token}"
        try:
            reader = await request.multipart()
            field = await reader.next()
            if field is None or field.name != "file":
                return self._html_response("No file found", "Please choose a .zip file and try again.", status=400)
            filename = Path(field.filename or "telegram-export.zip").name
            if not filename.lower().endswith(".zip"):
                return self._html_response("Wrong file type", "Please upload a .zip file.", status=400)

            if upload_dir.exists():
                shutil.rmtree(upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            source_path = upload_dir / filename
            bytes_written = 0
            max_bytes = self.config.upload_max_size_mb * 1024 * 1024
            with source_path.open("wb") as f:
                while True:
                    chunk = await field.read_chunk(size=1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > max_bytes:
                        raise web.HTTPRequestEntityTooLarge(max_size=max_bytes, actual_size=bytes_written)
                    f.write(chunk)

            try:
                chats = await asyncio.to_thread(list_export_chats, source_path)
            except Exception as exc:
                shutil.rmtree(upload_dir, ignore_errors=True)
                return self._html_response(
                    "Upload received but invalid",
                    f"The uploaded ZIP could not be read as a Telegram export: {html.escape(str(exc))}",
                    status=400,
                )

            self.web_upload_tickets.pop(token, None)
            ticket.consumed = True
            self._track_background_task(self._handle_uploaded_zip(ticket, source_path, filename, chats))
            return self._html_response(
                "Upload received",
                "The ZIP was uploaded successfully. You can close this page now; the bot will continue in Telegram.",
            )
        except web.HTTPRequestEntityTooLarge:
            shutil.rmtree(upload_dir, ignore_errors=True)
            return self._html_response(
                "Upload too large",
                f"The uploaded file exceeded the configured server limit of {self.config.upload_max_size_mb} MB.",
                status=413,
            )
        except Exception:
            LOGGER.exception("Unexpected error while handling web upload")
            shutil.rmtree(upload_dir, ignore_errors=True)
            return self._html_response(
                "Upload failed",
                "Something went wrong while receiving the file. Please try again with a fresh link from Telegram.",
                status=500,
            )
        finally:
            ticket.uploading = False

    async def _handle_uploaded_zip(
        self,
        ticket: WebUploadTicket,
        source_path: Path,
        original_name: str,
        chats: list[dict[str, str]],
    ) -> None:
        if not chats:
            await self._send_message(
                chat_id=ticket.chat_id,
                text="I couldn't find any chats in that uploaded ZIP.",
                reply_to_message_id=ticket.reply_to_message_id,
                message_thread_id=ticket.message_thread_id,
            )
            self._cleanup_source_path(source_path)
            return

        if len(chats) == 1:
            await self._send_message(
                chat_id=ticket.chat_id,
                text=f"Received the web upload. Found 1 chat: {chats[0]['title']}. Processing now...",
                reply_to_message_id=ticket.reply_to_message_id,
                message_thread_id=ticket.message_thread_id,
            )
            await self._process_and_deliver_chat(
                chat_id=ticket.chat_id,
                reply_to_message_id=ticket.reply_to_message_id,
                message_thread_id=ticket.message_thread_id,
                user_id=ticket.user_id,
                source_path=source_path,
                original_name=original_name,
                chat_ref=chats[0]["chat_ref"],
                cleanup_source=True,
            )
            return

        self._drop_pending_upload(ticket.user_id)
        self.pending_uploads[ticket.user_id] = PendingUpload(
            source_path=source_path,
            original_name=original_name,
            chat_refs=chats,
            chat_id=ticket.chat_id,
            reply_to_message_id=ticket.reply_to_message_id,
            message_thread_id=ticket.message_thread_id,
        )
        await self._send_message(
            chat_id=ticket.chat_id,
            text="Received the web upload successfully.",
            reply_to_message_id=ticket.reply_to_message_id,
            message_thread_id=ticket.message_thread_id,
        )
        await self._send_chat_selection(
            chat_id=ticket.chat_id,
            user_id=ticket.user_id,
            chats=chats,
            reply_to_message_id=ticket.reply_to_message_id,
            message_thread_id=ticket.message_thread_id,
        )

    def _create_web_upload_ticket(
        self,
        *,
        user_id: int,
        chat_id: int,
        reply_to_message_id: Optional[int],
        message_thread_id: Optional[int],
    ) -> WebUploadTicket:
        self._prune_expired_state()
        token = secrets.token_urlsafe(24)
        now = time.time()
        ticket = WebUploadTicket(
            token=token,
            user_id=user_id,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
            created_at=now,
            expires_at=now + self.config.upload_token_ttl_seconds,
        )
        self.web_upload_tickets[token] = ticket
        return ticket

    def build_upload_url(self, token: str) -> str:
        return f"{self.config.upload_base_url}/{token}"

    def _get_active_web_upload_ticket(self, token: str) -> Optional[WebUploadTicket]:
        ticket = self.web_upload_tickets.get(token)
        if ticket is None:
            return None
        if ticket.consumed or ticket.expires_at <= time.time():
            self._drop_web_upload_ticket(token)
            return None
        return ticket

    def _is_pending_upload_expired(self, pending: PendingUpload) -> bool:
        return pending.created_at + self.config.pending_upload_ttl_seconds <= time.time()

    def _drop_pending_upload(self, user_id: int) -> None:
        pending = self.pending_uploads.pop(user_id, None)
        if pending is not None:
            self._cleanup_source_path(pending.source_path)

    def _drop_web_upload_ticket(self, token: str) -> None:
        self.web_upload_tickets.pop(token, None)
        shutil.rmtree(self.config.workdir / "web-upload" / token, ignore_errors=True)
        for candidate in self.config.workdir.glob(f"*/web-{token}"):
            shutil.rmtree(candidate, ignore_errors=True)

    def _cleanup_source_path(self, source_path: Path) -> None:
        try:
            if source_path.exists():
                shutil.rmtree(source_path.parent, ignore_errors=True)
        except Exception:
            LOGGER.exception("Failed to clean up source path %s", source_path)

    def _prune_expired_state(self, now: Optional[float] = None) -> None:
        current = now or time.time()
        expired_tokens = [token for token, ticket in self.web_upload_tickets.items() if ticket.expires_at <= current or ticket.consumed]
        for token in expired_tokens:
            self._drop_web_upload_ticket(token)
        expired_users = [user_id for user_id, pending in self.pending_uploads.items() if pending.created_at + self.config.pending_upload_ttl_seconds <= current]
        for user_id in expired_users:
            self._drop_pending_upload(user_id)

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.cleanup_interval_seconds)
                self._prune_expired_state()
        except asyncio.CancelledError:
            raise

    def _html_response(self, title: str, body: str, status: int = 200) -> web.Response:
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<style>body{font-family:sans-serif;max-width:760px;margin:40px auto;padding:0 16px;line-height:1.5;}"
            "input,button{font-size:16px;margin-top:12px;}button{padding:8px 14px;}"
            "code{background:#f3f3f3;padding:2px 4px;border-radius:4px;}</style>"
            "</head><body>"
            f"<h1>{html.escape(title)}</h1>{body}</body></html>"
        )
        return web.Response(text=page, status=status, content_type="text/html")

    def _track_background_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)

        def _done(completed: asyncio.Task) -> None:
            self.background_tasks.discard(completed)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("Background task failed")

        task.add_done_callback(_done)

    async def _send_chat_action(self, *, chat_id: int, action: str, message_thread_id: Optional[int]) -> None:
        if self.application is None:
            raise RuntimeError("Telegram application is not initialized")
        kwargs = {"chat_id": chat_id, "action": action}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await self.application.bot.send_chat_action(**kwargs)

    async def _send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int],
        message_thread_id: Optional[int],
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        if self.application is None:
            raise RuntimeError("Telegram application is not initialized")
        kwargs = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await self.application.bot.send_message(**kwargs)

    async def _send_document(
        self,
        *,
        chat_id: int,
        path: Path,
        filename: str,
        reply_to_message_id: Optional[int],
        message_thread_id: Optional[int],
    ) -> None:
        if self.application is None:
            raise RuntimeError("Telegram application is not initialized")
        kwargs = {"chat_id": chat_id, "filename": filename}
        if reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        with path.open("rb") as f:
            await self.application.bot.send_document(document=f, **kwargs)

    async def handle_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        LOGGER.exception("Unhandled bot error", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "Something went wrong while handling that request. Please try again, and if it keeps happening I can inspect the server logs."
                )
            except Exception:
                LOGGER.exception("Failed to send error message back to Telegram user")

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

    async def run(self) -> None:
        application = self.build_application()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        initialized = False
        started = False
        polling_started = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass

        try:
            await application.initialize()
            initialized = True
            await application.start()
            started = True
            await self.start_upload_server()
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
            if application.updater is not None:
                await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                polling_started = True
            await stop_event.wait()
        finally:
            for task in list(self.background_tasks):
                task.cancel()
            if self.background_tasks:
                await asyncio.gather(*self.background_tasks, return_exceptions=True)
            if polling_started and application.updater is not None:
                await application.updater.stop()
            if self.cleanup_task is not None:
                self.cleanup_task.cancel()
                try:
                    await self.cleanup_task
                except asyncio.CancelledError:
                    pass
            await self.stop_upload_server()
            if started:
                await application.stop()
            if initialized:
                await application.shutdown()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    config = TelegramBotConfig.from_env()
    bot = TelegramConversationExporterBot(config)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
