"""Regenerate the deterministic sample meeting fixture."""

import json
from pathlib import Path


def main() -> None:
    """Write the canonical sample data file."""

    path = Path(__file__).with_name("sample_meeting.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
