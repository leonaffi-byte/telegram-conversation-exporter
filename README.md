# Telegram Conversation Exporter

A standalone CLI-first exporter for turning Telegram Desktop chat exports into AI-ready artifacts.

Preferred input for v1 is a single ZIP file containing the Telegram export JSON plus its media folder, so upload is one file instead of many.

Current features:
- Telegram Desktop JSON ingestion from either a raw JSON file or a ZIP containing `result.json` and media
- standalone Telegram bot that accepts export ZIP uploads and returns processed outputs
- single-chat and full-export chat selection
- range filtering by message ID or time
- participant anonymization (`Participant 1`, `Participant 2`, ...)
- reply preservation
- JSON export validated against a strict schema
- Markdown transcript export
- media validation
- Groq transcription for Telegram voice messages
- Groq vision for image description
- Google Cloud Vision OCR for screenshot/document text extraction
- default transcription stack tuned for Russian:
  - provider: Groq
  - model: `whisper-large-v3-turbo`
  - language hint: `ru`
- default image description stack:
  - provider: Groq
  - model: `meta-llama/llama-4-scout-17b-16e-instruct`
- default OCR stack:
  - provider: Google Cloud Vision OCR
- deterministic test suite with fixtures

Notes:
- This repository is now standalone. It does not depend on Hermes Agent internals.
- Real transcription and image description require `GROQ_API_KEY` in the environment.
- Real OCR requires Google Cloud Vision credentials via `GOOGLE_APPLICATION_CREDENTIALS` or `--google-application-credentials`.

## Install

```bash
pip install -e .
```

## Environment

```bash
export GROQ_API_KEY=your_groq_api_key
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/google-vision-service-account.json
```

## CLI

List chats in a Telegram Desktop full export:

```bash
tce list-chats --source /path/to/telegram-export.zip
```

Export a range:

```bash
tce export \
  --source /path/to/telegram-export.zip \
  --chat-ref chat_01 \
  --range 100:250 \
  --output-dir ./out \
  --transcribe-voice \
  --transcription-provider groq \
  --transcription-model whisper-large-v3-turbo \
  --transcription-language ru \
  --describe-images \
  --vision-provider groq \
  --vision-model meta-llama/llama-4-scout-17b-16e-instruct \
  --ocr \
  --ocr-provider google_cloud_vision \
  --google-application-credentials /path/to/google-vision-service-account.json
```

Dry run:

```bash
tce export \
  --source /path/to/result.json \
  --chat-ref chat_01 \
  --dry-run
```

Test-only stub transcription:

```bash
tce export \
  --source /path/to/result.json \
  --chat-ref chat_01 \
  --transcribe-voice \
  --transcription-provider stub
```

## Telegram bot

The repo now also includes a Telegram bot wrapper around the exporter engine.

Basic setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, GROQ_API_KEY, GOOGLE_APPLICATION_CREDENTIALS
set -a
source .env
set +a
```

Run the bot:

```bash
tce-bot
```

Bot flow:
- send a Telegram Desktop export ZIP
- if the ZIP is small enough for Telegram's bot download limit, the bot processes it normally
- if the ZIP is too large, the bot sends a one-time web upload link
- the upload link expires automatically and becomes inactive after one successful upload
- uploaded ZIPs are processed the same way as direct Telegram uploads
- uploaded source files are cleaned up after processing, and stale pending uploads are pruned automatically
- if the ZIP contains one chat, the bot processes it immediately
- if the ZIP contains multiple chats, the bot asks which chat to process
- it sends back:
  - conversation.json
  - conversation.md
  - one ZIP containing both outputs

Upload fallback environment variables:
- `TCE_UPLOAD_BASE_URL` — public base URL for one-time uploads (default: `https://amznl.cc/tce-upload`)
- `TCE_UPLOAD_HOST` / `TCE_UPLOAD_PORT` — local bind address for the embedded upload server
- `TCE_UPLOAD_TOKEN_TTL_SECONDS` — how long a one-time link remains valid before upload
- `TCE_PENDING_UPLOAD_TTL_SECONDS` — how long multi-chat pending ZIPs are retained waiting for `/pick`
- `TCE_CLEANUP_INTERVAL_SECONDS` — periodic cleanup sweep interval
- `TCE_UPLOAD_MAX_SIZE_MB` — maximum allowed size for web-uploaded ZIPs

A sample systemd unit is included at:
- `deploy/tce-telegram-bot.service`

## Tests

```bash
pytest -n 0 tests -q
```

## Docs

See `docs/` for:
- v1 MVP spec
- canonical schema
- architecture plan
