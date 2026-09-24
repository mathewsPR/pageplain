"""Simple single-tenant usage counters for Phase 0."""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from config import settings


@dataclass
class UsageStats:
    scrapes_total: int = 0
    scrapes_success: int = 0
    scrapes_failed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    jobs_created: int = 0
    jobs_completed: int = 0
    bytes_returned: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        total_lookups = self.cache_hits + self.cache_misses
        d["cache_hit_rate"] = (
            round(self.cache_hits / total_lookups, 4) if total_lookups else 0.0
        )
        # Rough token estimate: ~4 chars per token for returned text
        d["approx_tokens_returned"] = self.bytes_returned // 4
        d["approx_tokens_saved_by_cache"] = (self.cache_hits * 2000)  # heuristic
        return d


class UsageTracker:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (settings.data_dir / "usage_stats.json")
        self._lock = threading.Lock()
        self.stats = UsageStats()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(self.stats, k) and k != "started_at":
                        setattr(self.stats, k, v)
            except Exception:
                pass

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.stats.to_dict(), indent=2), encoding="utf-8"
        )

    def record_scrape(self, success: bool, from_cache: bool, bytes_out: int = 0) -> None:
        with self._lock:
            if from_cache:
                self.stats.cache_hits += 1
            else:
                self.stats.cache_misses += 1
                self.stats.scrapes_total += 1
                if success:
                    self.stats.scrapes_success += 1
                else:
                    self.stats.scrapes_failed += 1
            self.stats.bytes_returned += max(0, bytes_out)
            self._save()

    def record_job_created(self) -> None:
        with self._lock:
            self.stats.jobs_created += 1
            self._save()

    def record_job_completed(self) -> None:
        with self._lock:
            self.stats.jobs_completed += 1
            self._save()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self.stats.to_dict()


usage = UsageTracker()
