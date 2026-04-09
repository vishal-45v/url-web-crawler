# Architecture — URL Metadata Crawler

## Overview

The crawler is a single-responsibility REST service: given a URL, return structured metadata and semantic topics. It is designed as the POC nucleus that seeds the Part 2 scale architecture — every design choice here maps directly to how it behaves as an ECS Fargate worker at billions-of-URL scale.

---

## Component Architecture

```
                          ┌─────────────────────────────────────────┐
                          │         FastAPI Application              │
                          │                                          │
  POST /crawl             │   ┌───────────┐   ┌──────────────────┐  │
 ──────────────────────►  │   │  Fetcher  │   │    Extractor     │  │
                          │   │  Service  │──►│    Service       │  │
  GET /health             │   └───────────┘   └──────────────────┘  │
 ──────────────────────►  │         │                  │             │
                          │   httpx │ (fast path)      │             │
                          │         ▼                  ▼             │
                          │   Playwright          ┌──────────────┐   │
                          │   (JS fallback)       │  Classifier  │   │
                          │                       │   Service    │   │
                          │                       │  (Strategy)  │   │
                          │                       └──────────────┘   │
                          │                        KeyBERT│ Ollama   │
                          └─────────────────────────────────────────┘
```

---

## Request Lifecycle

```
Client
  │
  │ POST /crawl { "url": "https://..." }
  ▼
FastAPI Router
  │
  ├─► Pydantic validation (URL format, required fields)
  │       └─ 422 if invalid
  │
  ├─► FetcherService.fetch(url)
  │       │
  │       ├─ httpx GET (timeout=15s, follow_redirects=True)
  │       │       ├─ 429 → exponential backoff (2^n seconds, max 3 retries)
  │       │       ├─ non-HTML content-type → ValueError → 415
  │       │       └─ HTML < 1500 chars → trigger Playwright fallback
  │       │
  │       └─ Playwright chromium (headless)
  │               ├─ wait_until="networkidle" (all XHR settled)
  │               └─ returns full DOM HTML
  │
  ├─► ExtractorService.extract(html, url)
  │       ├─ Strip <script>, <style>, <noscript>
  │       ├─ Extract: title, meta description, og:*, canonical, keywords
  │       ├─ Clean body text (collapse whitespace)
  │       └─ Fallback: description = first 200 chars of body if meta missing
  │
  ├─► ClassifierService.classify(body_text, title)
  │       │
  │       ├─ KeyBERT: semantic keyphrase extraction (n-gram 1-3, top_n=10)
  │       └─ Ollama: LLM prompt → JSON array of {topic, score}
  │
  └─► Build CrawlResponse (Pydantic) → JSON
          └─ 200 OK
```

---

## Fetching Strategy — Two-Tier Design

### Why two strategies?

| Page Type | httpx Result | Playwright Result |
|---|---|---|
| Static HTML (REI blog) | Full DOM, ~15KB | Not needed |
| JS-rendered SPA (Amazon) | Skeleton ~800 chars | Full DOM ~45KB |
| News article (CNN) | Full DOM, ~20KB | Not needed |

The 1,500 character threshold is a heuristic. In practice:
- A real page with content is never shorter than 1,500 chars after boilerplate
- A JS skeleton page (`<div id="root"></div>` with script tags) is typically 500–1,200 chars

**Playwright is used only as a fallback** — it adds ~2s latency and 250MB memory overhead per invocation. At scale, we'd prescreen URL domains: Amazon always uses Playwright, static CDN URLs never do.

### Politeness

Every request includes a descriptive `User-Agent` identifying the crawler. Exponential backoff handles `429 Too Many Requests`. At scale, robots.txt is fetched once per domain and cached in Redis for 24 hours.

---

## Extraction Strategy

### What is extracted

| Field | Source | Fallback |
|---|---|---|
| `title` | `<title>` tag | None |
| `description` | `<meta name="description">` | First 200 chars of body |
| `og_title` | `<meta property="og:title">` | None |
| `og_description` | `<meta property="og:description">` | None |
| `og_image` | `<meta property="og:image">` | None |
| `canonical_url` | `<link rel="canonical">` | Input URL |
| `meta_keywords` | `<meta name="keywords">` | Empty list |
| `body_text` | All visible text (scripts removed) | None |
| `word_count` | `len(body_text.split())` | 0 |

### Why lxml over html.parser?

BeautifulSoup supports multiple parsers. `lxml` is 2–5x faster and more lenient with malformed HTML than Python's built-in `html.parser`. At millions of pages, this compounds. Trade-off: `lxml` is a C extension — adds ~5MB to the Docker image.

---

## Classification Strategy — Strategy Pattern

```python
BaseClassifier (abstract)
      │
      ├── KeyBERTClassifier
      │       model: all-MiniLM-L6-v2
      │       input: title + body[:2500]
      │       output: [(keyphrase, score), ...]
      │
      └── OllamaClassifier
              model: llama3 (or any Ollama model)
              input: structured prompt with title + body[:3000]
              output: parsed JSON [{topic, score}, ...]
```

### Classifier Factory (Singleton)

Each classifier type is instantiated once per process lifetime. This is critical for KeyBERT — the sentence-transformer model (~200MB in RAM) is loaded once on first request and reused for all subsequent requests. Loading it per-request would add 5–8 seconds of latency.

### Why KeyBERT over pure TF-IDF

| Aspect | TF-IDF | KeyBERT |
|---|---|---|
| "kitchen appliance" (appears 1x) | Low score (low frequency) | High score (semantically central) |
| "the" (appears 500x) | Would need stop-word list | Naturally ignored (low embedding relevance) |
| Multi-word phrases | Requires n-gram implementation | Native n-gram support (1-3 words) |
| Domain adaptation | Requires custom corpus | Semantic similarity is universal |

