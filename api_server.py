"""
Phase 1 REST API + admin UI.
  uvicorn api_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import api_keys
from service import service

app = FastAPI(
    title="Pageplain API",
    description="Phase 1 team API — jobs, scrape, cache, policy, usage",
    version="1.0.0-rc.2",
)

STATIC = Path(__file__).parent / "static"
STATIC.mkdir(exist_ok=True)


def require_key(authorization: Optional[str] = Header(None)) -> None:
    if not api_keys.verify(authorization):
        raise HTTPException(401, "Invalid or missing API key")


class ScrapeBody(BaseModel):
    url: str
    max_chars: int = 50000
    force_refresh: bool = False


class JobBody(BaseModel):
    urls: List[str]
    max_pages: int = 10
    max_depth: int = 0
    name: str = ""
    team: str = "default"


class PolicyBody(BaseModel):
    allowlist: Optional[List[str]] = None
    denylist: Optional[List[str]] = None
    allowlist_enabled: Optional[bool] = None
    skip_hard_domains: Optional[bool] = None


@app.on_event("startup")
async def _startup() -> None:
    raw = api_keys.ensure_bootstrap_key()
    if raw:
        # Write once to a local, operator-only file (0600) rather than only
        # stdout — stdout is convenient for a first local run but lands in
        # container/orchestrator logs in any shared deployment.
        key_path = Path(api_keys.path).parent / "bootstrap_key.txt"
        try:
            key_path.write_text(raw + "\n", encoding="utf-8")
            os.chmod(key_path, 0o600)
            print(
                f"[auth] Bootstrap API key written to {key_path} "
                "(0600). Read it once, then delete the file.",
                flush=True,
            )
        except Exception:
            print(f"[auth] Bootstrap API key (save it, do not log it): {raw}", flush=True)
    await service.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await service.stop()


@app.get("/health")
async def health():
    engine = getattr(service.browser_pool, "_engine", None) if service.browser_pool else None
    return {"ok": True, "started": service._started, "browser_engine": engine}


@app.post("/v1/scrape")
async def scrape(body: ScrapeBody, _: None = Depends(require_key)):
    return await service.scrape_url(body.url, body.max_chars, body.force_refresh)


@app.get("/v1/cache")
async def cache_get(url: str, max_chars: int = 50000, _: None = Depends(require_key)):
    return await service.get_cached(url, max_chars)


@app.post("/v1/jobs")
async def create_job(body: JobBody, _: None = Depends(require_key)):
    return await service.create_job(
        body.urls, body.max_pages, body.max_depth, body.name, body.team
    )


@app.get("/v1/jobs")
async def list_jobs(limit: int = 50, _: None = Depends(require_key)):
    return await service.list_jobs(limit)


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str, _: None = Depends(require_key)):
    return await service.get_job(job_id)


@app.get("/v1/jobs/{job_id}/results")
async def job_results(job_id: str, max_chars_per_page: int = 20000, _: None = Depends(require_key)):
    return await service.get_job_results(job_id, max_chars_per_page)


@app.get("/v1/jobs/{job_id}/export")
async def job_export(job_id: str, _: None = Depends(require_key)):
    path = service.get_export_path(job_id)
    if not path or not path.exists():
        raise HTTPException(404, "export not ready")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/v1/usage")
async def get_usage(_: None = Depends(require_key)):
    return service.get_usage()


@app.get("/v1/policy")
async def get_policy(_: None = Depends(require_key)):
    return service.get_policy()


@app.put("/v1/policy")
async def put_policy(body: PolicyBody, _: None = Depends(require_key)):
    return service.update_policy(
        allowlist=body.allowlist,
        denylist=body.denylist,
        allowlist_enabled=body.allowlist_enabled,
        skip_hard_domains=body.skip_hard_domains,
    )


@app.get("/v1/keys")
async def list_keys(_: None = Depends(require_key)):
    return {"ok": True, "keys": api_keys.list_meta()}



class SearchBody(BaseModel):
    query: str
    limit: int = 10


@app.post("/v1/search")
async def search(body: SearchBody, _: None = Depends(require_key)):
    return service.search_knowledge(body.query, body.limit)


@app.post("/v1/diff")
async def diff(body: ScrapeBody, _: None = Depends(require_key)):
    return await service.get_diff(body.url, body.max_chars)


@app.get("/v1/metrics")
async def metrics(_: None = Depends(require_key)):
    return service.get_metrics()



@app.get("/", response_class=HTMLResponse)
async def admin_ui():
    index = STATIC / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Admin UI missing</h1>")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
