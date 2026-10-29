# SOP Desk

An AI agent that answers employees' questions from approved SOPs and policies. It shows the exact section each answer came from, and sends questions the documents can't answer to an expert. When the expert replies, that answer joins the library, so the next person gets it with a citation.

Built with FastAPI and plain HTML/CSS/JS (no build step). Deploys to Render from GitHub.

## What's inside

**For employees (Desk)**
- Upload PDFs, Word files, Markdown or text by drag and drop. Each file is split into sections the agent can cite.
- Chat with the agent. Its work shows live as a route: *Searched → Read → Answered*.
- Numbered citations open the source on the right with the passage highlighted.
- Rate answers Helpful / Not helpful.

**For experts (Expert queue)**
- Questions the documents couldn't answer, with a box to reply.
- A published reply is added to the library as "Expert answers" and becomes searchable immediately.

**For the project lead (Insights)**
- Questions asked, % answered from documents, escalations, helpful rate, response time and tokens used.
- Most cited documents, and recent questions.
- Retrieval test: checks search finds the right document and refuses questions the documents don't cover. Free to run, and you can upload your own test CSV.

## How the agent works

The LLM makes the decisions; the code runs the tools and enforces the rules.

| Tool | What it does |
|---|---|
| `search_sops` | Keyword search (BM25 with domain synonyms) over all sections; returns the top 5 and a match label |
| `read_section` | Reads a full section; the agent must read before it answers |
| `escalate_to_sme` | Opens a ticket in the expert queue |
| `submit_answer` | Finishes with the answer and the section IDs it used |

**Guardrails enforced in code:**
- Only sections the agent actually read can be cited; any other citation is removed.
- An answer with no verified source is withheld and turned into an expert ticket.
- At most 6 agent steps per question.
- An optional access code, a rate limit, and upload size and type limits.

**Without an LLM key**, the app still works in retrieval-only mode. It shows the best-matching passage when the match is good and escalates when it isn't.

**On upload**, the LLM writes a one-line summary and three sample questions for each document. These appear as suggestions in the chat.

## Project layout

```
app/
  main.py       API routes (FastAPI)
  agent.py      Agent loop, system prompt, tools, guardrails
  llm.py        LLM adapters: Anthropic (Claude) and any OpenAI-compatible API
  search.py     BM25 search, synonyms, match label
  ingest.py     Reads PDF / DOCX / MD / TXT into sections
  store.py      Library, tickets, feedback, question log (JSON files)
  evaluate.py   Retrieval test
  config.py     Settings from environment variables
static/         index.html, styles.css, app.js (the UI)
data/samples/   8 sample SOPs for a fictional bank (loaded on first start)
data/synonyms.json, data/eval/test_questions.csv
tests/          pytest suite (uses a simulated LLM, no key needed)
render.yaml     Render deployment blueprint
```

## Run it on your laptop

You need Python 3.10 or later.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# open .env and paste your key after LLM_API_KEY=
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/docs.

Run the tests (no key needed):

```bash
pip install pytest
pytest -q
```

## Choosing the LLM

One key is enough. A key starting with `sk-ant-` uses Claude automatically. Any other key is treated as OpenAI-compatible.

| Provider | Settings |
|---|---|
| Anthropic (Claude) | `LLM_API_KEY=sk-ant-...` (model defaults to `claude-haiku-5-5`; change with `LLM_MODEL`) |
| OpenAI | `LLM_API_KEY=sk-...`, `LLM_MODEL=` a model on your account that supports tool calling |
| Groq, OpenRouter, Together, Gemini (OpenAI endpoint) | `LLM_PROVIDER=openai`, `LLM_BASE_URL=` the provider's OpenAI-compatible URL, `LLM_MODEL=` a tool-calling model |

The model must support tool (function) calling. If the key or model is wrong, the chat shows the provider's error message.

## Put it on GitHub

```bash
git init
git add .
git commit -m "SOP Desk: GenAI knowledge agent"
git branch -M main
git remote add origin https://github.com/<your-username>/sop-desk.git
git push -u origin main
```

`.env` and `storage/` are in `.gitignore`, so your key and uploaded files never go to GitHub. Check with `git status` before pushing.

## Deploy on Render

1. Sign in at render.com with GitHub.
2. Click **New → Blueprint** and pick the `sop-desk` repository. Render reads `render.yaml`.
3. When asked, paste your key into **LLM_API_KEY**. Set **ACCESS_CODE** to a code for pilot users, or leave it empty to keep the app open.
4. Click **Apply**. The first build takes a few minutes. Your app is live at `https://sop-desk-xxxx.onrender.com`.

Alternatively, choose **New → Web Service** and enter these settings yourself:
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Environment variables:** add the same ones under **Environment**.

**Things to know about Render's free plan:**
- The app sleeps after about 15 minutes without visits. The first visit after that takes up to a minute to wake it.
- The disk resets on every deploy or restart. Uploaded documents, tickets and feedback are lost, and the 8 samples reload. For a pilot, use a paid instance with a persistent disk (see the comment in `render.yaml`) and set `DATA_DIR=/var/data`.
- Anyone with the link can use your LLM credit. Set `ACCESS_CODE` and keep the default rate limit.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_API_KEY` | (empty) | LLM key. Empty = retrieval-only mode |
| `LLM_PROVIDER` | auto | `anthropic` or `openai` |
| `LLM_MODEL` | `claude-haiku-5-5` / `gpt-4o-mini` | Model used by the agent |
| `LLM_BASE_URL` | provider default | For other OpenAI-compatible providers |
| `ACCESS_CODE` | (empty) | Code users must enter |
| `MAX_UPLOAD_MB` | 10 | Upload size limit per file |
| `MAX_DOCUMENTS` | 60 | Most uploaded documents kept |
| `RATE_LIMIT` | 30 | Questions per person per 10 minutes |
| `ENRICH_UPLOADS` | 1 | LLM writes summaries and sample questions for uploads |
| `DATA_DIR` | `./storage` | Where the library, tickets and feedback are saved |

## Data and safety

- Upload only documents you are approved to use, and only send them to an LLM provider your company allows.
- Don't upload customer data. The agent is told never to repeat account, card, Aadhaar or PAN numbers.
- Scanned PDFs (images) have no text to read. Use the original Word or text version.

## API

All endpoints are listed at `/docs`. The main ones:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/documents` | Upload files (multipart field `files`) |
| GET | `/api/documents` | List documents |
| POST | `/api/chat` | Ask a question; streams agent steps as server-sent events |
| GET / POST | `/api/tickets`, `/api/tickets/{id}/answer` | Expert queue |
| POST | `/api/feedback` | Helpful / not helpful |
| GET | `/api/stats` | Insights |
| GET / POST | `/api/evaluate` | Retrieval test (default set or your CSV) |
