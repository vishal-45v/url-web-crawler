# URL Metadata Crawler

A production-grade web crawling and topic classification service. Given any URL, it fetches the page, extracts structured metadata, and classifies it into relevant semantic topics using pluggable NLP classifiers.

Designed to scale from a single-URL REST API to billions of URLs per month on AWS.

---

## Table of Contents

- [Quick Start](#quick-start)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Classifier Options](#classifier-options)
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Design Decisions](#design-decisions)
- [AI Tools Used](#ai-tools-used)

---

## Quick Start

### Prerequisites
- Python 3.12 or 3.13 (recommended)
- Docker (for Playwright dependencies)

> **Python 3.14 note:** `greenlet` and `lxml` do not yet ship pre-built wheels for
> Python 3.14. If `python3 --version` returns 3.14, create the virtual environment
> with an explicit 3.12/3.13 interpreter: `python3.12 -m venv .venv`

### Local Setup

```bash
# 1. Clone and enter the project
cd url-web-crawler

# 2. Create virtual environment with Python 3.12 or 3.13
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Copy environment config
cp .env.example .env

# 4. Install dependencies
pip install -r requirements.txt

# 5. Install Playwright's Chromium browser
playwright install chromium

# 6. Start the server
uvicorn app.main:app --reload --port 8000
```

The API is now live at `http://localhost:8000`

Interactive API docs: `http://localhost:8000/docs`

### Docker (Recommended)

```bash
docker build -t url-web-crawler .
docker run -p 8000:10000 --env-file .env url-web-crawler
```

---

## API Reference

### `GET /health`
Returns service status.

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2025-07-01T10:00:00Z"
}
```

---

### `POST /crawl`
Crawls a URL and returns structured metadata with classified topics.

**Request:**
```json
{
  "url": "https://www.amazon.com/Cuisinart-CPT-122..."
}
```

**Response:**
```json
{
  "url": "https://www.amazon.com/Cuisinart-CPT-122...",
  "canonical_url": "https://www.amazon.com/dp/B009GQ034C",
  "title": "Amazon.com: Cuisinart CPT-122 Compact 2-Slice Toaster",
  "description": "Toasts evenly with 6 shade settings...",
  "og_title": "Cuisinart CPT-122",
  "og_description": "Compact 2-slice toaster with cool-touch housing",
  "og_image": "https://images-na.ssl-images-amazon.com/...",
  "meta_keywords": ["toaster", "cuisinart", "kitchen"],
  "topics": [
    { "topic": "compact toaster", "score": 0.9134 },
    { "topic": "kitchen appliance", "score": 0.8720 },
    { "topic": "2-slice toaster", "score": 0.8401 },
    { "topic": "cuisinart", "score": 0.7933 },
    { "topic": "breakfast appliance", "score": 0.7612 }
  ],
  "word_count": 7843,
  "crawled_at": "2025-07-01T10:00:01.234Z",
  "classifier_used": "keybert"
}
```

**Error Responses:**

| Code | Reason |
|---|---|
| `415` | URL returns non-HTML content (PDF, image, etc.) |
| `422` | Invalid URL format |
| `502` | Target URL unreachable after retries |

---

### Test URLs (from assignment)

```bash
# Amazon product page (JS-rendered — uses Playwright fallback)
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "http://www.amazon.com/Cuisinart-CPT-122-Compact-2-Slice-Toaster/dp/B009GQ034C/"}'

# REI blog post (static HTML)
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.rei.com/blog/camp/how-to-introduce-your-indoorsy-friend-to-the-outdoors/"}'

# CNN tech article
curl -X POST http://localhost:8000/crawl \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.cnn.com/2025/09/23/tech/google-study-90-percent-tech-jobs-ai"}'
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and adjust.

| Variable | Default | Description |
|---|---|---|
| `CLASSIFIER` | `keybert` | Classifier to use: `keybert` or `ollama` |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server URL (only when `CLASSIFIER=ollama`) |
| `OLLAMA_MODEL` | `llama3` | Ollama model name |
| `REQUEST_TIMEOUT` | `15` | HTTP request timeout in seconds |
| `MAX_REDIRECTS` | `5` | Maximum redirects to follow |
| `MAX_RETRIES` | `3` | Retry attempts before failing |

---

## Classifier Options

The classifier is controlled entirely by the `CLASSIFIER` environment variable. No code changes needed to switch.

### KeyBERT (Default)
- **Model:** `all-MiniLM-L6-v2` (sentence-transformers)
- **Cost:** Free — runs in-process
- **Latency:** ~200–400ms per URL
- **Quality:** Medium-High — semantic keyword extraction
- **Best for:** Production at scale, cost-sensitive deployments

