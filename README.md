# Telegram Conversation Exporter

A standalone CLI-first exporter for turning Telegram Desktop chat exports into AI-ready artifacts.

Current features:
- Telegram Desktop JSON ingestion
- single-chat and full-export chat selection
- range filtering by message ID or time
- participant anonymization (`Participant 1`, `Participant 2`, ...)
- reply preservation
- JSON export validated against a strict schema
- Markdown transcript export
- media validation
- Groq transcription for Telegram voice messages
- default transcription stack tuned for Hebrew:
  - provider: Groq
  - model: `whisper-large-v3-turbo`
  - language hint: `he`
- deterministic test suite with fixtures

Notes:
- This repository is now standalone. It does not depend on Hermes Agent internals.
- Real transcription requires `GROQ_API_KEY` in the environment.
- Image description and OCR are still stubbed for now.

## Install

```bash
pip install -e .
```

## Environment

```bash
export GROQ_API_KEY=your_groq_api_key
```

## CLI

List chats in a Telegram Desktop full export:

```bash
tce list-chats --source /path/to/result.json
```

Export a range:

```bash
tce export \
  --source /path/to/result.json \
  --chat-ref chat_01 \
  --range 100:250 \
  --output-dir ./out \
  --transcribe-voice \
  --transcription-provider groq \
  --transcription-model whisper-large-v3-turbo \
  --transcription-language he \
  --describe-images
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

## Tests

```bash
pytest -n 0 tests -q
```

## Docs

See `docs/` for:
- v1 MVP spec
- canonical schema
- architecture plan
