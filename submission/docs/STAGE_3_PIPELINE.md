# Stage 3 — Meeting Record Generation

Stage 3 converts the refined transcript into a grounded, structured meeting
record. It produces detailed minutes, decisions, action items, unresolved
questions, next steps, and a machine-readable JSON record.

Its job is to document what the meeting said. It must not invent owners,
deadlines, decisions, dates, names, or facts that are absent from the
transcript.

The implementation is
[stage3_minutes.py](../final%20codes/stage3_minutes.py).

## 1. Position in the complete system

```text
Meeting audio
    │
    ▼
Stage 1 — Faster-Whisper transcription
    │
    └── raw_transcript.json
             │
             └── duration source for Stage 3
    │
    ▼
Stage 2 — Domain-aware transcript refinement
    │
    └── refined_transcript.txt  ◄── Stage 3 input
    │
    ▼
Stage 3 — Meeting documentation
    │
    ├── minutes.md
    ├── key_decisions.md
    ├── action_items.md
    └── meeting_record.json
```

In the complete pipeline, [pipeline.py](../final%20codes/pipeline.py) passes the
Stage 2 refined transcript and Stage 1 segment JSON into Stage 3.

## 2. Stage 3 input and outputs

### Required input

```text
outputs/refined_transcript.txt
```

This is the transcript after Stage 2 terminology refinement. Stage 3 reads it
as UTF-8 text, trims surrounding whitespace, and rejects it if it is missing
or empty.

### Optional input

```text
outputs/raw_transcript.json
```

This file supplies the recording duration. If it is missing, malformed, or
does not contain a usable final segment timestamp, duration becomes
`Unspecified`.

### Generated outputs

```text
outputs/
├── minutes.md
├── key_decisions.md
├── action_items.md
└── meeting_record.json
```

The Markdown files are human-readable. The JSON file is the canonical
machine-readable record used for downstream systems or UI rendering.

## 3. End-to-end execution tree

```text
run_stage3(refined_path, out_dir, segments_path)
│
├── 1. Validate refined_transcript.txt
│   ├── File exists?
│   ├── UTF-8 readable?
│   └── Contains non-empty text?
│
├── 2. Split transcript into parts
│   └── Target up to 2,500 words per part
│
├── 3. Extract a structured record from every part
│   ├── Apply Stage_3_system.txt
│   ├── Apply Stage_3_extract.txt
│   ├── Call the documentation LLM
│   ├── Parse and validate JSON with Pydantic
│   └── Retry once if the response is invalid
│
├── 4. Combine records when there are multiple parts
│   ├── Preserve chronological part order
│   ├── Merge duplicate topics, decisions, and tasks
│   ├── Let later statements override earlier statements
│   └── Validate the merged record
│
├── 5. Ground the record against the full transcript
│   ├── Verify evidence quotes occur in the transcript
│   ├── Reject unsupported decisions
│   ├── Reject unsupported action owners/deadlines
│   ├── Keep only transcript-supported attendees/title/date
│   └── Add unconfirmed items to open_questions
│
├── 6. Add recording duration
│   └── Read the final segment end time from raw_transcript.json
│
└── 7. Render final files
    ├── Jinja templates → Markdown
    └── Pydantic record → JSON
```

## 4. Step-by-step processing

### Step 1 — Validate the refined transcript

Stage 3 raises an explicit `Stage3Error` when:

- `refined_transcript.txt` does not exist;
- it cannot be read as UTF-8; or
- it contains no text after trimming whitespace.

No documentation model call is made for an empty transcript.

### Step 2 — Split the transcript into model-sized parts

The transcript is split into sentences using the Stage 2 sentence splitter.
Sentences are accumulated until adding another sentence would exceed:

```text
CHUNK_WORDS = 2500
```

Unlike Stage 2, Stage 3 does not add overlapping context between parts.
Each part contains complete sentences in their original order.

```text
Refined transcript
    │
    ▼
sentence splitting
    │
    ├── Part 1: up to approximately 2,500 words
    ├── Part 2: next approximately 2,500 words
    ├── Part 3: next approximately 2,500 words
    └── ...
```

The actual boundary is sentence-based, so a part can be slightly above the
target when one sentence itself is large.

### Step 3 — Extract a structured record per part

For every part, Stage 3 fills the extraction prompt with:

```text
part number
total number of parts
part transcript
```

The extraction model must return this record shape:

```json
{
  "meeting_info": {
    "title": "",
    "date": "",
    "duration": "Unspecified",
    "attendees": [],
    "purpose": ""
  },
  "executive_summary": "",
  "discussion_points": [],
  "decisions": [],
  "action_items": [],
  "open_questions": [],
  "next_steps": []
}
```

