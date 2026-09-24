"""Tests for the SSRF/scheme guard (security.py) and the policy hostname fix."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import DomainPolicy
from security import assert_public_host, quick_reject, UnsafeURLError


@pytest.mark.parametrize(
    "url",
    [
        "file://localhost/etc/passwd",
        "http://127.0.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://localhost/",
        "http://[::1]/",
        "ftp://example.com/",
    ],
)
def test_quick_reject_blocks_unsafe(url):
    assert quick_reject(url) is not None


@pytest.mark.parametrize("url", ["https://example.com/", "http://example.com/a?b=1"])
def test_quick_reject_allows_public(url):
    assert quick_reject(url) is None


def test_policy_denylist_survives_port_and_userinfo():
    p = DomainPolicy(Path("/tmp") / "pageplain_test_policy.json")
    p.denylist = {"example.com"}
    for url in ("https://example.com:443/", "https://user@example.com/", "https://example.com/"):
        ok, reason = p.check_url(url)
        assert not ok and reason == "denylist"


def test_policy_rejects_file_and_private_ip():
    p = DomainPolicy(Path("/tmp") / "pageplain_test_policy2.json")
    for url in ("file:///etc/passwd", "http://127.0.0.1/", "http://169.254.169.254/"):
        ok, _ = p.check_url(url)
        assert not ok


@pytest.mark.asyncio
async def test_assert_public_host_rejects_private():
    with pytest.raises(UnsafeURLError):
        await assert_public_host("http://127.0.0.1/")


@pytest.mark.asyncio
async def test_assert_public_host_allows_public():
    # example.com is a stable, non-routable-back IANA reserved domain used
    # for documentation; it resolves to a real public IP.
    await assert_public_host("https://example.com/")