### Why KeyBERT over Ollama at scale

Ollama provides higher quality classification but requires a separate server infrastructure. For a REST API serving real-time requests, adding an Ollama server dependency introduces:
- Network latency (50–200ms for local, 300–500ms for remote)
- Single point of failure if Ollama server is down
- Separate scaling requirement (Ollama needs GPU for speed)

The strategy pattern means we can use KeyBERT for real-time API requests and batch-process with Ollama overnight for higher-quality enrichment of stored metadata.

---

## API Layer — FastAPI Design

### Why FastAPI over Flask/Django

| Feature | Flask | Django | FastAPI |
|---|---|---|---|
| Async support | Limited (Quart) | Limited (channels) | Native (async/await) |
| OpenAPI docs | Manual | drf-spectacular | Auto-generated |
| Pydantic validation | Manual | DRF serializers | Built-in |
| Performance | Good | Lower | Best (Starlette) |
| Type safety | Optional | Optional | First-class |

FastAPI's auto-generated OpenAPI spec at `/docs` provides an interactive demo interface — valuable for assignment submission.

### Async throughout

`httpx.AsyncClient` and `async with async_playwright()` are both async. This means one FastAPI worker can handle multiple in-flight requests concurrently. While waiting for the network (the slowest part of crawling), the event loop processes other requests. At scale on ECS, each task runs one uvicorn process with async handling — no threading complexity.

---

## Data Models

### Request Model

```python
class CrawlRequest(BaseModel):
    url: HttpUrl          # Pydantic validates URL format; 422 if invalid
```

### Response Model

```python
class CrawlResponse(BaseModel):
    url: str
    canonical_url: Optional[str]     # May differ from input (redirect, canonical tag)
    title: Optional[str]
    description: Optional[str]
    og_title: Optional[str]
    og_description: Optional[str]
    og_image: Optional[str]
    meta_keywords: List[str]
    topics: List[Topic]              # Ranked by score, descending
    word_count: int
    crawled_at: datetime             # UTC, ISO 8601
    classifier_used: str             # "keybert" or "ollama:llama3"
```

All `Optional` fields return `null` in JSON when not found. This is intentional — the caller distinguishes between "no title tag found" (`null`) and "title was an empty string" (`""`). This distinction matters for quality metrics in Part 2.

---

## Security Considerations

| Risk | Mitigation |
|---|---|
| SSRF (crawling internal IPs) | At scale, add IP blocklist (10.x.x.x, 172.16.x.x, 192.168.x.x) before fetch |
| ReDoS in URL parsing | Pydantic's HttpUrl uses a battle-tested parser |
| Malicious HTML content | BeautifulSoup parses — no execution. Scripts are stripped before text extraction |
| API abuse | At scale, add AWS WAF + rate limiting at ALB level |
| Secrets in env vars | Use AWS Secrets Manager for production; `.env` only for local dev |

---

## Performance Characteristics

| Operation | Typical Latency | Notes |
|---|---|---|
| httpx fetch (static page) | 200–800ms | Network-bound |
| Playwright fetch (SPA) | 2,000–4,000ms | JS render time |
| BeautifulSoup extraction | 10–50ms | CPU-bound, scales with HTML size |
| KeyBERT classify | 150–400ms | First call: +5s model load (lazy) |
| Ollama classify | 800–3,000ms | Depends on model size and hardware |
| **End-to-end P50 (static)** | **~1,000ms** | |
| **End-to-end P50 (SPA)** | **~3,000ms** | |
| **End-to-end P99** | **< 5,000ms** | |

### Memory Profile

| Component | RAM |
|---|---|
| Python process baseline | ~80MB |
| FastAPI + uvicorn | ~30MB |
| Playwright Chromium (when active) | ~250MB |
| KeyBERT model (all-MiniLM-L6-v2) | ~200MB |
| **Total (KeyBERT + Playwright active)** | **~560MB** |

Render free tier: 512MB RAM. The model is loaded lazily — if Playwright and KeyBERT are active simultaneously, this can spike to ~580MB. Recommendation: use Render Starter plan (512MB → 1GB) for production demo.

---

## Extension Points

The architecture is designed for extension without modification (Open/Closed principle):

| Extension | How |
|---|---|
| Add a new classifier (e.g., OpenAI) | Implement `BaseClassifier`; register in `factory.py` |
| Add a new extractor field (e.g., JSON-LD schema) | Add to `extractor.py` `extract()` function and Pydantic model |
| Add authentication to the API | FastAPI `Depends()` injection into endpoint |
| Add request caching (don't re-crawl same URL) | Redis cache check before `fetch()` in `main.py` |
| Switch from uvicorn to gunicorn | Change CMD in Dockerfile; zero code changes |

---

## How Part 1 Becomes Part 2

At scale, the `app/main.py` FastAPI endpoint becomes unnecessary — instead, each ECS Fargate task runs a **worker script** that:

1. Polls SQS for a batch of 100 URLs
2. Calls `fetcher.fetch()` for each URL
3. Calls `extractor.extract()` for each result
4. Calls `classifier.classify()` for each result
5. Writes to DynamoDB + S3 + Aurora
6. Deletes the SQS message (ack)

The services (`fetcher`, `extractor`, `classifier`) are **reused unchanged**. Only the entry point changes from HTTP request to SQS message. This is exactly why the services are kept as pure, dependency-free Python modules — they compose cleanly into any execution context.
