"""Application configuration with repository-relative defaults."""

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Paths and runtime options used by the offline demo."""

    root: Path = ROOT
    sample_data: Path = ROOT / "data" / "sample_meeting.json"
    output_dir: Path = ROOT / "outputs" / "demo"
    offline: bool = True


def get_settings() -> Settings:
    """Return validated default settings."""

    return Settings()
