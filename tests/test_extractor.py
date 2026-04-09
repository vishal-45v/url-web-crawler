"""
Unit tests for app/services/extractor.py

Tests cover metadata extraction, fallback behaviour, noise removal,
and edge cases like missing or empty tags.
"""
from app.services.extractor import extract


# ---------------------------------------------------------------------------
# Happy-path: full metadata present
# ---------------------------------------------------------------------------

def test_title_extracted(full_html):
    result = extract(full_html, "https://example.com")
    assert result["title"] == "Sample Product Page"


def test_meta_description_extracted(full_html):
    result = extract(full_html, "https://example.com")
    assert result["description"] == "A quality kitchen toaster"


def test_og_tags_extracted(full_html):
    result = extract(full_html, "https://example.com")
    assert result["og_title"] == "Sample OG Title"
    assert result["og_description"] == "Sample OG Description"
    assert result["og_image"] == "https://example.com/toaster.png"


def test_canonical_url_extracted(full_html):
    result = extract(full_html, "https://example.com/other")
    assert result["canonical_url"] == "https://example.com/toaster"


def test_meta_keywords_extracted(full_html):
    result = extract(full_html, "https://example.com")
    assert result["meta_keywords"] == ["toaster", "kitchen", "appliance"]


def test_word_count_reflects_visible_text(full_html):
    result = extract(full_html, "https://example.com")
    # Script/style/noscript content must NOT count toward word_count
    assert result["word_count"] > 0
    assert "tracking" not in result["body_text"]


# ---------------------------------------------------------------------------
# Fallback behaviour
# ---------------------------------------------------------------------------

def test_description_falls_back_to_body_text(no_meta_html):
    result = extract(no_meta_html, "https://example.com")
    assert result["description"] is not None
    assert "body text" in result["description"].lower()


def test_canonical_url_falls_back_to_input_url(no_meta_html):
    result = extract(no_meta_html, "https://example.com/input-url")
    assert result["canonical_url"] == "https://example.com/input-url"


def test_description_first_200_chars_when_no_meta():
    long_body = "word " * 100  # 500 chars
    html = f"<html><head></head><body>{long_body}</body></html>"
    result = extract(html, "https://example.com")
    assert len(result["description"]) <= 200


# ---------------------------------------------------------------------------
# Missing / empty tags return safe defaults
# ---------------------------------------------------------------------------

def test_missing_og_tags_return_none(minimal_html):
    result = extract(minimal_html, "https://example.com")
    assert result["og_title"] is None
    assert result["og_description"] is None
    assert result["og_image"] is None


def test_missing_keywords_returns_empty_list(minimal_html):
    result = extract(minimal_html, "https://example.com")
    assert result["meta_keywords"] == []


def test_missing_title_returns_none():
    html = "<html><head></head><body>No title here</body></html>"
    result = extract(html, "https://example.com")
    assert result["title"] is None


# ---------------------------------------------------------------------------
# Noise removal: scripts, styles, noscript stripped from body text
# ---------------------------------------------------------------------------

def test_script_content_stripped_from_body():
    html = """
    <html><body>
      <p>Visible content</p>
      <script>var secret = 'should not appear';</script>
    </body></html>
    """
    result = extract(html, "https://example.com")
    assert "should not appear" not in result["body_text"]
    assert "Visible content" in result["body_text"]


def test_style_content_stripped_from_body():
    html = """
    <html><body>
      <p>Real text</p>
      <style>.class { color: red; font-size: 12px; }</style>
    </body></html>
    """
    result = extract(html, "https://example.com")
    assert "font-size" not in result["body_text"]
    assert "Real text" in result["body_text"]


def test_noscript_content_stripped_from_body():
    html = """
    <html><body>
      <p>Main content</p>
      <noscript>Please enable JavaScript to view this page.</noscript>
    </body></html>
    """
    result = extract(html, "https://example.com")
    assert "Please enable JavaScript" not in result["body_text"]
    assert "Main content" in result["body_text"]


# ---------------------------------------------------------------------------
# Word count
# ---------------------------------------------------------------------------

def test_word_count_exact():
    html = "<html><body><p>one two three four five</p></body></html>"
    result = extract(html, "https://example.com")
    assert result["word_count"] == 5


def test_word_count_zero_for_empty_body():
    html = "<html><head></head><body></body></html>"
    result = extract(html, "https://example.com")
    assert result["word_count"] == 0


# ---------------------------------------------------------------------------
# Whitespace normalisation
# ---------------------------------------------------------------------------

def test_whitespace_collapsed_in_title():
    html = "<html><head><title>  Extra   Spaces  </title></head><body>text</body></html>"
    result = extract(html, "https://example.com")
    assert result["title"] == "Extra Spaces"
