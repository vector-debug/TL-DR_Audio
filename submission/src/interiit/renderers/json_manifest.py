"""Manifest serialization for pipeline outputs."""

import json
from pathlib import Path
from typing import Any


def write_manifest(payload: dict[str, Any], output_dir: Path) -> Path:
    """Write a stable JSON manifest and return its path."""

    path = output_dir / "manifest.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
