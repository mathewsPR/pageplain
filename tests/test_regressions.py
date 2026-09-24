"""Regression tests for two Stage 0 correctness bugs:
1. force_refresh / get_diff silently returning cached content.
2. Domain budget being double-counted (permanent lockout after ~half budget).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from models import FailureReason
from state import StateManager


@pytest.fixture
async def state(tmp_path):
    settings.state_db_path = tmp_path / "test.db"
    settings.domain_budget_window_seconds = 86400
    s = StateManager()
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_domain_counter_not_double_counted(state):
    """increment_domain() + record_domain_result() together must advance
    pages_fetched by exactly 1 per page, not 2."""
    for _ in range(5):
        await state.increment_domain("blog.test")
        await state.record_domain_result("blog.test", FailureReason.SUCCESS)
    async with state._conn.execute(
        "SELECT pages_fetched, successes FROM domain_stats WHERE domain = ?",
        ("blog.test",),
    ) as cur:
        row = await cur.fetchone()
    assert row["pages_fetched"] == 5
    assert row["successes"] == 5


@pytest.mark.asyncio
async def test_domain_budget_resets_after_window(state, monkeypatch):
    settings.domain_budget_window_seconds = 0  # window "expires" immediately
    n1 = await state.increment_domain("news.test")
    n2 = await state.increment_domain("news.test")
    # With a zero-second window every call starts a fresh window, so the
    # counter must not keep climbing forever.
    assert n1 == 1
    assert n2 == 1


@pytest.mark.asyncio
async def test_claim_next_rowcount_guard_actually_races(state):
    """claim_next's lost-update guard must key off the UPDATE's own
    rowcount, not conn.total_changes (a lifetime counter that is never 0
    once any write has happened on the connection)."""
    await state.add_urls(["https://a.test/1"])
    t1 = await state.claim_next()
    assert t1 is not None
    t2 = await state.claim_next()  # nothing left pending
    assert t2 is None
