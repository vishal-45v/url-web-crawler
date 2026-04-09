import asyncio
import httpx
from playwright.async_api import async_playwright
from app.config.settings import settings

HEADERS = {
    "User-Agent": "MetadataCrawler/1.0 (educational crawler)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class _ClientBlockedError(Exception):
    """
    Raised when the target server explicitly rejects the request (4xx/5xx).
    Signals to fetch() that a Playwright fallback won't help — the site is blocking
    the request at the HTTP level, not JS-rendering it. The HTML error page is
    preserved so the extractor can still process whatever content was returned.
    """
    def __init__(self, html: str):
        self.html = html
        super().__init__(html)


async def _fetch_httpx(url: str) -> str:
    """Fast path: static HTML via httpx with retry + backoff."""
    for attempt in range(settings.max_retries):
        try:
            async with httpx.AsyncClient(
                headers=HEADERS,
                follow_redirects=True,
                max_redirects=settings.max_redirects,
                timeout=settings.request_timeout,
            ) as client:
                response = await client.get(url)

                if response.status_code == 429:
                    await asyncio.sleep(2 ** attempt)
                    continue

                content_type = response.headers.get("content-type", "")
                if "text/html" not in content_type:
                    raise ValueError(f"Unsupported content-type: {content_type}")

                # 4xx/5xx: server explicitly rejected the request.
                # Playwright won't bypass an HTTP-level block — return the error
                # page HTML directly instead of wasting a full browser launch.
                if response.status_code >= 400:
                    raise _ClientBlockedError(response.text)

                return response.text

        except (ValueError, _ClientBlockedError):
            raise
        except Exception:
            if attempt == settings.max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)

    return ""


async def _fetch_playwright(url: str) -> str:
    """Fallback path: headless Chromium for JS-rendered pages (e.g. Amazon SPAs)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers(HEADERS)
        await page.goto(
            url,
            wait_until="networkidle",
            timeout=settings.request_timeout * 1000,
        )
        content = await page.content()
        await browser.close()
        return content


async def fetch(url: str) -> str:
    """
    Two-tier fetch strategy:
      1. httpx  — fast, low overhead, sufficient for static pages
      2. Playwright — fallback only when httpx returns a short 2xx response
                      (<1500 chars), which indicates a JS-rendered SPA shell.

    4xx responses (bot-blocked pages) are returned as-is — Playwright cannot
    bypass an HTTP-level block and attempting it wastes ~2s per request.
    """
    try:
        html = await _fetch_httpx(url)
        if len(html) >= 1500:
            return html
    except ValueError:
        raise
    except _ClientBlockedError as e:
        # Server explicitly rejected (403/404/etc.) — return error page HTML.
        # Playwright can't help here; the site is blocking at the HTTP layer.
        return e.html
    except Exception:
        pass

    return await _fetch_playwright(url)
