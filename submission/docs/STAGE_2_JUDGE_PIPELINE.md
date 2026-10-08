# Stage 2 — Reliable Transcript Refinement

## One-line idea

Stage 2 improves the transcript **only when a likely speech-recognition error
is detected**. If the text looks correct, we preserve it as-is.

## Simple pipeline

```text
Stage 1 transcript
        |
        v
Find uncertain words
        |
        v
Is there a likely ASR error?
       / \
     No   Yes
     |     |
     |     v
     |   Correct using
     |   context + glossary
     |     |
     +-----+
        |
        v
Validate the correction
        |
   +----+----+
   |         |
 Valid    Invalid
   |         |
   |      Keep original
   |      transcript text
   +----+----+
        |
        v
Clean transcript for Stage 3
```

## What Stage 2 does

1. **Receives the transcript** from Stage 1, including confidence markers for
   words that may have been misheard.
2. **Detects possible errors** before asking an LLM to make changes.
3. **Corrects only likely ASR mistakes**, using the surrounding sentence and
   relevant domain terms.
4. **Checks the result** to ensure that meaning, numbers, negations, and
   commitments are preserved.
5. **Rejects unsafe output** and keeps the original wording when the correction
   cannot be validated.
6. **Sends the complete, safer transcript** to Stage 3 for meeting minutes,
   decisions, and action items.

## Why this prevents hallucination

The model is not allowed to freely rewrite the meeting. It must pass three
barriers:

```text
Detect an error first
        +
Change only what is justified
        +
Accept only validated output
```

When the model is uncertain, the system chooses the original transcript rather
than inventing information.

## Judge-facing takeaway

> “Stage 2 follows a correction-not-generation approach. It first checks
> whether an error is actually present, then makes a minimal correction, and
> finally validates that no meaning or important detail was changed. If the
> correction is unsafe, we keep the original transcript. This makes the output
> more reliable without reducing the detail of the final meeting minutes.”

