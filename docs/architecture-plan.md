# Telegram Conversation Export & Analysis Pipeline Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a system that converts Telegram conversations into AI-ready text-first exports, starting with Telegram Desktop export ingestion and later extending to an automated user-authorized Telegram client collector.

**Architecture:** Separate the system into four layers: ingestion, normalization/enrichment, export, and orchestration. Ship v1 using Telegram export JSON + media as the input source, while designing stable internal schemas and interfaces so v2 can swap in an automated Telegram client collector without rewriting downstream processing.

**Tech Stack:** Python, Telegram Desktop export JSON, Whisper-class STT or pluggable transcription backend, pluggable vision/OCR backend, SQLite for job/checkpoint metadata, filesystem-based export artifacts, optional Telegram bot control plane for future automation.

---

## 1. Product Summary

This system turns a Telegram conversation into a structured text corpus suitable for downstream AI analysis.

### Core product requirements
- Accept a Telegram conversation as input.
- Support text, voice messages, images/screenshots, and replies.
- Convert all voice content into text.
- Convert all visual content into text descriptions.
- Preserve conversation ordering and reply relationships.
- Replace real participant identities with neutral labels such as `Participant 1` and `Participant 2`.
- Export one or more analysis-friendly artifacts such as JSON and Markdown.
- Support bounded exports by message ID or time range.
- Be architected so the same processing pipeline works for both:
  - manual Telegram export ingestion (v1)
  - automated Telegram client collection (v2)

### Non-goals for v1
- Direct arbitrary private-chat access through the Telegram Bot API.
- Perfect emotion recognition.
- Real-time syncing.
- Group-chat generalization beyond what the data model naturally supports.

---

## 2. User Stories

### v1 user stories
1. As a user, I can upload a Telegram Desktop export JSON plus media so the system can process it.
2. As a user, I can choose a start and end boundary for the exported subset.
3. As a user, I receive a machine-readable JSON artifact.
4. As a user, I receive a human-readable Markdown artifact.
5. As a user, I can see which neutral participant said each message.
6. As a user, voice messages appear as transcript text rather than opaque media references.
7. As a user, images/screenshots appear as textual descriptions, optionally with OCR text.
8. As a user, reply-to relationships are preserved.

### v2 user stories
1. As a user, I can authorize my own Telegram account for collection.
2. As a user, I can select a specific private chat.
3. As a user, I can export by message range, date range, or checkpoint-to-now.
4. As a user, I can schedule recurring exports.
5. As a user, I can receive the generated files automatically.

---

## 3. High-Level System Architecture

## 3.1 Layers

### Layer A — Ingestion
Purpose: load raw Telegram data into a source-neutral internal representation.

Supported sources:
- `telegram_export` (v1)
- `telegram_client_session` (v2)
- optional `forwarded_batch` later if desired

Responsibilities:
- parse raw messages
- download/resolve media file paths
- capture sender IDs/names before anonymization
- preserve Telegram-native message IDs and reply references
- normalize timestamps

### Layer B — Normalization & Enrichment
Purpose: convert raw messages into analysis-ready structured records.

Responsibilities:
- map senders to neutral participant labels
- convert rich Telegram message formats into normalized text blocks
- transcribe voice/audio to text
- describe images/screenshots in text
- optionally OCR screenshots/doc images
- optionally infer tone/emotion metadata from audio/transcript
- attach reply previews/context

### Layer C — Export
Purpose: render normalized records into portable artifacts.

Outputs:
- canonical JSON export
- human-readable Markdown transcript
- optional plain text / NDJSON later

### Layer D — Orchestration
Purpose: manage runs, ranges, checkpoints, and delivery.

Responsibilities:
- accept run configuration
- define range boundaries
- deduplicate work
- track checkpoints and processed message IDs
- save artifacts
- later schedule recurring runs and bot-driven commands

---

## 4. Source Strategy

## 4.1 v1 source: Telegram Desktop export
Use Telegram Desktop export JSON because it is reliable and available today.

Input expectations:
- `result.json` for full export or single-chat export JSON
- exported media files referenced by relative path

Why this is the right first milestone:
- lowest product risk
- no Telegram account automation/auth complexity
- exposes the real structure of Telegram replies/media
- allows accurate design of downstream schema before automation work

