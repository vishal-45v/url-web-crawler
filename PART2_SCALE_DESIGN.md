# Part 2 — Scale Design: Operationalizing Billions of URLs

**Goal:** Process billions of URLs per month on AWS with no single point of failure, independent scaling per processing stage, and cost-optimised instance selection per workload type.


See companion diagrams:

- [Pipeline Architecture (Excalidraw)](https://excalidraw.com/#json=Qa81SJZLxyUJ17h07QEzr,rkwmT-oNzOrvtPi-JORQQQ) — end-to-end AWS pipeline with 4 dedicated fleets, inter-stage SQS queues, Bloom filter, and trade-off annotations

<img width="1538" height="815" alt="Pipeline Architecture" src="https://github.com/user-attachments/assets/8266567a-4f6a-4944-8c09-a6a71e150b20" />

- [`docs/Part2_Data_Schema.excalidraw`](docs/Part2_Data_Schema.excalidraw) — inter-stage SQS message schemas (①–④) + three-tier storage schemas

<img width="1236" height="907" alt="Data Schema" src="https://github.com/user-attachments/assets/5fe8f9c5-dea6-49c9-af8b-1fc7cbdacc58" />


## Core Design Principle: Decoupled Processing Fleets

The Part 1 monolith (`fastapi` app) handles Fetch → Extract → Classify → Store in a single synchronous request. That works for a REST API but fails at scale for three reasons:

| Problem | Monolithic Worker | Decoupled Fleets |
|---|---|---|
| Stage crash | All 4 steps fail together | Only the crashed fleet halts; others keep processing |
| Bottleneck scaling | Must scale whole worker for one slow stage | Scale only the bottleneck fleet |
| Instance cost | Same instance type for all work | Fetch = I/O instance, Classify = GPU instance |

The solution is 4 independent fleets, each consuming from its own SQS queue:

```
SQS Main Queue
      │
      ▼
[Fetch Fleet]  ──── SQS Fetch→Extract ────► [Extract Fleet]
                                                    │
                                          SQS Extract→Classify
                                                    │
                                                    ▼
                                          [Classify Fleet]
                                                    │
                                         SQS Classify→Store
                                                    │
                                                    ▼
                                           [Store Fleet]
                                         /      │       \
                                        S3   DynamoDB  Aurora
```

---

## Fleet Details

### Fleet 1 — Fetch Fleet (I/O-bound)

**What it does:** Poll SQS Main Queue, fetch the URL, write raw HTML to S3, emit to Fetch→Extract Queue.

**Code reuse:** `app/services/fetcher.py` — unchanged. The two-tier fetch strategy (httpx → Playwright fallback) runs inside each worker.

**Instance type:** `c5.large` (2 vCPU, 4GB RAM). Fetch work is network I/O — compute is minimal. Playwright adds ~250MB overhead per active browser context.

**Scaling trigger:** SQS Main Queue depth. Target: 1 task per 200 queued messages.

**Key design:** HTML is written to S3 *immediately* by the Fetch Fleet, before any downstream processing. The S3 path flows through all subsequent queues. This keeps every inter-stage SQS message well under the 256 KB limit regardless of page size.

**Output message (Fetch → Extract Queue):**
```json
{
  "url":           "string",
  "s3_html_path":  "s3://bucket/raw-html/2025-07/amazon.com/abc123.html.gz",
  "status_code":   200,
  "fetch_method":  "httpx | playwright",
  "fetched_at":    "ISO 8601"
}
```

---

### Fleet 2 — Extract Fleet (CPU-light)

**What it does:** Read HTML from S3 path in message, run BeautifulSoup extraction, emit structured metadata to Extract→Classify Queue.

**Code reuse:** `app/services/extractor.py` — unchanged.

**Instance type:** `t3.medium` (2 vCPU, 4GB RAM). Parsing HTML with lxml is fast and CPU-light (~10–50ms per page).

**Scaling trigger:** Fetch→Extract Queue depth.

**Output message (Extract → Classify Queue):**
```json
{
  "url":            "string",
  "canonical_url":  "string",
  "title":          "string",
  "description":    "string | null",
  "og_title":       "string | null",
  "og_description": "string | null",
  "og_image":       "string | null",
  "meta_keywords":  ["string"],
  "body_text":      "string  (≤ 2500 chars — classifier budget)",
  "word_count":     1842,
  "s3_html_path":   "string"
}
```

---

### Fleet 3 — Classify Fleet (CPU/GPU-bound)

**What it does:** Read extracted metadata from queue, run topic classification, emit full crawl result to Classify→Store Queue.

**Code reuse:** `app/services/classifier/` — unchanged. Classifier is selected by `CLASSIFIER` env var at fleet launch time.

**Instance type (KeyBERT):** `c5.2xlarge` (8 vCPU, 16GB RAM). all-MiniLM-L6-v2 is CPU-efficient; multiple workers per instance.

**Instance type (Ollama):** `g4dn.xlarge` (4 vCPU, 16GB RAM, 1× T4 GPU). Ollama drops from ~2s (CPU) to ~300ms (GPU). Isolated fleet means GPU instances are not wasted on Fetch/Extract work.

**Scaling trigger:** Extract→Classify Queue depth. This fleet is typically the bottleneck — it gets more instances allocated relative to others.

**Swapping classifiers:** `CLASSIFIER=keybert` vs `CLASSIFIER=ollama` is set as an ECS task environment variable. No code changes. Re-deploy the fleet with the new env var.

**Output message (Classify → Store Queue):**
```json
{
  "url":             "string",
  "canonical_url":   "string",
  "title":           "string",
  "description":     "string | null",
  "og_title":        "string | null",
  "og_description":  "string | null",
  "og_image":        "string | null",
  "meta_keywords":   ["string"],
  "topics":          [{"topic": "compact toaster", "score": 0.9134}],
  "word_count":      1842,
  "crawled_at":      "ISO 8601",
  "s3_html_path":    "string",
  "classifier_used": "keybert"
}
```

---

### Fleet 4 — Store Fleet (I/O-bound, parallel writes)

**What it does:** Read full crawl result from queue, write concurrently to 3 storage sinks (S3 metadata JSON, DynamoDB, Aurora), ack SQS message.

**Code reuse:** New `store.py` module (thin wrapper around 3 write calls). The data models (`CrawlResponse`) are reused unchanged.

**Instance type:** `t3.medium`. All 3 writes are async I/O — the fleet is network-bound, not compute-bound. Async writes to all 3 sinks run concurrently (not sequentially).

**Scaling trigger:** Classify→Store Queue depth.

**Why 3 sinks?** Each serves a different access pattern:

| Sink | Access Pattern | Why |
|---|---|---|
| DynamoDB | `GET /metadata?url=...` → O(1) lookup | Real-time API reads; 365-day TTL auto-expires old data |
| S3 | Archive + Athena queries | Raw HTML at ~$0.023/GB/month; Glue/Athena for analytics |
| Aurora | `GROUP BY domain`, joins, reports | SQL analytics; Serverless v2 scales to zero between batch runs |

---

## URL Deduplication — Bloom Filter

Before any URL is enqueued to SQS Main Queue, a Bloom filter check runs against ElastiCache Redis.

- **What it prevents:** re-crawling URLs already processed in the current monthly batch
- **Savings:** ~30% of candidate URLs are duplicates → 30% fewer SQS messages, S3 writes, DynamoDB writes
- **False positive rate:** 0.1% (acceptable — we occasionally skip a URL that wasn't actually crawled)
- **Storage:** 64MB Redis Bloom filter ≈ 500M URL fingerprints
- **Degradation:** if Redis is unavailable, the filter is bypassed and all URLs proceed (crawl duplicates rather than lose URLs)

---

## Failure Handling

### Per-fleet retry

Each SQS queue has a visibility timeout (5 min). If a worker crashes mid-processing, the message becomes visible again after 5 min. After 3 failures, the message moves to a fleet-specific DLQ.

```
SQS Queue (maxReceiveCount=3)
      │
      │  failure × 3
      ▼
Fleet DLQ  ──► SNS Alert ──► PagerDuty / Slack
```

### Partial failure isolation

If the Classify Fleet crashes entirely:
- Fetch and Extract fleets keep running, filling the Extract→Classify Queue
- Classify Queue acts as a buffer (SQS retention: 14 days)
- When Classify Fleet recovers, it drains the accumulated queue
- **No data loss.** No re-crawl needed.

This is the key advantage over a monolithic worker — a monolithic fleet crash loses all in-flight work across all stages.

### ElastiCache Redis failure

Redis is used for rate limiting and robots.txt caching. If Redis becomes unavailable:
- Rate limiting is skipped (we crawl slightly faster than politeness rules prefer)
- robots.txt is re-fetched per request instead of cached
- **Processing continues.** Redis is an optimisation, not a hard dependency.

---

## Auto-scaling

Each fleet has independent CloudWatch auto-scaling:

| Fleet | Scaling Metric | Target | Min | Max |
|---|---|---|---|---|
| Fetch | SQS Main Queue depth | 200 msgs/task | 5 | 500 |
| Extract | Fetch→Extract Queue depth | 500 msgs/task | 3 | 200 |
| Classify | Extract→Classify Queue depth | 100 msgs/task | 3 | 200 |
| Store | Classify→Store Queue depth | 500 msgs/task | 3 | 100 |

**Classify gets the tightest target** (100 msgs/task) because classification is the slowest step (~300ms–2s per URL). Fetch and Store are fast, so they need fewer tasks per message volume.

Scale-in cooldown: 5 minutes (prevents thrashing when queue briefly drains).

---

## Monitoring

| Signal | Tool | Alert Condition |
|---|---|---|
| Queue depth per fleet | CloudWatch Metrics | > 10,000 messages → warn; > 100,000 → page |
| DLQ depth | CloudWatch Alarms | > 0 messages → page (every DLQ message is a failure) |
| Fleet task count | CloudWatch | < min tasks running → page |
| Crawl success rate | Custom metric | < 95% success rate → warn |
| End-to-end latency | X-Ray traces | P99 > 30s → warn |
| Redis availability | ElastiCache Metrics | Connection errors → warn (non-critical) |

---

## How Part 1 Code Maps to Part 2

The Part 1 services are **pure Python modules with no framework coupling**. Each becomes the core of one fleet worker:

| Part 1 Module | Part 2 Fleet | Change Required |
|---|---|---|
| `app/services/fetcher.py` | Fetch Fleet worker | None — wrap in SQS poll loop |
| `app/services/extractor.py` | Extract Fleet worker | None — wrap in SQS poll loop |
| `app/services/classifier/` | Classify Fleet worker | None — wrap in SQS poll loop |
| `app/models/schemas.py` | All fleets | None — Pydantic models reused for message validation |
| `app/main.py` | Retired | Replaced by 4 worker entry points |
| *(new)* `worker/store.py` | Store Fleet worker | New — 3 concurrent async writes |

The entry point changes from `POST /crawl` HTTP request to SQS message poll. The business logic — fetch, extract, classify — is identical.

---

## Cost Estimate at 1 Billion URLs/Month

| Component | Assumption | Monthly Cost |
|---|---|---|
| ECS Fargate — Fetch | 100 tasks × 730h × c5.large | ~$7,000 |
| ECS Fargate — Extract | 30 tasks × 730h × t3.medium | ~$700 |
| ECS Fargate — Classify (KeyBERT) | 80 tasks × 730h × c5.2xlarge | ~$9,000 |
| ECS Fargate — Store | 30 tasks × 730h × t3.medium | ~$700 |
| SQS (5 queues × 1B messages) | $0.40 per 1M messages | ~$2,000 |
| S3 storage (HTML + metadata) | ~500TB at $0.023/GB + PUT costs | ~$12,000 |
| DynamoDB (1B writes + reads) | On-demand pricing | ~$3,000 |
| Aurora Serverless v2 | Batch analytics, scales to zero | ~$500 |
| ElastiCache Redis | cache.r6g.large | ~$200 |
| CloudWatch + X-Ray | Metrics, logs, traces | ~$500 |
| **Total (KeyBERT)** | | **~$35,600/month** |
| **Total (Ollama/GPU)** | Replace CPU classify with g4dn.xlarge | **~$42,000/month** |

> KeyBERT saves ~$6,400/month vs Ollama at this scale. The strategy pattern makes this a one-line env var change — switch between them based on quality requirements and budget.
