"""Async state tests – run with pytest-asyncio."""
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
    s = StateManager()
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_claim_atomic(state):
    await state.add_urls(["https://example.com/a", "https://example.com/b"])
    t1 = await state.claim_next()
    t2 = await state.claim_next()
    assert t1 is not None and t2 is not None
    assert t1.url != t2.url


@pytest.mark.asyncio
async def test_recovery(state):
    await state.add_urls(["https://example.com/x"])
    t = await state.claim_next()
    assert t is not None
    # Simulate crash: leave IN_PROGRESS, reopen
    await state.close()
    s2 = StateManager()
    await s2.open()
    pending = await s2.pending_count()
    assert pending >= 1
    await s2.close()
