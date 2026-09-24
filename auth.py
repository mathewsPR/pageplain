"""Simple API key auth for Phase 1 REST API."""
from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import List, Optional

from config import settings


class ApiKeyStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or (settings.data_dir / "api_keys.json")
        self.keys: dict[str, dict] = {}  # hash -> meta
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.keys = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.keys = {}

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
        if not self.keys:
            # No keys configured → open (dev mode)
            return True
        if not raw:
            return False
        raw = raw.removeprefix("Bearer ").strip()
        return self._hash(raw) in self.keys

    def list_meta(self) -> List[dict]:
        return [{"name": v["name"], "prefix": v.get("prefix", "")} for v in self.keys.values()]

    def ensure_bootstrap_key(self) -> Optional[str]:
        """Create a bootstrap key if none exist; return raw key once."""
        if self.keys:
            return None
        return self.create("bootstrap")


api_keys = ApiKeyStore()
