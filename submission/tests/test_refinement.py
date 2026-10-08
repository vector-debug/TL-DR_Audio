from interiit.models import TranscriptSegment
from interiit.stages.refinement import refine_segments


def test_refinement_preserves_timestamps_and_applies_glossary() -> None:
    segments = [TranscriptSegment(0, 1, "Use faster whisper today.")]
    refined, changes = refine_segments(segments, {"faster whisper": "Faster-Whisper"})
    assert refined[0].text == "Use Faster-Whisper today."
    assert refined[0].start == 0
    assert changes == 1
