"""Phase 0 validation: KnowledgeService scrape + cache + usage."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings


@pytest.fixture
async def svc(tmp_path):
    settings.data_dir = tmp_path
    settings.state_db_path = tmp_path / "state.db"
    settings.cache_dir = tmp_path / "cache"
    settings.output_dir = tmp_path / "output"
    settings.summary_path = tmp_path / "summary.json"
    from usage_stats import UsageTracker
    import usage_stats as us
    us.usage = UsageTracker(tmp_path / "usage.json")

    from service import KnowledgeService
    s = KnowledgeService()
    # Avoid starting browser for pure unit path if possible — start for integration
    yield s
    if s._started:
        await s.stop()


@pytest.mark.asyncio
async def test_scrape_and_cache(svc):
    r1 = await svc.scrape_url("https://example.com", max_chars=5000)
    assert r1["ok"] is True
    assert len(r1["markdown"]) > 20
    assert r1["from_cache"] is False

    r2 = await svc.scrape_url("https://example.com", max_chars=5000)
    assert r2["ok"] is True
    assert r2["from_cache"] is True

    cached = await svc.get_cached("https://example.com")
    assert cached["ok"] is True
    assert cached["from_cache"] is True

    usage = svc.get_usage()
    assert usage["cache_hits"] >= 1
    assert usage["scrapes_success"] >= 1


@pytest.mark.asyncio
async def test_job_flow(svc):
    created = await svc.create_job(["https://example.com"], max_pages=1, name="t")
    assert created["ok"] is True
    job_id = created["job_id"]
    # wait briefly for background task
    import asyncio
    for _ in range(30):
        st = await svc.get_job(job_id)
        if st.get("status") == "completed":
            break
        await asyncio.sleep(0.5)
    st = await svc.get_job(job_id)
    assert st.get("status") in ("completed", "running", "pending")
    if st.get("status") == "completed":
        res = await svc.get_job_results(job_id)
        assert res["ok"] is True
        assert len(res["results"]) >= 1
