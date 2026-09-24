from __future__ import annotations

import argparse
import asyncio
import json
import random
import signal
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from browser_pool import BrowserPool
from cache import ContentCache
from config import settings
from crawler import Crawler
from robots import RobotsManager
from router import Router
from state import StateManager


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    )
    log_path = settings.data_dir / "scraper.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.add(log_path, rotation="20 MB", retention="10 days", level="DEBUG")


async def worker(
    wid: int,
    crawler: Crawler,
    state: StateManager,
    stop: asyncio.Event,
    pages_done: asyncio.Queue,
) -> None:
    logger.info(f"Worker {wid} started")
    while not stop.is_set():
        try:
            task = await state.claim_next()
            if task is None:
                await asyncio.sleep(1.2)
                continue

            logger.info(f"[W{wid}] depth={task.depth} {task.url}")
            result = await crawler.process(task)

            if result.success:
                out = settings.output_dir
                out.mkdir(parents=True, exist_ok=True)
                name = "".join(
                    c if c.isalnum() or c in "-_." else "_" for c in task.url
                )[:140]
                path = out / f"{name}.md"
                header = f"# {result.metadata.title or task.url}\n\n"
                if settings.research_mode and result.research:
                    header += "<!-- Research metadata\n"
                    for k, v in result.research.items():
                        header += f"{k}: {v}\n"
                    header += "-->\n\n"
                path.write_text(header + result.markdown, encoding="utf-8")
                logger.success(
                    f"[W{wid}] {result.reason.value} via {result.path_used} | {task.url}"
                )
            else:
                logger.warning(f"[W{wid}] {result.reason.value} | {task.url}")

            await pages_done.put(1)
            delay = random.uniform(*settings.per_domain_delay_range)
            await asyncio.sleep(delay)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"[W{wid}] Unhandled error: {e}")
            await asyncio.sleep(2.0)

    logger.info(f"Worker {wid} stopped")


async def progress_reporter(
    state: StateManager,
    pages_done: asyncio.Queue,
    stop: asyncio.Event,
    max_pages: int,
) -> None:
    completed = 0
    while not stop.is_set():
        try:
            while True:
                try:
                    pages_done.get_nowait()
                    completed += 1
                except asyncio.QueueEmpty:
                    break

            stats = await state.stats()
            logger.info(
                f"Progress | done={completed} success={stats.get('success', 0)} "
                f"failed={stats.get('failed', 0)} pending={stats.get('pending', 0)} "
                f"in_progress={stats.get('in_progress', 0)}"
            )

            if max_pages > 0 and completed >= max_pages:
                logger.warning(f"Reached max_pages={max_pages} – stopping")
                stop.set()
                break

            if stats.get("pending", 0) == 0 and stats.get("in_progress", 0) == 0:
                await asyncio.sleep(2.5)
                stats = await state.stats()
                if stats.get("pending", 0) == 0 and stats.get("in_progress", 0) == 0:
                    logger.info("Queue empty – finishing")
                    stop.set()
                    break

            await asyncio.sleep(4.0)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Progress reporter error: {e}")
            await asyncio.sleep(3.0)


async def write_summary(state: StateManager) -> None:
    """Tier 2.9: end-of-run job summary."""
    try:
        summary = await state.full_summary()
        summary["finished_at"] = datetime.utcnow().isoformat() + "Z"
        summary["settings"] = {
            "browser_engine": settings.browser_engine,
            "skip_hard_domains": settings.skip_hard_domains,
            "max_depth": settings.max_depth,
            "research_mode": settings.research_mode,
        }
        path = settings.summary_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        logger.info(f"Run summary written to {path}")
        by = summary.get("by_status", {})
        logger.info(f"Summary by status: {by}")
    except Exception as e:
        logger.warning(f"Could not write summary: {e}")


async def main(seed_urls: list[str], max_pages: int = 0) -> None:
    setup_logging()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)

    if max_pages > 0:
        settings.max_pages = max_pages

    state = StateManager()
    await state.open()
    await state.add_urls(seed_urls, depth=0)

    browser_pool = BrowserPool()
    await browser_pool.start()
    cache = ContentCache()
    router = Router(browser_pool, cache)
    robots = RobotsManager()
    crawler = Crawler(state, router, robots, cache)

    stop = asyncio.Event()
    pages_done: asyncio.Queue = asyncio.Queue()

    def _stop() -> None:
        logger.warning("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _stop)
        except NotImplementedError:
            pass

    workers = [
        asyncio.create_task(worker(i, crawler, state, stop, pages_done))
        for i in range(settings.max_browser_concurrency)
    ]
    reporter = asyncio.create_task(
        progress_reporter(state, pages_done, stop, settings.max_pages)
    )

    await stop.wait()

    for w in workers:
        w.cancel()
    reporter.cancel()
    await asyncio.gather(*workers, reporter, return_exceptions=True)

    await write_summary(state)
    await browser_pool.close()
    await state.close()
    logger.info("Clean shutdown complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zero-budget research scraper")
    parser.add_argument("urls", nargs="+", help="Seed URL(s)")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--no-research", action="store_true")
    parser.add_argument(
        "--allow-hard",
        action="store_true",
        help="Do not skip known hard domains (Indeed, LinkedIn, …)",
    )
    parser.add_argument(
        "--sitemap-only",
        action="store_true",
        help="Prefer sitemap / robots-allowed paths only",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.max_depth is not None:
        settings.max_depth = args.max_depth
    if args.no_research:
        settings.research_mode = False
    if args.allow_hard:
        settings.skip_hard_domains = False
    if args.sitemap_only:
        settings.sitemap_only = True
    asyncio.run(main(args.urls, max_pages=args.max_pages))
