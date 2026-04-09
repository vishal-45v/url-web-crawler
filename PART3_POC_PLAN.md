# Part 3 — Engineering POC Plan, Blockers, Estimates & Release

---

## 1. POC Objectives

The Proof of Concept answers three questions before full-scale investment:

| Question | Success Criterion |
|---|---|
| Can the crawler handle all page types? | All 3 test URLs return correct metadata |
| Can the pipeline sustain throughput? | ≥ 5,000 URLs/min in load test |
| Is the cost model viable? | Estimated monthly cost < $2,000 for 1B URLs/month |

---

## 2. Implementation Schedule (Solo Developer)

> Estimates assume 1 developer, 8 working hours/day, no major unknowns.
> Buffer of 20% added to each phase for integration surprises.

### Phase 0 — Environment & CI/CD Setup
**Duration: 3 days**

| Day | Task | Done When |
|---|---|---|
| 1 | AWS account, IAM roles, VPC, ECR repository, S3 buckets | `aws sts get-caller-identity` succeeds; ECR repo visible |
| 2 | Local dev: Docker, Python 3.11, Playwright chromium, `.env` wired | `docker build .` passes locally |
| 3 | GitHub Actions pipeline: test → build → push ECR → deploy ECS | Push to `main` triggers deploy |

**Risk:** AWS account limits (ECR, ECS, ElastiCache) may need quota increase requests. Submit Day 1.

---

### Phase 1 — Core Crawler (Part 1 Code)
**Duration: 5 days**

| Day | Task | Done When |
|---|---|---|
| 1 | `fetcher.py` — httpx + Playwright fallback | All 3 test URLs return HTML > 1500 chars |
| 2 | `extractor.py` — BeautifulSoup metadata extraction | Title, description, OG tags extracted from all 3 URLs |
| 3 | `keybert_classifier.py` + `ollama_classifier.py` | ≥ 5 relevant topics returned per URL |
| 4 | FastAPI endpoints `/crawl`, `/health`, OpenAPI docs | Postman collection passes; Swagger UI renders |
| 5 | Render deployment + smoke test on live URLs | Live endpoint returns valid JSON for all 3 test URLs |

**Milestone: Part 1 Demo Ready**

---

### Phase 2 — Queue + Worker Infrastructure
**Duration: 6 days**

| Day | Task | Done When |
|---|---|---|
| 1 | SQS Main Queue + DLQ created; IAM policy for ECS → SQS | SQS visible in console; test message sent/received |
| 2 | Worker script: poll SQS → crawl → ack; batch size = 100 URLs | 100-URL batch processed end-to-end locally |
| 3 | ECS Fargate task definition; Docker image deployed to ECR | ECS task runs, logs appear in CloudWatch |
| 4 | ElastiCache Redis: domain token bucket + robots.txt cache | Rate limiter correctly throttles same-domain requests |
| 5 | Auto-scaling policy: SQS depth → ECS task count | 10 → 100 tasks in < 3 min under simulated load |
| 6 | Integration test: 10,000 URLs pumped through pipeline | < 2% DLQ rate; all 9,800+ URLs in DynamoDB |

---

### Phase 3 — Storage Layer
**Duration: 4 days**

| Day | Task | Done When |
|---|---|---|
| 1 | DynamoDB table: PK/SK, GSI (year_month-index), TTL, on-demand | `aws dynamodb describe-table` shows correct schema |
| 2 | S3 buckets: path convention, lifecycle rules, gzip write tested | Sample HTML written and readable from correct path |
| 3 | Aurora PostgreSQL: schema migrations, connection pool (max=100) | `crawl_results` and `topics` tables created; INSERT works |
| 4 | AWS Glue crawler + Athena test: query metadata by domain + month | Athena query returns correct row count, < 5s runtime |

---

### Phase 4 — Observability
**Duration: 3 days**

| Day | Task | Done When |
|---|---|---|
| 1 | CloudWatch alarms: SQS depth, DLQ rate, ECS CPU/Memory | Alarms visible; test trigger fires SNS notification |
| 2 | X-Ray tracing: annotate fetch → extract → classify → store | Trace appears in X-Ray console with all segments |
| 3 | Grafana dashboard: crawl throughput, success rate, latency, cost | Dashboard readable by non-technical stakeholder |

---

### Phase 5 — Load Testing & Hardening
**Duration: 4 days**

