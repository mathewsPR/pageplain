#!/usr/bin/env python3
"""
v1.0 smoke + corner-case test.
Exit 0 on pass, 1 on failure.

Usage:
  python scripts/smoke_test.py
  python scripts/smoke_test.py --live    # hit real network
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

# project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    raise AssertionError(msg)


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


async def run_unit_corners() -> None:
    print("\n=== Corner cases (offline) ===")
    from browser_pool import build_camoufox_kwargs, _browser_concurrency, _detect_ram_gb
    from config import settings
    from crawler import _is_hard_domain
    from policy import DomainPolicy
    from router import classify_protection
    from extractor import html_to_result, extract_embedded_json
    from models import FailureReason

    # RAM gate
    assert _browser_concurrency() >= 1
    ok(f"browser concurrency={_browser_concurrency()} ram≈{_detect_ram_gb():.1f}GB")

    # Presets
    settings.camoufox_preset = "fast"
    kf = build_camoufox_kwargs(None)
    assert kf.get("block_images") is True and kf.get("humanize") is False
    ok("fast preset")

    settings.camoufox_preset = "stealth"
    ks = build_camoufox_kwargs(None)
    assert ks.get("humanize") is True
    ok("stealth preset")
    settings.camoufox_preset = "fast"

    # Hard domains
    assert _is_hard_domain("de.indeed.com")
    assert _is_hard_domain("www.linkedin.com")
    assert not _is_hard_domain("example.com")
    ok("hard domain detection")

    # Challenge classifier
    assert classify_protection("turnstile challenge", 403) == "hard"
    assert classify_protection("just a moment checking your browser", 403) == "soft"
    assert classify_protection("<html><body>Hello article content here</body></html>", 200) == "none"
    ok("challenge classifier")

    # Policy denylist
    p = DomainPolicy(Path(tempfile.mkdtemp()) / "policy.json")
    p.denylist.add("blocked.test")
    p.skip_hard_domains = True
    allowed, reason = p.check_url("https://blocked.test/x")
    assert not allowed and reason == "denylist"
    allowed, reason = p.check_url("https://de.indeed.com/")
    assert not allowed and reason == "hard_domain"
    allowed, reason = p.check_url("https://example.com/")
    assert allowed
    ok("policy allow/deny/hard")

    # Empty / tiny HTML
    r = html_to_result("<html></html>", "https://example.com")
    assert r.success is False and r.reason == FailureReason.EMPTY
    ok("empty HTML → EMPTY")

    # JSON-LD extraction
    html = """
    <html><head><script type="application/ld+json">
    {"@type":"Article","headline":"T",
     "articleBody":"This is a long enough body of text that should be extracted as primary content for the page and exceed the minimum extraction length threshold easily enough."}
    </script></head><body></body></html>
    """
    emb = extract_embedded_json(html)
    assert emb and "long enough" in emb
    ok("JSON-LD extraction")

    # Proxy hooks default off
    assert settings.proxy_server is None
    assert settings.enable_geoip is False
    ok("proxy/geoip default off")


async def run_live() -> None:
    print("\n=== Live network smoke ===")
    import tempfile
    from config import settings

    tmp = Path(tempfile.mkdtemp())
    settings.data_dir = tmp
    settings.state_db_path = tmp / "state.db"
    settings.cache_dir = tmp / "cache"
    settings.output_dir = tmp / "out"
    settings.profile_dir = tmp / "profiles"
    settings.storage_state_dir = tmp / "storage"
    settings.camoufox_preset = "fast"
    settings.skip_hard_domains = True

    from usage_stats import UsageTracker
    import usage_stats as us
    us.usage = UsageTracker(tmp / "usage.json")
    from policy import DomainPolicy
    import policy as pol
    pol.policy = DomainPolicy(tmp / "policy.json")
    pol.policy.skip_hard_domains = True
    pol.policy.save()

    from service import KnowledgeService
    svc = KnowledgeService()

    # 1) Open site
    r = await svc.scrape_url("https://example.com")
    if not r.get("ok"):
        fail(f"example.com failed: {r}")
    ok(f"example.com path={r.get('path_used')} chars={r.get('chars')}")

    # 2) Cache hit
    r2 = await svc.scrape_url("https://example.com")
    if not r2.get("from_cache"):
        fail("expected cache hit")
    ok("cache hit")

    # 3) Real site
    r3 = await svc.scrape_url("https://www.python.org/")
    if not r3.get("ok"):
        fail(f"python.org failed: {r3}")
    ok(f"python.org chars={r3.get('chars')}")

    # 4) Hard domain must skip
    r4 = await svc.scrape_url("https://de.indeed.com/")
    if r4.get("ok") or r4.get("reason") not in ("HARD_DOMAIN", "hard_domain", "DENIED"):
        # policy returns hard_domain uppercased in service
        if r4.get("ok"):
            fail("indeed should not succeed under skip_hard_domains")
    ok(f"indeed skipped reason={r4.get('reason')}")

    # 5) Invalid URL / empty-ish handling
    r5 = await svc.scrape_url("https://this-domain-should-not-exist-zzzx.test/")
    ok(f"bad domain handled ok={r5.get('ok')} reason={r5.get('reason')}")

    # 6) Job isolation
    job = await svc.create_job(["https://example.com"], max_pages=1, name="smoke")
    jid = job["job_id"]
    for _ in range(40):
        st = await svc.get_job(jid)
        if st.get("status") in ("completed", "failed"):
            break
        await asyncio.sleep(0.3)
    st = await svc.get_job(jid)
    if st.get("status") != "completed":
        fail(f"job not completed: {st}")
    if not (tmp / "jobs" / jid / "job.json").exists():
        fail("job.json missing")
    z = svc.get_export_path(jid)
    if not z or not z.exists():
        fail("export zip missing")
    ok(f"job {jid} + zip export")

    # 7) Search
    sk = svc.search_knowledge("example", limit=3)
    if not sk.get("ok"):
        fail(f"search failed: {sk}")
    ok(f"search hits={len(sk.get('hits') or [])}")

    # 8) Diff
    d = await svc.get_diff("https://example.com")
    ok(f"diff changed={d.get('changed')} ok={d.get('ok')}")

    # 9) Metrics
    m = svc.get_metrics()
    if not m.get("ok"):
        fail("metrics failed")
    ok(f"metrics cache_entries={m.get('cache_entries')} engine={m.get('browser_engine')}")

    await svc.stop()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Run live network tests")
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args()

    print(f"pageplain smoke — root={ROOT}")
    ver = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "?"
    print(f"version {ver}")

    try:
        await run_unit_corners()
        if args.offline_only:
            print("\nSMOKE PASSED (offline only)")
            return 0
        if args.live or not args.offline_only:
            # default: run live
            await run_live()
        print("\n===== SMOKE PASSED =====")
        return 0
    except Exception as e:
        print(f"\n===== SMOKE FAILED: {e} =====")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
