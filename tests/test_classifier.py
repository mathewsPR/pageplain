"""Tier 1.5: unit tests for challenge classifier and hard-domain helper."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawler import _is_hard_domain
from router import classify_protection


def test_hard_captcha():
    html = "<html><body>Please complete the security check with turnstile</body></html>"
    assert classify_protection(html) == "hard"


def test_soft_block():
    html = "<html><body>Just a moment... checking your browser</body></html>"
    assert classify_protection(html, 403) == "soft"


def test_none():
    html = "<html><body><h1>Hello world article content here</h1><p>More text</p></body></html>"
    assert classify_protection(html, 200) == "none"


def test_hard_domains():
    assert _is_hard_domain("de.indeed.com") is True
    assert _is_hard_domain("www.linkedin.com") is True
    assert _is_hard_domain("example.com") is False
    assert _is_hard_domain("mageoai.com") is False