## 4.2 v2 source: user-authorized Telegram client collector
Implement later using a Telegram client library such as Telethon.

Collector capabilities should include:
- fetch by `min_id` / `max_id`
- fetch by date range
- fetch incrementally from `last_processed_message_id`
- fetch media and reply metadata
- persist session securely

Important design rule:
The downstream normalization/export layers must not care whether a message came from exported JSON or live Telegram API collection.

---

## 5. Canonical Internal Data Model

Create one canonical source-neutral message schema. All ingestion adapters must emit this shape before enrichment.

## 5.1 ConversationRunConfig

```json
{
  "source_type": "telegram_export",
  "source_ref": "/path/to/result.json",
  "chat_selector": {
    "chat_id": null,
    "chat_name": null
  },
  "range": {
    "mode": "message_id_range",
    "start_message_id": 100,
    "end_message_id": 250,
    "start_time": null,
    "end_time": null,
    "last_checkpoint_id": null
  },
  "options": {
    "anonymize": true,
    "include_reply_preview": true,
    "transcribe_voice": true,
    "describe_images": true,
    "run_ocr": true,
    "detect_audio_tone": true,
    "output_formats": ["json", "md"]
  }
}
```

## 5.2 CanonicalMessage

```json
{
  "source": {
    "source_type": "telegram_export",
    "chat_id": "telegram-chat-123",
    "message_id": 12345,
    "reply_to_message_id": 12340
  },
  "timestamp": "2026-04-15T10:22:15Z",
  "sender": {
    "source_sender_id": "user-111",
    "source_display_name": "Hidden at export stage",
    "participant_label": "Participant 2"
  },
  "content": {
    "kind": "voice",
    "text": "No problem, I’ll wait downstairs",
    "caption": null,
    "attachments": [
      {
        "type": "voice",
        "path": "chats/chat_01/voice_messages/audio_1.ogg"
      }
    ]
  },
  "enrichment": {
    "voice_transcript": "No problem, I’ll wait downstairs",
    "voice_tone": {
      "labels": ["calm", "slightly amused"],
      "confidence": 0.61,
      "notes": ["brief laugh at end"]
    },
    "image_description": null,
    "ocr_text": null,
    "reply_preview": "I’m on my way"
  },
  "raw": {
    "telegram_export_message_type": "message"
  }
}
```

## 5.3 CanonicalConversationExport

```json
{
  "metadata": {
    "source_type": "telegram_export",
    "chat_title": "Redacted",
    "participant_count": 2,
    "participants": [
      {"label": "Participant 1"},
      {"label": "Participant 2"}
    ],
    "range": {
      "mode": "message_id_range",
      "start_message_id": 100,
      "end_message_id": 250
    },
    "generated_at": "2026-04-15T10:30:00Z"
  },
  "messages": []
}
```

---

## 6. Message-Type Handling Rules

## 6.1 Text messages
- Preserve plain text.
- Flatten Telegram rich-text fragments into readable text.
- Preserve URLs unless anonymization policy says otherwise.

## 6.2 Voice messages
- Treat transcript as the primary export text.
- Preserve a note that the original message type was `voice`.
- Store transcript confidence if available.
- If transcription fails, emit a placeholder plus error metadata.

Example:
`[Voice message transcript unavailable: decoding failed]`

## 6.3 Images and screenshots
- Produce concise but useful descriptions.
- Distinguish likely categories where possible:
  - photo
  - screenshot
  - meme
  - document image
- If OCR is enabled and text is visible, include OCR text as structured metadata.
- The main transcript text should remain compact; detailed OCR can stay in nested fields.

Example main text:
`[Image: Screenshot of a WhatsApp conversation showing a missed call and two short text messages.]`

## 6.4 Reply messages
- Always preserve `reply_to_message_id` if known.
- Optionally add a short `reply_preview`.
- Do not inline the full parent message unless requested.

## 6.5 Unsupported or edge media
For stickers, GIFs, files, or unsupported media:
- preserve a textual placeholder
- include filename/path metadata

Example:
`[Sticker sent]`
`[File attachment: invoice.pdf]`

---

## 7. Anonymization Rules

### Default policy
- Replace human-readable sender names with stable labels:
  - `Participant 1`
  - `Participant 2`
- Keep an internal reversible mapping only if the product explicitly needs it.
- Default export artifacts should not reveal original names.