The system prompt requires the model to capture substantive discussion, not
only decisions. A topic can contain:

```text
topic
├── points
├── context
├── people_involved
├── proposals
├── questions_concerns
├── motions
├── outcome
└── decision
```

### Step 4 — Classify decisions conservatively

A statement is treated as a decision only when the transcript indicates that
the group settled on it, for example:

```text
we decided
we will
we'll go with
agreed
approved
final
```

The following remain proposals, discussion points, or open questions:

```text
we should
we could
maybe
might
I suggest
what if
let's consider
```

The model is also instructed that the latest statement wins. If a later
statement changes an earlier option, the final outcome is recorded and the
earlier option is retained only as context when the transcript says it was
replaced.

Every accepted decision must include an exact evidence quote from the
transcript.

### Step 5 — Extract action items without guessing

An action item must be work that someone was asked or committed to do.

```text
Named assignment
    ├── task = stated work
    ├── owner = exact name from transcript
    └── deadline = exact words from transcript

Agreed work with no named owner
    ├── task = stated work
    ├── owner = Unspecified
    └── deadline = Unspecified unless stated

General suggestion with no assignment
    └── not an action item
```

Examples of information preservation rules:

- `"by Friday"` remains `"by Friday"`; it is not converted to a calendar date.
- `"I'll do it"` has owner `Unspecified` unless the speaker's name is stated.
- An unstated deadline remains `Unspecified`.
- An exact evidence quote must support the task and include the owner or
  deadline words when those values are present.

### Step 6 — Retry invalid model responses

The LLM call uses:

```text
model = DOC_LLM_MODEL or openai/gpt-oss-120b
temperature = 0
response_format = JSON object
max_completion_tokens = 8192
```

The response is parsed with Pydantic using the `MeetingRecord` schema.

```text
LLM response
    │
    ├── valid JSON + valid schema → accept
    │
    └── invalid JSON/schema
          │
          ▼
       retry with the validation error
          │
          ├── valid → accept
          └── invalid again → Stage3Error
```

Stage 3 allows two attempts for each extraction and for the final merge.
There is no silent success-shaped fallback for a failed documentation call.

## 5. Multi-part merge flow

If the transcript fits in one part, that part's record is used directly.
If it has multiple parts, the records are serialized in chronological order
and sent to a separate merge prompt.

```text
Part 1 record ─┐
Part 2 record ─┼──► merge LLM ───► final MeetingRecord
Part 3 record ─┘
```

The merge prompt instructs the model to:

- combine records from one meeting;
- merge duplicate topics, decisions, and tasks;
- preserve the order in which topics were first raised;
- preserve chronology inside each topic;
- let later statements override earlier statements;
- remove questions answered later;
- preserve evidence quotes exactly;
- carry meeting information stated in any part; and
- produce a 3–5 sentence meeting-wide executive summary.

The merge stage is important because a decision or task can be introduced in
one part and clarified, changed, or cancelled in a later part.

## 6. Grounding and safety verification

After extraction or merge, `verify(...)` checks the final record against the
complete refined transcript.

### Evidence matching

Evidence is normalized with RapidFuzz processing and accepted when its partial
match score reaches:

```text
MATCH_THRESHOLD = 90
```

Unsupported decision evidence is removed from `decisions`. Unsupported action
evidence is removed from `action_items`.

### Decision verification

```text
For each decision:
    ├── evidence found in transcript?
    │   └── no → discard
    ├── evidence contains tentative language?
    │   └── yes → move to open_questions
    ├── negation status conflicts with decision text?
    │   └── yes → move to open_questions
    └── otherwise → retain as a decision
```

Tentative language includes words such as `should`, `could`, `might`, `maybe`,
`suggest`, `propose`, `consider`, and `what if`. Agreement language can prevent
a statement containing tentative wording from being rejected when the same
evidence also contains a clear agreement.

### Action-item verification

```text
For each action item:
    ├── evidence found in transcript?
    │   └── no → discard
    ├── owner appears in evidence?
    │   └── no → owner = Unspecified
    ├── deadline appears in evidence?
    │   └── no → deadline = Unspecified
    ├── no owner + tentative evidence?
    │   └── move to open_questions
    └── otherwise → retain as an action item
```

### Meeting information verification

```text
title     → retained only if supported by transcript
date      → retained only if supported by transcript
attendees → keep only names supported by transcript
duration  → always taken from raw_transcript.json or Unspecified
purpose   → model-generated from discussed content
```

Missing values use:

