# Telegram Conversation Exporter — v1 MVP Spec

Status: Draft

Basis: original architecture plan plus external review from Codex, Claude Code, OpenCode/Kimi, OpenCode/GLM, and OpenCode/MiniMax.

## 1. Purpose

Build a first usable version of the system that converts a Telegram Desktop chat export into AI-ready text-first artifacts.

The v1 MVP must prove the core value proposition:
- ingest Telegram Desktop export JSON + local media
- select a specific chat and message range
- anonymize participants
- preserve reply relationships
- convert voice messages to transcript text
- convert images/screenshots to concise text descriptions
- produce machine-readable JSON and human-readable Markdown

This version is intentionally not a Telegram bot and does not automate live account collection.

## 2. Explicit v1 scope

### Included
- CLI-only interface
- Telegram Desktop JSON export ingestion
  - single-chat export JSON
  - full account export JSON containing chats
- chat selection from export
- range selection by:
  - full chat
  - message ID range
  - time range
- deterministic participant anonymization
- canonical JSON export
- Markdown transcript export
- voice transcription
- image description
- optional OCR, default off
- best-effort processing with per-message failure reporting
- dry-run mode
- filesystem cache for media-derived enrichments
- placeholders for unsupported or failed media processing
- run summary and processing stats

### Explicitly excluded from v1
- Telegram Bot API ingestion
- Telethon/live Telegram client collection
- checkpoints and recurring automation
- SQLite metadata store
- tone/emotion detection from audio
- web UI
- perfect privacy redaction of message text content
- guaranteed support for every Telegram message type

## 3. User-facing behavior

### Primary command

Example shape:

```bash
tce export \
  --source /path/to/export/result.json \
  --chat-ref chat_01 \
  --range 100:250 \
  --output-dir ./out \
  --transcribe-voice \
  --describe-images
```

### Required outputs

The command writes:
- `conversation.json`
- `conversation.md`
- optional cache/artifact metadata files under output or cache dir

### Dry-run command

Example:

```bash
tce export \
  --source /path/to/export/result.json \
  --chat-ref chat_01 \
  --dry-run
```

Dry-run prints:
- chat resolved
- message count in selected range
- participant count
- media count by type
- number of missing media files
- estimated enrichment work
- warnings about unsupported content

Dry-run performs no transcription/vision calls.

## 4. Supported input contract

v1 supports Telegram Desktop JSON exports only.

### Accepted input shapes
1. Single-chat export JSON
2. Full-account export JSON where one chat is selected from the exported chat list

### Required assumptions
- exported JSON is readable UTF-8 text
- media files are still present at the relative paths referenced by the export, if media enrichment is desired
- source export comes from Telegram Desktop, not mobile copy/export formats

### Unsupported / best-effort conditions
- HTML exports
- partially downloaded cloud-only media that is not present locally
- malformed or truncated exports
- unsupported Telegram message kinds beyond placeholder handling

## 5. v1 product decisions

These open questions are now resolved for v1:

1. Interface: CLI only
2. Participant model: support N participants generically, while optimizing examples for 2 participants
3. Timestamp policy: preserve raw source timestamp and normalized UTC timestamp
4. OCR policy: structured JSON by default; optional compact inline mention in Markdown only when short
5. Participant stability: labels must be stable at chat scope within a given source export, not per selected range
6. Privacy policy: sender anonymization is mandatory; broader PII redaction is documented as limited in v1
7. Enrichment defaults:
   - voice transcription: on when voice messages exist and flag enabled
   - image description: on when images exist and flag enabled
   - OCR: off by default
   - tone detection: not in v1

## 6. Functional requirements

## 6.1 Ingestion

The system must:
- load Telegram Desktop export JSON
- identify whether it is a single-chat or full-export structure
- enumerate available chats when needed
- resolve a target chat by a stable `chat_ref`
- normalize raw Telegram messages into an internal source-neutral representation

The parser must preserve, where available:
- source message ID
- source reply-to message ID
- sender key / sender identifier
- sender display name only in ephemeral in-memory parsing, not final exported artifact
- raw timestamp fields
- message edit timestamp
- forwarded metadata presence
- media-relative paths
- raw message type

## 6.2 Range selection

v1 must support:
- `full_chat`
- `message_id_range`
- `time_range`

Rules:
- range filtering happens before expensive enrichment
- messages remain in original chronological order
- if a message replies to something outside the selected range, the relation is preserved as out-of-range when known

v1 does not implement checkpoint-to-now.

## 6.3 Participant anonymization

Rules:
- participants are labeled `Participant 1`, `Participant 2`, ..., `Participant N`
- labels are assigned deterministically using stable sender identity within the selected source chat, not based only on the selected range
- exported JSON must not include raw display names by default
- exported JSON may include a stable anonymized participant ID derived from source identity

Privacy note:
- message body text, forwarded content, OCR text, and transcripts may still contain PII in v1
- the export metadata must explicitly indicate this limitation

## 6.4 Message-type behavior

### Supported as first-class in v1
- text
- voice
- image/photo/screenshot
- document with image file if image processing is possible
- service
- unsupported

### Text
- flatten Telegram text fragments into readable plain text
- preserve URLs in v1 unless optional redaction mode is later added

### Voice
- validate media file exists
- if valid and transcription enabled, transcribe to text
- if transcription fails, emit placeholder text plus structured processing error
- keep original media metadata

