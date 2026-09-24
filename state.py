from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import aiosqlite
from loguru import logger

from config import settings
from models import FailureReason, Task


class StateManager:
    def __init__(self) -> None:
        self.db_path = settings.state_db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                url           TEXT PRIMARY KEY,
                domain        TEXT,
                depth         INTEGER DEFAULT 0,
                status        TEXT    DEFAULT 'PENDING',
                attempts      INTEGER DEFAULT 0,
                last_attempt  TEXT,
                error_message TEXT,
                parent_url    TEXT
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS domain_stats (
                domain          TEXT PRIMARY KEY,
                pages_fetched   INTEGER DEFAULT 0,
                successes       INTEGER DEFAULT 0,
                soft_failures   INTEGER DEFAULT 0,
                hard_failures   INTEGER DEFAULT 0,
                cooldown_until  REAL DEFAULT 0
            )
        """)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status_depth "
            "ON tasks(status, depth, last_attempt)"
        )
        await self._conn.commit()
        recovered = await self._recover_stuck_tasks()
        if recovered:
            logger.warning(f"Recovered {recovered} stuck task(s) → PENDING")
        logger.info("State DB ready")

    async def _recover_stuck_tasks(self) -> int:
        assert self._conn
        cur = await self._conn.execute(
            "UPDATE tasks SET status = 'PENDING' WHERE status = 'IN_PROGRESS'"
        )
        await self._conn.commit()
        return cur.rowcount

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def add_task(self, url: str, depth: int = 0, parent_url: str | None = None) -> None:
        assert self._conn
        domain = urlparse(url).netloc
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO tasks (url, domain, depth, status, parent_url)
            VALUES (?, ?, ?, 'PENDING', ?)
            """,
            (url, domain, depth, parent_url),
        )
        await self._conn.commit()

    async def add_urls(self, urls: List[str], depth: int = 0) -> None:
        for u in urls:
            await self.add_task(u, depth=depth)

    async def claim_next(self) -> Optional[Task]:
        assert self._conn
        for attempt in range(3):
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                async with self._conn.execute(
                    """
                    SELECT url, domain, depth, attempts, last_attempt,
                           error_message, parent_url
                    FROM tasks
                    WHERE status = 'PENDING' AND attempts < ?
                    ORDER BY depth ASC,
                             last_attempt IS NULL DESC,
                             last_attempt ASC
                    LIMIT 1
                    """,
                    (settings.max_attempts_per_url,),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    await self._conn.execute("COMMIT")
                    return None
                now = datetime.utcnow().isoformat()
                await self._conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'IN_PROGRESS', attempts = attempts + 1, last_attempt = ?
                    WHERE url = ? AND status = 'PENDING'
                    """,
                    (now, row["url"]),
                )
                if self._conn.total_changes == 0:
                    await self._conn.execute("ROLLBACK")
                    continue
                await self._conn.execute("COMMIT")
                return Task(
                    url=row["url"],
                    domain=row["domain"] or "",
                    depth=row["depth"] or 0,
                    status=FailureReason.PENDING,
                    attempts=row["attempts"] + 1,
                    last_attempt=datetime.utcnow(),
                    error_message=row["error_message"],
                    parent_url=row["parent_url"],
                )
            except aiosqlite.OperationalError as e:
                try:
                    await self._conn.execute("ROLLBACK")
                except Exception:
                    pass
                if attempt == 2:
                    logger.warning(f"claim_next lock: {e}")
                    return None
                await asyncio.sleep(0.05 * (attempt + 1))
        return None

    async def mark(self, url: str, reason: FailureReason, error: str | None = None) -> None:
        assert self._conn
        await self._conn.execute(
            """
            UPDATE tasks SET status = ?, error_message = ?, last_attempt = ?
            WHERE url = ?
            """,
            (reason.value, error, datetime.utcnow().isoformat(), url),
        )
        await self._conn.commit()

    async def record_domain_result(self, domain: str, reason: FailureReason) -> None:
        """Update success/failure counts and apply cool-down on repeated hard failures."""
        assert self._conn
        await self._conn.execute(
            """
            INSERT INTO domain_stats (domain, pages_fetched, successes, soft_failures, hard_failures)
            VALUES (?, 0, 0, 0, 0)
            ON CONFLICT(domain) DO NOTHING
            """,
            (domain,),
        )
        if reason == FailureReason.SUCCESS or reason == FailureReason.CACHED:
            await self._conn.execute(
                """
                UPDATE domain_stats
                SET pages_fetched = pages_fetched + 1, successes = successes + 1
                WHERE domain = ?
                """,
                (domain,),
            )
        elif reason == FailureReason.CAPTCHA:
            await self._conn.execute(
                """
                UPDATE domain_stats
                SET pages_fetched = pages_fetched + 1, hard_failures = hard_failures + 1
                WHERE domain = ?
                """,
                (domain,),
            )
            # Cool-down after N hard failures
            async with self._conn.execute(
                "SELECT hard_failures FROM domain_stats WHERE domain = ?", (domain,)
            ) as cur:
                row = await cur.fetchone()
            if row and int(row["hard_failures"]) >= settings.domain_cooldown_failures:
                until = time.time() + settings.domain_cooldown_seconds
                await self._conn.execute(
                    "UPDATE domain_stats SET cooldown_until = ? WHERE domain = ?",
                    (until, domain),
                )
                logger.warning(
                    f"Domain {domain} entered cool-down until "
                    f"{datetime.utcfromtimestamp(until).isoformat()}Z"
                )
        elif reason in (
            FailureReason.BLOCKED,
            FailureReason.TIMEOUT,
            FailureReason.EMPTY,
            FailureReason.ERROR,
        ):
            await self._conn.execute(
                """
                UPDATE domain_stats
                SET pages_fetched = pages_fetched + 1, soft_failures = soft_failures + 1
                WHERE domain = ?
                """,
                (domain,),
            )
        else:
            await self._conn.execute(
                "UPDATE domain_stats SET pages_fetched = pages_fetched + 1 WHERE domain = ?",
                (domain,),
            )
        await self._conn.commit()

    async def is_domain_in_cooldown(self, domain: str) -> bool:
        assert self._conn
        async with self._conn.execute(
            "SELECT cooldown_until FROM domain_stats WHERE domain = ?", (domain,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        return float(row["cooldown_until"] or 0) > time.time()

    async def increment_domain(self, domain: str) -> int:
        assert self._conn
        await self._conn.execute(
            """
            INSERT INTO domain_stats (domain, pages_fetched) VALUES (?, 1)
            ON CONFLICT(domain) DO UPDATE SET pages_fetched = pages_fetched + 1
            """,
            (domain,),
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT pages_fetched FROM domain_stats WHERE domain = ?", (domain,)
        ) as cur:
            row = await cur.fetchone()
            return int(row["pages_fetched"]) if row else 1

    async def pending_count(self) -> int:
        assert self._conn
        async with self._conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'PENDING'"
        ) as cur:
            row = await cur.fetchone()
            return int(row["c"]) if row else 0

    async def stats(self) -> Dict[str, int]:
        assert self._conn
        result: Dict[str, int] = {
            "pending": 0, "in_progress": 0, "success": 0, "failed": 0, "total": 0,
        }
        async with self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
        ) as cur:
            async for row in cur:
                status = row["status"]
                count = int(row["c"])
                result["total"] += count
                if status == "PENDING":
                    result["pending"] = count
                elif status == "IN_PROGRESS":
                    result["in_progress"] = count
                elif status == "SUCCESS":
                    result["success"] = count
                else:
                    result["failed"] += count
        return result

    async def full_summary(self) -> Dict:
        """End-of-run summary for research reporting."""
        assert self._conn
        by_status: Dict[str, int] = {}
        async with self._conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"
        ) as cur:
            async for row in cur:
                by_status[row["status"]] = int(row["c"])
        domains = []
        async with self._conn.execute(
            "SELECT domain, pages_fetched, successes, soft_failures, hard_failures, cooldown_until "
            "FROM domain_stats ORDER BY pages_fetched DESC"
        ) as cur:
            async for row in cur:
                domains.append(dict(row))
        return {"by_status": by_status, "domains": domains}
