from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timezone

from app.models.schemas import CrawlRequest, CrawlResponse
from app.services.fetcher import fetch
from app.services.extractor import extract
from app.services.classifier.factory import get_classifier

app = FastAPI(
    title="URL Metadata Crawler API",
    description=(
        "Fetch any URL, extract structured metadata, and classify topics "
        "using pluggable NLP classifiers (KeyBERT or Ollama)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/crawl", response_model=CrawlResponse)
async def crawl(request: CrawlRequest):
    url = str(request.url)

    try:
        html = await fetch(url)
    except ValueError as e:
        raise HTTPException(status_code=415, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")

    try:
        metadata = extract(html, url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to extract metadata: {e}")

    try:
        classifier = get_classifier()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    topics = await classifier.classify(
        text=metadata["body_text"],
        title=metadata.get("title", ""),
    )

    return CrawlResponse(
        url=url,
        canonical_url=metadata["canonical_url"],
        title=metadata["title"],
        description=metadata["description"],
        og_title=metadata["og_title"],
        og_description=metadata["og_description"],
        og_image=metadata["og_image"],
        meta_keywords=metadata["meta_keywords"],
        topics=topics,
        word_count=metadata["word_count"],
        crawled_at=datetime.now(timezone.utc),
        classifier_used=classifier.name,
    )