### Deterministic mapping
Use stable order to assign labels, for example:
1. sort by earliest appearance in the selected range
2. assign labels in encounter order

This ensures reruns over the same range produce consistent participant labels.

### Optional future anonymization
Consider later:
- redact phone numbers
- redact usernames
- redact emails
- redact URLs
- redact named entities

Do not over-build this in v1; sender anonymization is the must-have.

---

## 8. Range Selection Model

The system must support the same range API regardless of input source.

### Range modes
1. `full_chat`
2. `message_id_range`
3. `time_range`
4. `checkpoint_to_now`

### Behavior
- `message_id_range`: include messages where `start_message_id <= id <= end_message_id`
- `time_range`: include messages whose timestamps fall inside the window
- `checkpoint_to_now`: include messages strictly after the saved checkpoint

### Reply-context nuance
If a selected message replies to a parent outside the range:
- keep the reply reference if known
- optionally inject a minimal parent preview if retrievable
- never silently drop the fact that it is a reply

---

## 9. Audio Processing Design

## 9.1 v1 baseline
Implement pluggable transcription with at least one low-cost backend.

Recommended baseline:
- local/open-source Whisper-class model or cheap API backend
- backend chosen by config

Output fields:
- transcript text
- confidence if backend supports it
- detected language if available
- processing error if any

## 9.2 Tone / sentiment / nonverbal cues
Treat this as optional enrichment, not ground truth.

Suggested v1 labels:
- calm
- neutral
- happy
- amused
- irritated
- angry
- sad
- stressed
- uncertain

Suggested nonverbal events:
- laugh
- sigh
- raised voice
- whisper/quiet speech
- long pause

Important requirements:
- store confidence scores
- keep output optional and explicitly probabilistic
- allow disabling the feature

Recommended v1 strategy:
- start with heuristic or model-assisted lightweight classification
- do not block core export if tone detection fails

---

## 10. Vision / OCR Processing Design

## 10.1 v1 baseline
Use a pluggable image analysis backend.

For each image:
- generate concise textual description
- optionally run OCR
- merge both into structured enrichment fields

### Description style guidelines
- objective first
- brief and useful
- note uncertainty explicitly
- avoid over-interpretation

Bad:
`She looks guilty and is probably lying.`

Good:
`Photo of a woman sitting in a car, looking down at a phone. Exact emotional state unclear.`

## 10.2 Screenshot handling
Special-case screenshots because they often matter more for AI analysis than photos.

If screenshot-like:
- prioritize OCR text extraction
- describe application/UI context if identifiable
- summarize visible conversation/action

---

## 11. Output Formats

## 11.1 Canonical JSON
Primary artifact for downstream AI systems.

Requirements:
- stable schema
- preserve structured metadata
- easy machine parsing
- include configuration + provenance

## 11.2 Markdown transcript
Human review artifact.

Recommended structure:
- metadata header
- participant legend
- chronological transcript
- clear markers for media-derived content

Example:

```md
# Conversation Export

- Source: Telegram export
- Range: messages 100-250
- Participants: Participant 1, Participant 2

## Transcript

[2026-04-15 10:22:01] Participant 1:
I’m on my way.

[2026-04-15 10:22:15] Participant 2 (reply to #12340):
[Voice transcript] No problem, I’ll wait downstairs.
Tone: calm, slightly amused.

[2026-04-15 10:23:02] Participant 1:
[Image] Screenshot of a map with a pinned location near a train station.
OCR: Central Station, Exit B.
```

## 11.3 Optional future formats
- plain text
- NDJSON
- XML for structured ingestion
- LLM-optimized compact transcript format

---

## 12. Persistence & Metadata

Use lightweight persistence for orchestration state.

Recommended v1 metadata store:
- SQLite database for jobs/runs/checkpoints
- filesystem for artifacts and cached media derivatives

### Suggested tables
- `sources`
- `runs`
- `checkpoints`
- `artifacts`
- `processing_errors`

Example `checkpoints` fields:
- `source_key`
- `chat_key`
- `last_message_id`
- `last_timestamp`
- `updated_at`

This becomes critical in v2.

---

## 13. Proposed Repository/Module Layout

This layout assumes a standalone project, not modifications inside Hermes core.

