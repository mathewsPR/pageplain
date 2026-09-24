"""
Phase 1 knowledge service: multi-job isolation, policy, export, usage.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger

from browser_pool import BrowserPool
from cache import ContentCache
from config import settings
from crawler import Crawler
from models import Task
from policy import policy
from robots import RobotsManager
from router import Router
from state import StateManager
from usage_stats import usage
from knowledge_index import knowledge_index


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n\n…[truncated]…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeService:
    def __init__(self) -> None:
        self._started = False
        self._lock = asyncio.Lock()
        self.browser_pool: Optional[BrowserPool] = None
        self.cache = ContentCache()
        self.state: Optional[StateManager] = None
        self.router: Optional[Router] = None
        self.crawler: Optional[Crawler] = None
        self.robots = RobotsManager()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self.jobs_dir = settings.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._load_jobs_index()

    def _load_jobs_index(self) -> None:
        for p in self.jobs_dir.glob("*/job.json"):
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
                self._jobs[job["id"]] = job
            except Exception:
                continue

    def _job_dir(self, job_id: str) -> Path:
        d = self.jobs_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist_job(self, job: Dict[str, Any]) -> None:
        path = self._job_dir(job["id"]) / "job.json"
        path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")

    async def start(self) -> None:
        async with self._lock:
            if self._started:
                return
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            settings.output_dir.mkdir(parents=True, exist_ok=True)
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
            self.state = StateManager()
            await self.state.open()
            self.browser_pool = BrowserPool()
            await self.browser_pool.start()
            self.router = Router(self.browser_pool, self.cache)
            self.crawler = Crawler(self.state, self.router, self.robots, self.cache)
            self._started = True
            logger.info("KnowledgeService started")

    async def stop(self) -> None:
        async with self._lock:
            if self.browser_pool:
                await self.browser_pool.close()
            if self.state:
                await self.state.close()
            self._started = False
            logger.info("KnowledgeService stopped")

    async def scrape_url(
        self,
        url: str,
        max_chars: int = 50000,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        await self.start()
        assert self.crawler

        allowed, reason = policy.check_url(url)
        if not allowed:
            usage.record_scrape(False, from_cache=False, bytes_out=0)
            return {
                "ok": False,
                "url": url,
                "from_cache": False,
                "reason": reason.upper() if reason != "ok" else "DENIED",
                "markdown": "",
                "error": f"policy denied: {reason}",
            }

        if not force_refresh:
            cached = self.cache.get(url)
            if cached and cached.success:
                md = _truncate(cached.markdown or cached.content, max_chars)
                usage.record_scrape(True, from_cache=True, bytes_out=len(md))
                return {
                    "ok": True,
                    "url": url,
                    "from_cache": True,
                    "reason": "CACHED",
                    "path_used": "cache",
                    "title": cached.metadata.title if cached.metadata else None,
                    "markdown": md,
                    "content_hash": cached.content_hash,
                    "chars": len(md),
                }

        domain = urlparse(url).netloc
        task = Task(url=url, domain=domain, depth=0)
        result = await self.crawler.process(task)
        md = _truncate(result.markdown or result.content, max_chars) if result.success else ""
        usage.record_scrape(result.success, from_cache=False, bytes_out=len(md))

        out: Dict[str, Any] = {
            "ok": result.success,
            "url": url,
            "from_cache": False,
            "reason": result.reason.value,
            "path_used": result.path_used or None,
            "title": result.metadata.title if result.metadata else None,
            "markdown": md,
            "content_hash": result.content_hash,
            "chars": len(md),
            "error": result.error_message,
            "status_code": result.status_code,
        }
        if result.success and settings.research_mode:
            out["research"] = result.research or {
                "crawled_at": result.timestamp.isoformat(),
                "content_hash": result.content_hash,
                "path_used": result.path_used,
            }
        return out

    async def get_cached(self, url: str, max_chars: int = 50000) -> Dict[str, Any]:
        cached = self.cache.get(url)
        if not cached or not cached.success:
            return {"ok": False, "url": url, "from_cache": False, "reason": "MISS", "markdown": ""}
        md = _truncate(cached.markdown or cached.content, max_chars)
        usage.record_scrape(True, from_cache=True, bytes_out=len(md))
        return {
            "ok": True,
            "url": url,
            "from_cache": True,
            "reason": "CACHED",
            "title": cached.metadata.title if cached.metadata else None,
            "markdown": md,
            "content_hash": cached.content_hash,
            "chars": len(md),
        }

    async def create_job(
        self,
        urls: List[str],
        max_pages: int = 10,
        max_depth: int = 0,
        name: str = "",
        team: str = "default",
    ) -> Dict[str, Any]:
        await self.start()
        # Filter by policy
        accepted, denied = [], []
        for u in urls:
            ok, reason = policy.check_url(u)
            if ok:
                accepted.append(u)
            else:
                denied.append({"url": u, "reason": reason})

        job_id = str(uuid.uuid4())[:12]
        job_dir = self._job_dir(job_id)
        (job_dir / "pages").mkdir(exist_ok=True)

        job: Dict[str, Any] = {
            "id": job_id,
            "name": name or f"job-{job_id}",
            "team": team,
            "urls": accepted,
            "denied_urls": denied,
            "max_pages": min(max(1, max_pages), 100),
            "max_depth": max_depth,
            "status": "pending",
            "created_at": _now(),
            "finished_at": None,
            "results": [],
            "error": None,
            "dir": str(job_dir),
        }
        self._jobs[job_id] = job
        self._persist_job(job)
        usage.record_job_created()
        asyncio.create_task(self._run_job(job_id))
        return {
            "ok": True,
            "job_id": job_id,
            "status": "pending",
            "name": job["name"],
            "accepted": len(accepted),
            "denied": len(denied),
        }

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job["status"] = "running"
        self._persist_job(job)
        results: List[Dict[str, Any]] = []
        pages_dir = self._job_dir(job_id) / "pages"
        try:
            pages = 0
            for url in job["urls"]:
                if pages >= job["max_pages"]:
                    break
                r = await self.scrape_url(url, max_chars=100_000)
                results.append(r)
                if r.get("ok") and r.get("markdown"):
                    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in url)[:120]
                    (pages_dir / f"{safe}.md").write_text(
                        f"# {r.get('title') or url}\n\n{r['markdown']}", encoding="utf-8"
                    )
                pages += 1
            job["results"] = results
            job["status"] = "completed"
            job["finished_at"] = _now()
            usage.record_job_completed()
            self._persist_job(job)
            self.export_job_zip(job_id)
        except Exception as e:
            logger.exception(f"Job {job_id} failed")
            job["status"] = "failed"
            job["error"] = str(e)
            job["finished_at"] = _now()
            job["results"] = results
            self._persist_job(job)

    async def get_job(self, job_id: str) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if not job:
            path = self._job_dir(job_id) / "job.json"
            if path.exists():
                job = json.loads(path.read_text(encoding="utf-8"))
                self._jobs[job_id] = job
            else:
                return {"ok": False, "error": f"job not found: {job_id}"}
        summary = {k: v for k, v in job.items() if k != "results"}
        summary["ok"] = True
        summary["result_count"] = len(job.get("results") or [])
        return summary

    async def list_jobs(self, limit: int = 50) -> Dict[str, Any]:
        items = sorted(
            self._jobs.values(),
            key=lambda j: j.get("created_at") or "",
            reverse=True,
        )[:limit]
        out = []
        for j in items:
            out.append(
                {
                    "id": j["id"],
                    "name": j.get("name"),
                    "team": j.get("team"),
                    "status": j.get("status"),
                    "created_at": j.get("created_at"),
                    "finished_at": j.get("finished_at"),
                    "result_count": len(j.get("results") or []),
                    "accepted": len(j.get("urls") or []),
                    "denied": len(j.get("denied_urls") or []),
                }
            )
        return {"ok": True, "jobs": out}

    async def get_job_results(
        self, job_id: str, max_chars_per_page: int = 20000
    ) -> Dict[str, Any]:
        job = self._jobs.get(job_id)
        if not job:
            path = self._job_dir(job_id) / "job.json"
            if path.exists():
                job = json.loads(path.read_text(encoding="utf-8"))
            else:
                return {"ok": False, "error": f"job not found: {job_id}"}
        results = []
        for r in job.get("results") or []:
            item = dict(r)
            if item.get("markdown"):
                item["markdown"] = _truncate(item["markdown"], max_chars_per_page)
            results.append(item)
        return {
            "ok": True,
            "job_id": job_id,
            "status": job.get("status"),
            "results": results,
        }

    def export_job_zip(self, job_id: str) -> Optional[Path]:
        job_dir = self._job_dir(job_id)
        if not (job_dir / "job.json").exists():
            return None
        zip_path = job_dir / f"{job_id}_export.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(job_dir / "job.json", "job.json")
            pages = job_dir / "pages"
            if pages.exists():
                for f in pages.glob("*.md"):
                    zf.write(f, f"pages/{f.name}")
        return zip_path

    def get_export_path(self, job_id: str) -> Optional[Path]:
        p = self._job_dir(job_id) / f"{job_id}_export.zip"
        if p.exists():
            return p
        return self.export_job_zip(job_id)

    def get_usage(self) -> Dict[str, Any]:
        return {"ok": True, **usage.snapshot()}

    def get_policy(self) -> Dict[str, Any]:
        return {"ok": True, **policy.to_dict()}

    def update_policy(
        self,
        allowlist: Optional[List[str]] = None,
        denylist: Optional[List[str]] = None,
        allowlist_enabled: Optional[bool] = None,
        skip_hard_domains: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if allowlist is not None:
            policy.allowlist = set(allowlist)
        if denylist is not None:
            policy.denylist = set(denylist)
        if allowlist_enabled is not None:
            policy.allowlist_enabled = allowlist_enabled
        if skip_hard_domains is not None:
            policy.skip_hard_domains = skip_hard_domains
        policy.save()
        return {"ok": True, **policy.to_dict()}


    def search_knowledge(self, query: str, limit: int = 10) -> Dict[str, Any]:
        return knowledge_index.search(query, limit=limit)

    async def get_diff(self, url: str, max_chars: int = 30000) -> Dict[str, Any]:
        """Re-fetch URL and compare content hash to cache (change detection)."""
        await self.start()
        old = self.cache.get(url)
        old_hash = old.content_hash if old and old.success else None
        old_md = (old.markdown or old.content) if old and old.success else ""

        fresh = await self.scrape_url(url, max_chars=max_chars, force_refresh=True)
        new_hash = fresh.get("content_hash")
        new_md = fresh.get("markdown") or ""

        changed = bool(old_hash and new_hash and old_hash != new_hash)
        first_seen = old_hash is None and fresh.get("ok")

        return {
            "ok": fresh.get("ok", False),
            "url": url,
            "changed": changed,
            "first_seen": first_seen,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "reason": fresh.get("reason"),
            "title": fresh.get("title"),
            "old_chars": len(old_md),
            "new_chars": len(new_md),
            "markdown": new_md if (changed or first_seen) else "",
            "error": fresh.get("error"),
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Phase 2 observability snapshot."""
        u = usage.snapshot()
        jobs = list(self._jobs.values())
        by_status: Dict[str, int] = {}
        for j in jobs:
            s = j.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        cache_files = 0
        if settings.cache_dir.exists():
            cache_files = len(list(settings.cache_dir.glob("*.json")))
        return {
            "ok": True,
            "usage": u,
            "jobs_by_status": by_status,
            "jobs_total": len(jobs),
            "cache_entries": cache_files,
            "policy": policy.to_dict(),
            "service_started": self._started,
            "browser_engine": getattr(self.browser_pool, "_engine", None) if self.browser_pool else None,
        }


service = KnowledgeService()
