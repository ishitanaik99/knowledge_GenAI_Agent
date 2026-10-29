"""Where everything is kept: the document library, SME tickets, feedback and a question log.

Plain JSON files under DATA_DIR, guarded by a lock. Simple, readable, and enough for a pilot.
On Render's free plan the disk resets on each deploy or restart; add a persistent disk to keep uploads.
"""
import json
import threading
import uuid
from datetime import date, datetime, timezone

from . import config
from .ingest import parse_file
from .search import SearchIndex

_lock = threading.RLock()
SME_DOC_ID = "SME-ANSWERS"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(name, default):
    path = config.DATA_DIR / name
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write(name, data):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = config.DATA_DIR / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(config.DATA_DIR / name)


class Library:
    def __init__(self):
        with _lock:
            data = _read("library.json", None)
            if data is None:
                data = {"documents": {}, "sections": []}
                self._data = data
                self._load_samples()
            else:
                self._data = data
                self._rebuild()

    # ---- internal ----
    def _rebuild(self):
        self.by_id = {s["id"]: s for s in self._data["sections"]}
        self.index = SearchIndex(self._data["sections"])

    def _save(self):
        _write("library.json", self._data)
        self._rebuild()

    def _load_samples(self):
        for path in sorted(config.SAMPLES_DIR.glob("*.md")):
            doc, sections = parse_file(path.name, path.read_bytes(), kind="sample")
            self._put(doc, sections)
        self._save()

    def _put(self, doc, sections):
        self._data["documents"][doc["doc_id"]] = doc
        self._data["sections"] = [s for s in self._data["sections"] if s["doc_id"] != doc["doc_id"]] + sections

    # ---- public ----
    def documents(self):
        with _lock:
            docs = list(self._data["documents"].values())
        order = {"upload": 0, "sme": 1, "sample": 2}
        return sorted(docs, key=lambda d: (order.get(d.get("kind"), 3), d["doc_id"]))

    def document(self, doc_id):
        with _lock:
            doc = self._data["documents"].get(doc_id)
            if not doc:
                return None
            return {**doc, "sections": [s for s in self._data["sections"] if s["doc_id"] == doc_id]}

    def section(self, section_id):
        return self.by_id.get(section_id)

    def add(self, doc, sections):
        with _lock:
            uploads = [d for d in self._data["documents"].values() if d.get("kind") == "upload"]
            if doc["doc_id"] not in self._data["documents"] and len(uploads) >= config.MAX_DOCUMENTS:
                raise ValueError(f"Library is full ({config.MAX_DOCUMENTS} uploaded documents). Remove one first.")
            self._put(doc, sections)
            self._save()
        return doc

    def update_meta(self, doc_id, **fields):
        with _lock:
            if doc_id in self._data["documents"]:
                self._data["documents"][doc_id].update(fields)
                if "title" in fields:
                    for s in self._data["sections"]:
                        if s["doc_id"] == doc_id:
                            s["title"] = fields["title"]
                self._save()

    def delete(self, doc_id):
        with _lock:
            if doc_id not in self._data["documents"]:
                return False
            del self._data["documents"][doc_id]
            self._data["sections"] = [s for s in self._data["sections"] if s["doc_id"] != doc_id]
            self._save()
            return True

    def reset(self):
        with _lock:
            self._data = {"documents": {}, "sections": []}
            self._load_samples()

    def search(self, query, k=config.TOP_K):
        with _lock:
            results = self.index.search(query, k)
        return results, SearchIndex.confidence(results)

    def add_sme_answer(self, question, answer):
        """Expert answers become a searchable document: the knowledge refresh loop."""
        with _lock:
            doc = self._data["documents"].get(SME_DOC_ID) or {
                "doc_id": SME_DOC_ID, "title": "Expert answers", "owner": "SME panel", "version": "live",
                "source": "SME queue", "kind": "sme", "summary": "Answers published by experts from the SME queue.",
                "questions": [], "uploaded_at": _now()}
            existing = [s for s in self._data["sections"] if s["doc_id"] == SME_DOC_ID]
            n = len(existing) + 1
            section = {"id": f"{SME_DOC_ID}-S{n:02d}", "doc_id": SME_DOC_ID, "title": doc["title"],
                       "section": question.strip()[:120], "text": answer.strip()}
            doc.update(updated=f"{date.today():%b %Y}", section_count=n,
                       word_count=sum(len(s["text"].split()) for s in existing + [section]))
            self._data["documents"][SME_DOC_ID] = doc
            self._data["sections"].append(section)
            self._save()
            return section


class Records:
    """SME tickets, feedback and a log of every question (for the Insights tab)."""

    def tickets(self):
        return _read("tickets.json", [])

    def open_ticket(self, question, reason):
        with _lock:
            tickets = self.tickets()
            ticket = {"ticket": "SME-" + uuid.uuid4().hex[:6].upper(), "question": question.strip(),
                      "reason": reason.strip(), "status": "open", "asked_at": _now(), "answer": "", "answered_at": ""}
            tickets.append(ticket)
            _write("tickets.json", tickets)
            return ticket

    def answer_ticket(self, ticket_id, answer):
        with _lock:
            tickets = self.tickets()
            for t in tickets:
                if t["ticket"] == ticket_id:
                    if t["status"] == "answered":
                        raise ValueError("This ticket is already answered.")
                    t.update(status="answered", answer=answer.strip(), answered_at=_now())
                    _write("tickets.json", tickets)
                    return t
            return None

    def add_feedback(self, item):
        with _lock:
            items = _read("feedback.json", [])
            items.append({**item, "at": _now()})
            _write("feedback.json", items[-5000:])

    def feedback(self):
        return _read("feedback.json", [])

    def log_question(self, entry):
        with _lock:
            log = _read("questions.json", [])
            log.append({**entry, "at": _now()})
            _write("questions.json", log[-5000:])

    def questions(self):
        return _read("questions.json", [])


_library = None
_records = Records()


def library() -> Library:
    global _library
    with _lock:
        if _library is None:
            _library = Library()
        return _library


def records() -> Records:
    return _records


def reset_singletons():
    """Used by tests."""
    global _library
    _library = None
