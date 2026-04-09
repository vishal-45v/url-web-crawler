# Part 2 — Scale Design: Operationalizing Billions of URLs

## Diagrams

| Diagram | Excalidraw Link |
|---|---|
| AWS Pipeline Architecture | https://excalidraw.com/#json=-eNdpOOW8Msvv9dXkRQ-x,z-UXPaeMHQ8cToT9FMH83Q |
| Unified Data Schema | https://excalidraw.com/#json=dhZuNb7QZYoWi6qq_q2sT,XEKbLW1Nq5JW6IDd7NjXtw |

---

## 1. System Architecture Overview

### Pipeline Flow (left to right)

```
INPUT → QUEUE → PROCESSING → STORAGE → ANALYTICS
  |        |         |            |          |
  S3      SQS      ECS         S3 +       Glue +
  MySQL   DLQ     Fargate    DynamoDB    Athena
  EB              + Redis    + Aurora
```

### Input Layer
| Source | Use Case |
|---|---|
| S3 (text file) | Bulk upload of billions of URLs for a crawl run |
| RDS MySQL | Structured URL lists partitioned by `year_month`, `domain` |
| EventBridge | Monthly cron trigger (`0 0 1 * *`) to kick off the crawl |

**Why both S3 and MySQL?** S3 handles raw bulk input (one-time uploads). MySQL handles structured, queryable input (e.g., "give me all amazon.com URLs for July"). EventBridge decouples scheduling from the input source — the same trigger fires regardless of where URLs live.

### Queue Layer
- **SQS Main Queue**: Receives URL batches (1000 URLs/message). Visibility timeout = 5 min (matches max crawler runtime per batch).
- **SQS Dead Letter Queue (DLQ)**: URLs that fail after 3 receive attempts. Triggers SNS alert. Can be manually re-queued after investigation.
- **Retry strategy**: Exponential backoff within the worker; DLQ for systematic failures.

**Why SQS over Kafka?**
- SQS is serverless, zero-ops, pay-per-message
- At billions of URLs, Kafka requires cluster management overhead
- SQS FIFO not needed here — URL order doesn't matter for metadata extraction
- Kafka would be chosen if we needed replay, consumer groups, or event streaming to downstream systems in real-time

### Processing Layer — ECS Fargate
- **Auto-scaling**: 10 (idle) → 500 tasks (peak), driven by SQS queue depth metric
- **Each task**: Pulls 1000 URLs from SQS, crawls sequentially with 2s delay between requests per domain
- **ElastiCache Redis**: Domain-level token bucket for rate limiting + robots.txt cache (24h TTL per domain)
- **Politeness**: Each worker checks Redis before crawling. If token unavailable, requeues URL with delay.

**Why ECS Fargate over Lambda?**
- Lambda has 250MB package limit — breaks with Playwright + sentence-transformers
- Lambda max timeout 15 min — insufficient for large batches
- Fargate gives full container control, no cold-start penalty at scale

**Why Fargate over EC2?**
- No capacity planning — Fargate provisions on demand
- Per-task billing — cost scales linearly with crawl volume
- Trade-off: EC2 is cheaper per hour at 100% utilization. Use EC2 Spot if budget is critical.

### Storage Layer — Three-Tier Design

| Store | What | Access Pattern | Cost |
|---|---|---|---|
| S3 | Raw HTML (gzipped) | Sequential read, archival | ~$0.023/GB/month |
| DynamoDB | Metadata + topics | Real-time point lookup | Pay-per-request |
| Aurora PostgreSQL | Structured analytics | Complex SQL queries | Instance-based |

**Why three stores instead of one?**
Each store is optimized for a different access pattern. Using Aurora for everything would be expensive for point lookups at scale. Using DynamoDB for everything loses SQL analytics. S3 for raw HTML keeps compute costs separate from query costs.

### Analytics Layer
- **AWS Glue**: Crawls S3 metadata folder → builds Data Catalog (schema inference, partitioned by `year_month`/`domain`)
- **Athena**: SQL on S3 via Glue catalog. Pay per TB scanned. Partition pruning on `year_month` reduces scan cost by 95%+.
- **Grafana**: Connected to CloudWatch + Aurora for operational dashboards

