"""Response cache, saved to disk so answers survive a restart (and a flaky venue network).

Exact matches are always used. Near-duplicate matches are opt-in per call (semantic=True), and a
near-duplicate must contain exactly the same numbers as the cached prompt, so "machine M-07 at 61 °C"
never reuses the answer written for "machine M-08 at 49 °C".
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import config

_NUM = re.compile(r"\d+(?:\.\d+)?")


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


class ResponseCache:
    def __init__(self, path: Path, threshold: float = 0.9):
        self.path = path
        self.threshold = threshold
        self._lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.entries = {}

    @staticmethod
    def _key(task: str, system: str, prompt: str) -> str:
        return hashlib.sha256(f"{task}\x00{_norm(system)}\x00{_norm(prompt)}".encode()).hexdigest()

    def get(self, task: str, system: str, prompt: str, semantic: bool = False) -> tuple[str, str, int, int] | None:
        """Return (text, "exact" | "semantic", tokens_in, tokens_out) or None."""
        hit = self.entries.get(self._key(task, system, prompt))
        if hit:
            return hit["text"], "exact", hit["tin"], hit["tout"]
        if not semantic:
            return None
        sys_n, nums, query = _norm(system), _NUM.findall(prompt), _norm(prompt)
        cands = [e for e in self.entries.values() if e["task"] == task and e["system"] == sys_n and e["nums"] == nums]
        if not cands:
            return None
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit([c["prompt"] for c in cands] + [query])
        sims = cosine_similarity(vec.transform([query]), vec.transform([c["prompt"] for c in cands]))[0]
        best = int(sims.argmax())
        if sims[best] >= self.threshold:
            c = cands[best]
            return c["text"], "semantic", c["tin"], c["tout"]
        return None

    def put(self, task: str, system: str, prompt: str, text: str, tokens_in: int, tokens_out: int) -> None:
        with self._lock:
            self.entries[self._key(task, system, prompt)] = {
                "task": task,
                "system": _norm(system),
                "prompt": _norm(prompt),
                "nums": _NUM.findall(prompt),
                "text": text,
                "tin": tokens_in,
                "tout": tokens_out,
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.entries), encoding="utf-8")
            tmp.replace(self.path)

    def clear(self) -> None:
        with self._lock:
            self.entries = {}
            self.path.unlink(missing_ok=True)


cache = ResponseCache(config.CACHE_DIR / "llm_cache.json", float(config.env("SEMANTIC_CACHE_THRESHOLD", "0.9")))
