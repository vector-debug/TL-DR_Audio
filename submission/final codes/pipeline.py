import argparse
import json
import sys
from pathlib import Path

from Stage_1 import run_stage1
from stage2_refine_clean import Stage2Error, run_stage2
from stage3_minutes import Stage3Error, run_stage3


class PipelineError(RuntimeError):
    pass


def run_pipeline(audio_path, output_dir="outputs", on_progress=None):
    note = on_progress or (lambda stage, state, msg: None)
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)

    if not audio_path.is_file():
        raise PipelineError(f"Audio file not found: {audio_path}")
    if audio_path.stat().st_size == 0:
        raise PipelineError(f"Audio file is empty: {audio_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        note(2, "start", "Transcribing speech with Faster-Whisper")
        stage1 = run_stage1(audio_path, output_dir)
        note(2, "done", f"Stage 1 complete: {stage1['segments']} segments, language {stage1['language']}")
        note(3, "start", "Refining transcript in chunks")
        stage2 = run_stage2(
            stage1["marked_transcript"],
            output_dir,
            raw_transcript_path=stage1["raw_transcript"],
            low_confidence_path=stage1["low_confidence"],
        )
        note(3, "done", f"Stage 2 complete: {stage2['chunks']} chunks, {stage2['total_changes']} changes, {stage2['fallbacks']} fallbacks")
        note(4, "start", "Generating structured meeting record")
        stage3 = run_stage3(
            stage2["refined_path"],
            output_dir,
            stage1["raw_transcript_json"],
        )
        note(4, "done", f"Stage 3 complete: {stage3['decisions']} decisions, {stage3['action_items']} action items")
    except (Stage2Error, Stage3Error, RuntimeError, ValueError) as exc:
        raise PipelineError(str(exc)) from exc

    manifest = {
        "audio_file": str(audio_path),
        "outputs": {
            "raw_transcript": stage1["raw_transcript"],
            "refined_transcript": stage2["refined_path"],
            "minutes": stage3["minutes.md"],
            "key_decisions": stage3["key_decisions.md"],
            "action_items": stage3["action_items.md"],
            "meeting_record": stage3["meeting_record.json"],
            "raw_transcript_segments": stage1["raw_transcript_json"],
            "refinement_log": stage2["change_log_path"],
        },
        "stage1": {
            "segments": stage1["segments"],
            "language": stage1["language"],
            "language_probability": stage1["language_probability"],
        },
        "stage2": {
            "chunks": stage2["chunks"],
            "changes": stage2["total_changes"],
            "fallbacks": stage2["fallbacks"],
        },
        "stage3": {
            "decisions": stage3["decisions"],
            "action_items": stage3["action_items"],
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest


def parse_args():
    parser = argparse.ArgumentParser(description="Run the meeting transcription pipeline.")
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("--out", type=Path, default=Path("outputs"))
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = run_pipeline(args.audio_file, args.out)
    except PipelineError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Done: outputs written to {args.out}")
    print(json.dumps(result["outputs"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
