"""Grounded extraction of minutes, decisions, and action items."""

import re
from datetime import datetime

from interiit.models import ActionItem, Decision, MeetingRecord


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def extract_record(text: str, title: str, date: str, purpose: str) -> MeetingRecord:
    """Extract a deterministic meeting record from a controlled transcript."""

    sentences = _sentences(text)
    decisions = [
        Decision(sentence, sentence)
        for sentence in sentences
        if re.search(r"\b(decided|approved|agreed|adopted)\b", sentence, re.IGNORECASE)
    ]
    actions: list[ActionItem] = []
    for sentence in sentences:
        match = re.search(
            r"(?P<owner>[A-Z][\w-]+)\s+will\s+(?P<task>[^.]+)"
            r"(?:\s+by\s+(?P<deadline>[^.]+))?",
            sentence,
        )
        if match:
            actions.append(
                ActionItem(
                    task=match.group("task").strip(),
                    owner=match.group("owner").strip(),
                    deadline=(match.group("deadline") or "Unspecified").strip(),
                    evidence=sentence,
                )
            )
    summary = " ".join(sentences[:2]) if sentences else "No transcript content was available."
    return MeetingRecord(title, date, purpose, summary, decisions, actions)


def render_markdown(record: MeetingRecord) -> dict[str, str]:
    """Render documentation artifacts from a validated record."""

    decisions = "\n".join(
        f"{index}. {item.decision}\n   - Evidence: {item.evidence}"
        for index, item in enumerate(record.decisions, 1)
    ) or "No grounded decisions were identified."
    actions = "\n".join(
        f"{index}. **{item.task}** — Owner: {item.owner}; Deadline: {item.deadline}\n"
        f"   - Evidence: {item.evidence}"
        for index, item in enumerate(record.action_items, 1)
    ) or "No grounded action items were identified."
    minutes = (
        f"# {record.title}\n\n"
        f"- **Date:** {record.date}\n- **Purpose:** {record.purpose}\n\n"
        f"## Executive summary\n{record.executive_summary}\n\n"
        f"## Decisions\n{decisions}\n\n## Action items\n{actions}\n"
    )
    return {
        "minutes.md": minutes,
        "key_decisions.md": f"# Key decisions\n\n{decisions}\n",
        "action_items.md": f"# Action items\n\n{actions}\n",
    }
