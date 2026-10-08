import argparse
import gc
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

LOW_CONFIDENCE_THRESHOLD = 0.5
DEFAULT_AUDIO_PATH = Path("Recordings/09-15-2026-Council-Meeting.mp3")
OUTPUT_DIR = Path("outputs")


def is_lexical_token(token):
    return any(character.isalnum() for character in token)


def configure_windows_cuda_dlls():
    if os.name != "nt":
        return

    dll_dirs = []
    for package in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            spec = importlib.util.find_spec(package)
        except ModuleNotFoundError:
            spec = None
        if spec and spec.submodule_search_locations:
            dll_dir = Path(next(iter(spec.submodule_search_locations))) / "bin"
            if dll_dir.is_dir():
                dll_dirs.append(str(dll_dir))

    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs + [os.environ.get("PATH", "")])
        for dll_dir in dll_dirs:
            os.add_dll_directory(dll_dir)


configure_windows_cuda_dlls()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio_file", nargs="?", type=Path, default=DEFAULT_AUDIO_PATH)
    return parser.parse_args().audio_file


def load_model():
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Transcription dependencies are missing. Run 'uv sync' before processing audio."
        ) from exc

    cuda_devices = ctranslate2.get_cuda_device_count()
    print(f"CTranslate2 CUDA devices visible: {cuda_devices}")

    modes = []
    if cuda_devices:
        modes.extend((("cuda", "float16"), ("cuda", "int8_float16")))
    modes.append(("cpu", "int8"))

    errors = []
    for device, compute_type in modes:
        try:
            model = WhisperModel("large-v3-turbo", device=device, compute_type=compute_type)
            active_mode = f"device={device}, compute_type={compute_type}"
            print(f"Whisper mode active: {active_mode}")
            return model, active_mode
        except Exception as exc:
            errors.append(f"{device}/{compute_type}: {exc}")
            print(f"Could not load {device}/{compute_type}: {exc}", file=sys.stderr)

    raise RuntimeError("Could not load WhisperModel. " + " | ".join(errors))


def render_marked_transcript(segments):
    rendered_segments = []

    for segment in segments:
        words = segment["words"]

        if not words:
            rendered_segments.append(segment["text"])
            continue

        rendered_words = []
        for word in words:
            token = word["word"]

            if word["prob"] < LOW_CONFIDENCE_THRESHOLD and is_lexical_token(token):
                leading_space = token[:len(token) - len(token.lstrip())]
                rendered_words.append(f"{leading_space}[[{token.strip()}|{word['prob']:.2f}]]")
            else:
                rendered_words.append(token)

        rendered_segments.append("".join(rendered_words).strip())

    return "\n".join(rendered_segments)


def build_low_confidence_words(segments):
    low_confidence = []

    for segment_index, segment in enumerate(segments):
        words = segment["words"]

        for word_index, word in enumerate(words):
            if word["prob"] < LOW_CONFIDENCE_THRESHOLD and is_lexical_token(word["word"]):
                context_words = (
                    words[max(0, word_index - 5):word_index]
                    + words[word_index + 1:word_index + 6]
                )

                low_confidence.append({
                    "segment_index": segment_index,
                    "word": word["word"],
                    "start": word["start"],
                    "end": word["end"],
                    "prob": word["prob"],
                    "context": "".join(item["word"] for item in context_words).strip(),
                })

    return low_confidence


def run_stage1(audio_path, out_dir=OUTPUT_DIR):
    audio_path = Path(audio_path)
    output_dir = Path(out_dir)
    if not audio_path.is_file():
        raise ValueError(f"Audio file not found: {audio_path}")

    if audio_path.stat().st_size == 0:
        raise ValueError(f"Audio file is empty: {audio_path}")

    model = None

    try:
        model, _ = load_model()
        started_at = time.perf_counter()

        segments_iterator, info = model.transcribe(
            str(audio_path),
            language="en",
            temperature=0.0,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )

        segments = []

        for segment in segments_iterator:
            words = [
                {
                    "word": word.word,
                    "start": word.start,
                    "end": word.end,
                    "prob": round(word.probability, 3),
                }
                for word in (segment.words or [])
            ]

            segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text.strip(),
                "avg_logprob": segment.avg_logprob,
                "no_speech_prob": segment.no_speech_prob,
                "words": words,
            })

        if not segments:
            raise RuntimeError("No speech could be decoded from the audio file.")

        output_dir.mkdir(parents=True, exist_ok=True)
        low_confidence = build_low_confidence_words(segments)

        raw_json_path = output_dir / "raw_transcript.json"
        raw_text_path = output_dir / "raw_transcript.txt"
        low_confidence_path = output_dir / "low_confidence.json"
        marked_path = output_dir / "marked_transcript.txt"

        raw_json_path.write_text(
            json.dumps(segments, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        raw_text_path.write_text(
            "\n".join(segment["text"] for segment in segments),
            encoding="utf-8"
        )

        low_confidence_path.write_text(
            json.dumps(low_confidence, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        marked_path.write_text(
            render_marked_transcript(segments),
            encoding="utf-8"
        )

        elapsed = time.perf_counter() - started_at
        print(f"Detected language: {info.language} ({info.language_probability:.2%})")
        print(f"Finished in {elapsed:.1f}s. Low-confidence words: {len(low_confidence)}")

        return {
            "raw_transcript": str(raw_text_path),
            "raw_transcript_json": str(raw_json_path),
            "marked_transcript": str(marked_path),
            "low_confidence": str(low_confidence_path),
            "segments": len(segments),
            "language": info.language,
            "language_probability": info.language_probability,
        }

    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(
            f"Could not transcribe '{audio_path}'. The file may be unreadable or use an unsupported audio format.\n{exc}"
        ) from exc

    finally:
        if model is not None:
            del model
        gc.collect()


def main():
    audio_path = parse_args()
    try:
        run_stage1(audio_path)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
