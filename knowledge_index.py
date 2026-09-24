"""
Phase 2: simple full-text knowledge index over cached pages + job artifacts.
No external vector DB required — substring/token search with ranking.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


class KnowledgeIndex:
    def __init__(self) -> None:
        self.cache_dir = settings.cache_dir
        self.jobs_dir = settings.data_dir / "jobs"

    def _iter_documents(self) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        if self.cache_dir.exists():
            for p in self.cache_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    md = data.get("markdown") or data.get("content") or ""
                    if not md:
                        continue
                    docs.append(
                        {
                            "source": "cache",
                            "url": data.get("url"),
                            "title": (data.get("metadata") or {}).get("title"),
                            "content_hash": data.get("content_hash"),
                            "markdown": md,
                            "path": str(p),
                        }
                    )
                except Exception:
                    continue
        if self.jobs_dir.exists():
            for job_dir in self.jobs_dir.iterdir():
                pages = job_dir / "pages"
                if not pages.is_dir():
                    continue
                for p in pages.glob("*.md"):
                    try:
                        text = p.read_text(encoding="utf-8")
                        docs.append(
                            {
                                "source": "job",
                                "job_id": job_dir.name,
                                "url": None,
                                "title": text.split("\n", 1)[0][:120],
                                "content_hash": None,
                                "markdown": text,
                                "path": str(p),
                            }
                        )
                    except Exception:
                        continue
        return docs

    def search(self, query: str, limit: int = 10, max_snippet: int = 400) -> Dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "query required", "hits": []}
        q_tokens = _tokens(q)
        q_lower = q.lower()
        hits: List[Dict[str, Any]] = []
        for doc in self._iter_documents():
            text = doc["markdown"]
            text_l = text.lower()
            score = 0.0
            if q_lower in text_l:
                score += 10.0
            if q_tokens:
                dt = _tokens(text)
                overlap = len(q_tokens & dt)
                score += overlap * 2.0
            if score <= 0:
                continue
            # snippet around first match
            idx = text_l.find(q_lower) if q_lower in text_l else 0
            start = max(0, idx - 80)
            snippet = text[start : start + max_snippet].replace("\n", " ")
            hits.append(
                {
                    "score": round(score, 2),
                    "url": doc.get("url"),
                    "title": doc.get("title"),
                    "source": doc.get("source"),
                    "job_id": doc.get("job_id"),
                    "content_hash": doc.get("content_hash"),
                    "snippet": snippet,
                }
            )
        hits.sort(key=lambda h: h["score"], reverse=True)
        return {"ok": True, "query": q, "hits": hits[:limit], "total_indexed": len(self._iter_documents())}


knowledge_index = KnowledgeIndex()
