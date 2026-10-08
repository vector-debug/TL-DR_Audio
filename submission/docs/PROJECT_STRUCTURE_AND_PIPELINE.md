# InterIIT Project Structure and Pipeline Flow

This document explains how the InterIIT meeting assistant is organized and how
an audio recording moves through Stage 1, Stage 2, and Stage 3 to become
transcripts, meeting minutes, decisions, action items, and machine-readable
output.

The three processing stages are documented in:

- [Stage 1 workflow](STAGE_1_WORKFLOW.md)
- [Stage 2 pipeline](STAGE_2_PIPELINE.md)
- [Short Stage 2 explanation for judges](STAGE_2_JUDGE_PIPELINE.md)
- [Stage 3 pipeline](STAGE_3_PIPELINE.md)
- [Pipeline orchestrator](../final%20codes/pipeline.py)

## 1. Repository structure

The following tree shows the parts of the repository that are relevant to the
application and its end-to-end processing flow. Generated caches, virtual
environment files, and Python bytecode are omitted.

```text
submission/
├── final codes/
│   ├── Stage_1.py                # Audio transcription and confidence analysis
│   ├── stage2_refine_clean.py    # Domain-aware ASR correction
│   ├── stage3_minutes.py         # Meeting record generation
│   ├── pipeline.py               # Stage orchestration and manifest creation
│   ├── prompts/                  # Stage 2 and Stage 3 LLM prompts
│   ├── templates/                # Jinja templates for the Markdown outputs
│   └── outputs/                  # Cached sample run used by the web app
├── src/
│   └── interiit/
│       ├── cli.py, pipeline.py   # Offline deterministic demo
│       ├── stages/, renderers/   # Offline demo stages
│       └── server.py             # FastAPI server around the real pipeline
├── web/index.html                # Upload UI served by the FastAPI server
├── data/sample_meeting.json      # Fixture for the offline demo
├── Recordings/example.mp3        # Short example recording
├── tests/, scripts/              # Tests and baseline evaluation
├── docs/                         # Stage documentation (this folder)
├── README.md                     # Setup and usage overview
└── pyproject.toml, uv.lock       # uv project and dependency definition
```

The `final codes` directory contains the current pipeline implementation. The
prompt files define the instructions supplied to the language models, and the
Jinja templates define how the verified Stage 3 record is rendered into
human-readable Markdown. The `web` and `src` directories are consumers or
integration points; the pipeline itself is coordinated by
`final codes/pipeline.py`.

## 2. Complete data-flow overview

```text
                    input
                      │
                      ▼
              Meeting audio file
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Stage 1 — Audio transcription                                        │
│ final codes/Stage_1.py                                               │
│                                                                       │
│ Faster-Whisper → timestamped segments → confidence analysis           │
└─────────────────────────────────────────────────────────────────────┘
                      │
                      ├── raw_transcript.txt
                      ├── raw_transcript.json
                      ├── marked_transcript.txt
                      └── low_confidence.json
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Stage 2 — Domain-aware transcript refinement                          │
│ final codes/stage2_refine_clean.py                                   │
│                                                                       │
│ marked transcript → chunks → glossary/LLM correction → validation      │
└─────────────────────────────────────────────────────────────────────┘
                      │
                      ├── refined_transcript.txt
                      └── change_log.json
                      │
                      ├──────────────────────────────┐
                      ▼                              │
┌─────────────────────────────────────────────────────┐│
│ Stage 3 — Meeting documentation                      ││
│ final codes/stage3_minutes.py                        ││
│                                                       ││
│ refined transcript → structured record → verification ││
│                    → Markdown and JSON rendering      ││
└─────────────────────────────────────────────────────┘│
                      │                              │
                      ├── minutes.md                 │
                      ├── key_decisions.md           │
                      ├── action_items.md            │
                      └── meeting_record.json        │
                                                     │
Stage 1 raw_transcript.json ─────────────────────────┘
(used by Stage 3 for recording duration)

All stage metadata and output paths
                      │
                      ▼
                 manifest.json
```

The stages are deliberately separated by responsibility. Stage 1 records what
the speech recognizer heard and marks uncertainty. Stage 2 may correct likely
speech-recognition errors, but it is not allowed to summarize or change the
meaning of the meeting. Stage 3 turns the refined transcript into a structured
meeting record and verifies that decisions, tasks, people, dates, and evidence
are supported by the transcript.

## 3. Orchestration in `pipeline.py`

The main entry point is `run_pipeline(audio_path, output_dir="outputs")` in
[pipeline.py](../final%20codes/pipeline.py). Its execution tree is:

