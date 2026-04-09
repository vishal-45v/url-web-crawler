"""
Unit tests for app/services/fetcher.py

Tests cover the two-tier fetch strategy:
  - httpx fast path for static pages (>= 1500 chars)
  - Playwright fallback for short 2xx responses (JS-rendered SPAs)
  - 4xx responses returned as-is without Playwright (bot-blocked pages)
  - Non-HTML content type raises ValueError (→ 415)
  - Network failures fall back to Playwright
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.fetcher import fetch, _ClientBlockedError

LARGE_HTML = "<html><body>" + "content " * 300 + "</body></html>"   # >> 1500 chars
SMALL_HTML = "<html><body>tiny</body></html>"                        # << 1500 chars
BLOCKED_HTML = "<html><body>Access Denied</body></html>"             # 4xx error page
PLAYWRIGHT_HTML = "<html><body>" + "js-rendered " * 300 + "</body></html>"


# ---------------------------------------------------------------------------
# httpx fast path
# ---------------------------------------------------------------------------

async def test_httpx_fast_path_returned_for_large_response():
    """When httpx returns >= 1500 chars, Playwright must NOT be called."""
    with patch("app.services.fetcher._fetch_httpx", new=AsyncMock(return_value=LARGE_HTML)):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock()) as mock_pw:
            result = await fetch("https://example.com")
            assert result == LARGE_HTML
            mock_pw.assert_not_called()


# ---------------------------------------------------------------------------
# Playwright fallback for JS-rendered pages
# ---------------------------------------------------------------------------

async def test_playwright_fallback_triggered_for_short_2xx_response():
    """Short 2xx response (JS-skeleton) must trigger Playwright fallback."""
    with patch("app.services.fetcher._fetch_httpx", new=AsyncMock(return_value=SMALL_HTML)):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock(return_value=PLAYWRIGHT_HTML)) as mock_pw:
            result = await fetch("https://example.com")
            assert result == PLAYWRIGHT_HTML
            mock_pw.assert_called_once_with("https://example.com")


async def test_playwright_fallback_triggered_when_httpx_raises_network_error():
    """Unrecoverable httpx network error must fall back to Playwright."""
    with patch("app.services.fetcher._fetch_httpx", new=AsyncMock(side_effect=ConnectionError("refused"))):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock(return_value=PLAYWRIGHT_HTML)) as mock_pw:
            result = await fetch("https://example.com")
            assert result == PLAYWRIGHT_HTML
            mock_pw.assert_called_once()


# ---------------------------------------------------------------------------
# 4xx handling — bot-blocked pages must NOT trigger Playwright
# ---------------------------------------------------------------------------

async def test_4xx_response_returned_without_playwright_fallback():
    """
    A 4xx response is a definitive server rejection.
    Playwright cannot bypass an HTTP-level block — attempting it wastes ~2s.
    The error HTML should be returned directly so the extractor can still
    process it (e.g. extract the 'Access Denied' page title/description).
    """
    with patch("app.services.fetcher._fetch_httpx",
               new=AsyncMock(side_effect=_ClientBlockedError(BLOCKED_HTML))):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock()) as mock_pw:
            result = await fetch("https://example.com")
            assert result == BLOCKED_HTML
            mock_pw.assert_not_called()


async def test_4xx_response_html_preserved():
    """The full HTML body of a 4xx response is passed to the caller."""
    error_page = "<html><body><h1>403 Forbidden</h1><p>You are blocked.</p></body></html>"
    with patch("app.services.fetcher._fetch_httpx",
               new=AsyncMock(side_effect=_ClientBlockedError(error_page))):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock()):
            result = await fetch("https://example.com")
            assert "403 Forbidden" in result
            assert "You are blocked." in result


# ---------------------------------------------------------------------------
# Non-HTML content type → ValueError → 415 upstream
# ---------------------------------------------------------------------------

async def test_non_html_content_type_raises_value_error():
    """PDF / image content types must raise ValueError (mapped to 415 by the endpoint)."""
    with patch("app.services.fetcher._fetch_httpx",
               new=AsyncMock(side_effect=ValueError("Unsupported content-type: application/pdf"))):
        with pytest.raises(ValueError, match="Unsupported content-type"):
            await fetch("https://example.com/report.pdf")


async def test_value_error_not_swallowed_by_playwright_fallback():
    """ValueError (wrong content-type) must propagate — not silently fall back to Playwright."""
    with patch("app.services.fetcher._fetch_httpx",
               new=AsyncMock(side_effect=ValueError("Unsupported content-type: image/png"))):
        with patch("app.services.fetcher._fetch_playwright", new=AsyncMock()) as mock_pw:
            with pytest.raises(ValueError):
                await fetch("https://example.com/photo.png")
            mock_pw.assert_not_called()
