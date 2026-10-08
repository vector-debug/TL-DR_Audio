# Stage 2 — Domain-Aware Transcript Refinement

Stage 2 converts Stage 1's confidence-marked transcript into a cleaner
transcript without changing what was said.

Its job is **ASR error correction**, not summarization, rewriting, or meeting
minutes generation.

## 1. Position in the complete system

```text
Meeting audio
    │
    ▼
Stage 1 — Whisper transcription
    │
    ├── raw_transcript.txt
    ├── raw_transcript.json
    ├── marked_transcript.txt  ◄── Stage 2 input
    └── low_confidence.json
    │
    ▼
Stage 2 — Domain-aware refinement
    │
    ├── refined_transcript.txt
    └── change_log.json
    │
    ▼
Stage 3 — Meeting documentation
    │
    └── minutes, decisions, and action items
```

The implementation is [stage2_refine_clean.py](../final%20codes/stage2_refine_clean.py).
It expects the Stage 1 file
`outputs/marked_transcript.txt` by default.

## 2. What enters Stage 2

### Required input

```text
outputs/marked_transcript.txt
```

This is a UTF-8 text file made by Stage 1. A low-confidence word is represented
as:

```text
[[recognised_word|probability]]
```

Example:

```text
The team will deploy the [[kubernetes|0.31]] cluster on Friday.
```

The marker is a review signal. It is not meant to appear in the final refined
transcript.

### Optional inputs

| Input | Source | Effect |
|---|---|---|
| `glossary.txt` | Next to the marked transcript, or current working directory | Supplies known domain terms for fuzzy matching |
| `--domain` | Command-line argument | Overrides automatic domain detection |
| `--context` | Command-line argument | Overrides the automatically generated meeting summary/context |
| `GROQ_API_KEY` | Environment variable or `.env` | Authenticates LLM requests |
| `LLM_PROVIDER` | Environment variable | Must currently be `groq`; defaults to `groq` |
| `LLM_MODEL` | Environment variable | Groq model name; defaults to `openai/gpt-oss-20b` |

## 3. End-to-end execution tree

```text
run_stage2(marked_path, out_dir, domain, context)
│
├── 1. Validate the input file
│   ├── Does the path exist?
│   ├── Can it be decoded as UTF-8?
│   ├── Is it non-empty?
│   └── Does it contain readable sentences?
│
├── 2. Split the transcript into context-preserving chunks
│   ├── Split on sentence-ending punctuation
│   ├── Target at most 400 words per chunk
│   └── Attach the previous 2 sentences as context
│
├── 3. Load the local glossary
│   └── glossary.txt → candidate domain terms
│
├── 4. Detect the meeting domain unless supplied by the user
│   ├── Send a transcript excerpt to the domain LLM call
│   ├── Receive domain + short summary + key terms
│   └── Continue without detection if this optional call fails
│
├── 5. Build the effective refinement context
│   ├── User domain wins over detected domain
│   ├── User context wins over detected summary
│   └── Add detected terms to the glossary when available
│
├── 6. Refine every chunk
│   ├── Retrieve up to 10 glossary terms related to flagged words
│   ├── Run an error pre-detection gate
│   ├── Keep the chunk unchanged when no likely ASR error is detected
│   ├── Call the refinement LLM only for detected errors
│   ├── Parse the JSON response
│   ├── Validate preservation constraints
│   ├── Retry up to two times if the result is invalid
│   └── Fall back to the original text with marks removed if retry fails
│
├── 7. Combine refined chunks
│   └── Join chunks with blank lines and add a final newline
│
└── 8. Write the Stage 2 outputs
    ├── refined_transcript.txt
    └── change_log.json
```

## 4. Step-by-step processing

### Step 1 — Validate the input

Stage 2 stops with a clear `Stage2Error` when:

- the marked transcript does not exist;
- the file is not valid UTF-8;
- the file is empty; or
- no readable sentences can be extracted.

This prevents an API call from being made with invalid input.

### Step 2 — Split into chunks

The transcript is split using `.`, `!`, and `?` followed by whitespace.
Chunks target `400` words (`CHUNK_WORDS = 400`).

Each chunk contains:

```text
chunk.context → up to the previous 2 sentences
chunk.text    → the sentences being refined
chunk_id      → 1-based processing order
```

The context helps the model resolve terms across a sentence boundary, but the
context is supplied for understanding only and must not be copied into the
refined text.