```text
run_pipeline(audio_path, output_dir)
│
├── Validate audio_path
│   ├── path must point to a file
│   └── file must not be empty
│
├── Create output_dir if necessary
│
├── Run Stage 1
│   └── run_stage1(audio_path, output_dir)
│       └── returns paths and transcription metadata
│
├── Run Stage 2
│   └── run_stage2(stage1["marked_transcript"], output_dir)
│       └── returns refined transcript path and refinement statistics
│
├── Run Stage 3
│   └── run_stage3(
│           stage2["refined_path"],
│           output_dir,
│           stage1["raw_transcript_json"],
│       )
│       └── returns document paths and record statistics
│
├── Build manifest data
│
└── Write output_dir/manifest.json
```

The orchestrator passes paths rather than copying the contents of each file.
Stage 2 receives Stage 1's `marked_transcript.txt`, while Stage 3 receives the
refined transcript and Stage 1's segment JSON. The segment JSON is a parallel
input to Stage 3 because it contains the final timestamp needed to calculate
meeting duration.

If Stage 2 or Stage 3 raises a known processing error, the orchestrator wraps
it in `PipelineError`. The command-line entry point prints the error to
standard error and returns exit code `1`; a successful run prints the output
mapping and returns exit code `0`.

## 4. Stage 1: audio to confidence-marked transcript

```text
Audio file
│
├── Validate existence and non-zero size
├── Prepare CUDA DLL paths on Windows
├── Load Faster-Whisper large-v3-turbo
│   ├── CUDA + float16
│   ├── CUDA + int8_float16
│   └── CPU + int8 fallback
├── Transcribe as English
│   ├── word timestamps enabled
│   ├── voice-activity filtering enabled
│   └── deterministic decoding settings
├── Normalize segments and words
├── Find words with probability < 0.50
├── Add confidence markers to uncertain words
└── Write four Stage 1 artifacts
    ├── raw_transcript.txt
    ├── raw_transcript.json
    ├── marked_transcript.txt
    └── low_confidence.json
```

Stage 1 is the only stage that reads the audio. Faster-Whisper produces
segments, word timestamps, and word probabilities. The raw text keeps one
segment per line, while the JSON file preserves the timing and confidence
metadata. A word below the strict `0.50` probability threshold is represented
in the marked transcript as `[[word|probability]]`.

For example, a segment such as:

```text
The new [[technlogy|0.33]] will reduce costs.
```

gives Stage 2 an explicit signal about where recognition is uncertain. Stage 1
does not correct the term and does not infer meeting decisions or tasks. Its
role is to preserve the recognizer's result and provide evidence for later
processing.

## 5. Stage 1 to Stage 2 handoff

```text
raw_transcript.txt       ── human-readable raw transcript
raw_transcript.json      ── timings, segments, and word probabilities
marked_transcript.txt    ── primary Stage 2 input
low_confidence.json      ── structured list of uncertain words
                                      │
                                      ▼
                         Stage 2 reads marked_transcript.txt
```

The operational handoff is `marked_transcript.txt`. It contains the transcript
in readable form while preserving low-confidence locations inline. Stage 2
also looks for an optional `glossary.txt` next to the input or in the current
working directory. The glossary helps match uncertain words to domain terms.
`raw_transcript.json` and `low_confidence.json` remain useful audit artifacts,
but `pipeline.py` passes the marked transcript directly to Stage 2.

## 6. Stage 2: domain-aware transcript refinement

```text
marked_transcript.txt
│
├── Validate UTF-8, non-empty content, and readable sentences
├── Split into sentence-preserving chunks
│   ├── target: at most 400 words
│   └── include up to two previous sentences as context
├── Load local glossary
├── Detect domain and meeting context when not supplied
├── Retrieve up to ten glossary matches for flagged words
├── Call the refinement LLM for each chunk
├── Parse and validate each JSON response
│   ├── remove all confidence markers
│   ├── allow at most 10% word changes
│   ├── preserve numbers or log explicit changes
│   └── preserve negation count
├── Retry invalid responses once
├── Fall back to the original chunk with markers removed
│   └── record fallback and reason in the change log
└── Write:
    ├── refined_transcript.txt
    └── change_log.json
```

Stage 2 is a constrained correction pass. It focuses model attention on
low-confidence words and supplies relevant domain context, but the original
sentence order and meaning must remain intact. The two-sentence overlap is
context only; it is not copied into the output as additional content.

Every chunk is checked after the model responds. If the response is unsafe or
invalid, Stage 2 retries once with stricter instructions. If the retry also
fails, the original chunk is retained after removing its confidence markers.
This conservative fallback keeps the transcript usable without silently
accepting an unsupported rewrite.

The two Stage 2 outputs serve different purposes:

```text
refined_transcript.txt
    └── clean transcript consumed by Stage 3

change_log.json
    └── audit trail of corrections, chunk statistics, and fallbacks
```

## 7. Stage 2 to Stage 3 handoff

