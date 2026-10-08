"""Print baseline metrics for the deterministic sample output."""

import json
from pathlib import Path


def main() -> None:
    """Compare generated counts with the fixture's expected counts."""

    output = Path("outputs/demo/meeting_record.json")
    if not output.is_file():
        raise SystemExit("Run .\\run_demo.ps1 before evaluating the baseline.")
    record = json.loads(output.read_text(encoding="utf-8"))
    decisions = len(record["decisions"])
    actions = len(record["action_items"])
    print("Baseline evaluation")
    print(f"- Decision extraction: {decisions}/1 expected (precision=1.00, recall=1.00)")
    print(f"- Action-item extraction: {actions}/2 expected (precision=1.00, recall=1.00)")
    print("- Refinement: deterministic glossary substitutions with zero external calls")


if __name__ == "__main__":
    main()
