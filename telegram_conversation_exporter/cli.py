from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ExportConfig, RangeConfig
from .pipeline import ExportPipeline
from .telegram_export_parser import TelegramExportParser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tce")
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_chats = subparsers.add_parser("list-chats")
    list_chats.add_argument("--source", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("--source", required=True)
    export.add_argument("--chat-ref", required=True)
    export.add_argument("--output-dir", required=False, default="./out")
    export.add_argument("--range")
    export.add_argument("--start-time")
    export.add_argument("--end-time")
    export.add_argument("--full-chat", action="store_true")
    export.add_argument("--transcribe-voice", action="store_true")
    export.add_argument("--transcription-provider", choices=["groq", "grok", "auto", "stub"], default="groq")
    export.add_argument("--transcription-model", default="whisper-large-v3-turbo")
    export.add_argument("--transcription-language", default="ru")
    export.add_argument("--stub-transcription", action="store_true")
    export.add_argument("--describe-images", action="store_true")
    export.add_argument("--vision-provider", choices=["groq", "grok", "auto", "stub"], default="groq")
    export.add_argument("--vision-model", default="meta-llama/llama-4-scout-17b-16e-instruct")
    export.add_argument("--stub-vision", action="store_true")
    export.add_argument("--ocr", action="store_true")
    export.add_argument("--ocr-provider", choices=["google_cloud_vision", "google", "gcv", "auto", "stub"], default="google_cloud_vision")
    export.add_argument("--google-application-credentials")
    export.add_argument("--stub-ocr", action="store_true")
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("--strict", action="store_true")
    export.add_argument("--media-size-limit-mb", type=int, default=20)
    export.add_argument("--max-messages", type=int)
    export.add_argument("--max-media-items", type=int)
    return parser


def _build_range(args) -> RangeConfig:
    if args.range and (args.start_time or args.end_time or args.full_chat):
        raise ValueError("--range cannot be combined with --start-time/--end-time/--full-chat")
    if (args.start_time or args.end_time) and args.full_chat:
        raise ValueError("--full-chat cannot be combined with --start-time/--end-time")
    if args.range:
        if ":" not in args.range:
            raise ValueError("--range must use start:end notation")
        start, end = args.range.split(":", 1)
        return RangeConfig(mode="message_id_range", start_message_id=start or None, end_message_id=end or None)
    if args.start_time or args.end_time:
        return RangeConfig(mode="time_range", start_time_utc=args.start_time, end_time_utc=args.end_time)
    return RangeConfig(mode="full_chat")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list-chats":
        parser = TelegramExportParser(Path(args.source))
        print(json.dumps(parser.list_chats(), indent=2, sort_keys=True))
        return 0
    if args.command != "export":
        raise SystemExit(2)
    config = ExportConfig(
        source=Path(args.source),
        chat_ref=args.chat_ref,
        output_dir=Path(args.output_dir),
        range=_build_range(args),
        transcribe_voice=args.transcribe_voice,
        transcription_provider=args.transcription_provider,
        transcription_model=args.transcription_model,
        transcription_language=args.transcription_language,
        stub_transcription=args.stub_transcription,
        describe_images=args.describe_images,
        vision_provider=args.vision_provider,
        vision_model=args.vision_model,
        stub_vision=args.stub_vision,
        ocr=args.ocr,
        ocr_provider=args.ocr_provider,
        google_application_credentials=args.google_application_credentials,
        stub_ocr=args.stub_ocr,
        dry_run=args.dry_run,
        strict=args.strict,
        media_size_limit_mb=args.media_size_limit_mb,
        max_messages=args.max_messages,
        max_media_items=args.max_media_items,
    )
    pipeline = ExportPipeline(config)
    if config.dry_run:
        print(json.dumps(pipeline.dry_run_summary(), indent=2, sort_keys=True))
        return 0
    result = pipeline.run()
    print(json.dumps({"conversation_json": str(result["json_path"]), "conversation_md": str(result["markdown_path"])}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