```text
telegram-conversation-exporter/
├── app/
│   ├── config.py
│   ├── models.py
│   ├── pipeline.py
│   ├── db.py
│   ├── range_selectors.py
│   ├── anonymization.py
│   ├── exporters/
│   │   ├── json_exporter.py
│   │   └── markdown_exporter.py
│   ├── ingestion/
│   │   ├── telegram_export.py
│   │   └── telegram_client.py        # v2
│   ├── enrich/
│   │   ├── transcription.py
│   │   ├── tone.py
│   │   ├── vision.py
│   │   └── ocr.py
│   └── cli.py
├── tests/
│   ├── test_telegram_export_ingestion.py
│   ├── test_range_selectors.py
│   ├── test_anonymization.py
│   ├── test_transcript_export_json.py
│   ├── test_transcript_export_markdown.py
│   ├── test_reply_preservation.py
│   ├── test_voice_transcription_pipeline.py
│   └── fixtures/
│       └── telegram_export_samples/
├── docs/
│   ├── architecture.md
│   └── schemas/
│       └── canonical-export.schema.json
├── .env.example
├── README.md
└── main.py
```

---

## 14. Implementation Phases

## Phase 0 — Design freeze and fixture collection

**Objective:** lock the schema and collect representative export fixtures.

Deliverables:
- canonical message schema
- canonical export schema
- 2–3 sample Telegram export fixtures
- explicit handling rules for text/voice/image/reply edge cases

Validation:
- sample exports can be parsed into a draft internal structure

## Phase 1 — Telegram export ingestion MVP

**Objective:** parse Telegram export JSON and emit canonical messages.

Tasks:
1. Parse single-chat and full-export chat structures.
2. Resolve relative media paths.
3. Normalize timestamps.
4. Preserve message IDs and reply IDs.
5. Filter messages by range selector.
6. Emit canonical messages for text/media placeholders even before enrichment.

Acceptance criteria:
- a selected chat can be loaded from export JSON
- a bounded message subset can be selected
- message order and reply references are preserved

## Phase 2 — Anonymization and base export formats

**Objective:** produce useful analysis-ready outputs without yet requiring advanced AI enrichment.

Tasks:
1. Implement participant mapping.
2. Generate canonical JSON artifact.
3. Generate Markdown transcript artifact.
4. Add placeholders for non-text media.

Acceptance criteria:
- sender names are replaced by neutral participant labels
- JSON and Markdown output are both produced
- replies are visible in both formats

## Phase 3 — Voice transcription

**Objective:** turn voice/audio messages into text.

Tasks:
1. Add pluggable transcription backend interface.
2. Implement at least one backend.
3. Cache transcript results.
4. Add transcript text to canonical export.
5. Add failure metadata and fallback placeholders.

Acceptance criteria:
- voice messages become text in exported transcript
- failures do not break the export job

## Phase 4 — Image description and OCR

**Objective:** convert visual messages into useful text.

Tasks:
1. Add pluggable vision backend interface.
2. Implement image description.
3. Implement optional OCR pass.
4. Merge description and OCR into export fields.

Acceptance criteria:
- photos and screenshots appear as text descriptions in output
- OCR text appears when present and enabled

## Phase 5 — Tone metadata (optional but supported)

**Objective:** enrich voice messages with probabilistic tone labels.

Tasks:
1. Define label taxonomy.
2. Add tone backend interface.
3. Implement optional backend or heuristic model.
4. Store confidence and notes.

Acceptance criteria:
- tone metadata can be included or disabled by config
- export still works if tone analysis is off or fails

## Phase 6 — Run state and checkpoints

**Objective:** support repeated operation over the same chat/source.

Tasks:
1. Add SQLite metadata store.
2. Persist runs, artifacts, and checkpoints.
3. Implement `checkpoint_to_now` range mode.

Acceptance criteria:
- the system can resume from the last processed message ID
- repeated processing avoids duplicated exports when configured

## Phase 7 — v2 automated collector design and implementation

**Objective:** add Telegram client session collection while keeping the rest of the system unchanged.

Tasks:
1. Add Telethon-based collector adapter.
2. Implement account authorization flow.
3. Implement chat selection.
4. Implement message/date/checkpoint range collection.
5. Reuse the same downstream pipeline and exporters.

