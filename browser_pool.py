from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from loguru import logger

from config import settings

_HAS_CAMOUFOX = False
try:
    from camoufox.async_api import AsyncCamoufox
    _HAS_CAMOUFOX = True
except ImportError:
    AsyncCamoufox = None  # type: ignore

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from stealth import get_context_options, get_launch_args


def _detect_ram_gb() -> float:
    try:
        meminfo = Path("/proc/meminfo").read_text()
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb / (1024 * 1024)
    except Exception:
        pass
    return 8.0


def _browser_concurrency() -> int:
    """Never parallelize Camoufox on small RAM."""
    ram = _detect_ram_gb()
    if ram < settings.min_ram_gb_for_parallel_browser:
        return 1
    return min(settings.max_browser_concurrency, 2)


def build_camoufox_kwargs(domain: str | None = None) -> Dict[str, Any]:
    """
    Launch presets:
      fast    — text scrape: block images, no humanize, session cache
      stealth — softer sites: humanize, webrtc block, persistent profile
    """
    preset = (settings.camoufox_preset or "fast").lower()
    kwargs: Dict[str, Any] = {
        "headless": settings.camoufox_headless,
        "os": settings.camoufox_os,
        "locale": settings.camoufox_locale,
        "window": (settings.camoufox_window_width, settings.camoufox_window_height),
        "block_webrtc": True,
    }

    if preset == "stealth":
        kwargs.update(
            {
                "humanize": True,
                "block_images": False,
                "enable_cache": True,
            }
        )
    else:  # fast — block images for speed; acknowledge WAF tradeoff
        kwargs.update(
            {
                "humanize": False,
                "block_images": True,
                "enable_cache": True,
                "i_know_what_im_doing": True,  # suppress Camoufox WAF warning for intentional fast mode
            }
        )

    # Proxy + geoip (only when configured)
    if settings.proxy_server:
        proxy: Dict[str, str] = {"server": settings.proxy_server}
        if settings.proxy_username:
            proxy["username"] = settings.proxy_username
        if settings.proxy_password:
            proxy["password"] = settings.proxy_password
        kwargs["proxy"] = proxy
        if settings.enable_geoip:
            kwargs["geoip"] = True

    # Persistent profile per domain when enabled
    if settings.enable_storage_state and domain:
        kwargs["persistent_context"] = True
        safe = domain.replace(":", "_").replace("/", "_")
        ud = settings.profile_dir / safe
        ud.mkdir(parents=True, exist_ok=True)
        kwargs["user_data_dir"] = str(ud)

    return kwargs


