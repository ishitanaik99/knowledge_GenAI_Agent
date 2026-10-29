"""The agent. The LLM makes the decisions (which tool, what to search, when to answer or escalate);
this code runs the tools and enforces the guardrails.

run() is a generator: it yields events as the agent works, which the API streams to the browser.
  {"type": "step", "tool": ..., "label": ..., "detail": ...}
  {"type": "final", "result": {...}}
"""
import json
import re
import time

from . import config
from .llm import LLMError
from .store import library, records

SYSTEM_PROMPT = """You are SOP Desk, the internal knowledge assistant for a BFSI (banking and financial services) team.
You answer employees' questions using ONLY the company's approved documents, which you reach through tools.

How to work:
1. Always call search_sops first. If the match is Weak or No match, search once or twice more with different words
   (synonyms, the formal term, the document's likely wording).
2. Call read_section on the most relevant section(s) before answering. Never answer from snippets alone.
3. Answer ONLY from text returned by read_section. Never use outside knowledge, even when you know the answer.
4. If the sections don't clearly answer the question, call escalate_to_sme and then submit_answer with status "escalated".
5. Finish by calling submit_answer exactly once.

Answer style: under 120 words, plain words, steps on numbered lines, exact figures (days, amounts, times) as written.
Put the section_id in square brackets right after each fact it supports, e.g. "Cards are dispatched within 7 working
days [SOP-CARD-007-S02]."

Data rule: never ask for or repeat customer data (account, card, Aadhaar or PAN numbers), even if the user gives it."""

TOOLS = [
    {"name": "search_sops",
     "description": "Search the approved documents. Returns up to 5 sections (section_id, document, heading, snippet) "
                    "and a match label: Strong, Good, Weak or No match. Use short keyword-style queries.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_section",
     "description": "Read the full text of one section by its section_id from search_sops. Read before answering.",
     "parameters": {"type": "object", "properties": {"section_id": {"type": "string"}}, "required": ["section_id"]}},
    {"name": "escalate_to_sme",
     "description": "Send the question to a subject-matter expert when the documents don't contain the answer, "
                    "or only part of it. Returns a ticket id.",
     "parameters": {"type": "object", "properties": {
         "question": {"type": "string", "description": "The employee's question"},
         "reason": {"type": "string", "description": "What you searched and why it wasn't enough"}},
         "required": ["question", "reason"]}},
    {"name": "submit_answer",
     "description": "Finish. status 'answered' needs the answer with [section_id] markers and the list of section_ids "
                    "used. status 'escalated' means escalate_to_sme was called; give a one-line note in answer.",
     "parameters": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["answered", "escalated"]},
         "answer": {"type": "string"},
         "citations": {"type": "array", "items": {"type": "string"}}},
         "required": ["status", "answer", "citations"]}},
]

CITE_RE = re.compile(r"\[([A-Z0-9][A-Z0-9-]*-S\d{2,})\]")


def _search(query):
    results, label = library().search(query)
    return {"match": label, "results": [
        {"section_id": r["id"], "document": f"{r['doc_id']} | {r['title']}", "heading": r["section"],
         "snippet": r["text"][:240] + ("..." if len(r["text"]) > 240 else "")} for r in results]}


def _read(section_id):
    s = library().section(str(section_id).strip())
    if not s:
        raise ValueError(f"No section with id {section_id}. Use a section_id returned by search_sops.")
    doc = library().document(s["doc_id"]) or {}
    return {"section_id": s["id"], "document": f"{s['doc_id']} | {s['title']}", "heading": s["section"],
            "owner": doc.get("owner", ""), "version": doc.get("version", ""), "text": s["text"]}


