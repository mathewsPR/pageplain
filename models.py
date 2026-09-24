from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class FailureReason(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"              # soft block
    CAPTCHA = "CAPTCHA"              # hard interactive challenge
    EMPTY = "EMPTY"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    ROBOTS_DISALLOWED = "ROBOTS_DISALLOWED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CACHED = "CACHED"
    UNCHANGED = "UNCHANGED"
    PENDING = "PENDING"
    COOLDOWN = "COOLDOWN"            # domain in cool-down
    SKIPPED_HARD = "SKIPPED_HARD"    # domain tier = hard, skipped
    UNSAFE_URL = "UNSAFE_URL"        # blocked by the scheme/SSRF guard


class DomainTier(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Task(BaseModel):
    url: str
    domain: str = ""
    depth: int = 0
    status: FailureReason = FailureReason.PENDING
    attempts: int = 0
    last_attempt: Optional[datetime] = None
    error_message: Optional[str] = None
    parent_url: Optional[str] = None


class PageMetadata(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[str] = None
    canonical: Optional[str] = None
    language: Optional[str] = None


class ScrapeResult(BaseModel):
    url: str
    success: bool = False
    reason: FailureReason = FailureReason.ERROR
    content: str = ""
    markdown: str = ""
    metadata: PageMetadata = Field(default_factory=PageMetadata)
    path_used: str = ""
    status_code: Optional[int] = None
    content_hash: Optional[str] = None
    error_message: Optional[str] = None
    links: List[str] = Field(default_factory=list)
    cached: bool = False
    changed: bool = True
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    research: Dict[str, Any] = Field(default_factory=dict)


# Known hard targets – skip or deprioritize on free tier
DEFAULT_HARD_DOMAINS = {
    "indeed.com", "de.indeed.com", "www.indeed.com",
    "linkedin.com", "www.linkedin.com",
    "glassdoor.com", "www.glassdoor.com",
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
}
