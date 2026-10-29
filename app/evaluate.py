"""Retrieval evaluation: does search find the right document, and does the confidence gate refuse
questions the documents don't cover? Free to run (no LLM calls).

Test set CSV columns: question, expected_doc   (expected_doc = NONE when the documents shouldn't answer it)
"""
import csv
import io

from . import config
from .store import library


def load_csv(text: str):
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        q = (r.get("question") or "").strip()
        if q:
            exp = (r.get("expected_doc") or "NONE").strip()
            rows.append({"question": q, "expected": None if exp.upper() == "NONE" else exp.upper()})
    return rows


def default_tests():
    return load_csv(config.TEST_SET.read_text(encoding="utf-8"))


def run(tests):
    lib = library()
    rows = []
    for t in tests:
        results, label = lib.search(t["question"])
        docs = [r["doc_id"] for r in results]
        answers = label in ("Strong", "Good")
        if t["expected"]:
            top1, top3 = docs[:1] == [t["expected"]], t["expected"] in docs[:3]
            correct = answers and top1
        else:
            top1 = top3 = None
            correct = not answers
        rows.append({"question": t["question"], "expected": t["expected"] or "NONE", "top_doc": docs[0] if docs else "",
                     "top_section": results[0]["section"] if results else "", "match": label,
                     "score": results[0]["score"] if results else 0, "top1": top1, "top3": top3, "correct": correct})
    inscope = [r for r in rows if r["expected"] != "NONE"]
    outscope = [r for r in rows if r["expected"] == "NONE"]

    def pct(n, d):
        return round(100 * n / d) if d else None

    missing = sorted({r["expected"] for r in inscope} - {d["doc_id"] for d in lib.documents()})
    return {
        "summary": {
            "questions": len(rows), "in_scope": len(inscope), "out_of_scope": len(outscope),
            "top1": pct(sum(bool(r["top1"]) for r in inscope), len(inscope)),
            "top3": pct(sum(bool(r["top3"]) for r in inscope), len(inscope)),
            "refusals": pct(sum(r["correct"] for r in outscope), len(outscope)),
            "overall": pct(sum(r["correct"] for r in rows), len(rows)),
        },
        "missing_documents": missing,
        "rows": rows,
    }
