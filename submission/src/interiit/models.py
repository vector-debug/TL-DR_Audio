"""Typed data contracts for the meeting assistant."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TranscriptSegment:
    """A timestamped piece of a transcript."""

    start: float
    end: float
    text: str
    confidence: float = 1.0


@dataclass(frozen=True)
class Decision:
    """A decision grounded in transcript evidence."""

    decision: str
    evidence: str


@dataclass(frozen=True)
class ActionItem:
    """A follow-up task extracted from a meeting."""

    task: str
    owner: str = "Unspecified"
    deadline: str = "Unspecified"
    evidence: str = ""


@dataclass
class MeetingRecord:
    """Structured documentation generated from a meeting transcript."""

    title: str
    date: str
    purpose: str
    executive_summary: str
    decisions: list[Decision] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)
