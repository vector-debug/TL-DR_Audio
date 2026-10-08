import argparse
import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from groq import Groq
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, BeforeValidator, ValidationError
from rapidfuzz import fuzz, utils

from stage2_refine_clean import negation_count, split_sentences

CHUNK_WORDS = 2500
MATCH_THRESHOLD = 90
DEFAULT_MODEL = "openai/gpt-oss-120b"
UNSPECIFIED = "Unspecified"
EMPTY_VALUES = {"", "unspecified", "none", "null", "n/a", "not specified", "unknown", "tbd"}
TENTATIVE = re.compile(r"\b(should|could|might|maybe|perhaps|suggest\w*|propos\w*|consider\w*|what if)\b", re.I)
AGREED = re.compile(r"\b(agree\w*|decided|approved?|confirm\w*|settled|final\w*|go(ing)? with)\b", re.I)

BASE_DIR = Path(__file__).parent
SYSTEM_PROMPT_FILE = "Stage_3_system.txt"
EXTRACT_PROMPT_FILE = "Stage_3_extract.txt"
MERGE_PROMPT_FILE = "Stage_3_merge.txt"
OUTPUT_FILES = ("minutes.md", "key_decisions.md", "action_items.md")

Stated = Annotated[str, BeforeValidator(lambda v: UNSPECIFIED if str(v or "").strip().lower() in EMPTY_VALUES else str(v).strip())]
Text = Annotated[str, BeforeValidator(lambda v: str(v or "").strip())]


class Stage3Error(RuntimeError):
    pass


class MeetingInfo(BaseModel):
    title: Stated = UNSPECIFIED
    date: Stated = UNSPECIFIED
    duration: Stated = UNSPECIFIED
    attendees: list[str] = []
    purpose: Stated = UNSPECIFIED


class Motion(BaseModel):
    motion: Text = ""
    moved_by: Stated = UNSPECIFIED
    seconded_by: Stated = UNSPECIFIED
    outcome: Text = ""
    evidence: Text = ""


class Topic(BaseModel):
    topic: str
    points: list[str] = []
    context: list[str] = []
    people_involved: list[str] = []
    proposals: list[str] = []
    questions_concerns: list[str] = []
    motions: list[Motion] = []
    outcome: Text = ""
    decision: Text = ""


class Decision(BaseModel):
    decision: str
    context: Text = ""
    evidence: Text = ""


class ActionItem(BaseModel):
    task: str
    owner: Stated = UNSPECIFIED
    deadline: Stated = UNSPECIFIED
    evidence: Text = ""


class MeetingRecord(BaseModel):
    meeting_info: MeetingInfo = MeetingInfo()
    executive_summary: Text = ""
    discussion_points: list[Topic] = []
    decisions: list[Decision] = []
    action_items: list[ActionItem] = []
    open_questions: list[str] = []
    next_steps: list[str] = []


def read_prompt(name):
    path = BASE_DIR / "prompts" / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Stage3Error(f"Prompt file is missing: {path}") from exc