```text
Transcript sentences
│
├── Chunk 1: [text 1 ... text N]
├── Chunk 2: [context from end of chunk 1] + [text N+1 ...]
├── Chunk 3: [context from end of chunk 2] + [text ...]
└── ...
```

Confidence markers are ignored when counting words, so they do not consume
chunk capacity.

### Step 3 — Load and retrieve glossary terms

The loader searches in this order:

```text
marked_transcript.parent / glossary.txt
        │ if absent
        ▼
current working directory / glossary.txt
```

For each chunk, only the words inside `[[...|probability]]` markers are
queries. Every glossary term is compared to those queries with
`rapidfuzz.fuzz.ratio`. The 10 highest-scoring terms are sent to the LLM.

```text
flagged words in chunk
        │
        ▼
fuzzy-match against glossary
        │
        ▼
top 10 terms
```

If there are no confidence marks or no glossary, the chunk receives
`(none)` for glossary terms.

### Step 4 — Infer the domain and meeting context

Unless the user supplies `--domain` or `--context`, Stage 2 sends an excerpt
to the domain-detection LLM. The excerpt contains:

- the complete transcript when it has at most 1,800 words; or
- the first 600 words, middle 600 words, and final 600 words for longer input.

The intended response is JSON containing:

```json
{
  "domain": "short domain description",
  "summary": "one or two sentences",
  "key_terms": ["term 1", "term 2"]
}
```

The detected domain and summary become context for all chunk-refinement calls.
User-provided values take precedence:

```text
effective domain  = --domain  if provided, otherwise detected domain
effective context = --context if provided, otherwise detected summary
```

Domain detection is optional. If it fails, Stage 2 logs the reason and
continues with any user-provided values and the local glossary.

> **Implementation note:** `Stage_2_domain.txt` currently asks for the JSON
> field `"key terms"` (with a space), while the Python code reads
> `"key_terms"` (with an underscore). Therefore, automatic domain and summary
> detection can still work, but prompt-generated key terms are not currently
> added to the glossary unless the prompt and code contract is aligned.

### Step 5 — Construct the refinement prompt

Before correction, each chunk passes through an error pre-detection prompt.
The detector must return `has_error: false` when the text is plausible and
should not be stylistically improved. When it returns false, Stage 2 strips
only the confidence markers and sends the complete chunk unchanged to Stage 3.
This prevents the correction model from rewriting already-correct content.

Each chunk receives:

```text
domain/context
previous-sentence context
top matching glossary terms
current chunk text
retry instruction, only after a failed first attempt
```

The system prompt defines the model as an ASR correction engine. The model is
explicitly told to:

- check low-confidence words first;
- correct only likely speech-recognition errors;
- preserve names, numbers, dates, negation, and commitments;
- preserve wording, fillers, and sentence structure;
- avoid adding, removing, summarizing, or reordering content;
- remove all `[[...]]` markers; and
- return JSON only.

Expected per-chunk response:

```json
{
  "refined_text": "corrected chunk text",
  "changes": [
    {
      "original": "recognised term",
      "corrected": "domain term",
      "reason": "why this is a likely ASR error",
      "confidence": 0.94
    }
  ]
}
```

The Groq call uses:

```text
temperature = 0
response_format = JSON object
max_completion_tokens = 8192
reasoning_effort = low
```

### Step 6 — Validate each response

A response is accepted only when all of the following hold:

```text
JSON parses
│
├── response is an object
├── refined_text is a string
├── changes is a list
├── refined text contains no [[ or ]] markers
├── fewer than or equal to 10% of words changed
├── every original number remains, or is explicitly logged as changed
└── negation count is unchanged
```

The 10% threshold is calculated with a word-level sequence comparison between
the original chunk and refined chunk. Negations include:

```text
not, no, never, neither, nor, and contractions ending in n't
```

The validation is designed to prevent a refinement model from turning a
transcript correction into an unsupported rewrite.

### Step 7 — Retry or fall back

Only chunks accepted by the detection gate enter correction. Each detected
chunk receives at most three total correction attempts:

```text
First LLM attempt
    │
    ├── valid → accept refined text
    │
    └── invalid/error
          │
          ▼
     bounded retry with a stricter preservation instruction
          │
          ├── valid → accept refined text
          │
          └── invalid/error → strip marks from original chunk

The detector is fail-open to correction, not to output: if detection itself is
unavailable, the existing correction and validation path is used. If correction
cannot be validated, the original chunk is preserved. This avoids losing
transcript content while still preventing an unvalidated rewrite.
```

