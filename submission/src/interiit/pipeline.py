"""End-to-end offline meeting documentation pipeline."""

import json
from pathlib import Path
from typing import Callable

from interiit.errors import PipelineError
from interiit.models import TranscriptSegment
from interiit.renderers.json_manifest import write_manifest
from interiit.stages.documentation import extract_record, render_markdown
from interiit.stages.refinement import refine_segments, write_refined_transcript
from interiit.stages.transcription import load_sample_transcript, write_raw_transcript

ProgressCallback = Callable[[str, str], None]


def run_demo(
    sample_path: Path, output_dir: Path, on_progress: ProgressCallback | None = None
) -> dict:
    """Run all offline stages and return the generated manifest."""

    notify = on_progress or (lambda _stage, _message: None)
    if not sample_path.is_file():
        raise PipelineError(f"Sample data not found: {sample_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    notify("transcription", "Loading deterministic sample transcript")
    segments = load_sample_transcript(sample_path)
    write_raw_transcript(segments, output_dir)
    notify("refinement", "Applying glossary corrections")
    refined, changes = refine_segments(segments, payload.get("glossary", {}))
    write_refined_transcript(refined, output_dir)
    text = " ".join(segment.text for segment in refined)
    notify("documentation", "Extracting grounded decisions and action items")
    record = extract_record(
        text,
        title=str(payload.get("title", "Meeting")),
        date=str(payload.get("date", "Unspecified")),
        purpose=str(payload.get("purpose", "Unspecified")),
    )
    for filename, content in render_markdown(record).items():
        (output_dir / filename).write_text(content, encoding="utf-8")
    (output_dir / "meeting_record.json").write_text(
        json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "mode": "offline",
        "source": str(sample_path),
        "outputs": {name: str(output_dir / name) for name in (
            "raw_transcript.txt", "refined_transcript.txt", "minutes.md",
            "key_decisions.md", "action_items.md", "meeting_record.json",
        )},
        "metrics": {
            "segments": len(segments),
            "glossary_changes": changes,
            "decisions": len(record.decisions),
            "action_items": len(record.action_items),
        },
    }
    write_manifest(manifest, output_dir)
    notify("complete", "Demo artifacts written successfully")
    return manifest
