import hashlib
import io
import json
import shutil
import sys
import threading
import time
import uuid
import zipfile
from html import escape
from pathlib import Path

import markdown
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from xhtml2pdf import pisa

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "final codes"))
from pipeline import PipelineError, run_pipeline  # noqa: E402

RUNS = ROOT / "runs"
SAMPLE = ROOT / "final codes" / "outputs"
DOCS = ("raw", "refined", "decisions", "actions", "minutes")

app = FastAPI()
jobs = {}  # job_id -> {"events": [], "out": Path, "name": str, "duration": str, "done": bool}
# ponytail: one pipeline at a time (Whisper is heavy); queue/workers if concurrent users matter
run_lock = threading.Lock()


def emit(job, kind, **data):
    job["events"].append({"type": kind, **data})


def fmt_dur(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}h {m}m {s}s" if h else f"{m}m {s:02d}s"


def probe_duration(path):
    from pydub.utils import mediainfo
    return float(mediainfo(str(path))["duration"])


def run_job(job, audio):
    with run_lock:
        try:
            emit(job, "stage", stage=1, state="start", msg="Validating audio file")
            job["seconds"] = probe_duration(audio)
            job["duration"] = fmt_dur(job["seconds"])
            emit(job, "stage", stage=1, state="done", msg=f"Audio valid: {job['duration']}, {audio.stat().st_size} bytes")
            run_pipeline(
                audio, job["out"],
                on_progress=lambda stage, state, msg: emit(job, "stage", stage=stage, state=state, msg=msg),
            )
            emit(job, "done")
        except Exception as exc:  # surface backend message to the UI
            emit(job, "error", msg=str(exc) or exc.__class__.__name__)


def replay_sample(job):
    steps = [(1, "Validating audio file"), (2, "Loaded cached transcription"),
             (3, "Loaded cached refinement"), (4, "Loaded cached meeting record")]
    for stage, msg in steps:
        emit(job, "stage", stage=stage, state="start", msg=msg)
        time.sleep(0.8)
        emit(job, "stage", stage=stage, state="done", msg=msg)
    emit(job, "done")


@app.post("/api/jobs")
async def create_job(audio: UploadFile = File(None), sample: bool = False):
    job_id = uuid.uuid4().hex[:12]
    job = {"events": [], "name": "", "duration": ""}
    jobs[job_id] = job
    if sample:
        job.update(out=SAMPLE, name="09-15-2026-Council-Meeting.mp3", duration="10m 03s")
        threading.Thread(target=replay_sample, args=(job,), daemon=True).start()
        return {"job_id": job_id}
    if audio is None or not audio.filename.lower().endswith(".mp3"):
        raise HTTPException(400, "Only .mp3 files accepted")
    out = RUNS / job_id
    out.mkdir(parents=True)
    path = out / "input.mp3"
    with path.open("wb") as f:
        shutil.copyfileobj(audio.file, f)
    job.update(out=out, name=Path(audio.filename).name)
    threading.Thread(target=run_job, args=(job, path), daemon=True).start()
    return {"job_id": job_id}


def get_job(job_id):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job")
    return jobs[job_id]


@app.get("/api/jobs/{job_id}/events")
def events(job_id: str):
    job = get_job(job_id)

    def stream():
        i = 0
        while True:
            while i < len(job["events"]):
                ev = job["events"][i]
                i += 1
                yield f"data: {json.dumps(ev)}\n\n"
                if ev["type"] in ("done", "error"):
                    return
            time.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream")


def read(out, name):
    return (out / name).read_text(encoding="utf-8")


def hms(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def raw_markdown(out):
    segs = json.loads(read(out, "raw_transcript.json"))
    return "# Raw Transcript\n\n" + "\n\n".join(f"**[{hms(s['start'])}]** {s['text'].strip()}" for s in segs)


def doc_markdown(out, doc):
    if doc == "raw":
        return raw_markdown(out)
    if doc == "refined":
        return "# Refined Transcript\n\n" + read(out, "refined_transcript.txt")
    files = {
        "decisions": "key_decisions.md",
        "actions": "action_items.md",
        "minutes": "minutes.md",
    }
    return read(out, files[doc])


def make_pdf(out, doc):
    cache = out / "pdf" / f"{doc}.pdf"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        html = markdown.markdown(doc_markdown(out, doc), extensions=["tables"])
        css = "body{font-family:Helvetica;font-size:10pt;line-height:1.4}h1,h2,h3{color:#090A0F}"
        with cache.open("wb") as f:
            pisa.CreatePDF(f"<style>{css}</style>{html}", dest=f)
    return cache


def kb(n):
    return f"{n / 1024 / 1024:.1f} MB" if n > 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


@app.get("/api/jobs/{job_id}/result")
def result(job_id: str):
    job = get_job(job_id)
    out = job["out"]
    segs = json.loads(read(out, "raw_transcript.json"))
    refined = read(out, "refined_transcript.txt")
    manifest = read(out, "manifest.json")
    preview_content = {doc: doc_markdown(out, doc) for doc in DOCS}
    return {
        "filename": job["name"],
        "duration": job["duration"],
        "checksum": "-".join(hashlib.sha256(manifest.encode()).hexdigest()[:8][i:i + 4] for i in (0, 4)),
        "raw_range": f"{hms(segs[0]['start'])} – {hms(segs[-1]['end'])}" if segs else "",
        "raw_excerpt": [{"t": hms(s["start"]), "text": s["text"].strip()} for s in segs[:3]],
        "refined_excerpt": refined[:600],
        "preview_content": preview_content,
        "record": json.loads(read(out, "meeting_record.json")),
        "pdf_sizes": {d: kb(make_pdf(out, d).stat().st_size) for d in DOCS},
    }


NAMES = {
    "raw": "Raw_Transcript",
    "refined": "Refined_Transcript",
    "decisions": "Decisions",
    "actions": "Actionable_Items",
    "minutes": "Minutes_of_the_Meet",
}


@app.get("/api/jobs/{job_id}/download/{doc}.pdf")
def download(job_id: str, doc: str):
    if doc not in DOCS:
        raise HTTPException(404)
    return FileResponse(make_pdf(get_job(job_id)["out"], doc), media_type="application/pdf", filename=f"{NAMES[doc]}.pdf")


@app.get("/api/jobs/{job_id}/download.zip")
def download_zip(job_id: str, docs: str = ",".join(DOCS)):
    out = get_job(job_id)["out"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for d in docs.split(","):
            if d in DOCS:
                z.write(make_pdf(out, d), f"{NAMES[d]}.pdf")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="meeting_documents.zip"'})


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
