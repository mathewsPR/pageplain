import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractor import html_to_result, extract_embedded_json


def test_jsonld_preferred():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "Article", "headline": "Test Article Title Here",
     "articleBody": "This is a long enough body of text that should be extracted as primary content for the page and exceed the minimum length threshold easily."}
    </script>
    </head><body><p>ignore</p></body></html>
    """
    embedded = extract_embedded_json(html)
    assert embedded is not None
    assert "long enough body" in embedded

    result = html_to_result(html, "https://example.com/a")
    assert result.success
    assert "long enough body" in result.markdown


def test_empty_html():
    result = html_to_result("<html></html>", "https://example.com")
    assert result.success is False
