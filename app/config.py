"""All settings come from environment variables (set them in Render's dashboard or a local .env file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ---- Files ----
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "storage")))     # library, tickets, feedback
SAMPLES_DIR = BASE_DIR / "data" / "samples"                            # loaded on first start
SYNONYMS_FILE = BASE_DIR / "data" / "synonyms.json"
TEST_SET = BASE_DIR / "data" / "eval" / "test_questions.csv"
STATIC_DIR = BASE_DIR / "static"

# ---- LLM ----
# One key is enough. Keys starting with "sk-ant-" use Anthropic (Claude); anything else is treated as an
# OpenAI-compatible API (OpenAI, Groq, OpenRouter, Together, Gemini's OpenAI endpoint ...).
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or
                ("anthropic" if LLM_API_KEY.startswith("sk-ant-") else ("openai" if LLM_API_KEY else ""))).lower()
_DEFAULT_MODEL = {"anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini"}
_DEFAULT_URL = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}
LLM_MODEL = os.getenv("LLM_MODEL") or _DEFAULT_MODEL.get(LLM_PROVIDER, "")
LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or _DEFAULT_URL.get(LLM_PROVIDER, "")).rstrip("/")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
ENRICH_UPLOADS = os.getenv("ENRICH_UPLOADS", "1") == "1"   # LLM writes a summary + sample questions per upload

# ---- Protection for a public deployment ----
ACCESS_CODE = os.getenv("ACCESS_CODE", "").strip()          # if set, the UI asks for it
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_DOCUMENTS = int(os.getenv("MAX_DOCUMENTS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "30"))             # questions per user per 10 minutes
MAX_QUESTION_CHARS = 600

# ---- Agent and retrieval ----
MAX_AGENT_STEPS = 6
TOP_K = 5
MIN_COVERAGE = 0.5      # share of question words found in the best section
MIN_BM25 = 2.0          # minimum keyword score of the best section
PDF_CHUNK_WORDS = 220
ALLOWED_TYPES = {".pdf", ".docx", ".md", ".txt"}