The fallback is deliberately conservative: it does not invent a correction.
The chunk remains usable, but its `fallback` flag and failure reason are
recorded in `change_log.json`.

### Step 8 — Normalize the change log

Accepted changes are normalized into a consistent structure. Confidence values
are converted to numbers and clamped to the range `0.0`–`1.0`. Each change is
also assigned its `chunk_id`.

```text
LLM changes
    │
    ▼
safe_changes(...)
    ├── original
    ├── corrected
    ├── reason
    ├── confidence [0.0, 1.0]
    └── chunk_id
```

### Step 9 — Combine and write outputs

Accepted refined chunks are joined with two newline characters. Stage 2 then
writes the following files to the output directory:

```text
outputs/
├── refined_transcript.txt
└── change_log.json
```

## 5. Output contracts

### `refined_transcript.txt`

Human-readable transcript after domain-aware ASR correction.

Guarantees intended by the validator:

- confidence markers are removed;
- unsupported large-scale rewriting is rejected;
- numbers are preserved unless a change is explicitly logged;
- negation count is preserved;
- chunk order is preserved.

### `change_log.json`

Machine-readable audit record:

```json
{
  "summary": {
    "total_changes": 0,
    "fallbacks": 0,
    "percent_words_changed": 0.0,
    "chunks": 1,
    "domain": "detected or supplied domain",
    "domain_source": "user",
    "context": "meeting context",
    "key_terms": []
  },
  "changes": [
    {
      "original": "old word",
      "corrected": "new word",
      "reason": "ASR correction",
      "confidence": 0.9,
      "chunk_id": 1
    }
  ],
  "chunks": [
    {
      "chunk_id": 1,
      "fallback": false,
      "percent_words_changed": 2.4,
      "failure_reason": ""
    }
  ]
}
```

Important summary fields:

| Field | Meaning |
|---|---|
| `total_changes` | Number of normalized word changes accepted |
| `fallbacks` | Number of chunks returned unchanged apart from marker removal |
| `percent_words_changed` | Overall word-level change percentage |
| `chunks` | Number of transcript chunks processed |
| `domain_source` | `user`, `auto-detected`, or `none` |
| `key_terms` | Terms returned by the domain detector, when available |

## 6. Running Stage 2

From the repository root:

```powershell
uv run python "final codes/stage2_refine_clean.py"
```

Custom input and output:

```powershell
uv run python "final codes/stage2_refine_clean.py" `
  --input "outputs/marked_transcript.txt" `
  --out "outputs" `
  --domain "software engineering" `
  --context "The team is reviewing the release plan."
```

The default input is:

```text
outputs/marked_transcript.txt
```

The default output directory is:

```text
outputs/
```

Required environment setup:

```text
GROQ_API_KEY=<secret>
LLM_PROVIDER=groq
LLM_MODEL=openai/gpt-oss-20b
```

Do not place the API key in the transcript, glossary, source code, or a
committed file.

## 7. Failure paths

```text
Input failure
    └── stop immediately with an explicit error

Missing prompt
    └── stop with Stage2Error

Missing API key/provider/dependency
    └── domain detection may be skipped
    └── chunk refinement retries, then falls back per chunk

Invalid LLM JSON
    └── retry once
    └── fallback to marked input with markers removed

Unsafe refinement
    ├── >10% word change
    ├── number removed without a logged change
    ├── negation count changed
    └── confidence markers remain
        └── retry once, then fallback
```

## 8. Stage 2 design rationale

```text
Low-confidence prioritization
    → focuses model attention where Whisper is least certain

Domain detection + glossary retrieval
    → gives the model relevant terminology without sending every term

400-word chunks + two-sentence overlap
    → keeps requests bounded while retaining local context

Strict JSON response
    → makes output parseable and auditable

Validation thresholds
    → blocks meaning-changing rewrites

Retry + conservative fallback
    → keeps the pipeline usable without silently accepting unsafe output
```

## 9. Deck-ready one-slide summary

> **Stage 2 refines, it does not rewrite.** The marked transcript is split into
> context-preserving chunks. Low-confidence words are matched against relevant
> glossary terms, and a deterministic LLM prompt corrects only likely ASR
> mistakes. Every response is checked for marker removal, limited word changes,
> number preservation, and negation preservation. Invalid responses are retried
> once and then safely fall back to the original chunk. The result is a
> human-readable refined transcript plus a machine-readable audit log.