Acceptance criteria:
- live-collected data produces the same canonical JSON/Markdown shapes as imported export data

## Phase 8 — Control plane and scheduling

**Objective:** add user-friendly automation.

Potential interface:
- Telegram bot as control UI
- commands for connect/select/export/checkpoint
- scheduled jobs

Acceptance criteria:
- user can request exports without touching raw files
- recurring exports can be scheduled

---

## 15. Testing Strategy

## 15.1 Unit tests
Create tests for:
- Telegram export parser
- range filtering logic
- participant anonymization
- reply preservation
- JSON exporter
- Markdown exporter
- media placeholder generation
- transcription failure handling
- image description merge logic

## 15.2 Fixture tests
Maintain realistic exported chat fixtures including:
- pure text conversation
- conversation with voice notes
- conversation with image screenshots
- messages with replies
- messages with missing media file references

## 15.3 Integration tests
End-to-end tests should validate:
- export JSON in -> canonical messages -> JSON/Markdown out
- start/end range works
- participant labels stable across reruns
- reply previews remain correct
- failed enrichment does not crash the pipeline

## 15.4 Future collector tests
For v2, mock Telegram client responses rather than requiring live Telegram in CI.

---

## 16. Risks and Tradeoffs

### Risk: Telegram export schema variability
Mitigation:
- collect multiple real fixtures early
- separate raw parser from canonical model

### Risk: media references missing or filtered during export
Mitigation:
- emit explicit placeholders and processing errors
- document export requirements clearly

### Risk: voice emotion detection over-promises certainty
Mitigation:
- label as optional probabilistic metadata
- include confidence
- allow disable-by-default if necessary

### Risk: screenshot description quality varies
Mitigation:
- keep OCR and vision separate
- expose both raw OCR and concise summary

### Risk: automated account collection adds security/privacy complexity
Mitigation:
- defer to v2
- use participant-owned authorization only
- store sessions securely

---

## 17. Open Questions to Resolve Before Implementation

1. Will v1 be a CLI tool, a web upload tool, or a Telegram bot that accepts files?
2. Should v1 support exactly two participants only, or allow more while primarily optimizing for two?
3. Do you want original timestamps in local timezone, UTC, or both?
4. Should OCR text appear inline in Markdown or only in structured JSON metadata?
5. Should speaker labels remain stable across different export ranges of the same chat if checkpoints are used?
6. What privacy/redaction features beyond participant anonymization are required for the first usable release?
7. Which transcription and vision backends do you prefer for cost/privacy reasons: local open-source, hosted API, or pluggable with both?

---

## 18. Recommended Build Order

If we start immediately, build in this exact order:

1. Freeze canonical schema.
2. Collect 2–3 real Telegram export fixtures.
3. Implement Telegram export parser.
4. Implement range filtering.
5. Implement anonymization.
6. Implement JSON and Markdown exporters.
7. Verify end-to-end on fixture data.
8. Add voice transcription.
9. Add image description and OCR.
10. Add checkpoints and run metadata.
11. Only then start the automated Telegram client collector.

This order proves the product’s core value before spending time on Telegram automation.

---

## 19. Proof-of-Functionality Milestone

The first proof milestone should be:

**Input:** one exported private Telegram chat with text, at least one voice message, at least one image/screenshot, and at least one reply.

**Run:** select a bounded range from message A to message B.

**Output:**
- `conversation.json`
- `conversation.md`

Both must show:
- neutral participants
- reply relationships
- voice converted to text
- image converted to text
- complete chronological ordering

If that milestone works well, the product concept is validated.

---

## 20. Suggested Next Spec Deliverables

After this plan, the next documents to produce should be:
1. `docs/architecture.md` — component and data-flow document
2. `docs/schemas/canonical-export.schema.json` — strict JSON schema
3. `docs/mvp-spec.md` — narrowed v1 requirements only
4. `docs/v2-collector-spec.md` — Telegram client automation extension

---

## 21. Immediate Recommendation

Proceed with a v1 proof-of-functionality using Telegram export input only.

That will validate:
- schema design
- range selection
- anonymization
- reply preservation
- voice-to-text handling
- image-to-text handling
- export usability for downstream AI analysis

Once that is solid, we can add the user-authorized Telegram client collector as a clean second ingestion adapter instead of entangling automation concerns into the first version.