### Image/photo/screenshot
- validate media file exists
- if valid and image description enabled, generate concise non-identifying description
- if OCR enabled, extract visible text into structured field
- if description fails, emit placeholder plus processing error

### Service messages
- preserve as typed service events, not as normal participant speech
- sender label may be null

### Unsupported message kinds
- emit placeholder text and preserve raw type metadata

## 6.5 Replies

The export must preserve:
- `reply_to_message_id`
- `reply_status` where possible:
  - `in_range`
  - `out_of_range`
  - `missing`
  - `unknown`
- optional short `reply_preview` generated at export time only

Reply previews must not be cached as canonical truth because upstream message text may change between reruns.

## 6.6 Edited and forwarded messages

### Edited messages
v1 exports only the latest visible message text from the Desktop export and records:
- `is_edited`
- `edited_at`

No historical diff/edit reconstruction in v1.

### Forwarded messages
v1 must indicate that a message was forwarded while avoiding identity leakage.
Recommended default rendering:
- JSON: `is_forwarded: true`
- JSON: sanitized forwarded metadata or null
- Markdown: `[Forwarded message]`

## 6.7 Media validation

Before enrichment, the pipeline must validate referenced media files:
- file path exists
- file size > 0
- file size within configured limit
- file type is recognized enough for routing

If validation fails, the export still continues in best-effort mode.

## 7. Non-functional requirements

### Reliability
- best-effort mode is the default
- one failed message must not abort the whole export
- the final artifact must include per-message failures and run-level counts

### Performance
- avoid loading unnecessary media into memory until needed
- support streaming or incremental parsing for large export JSON where practical
- cache transcriptions and image descriptions by media content hash when possible

### Cost control
- dry-run must estimate media workload before enrichment
- configurable per-file size limits
- OCR default off

### Privacy
- descriptions for images should avoid names and identifying details where possible
- final export metadata must warn that v1 does not fully redact PII from conversation content

## 8. Output contract

The primary output is `conversation.json` validated against a strict schema.

The JSON structure must separate:
- source facts
- normalized text representation
- optional enrichments
- processing status/errors

The secondary output is `conversation.md`, derived from the canonical JSON.

Markdown rules:
- chronological transcript
- clear labels for participants and media-derived text
- compact reply markers
- do not dump massive OCR bodies inline
- if OCR text is long, summarize inline and keep full OCR in JSON only

## 9. Required metadata in final export

Top-level metadata must include:
- `schema_version`
- `pipeline_version`
- `source_type`
- `source_export_kind`
- `generated_at`
- `chat_ref`
- `chat_title_redacted`
- `range`
- `participant_count`
- `enabled_features`
- `privacy_limitations`
- `run_summary`

Run summary must include:
- total messages selected
- counts by message kind
- media files missing
- transcription successes/failures
- image description successes/failures
- OCR successes/failures
- processing duration

## 10. Strict v1 limitations to document

The tool must clearly document these limitations:
- not all Telegram Desktop export variants are guaranteed identical
- v1 supports Telegram Desktop JSON only
- raw message text/transcripts/OCR may still contain identifying information
- forwarded content can still contain latent identity clues even after sanitization
- unsupported message kinds may appear as placeholders
- live Telegram automation is not included in v1

## 11. Recommended CLI surface

### Required flags
- `--source <path>`
- `--chat-ref <id-or-selector>`
- `--output-dir <path>`

### Range flags
- `--range <start:end>`
- `--start-time <iso8601>`
- `--end-time <iso8601>`
- `--full-chat`

### Processing flags
- `--transcribe-voice`
- `--describe-images`
- `--ocr`
- `--dry-run`
- `--strict`
- `--media-size-limit-mb <int>`

### Optional safety flags
- `--max-messages <int>`
- `--max-media-items <int>`

## 12. Acceptance criteria

v1 is accepted when all of the following are true:

1. A Telegram Desktop export can be loaded successfully.
2. A user can select a target chat from that export.
3. A bounded message range can be exported.
4. Participants are anonymized deterministically.
5. Reply relationships are preserved.
6. Voice messages become transcript text when media is present and transcription succeeds.
7. Images become concise textual descriptions when media is present and vision succeeds.
8. Failures produce structured errors and placeholders without crashing the export.
9. `conversation.json` validates against the v1 schema.
10. `conversation.md` is readable and matches the selected range.
11. Dry-run reports realistic message/media workload before enrichment.

## 13. Required fixture set before implementation

Implementation must not begin until at least these fixtures exist:
- simple private chat with plain text only
- private chat with replies
- private chat with at least one edited message
- private chat with at least one forwarded message
- private chat with at least one voice message and one missing voice file case
- private chat with at least one screenshot/photo and one missing image file case
- full export containing multiple chats so chat selection can be tested

## 14. Deferred items for v1.1 / v2

### v1.1 candidates
- OCR tuning
- richer placeholder handling for more message kinds
- optional content redaction heuristics
- resumable runs and stronger caching controls

### v2
- user-authorized Telegram client collector
- checkpoint-based incremental exports
- recurring automation
- Telegram bot control plane
- tone/emotion detection if still needed after core validation

## 15. Build order

1. Collect fixtures and write field map for Telegram Desktop export
2. Freeze JSON schema
3. Implement parser and chat selection
4. Implement range filtering
5. Implement anonymization
6. Implement JSON exporter
7. Implement Markdown exporter
8. Implement media validation
9. Implement voice transcription
10. Implement image description
11. Add dry-run and run summary
12. Validate against fixture pack
