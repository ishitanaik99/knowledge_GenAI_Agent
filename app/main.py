"""SOP Desk API. Run locally:  uvicorn app.main:app --reload"""
import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, config, evaluate
from .ingest import IngestError, parse_file
from .llm import get_llm
from .store import library, records


@asynccontextmanager
async def lifespan(_app):
    library()          # load the library (and the samples on first start)
    yield


app = FastAPI(title="SOP Desk", version="1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


# ---------- protection ----------
def require_access(x_access_code: Optional[str] = Header(default=None)):
    if config.ACCESS_CODE and x_access_code != config.ACCESS_CODE:
        raise HTTPException(401, "Access code required.")


_hits = defaultdict(deque)


def rate_limit(request: Request):
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "local")).split(",")[0]
    now, window = time.time(), _hits[ip]
    while window and now - window[0] > 600:
        window.popleft()
    if len(window) >= config.RATE_LIMIT:
        raise HTTPException(429, "Too many questions. Please wait a few minutes.")
    window.append(now)


guard = [Depends(require_access)]


# ---------- request bodies ----------
class Turn(BaseModel):
    question: str = ""
    answer: str = ""


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=config.MAX_QUESTION_CHARS)
    history: List[Turn] = []


class FeedbackRequest(BaseModel):
    question: str
    answer: str = ""
    rating: str = Field(pattern="^(helpful|not_helpful)$")
    citations: List[str] = []
    comment: str = Field(default="", max_length=500)


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=3, max_length=3000)


# ---------- pages ----------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    llm = get_llm()
    return {"llm_ready": llm is not None, "provider": getattr(llm, "provider", "") or config.LLM_PROVIDER,
            "model": config.LLM_MODEL if llm else "", "access_required": bool(config.ACCESS_CODE),
            "max_upload_mb": config.MAX_UPLOAD_MB, "allowed_types": sorted(config.ALLOWED_TYPES)}


@app.post("/api/access", dependencies=guard)
def check_access():
    return {"ok": True}


# ---------- documents ----------
@app.get("/api/documents", dependencies=guard)
def list_documents():
    return {"documents": library().documents()}


@app.get("/api/documents/{doc_id}", dependencies=guard)
def get_document(doc_id: str):
    doc = library().document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    return doc


@app.get("/api/sections/{section_id}", dependencies=guard)
def get_section(section_id: str):
    s = library().section(section_id)
    if not s:
        raise HTTPException(404, "Section not found.")
    doc = library().document(s["doc_id"]) or {}
    return {**s, "owner": doc.get("owner", ""), "version": doc.get("version", ""), "updated": doc.get("updated", ""),
            "source": doc.get("source", "")}


@app.post("/api/documents", dependencies=guard)
async def upload_documents(background: BackgroundTasks, files: List[UploadFile] = File(...)):
    added, errors = [], []
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    for f in files[:20]:
        name = (f.filename or "upload").replace("\\", "/").split("/")[-1]
        data = await f.read(limit + 1)
        if len(data) > limit:
            errors.append(f"{name}: larger than {config.MAX_UPLOAD_MB} MB.")
            continue
        try:
            doc, sections = await run_in_threadpool(parse_file, name, data, "upload")
            library().add(doc, sections)
            added.append(doc)
        except (IngestError, ValueError) as e:
            errors.append(str(e))
    llm = get_llm()
    if llm and config.ENRICH_UPLOADS:
        for doc in added:
            background.add_task(agent.enrich_document, doc["doc_id"], llm)
    return {"added": added, "errors": errors}


@app.delete("/api/documents/{doc_id}", dependencies=guard)
def delete_document(doc_id: str):
    if not library().delete(doc_id):
        raise HTTPException(404, "Document not found.")
    return {"ok": True}


@app.post("/api/documents/reset", dependencies=guard)
def reset_documents():
    library().reset()
    return {"documents": library().documents()}


# ---------- chat ----------
@app.post("/api/chat", dependencies=guard + [Depends(rate_limit)])
def chat(body: ChatRequest):
    history = [t.model_dump() for t in body.history]

    def stream():
        try:
            for event in agent.run(body.question.strip(), history, get_llm()):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # never leave the browser hanging
            yield f"data: {json.dumps({'type': 'error', 'message': f'Something went wrong: {e}'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/feedback", dependencies=guard)
def feedback(body: FeedbackRequest):
    records().add_feedback(body.model_dump())
    return {"ok": True}


@app.get("/api/suggestions", dependencies=guard)
def suggestions():
    qs = []
    for d in library().documents():
        qs += [{"question": q, "doc_id": d["doc_id"]} for q in d.get("questions", [])[:2]]
    if not qs:
        qs = [{"question": q, "doc_id": ""} for q in (
            "Customer lost their debit card. How do they block it?", "When does the CAB meet?",
            "ATM debited but no cash. When is it reversed?", "Can I paste client code into ChatGPT?",
            "Who approves the canteen budget?")]
    return {"suggestions": qs[:6]}


# ---------- expert queue ----------
@app.get("/api/tickets", dependencies=guard)
def tickets():
    items = records().tickets()
    return {"tickets": sorted(items, key=lambda t: (t["status"] != "open", t["asked_at"]), reverse=False)}


@app.post("/api/tickets/{ticket_id}/answer", dependencies=guard)
def answer_ticket(ticket_id: str, body: AnswerRequest):
    try:
        t = records().answer_ticket(ticket_id, body.answer)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if not t:
        raise HTTPException(404, "Ticket not found.")
    section = library().add_sme_answer(t["question"], body.answer)
    return {"ticket": t, "section_id": section["id"]}


# ---------- insights and evaluation ----------
@app.get("/api/stats", dependencies=guard)
def stats():
    qs, fb, tk = records().questions(), records().feedback(), records().tickets()
    answered = [q for q in qs if q["status"] == "answered"]
    cited = defaultdict(int)
    for q in qs:
        for d in q.get("cited_docs", []):
            cited[d] += 1
    titles = {d["doc_id"]: d["title"] for d in library().documents()}
    n = len(qs)
    return {
        "questions": n,
        "answered_pct": round(100 * len(answered) / n) if n else None,
        "escalated": sum(q["status"] == "escalated" for q in qs),
        "open_tickets": sum(t["status"] == "open" for t in tk),
        "helpful_pct": round(100 * sum(f["rating"] == "helpful" for f in fb) / len(fb)) if fb else None,
        "ratings": len(fb),
        "avg_seconds": round(sum(q.get("seconds", 0) for q in qs) / n, 1) if n else None,
        "avg_steps": round(sum(q.get("steps", 0) for q in qs) / n, 1) if n else None,
        "tokens": sum(q.get("input_tokens", 0) + q.get("output_tokens", 0) for q in qs),
        "top_documents": [{"doc_id": d, "title": titles.get(d, d), "count": c}
                          for d, c in sorted(cited.items(), key=lambda x: -x[1])[:6]],
        "recent": list(reversed(qs[-8:])),
        "documents": len(library().documents()),
    }


@app.get("/api/evaluate", dependencies=guard)
def evaluate_default():
    return evaluate.run(evaluate.default_tests())


@app.post("/api/evaluate", dependencies=guard)
async def evaluate_custom(file: UploadFile = File(...)):
    text = (await file.read(1024 * 1024)).decode("utf-8", errors="replace")
    tests = evaluate.load_csv(text)
    if not tests:
        raise HTTPException(400, "The CSV needs columns: question, expected_doc")
    return evaluate.run(tests[:300])
