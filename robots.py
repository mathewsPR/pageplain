from __future__ import annotations

import asyncio
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
from typing import Dict

from loguru import logger
from curl_cffi.requests import AsyncSession

from config import settings


class RobotsManager:
    def __init__(self) -> None:
        self._parsers: Dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    async def allowed(self, url: str, user_agent: str = "*") -> bool:
        if not settings.respect_robots:
            return True
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        async with self._lock:
            if base not in self._parsers:
                rp = RobotFileParser()
                robots_url = urljoin(base, "/robots.txt")
                try:
                    async with AsyncSession() as session:
                        resp = await session.get(robots_url, impersonate="chrome124", timeout=10)
                        if resp.status_code == 200:
                            rp.parse(resp.text.splitlines())
                        else:
                            rp.parse([])
                except Exception:
                    rp.parse([])
                self._parsers[base] = rp
            return self._parsers[base].can_fetch(user_agent, url)
