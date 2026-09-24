#!/usr/bin/env python3
"""
v1.0 evaluation harness — public-style scrape success + extraction quality proxies.

Public references this mimics:
  - Firecrawl scrape-content style: success rate on open URLs
  - Trafilatura / main-content: non-empty main text, length, title presence
  - Spider/Crawl4AI blogs: static vs light-JS open sites (not hard anti-bot)

Usage:
  python scripts/eval_benchmark.py
  python scripts/eval_benchmark.py --out data/eval_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Open / semi-open URLs used in community benches (no hard WAF expected)
EVAL_URLS: List[Dict[str, str]] = [
    {"url": "https://example.com", "tier": "static", "expect": "success"},
    {"url": "https://www.python.org/", "tier": "static", "expect": "success"},
    {"url": "https://www.wikipedia.org/", "tier": "static", "expect": "success"},
    {"url": "https://quotes.toscrape.com/", "tier": "static", "expect": "success"},
    {"url": "https://books.toscrape.com/", "tier": "static", "expect": "success"},
    {"url": "https://httpbin.org/html", "tier": "static", "expect": "success"},
    {"url": "https://news.ycombinator.com/", "tier": "static", "expect": "success"},
    {"url": "https://mageoai.com/", "tier": "static", "expect": "success"},
    {"url": "https://www.bbc.com/news", "tier": "news", "expect": "success"},
    {"url": "https://www.stepstone.de/", "tier": "jobs_open", "expect": "success_or_partial"},
    # Policy / hard — success means correct skip, not content
    {"url": "https://de.indeed.com/", "tier": "hard", "expect": "skip"},
]


@dataclass
class Row:
    url: str
    tier: str
    expect: str
    ok: bool
    reason: str
    path: str | None
    chars: int
    has_title: bool
    latency_s: float
    pass_metric: bool
    note: str


def score_row(meta: Dict[str, str], result: Dict[str, Any], latency: float) -> Row:
    expect = meta["expect"]
    ok = bool(result.get("ok"))
    reason = str(result.get("reason") or "")
    chars = int(result.get("chars") or 0)
    title = bool(result.get("title"))
    path = result.get("path_used")

    passed = False
    note = ""
    if expect == "success":
        passed = ok and chars >= 50
        note = "need ok + >=50 chars" if not passed else "ok"
    elif expect == "success_or_partial":
        passed = ok and chars >= 20
        note = "partial allowed"
    elif expect == "skip":
        passed = (not ok) and reason.upper() in (
            "HARD_DOMAIN",
            "DENIED",
            "DENYLIST",
            "HARD_DOMAIN",
        )
        # also accept policy phrasing
        if not passed and not ok and "HARD" in reason.upper():
            passed = True
        note = "correct skip" if passed else f"unexpected {reason}"

    return Row(
        url=meta["url"],
        tier=meta["tier"],
        expect=expect,
        ok=ok,
        reason=reason,
        path=path,
        chars=chars,
        has_title=title,
        latency_s=round(latency, 3),
        pass_metric=passed,
        note=note,
    )


async def run_eval() -> Dict[str, Any]:
    tmp = Path(tempfile.mkdtemp())
    from config import settings

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
    rows: List[Row] = []
    t0 = time.monotonic()

    for meta in EVAL_URLS:
        t1 = time.monotonic()
        result = await svc.scrape_url(meta["url"], max_chars=8000)
        rows.append(score_row(meta, result, time.monotonic() - t1))

    # cache microbench
    t1 = time.monotonic()
    cached = await svc.scrape_url("https://example.com", max_chars=8000)
    cache_latency = time.monotonic() - t1
    cache_hit = bool(cached.get("from_cache"))

    await svc.stop()

    by_tier: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_tier.setdefault(r.tier, {"n": 0, "pass": 0})
        by_tier[r.tier]["n"] += 1
        if r.pass_metric:
            by_tier[r.tier]["pass"] += 1

    content_rows = [r for r in rows if r.expect != "skip"]
    content_pass = sum(1 for r in content_rows if r.pass_metric)
    skip_rows = [r for r in rows if r.expect == "skip"]
    skip_pass = sum(1 for r in skip_rows if r.pass_metric)

    report = {
        "version": (ROOT / "VERSION").read_text().strip()
        if (ROOT / "VERSION").exists()
        else "unknown",
        "wall_s": round(time.monotonic() - t0, 2),
        "urls": len(rows),
        "content_success_rate": round(content_pass / max(len(content_rows), 1), 4),
        "policy_skip_accuracy": round(skip_pass / max(len(skip_rows), 1), 4),
        "overall_pass_rate": round(sum(1 for r in rows if r.pass_metric) / len(rows), 4),
        "avg_latency_s": round(sum(r.latency_s for r in rows) / len(rows), 3),
        "cache_hit": cache_hit,
        "cache_latency_s": round(cache_latency, 4),
        "by_tier": by_tier,
        "rows": [asdict(r) for r in rows],
        "public_benchmark_refs": [
            "Firecrawl HF scrape-content-dataset-v1 (success-style)",
            "Community Spider/Crawl4AI open-URL success rates",
            "Trafilatura-style: non-empty main text proxy (chars>=50)",
        ],
        "notes": (
            "This is a reproducible open-site suite, not full anti-bot competition. "
            "Hard sites are scored on correct skip under free-tier policy."
        ),
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "eval_report.json")
    args = ap.parse_args()
    report = asyncio.run(run_eval())

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n===== EVAL REPORT =====")
    print(f"version: {report['version']}")
    print(f"content_success_rate: {report['content_success_rate']:.1%} "
          f"({sum(1 for r in report['rows'] if r['expect']!='skip' and r['pass_metric'])}/"
          f"{sum(1 for r in report['rows'] if r['expect']!='skip')})")
    print(f"policy_skip_accuracy: {report['policy_skip_accuracy']:.1%}")
    print(f"overall_pass_rate:    {report['overall_pass_rate']:.1%}")
    print(f"avg_latency_s:        {report['avg_latency_s']}")
    print(f"cache_hit:            {report['cache_hit']} ({report['cache_latency_s']}s)")
    print("by_tier:", report["by_tier"])
    print("\nPer URL:")
    for r in report["rows"]:
        flag = "PASS" if r["pass_metric"] else "FAIL"
        print(f"  {flag} {r['reason']:12} {r['latency_s']:5.1f}s "
              f"chars={r['chars']:5} {r['url']}")
    print(f"\nWrote {args.out}")
    # Fail CI if content success < 70% or policy skip wrong
    if report["content_success_rate"] < 0.7:
        return 1
    if report["policy_skip_accuracy"] < 1.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
