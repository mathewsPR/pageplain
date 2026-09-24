from __future__ import annotations

import asyncio
import random
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from browser_pool import BrowserPool
from cache import ContentCache
from config import settings
from extractor import html_to_result
from models import FailureReason, ScrapeResult, Task
from network_interceptor import NetworkInterceptor


# Soft signals – often just rate-limit or light bot check; worth trying browser
SOFT_BLOCK_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"access denied",
        r"attention required",
        r"ddos protection",
        r"please wait",
        r"checking your browser",
        r"just a moment",
        r"cf-browser-verification",
        r"challenge-platform",
    ]
]

# Hard interactive CAPTCHA – almost never solvable without paid solver
HARD_CAPTCHA_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"turnstile",
        r"cf-turnstile",
        r"g-recaptcha",
        r"hcaptcha",
        r"datadome",
        r"px-captcha",
        r"captcha-delivery",
        r"geo\.captcha-delivery",
        r"challenge-form",
        r"verify you are human",
        r"complete the security check",
    ]
]


def classify_protection(html: str, status_code: int | None = None) -> str:
    """
    Returns: 'none' | 'soft' | 'hard'
    """
    if not html:
        return "soft" if status_code in (403, 429, 503) else "none"
    sample = html[:12000]
    if any(p.search(sample) for p in HARD_CAPTCHA_PATTERNS):
        return "hard"
    if status_code in (403, 429, 503) or any(p.search(sample) for p in SOFT_BLOCK_PATTERNS):
        return "soft"
    return "none"


class Router:
    def __init__(self, browser_pool: BrowserPool, cache: ContentCache) -> None:
        self.browser_pool = browser_pool
        self.cache = cache
        self._http_sem = asyncio.Semaphore(settings.max_http_concurrency)
        self._warmed_domains: set[str] = set()

    async def route(self, task: Task) -> ScrapeResult:
        # Cache hit
        cached = self.cache.get(task.url)
        if cached and cached.success:
            cached.reason = FailureReason.CACHED
            cached.path_used = "cache"
            return cached

        # Optional warm-up: visit domain root once per domain
        domain = urlparse(task.url).netloc
        if settings.enable_warmup and domain and domain not in self._warmed_domains:
            await self._warmup(domain)
            self._warmed_domains.add(domain)

        # Path 1 – fast TLS impersonation
        result = await self._curl(task)
        if result.success:
            result.path_used = "curl_cffi"
            result.content_hash = self.cache.content_hash(result.markdown)
            self.cache.set(result)
            return result

        protection = "hard" if result.reason == FailureReason.CAPTCHA else (
            "soft" if result.reason == FailureReason.BLOCKED else "none"
        )
        # Re-classify from any error message / status if needed
        if not result.success and result.status_code:
            # We already classified inside _curl; keep it
            pass

        # Free-tier policy: escalate soft blocks to browser; abort only on hard CAPTCHA
        if result.reason == FailureReason.CAPTCHA and settings.hard_captcha_only_abort:
            # Only abort early if we are sure it is hard; otherwise still try browser
            # (fast path often mis-labels soft challenges)
            if not settings.escalate_on_soft_block:
                result.path_used = "curl_cffi"
                return result
        elif result.reason == FailureReason.BLOCKED and not settings.escalate_on_soft_block:
            result.path_used = "curl_cffi"
            return result

        logger.info(f"Escalating {task.url} to browser path (fast path: {result.reason.value})")

        # Path 2 – Playwright
        result = await self._browser(task, scroll=False)
        if result.success:
            result.path_used = "playwright"
            result.content_hash = self.cache.content_hash(result.markdown)
            self.cache.set(result)
            return result
        if result.reason == FailureReason.CAPTCHA:
            result.path_used = "playwright"
            return result  # hard challenge from real browser → stop

        # Path 3 – scroll-and-wait
        result = await self._browser(task, scroll=True)
        result.path_used = "scroll"
        if result.success:
            result.content_hash = self.cache.content_hash(result.markdown)
            self.cache.set(result)
        return result

    async def _warmup(self, domain: str) -> None:
        """Lightweight visit to domain root to pick up cookies / appear less cold."""
        root = f"https://{domain}/"
        try:
            async with self._http_sem:
                async with AsyncSession() as session:
                    await session.get(
                        root,
                        impersonate="chrome124",
                        timeout=12,
                        allow_redirects=True,
                    )
            logger.debug(f"Warm-up done for {domain}")
            await asyncio.sleep(random.uniform(1.0, 2.5))
        except Exception as e:
            logger.debug(f"Warm-up failed for {domain}: {e}")

    async def _curl(self, task: Task) -> ScrapeResult:
        async with self._http_sem:
            try:
                async with AsyncSession() as session:
                    resp = await session.get(
                        task.url,
                        impersonate="chrome124",
                        timeout=settings.request_timeout,
                        allow_redirects=True,
                    )
                    html = resp.text
                    kind = classify_protection(html, resp.status_code)
                    if kind == "hard":
                        return ScrapeResult(
                            url=task.url,
                            success=False,
                            reason=FailureReason.CAPTCHA,
                            status_code=resp.status_code,
                            error_message="Hard CAPTCHA (fast path)",
                        )
                    if kind == "soft":
                        return ScrapeResult(
                            url=task.url,
                            success=False,
                            reason=FailureReason.BLOCKED,
                            status_code=resp.status_code,
                            error_message="Soft block (fast path)",
                        )
                    result = html_to_result(html, task.url)
                    result.status_code = resp.status_code
                    return result
            except Exception as e:
                return ScrapeResult(
                    url=task.url,
                    success=False,
                    reason=FailureReason.ERROR,
                    error_message=str(e)[:300],
                )

    async def _browser(self, task: Task, scroll: bool = False) -> ScrapeResult:
        page: Optional[Page] = None
        try:
            page = await self.browser_pool.get_page(task.url)
            interceptor = NetworkInterceptor(page)
            await interceptor.start()

            # Human-like short pause before navigation
            await asyncio.sleep(random.uniform(0.4, 1.2))

            await page.goto(
                task.url,
                wait_until="domcontentloaded",
                timeout=settings.browser_timeout * 1000,
            )

            if scroll:
                for _ in range(4):
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                    await asyncio.sleep(random.uniform(0.9, 1.8))
                await page.wait_for_timeout(1500)
            else:
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except PlaywrightTimeout:
                    pass
                # Extra settle time
                await asyncio.sleep(random.uniform(1.0, 2.0))

            html = await page.content()
            kind = classify_protection(html)
            if kind == "hard":
                return ScrapeResult(
                    url=task.url,
                    success=False,
                    reason=FailureReason.CAPTCHA,
                    error_message="Hard CAPTCHA (browser)",
                )
            if kind == "soft":
                # Still try extraction – sometimes content is present behind a soft banner
                result = html_to_result(html, task.url)
                if result.success and len(result.markdown) > 200:
                    return result
                return ScrapeResult(
                    url=task.url,
                    success=False,
                    reason=FailureReason.BLOCKED,
                    error_message="Soft block (browser)",
                )

            result = html_to_result(html, task.url)
            return result

        except PlaywrightTimeout:
            return ScrapeResult(url=task.url, success=False, reason=FailureReason.TIMEOUT)
        except Exception as e:
            return ScrapeResult(
                url=task.url,
                success=False,
                reason=FailureReason.ERROR,
                error_message=str(e)[:300],
            )
        finally:
            if page:
                await self.browser_pool.release_page(page)