```text
Unspecified
```

Empty collections use:

```json
[]
```

## 7. Output contracts

### `meeting_record.json`

The complete structured record:

```json
{
  "meeting_info": {
    "title": "Unspecified",
    "date": "Unspecified",
    "duration": "00:42:18",
    "attendees": ["Name stated in transcript"],
    "purpose": "One transcript-grounded sentence"
  },
  "executive_summary": "Three to five concise sentences.",
  "discussion_points": [
    {
      "topic": "Topic name",
      "points": ["Substantive discussion"],
      "context": [],
      "people_involved": [],
      "proposals": [],
      "questions_concerns": [],
      "motions": [],
      "outcome": "",
      "decision": ""
    }
  ],
  "decisions": [
    {
      "decision": "Settled outcome",
      "context": "",
      "evidence": "Exact transcript quote"
    }
  ],
  "action_items": [
    {
      "task": "Work explicitly assigned or committed to",
      "owner": "Unspecified",
      "deadline": "Unspecified",
      "evidence": "Exact transcript quote"
    }
  ],
  "open_questions": [],
  "next_steps": []
}
```

### `minutes.md`

Human-readable meeting record containing:

```text
Meeting information
Executive summary
Detailed minutes by topic
Key decisions
Action items
Open questions / unresolved issues
Next steps
```

### `key_decisions.md`

Lists each verified decision with optional context and its evidence quote. If
no decision survives verification, it states:

```text
No decisions were reached during this meeting.
```

### `action_items.md`

Renders verified tasks in a table:

```text
| # | Task | Owner | Deadline |
|---|------|-------|----------|
```

Evidence quotes are included below the table. If there are no verified tasks,
it states:

```text
No action items were assigned during this meeting.
```

## 8. Recording duration

When Stage 3 receives `outputs/raw_transcript.json`, it reads the final
segment's `end` timestamp and converts seconds to a human-readable
`HH:MM:SS`-style duration using `timedelta`.

```text
raw_transcript.json
    │
    └── segments[-1]["end"]
            │
            ▼
        duration
```

If the segment file cannot be read or its structure is unusable, the record
uses `Unspecified` rather than failing the entire documentation stage.

## 9. Running Stage 3

From the repository root:

```powershell
uv run python "final codes/stage3_minutes.py"
```

Custom paths:

```powershell
uv run python "final codes/stage3_minutes.py" `
  --input "outputs/refined_transcript.txt" `
  --segments "outputs/raw_transcript.json" `
  --out "outputs"
```

Defaults:

```text
--input    outputs/refined_transcript.txt
--segments outputs/raw_transcript.json
--out      outputs
```

Environment:

```text
GROQ_API_KEY=<secret>
DOC_LLM_MODEL=openai/gpt-oss-120b
```

The API key must be supplied through the environment or a local `.env` file.
It must not be committed to source code or output files.

## 10. Failure paths

```text
Missing refined transcript
    └── stop with Stage3Error

Empty refined transcript
    └── stop with Stage3Error

Missing prompt file
    └── stop with Stage3Error

Missing GROQ_API_KEY
    └── stop with Stage3Error

Invalid extraction or merge JSON
    ├── retry once with schema error
    └── stop with Stage3Error if still invalid

Unsupported decision evidence
    └── remove from decisions

Unsupported action evidence
    └── remove from action_items

Unconfirmed decision or tentative unowned task
    └── move to open_questions

Unavailable duration source
    └── set duration to Unspecified
```

## 11. Stage 3 design rationale

```text
Structured Pydantic schema
    → ensures predictable fields for UI and exports

Detailed topic extraction
    → preserves reasoning, alternatives, questions, and context

Separate merge prompt
    → resolves cross-part duplicates and later corrections

Exact evidence fields
    → makes decisions and tasks auditable against the transcript

Fuzzy evidence verification
    → removes unsupported model-generated records

Explicit Unspecified values
    → prevents invented owners, dates, deadlines, and durations

Markdown + JSON rendering
    → supports both human review and machine integration
```

## 12. Deck-ready one-slide summary

> **Stage 3 turns the refined transcript into a grounded meeting record.**
> The transcript is split into sentence-safe parts, and a documentation model
> extracts topic-based discussion, decisions, action items, open questions, and
> next steps using a strict JSON schema. Long meetings use a second merge pass
> that reconciles duplicates and lets later statements override earlier ones.
> Evidence quotes are checked against the full transcript, unsupported
> decisions and tasks are removed, and missing owners or deadlines remain
> `Unspecified`. The final record is exported as readable Markdown and
> machine-readable JSON.
