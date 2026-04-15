from __future__ import annotations

import json
from pathlib import Path

from .models import ConversationExport, to_plain_data


def conversation_to_dict(conversation: ConversationExport) -> dict:
    return to_plain_data(conversation)


def write_json_export(conversation: ConversationExport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "conversation.json"
    path.write_text(json.dumps(conversation_to_dict(conversation), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def render_markdown(conversation: ConversationExport) -> str:
    lines = [f"# {conversation.metadata['chat_title_redacted']}", ""]
    for message in conversation.messages:
        timestamp = message.timestamps["utc"]
        label = message.sender["participant_label"] or "Service"
        reply = ""
        if message.relations.get("reply_status") != "unknown":
            preview = message.relations.get("reply_preview")
            if preview:
                reply = f" ↪ {preview['sender_label'] or 'Unknown'}: {preview['text']}"
            else:
                reply = f" ↪ {message.relations['reply_status']}"
        body = message.normalized.get("plain_text") or message.normalized.get("service_text") or "[No text]"
        if message.source.get("is_forwarded"):
            body = f"[Forwarded message] {body}"
        lines.append(f"- {timestamp} {label}{reply}: {body}")
        enrichment = message.enrichment or {}
        transcription = enrichment.get("transcription") or {}
        vision = enrichment.get("vision") or {}
        ocr = enrichment.get("ocr") or {}
        if transcription.get("text"):
            lines.append(f"  - Transcript: {transcription['text']}")
        if vision.get("description"):
            lines.append(f"  - Image: {vision['description']}")
        ocr_text = ocr.get("text")
        if ocr_text:
            compact = ocr_text if len(ocr_text) <= 80 else ocr_text[:77] + "..."
            lines.append(f"  - OCR: {compact}")
    lines.append("")
    return "\n".join(lines)


def write_markdown_export(conversation: ConversationExport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "conversation.md"
    path.write_text(render_markdown(conversation), encoding="utf-8")
    return path