```text
refined_transcript.txt ───────────────┐
                                      │
                                      ▼
                          Stage 3 documentation input

change_log.json ── audit/reference artifact

raw_transcript.json ────────────────┐
                                    ▼
                         Stage 3 duration source
```

The primary Stage 3 input is `refined_transcript.txt`. Stage 3 does not use the
confidence markers because Stage 2 has either corrected them or safely removed
them. Stage 3 also receives `raw_transcript.json` so that it can read the final
segment end timestamp and calculate the recording duration. If that optional
metadata is unavailable or malformed, duration is recorded as `Unspecified`
rather than causing the documentation stage to fail.

## 8. Stage 3: refined transcript to meeting record

```text
refined_transcript.txt
│
├── Validate existence, UTF-8 readability, and non-empty content
├── Split into complete-sentence parts
│   └── target: approximately 2,500 words per part
├── Extract a structured record for each part
│   ├── discussion points
│   ├── decisions
│   ├── action items
│   ├── open questions
│   └── next steps
├── Retry invalid JSON/schema responses once
├── Merge multiple part records chronologically
│   ├── merge duplicate topics and tasks
│   └── let later statements override earlier ones
├── Ground the result against the complete transcript
│   ├── verify evidence quotes
│   ├── remove unsupported decisions
│   ├── remove unsupported action items
│   ├── keep only transcript-supported people/date/title
│   └── move uncertain items to open_questions
├── Add duration from raw_transcript.json
└── Render final outputs
    ├── Jinja templates → Markdown files
    └── Pydantic record → meeting_record.json
```

Stage 3 treats the refined transcript as the source of truth for the meeting's
content. A decision must be supported by an evidence quote and must represent a
settled outcome rather than a suggestion. An action item must represent work
that was assigned or committed to; missing owners and deadlines remain
`Unspecified` instead of being guessed.

For long meetings, Stage 3 extracts records from multiple transcript parts and
then merges them. The merge step is important because a later part can clarify,
replace, or cancel an earlier proposal. The final grounding pass checks the
merged result against the full transcript before anything is rendered.

## 9. Final output tree

For an output directory such as `outputs/meeting-001`, the completed run
produces:

```text
outputs/meeting-001/
├── raw_transcript.txt
├── raw_transcript.json
├── marked_transcript.txt
├── low_confidence.json
├── refined_transcript.txt
├── change_log.json
├── minutes.md
├── key_decisions.md
├── action_items.md
├── meeting_record.json
└── manifest.json
```

The artifacts divide into three categories:

```text
Transcript artifacts
├── raw_transcript.txt
├── raw_transcript.json
├── marked_transcript.txt
└── refined_transcript.txt

Audit and metadata artifacts
├── low_confidence.json
├── change_log.json
└── manifest.json

Meeting documentation artifacts
├── minutes.md
├── key_decisions.md
├── action_items.md
└── meeting_record.json
```

`manifest.json` is the top-level output index. It records the input audio path,
the paths to all generated files, and summary statistics such as Stage 1
segment count, Stage 2 chunk/change/fallback counts, and Stage 3 decision and
action-item counts. It is therefore the natural contract for a future
frontend or upload service.

## 10. Running the complete pipeline

From the repository root:

```powershell
uv sync
$env:GROQ_API_KEY = "<your-key>"
uv run python "final codes\pipeline.py" "Recordings\example.mp3" --out "outputs\example"
```

The command performs the stages in order:

```text
audio file
  → Stage 1 transcription
  → marked_transcript.txt
  → Stage 2 refinement
  → refined_transcript.txt
  → Stage 3 documentation
  → Markdown + JSON meeting outputs
  → manifest.json
```

The command can also be run through the importable API:

```python
from pipeline import run_pipeline

result = run_pipeline("meeting.mp3", "outputs/meeting")
```

The API returns the same output mapping that is written into the manifest,
with the manifest path added after `manifest.json` is created.

## 11. Reliability boundaries

```text
Invalid audio path or empty audio
    └── pipeline stops before Stage 1

Missing transcription dependencies or unusable model
    └── Stage 1 reports an explicit error

Invalid marked transcript
    └── Stage 2 stops before refinement calls

Invalid or unsafe Stage 2 model response
    ├── retry once
    └── conservative marker-removal fallback per chunk

Invalid Stage 3 extraction or merge response
    ├── retry once with validation feedback
    └── fail the documentation stage if still invalid

Unsupported Stage 3 evidence
    ├── remove unsupported decisions/tasks
    └── move uncertain items to open_questions where appropriate
```

These boundaries preserve the distinction between transcription, correction,
and documentation. The pipeline may continue with a conservative Stage 2
chunk fallback, but Stage 3 does not fabricate a meeting record when its
structured extraction cannot be validated.
