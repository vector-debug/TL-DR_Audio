import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "final codes" / "Stage_1.py"
SPEC = importlib.util.spec_from_file_location("stage1", MODULE_PATH)
stage1 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage1)


def test_punctuation_only_low_confidence_tokens_are_not_marked():
    segments = [{
        "text": "We have - a motion.",
        "words": [
            {"word": " We", "prob": 0.99},
            {"word": " –", "prob": 0.10},
            {"word": " a", "prob": 0.99},
            {"word": " motion.", "prob": 0.99},
        ],
    }]

    assert stage1.build_low_confidence_words(segments) == []
    assert stage1.render_marked_transcript(segments) == "We – a motion."


def test_lexical_low_confidence_tokens_are_marked_and_recorded():
    segments = [{
        "text": "A council motion.",
        "words": [
            {"word": " A", "prob": 0.99},
            {"word": " council", "prob": 0.40, "start": 1.0, "end": 1.4},
            {"word": " motion.", "prob": 0.99},
        ],
    }]

    assert "[[council|0.40]]" in stage1.render_marked_transcript(segments)
    assert stage1.build_low_confidence_words(segments) == [{
        "segment_index": 0,
        "word": " council",
        "start": 1.0,
        "end": 1.4,
        "prob": 0.40,
        "context": "A motion.",
    }]