def _label(name, args, output):
    if name == "search_sops":
        match = output.get("match", "") if isinstance(output, dict) else ""
        top = output["results"][0]["document"].split(" | ")[0] if isinstance(output, dict) and output.get("results") else ""
        return f"Searched “{args.get('query', '')}”", f"{match}{' · top: ' + top if top else ''}"
    if name == "read_section":
        if isinstance(output, dict):
            return f"Read {output['document'].split(' | ')[0]} › {output['heading']}", output["section_id"]
        return "Tried to read a section", "not found"
    if name == "escalate_to_sme":
        return "Sent to an expert", output.get("ticket", "") if isinstance(output, dict) else ""
    return name, ""


def _context(question, history):
    """Short memory for follow-ups like 'and for premium accounts?'."""
    lines = []
    for turn in (history or [])[-2:]:
        q, a = str(turn.get("question", ""))[:300], str(turn.get("answer", ""))[:400]
        if q:
            lines.append(f"Earlier question: {q}\nEarlier answer: {a}")
    prefix = ("Conversation so far (for context only; still search the documents):\n" + "\n".join(lines) + "\n\n") if lines else ""
    return prefix + f"Question: {question}"


def run(question, history=None, llm=None):
    started = time.time()
    state = {"question": question, "status": "error", "answer": "", "citations": [], "ticket": "", "note": "",
             "steps": [], "input_tokens": 0, "output_tokens": 0, "mode": "agent"}
    if llm is None:
        yield from _retrieval_only(question, state, started)
        return

    transcript = [{"role": "user", "text": _context(question, history)}]
    read_ids, final, nudged = set(), None, False
    try:
        for _ in range(config.MAX_AGENT_STEPS + 1):
            reply = llm.chat(SYSTEM_PROMPT, transcript, TOOLS, force_tools=True)
            state["input_tokens"] += reply.input_tokens
            state["output_tokens"] += reply.output_tokens
            transcript.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls})
            if not reply.tool_calls:
                if nudged:
                    break
                nudged = True
                transcript.append({"role": "user", "text": "Use the tools, and finish by calling submit_answer."})
                continue
            results = []
            for call in reply.tool_calls:
                name, args = call["name"], call.get("input") or {}
                if name == "submit_answer":
                    final = args
                    continue
                try:
                    if name == "search_sops":
                        output = _search(str(args.get("query", "")))
                    elif name == "read_section":
                        output = _read(args.get("section_id", ""))
                        read_ids.add(output["section_id"])
                    elif name == "escalate_to_sme":
                        t = records().open_ticket(question, str(args.get("reason", "Not found in the documents.")))
                        state["ticket"] = t["ticket"]
                        output = {"ticket": t["ticket"], "message": "Sent to the SME queue."}
                    else:
                        raise ValueError(f"Unknown tool {name}")
                    results.append({"id": call["id"], "name": name, "content": json.dumps(output)})
                except Exception as e:
                    output = f"Error: {e}"
                    results.append({"id": call["id"], "name": name, "content": output, "is_error": True})
                label, detail = _label(name, args, output)
                step = {"tool": name, "label": label, "detail": detail}
                state["steps"].append(step)
                yield {"type": "step", **step}
            if final is not None:
                break
            transcript.append({"role": "tool_results", "results": results})
    except LLMError as e:
        state.update(status="error", note=str(e))
        yield {"type": "final", "result": _finish(state, started)}
        return

    _guardrail(state, final, read_ids)
    if state["status"] == "escalated" and state["ticket"] and not any(s["tool"] == "escalate_to_sme" for s in state["steps"]):
        step = {"tool": "escalate_to_sme", "label": "Sent to an expert", "detail": state["ticket"]}
        state["steps"].append(step)
        yield {"type": "step", **step}
    yield {"type": "final", "result": _finish(state, started)}


