from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from models import ScrapeResult


class ContentCache:
    def __init__(self) -> None:
        self.cache_dir = settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def _path(self, url: str) -> Path:
        return self.cache_dir / f"{self._key(url)}.json"

    def get(self, url: str) -> Optional[ScrapeResult]:
        if not settings.enable_cache:
            return None
        path = self._path(url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result = ScrapeResult(**data)
            result.cached = True
            return result
        except Exception as e:
            logger.warning(f"Cache read failed for {url}: {e}")
            return None

    def set(self, result: ScrapeResult) -> None:
        if not settings.enable_cache or not result.success:
            return
        path = self._path(result.url)
        try:
            path.write_text(result.model_dump_json(), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Cache write failed: {e}")

    def content_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
