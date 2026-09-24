"""Domain allow/deny policy for Phase 1 team deployments."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import urlparse

from config import settings
from models import DEFAULT_HARD_DOMAINS
from security import quick_reject


class DomainPolicy:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (settings.data_dir / "policy.json")
        self.allowlist: Set[str] = set()
        self.denylist: Set[str] = set()
        self.allowlist_enabled: bool = False  # if True, only allowlist may be scraped
        self.skip_hard_domains: bool = settings.skip_hard_domains
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.allowlist = set(data.get("allowlist") or [])
                self.denylist = set(data.get("denylist") or [])
                self.allowlist_enabled = bool(data.get("allowlist_enabled", False))
                self.skip_hard_domains = bool(
                    data.get("skip_hard_domains", settings.skip_hard_domains)
                )
            except Exception:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "allowlist_enabled": self.allowlist_enabled,
                    "allowlist": sorted(self.allowlist),
                    "denylist": sorted(self.denylist),
                    "skip_hard_domains": self.skip_hard_domains,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _norm(self, domain: str) -> str:
        return domain.lower().removeprefix("www.")

    def check_url(self, url: str) -> tuple[bool, str]:
        """Returns (allowed, reason)."""
        unsafe = quick_reject(url)
        if unsafe:
            return False, unsafe
        # hostname (not netloc) — netloc includes port/userinfo, which lets
        # "example.com:443" or "user@example.com" slip past a denylist
        # entry for "example.com".
        domain = self._norm(urlparse(url).hostname or "")
        if not domain:
            return False, "invalid_url"
        if domain in self.denylist or any(
            domain.endswith("." + d) or domain == d for d in self.denylist
        ):
            return False, "denylist"
        if self.allowlist_enabled:
            ok = domain in self.allowlist or any(
                domain.endswith("." + d) or domain == d for d in self.allowlist
            )
            if not ok:
                return False, "not_in_allowlist"
        if self.skip_hard_domains:
            for hard in DEFAULT_HARD_DOMAINS:
                h = self._norm(hard)
                if domain == h or domain.endswith("." + h):
                    return False, "hard_domain"
        return True, "ok"

    def to_dict(self) -> dict:
        return {
            "allowlist_enabled": self.allowlist_enabled,
            "allowlist": sorted(self.allowlist),
            "denylist": sorted(self.denylist),
            "skip_hard_domains": self.skip_hard_domains,
        }


policy = DomainPolicy()