def fill(template, **values):
    for key, value in values.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def call_llm(prompt):
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise Stage3Error("Missing API key: set GROQ_API_KEY.")
    try:
        response = Groq(api_key=api_key).chat.completions.create(
            model=os.getenv("DOC_LLM_MODEL", DEFAULT_MODEL),
            messages=[
                {"role": "system", "content": read_prompt(SYSTEM_PROMPT_FILE)},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_completion_tokens=8192,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        raise Stage3Error(f"LLM API call failed: {exc}") from exc


def generate(prompt):
    error = ""
    for _ in range(2):
        retry = f"\n\nYour previous reply was rejected: {error}\nReturn valid JSON that matches the schema exactly." if error else ""
        try:
            return MeetingRecord.model_validate_json(call_llm(prompt + retry))
        except ValidationError as exc:
            error = str(exc)[:500]
            print(f"Invalid LLM output, retrying: {error.splitlines()[0]}", file=sys.stderr)
    raise Stage3Error(f"LLM returned an invalid meeting record: {error}")


def chunk_transcript(text):
    chunks, current, size = [], [], 0
    for sentence in split_sentences(text):
        words = len(sentence.split())
        if current and size + words > CHUNK_WORDS:
            chunks.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += words
    if current:
        chunks.append(" ".join(current))
    return chunks


def recording_duration(segments_path):
    try:
        segments = json.loads(Path(segments_path).read_text(encoding="utf-8"))
        return str(timedelta(seconds=round(segments[-1]["end"])))
    except (OSError, ValueError, LookupError, TypeError):
        return UNSPECIFIED


def verify(record, transcript, duration):
    source = utils.default_process(transcript)

    def found(value, text):
        return value != UNSPECIFIED and bool(value.strip()) and fuzz.partial_ratio(utils.default_process(value), text) >= MATCH_THRESHOLD

    def tentative(text):
        return bool(TENTATIVE.search(text)) and not AGREED.search(text)

    unconfirmed, decisions, actions = [], [], []
    for item in record.decisions:
        if not found(item.evidence, source):
            continue
        if tentative(item.evidence) or bool(negation_count(item.evidence)) != bool(negation_count(item.decision)):
            unconfirmed.append(f'Unconfirmed: "{item.evidence}"')
        else:
            decisions.append(item)

    for item in record.action_items:
        if not found(item.evidence, source):
            continue
        evidence = utils.default_process(item.evidence)
        item.owner = item.owner if found(item.owner, evidence) else UNSPECIFIED
        item.deadline = item.deadline if found(item.deadline, evidence) else UNSPECIFIED
        if item.owner == UNSPECIFIED and tentative(item.evidence):
            unconfirmed.append(f'Unconfirmed: "{item.evidence}"')
        else:
            actions.append(item)

    info = record.meeting_info
    info.title = info.title if found(info.title, source) else UNSPECIFIED
    info.date = info.date if found(info.date, source) else UNSPECIFIED
    info.attendees = [name for name in info.attendees if found(name, source)]
    info.duration = duration
    record.decisions, record.action_items = decisions, actions
    record.open_questions += unconfirmed
    return record


def render(record, out_dir):
    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"), trim_blocks=True, lstrip_blocks=True)
    data = record.model_dump()
    paths = {}
    for name in OUTPUT_FILES:
        paths[name] = out_dir / name
        paths[name].write_text(env.get_template(name + ".j2").render(**data), encoding="utf-8")
    paths["meeting_record.json"] = out_dir / "meeting_record.json"
    paths["meeting_record.json"].write_text(record.model_dump_json(indent=2), encoding="utf-8")
    return {name: str(path) for name, path in paths.items()}


def run_stage3(refined_path, out_dir, segments_path=""):
    input_path = Path(refined_path)
    if not input_path.is_file():
        raise Stage3Error(f"Refined transcript not found: {input_path}")
    transcript = input_path.read_text(encoding="utf-8").strip()
    if not transcript:
        raise Stage3Error("Refined transcript is empty.")

    chunks = chunk_transcript(transcript)
    print(f"Generating minutes from {len(chunks)} part(s) with {os.getenv('DOC_LLM_MODEL', DEFAULT_MODEL)}")
    notes = [generate(fill(read_prompt(EXTRACT_PROMPT_FILE), part=i, total=len(chunks), text=chunk))
             for i, chunk in enumerate(chunks, 1)]
    if len(notes) == 1:
        record = notes[0]
    else:
        notes_json = json.dumps([{"part": i, **n.model_dump()} for i, n in enumerate(notes, 1)], indent=1, ensure_ascii=False)
        record = generate(fill(read_prompt(MERGE_PROMPT_FILE), total=len(notes), notes=notes_json))

    record = verify(record, transcript, recording_duration(segments_path))
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {"decisions": len(record.decisions), "action_items": len(record.action_items), **render(record, output_dir)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/refined_transcript.txt", help="Path to refined_transcript.txt")
    parser.add_argument("--segments", default="outputs/raw_transcript.json", help="Stage 1 segments (for duration)")
    parser.add_argument("--out", default="outputs", help="Directory for Stage 3 outputs")
    args = parser.parse_args()
    try:
        result = run_stage3(args.input, args.out, args.segments)
    except Stage3Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {result['decisions']} decisions, {result['action_items']} action items. Minutes: {result['minutes.md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
