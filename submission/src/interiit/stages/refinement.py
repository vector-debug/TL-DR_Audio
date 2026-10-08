"""Deterministic terminology refinement for the local demo."""

import re
from pathlib import Path

from interiit.models import TranscriptSegment


def refine_segments(
    segments: list[TranscriptSegment], glossary: dict[str, str]
) -> tuple[list[TranscriptSegment], int]:
    """Apply exact glossary replacements while preserving timestamps."""

    refined: list[TranscriptSegment] = []
    changes = 0
    for segment in segments:
        text = segment.text
        for incorrect, correct in glossary.items():
            updated = re.sub(rf"\b{re.escape(incorrect)}\b", correct, text, flags=re.IGNORECASE)
            if updated != text:
                changes += 1
                text = updated
        refined.append(
            TranscriptSegment(segment.start, segment.end, text, segment.confidence)
        )
    return refined, changes


def write_refined_transcript(segments: list[TranscriptSegment], output_dir: Path) -> Path:
    """Write the refined transcript and return its path."""

    path = output_dir / "refined_transcript.txt"
    path.write_text("\n".join(segment.text for segment in segments) + "\n", encoding="utf-8")
    return path