| Day | Task | Done When |
|---|---|---|
| 1 | Load test: 1M URLs, measure throughput, DLQ rate, cost | ≥ 5,000 URLs/min sustained; DLQ < 2% |
| 2 | Identify and fix bottlenecks (likely: Aurora connections, Redis) | Re-run load test shows ≥ 15% throughput improvement |
| 3 | Chaos test: kill 50% of ECS tasks mid-run; verify recovery | All in-flight URLs reprocessed; no data loss |
| 4 | Cost analysis: actual vs projected; optimize top 3 levers | Cost projection for 1B URLs within $2,000 budget |

---

### Phase 6 — Release
**Duration: 3 days**

| Day | Task | Done When |
|---|---|---|
| 1 | Documentation: README, architecture.md, runbook, API spec | PR reviewed and merged; docs render correctly |
| 2 | Canary deployment: 10% traffic to new version; monitor 2h | Zero P0 alerts; error rate unchanged |
| 3 | Full production go-live: 100% traffic; team sign-off | Stakeholder demo passes; go/no-go form signed |

---

**Total: ~28 working days (≈ 5.5 weeks, solo developer)**

```
Week 1:  Phase 0 + Phase 1 (core crawler live on Render)
Week 2:  Phase 2 partial (queue + workers)
Week 3:  Phase 2 complete + Phase 3 (storage layer)
Week 4:  Phase 4 + Phase 5 (observability + load test)
Week 5:  Phase 5 complete + Phase 6 (hardening + release)
Week 5.5: Buffer for unknown blockers
```

---

## 3. Blockers — Known vs Unknown

### Known Blockers (Trivial — solved with standard approaches)

| # | Blocker | Severity | Solution | ETA to Resolve |
|---|---|---|---|---|
| K1 | Amazon blocks scrapers (bot detection) | Medium | Playwright headless + random User-Agent + 2s delay | Already handled in fetcher.py |
| K2 | robots.txt fetch adds latency per domain | Low | Cache in Redis with 24h TTL; fetch once per domain | Phase 2, Day 4 |
| K3 | SQS 256KB message size limit | Low | Batch 100 URLs/message (avg URL ~100 chars → 10KB/batch) | Phase 2, Day 2 |
| K4 | Playwright memory (~250MB per Chromium instance) | Medium | 1 Playwright instance per ECS task; task memory = 1GB | Phase 2, Day 3 |
| K5 | KeyBERT model cold-start on first request | Low | Pre-download model at Docker build time (`RUN python -c "..."`) | Already in Dockerfile |
| K6 | URL normalization — same page, different query params | Low | Normalize: strip UTM params, sort params, lowercase scheme | Phase 2, Day 2 (URL normalizer utility) |
| K7 | Render free tier spins down after inactivity | Low | Configure Render health check ping every 5 min (UptimeRobot) | Phase 1, Day 5 |

### Unknown Blockers (High-risk — require investigation)

| # | Blocker | Risk Level | Contingency | Discovery ETA |
|---|---|---|---|---|
| U1 | Aurora connection pool exhaustion at 500 concurrent ECS tasks | High | Add PgBouncer connection pooler in front of Aurora; or switch to Aurora Data API | Phase 5 load test |
| U2 | DynamoDB hot partition — high-volume domains (amazon.com) concentrate writes | Medium | Add random suffix to PK: `domain#url_hash#shard(0-9)`; sharded PK spreads writes | Phase 5 load test |
| U3 | Glue catalog partition discovery lag (new year_month partitions not immediately queryable in Athena) | Medium | Manual `MSCK REPAIR TABLE` after each run; or use Glue crawler schedule | Phase 3, Day 4 |
| U4 | Site-specific anti-bot JS challenges (Cloudflare, hCaptcha) | High | Proxy rotation service (Bright Data, Oxylabs); flagged URLs rerouted to manual queue | Phase 1-2 integration |
| U5 | ECS Fargate cold-start latency (60-90s to pull image + start) | Low-Medium | Keep minimum 10 tasks always running; pre-warm on schedule | Phase 2 auto-scaling test |
| U6 | Redis ElastiCache network latency adding >50ms per URL | Low | Ensure Redis in same VPC/AZ as ECS tasks; use Redis Cluster with read replicas | Phase 5 profiling |

---

## 4. Go / No-Go Criteria

The POC is considered successful when ALL of the following pass:

| Criterion | Target | How to Measure |
|---|---|---|
| All 3 test URLs return correct metadata | 100% | Manual validation of API response |
| Crawl throughput | ≥ 5,000 URLs/min sustained | CloudWatch `SQS NumberOfMessagesDeleted` over 10 min |
| Crawl success rate | ≥ 90% (DLQ rate ≤ 10%) | `DLQ_count / total_messages` |
| API P99 latency | ≤ 3 seconds | CloudWatch `TargetResponseTime P99` |
| Data consistency | 100% of crawled URLs found in DynamoDB | Count match: SQS sent vs DynamoDB items |
| Cost projection | < $2,000 / 1B URLs | AWS Cost Calculator based on load test metrics |
| Recovery from failure | No data loss after killing 50% of tasks | Re-run count + DynamoDB total after chaos test |
| Automated alerting | DLQ alert fires within 5 min of threshold breach | Inject 5% failure rate; confirm SNS notification |

