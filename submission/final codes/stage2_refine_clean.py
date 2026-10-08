import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

CHUNK_WORDS = 400
OVERLAP_SENTENCES = 2
TOP_GLOSSARY_TERMS = 10
MAX_CORRECTION_ATTEMPTS = 3
NEGATIONS = {"not", "no", "never", "neither", "nor"}
MARK_PATTERN = re.compile(r"\[\[([^\]|]+)\|[^\]]+\]\]")
WORD_PATTERN = re.compile(r"\b[\w']+\b")
NUMBER_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\b")

# Prompt files inside "prompts/" (next to this script)
SYSTEM_PROMPT_FILE = "Stage_2_system.txt"
USER_PROMPT_FILE = "Stage_2_user.txt"
DETECTION_PROMPT_FILE = "Stage_2_detect.txt"
DOMAIN_PROMPT_FILE = "Stage_2_domain.txt"


class Stage2Error(RuntimeError):
    pass


def strip_marks(text):
    return MARK_PATTERN.sub(r"\1", text)


def split_sentences(text):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def word_count(text):
    return len(WORD_PATTERN.findall(strip_marks(text)))


def chunk_transcript(text):
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks = []
    start = 0
    while start < len(sentences):
        end = start
        size = 0
        while end < len(sentences):
            sentence_words = word_count(sentences[end])
            if end > start and size + sentence_words > CHUNK_WORDS:
                break
            size += sentence_words
            end += 1

        context_start = max(0, start - OVERLAP_SENTENCES)
        chunks.append({
            "chunk_id": len(chunks) + 1,
            "context": " ".join(sentences[context_start:start]),
            "text": " ".join(sentences[start:end]),
        })
        start = end
    return chunks


def flagged_words(text):
    return [match.group(1).strip() for match in MARK_PATTERN.finditer(text)]


def load_glossary(marked_path):
    candidates = [marked_path.parent / "glossary.txt", Path("glossary.txt")]
    for glossary_path in candidates:
        if glossary_path.is_file():
            return [line.strip() for line in glossary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return []


def retrieve_glossary_terms(chunk_text, glossary):
    queries = flagged_words(chunk_text)
    if not glossary or not queries:
        return []

    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise Stage2Error("rapidfuzz is required for glossary matching. Install requirements.txt.") from exc

    scored = []
    for term in glossary:
        score = max(fuzz.ratio(query.lower(), term.lower()) for query in queries)
        scored.append((score, term))
    return [term for _, term in sorted(scored, reverse=True)[:TOP_GLOSSARY_TERMS]]


def read_prompt(name):
    path = Path(__file__).with_name("prompts") / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Stage2Error(f"Prompt file is missing: {path}") from exc


def call_llm(prompt, system=None):
    load_dotenv()
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")


    if provider != "groq":
        raise Stage2Error(f"Unsupported LLM_PROVIDER '{provider}'. Currently supported: groq.")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise Stage2Error("Missing API key: set GROQ_API_KEY (or choose a supported LLM_PROVIDER).")

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system or read_prompt(SYSTEM_PROMPT_FILE)},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_completion_tokens=8192,
            reasoning_effort="low",
        )
        return response.choices[0].message.content or ""
    except Stage2Error:
        raise
    except Exception as exc:
        raise Stage2Error(f"LLM API call failed: {exc}") from exc


def detect_domain(marked_text):
    """Ask the LLM to infer domain, a short summary and key terms from the transcript."""
    words = strip_marks(marked_text).split()
    n = len(words)
    if n <= 1800:
        excerpt = " ".join(words)
    else:
        mid = n // 2
        parts = [words[:600], words[mid - 300:mid + 300], words[-600:]]
        excerpt = " ... ".join(" ".join(p) for p in parts)

    prompt = read_prompt(DOMAIN_PROMPT_FILE).replace("{text}", excerpt)
    raw = call_llm(prompt, system="You analyze meeting transcripts. Return valid JSON only.")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Stage2Error(f"Domain detection returned invalid JSON: {exc.msg}") from exc
    if not isinstance(result, dict):
        raise Stage2Error("Domain detection JSON must be an object.")

    key_terms = result.get("key_terms", [])
    if not isinstance(key_terms, list):
        key_terms = []
    key_terms = [str(t).strip() for t in key_terms if str(t).strip()]
    return str(result.get("domain", "")).strip(), str(result.get("summary", "")).strip(), key_terms


