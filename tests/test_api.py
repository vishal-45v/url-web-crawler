"""
Integration tests for the FastAPI endpoints.

Uses FastAPI's TestClient (ASGI transport) with mocked service layer.
Tests cover all HTTP status codes, response structure, and field validation.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import Topic

client = TestClient(app)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

MOCK_METADATA = {
    "canonical_url": "https://example.com/page",
    "title": "Example Page Title",
    "description": "An example page used for testing",
    "og_title": "OG Title",
    "og_description": "OG Description",
    "og_image": "https://example.com/image.png",
    "meta_keywords": ["example", "testing"],
    "body_text": "This is the body text of the page for topic classification",
    "word_count": 11,
}

MOCK_TOPICS = [
    Topic(topic="example page", score=0.8901),
    Topic(topic="testing framework", score=0.7654),
]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

def test_health_returns_200():
    response = client.get("/health")
    assert response.status_code == 200


def test_health_response_contains_status_ok():
    response = client.get("/health")
    assert response.json()["status"] == "ok"


def test_health_response_contains_timestamp():
    response = client.get("/health")
    assert "timestamp" in response.json()


# ---------------------------------------------------------------------------
# POST /crawl — input validation
# ---------------------------------------------------------------------------

def test_crawl_invalid_url_returns_422():
    response = client.post("/crawl", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_crawl_missing_url_field_returns_422():
    response = client.post("/crawl", json={})
    assert response.status_code == 422


def test_crawl_empty_body_returns_422():
    response = client.post("/crawl", content=b"", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_crawl_wrong_method_returns_405():
    response = client.get("/crawl")
    assert response.status_code == 405


# ---------------------------------------------------------------------------
# POST /crawl — error propagation
# ---------------------------------------------------------------------------

def test_crawl_non_html_returns_415():
    with patch("app.main.fetch", new=AsyncMock(side_effect=ValueError("Unsupported content-type: application/pdf"))):
        response = client.post("/crawl", json={"url": "https://example.com/file.pdf"})
    assert response.status_code == 415
    assert "application/pdf" in response.json()["detail"]


def test_crawl_unreachable_url_returns_502():
    with patch("app.main.fetch", new=AsyncMock(side_effect=Exception("Connection refused"))):
        response = client.post("/crawl", json={"url": "https://unreachable.example.com"})
    assert response.status_code == 502


def test_crawl_502_detail_includes_reason():
    with patch("app.main.fetch", new=AsyncMock(side_effect=Exception("DNS resolution failed"))):
        response = client.post("/crawl", json={"url": "https://gone.example.com"})
    assert "DNS resolution failed" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /crawl — successful response structure
# ---------------------------------------------------------------------------

def _mock_crawl(metadata=None, topics=None):
    """Context manager that stubs fetch, extract, and get_classifier."""
    meta = metadata or MOCK_METADATA
    tpcs = topics or MOCK_TOPICS
    mock_classifier = MagicMock()
    mock_classifier.name = "keybert"
    mock_classifier.classify = AsyncMock(return_value=tpcs)

    return (
        patch("app.main.fetch", new=AsyncMock(return_value="<html>mock</html>")),
        patch("app.main.extract", return_value=meta),
        patch("app.main.get_classifier", return_value=mock_classifier),
    )


def test_crawl_success_returns_200():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.status_code == 200


def test_crawl_response_contains_url():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.json()["url"] == "https://example.com/"


def test_crawl_response_title():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.json()["title"] == "Example Page Title"


def test_crawl_response_topics_list():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    topics = response.json()["topics"]
    assert len(topics) == 2
    assert topics[0]["topic"] == "example page"
    assert topics[0]["score"] == 0.8901


def test_crawl_response_classifier_used():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.json()["classifier_used"] == "keybert"


def test_crawl_response_contains_crawled_at():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert "crawled_at" in response.json()


def test_crawl_response_word_count():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.json()["word_count"] == 11


def test_crawl_response_null_og_fields_when_missing():
    meta_without_og = {**MOCK_METADATA, "og_title": None, "og_description": None, "og_image": None}
    p1, p2, p3 = _mock_crawl(metadata=meta_without_og)
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    data = response.json()
    assert data["og_title"] is None
    assert data["og_description"] is None
    assert data["og_image"] is None


def test_crawl_response_meta_keywords():
    p1, p2, p3 = _mock_crawl()
    with p1, p2, p3:
        response = client.post("/crawl", json={"url": "https://example.com"})
    assert response.json()["meta_keywords"] == ["example", "testing"]


# ---------------------------------------------------------------------------
# GET /docs and /openapi.json
# ---------------------------------------------------------------------------

def test_openapi_docs_returns_200():
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_title():
    response = client.get("/openapi.json")
    assert response.json()["info"]["title"] == "URL Metadata Crawler API"


def test_openapi_schema_exposes_health_and_crawl_endpoints():
    response = client.get("/openapi.json")
    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/crawl" in paths
