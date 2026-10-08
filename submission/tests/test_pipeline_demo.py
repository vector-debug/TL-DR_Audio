import json
from pathlib import Path

from interiit.pipeline import run_demo


def test_demo_pipeline_writes_contract(tmp_path: Path) -> None:
    manifest = run_demo(Path("data/sample_meeting.json"), tmp_path)
    assert manifest["metrics"]["decisions"] == 1
    assert manifest["metrics"]["action_items"] == 2
    assert (tmp_path / "manifest.json").is_file()
    record = json.loads((tmp_path / "meeting_record.json").read_text(encoding="utf-8"))
    assert record["title"] == "InterIIT Product Planning"