def build_prompt(chunk, glossary_terms, domain_text, failure_reason=""):
    retry_instruction = ""
    if failure_reason:
        retry_instruction = f"RETRY REQUIREMENT: Your previous result was rejected because: {failure_reason}. Preserve the original wording more strictly."
    prompt = read_prompt(USER_PROMPT_FILE)
    replacements = {
        "{domain}": domain_text or "(unknown)",
        "{context}": chunk["context"] or "(none)",
        "{glossary}": ", ".join(glossary_terms) or "(none)",
        "{text}": chunk["text"],
        "{retry_instruction}": retry_instruction,
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def build_detection_prompt(chunk, glossary_terms, domain_text):
    prompt = read_prompt(DETECTION_PROMPT_FILE)
    replacements = {
        "{domain}": domain_text or "(unknown)",
        "{context}": chunk["context"] or "(none)",
        "{glossary}": ", ".join(glossary_terms) or "(none)",
        "{text}": chunk["text"],
    }
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


def parse_response(raw_response):
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise Stage2Error(f"LLM returned invalid JSON: {exc.msg}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("refined_text"), str):
        raise Stage2Error("LLM JSON must contain a string refined_text.")
    if not isinstance(result.get("changes"), list):
        raise Stage2Error("LLM JSON must contain a changes list.")
    return result


def parse_detection_response(raw_response):
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise Stage2Error(f"LLM error detection returned invalid JSON: {exc.msg}") from exc
    if not isinstance(result, dict) or not isinstance(result.get("has_error"), bool):
        raise Stage2Error("LLM error detection JSON must contain a boolean has_error.")
    suspect_phrases = result.get("suspect_phrases", [])
    if not isinstance(suspect_phrases, list):
        raise Stage2Error("LLM error detection JSON must contain a suspect_phrases list.")
    return result


def negation_count(text):
    words = [word.lower() for word in WORD_PATTERN.findall(text)]
    return sum(word in NEGATIONS or word.endswith("n't") for word in words)


def changed_word_percentage(original, refined):
    original_words = WORD_PATTERN.findall(strip_marks(original).lower())
    refined_words = WORD_PATTERN.findall(refined.lower())
    if not original_words:
        return 0.0
    changed = 0
    for tag, start, end, other_start, other_end in difflib.SequenceMatcher(None, original_words, refined_words).get_opcodes():
        if tag != "equal":
            changed += max(end - start, other_end - other_start)
    return changed / len(original_words) * 100


def transcript_words(text):
    return [match.group(0).casefold() for match in WORD_PATTERN.finditer(strip_marks(text))]


def marked_word_indices(text):
    raw_words = transcript_words(text)
    allowed_indices = set()
    search_start = 0

    for marked_word in flagged_words(text):
        candidate_words = transcript_words(marked_word)
        if len(candidate_words) != 1:
            continue
        try:
            index = raw_words.index(candidate_words[0], search_start)
        except ValueError:
            continue
        allowed_indices.add(index)
        search_start = index + 1

    return allowed_indices


def load_low_confidence_words(path, raw_words):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Stage2Error(f"Low-confidence file not found: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage2Error(f"Could not read low-confidence file '{path}': {exc}") from exc

    if not isinstance(data, list):
        raise Stage2Error(f"Low-confidence file must contain a JSON list: {path}")

    allowed_indices = set()
    search_start = 0
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("word"), str):
            raise Stage2Error(f"Invalid low-confidence entry in {path}")
        candidate_words = transcript_words(item["word"])
        if len(candidate_words) != 1:
            raise Stage2Error(
                f"Low-confidence entry must contain one word: {item['word']!r}"
            )

        candidate = candidate_words[0]
        try:
            index = raw_words.index(candidate, search_start)
        except ValueError as exc:
            raise Stage2Error(
                f"Low-confidence word {item['word']!r} was not found in raw transcript"
            ) from exc
        allowed_indices.add(index)
        search_start = index + 1

    return allowed_indices


def audit_transcript_integrity(raw_text, refined_text, low_confidence_path):
    raw_words = transcript_words(raw_text)
    refined_words = transcript_words(refined_text)
    allowed_indices = load_low_confidence_words(low_confidence_path, raw_words)

    matcher = difflib.SequenceMatcher(None, raw_words, refined_words, autojunk=False)
    for tag, raw_start, raw_end, refined_start, refined_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        if raw_start == raw_end:
            raise Stage2Error(
                "Stage 2 integrity check failed: words were inserted outside "
                "low-confidence corrections. Please try again."
            )

        unauthorized = [
            index for index in range(raw_start, raw_end)
            if index not in allowed_indices
        ]
        if unauthorized:
            original = " ".join(raw_words[raw_start:raw_end])
            replacement = " ".join(refined_words[refined_start:refined_end])
            raise Stage2Error(
                "Stage 2 integrity check failed: unauthorized change "
                f"{original!r} -> {replacement!r}. Please try again."
            )

    return len(allowed_indices)


def validate_chunk(original, result):
    refined = result["refined_text"]
    if "[[" in refined or "]]" in refined:
        return False, "refined_text still contains confidence marks", 0.0

    percent_changed = changed_word_percentage(original, refined)
    if percent_changed > 10:
        return False, f"{percent_changed:.1f}% of words changed (maximum is 10%)", percent_changed

    original_words = transcript_words(original)
    refined_words = transcript_words(refined)
    allowed_indices = marked_word_indices(original)
    for tag, raw_start, raw_end, refined_start, refined_end in difflib.SequenceMatcher(
        None, original_words, refined_words, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            continue
        if raw_start == raw_end:
            return False, "words were inserted outside low-confidence corrections", percent_changed
        unauthorized = [
            index for index in range(raw_start, raw_end)
            if index not in allowed_indices
        ]
        if unauthorized:
            original_change = " ".join(original_words[raw_start:raw_end])
            replacement = " ".join(refined_words[refined_start:refined_end])
            return False, (
                f"unauthorized change {original_change!r} -> {replacement!r}"
            ), percent_changed

    listed_originals = {str(change.get("original", "")) for change in result["changes"] if isinstance(change, dict)}
    for number in NUMBER_PATTERN.findall(strip_marks(original)):
        if number not in refined and number not in listed_originals:
            return False, f"number {number!r} is missing without a logged change", percent_changed

    if negation_count(strip_marks(original)) != negation_count(refined):
        return False, "negation count changed", percent_changed
    return True, "", percent_changed


def safe_changes(changes, chunk_id):
    normalized = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        try:
            confidence = float(change.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        normalized.append({
            "original": str(change.get("original", "")),
            "corrected": str(change.get("corrected", "")),
            "reason": str(change.get("reason", "")),
            "confidence": max(0.0, min(1.0, confidence)),
            "chunk_id": chunk_id,
        })
    return normalized


def refine_chunk(chunk, glossary, domain_text):
    terms = retrieve_glossary_terms(chunk["text"], glossary)
    try:
        detection = parse_detection_response(
            call_llm(build_detection_prompt(chunk, terms, domain_text),
                     system="You detect possible ASR errors. Return valid JSON only.")
        )
    except Stage2Error as exc:
        print(f"Chunk {chunk['chunk_id']} error detection unavailable: {exc}", file=sys.stderr)
        detection = {"has_error": True, "suspect_phrases": []}

    if not detection["has_error"]:
        return strip_marks(chunk["text"]), [], False, 0.0, ""

    failure_reason = ""
    for attempt in range(MAX_CORRECTION_ATTEMPTS):
        try:
            response = call_llm(build_prompt(chunk, terms, domain_text, failure_reason))
            result = parse_response(response)
            valid, failure_reason, percent_changed = validate_chunk(chunk["text"], result)
            if valid:
                return result["refined_text"].strip(), safe_changes(result["changes"], chunk["chunk_id"]), False, percent_changed, ""
        except Stage2Error as exc:
            failure_reason = str(exc)

        if attempt < MAX_CORRECTION_ATTEMPTS - 1:
            print(f"Chunk {chunk['chunk_id']} retrying: {failure_reason}", file=sys.stderr)

    print(f"Chunk {chunk['chunk_id']} fell back to marked input: {failure_reason}", file=sys.stderr)
    return strip_marks(chunk["text"]), [], True, 0.0, failure_reason


def run_stage2(
    marked_path,
    out_dir,
    domain="",
    context="",
    raw_transcript_path=None,
    low_confidence_path=None,
):
    input_path = Path(marked_path)
    output_dir = Path(out_dir)
    if not input_path.is_file():
        raise Stage2Error(f"Marked transcript not found: {input_path}")
    try:
        marked_text = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise Stage2Error("Marked transcript must be UTF-8.") from exc
    if not marked_text.strip():
        raise Stage2Error("Marked transcript is empty.")

    raw_path = Path(raw_transcript_path) if raw_transcript_path else input_path.parent / "raw_transcript.txt"
    low_confidence_file = (
        Path(low_confidence_path)
        if low_confidence_path
        else input_path.parent / "low_confidence.json"
    )
    try:
        raw_text = raw_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise Stage2Error(f"Raw transcript not found: {raw_path}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise Stage2Error(f"Could not read raw transcript '{raw_path}': {exc}") from exc
    if not raw_text.strip():
        raise Stage2Error("Raw transcript is empty.")

    chunks = chunk_transcript(marked_text)
    if not chunks:
        raise Stage2Error("Marked transcript contains no readable sentences.")

    glossary = load_glossary(input_path)

    # Domain: user-provided values win; otherwise auto-detect from the transcript.
    # Key terms are always extracted to enrich the glossary.
    detected = False
    try:
        det_domain, det_summary, key_terms = detect_domain(marked_text)
        detected = True
    except Stage2Error as exc:
        print(f"Domain detection skipped: {exc}", file=sys.stderr)
        det_domain, det_summary, key_terms = "", "", []

    final_domain = domain.strip() or det_domain
    final_context = context.strip() or det_summary
    domain_text = f"{final_domain}. {final_context}".strip(" .") if (final_domain or final_context) else ""
    glossary = glossary + [t for t in key_terms if t not in glossary]

    print(f"Domain: {final_domain or '(unknown)'} | auto-detected: {not domain.strip() and detected}")
    print(f"Refining {len(chunks)} chunks. Glossary terms loaded: {len(glossary)}")

    refined_chunks = []
    all_changes = []
    chunk_results = []
    fallbacks = 0
    for chunk in chunks:
        refined_text, changes, fallback, percent_changed, failure_reason = refine_chunk(chunk, glossary, domain_text)
        refined_chunks.append(refined_text)
        all_changes.extend(changes)
        fallbacks += int(fallback)
        chunk_results.append({
            "chunk_id": chunk["chunk_id"],
            "fallback": fallback,
            "percent_words_changed": round(percent_changed, 2),
            "failure_reason": failure_reason if fallback else "",
        })

    refined_text = "\n\n".join(refined_chunks) + "\n"
    allowed_corrections = audit_transcript_integrity(
        raw_text,
        refined_text,
        low_confidence_file,
    )
    overall_change_percent = changed_word_percentage(marked_text, refined_text)
    summary = {
        "total_changes": len(all_changes),
        "fallbacks": fallbacks,
        "percent_words_changed": round(overall_change_percent, 2),
        "chunks": len(chunks),
        "domain": final_domain,
        "domain_source": "user" if domain.strip() else ("auto-detected" if detected else "none"),
        "context": final_context,
        "key_terms": key_terms,
        "integrity_audit": "passed",
        "allowed_low_confidence_words": allowed_corrections,
    }
    change_log = {"summary": summary, "changes": all_changes, "chunks": chunk_results}

    output_dir.mkdir(parents=True, exist_ok=True)
    refined_path = output_dir / "refined_transcript.txt"
    change_log_path = output_dir / "change_log.json"
    refined_path.write_text(refined_text, encoding="utf-8")
    change_log_path.write_text(json.dumps(change_log, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {**summary, "refined_path": str(refined_path), "change_log_path": str(change_log_path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="outputs/marked_transcript.txt", help="Path to marked_transcript.txt")
    parser.add_argument("--out", default="outputs", help="Directory for Stage 2 outputs")
    parser.add_argument("--domain", default="", help="Meeting domain (optional; auto-detected if empty)")
    parser.add_argument("--context", default="", help="Extra meeting context (optional)")
    parser.add_argument("--raw", default="", help="Path to raw_transcript.txt")
    parser.add_argument("--low-confidence", default="", help="Path to low_confidence.json")
    args = parser.parse_args()
    try:
        result = run_stage2(
            args.input,
            args.out,
            args.domain,
            args.context,
            args.raw or None,
            args.low_confidence or None,
        )
    except Stage2Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Done: {result['chunks']} chunks, {result['total_changes']} changes, {result['fallbacks']} fallbacks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
