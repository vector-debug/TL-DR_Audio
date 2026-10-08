"""Offline transcription stage used by the reproducible demo."""

import json
from pathlib import Path
from typing import Any

from interiit.errors import PipelineError
from interiit.models import TranscriptSegment


def load_sample_transcript(path: Path) -> list[TranscriptSegment]:
    """Load deterministic transcript segments from a sample JSON file."""

    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        segments = payload["segments"]
        return [
            TranscriptSegment(
                start=float(item["start"]),
                end=float(item["end"]),
                text=str(item["text"]).strip(),
                confidence=float(item.get("confidence", 1.0)),
            )
            for item in segments
        ]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(f"Invalid sample transcript: {path}") from exc


def write_raw_transcript(segments: list[TranscriptSegment], output_dir: Path) -> Path:
    """Write raw transcript text and return its path."""

    if not segments:
        raise PipelineError("Transcription produced no segments.")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "raw_transcript.txt"
    path.write_text("\n".join(segment.text for segment in segments) + "\n", encoding="utf-8")
    (output_dir / "raw_transcript.json").write_text(
        json.dumps([segment.__dict__ for segment in segments], indent=2),
        encoding="utf-8",
    )
    return path
