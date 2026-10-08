# Stage 1 — Audio to Raw Transcript

> **Purpose:** Convert one English meeting-audio file into a raw transcript
> plus word-level confidence metadata.  
> **Scope:** Stage 1 only. Transcript refinement, meeting minutes, decisions,
> and action-item extraction belong to later stages.

## 1. Stage 1 at a glance

```text
Audio file
  |
  v
Validate path and file size
  |
  v
Prepare Windows CUDA DLL paths (Windows only)
  |
  v
Import CTranslate2 + faster-whisper
  |
  v
Detect available CUDA devices
  |
  +--> CUDA available: try CUDA/float16
  |                     then CUDA/int8_float16
  |
  +--> No CUDA or CUDA attempts fail: try CPU/int8
  |
  v
Load Whisper large-v3-turbo
  |
  v
Transcribe English audio
  |    language=en
  |    temperature=0.0
  |    beam_size=5
  |    word_timestamps=True
  |    vad_filter=True
  |
  v
Collect timestamped segments and words
  |
  +--> No segments: stop with an error
  |
  v
Find words with probability < 0.50
  |
  v
Create outputs/
  |
  +--> raw_transcript.txt
  +--> raw_transcript.json
  +--> marked_transcript.txt
  +--> low_confidence.json
  |
  v
Print detected language, duration, and low-confidence count
```

## 2. Inputs

### 2.1 Command-line input

The program accepts one optional positional argument:

```text
uv run python "final codes/Stage_1.py" <audio-file>
```

If no argument is provided, it uses:

```text
Recordings/09-15-2026-Council-Meeting.mp3
```

The path is converted to a `Path` object before processing.

### 2.2 Input checks

Processing stops before model loading when either condition is true:

```text
Input path
  |
  +--> Does not point to a file
  |       |
  |       +--> ValueError: Audio file not found
  |
  +--> File size is 0 bytes
          |
          +--> ValueError: Audio file is empty
```

The script does not pre-check the extension. Unsupported or unreadable audio
is reported if the transcription backend cannot decode it.

## 3. Runtime preparation

On Windows, `configure_windows_cuda_dlls()` looks for the `bin` directories
of:

```text
nvidia.cublas
nvidia.cudnn
```

When those directories exist, they are:

1. Added to the beginning of `PATH`.
2. Registered with `os.add_dll_directory()`.

On non-Windows systems this function returns immediately.

This preparation occurs when the module is loaded, before the model is
created.

## 4. Model loading

`load_model()` imports:

```text
ctranslate2
faster_whisper.WhisperModel
```

If either dependency is unavailable, the program raises an explicit error
instructing the user to run `uv sync`.

### 4.1 Device-selection tree

```text
Can CTranslate2 be imported?
  |
  +--> No: stop — transcription dependencies are missing
  |
  +--> Yes
         |
         v
   Count visible CUDA devices
         |
         +--> One or more CUDA devices
         |      |
         |      +--> Try large-v3-turbo / CUDA / float16
         |      |       |
         |      |       +--> Success: use it
         |      |       +--> Failure: continue
         |      |
         |      +--> Try large-v3-turbo / CUDA / int8_float16
         |              |
         |              +--> Success: use it
         |              +--> Failure: continue to CPU
         |
         +--> Zero CUDA devices, or all CUDA attempts failed
                |
                +--> Try large-v3-turbo / CPU / int8
                        |
                        +--> Success: use it
                        +--> Failure: stop with all load errors
```

The selected mode is printed. Every failed device/precision combination is
retained and included in the final model-loading error if no mode works.

## 5. Transcription

The loaded Whisper model receives the input path as a string and is called
with:

| Setting | Value | Effect |
|---|---:|---|
| `language` | `"en"` | Forces English decoding |
| `temperature` | `0.0` | Uses deterministic decoding |
| `beam_size` | `5` | Uses beam search with five candidates |
| `word_timestamps` | `True` | Requests timestamps and probabilities per word |
| `vad_filter` | `True` | Filters non-speech regions |

The backend returns:

```text
segments_iterator, info
```

The iterator is consumed completely. Transcription is therefore finished only
after every segment has been converted into the Stage 1 JSON structure.

## 6. Segment and word normalization

Each decoded segment becomes:

```json
{
  "start": 0.0,
  "end": 4.2,
  "text": "Meeting starts at nine.",
  "avg_logprob": -0.18,
  "no_speech_prob": 0.01,
  "words": [
    {
      "word": " Meeting",
      "start": 0.0,
      "end": 0.7,
      "prob": 0.997
    }
  ]
}
```