```bash
CLASSIFIER=keybert
```

### Ollama (Self-hosted LLM)
- **Model:** Any Ollama-compatible model (Llama3, Mistral, Gemma)
- **Cost:** Free — self-hosted
- **Latency:** 1–3s per URL (CPU) / 300ms (GPU)
- **Quality:** High — LLM understands context deeply
- **Best for:** Quality-critical classification with available GPU infrastructure
- **Requires:** [Ollama](https://ollama.ai) running on a reachable server

```bash
CLASSIFIER=ollama
OLLAMA_BASE_URL=http://your-ollama-server:11434
OLLAMA_MODEL=llama3
```

**Switching at runtime** (no redeploy):
Update the `CLASSIFIER` env var in Render/ECS and restart the service.

---

## Deployment

### Render (Recommended for Demo)

1. Fork or push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Render detects `render.yaml` automatically
5. Set environment variables in the Render dashboard
6. Deploy

The `Dockerfile` uses the official Microsoft Playwright image with all Chromium dependencies pre-installed. The sentence-transformer model is downloaded at build time to eliminate cold-start delays.

### AWS Elastic Beanstalk (Production)

```bash
# Install EB CLI
pip install awsebcli

# Initialize and deploy
eb init url-web-crawler --platform docker
eb create url-crawler-production
eb deploy
```

Set environment variables via `eb setenv CLASSIFIER=keybert ...`

---

## Project Structure

```
url-web-crawler/
├── app/
│   ├── main.py                          # FastAPI app — endpoints and middleware
│   ├── config/
│   │   ├── __init__.py                  # Re-exports settings
│   │   └── settings.py                  # Pydantic settings from env vars
│   ├── models/
│   │   └── schemas.py                   # Request/response Pydantic models
│   └── services/
│       ├── fetcher.py                   # Two-tier fetch: httpx → Playwright fallback
│       ├── extractor.py                 # BeautifulSoup metadata extraction
│       └── classifier/
│           ├── base.py                  # Strategy pattern interface
│           ├── keybert_classifier.py    # KeyBERT semantic keyword extraction
│           ├── ollama_classifier.py     # Ollama self-hosted LLM classification
│           └── factory.py              # Classifier singleton factory
├── docs/
│   ├── README.md                        # This file
│   └── architecture.md                  # System architecture deep-dive
├── Dockerfile                           # Playwright + Python production image
├── render.yaml                          # Render deployment config
├── requirements.txt                     # Python dependencies
└── .env.example                         # Environment variable template
```

---

## Design Decisions

### Why two fetch strategies?

JavaScript-rendered pages (like Amazon product pages) return skeleton HTML when fetched with a plain HTTP client. Playwright runs a real Chromium browser, waits for all XHR calls to complete (`networkidle`), and returns the fully rendered DOM. The threshold (1,500 chars) is a heuristic: pages shorter than this are almost certainly JS-rendered shells.

### Why the Strategy Pattern for classifiers?

The `BaseClassifier` interface decouples the classification algorithm from the rest of the system. Switching from KeyBERT to Ollama is one env var change — no code modification, no redeploy (just restart). This directly supports the scale design in Part 2, where the classifier choice changes based on cost and quality requirements.

### Why KeyBERT over TF-IDF?

TF-IDF scores words by frequency — it would rank "toaster" highly on the Amazon page, but miss "kitchen appliance" unless that exact phrase appears frequently. KeyBERT uses a sentence-transformer model (`all-MiniLM-L6-v2`) to find phrases semantically similar to the document as a whole. It understands that "breakfast appliance" is relevant even if it appears once.

### Why Pydantic for response models?

Pydantic validates at the boundary. If BeautifulSoup returns `None` for a missing tag, Pydantic's `Optional[str]` handles it cleanly — no `KeyError` or `TypeError` propagates to the client. FastAPI auto-generates OpenAPI docs from these models, giving a live, interactive API spec at `/docs`.

---

## AI Tools Used

Per assignment requirements, the following AI tools assisted in development:

| Tool | Usage |
|---|---|
| **Claude (Anthropic)** | Design pattern discussion, code review and documentation |
| **KeyBERT** | Semantic topic classification (open-source NLP library) |
| **Ollama** | Self-hosted LLM for topic classification (optional classifier) |

All design decisions, architectural trade-offs, and implementation were driven by the developer. AI tooling was used as a sounding board for trade-off analysis, code review, and unit test coverage suggestions.
