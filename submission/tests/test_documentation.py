from interiit.stages.documentation import extract_record, render_markdown


def test_documentation_extracts_grounded_decision_and_action() -> None:
    record = extract_record(
        "The team agreed to ship Friday. Asha will prepare slides by Friday.",
        "Demo",
        "2026-10-07",
        "Ship the demo.",
    )
    assert len(record.decisions) == 1
    assert record.action_items[0].owner == "Asha"
    assert "Key decisions" in render_markdown(record)["key_decisions.md"]
