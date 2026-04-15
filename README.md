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
- real transcription backend wiring with Hebrew default
- transcription provider selection:
  - auto
  - local
  - groq
  - grok (alias to groq)
  - openai
- deterministic test suite with fixtures

Important note:
This version currently reuses Hermes Agent's transcription stack (`tools.transcription_tools`) for local/Groq/OpenAI transcription behavior. That means this repository is best used together with Hermes Agent installed in the environment.

## Install

Recommended for now inside a Hermes-capable environment:

```bash
pip install -e .
```

If you are not already using Hermes Agent, make sure `hermes-agent` is installed in the same Python environment as this package.

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
  --transcription-provider auto \
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

## Tests

```bash
pytest -n 0 tests/telegram_exporter -q
```

## Docs

See `docs/` for:
- v1 MVP spec
- canonical schema
- architecture plan
