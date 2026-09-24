"""Extraction-quality regression tests.

These reproduce two real bugs found while testing against live sites:

1. HTML entities leaking into markdown — a StepStone.de FAQ page's
   JSON-LD "answer" fields contained raw HTML (<a href=...> links),
   HTML-entity-encoded inside the JSON string. The old extractor decoded
   the JSON but never unescaped/stripped that inner HTML, so the returned
   "markdown" contained literal `&lt;a href=...&gt;` text.

2. Non-content JSON-LD silently overriding real page content — the old
   extractor tried embedded JSON-LD *before* trafilatura and returned any
   block over 200 chars, so an Organization's "about us" description (or
   similar site metadata) could replace a real article body.

Where possible these use synthetic HTML that reproduces the same JSON-LD
shape as the real pages, since only the extracted markdown (not the raw
HTML) was captured from the live sites.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractor import extract_embedded_json, html_to_result


def test_html_entities_stripped_from_jsonld_faq():
    """Reproduces the StepStone.de bug: a FAQPage JSON-LD block whose
    answer text contains HTML-entity-encoded <a> tags must come back as
    clean, readable text — not literal &lt;a href=...&gt; markup."""
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      "mainEntity": [{
        "@type": "Question",
        "name": "Wie viele offene Stellenangebote gibt es?",
        "acceptedAnswer": {
          "@type": "Answer",
          "text": "Aktuell gibt es auf &lt;a href=&apos;/&apos;&gt;StepStone&lt;/a&gt; 60 offene Stellenanzeigen f\\u00fcr Labview Jobs und noch einiges mehr an zus\\u00e4tzlichem Text, damit die Mindestl\\u00e4nge sicher \\u00fcberschritten wird."
        }
      }]
    }
    </script>
    </head><body><p>short</p></body></html>
    """
    embedded = extract_embedded_json(html)
    assert embedded is not None
    assert "&lt;" not in embedded, f"raw HTML entities leaked into output: {embedded!r}"
    assert "<a href" not in embedded, f"raw HTML tag leaked into output: {embedded!r}"
    assert "StepStone" in embedded
    assert "60 offene Stellenanzeigen" in embedded

    result = html_to_result(html, "https://www.stepstone.de/jobs/labview")
    assert result.success
    assert "&lt;" not in result.markdown
    assert "<a href" not in result.markdown


def test_trafilatura_preferred_over_organization_jsonld():
    """Reproduces the ordering bug: a page with both a real article body
    AND an unrelated Organization JSON-LD block must return the article,
    not the organization description — regardless of which one is longer."""
    org_ld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Organization",'
        '"description":"' + ("We are a large media company. " * 20) + '"}'
        "</script>"
    )
    article_html = (
        "<article><h1>Real headline</h1>"
        + "<p>" + ("The real article body the reader actually wants. " * 10) + "</p>"
        + "</article>"
    )
    html = f"<html><head>{org_ld}</head><body>{article_html}</body></html>"

    result = html_to_result(html, "https://news.test/a")
    assert result.success
    assert "real article body" in result.markdown
    assert "large media company" not in result.markdown


def test_faqpage_jsonld_used_when_trafilatura_finds_little():
    """FAQPage/Question/Answer content IS legitimate page content and
    should still be usable as a fallback when there's no real DOM body for
    trafilatura to extract (e.g. an SPA that renders the FAQ client-side)."""
    faq_ld = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":['
        '{"@type":"Question","name":"Q1","acceptedAnswer":{"@type":"Answer",'
        '"text":"' + ("This is a real FAQ answer with real content in it. " * 5) + '"}}'
        "]}"
        "</script>"
    )
    html = f"<html><head>{faq_ld}</head><body><div id='app'></div></body></html>"

    result = html_to_result(html, "https://faq.test/")
    assert result.success
    assert "real FAQ answer" in result.markdown


def test_organization_only_jsonld_not_used_as_last_resort_content():
    """Even when trafilatura AND the raw-text fallback would both be thin,
    a non-content JSON-LD type (Organization) must not become the
    returned markdown — extract_embedded_json() should simply decline it,
    letting html_to_result() fall through to the raw-text tier instead."""
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Organization","description":"'
        + ("Corporate boilerplate text. " * 20)
        + '"}</script></head><body><p>tiny</p></body></html>'
    )
    embedded = extract_embedded_json(html)
    assert embedded is None, f"non-content JSON-LD type should be skipped, got: {embedded!r}"