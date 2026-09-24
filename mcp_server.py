"""
Phase 1 MCP server — Web Knowledge Gateway (team edition).

Tools: scrape_url, get_cached, create_job, list_jobs, get_job,
       get_job_results, get_usage, get_policy, health
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from loguru import logger
from mcp.server import MCPServer

from service import service

logger.remove()
logger.add(lambda msg: print(msg, end="", file=__import__("sys").stderr), level="INFO")

mcp = MCPServer(
    "pageplain",
    description=(
        "Self-hosted web knowledge gateway for teams. "
        "Cache-first markdown extraction to reduce LLM token burn. "
        "Respects allow/deny policy and skips hard anti-bot domains by default."
    ),
)


def _j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@mcp.tool(description="Scrape one URL to markdown (cache-first).")
async def scrape_url(url: str, max_chars: int = 50000, force_refresh: bool = False) -> str:
    return _j(await service.scrape_url(url, max_chars=max_chars, force_refresh=force_refresh))


@mcp.tool(description="Return cached markdown only (no network).")
async def get_cached(url: str, max_chars: int = 50000) -> str:
    return _j(await service.get_cached(url, max_chars=max_chars))


@mcp.tool(description="Create async multi-URL job. Returns job_id.")
async def create_job(
    urls: List[str],
    max_pages: int = 10,
    max_depth: int = 0,
    name: str = "",
    team: str = "default",
) -> str:
    if not urls:
        return _j({"ok": False, "error": "urls required"})
    return _j(
        await service.create_job(
            urls=urls[:100],
            max_pages=min(max(1, max_pages), 100),
            max_depth=max_depth,
            name=name,
            team=team,
        )
    )


@mcp.tool(description="List recent jobs.")
async def list_jobs(limit: int = 20) -> str:
    return _j(await service.list_jobs(limit=limit))


@mcp.tool(description="Get job status by id.")
async def get_job(job_id: str) -> str:
    return _j(await service.get_job(job_id))


@mcp.tool(description="Get job page results (truncated markdown).")
async def get_job_results(job_id: str, max_chars_per_page: int = 20000) -> str:
    return _j(await service.get_job_results(job_id, max_chars_per_page=max_chars_per_page))


@mcp.tool(description="Usage stats including cache hit rate and token estimates.")
async def get_usage() -> str:
    return _j(service.get_usage())


@mcp.tool(description="Current domain policy (allowlist/denylist/hard-skip).")
async def get_policy() -> str:
    return _j(service.get_policy())


@mcp.tool(description="Health check.")
async def health() -> str:
    await service.start()
    engine = getattr(service.browser_pool, "_engine", None) if service.browser_pool else None
    return _j({"ok": True, "started": service._started, "browser_engine": engine, "usage": service.get_usage()})




@mcp.tool(description="Search already-crawled knowledge (cache + job pages). Token-cheap retrieval.")
async def search_knowledge(query: str, limit: int = 10) -> str:
    return _j(service.search_knowledge(query, limit=limit))


@mcp.tool(description="Re-fetch URL and report whether content hash changed since last cache.")
async def get_diff(url: str, max_chars: int = 30000) -> str:
    return _j(await service.get_diff(url, max_chars=max_chars))


@mcp.tool(description="Operational metrics: jobs, cache size, usage, policy.")
async def get_metrics() -> str:
    return _j(service.get_metrics())


if __name__ == "__main__":
    mcp.run()
