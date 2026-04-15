from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .anonymization import build_participant_map
from .backends import (
    StubOCRBackend,
    StubTranscriptionBackend,
    StubVisionBackend,
    build_ocr_backend,
    build_transcription_backend,
    build_vision_backend,
)
from .config import ExportConfig
from .exporters import conversation_to_dict, write_json_export, write_markdown_export
from .media import validate_media
from .models import (
    ConversationExport,
    ExportMessage,
    ExportRunSummary,
    FeatureRunSummary,
    ProcessingError,
)
from .range_selection import resolve_reply_status, select_messages
from .schema import PIPELINE_VERSION, SCHEMA_VERSION, validate_export
from .telegram_export_parser import TelegramExportParser

PRIVACY_LIMITATIONS = [
    "Message text, transcripts, and OCR may still contain identifying information.",
    "Forwarded content may preserve indirect identity clues.",
]


class ExportPipeline:
    def __init__(self, config: ExportConfig, *, transcription_backend=None, vision_backend=None, ocr_backend=None):
        self.config = config
        self.transcription_backend = transcription_backend or self._default_transcription_backend()
        self.vision_backend = vision_backend or self._default_vision_backend()
        self.ocr_backend = ocr_backend or self._default_ocr_backend()

    def _default_transcription_backend(self):
        if not self.config.transcribe_voice:
            return StubTranscriptionBackend()
        return build_transcription_backend(self.config)

    def _default_vision_backend(self):
        if not self.config.describe_images:
            return StubVisionBackend()
        return build_vision_backend(self.config)

    def _default_ocr_backend(self):
        if not self.config.ocr:
            return StubOCRBackend()
        return build_ocr_backend(self.config)

    def dry_run_summary(self) -> dict[str, Any]:
        parser = TelegramExportParser(self.config.source)
        chat = parser.parse_chat(self.config.chat_ref)
        selected = self._apply_limits(select_messages(chat.messages, self.config.range))
        participants = build_participant_map(chat.messages)
        media_counts = {"voice": 0, "image": 0, "document": 0}
        missing_media_files = 0
        unsupported_count = 0
        warnings: list[str] = []
        for message in selected:
            kind = self._message_kind(message)
            if kind == "unsupported":
                unsupported_count += 1
            if message.media:
                media_bucket = message.media.type if message.media.type in media_counts else "document"
                media_counts[media_bucket] += 1
                validated, errors = validate_media(parser.base_dir, message.media, self.config.media_size_limit_bytes)
                if any(error.code == "missing_media" for error in errors):
                    missing_media_files += 1
                for error in errors:
                    warnings.append(error.message)
        if unsupported_count:
            warnings.append(f"Unsupported messages in range: {unsupported_count}")
        if self.config.max_media_items is not None:
            total_media = sum(media_counts.values())
            if total_media > self.config.max_media_items:
                warnings.append(
                    f"Media items exceed max_media_items limit ({total_media} > {self.config.max_media_items}); extra items will be exported without enrichment."
                )
        return {
            "chat_ref": chat.chat_ref,
            "chat_title": chat.title,
            "message_count": len(selected),
            "participant_count": len(participants),
            "media_counts": media_counts,
            "missing_media_files": missing_media_files,
            "unsupported_count": unsupported_count,
            "estimated_enrichment_work": {
                "transcription": media_counts["voice"] if self.config.transcribe_voice else 0,
                "vision": media_counts["image"] if self.config.describe_images else 0,
                "ocr": media_counts["image"] if self.config.ocr else 0,
            },
            "warnings": sorted(set(warnings)),
        }

    def run(self) -> dict[str, Path | dict[str, Any]]:
        started = time.perf_counter()
        parser = TelegramExportParser(self.config.source)
        chat = parser.parse_chat(self.config.chat_ref)
        participants_map = build_participant_map(chat.messages)
        selected_messages = self._apply_limits(select_messages(chat.messages, self.config.range))
        all_ids = {message.message_id for message in chat.messages}
        selected_ids = {message.message_id for message in selected_messages}

        kind_counts = {key: 0 for key in ("text", "voice", "image", "document", "service", "unsupported")}
        missing_media = 0
        export_messages: list[ExportMessage] = []
        transcription_summary = FeatureRunSummary()
        vision_summary = FeatureRunSummary()
        ocr_summary = FeatureRunSummary()
        media_budget = {"seen": 0}

        for sequence_index, message in enumerate(selected_messages, start=1):
            export_message, missing = self._build_export_message(
                sequence_index=sequence_index,
                message=message,
                participants_map=participants_map,
                selected_messages=selected_messages,
                selected_ids=selected_ids,
                all_ids=all_ids,
                base_dir=parser.base_dir,
                chat_ref=chat.chat_ref,
                transcription_summary=transcription_summary,
                vision_summary=vision_summary,
                ocr_summary=ocr_summary,
                media_budget=media_budget,
            )
            kind_counts[export_message.kind] += 1
            missing_media += int(missing)
            export_messages.append(export_message)

        run_summary = ExportRunSummary(
            total_messages=len(export_messages),
            message_kind_counts=kind_counts,
            missing_media_files=missing_media,
            transcription=transcription_summary,
            vision=vision_summary,
            ocr=ocr_summary,
            processing_duration_seconds=round(time.perf_counter() - started, 6),
        )
        participants = list(participants_map.values())
        conversation = ConversationExport(
            schema_version=SCHEMA_VERSION,
            metadata={
                "pipeline_version": PIPELINE_VERSION,
                "source_type": "telegram_export",
                "source_export_kind": chat.source_export_kind,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "chat_ref": chat.chat_ref,
                "chat_title_redacted": "Redacted Chat",
                "participant_count": len(participants),
                "enabled_features": {
                    "transcribe_voice": self.config.transcribe_voice,
                    "describe_images": self.config.describe_images,
                    "ocr": self.config.ocr,
                },
                "privacy_limitations": PRIVACY_LIMITATIONS,
                "range": asdict(self.config.range),
            },
            participants=participants,
            messages=export_messages,
            run_summary=run_summary,
        )
        payload = conversation_to_dict(conversation)
        validate_export(payload)
        if self.config.strict and any(message["processing"]["errors"] for message in payload["messages"]):
            raise RuntimeError("Strict mode aborted export because one or more messages had processing errors.")
        json_path = write_json_export(conversation, self.config.output_dir)
        md_path = write_markdown_export(conversation, self.config.output_dir)
        return {"conversation": payload, "json_path": json_path, "markdown_path": md_path}

    def _apply_limits(self, messages):
        limited = list(messages)
        if self.config.max_messages is not None:
            limited = limited[: self.config.max_messages]
        return limited

    def _message_kind(self, message) -> str:
        if message.service_text or message.sender_type == "service":
            return "service"
        if message.media:
            if message.media.type == "voice":
                return "voice"
            if message.media.type == "image":
                return "image"
            if message.media.type in {"document", "unknown"}:
                return "document"
        if message.raw_message_type.lower() in {"message", "text"}:
            return "text"
        return "unsupported"

    def _build_export_message(
        self,
        *,
        sequence_index,
        message,
        participants_map,
        selected_messages,
        selected_ids,
        all_ids,
        base_dir,
        chat_ref,
        transcription_summary,
        vision_summary,
        ocr_summary,
        media_budget,
    ):
        warnings: list[str] = []
        errors: list[dict[str, str]] = []
        media_dict = None
        enrichment = None
        missing = False
        kind = self._message_kind(message)
        if kind == "unsupported" and not message.text:
            message.text = f"[Unsupported Telegram message: {message.raw_message_type}]"
        if message.media:
            validated_media, media_errors = validate_media(base_dir, message.media, self.config.media_size_limit_bytes)
            message.media = validated_media
            media_dict = asdict(validated_media)
            for error in media_errors:
                errors.append(asdict(error))
                warnings.append(error.message)
            missing = any(error.code == "missing_media" for error in media_errors)
            enrichable_media = validated_media.type in {"voice", "image"}
            over_media_limit = (
                enrichable_media
                and self.config.max_media_items is not None
                and media_budget["seen"] >= self.config.max_media_items
            )
            if over_media_limit:
                warnings.append("Media enrichment skipped because max_media_items limit was reached.")
                enrichment = self._skipped_enrichment(message.media.type, "max_media_items_exceeded", transcription_summary, vision_summary, ocr_summary)
            else:
                if enrichable_media:
                    media_budget["seen"] += 1
                enrichment = self._enrich_message(message, base_dir, transcription_summary, vision_summary, ocr_summary, errors)
        reply_status = resolve_reply_status(message, selected_ids, all_ids)
        reply_preview = None
        if message.reply_to_message_id:
            original = next((candidate for candidate in selected_messages if candidate.message_id == message.reply_to_message_id), None)
            if original:
                participant = participants_map.get(original.sender_key) if original.sender_key else None
                preview_text = original.text or original.service_text or "[No text]"
                reply_preview = {
                    "message_id": original.message_id,
                    "sender_label": participant.label if participant else None,
                    "text": preview_text[:80],
                }
        participant = participants_map.get(message.sender_key) if message.sender_key and message.sender_type != "service" else None
        sender_type = message.sender_type if message.sender_type else ("participant" if participant else "unknown")
        if message.parse_error:
            errors.append(asdict(ProcessingError(stage="parse", code="parse_failed", message=message.parse_error)))
            warnings.append(f"Message {message.message_id} could not be parsed cleanly.")
        export_status = "partial" if errors else "ok"
        export_message = ExportMessage(
            canonical_message_id=f"{chat_ref}:{message.message_id}",
            sequence_index=sequence_index,
            kind=kind,
            source={
                "message_id": message.message_id,
                "reply_to_message_id": message.reply_to_message_id,
                "raw_message_type": message.raw_message_type,
                "raw_media_type": message.raw_media_type,
                "is_edited": message.is_edited,
                "edited_at": message.edited_at,
                "is_forwarded": message.is_forwarded,
                "forwarded_from_sanitized": message.forwarded_from_sanitized,
            },
            sender={
                "participant_id": participant.participant_id if participant else None,
                "participant_label": participant.label if participant else None,
                "sender_type": sender_type,
            },
            timestamps={"source_raw": message.timestamp_raw, "utc": message.timestamp_utc},
            normalized={
                "plain_text": message.text,
                "rendered_markdown_block": message.text or message.service_text,
                "service_text": message.service_text,
            },
            media=media_dict,
            relations={"reply_status": reply_status, "reply_preview": reply_preview},
            enrichment=enrichment,
            processing={"export_status": export_status, "warnings": warnings, "errors": errors},
        )
        return export_message, missing

    def _skipped_enrichment(self, media_type, reason, transcription_summary, vision_summary, ocr_summary):
        if media_type == "voice":
            transcription_summary.requested += 1
            transcription_summary.skipped += 1
            return {
                "transcription": self._transcription_result(status="skipped", text="[Voice transcription skipped]", backend=None, error_code=reason),
                "vision": None,
                "ocr": None,
            }
        if media_type == "image":
            if self.config.describe_images:
                vision_summary.requested += 1
                vision_summary.skipped += 1
            if self.config.ocr:
                ocr_summary.requested += 1
                ocr_summary.skipped += 1
            return {
                "transcription": None,
                "vision": self._vision_result(status="skipped", description="[Image description skipped]", backend=None, error_code=reason, category="unknown"),
                "ocr": self._ocr_result(status="skipped", text="[OCR skipped]", backend=None, error_code=reason),
            }
        return {"transcription": None, "vision": None, "ocr": None}

    def _cache_path(self, cache_kind: str, backend_name: str | None, media_sha: str | None) -> Path | None:
        if not media_sha:
            return None
        backend_part = backend_name or "none"
        return self.config.cache_dir / f"{cache_kind}-{backend_part}-{media_sha}.json"

    def _load_cached_result(self, cache_kind: str, backend_name: str | None, media_sha: str | None) -> dict[str, Any] | None:
        cache_path = self._cache_path(cache_kind, backend_name, media_sha)
        if cache_path is None or not cache_path.exists():
            return None
        return json.loads(cache_path.read_text(encoding="utf-8"))

    def _store_cached_result(self, cache_kind: str, backend_name: str | None, media_sha: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        cache_path = self._cache_path(cache_kind, backend_name, media_sha)
        if cache_path is not None:
            self.config.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return payload

    def _enrich_message(self, message, base_dir, transcription_summary, vision_summary, ocr_summary, errors):
        if not message.media.available:
            return {
                "transcription": self._transcription_result(status="skipped", text="[Media unavailable]", backend=None, error_code="missing_media") if message.media.type == "voice" else None,
                "vision": self._vision_result(status="skipped", description="[Media unavailable]", backend=None, error_code="missing_media", category="unknown") if message.media.type == "image" else None,
                "ocr": self._ocr_result(status="skipped", text="[Media unavailable]", backend=None, error_code="missing_media") if message.media.type == "image" else None,
            }
        path = base_dir / message.media.relative_path
        enrichment = {
            "transcription": None,
            "vision": None,
            "ocr": None,
        }
        if message.media.type == "voice":
            enrichment["transcription"] = self._run_transcription(path, message.media.sha256, transcription_summary, errors)
        elif message.media.type == "image":
            enrichment["vision"] = self._run_vision(path, message.media.sha256, vision_summary, errors)
            enrichment["ocr"] = self._run_ocr(path, message.media.sha256, ocr_summary, errors)
        return enrichment

    def _run_transcription(self, path, media_sha, summary, errors):
        summary.requested += 1
        if not self.config.transcribe_voice:
            summary.skipped += 1
            return self._transcription_result(status="not_requested", text=None, backend=None, error_code=None)
        cached = self._load_cached_result("transcription", self.transcription_backend.name, media_sha)
        if cached is not None:
            summary.succeeded += 1
            return cached
        try:
            result = self.transcription_backend.transcribe(path)
            summary.succeeded += 1
            payload = self._transcription_result(status="ok", text=result.text, language=result.language, confidence=result.confidence, backend=self.transcription_backend.name, error_code=None)
            return self._store_cached_result("transcription", self.transcription_backend.name, media_sha, payload)
        except Exception as exc:
            summary.failed += 1
            errors.append(asdict(ProcessingError(stage="transcription", code="transcription_failed", message=str(exc))))
            return self._transcription_result(status="failed", text="[Voice transcription failed]", backend=self.transcription_backend.name, error_code="transcription_failed")

    def _run_vision(self, path, media_sha, summary, errors):
        summary.requested += 1
        if not self.config.describe_images:
            summary.skipped += 1
            return self._vision_result(status="not_requested", description=None, backend=None, error_code=None, category=None)
        cached = self._load_cached_result("vision", self.vision_backend.name, media_sha)
        if cached is not None:
            summary.succeeded += 1
            return cached
        try:
            result = self.vision_backend.describe(path)
            summary.succeeded += 1
            payload = self._vision_result(status="ok", description=result.description, backend=self.vision_backend.name, error_code=None, category=result.category)
            return self._store_cached_result("vision", self.vision_backend.name, media_sha, payload)
        except Exception as exc:
            summary.failed += 1
            errors.append(asdict(ProcessingError(stage="vision", code="vision_failed", message=str(exc))))
            return self._vision_result(status="failed", description="[Image description failed]", backend=self.vision_backend.name, error_code="vision_failed", category="unknown")

    def _run_ocr(self, path, media_sha, summary, errors):
        summary.requested += 1
        if not self.config.ocr:
            summary.skipped += 1
            return self._ocr_result(status="not_requested", text=None, backend=None, error_code=None)
        cached = self._load_cached_result("ocr", self.ocr_backend.name, media_sha)
        if cached is not None:
            summary.succeeded += 1
            return cached
        try:
            result = self.ocr_backend.extract(path)
            summary.succeeded += 1
            payload = self._ocr_result(status="ok", text=result.text, backend=self.ocr_backend.name, error_code=None)
            return self._store_cached_result("ocr", self.ocr_backend.name, media_sha, payload)
        except Exception as exc:
            summary.failed += 1
            errors.append(asdict(ProcessingError(stage="ocr", code="ocr_failed", message=str(exc))))
            return self._ocr_result(status="failed", text="[OCR failed]", backend=self.ocr_backend.name, error_code="ocr_failed")

    def _transcription_result(self, *, status, text, backend, error_code, language=None, confidence=None):
        return {
            "status": status,
            "text": text,
            "language": language,
            "confidence": confidence,
            "backend": backend,
            "error_code": error_code,
        }

    def _vision_result(self, *, status, description, backend, error_code, category=None):
        return {
            "status": status,
            "description": description,
            "category": category,
            "backend": backend,
            "error_code": error_code,
        }

    def _ocr_result(self, *, status, text, backend, error_code):
        return {
            "status": status,
            "text": text,
            "backend": backend,
            "error_code": error_code,
        }
