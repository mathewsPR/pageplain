from __future__ import annotations

import json
import re
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup
from loguru import logger

from models import FailureReason, PageMetadata, ScrapeResult


def extract_metadata(html: str, url: str) -> PageMetadata:
    soup = BeautifulSoup(html, "lxml")
    meta = PageMetadata()
    title_tag = soup.find("title")
    if title_tag:
        meta.title = title_tag.get_text(strip=True)
    for prop in ["og:title", "twitter:title"]:
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            meta.title = tag["content"].strip()
            break
    desc = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", property="og:description"
    )
    if desc and desc.get("content"):
        meta.description = desc["content"].strip()
    author = soup.find("meta", attrs={"name": "author"})
    if author and author.get("content"):
        meta.author = author["content"].strip()
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        meta.canonical = canonical["href"].strip()
    return meta


def extract_links(html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.scheme in ("http", "https"):
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            links.add(clean)
    return list(links)


def _jsonld_to_text(data: Any) -> str:
    """Flatten JSON-LD / embedded JSON into readable text."""
    parts: List[str] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("@context", "@type", "@id") and not isinstance(v, (dict, list)):
                    continue
                if isinstance(v, str) and len(v) > 40:
                    parts.append(v.strip())
                else:
                    walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                walk(item, depth + 1)
        elif isinstance(obj, str) and len(obj) > 40:
            parts.append(obj.strip())

    walk(data)
    # de-dupe preserving order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return "\n\n".join(out)


def extract_embedded_json(html: str) -> Optional[str]:
    """
    Tier 2: prefer JSON-LD and common SPA payloads (__NEXT_DATA__, etc.)
    over full DOM when they contain substantial article-like text.
    """
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            text = _jsonld_to_text(data)
            if text and len(text) > 120:
                return text
        except Exception:
            continue

    # Next.js / common SPA
    for script_id in ("__NEXT_DATA__", "__NUXT_DATA__", "__INITIAL_STATE__"):
        tag = soup.find("script", id=script_id)
        if not tag:
            continue
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip())
            text = _jsonld_to_text(data)
            if text and len(text) > 150:
                return text
        except Exception:
            continue

    return None


def html_to_result(html: str, url: str) -> ScrapeResult:
    if not html or len(html) < 200:
        return ScrapeResult(url=url, success=False, reason=FailureReason.EMPTY)

    metadata = extract_metadata(html, url)
    links = extract_links(html, url)

    # Tier 2: embedded structured data first
    embedded = extract_embedded_json(html)
    if embedded and len(embedded) > 200:
        return ScrapeResult(
            url=url,
            success=True,
            reason=FailureReason.SUCCESS,
            content=embedded,
            markdown=embedded,
            metadata=metadata,
            links=links,
        )

    markdown = ""
    try:
        markdown = (
            trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                output_format="markdown",
                url=url,
            )
            or ""
        )
    except Exception as e:
        logger.warning(f"trafilatura error: {e}")

    if not markdown or len(markdown.strip()) < 120:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        markdown = soup.get_text(separator="\n", strip=True)

    if not markdown or len(markdown.strip()) < 80:
        return ScrapeResult(
            url=url,
            success=False,
            reason=FailureReason.EMPTY,
            metadata=metadata,
            links=links,
        )

    return ScrapeResult(
        url=url,
        success=True,
        reason=FailureReason.SUCCESS,
        content=markdown,
        markdown=markdown,
        metadata=metadata,
        links=links,
    )
