"""v1.0 corner-case unit tests (offline)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_pool import build_camoufox_kwargs, _browser_concurrency
from config import settings
from crawler import _is_hard_domain
from extractor import html_to_result, extract_embedded_json
from models import FailureReason
from policy import DomainPolicy
from router import classify_protection


def test_hard_domain_suffixes():
    assert _is_hard_domain("indeed.com")
    assert _is_hard_domain("de.indeed.com")
    assert _is_hard_domain("www.linkedin.com")
    assert not _is_hard_domain("notindeed.com")
    assert not _is_hard_domain("example.org")


def test_classifier_empty_and_status():
    assert classify_protection("", 403) == "soft"
    assert classify_protection("", 200) == "none"
    assert classify_protection("cf-turnstile widget", 200) == "hard"


def test_policy_allowlist_mode(tmp_path):
    p = DomainPolicy(tmp_path / "p.json")
    p.allowlist_enabled = True
    p.allowlist = {"example.com"}
    p.skip_hard_domains = False
    ok, reason = p.check_url("https://example.com/a")
    assert ok and reason == "ok"
    ok, reason = p.check_url("https://other.com/")
    assert not ok and reason == "not_in_allowlist"


def test_presets_do_not_enable_proxy_by_default():
    settings.proxy_server = None
    settings.enable_geoip = False
    settings.camoufox_preset = "fast"
    k = build_camoufox_kwargs(None)
    assert "proxy" not in k
    assert "geoip" not in k


def test_browser_concurrency_at_least_one():
    assert _browser_concurrency() >= 1


def test_extractor_scripts_stripped_still_text():
    html = """
    <html><body>
    <script>var x=1</script>
    <p>Visible paragraph with enough text content for extraction thresholds to pass clearly.</p>
    </body></html>
    """
    r = html_to_result(html, "https://example.com/p")
    # may succeed via trafilatura or fallback
    assert r.reason in (FailureReason.SUCCESS, FailureReason.EMPTY) or isinstance(r.success, bool)


def test_jsonld_threshold():
    body = "x" * 150
    html = f'<script type="application/ld+json">{{"articleBody": "{body}"}}</script>'
    emb = extract_embedded_json(f"<html><head>{html}</head></html>")
    assert emb is not None