def _guardrail(state, final, read_ids):
    """Rules enforced in code: only sections the agent read can be cited; no verified source = no answer."""
    if final is None:
        final = {"status": "answered", "answer": "", "citations": []}
        state["note"] = "The agent didn't finish within its step limit."
    lib = library()
    cited = [c for c in final.get("citations") or [] if c in read_ids and lib.section(c)]
    for c in CITE_RE.findall(final.get("answer") or ""):          # markers used inline also count, if read
        if c in read_ids and c not in cited and lib.section(c):
            cited.append(c)
    answer = (final.get("answer") or "").strip()
    answer = CITE_RE.sub(lambda m: m.group(0) if m.group(1) in cited else "", answer)   # drop unverified markers
    status = final.get("status") if final.get("status") in ("answered", "escalated") else "answered"
    if status == "answered" and not cited:
        if not state["ticket"]:
            state["ticket"] = records().open_ticket(state["question"], "Agent could not verify an answer in the documents.")["ticket"]
        status = "escalated"
        answer = "I couldn't verify an answer in the approved documents, so I've sent your question to an expert."
        state["note"] = state["note"] or "Guardrail: no verified source, so the answer was withheld."
    if status == "escalated" and not state["ticket"]:
        state["ticket"] = records().open_ticket(state["question"], "Not found in the documents.")["ticket"]
    state.update(status=status, answer=answer or "Sent to an expert.", citations=[
        {"section_id": c, "doc_id": lib.section(c)["doc_id"], "title": lib.section(c)["title"],
         "heading": lib.section(c)["section"]} for c in cited])


def _retrieval_only(question, state, started):
    """No LLM key: answer with the best-matching passage when confident, otherwise escalate."""
    state["mode"] = "retrieval-only"
    results, label = library().search(question)
    step = {"tool": "search_sops", "label": f"Searched “{question[:60]}”",
            "detail": f"{label}{' · top: ' + results[0]['doc_id'] if results else ''}"}
    state["steps"].append(step)
    yield {"type": "step", **step}
    if label in ("Strong", "Good"):
        top = results[0]
        state.update(status="answered", answer=f"{top['text']} [{top['id']}]",
                     citations=[{"section_id": top["id"], "doc_id": top["doc_id"], "title": top["title"],
                                 "heading": top["section"]}],
                     note="Retrieval-only mode: showing the best-matching passage. Add an LLM key for written answers.")
    else:
        t = records().open_ticket(question, f"Retrieval-only mode; match was {label}.")
        state.update(status="escalated", ticket=t["ticket"],
                     answer="I couldn't find this in the approved documents, so I've sent it to an expert.")
        step = {"tool": "escalate_to_sme", "label": "Sent to an expert", "detail": t["ticket"]}
        state["steps"].append(step)
        yield {"type": "step", **step}
    yield {"type": "final", "result": _finish(state, started)}


def _finish(state, started):
    state["seconds"] = round(time.time() - started, 1)
    records().log_question({k: state[k] for k in ("question", "status", "ticket", "mode", "seconds",
                                                    "input_tokens", "output_tokens")}
                           | {"steps": len(state["steps"]), "cited_docs": sorted({c["doc_id"] for c in state["citations"]})})
    return state


def enrich_document(doc_id, llm):
    """After an upload, ask the LLM for a one-line summary and 3 questions the document answers."""
    doc = library().document(doc_id)
    if not doc or llm is None:
        return
    excerpt = "\n".join(f"## {s['section']}\n{s['text']}" for s in doc["sections"])[:6000]
    prompt = (f"Here is an internal document titled \"{doc['title']}\".\n\n{excerpt}\n\n"
              'Return JSON: {"title": "a clear title, max 8 words", "summary": "one sentence, max 25 words", '
              '"questions": ["3 short questions an employee might ask that this document answers"]}')
    try:
        data = llm.complete_json(prompt)
        fields = {"summary": str(data.get("summary", ""))[:200],
                  "questions": [str(q)[:140] for q in (data.get("questions") or [])][:3]}
        if doc.get("title_auto") and data.get("title"):        # only replace titles guessed from the filename
            fields["title"] = str(data["title"])[:80]
            fields["title_auto"] = False
        library().update_meta(doc_id, **fields)
    except Exception:
        pass   # enrichment is a nice-to-have; never block an upload
