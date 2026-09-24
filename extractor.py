from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, List, Optional
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup
from loguru import logger

from models import FailureReason, PageMetadata, ScrapeResult

# schema.org @type values worth treating as page *content*. Anything else
# found in a JSON-LD block (Organization, WebSite, BreadcrumbList, Person,
# ImageObject, ...) is metadata about the page or the publisher, not the
# page's content, and must never become the returned markdown even if it's
# long — that was the bug where an Organization's "about us" description
# silently replaced a real article body.
CONTENT_LD_TYPES = {
    "article", "newsarticle", "blogposting", "report",
    "faqpage", "question", "answer", "qapage",
    "product", "review", "jobposting", "recipe",
}

# Minimum length, in characters, before a candidate extraction is trusted
# as "real" content rather than a stub (a nav label, a short caption, etc).
MIN_CONTENT_CHARS = 120
MIN_FALLBACK_CHARS = 80


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


def _clean_embedded_text(value: str) -> str:
    """HTML-unescape a string pulled out of a JSON/JSON-LD value and, if it
    still contains markup (sites commonly embed raw HTML inside a JSON-LD
    text field, e.g. FAQ answers with <a> links), strip the tags and keep
    only the visible text.

    Without this, an entity-encoded anchor tag like
        &lt;a href=&apos;/&apos;&gt;StepStone&lt;/a&gt;
    decodes to a literal <a href='/'>StepStone</a> string sitting in the
    "markdown" output — readable by a human skimming raw text, but noisy
    and token-wasteful for an LLM agent, and not valid markdown either.
    """
    if not value:
        return value
    unescaped = html_lib.unescape(value)
    if "<" in unescaped and ">" in unescaped:
        try:
            soup = BeautifulSoup(unescaped, "lxml")
            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                return text
        except Exception:
            pass
    return unescaped


def _collect_ld_types(obj: Any, types: set[str]) -> None:
    """Recursively collect every schema.org @type found in a JSON-LD graph
    (lowercased), so the caller can decide whether this block is actually
    page content or just site/organization metadata."""
    if isinstance(obj, dict):
        t = obj.get("@type")
        if isinstance(t, str):
            types.add(t.lower())
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.add(x.lower())
        for v in obj.values():
            _collect_ld_types(v, types)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ld_types(item, types)


def _jsonld_to_text(data: Any) -> str:
    """Flatten JSON-LD / embedded JSON into readable text, cleaning each
    leaf string as we go (see _clean_embedded_text)."""
    parts: List[str] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("@context", "@type", "@id") and not isinstance(v, (dict, list)):
                    continue
                if isinstance(v, str) and len(v) > 40:
                    cleaned = _clean_embedded_text(v)
                    if cleaned:
                        parts.append(cleaned)
                else:
                    walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj[:50]:
                walk(item, depth + 1)
        elif isinstance(obj, str) and len(obj) > 40:
            cleaned = _clean_embedded_text(obj)
            if cleaned:
                parts.append(cleaned)

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
    Fallback extraction from embedded structured data (JSON-LD, common SPA
    payloads like __NEXT_DATA__). This is intentionally a FALLBACK, tried
    only when trafilatura's DOM-based extraction comes back too short —
    see html_to_result(). Structured data is frequently written by a
    different team than the page copy (analytics/SEO tooling vs. content),
    covers organization/site metadata as often as it covers the actual
    page content, and its text fields sometimes contain raw embedded HTML.
    Using it as a *primary* source previously let an Organization's "about
    us" blurb silently replace a real article body.
    """
    soup = BeautifulSoup(html, "lxml")

    # JSON-LD — restricted to content-bearing @type values (see
    # CONTENT_LD_TYPES). A block whose only types are things like
    # Organization, WebSite, or BreadcrumbList is site metadata, not page
    # content, and must not be returned even if it's long.
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        types: set[str] = set()
        _collect_ld_types(data, types)
        if types and not (types & CONTENT_LD_TYPES):
            logger.debug(f"Skipping JSON-LD block with non-content types: {types}")
            continue
        text = _jsonld_to_text(data)
        if text and len(text) > MIN_CONTENT_CHARS:
            return text

    # Next.js / common SPA payloads — no @type discipline available here,
    # so these are trusted at a higher length threshold instead.
    for script_id in ("__NEXT_DATA__", "__NUXT_DATA__", "__INITIAL_STATE__"):
        tag = soup.find("script", id=script_id)
        if not tag:
            continue
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw.strip())
        except Exception:
            continue
        text = _jsonld_to_text(data)
        if text and len(text) > 150:
            return text

    return None


def html_to_result(html: str, url: str) -> ScrapeResult:
    if not html or len(html) < 200:
        return ScrapeResult(url=url, success=False, reason=FailureReason.EMPTY)

    metadata = extract_metadata(html, url)
    links = extract_links(html, url)

    # Tier 1: trafilatura — DOM-based main-content extraction, tuned for
    # exactly this job and already returns clean text. This runs FIRST.
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

    path_note = "trafilatura"

    # Tier 2: embedded structured data — ONLY as a fallback, when
    # trafilatura came back empty or too short (e.g. a client-rendered SPA
    # with little server-side HTML). See extract_embedded_json() for why
    # this must not run first.
    if not markdown or len(markdown.strip()) < MIN_CONTENT_CHARS:
        embedded = extract_embedded_json(html)
        if embedded and len(embedded.strip()) >= MIN_CONTENT_CHARS:
            markdown = embedded
            path_note = "embedded_json_fallback"

    # Tier 3: raw text strip — last resort.
    if not markdown or len(markdown.strip()) < MIN_FALLBACK_CHARS:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        markdown = soup.get_text(separator="\n", strip=True)
        path_note = "raw_text_fallback"

    if not markdown or len(markdown.strip()) < MIN_FALLBACK_CHARS:
        return ScrapeResult(
            url=url,
            success=False,
            reason=FailureReason.EMPTY,
            metadata=metadata,
            links=links,
        )

    logger.debug(f"Extraction path for {url}: {path_note}")
    return ScrapeResult(
        url=url,
        success=True,
        reason=FailureReason.SUCCESS,
        content=markdown,
        markdown=markdown,
        metadata=metadata,
        links=links,
    )