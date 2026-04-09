import re
from bs4 import BeautifulSoup


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract(html: str, url: str) -> dict:
    """
    Parse raw HTML and return structured metadata dict.
    Strips scripts/styles before extracting body text.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise elements before body text extraction
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    # --- Title ---
    title = _clean(soup.title.get_text()) if soup.title else None

    # --- Meta description ---
    meta_desc_tag = soup.find("meta", attrs={"name": "description"})
    description = (
        _clean(meta_desc_tag["content"])
        if meta_desc_tag and meta_desc_tag.get("content")
        else None
    )

    # --- Open Graph tags ---
    def og(prop: str) -> str | None:
        tag = soup.find("meta", property=f"og:{prop}")
        return _clean(tag["content"]) if tag and tag.get("content") else None

    # --- Canonical URL ---
    canonical_tag = soup.find("link", rel="canonical")
    canonical = (
        canonical_tag["href"]
        if canonical_tag and canonical_tag.get("href")
        else url
    )

    # --- Meta keywords ---
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    meta_keywords = (
        [k.strip() for k in kw_tag["content"].split(",") if k.strip()]
        if kw_tag and kw_tag.get("content")
        else []
    )

    # --- Body text ---
    body_text = _clean(soup.get_text(separator=" "))
    word_count = len(body_text.split())

    # Fallback: use first 200 chars of body if no meta description
    if not description and body_text:
        description = body_text[:200]

    return {
        "canonical_url": canonical,
        "title": title,
        "description": description,
        "og_title": og("title"),
        "og_description": og("description"),
        "og_image": og("image"),
        "meta_keywords": meta_keywords,
        "body_text": body_text,
        "word_count": word_count,
    }
