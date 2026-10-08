import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "final codes" / "stage2_refine_clean.py"
SPEC = importlib.util.spec_from_file_location("stage2_refine_clean", MODULE_PATH)
stage2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage2)


def test_validate_chunk_allows_changes_to_marked_words():
    valid, reason, _ = stage2.validate_chunk(
        "The [[council|0.20]] met while members reviewed the agenda and discussed several items before voting on the motion.",
        {"refined_text": "The committee met while members reviewed the agenda and discussed several items before voting on the motion.", "changes": []},
    )

    assert valid
    assert reason == ""


def test_validate_chunk_rejects_unmarked_deletions():
    valid, reason, _ = stage2.validate_chunk(
        "The [[council|0.20]] met while members reviewed the agenda and discussed several items before voting on the motion.",
        {"refined_text": "The council while members reviewed the agenda and discussed several items before voting on the motion.", "changes": []},
    )

    assert not valid
    assert "unauthorized change" in reason
