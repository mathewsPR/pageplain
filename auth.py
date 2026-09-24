"""Simple API key auth for Phase 1 REST API."""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import List, Optional

from loguru import logger

from config import settings


class ApiKeyStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (settings.data_dir / "api_keys.json")
        self.keys: dict[str, dict] = {}  # hash -> meta
        # True only when the file exists but failed to parse. In that case
        # verify() must fail closed — a corrupt store is not the same thing
        # as "no keys configured yet" (which is an intentional open-dev-mode
        # default) and must never silently become an open API.
        self._corrupt = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self.keys = {}
            return
        try:
            self.keys = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(
                f"API key store at {self.path} is corrupt ({e}). "
                "Failing closed: all requests will be rejected until the "
                "file is restored or deleted."
            )
            self.keys = {}
            self._corrupt = True

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.keys, indent=2), encoding="utf-8")

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def create(self, name: str = "default") -> str:
        raw = "sk_" + secrets.token_urlsafe(24)
        self.keys[self._hash(raw)] = {"name": name, "prefix": raw[:10]}
        self._save()
        return raw

    def verify(self, raw: Optional[str]) -> bool:
        if self._corrupt:
            return False
        if not self.keys:
            # No keys file at all → open (dev mode). This is intentional
            # for local/dev use; set at least one key before exposing the
            # API beyond localhost.
            return True
        if not raw:
            return False
        raw = raw.removeprefix("Bearer ").strip()
        return self._hash(raw) in self.keys

    def list_meta(self) -> List[dict]:
        return [{"name": v["name"], "prefix": v.get("prefix", "")} for v in self.keys.values()]

    def ensure_bootstrap_key(self) -> Optional[str]:
        """Create a bootstrap key if none exist; return raw key once.
        Caller is responsible for how it's surfaced — printing it to stdout
        is convenient for local use but will land in container/orchestrator
        logs, so avoid that in a shared or production deployment; write it
        to a file with restricted permissions instead."""
        if self.keys or self._corrupt:
            return None
        return self.create("bootstrap")


api_keys = ApiKeyStore()
