"""URL safety guard: scheme allowlist + SSRF (private/internal IP) protection.

Call quick_reject() for a fast, sync, no-DNS check on every URL before it
enters the policy layer. Call assert_public_host() immediately before every
real network fetch (including after following a redirect) — quick_reject
alone does not catch DNS rebinding, where a public hostname resolves to a
private address.
"""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

_LOCAL_SUFFIXES = (".localhost",)
_LOCAL_HOSTS = frozenset({"localhost"})


class UnsafeURLError(ValueError):
    """Raised when a URL fails the scheme or SSRF safety check."""


def normalized_host(url: str) -> str | None:
    """Return the lowercase hostname if the URL uses an allowed scheme
    and has a host, else None."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return None
    host = parts.hostname
    return host.rstrip(".").lower() if host else None


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # is_global already excludes private/loopback/link-local/reserved/
    # unspecified ranges on modern Python, but check the others explicitly
    # too so the intent is clear and future stdlib changes stay covered.
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def quick_reject(url: str) -> str | None:
    """Fast, sync, no-DNS check. Returns a rejection reason, or None if the
    URL passes this stage (still needs assert_public_host before fetching)."""
    host = normalized_host(url)
    if host is None:
        return "invalid_scheme_or_host"
    if host in _LOCAL_HOSTS or host.endswith(_LOCAL_SUFFIXES):
        return "localhost"
    ip = _as_ip(host)
    if ip is not None and _is_blocked_ip(ip):
        return "blocked_ip_literal"
    return None


async def assert_public_host(url: str, *, timeout: float = 5.0) -> None:
    """Resolve the URL's host and raise UnsafeURLError unless every
    resolved address is a public, routable IP address."""
    reason = quick_reject(url)
    if reason:
        raise UnsafeURLError(reason)
    host = normalized_host(url)
    assert host is not None
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=timeout)
    except UnsafeURLError:
        raise
    except Exception as e:
        raise UnsafeURLError(f"dns_resolution_failed: {e}") from e
    if not infos:
        raise UnsafeURLError("dns_no_results")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise UnsafeURLError(f"blocked_ip:{ip}")