class BrowserPool:
    """
    Camoufox preferred (presets), Playwright fallback.
    Browser semaphore forced to 1 on hosts with < 16 GB RAM.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Any = None
        self._context: Optional[BrowserContext] = None
        self._camoufox_cm: Any = None
        self._page_count = 0
        self._started_at = 0.0
        self._lock = asyncio.Lock()
        conc = _browser_concurrency()
        self._semaphore = asyncio.Semaphore(conc)
        self._current_domain: Optional[str] = None
        self._engine: str = "none"
        self._preset: str = settings.camoufox_preset
        settings.storage_state_dir.mkdir(parents=True, exist_ok=True)
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"BrowserPool init: ram≈{_detect_ram_gb():.1f}GB "
            f"browser_concurrency={conc} preset={self._preset}"
        )

    def _state_path(self, domain: str) -> Path:
        safe = domain.replace(":", "_").replace("/", "_")
        return settings.storage_state_dir / f"{safe}.json"

    async def start(self) -> None:
        async with self._lock:
            await self._start_browser()

    async def _start_browser(self, domain: str | None = None) -> None:
        prefer = settings.browser_engine.lower()
        if prefer == "camoufox" and _HAS_CAMOUFOX:
            try:
                await self._start_camoufox(domain)
                return
            except Exception as e:
                logger.warning(f"Camoufox failed ({e}) – Playwright fallback")
        await self._start_playwright(domain)

    async def _start_camoufox(self, domain: str | None = None) -> None:
        base = build_camoufox_kwargs(domain)
        headless_opts = [base.get("headless", True), True, False]
        last_err: Exception | None = None
        for headless in headless_opts:
            try:
                kwargs = dict(base)
                kwargs["headless"] = headless
                self._camoufox_cm = AsyncCamoufox(**kwargs)
                self._browser = await self._camoufox_cm.__aenter__()
                if hasattr(self._browser, "new_page"):
                    self._context = self._browser  # type: ignore
                else:
                    self._context = await self._browser.new_context()
                self._page_count = 0
                self._started_at = time.monotonic()
                self._current_domain = domain
                self._engine = "camoufox"
                logger.info(
                    f"Browser pool started (Camoufox preset={self._preset} headless={headless!r})"
                )
                return
            except Exception as e:
                last_err = e
                logger.debug(f"Camoufox headless={headless!r} failed: {e}")
                try:
                    if self._camoufox_cm:
                        await self._camoufox_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                self._camoufox_cm = None
                self._browser = None
                self._context = None
        raise RuntimeError(f"Camoufox could not start: {last_err}")

    async def _start_playwright(self, domain: str | None = None) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=get_launch_args(),
        )
        opts = get_context_options()
        if settings.enable_storage_state and domain:
            path = self._state_path(domain)
            if path.exists():
                try:
                    opts["storage_state"] = str(path)
                except Exception as e:
                    logger.warning(f"storage_state load: {e}")
        self._context = await self._browser.new_context(**opts)
        self._page_count = 0
        self._started_at = time.monotonic()
        self._current_domain = domain
        self._engine = "playwright"
        logger.info("Browser pool started (Playwright Chromium)")

    async def _should_recycle(self) -> bool:
        if self._page_count >= settings.browser_recycle_every_pages:
            return True
        if (time.monotonic() - self._started_at) >= settings.browser_recycle_every_seconds:
            return True
        return False

    async def get_page(self, url: str | None = None) -> Page:
        domain = urlparse(url).netloc if url else None
        await self._semaphore.acquire()
        try:
            async with self._lock:
                if self._browser is None or self._context is None:
                    await self._start_browser(domain)
                elif (
                    domain
                    and self._current_domain
                    and domain != self._current_domain
                    and settings.enable_storage_state
                ):
                    await self._save_state()
                    await self._shutdown_browser()
                    await self._start_browser(domain)
                elif await self._should_recycle():
                    logger.info(f"Recycling browser ({self._engine})")
                    await self._save_state()
                    await self._shutdown_browser()
                    await self._start_browser(domain or self._current_domain)
                else:
                    if domain and not self._current_domain:
                        self._current_domain = domain
                assert self._context is not None
                page = await self._context.new_page()
                self._page_count += 1
                return page
        except Exception:
            self._semaphore.release()
            raise

    async def _save_state(self) -> None:
        if not settings.enable_storage_state or not self._context or not self._current_domain:
            return
        if self._engine == "camoufox":
            return  # persistent user_data_dir
        try:
            path = self._state_path(self._current_domain)
            await self._context.storage_state(path=str(path))
        except Exception as e:
            logger.warning(f"save storage_state: {e}")

    async def release_page(self, page: Page) -> None:
        try:
            await page.close()
        finally:
            self._semaphore.release()

    async def _shutdown_browser(self) -> None:
        try:
            if self._camoufox_cm is not None:
                await self._camoufox_cm.__aexit__(None, None, None)
                self._camoufox_cm = None
                self._browser = None
                self._context = None
                return
        except Exception as e:
            logger.debug(f"Camoufox shutdown: {e}")
        if self._context and self._engine == "playwright":
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser and self._engine == "playwright":
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

    async def close(self) -> None:
        async with self._lock:
            await self._save_state()
            await self._shutdown_browser()
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            self._engine = "none"
            logger.info("Browser pool closed")
