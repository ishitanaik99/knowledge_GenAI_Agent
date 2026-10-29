"""Keyword search (BM25) with domain synonyms and a confidence label.

BM25 is light enough for a free Render instance (no embedding model to download or hold in memory).
The agent makes up for paraphrases by searching again with different words when a match is weak.
"""
import json
import re

from rank_bm25 import BM25Okapi

from . import config

STOP = set("""a an the is are was were be been to of in on for and or with by at from as it this that these
those i me my we our you your how what when where which who whom why do does did can could should would will
shall may might must if then than there here about into over under any all some no not only also per via get
got have has had need needs""".split())


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 4 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokenize(text: str):
    return [stem(w) for w in re.sub(r"[^a-z0-9\s]", " ", str(text).lower()).split() if w and w not in STOP]


def load_synonyms():
    try:
        raw = json.loads(config.SYNONYMS_FILE.read_text(encoding="utf-8"))
        return {stem(k): [t for v in vals for t in tokenize(v)] for k, vals in raw.items()}
    except FileNotFoundError:
        return {}


class SearchIndex:
    def __init__(self, sections, synonyms=None):
        self.sections = sections
        self.synonyms = synonyms if synonyms is not None else load_synonyms()
        self.tokens = [tokenize(f"{s['title']} {s['title']} {s['section']} {s['text']}") for s in sections]
        self.token_sets = [set(t) for t in self.tokens]
        self.bm25 = BM25Okapi(self.tokens, k1=1.2, b=0.75) if sections else None

    def search(self, query: str, k: int = config.TOP_K):
        if not self.bm25:
            return []
        base = tokenize(query)
        expanded = list(base)
        for w in base:
            expanded += [s for s in self.synonyms.get(w, []) if s not in expanded]
        if not expanded:
            return []
        scores = self.bm25.get_scores(expanded)
        order = sorted(range(len(self.sections)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order[:k]:
            if scores[i] <= 0:
                break
            toks = self.token_sets[i]
            hit = sum(1 for w in base if w in toks or any(s in toks for s in self.synonyms.get(w, [])))
            out.append({**self.sections[i], "score": round(float(scores[i]), 2),
                        "coverage": round(hit / len(base), 2) if base else 0.0})
        return out

    @staticmethod
    def confidence(results) -> str:
        if not results:
            return "No match"
        top = results[0]
        if top["score"] >= config.MIN_BM25 and top["coverage"] >= config.MIN_COVERAGE:
            return "Strong" if top["coverage"] >= 0.66 and top["score"] >= 5 else "Good"
        return "Weak"