Normalization rules:

- Segment text is trimmed with `.strip()`.
- Each word keeps its original text, including leading spacing.
- Word `start` and `end` timestamps are retained.
- Word probability is rounded to three decimal places.
- Segment-level `avg_logprob` and `no_speech_prob` are retained.
- If the backend supplies no words for a segment, `words` is an empty list.

If the completed segment list is empty, Stage 1 stops with:

```text
No speech could be decoded from the audio file.
```

## 7. Confidence analysis

The threshold is:

```python
LOW_CONFIDENCE_THRESHOLD = 0.5
```

A word is flagged when:

```text
word probability < 0.50
```

The threshold is strict: a probability of exactly `0.50` is not flagged.

For each flagged word, Stage 1 stores:

```json
{
  "segment_index": 2,
  "word": " technlogy",
  "start": 18.4,
  "end": 19.1,
  "prob": 0.327,
  "context": "the new technlogy will reduce costs"
}
```

The context contains up to five words before and five words after the flagged
word within the same segment. It is evidence for later review or transcript
refinement; Stage 1 does not correct the word.

## 8. Output generation

The output directory is:

```text
outputs/
```

It is created automatically when it does not exist.

```text
outputs/
├── raw_transcript.txt
├── raw_transcript.json
├── marked_transcript.txt
└── low_confidence.json
```

### 8.1 `raw_transcript.txt`

Plain-text transcript containing one trimmed segment per line:

```text
Welcome everyone.
The meeting starts at nine.
```

This is the primary human-readable raw transcript passed conceptually to
later stages.

### 8.2 `raw_transcript.json`

Machine-readable list of all normalized segments. It contains timing,
segment confidence metadata, and word-level timing/probability data. JSON is
written with indentation and UTF-8 encoding.

### 8.3 `marked_transcript.txt`

Human-readable transcript with low-confidence words marked using Obsidian
inline-field-style brackets:

```text
The new [[technlogy|0.33]] will reduce costs.
```

Words that meet the confidence threshold remain unchanged. If a segment has
no word data, its segment text is written without confidence markers.

### 8.4 `low_confidence.json`

Machine-readable list of only the flagged words. It is an empty JSON list
(`[]`) when no word falls below the threshold.

## 9. Completion and returned result

After all four files are written, the program prints:

```text
Detected language: <language> (<probability>)
Finished in <seconds>s. Low-confidence words: <count>
```

`run_stage1()` also returns a dictionary containing:

```text
raw_transcript        -> path to raw_transcript.txt
raw_transcript_json   -> path to raw_transcript.json
marked_transcript     -> path to marked_transcript.txt
low_confidence        -> path to low_confidence.json
segments              -> number of decoded segments
language              -> detected language reported by Whisper
language_probability  -> language probability reported by Whisper
```

Although the language is forced to English during decoding, the returned
language metadata is still preserved from the model's `info` object.

## 10. Error and cleanup paths

```text
Start
  |
  +--> Missing path / empty file
  |       +--> Print clear error to stderr
  |       +--> Exit code 1
  |
  +--> Missing dependencies / no usable model
  |       +--> Print clear error to stderr
  |       +--> Exit code 1
  |
  +--> Decoder cannot read audio / no speech
  |       +--> Wrap failure with input-file context
  |       +--> Print clear error to stderr
  |       +--> Exit code 1
  |
  +--> Successful transcription
          +--> Write all outputs
          +--> Print completion information
          +--> Exit code 0
```

The model reference is deleted in the `finally` block, followed by
`gc.collect()`, so model memory is released after the run even when
transcription fails.

## 11. Stage 1 contract with later stages

```text
Stage 1
  input:  meeting audio file
  output: raw transcript + timestamps + confidence evidence
      |
      v
Stage 2
  input: raw transcript and/or low-confidence evidence
  output: domain-refined transcript
      |
      v
Stage 3
  input: refined transcript
  output: minutes, decisions, and action items
```

Stage 1 must remain semantically raw: it records what the speech-to-text
model decoded and highlights uncertain words, but it must not invent owners,
deadlines, decisions, or corrected terminology.

## 12. Relevant implementation files

- [`final codes/Stage_1.py`](../final%20codes/Stage_1.py) — complete Stage 1
  implementation.
- [`pyproject.toml`](../pyproject.toml) — project dependencies and the
  `interiit` entry point.