---

## 5. Evaluation of the POC

### How to Evaluate Correctness
```
Test Set: 3 provided URLs + 10 additional URLs across categories:
  - E-commerce product page (Amazon)
  - Blog/editorial (REI)
  - News article (CNN)
  - SPA (React-rendered page)
  - Page with no meta description
  - Page returning 301 redirect
  - Page with robots.txt disallow
  - Non-English page (Chinese)
  - Minimal HTML page
  - PDF (should return 415)
```

For each test URL, validate:
1. `title` matches visible page title ✓/✗
2. `description` is meaningful (> 20 chars, not noise) ✓/✗
3. `topics` — at least 5 relevant topics with score > 0.5 ✓/✗
4. `word_count` > 0 ✓/✗
5. `crawled_at` is within 10 seconds of request time ✓/✗

### How to Evaluate Scale Performance
- Inject 100K URLs into SQS
- Measure wall-clock time to empty queue
- Calculate URLs/min; compare against 5,000 target
- Check DynamoDB item count matches input count
- Check DLQ count

### How to Evaluate Cost
- Run 100K URL crawl; check AWS Cost Explorer for that hour
- Extrapolate to 1B URLs: `actual_cost × (1B / 100K)`
- Compare against $2,000 budget

---

## 6. Release Plan

### Release Stages

```
Alpha (Week 3)     → Internal only, 10K URLs, 2 domains (amazon.com, blog.rei.com)
Beta  (Week 4)     → Extended team, 1M URLs, 5 domains, observability live
RC    (Week 5)     → Full load test, all domains, chaos test passed
GA    (Week 5.5)   → Production go-live, canary → 10% → 50% → 100%
```

### Deployment Strategy — Blue/Green via ECS

```
Current (Blue):  ECS Service v1 — 0 tasks (idle post-POC)
New     (Green): ECS Service v2 — 10 tasks minimum

Step 1: Deploy Green alongside Blue
Step 2: Route 10% of SQS messages to Green workers (via queue routing)
Step 3: Monitor for 2 hours — zero P0 alerts
Step 4: Route 100% to Green; deregister Blue
Step 5: Keep Blue task definition for 7-day rollback window
```

### Rollback Plan
- Trigger: DLQ rate > 10% OR P0 alert fires during canary
- Action: Reroute SQS back to Blue workers (< 2 min)
- Data: DynamoDB writes are idempotent (same PK/SK = overwrite, not duplicate)
- SLA impact: Zero — Blue workers continue processing during rollback

### Definition of a High-Quality Release

| Dimension | Standard |
|---|---|
| **Code** | 80%+ unit test coverage on extractor + classifier; integration tests for all 10 test URLs |
| **Documentation** | README, architecture.md, runbook, API spec (auto-generated via FastAPI) all up to date |
| **Observability** | All CloudWatch alarms active; Grafana dashboard reviewed by stakeholder |
| **Security** | No hardcoded credentials; all secrets in AWS Secrets Manager; IAM least-privilege |
| **Cost** | AWS Cost Explorer alert set at 80% of monthly budget |
| **Runbook** | On-call runbook covers top 5 failure scenarios with clear remediation steps |
| **Sign-off** | Engineering lead + product owner sign go/no-go form before GA |

---

## 7. Next Steps Post-POC (Roadmap)

| Priority | Feature | Rationale |
|---|---|---|
| P0 | Proxy rotation for anti-bot sites | Unblocks the ~15% of URLs behind Cloudflare |
| P0 | URL deduplication (Bloom filter in Redis) | Eliminates ~30% redundant crawls from UTM variants |
| P1 | Ollama on dedicated EC2 instance (GPU) | LLM-quality classification at zero API cost |
| P1 | Incremental re-crawl (change detection) | Re-crawl only URLs changed since last month — reduces cost 60% |
| P2 | Multi-region deployment (us-east-1 + eu-west-1) | Geographic load distribution; resilience |
| P2 | Content diff store (S3) | Track metadata changes over time for trend analysis |
| P3 | Domain-aware taxonomy classification | Better topic quality per vertical (e-commerce vs news) |
| P3 | Real-time crawl API (WebSocket progress) | Allow stakeholders to monitor live crawl status |
