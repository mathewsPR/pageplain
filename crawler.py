from __future__ import annotations

from urllib.parse import urlparse

from loguru import logger

from config import settings
from models import DEFAULT_HARD_DOMAINS, FailureReason, ScrapeResult, Task
from robots import RobotsManager
from router import Router
from state import StateManager
from cache import ContentCache


def _is_hard_domain(domain: str) -> bool:
    d = domain.lower().removeprefix("www.")
    if d in DEFAULT_HARD_DOMAINS:
        return True
    # suffix match e.g. de.indeed.com
    for hard in DEFAULT_HARD_DOMAINS:
        if d.endswith("." + hard) or d == hard:
            return True
    return False


class Crawler:
    def __init__(
        self,
        state: StateManager,
        router: Router,
        robots: RobotsManager,
        cache: ContentCache,
    ) -> None:
        self.state = state
        self.router = router
        self.robots = robots
        self.cache = cache

    async def process(self, task: Task) -> ScrapeResult:
        domain = task.domain or urlparse(task.url).netloc

        # Tier 1: skip known hard domains on free tier
        if settings.skip_hard_domains and _is_hard_domain(domain):
            logger.info(f"Skipping hard domain {domain}")
            await self.state.mark(task.url, FailureReason.SKIPPED_HARD, "hard domain tier")
            await self.state.record_domain_result(domain, FailureReason.SKIPPED_HARD)
            return ScrapeResult(
                url=task.url,
                success=False,
                reason=FailureReason.SKIPPED_HARD,
                error_message="Domain marked hard – skipped on free tier",
            )

        # Tier 1: domain cool-down
        if await self.state.is_domain_in_cooldown(domain):
            logger.info(f"Domain {domain} in cool-down – skipping")
            await self.state.mark(task.url, FailureReason.COOLDOWN, "domain cool-down")
            return ScrapeResult(
                url=task.url,
                success=False,
                reason=FailureReason.COOLDOWN,
                error_message="Domain in cool-down",
            )

        if not await self.robots.allowed(task.url):
            await self.state.mark(task.url, FailureReason.ROBOTS_DISALLOWED)
            await self.state.record_domain_result(domain, FailureReason.ROBOTS_DISALLOWED)
            return ScrapeResult(
                url=task.url, success=False, reason=FailureReason.ROBOTS_DISALLOWED
            )

        count = await self.state.increment_domain(domain)
        if count > settings.default_domain_budget:
            await self.state.mark(task.url, FailureReason.BUDGET_EXCEEDED)
            await self.state.record_domain_result(domain, FailureReason.BUDGET_EXCEEDED)
            return ScrapeResult(
                url=task.url, success=False, reason=FailureReason.BUDGET_EXCEEDED
            )

        result = await self.router.route(task)

        if settings.research_mode and result.success:
            result.research = {
                "crawled_at": result.timestamp.isoformat(),
                "content_hash": result.content_hash,
                "source_url": result.url,
                "canonical": result.metadata.canonical,
                "path_used": result.path_used,
                "depth": task.depth,
            }

        await self.state.mark(task.url, result.reason, result.error_message)
        await self.state.record_domain_result(domain, result.reason)

        # Link discovery
        if result.success and task.depth < settings.max_depth and result.links:
            base_domain = domain
            for link in result.links:
                link_domain = urlparse(link).netloc
                if settings.same_domain_only and link_domain != base_domain:
                    continue
                if settings.skip_hard_domains and _is_hard_domain(link_domain):
                    continue
                await self.state.add_task(link, depth=task.depth + 1, parent_url=task.url)

        return result