---

## 2. Unified Data Schema

### DynamoDB — `page_metadata` Table

```
Partition Key:  domain#url_hash       (e.g. "amazon.com#a3f2b1...")
Sort Key:       crawled_at            (ISO 8601, e.g. "2025-07-01T10:00:00Z")

Attributes:
  url             String
  title           String
  description     String
  og_title        String
  og_description  String
  og_image        String
  meta_keywords   StringSet
  topics          List<Map {topic: String, score: Number}>
  word_count      Number
  classifier      String
  year_month      String              (e.g. "2025-07")
  s3_html_path    String
  s3_meta_path    String
  TTL             Number              (Unix timestamp, crawled_at + 365 days)

GSI: year_month-crawled_at-index
  Allows: "give me all URLs crawled in July 2025"
```

**Design decisions:**
- `domain#url_hash` as PK keeps same-domain items together, enabling efficient domain-level scans
- TTL auto-expires stale records, reducing storage costs without manual cleanup
- Topics stored as a list of maps (not a separate table) — DynamoDB reads are always the full item anyway

### S3 — Path Convention

```
s3://url-metadata-crawl/
├── raw-html/
│   └── {year_month}/
│       └── {domain}/
│           └── {url_hash}.html.gz
└── metadata/
    └── {year_month}/
        └── {domain}/
            └── {url_hash}.json

Example:
  raw-html/2025-07/amazon.com/a3f2b1c9.html.gz
  metadata/2025-07/amazon.com/a3f2b1c9.json
```

**Design decisions:**
- Partitioned by `year_month` first — Athena partition pruning dramatically reduces scan cost
- `url_hash` (SHA-256 of normalized URL) as filename — collision-resistant, URL-safe, fixed length
- Gzip compression: ~70% size reduction on HTML, ~50% on JSON
- S3 Lifecycle rule: Standard → Intelligent-Tiering (30 days) → Glacier (12 months)

### Aurora PostgreSQL — Analytics Schema

```sql
CREATE TABLE crawl_results (
  id            BIGSERIAL PRIMARY KEY,
  url           TEXT NOT NULL,
  url_hash      CHAR(64) NOT NULL,
  domain        TEXT NOT NULL,
  title         TEXT,
  description   TEXT,
  word_count    INT,
  classifier    VARCHAR(50),
  crawled_at    TIMESTAMPTZ NOT NULL,
  year_month    CHAR(7) NOT NULL,
  s3_html_path  TEXT,
  s3_meta_path  TEXT,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE topics (
  id        BIGSERIAL PRIMARY KEY,
  crawl_id  BIGINT NOT NULL REFERENCES crawl_results(id) ON DELETE CASCADE,
  topic     TEXT NOT NULL,
  score     FLOAT NOT NULL
);

-- Indexes for common query patterns
CREATE INDEX idx_crawl_domain_ym ON crawl_results(domain, year_month);
CREATE INDEX idx_crawl_year_month ON crawl_results(year_month);
CREATE UNIQUE INDEX idx_crawl_hash_ym ON crawl_results(url_hash, year_month);
CREATE INDEX idx_topics_crawl_id ON topics(crawl_id);
```

**Design decisions:**
- Aurora Serverless v2 — scales from 0.5 to 128 ACUs, no idle cost during off-peak
- `url_hash + year_month` unique index prevents duplicate crawls for same month
- Topics in a separate table — enables `GROUP BY topic` analytics across billions of crawls
- Partition table by `year_month` (PostgreSQL declarative partitioning) when row count exceeds 100M

---

## 3. SLOs and SLAs

### SLOs (Internal Engineering Targets)

| Metric | Target | Measurement Window |
|---|---|---|
| Crawl throughput | ≥ 10,000 URLs/minute (peak) | Per crawl run |
| API P99 latency (`/crawl`) | ≤ 3 seconds | Rolling 1-hour |
| API P50 latency (`/crawl`) | ≤ 800ms | Rolling 1-hour |
| Crawl success rate | ≥ 95% of valid URLs | Per run |
| DLQ rate | ≤ 2% of total URLs | Per run |
| Data freshness | Metadata available within 1h of crawl | Per run |
| S3 write durability | 99.999999999% (S3 guarantee) | Lifetime |
| Monthly crawl completion | 100% of input URLs processed within 72h | Per month |

### SLAs (External / Stakeholder Commitments)

| Commitment | Value |
|---|---|
| API uptime | 99.9% (43.8 min/month downtime budget) |
| Monthly crawl delivery | Results available by day 5 of following month |
| Data retention | Raw HTML: 12 months, Metadata: 24 months |
| Incident response | P0 (data loss): 30 min, P1 (crawl failure): 4 hours |

---

## 4. Key Monitoring Metrics and Tools

### Tier 1 — Crawl Health (CloudWatch)

| Metric | Alarm Threshold | Action |
|---|---|---|
| `SQS ApproximateNumberOfMessagesVisible` | > 500K (falling behind) | Scale up ECS tasks |
| `SQS NumberOfMessagesSent to DLQ` | > 2% of throughput | PagerDuty alert → investigate |
| `ECS CPUUtilization` | > 85% for 5min | Scale out |
| `ECS MemoryUtilization` | > 80% | Alert + investigate OOM risk |
| Crawl success rate | < 95% | P1 alert |

### Tier 2 — API Performance (CloudWatch + X-Ray)

| Metric | Alarm |
|---|---|
| API P99 latency | > 3s → alert |
| 5xx error rate | > 1% → alert |
| Playwright fallback rate | > 40% (indicates JS-render spike) → alert |
| KeyBERT inference time | > 1s P95 → investigate model load |

### Tier 3 — Storage and Cost (CloudWatch + AWS Cost Explorer)

| Metric | Action |
|---|---|
| S3 storage growth rate | Alert if > 2x expected monthly growth |
| DynamoDB consumed RCU/WCU | Auto-scaling configured; alert at 80% provisioned |
| Aurora CPU | > 70% sustained → scale up instance |
| Athena bytes scanned per query | Alert if > 1TB (partition pruning failure) |

### Observability Stack

| Tool | Purpose |
|---|---|
| **CloudWatch** | Metrics, alarms, log aggregation, dashboards |
| **X-Ray** | Distributed tracing across fetch → extract → classify → store |
| **SNS + PagerDuty** | Alert routing (P0/P1 to on-call, P2 to Slack) |
| **Grafana** | Executive and operational dashboards (connected to CloudWatch + Aurora) |
| **AWS Cost Explorer** | Daily cost anomaly detection; budget alerts at 80% of monthly cap |

---

## 5. Cost Optimization Levers

| Lever | Savings |
|---|---|
| ECS Spot tasks for crawl workers (with on-demand fallback) | ~60% compute cost reduction |
| S3 Intelligent-Tiering for raw HTML | ~40% storage cost after 30 days |
| S3 Glacier after 12 months | ~85% storage cost vs Standard |
| Athena partition pruning on `year_month` | ~95% reduction in bytes scanned |
| DynamoDB on-demand billing (no reserved capacity) | Pay only during active crawl windows |
| Ollama on EC2 (self-hosted) vs OpenAI API | $0 vs ~$0.002/URL for topic classification |
| Bloom filter dedup before queuing | Avoid re-crawling ~30% of URLs (duplicate/redirect variants) |

---

## 6. Reliability and Fault Tolerance

| Failure Scenario | Mitigation |
|---|---|
| ECS task crashes mid-batch | SQS visibility timeout expires → message requeued automatically |
| Target site returns 429 | Exponential backoff in worker; if 3 retries fail → DLQ |
| Playwright timeout | 15s timeout → fallback to httpx-only metadata extraction |
| DynamoDB throttle | On-demand mode auto-scales; no throttle under normal conditions |
| Aurora failover | Aurora Multi-AZ; automatic failover in < 30s |
| S3 write failure | Worker retries with AWS SDK built-in retry (3 attempts) |
| Redis unavailable | Rate limiter degrades gracefully (bypasses check, logs warning) |
| Entire region outage | S3 cross-region replication for raw HTML; DynamoDB Global Tables (optional) |
