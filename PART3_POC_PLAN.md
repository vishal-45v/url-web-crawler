# Part 3 — Engineering POC Plan, Blockers, Estimates & Release

---

## 1. POC Objectives

The Proof of Concept answers three questions before full-scale investment:

| Question | Success Criterion |
|---|---|
| Can the crawler handle all page types? | All 3 test URLs return correct metadata |
| Can the pipeline sustain throughput? | ≥ 5,000 URLs/min under load test |
| Is the cost model viable? | Actual AWS spend extrapolates to < $50K/month at 1B URLs |

> **Note on cost target:** The Part 2 architecture estimates ~$35,600/month at 1B URLs/month (KeyBERT fleet). The POC validates that the cost model scales linearly — not that we hit an arbitrary budget number. The right question is: does actual load-test cost extrapolate predictably to the design estimate?

---

## 2. Implementation Schedule (Solo Developer, Startup Pace)

> Estimates assume 1 developer, 8 working hours/day.
> Part 1 code (fetcher, extractor, classifier) is **already complete** — Phase 1 adapts it for the worker model, not rebuilds it.
> Startup pace: parallel where possible, no ceremony for ceremony's sake.

### Phase 0 — Environment & CI/CD Setup
**Duration: 1 day** *(was 3 — AWS console + Terraform modules are fast with experience)*

| Task | Done When |
|---|---|
| AWS account, IAM roles, VPC, ECR repo, S3 buckets, SQS queues | `aws sts get-caller-identity` succeeds; resources visible in console |
| GitHub Actions: test → build → push ECR → deploy ECS | Push to `main` auto-deploys |

**Risk:** AWS quota increase requests (ECS task limits, ElastiCache nodes) — submit on Day 1, takes 24–48h to process.

---

### Phase 1 — Worker Adaptation (Part 1 Code → Pipeline Worker)
**Duration: 1 day** *(Part 1 core logic already complete — this wraps it in an SQS poll loop)*

| Task | Done When |
|---|---|
| `worker/fetch_worker.py` — poll SQS Main Queue → call `fetcher.py` → write HTML to S3 → emit to Fetch→Extract Queue | 100-URL batch processed locally, S3 object visible |
| `worker/extract_worker.py`, `classify_worker.py`, `store_worker.py` — same pattern per fleet | End-to-end: URL in SQS → metadata in DynamoDB via 4 workers |

**Why so fast?** `fetcher.py`, `extractor.py`, and `classifier/` are unchanged. Worker scripts are thin poll-loop wrappers — each is ~50 lines.

---

### Phase 2 — Queue + Fleet Infrastructure
**Duration: 3 days** *(was 6 — SQS/ECS setup is fast; Redis + auto-scaling are config, not code)*

| Day | Task | Done When |
|---|---|---|
| 1 | 4 inter-stage SQS queues + DLQs; IAM policies for ECS → SQS + S3 + DynamoDB | Test message flows end-to-end through all 4 queues |
| 2 | ECS Fargate task definitions for all 4 fleets; Docker images pushed to ECR; ElastiCache Redis wired | All 4 ECS services running; CloudWatch logs visible |
| 3 | Auto-scaling: SQS depth → ECS task count per fleet; integration test 10K URLs | 10K URLs processed; < 2% DLQ rate; all in DynamoDB |

---

### Phase 3 — Storage Layer
**Duration: 2 days** *(was 4 — schemas are defined in Part 2; implementation is mechanical)*

| Day | Task | Done When |
|---|---|---|
| 1 | DynamoDB: PK/SK, GSI (year_month-index), TTL; S3 lifecycle rules (Glacier 12mo) | Schema validated; sample write + read works |
| 2 | Aurora PostgreSQL: `crawl_results` + `topics` tables, PgBouncer pooler; Glue crawler + Athena test query | Athena query returns correct row count, < 10s |

---

### Phase 4 — Observability
**Duration: 1 day** *(was 3 — CloudWatch dashboards over Grafana for POC speed)*

| Task | Done When |
|---|---|
| CloudWatch alarms: SQS depth per fleet, DLQ rate, ECS CPU/memory | Test alarm fires SNS notification |
| CloudWatch dashboard: crawl throughput, success rate, queue depths | Dashboard readable by non-technical stakeholder |
| X-Ray tracing: annotate each fleet's critical path | Trace visible in X-Ray console |

> Grafana is production polish — skip for POC. CloudWatch dashboards are sufficient to demo observability.

---

### Phase 5 — Load Test & Cost Validation
**Duration: 2 days** *(was 4 — 100K URL test is sufficient for extrapolation; don't need 1M for a POC)*

| Day | Task | Done When |
|---|---|---|
| 1 | Load test: 100K URLs pumped through pipeline; measure throughput, DLQ rate, per-fleet scaling | ≥ 5,000 URLs/min sustained; DLQ < 2% |
| 2 | Chaos test: kill 50% of ECS tasks mid-run; verify queue recovery; check AWS Cost Explorer for the run | No data loss; cost extrapolation documented |

**Cost validation:** Run actual AWS cost for 100K-URL test → multiply to 1B. Compare against $35,600/month Part 2 estimate. Acceptable variance: ±20%.

---

### Phase 6 — POC Release
**Duration: 1 day** *(was 3 — a POC demo is not a production release)*

| Task | Done When |
|---|---|
| Docs: update README, architecture.md, runbook (top 5 failure scenarios) | PR merged |
| Stakeholder demo: live crawl of all 3 test URLs through the pipeline | Go/no-go form signed |

> **No Blue/Green canary for a POC.** Blue/Green is production release ceremony. A POC ships to a demo environment, gets signed off, and the production hardening plan is scoped separately.

---

**Total: ~11 working days (≈ 2.5 weeks, solo developer)**

```
Week 1:  Phase 0 + Phase 1 + Phase 2 (infra up, workers running)
Week 2:  Phase 3 + Phase 4 + Phase 5 (storage, observability, load test)
Week 2.5: Phase 6 (docs + demo)
```

**vs. original estimate of 5.5 weeks:**
- Phase 0: 3 days → 1 day (AWS setup is fast for an experienced engineer)
- Phase 1: 5 days → 1 day (code already exists from Part 1)
- Phase 2: 6 days → 3 days (SQS/ECS are config-heavy, not code-heavy)
- Phase 3: 4 days → 2 days (schemas are already designed in Part 2)
- Phase 4: 3 days → 1 day (CloudWatch over Grafana for POC)
- Phase 5: 4 days → 2 days (100K URLs sufficient for extrapolation)
- Phase 6: 3 days → 1 day (POC demo ≠ production release ceremony)

---

## 3. Blockers — Known vs Unknown

### Known Blockers (Handled — standard solutions)

| # | Blocker | Severity | Solution | ETA |
|---|---|---|---|---|
| K1 | Amazon bot detection | Medium | Playwright headless + random User-Agent + 2s delay | Already in `fetcher.py` |
| K2 | robots.txt fetch latency per domain | Low | Redis cache, 24h TTL | Phase 2, Day 2 |
| K3 | SQS 256KB message size limit | Low | Store HTML in S3; pass S3 path in message (never raw HTML) | Phase 1 design |
| K4 | Playwright memory (~250MB per Chromium) | Medium | 1 Playwright instance per Fetch Worker task; 1GB task memory | Phase 2, Day 2 |
| K5 | KeyBERT model cold-start | Low | Pre-download at Docker build time | Already in Dockerfile |
| K6 | URL normalization (UTM variants) | Low | Strip UTM params, sort query params, lowercase scheme | Phase 1 |
| K7 | AWS quota limits | Medium | Submit increase requests Day 1 (24–48h SLA) | Phase 0, Day 1 |

### Unknown Blockers (High-risk — investigate during POC)

| # | Blocker | Risk | Contingency | Discovery Point |
|---|---|---|---|---|
| U1 | Aurora connection exhaustion at 500 concurrent Store workers | High | PgBouncer in front of Aurora; or Aurora Data API (serverless connection model) | Phase 5 load test |
| U2 | DynamoDB hot partition on high-volume domains | Medium | Append shard suffix to PK: `domain#url_hash#shard(0-9)` | Phase 5 load test |
| U3 | Glue catalog partition lag (new `year_month` not immediately queryable) | Low | `MSCK REPAIR TABLE` post-run; or Glue crawler schedule | Phase 3, Day 2 |
| U4 | Cloudflare / hCaptcha JS challenges blocking ~15% of URLs | High | Proxy rotation (Bright Data / Oxylabs); flagged URLs routed to manual DLQ | Phase 1–2 |
| U5 | ECS Fargate cold-start latency (60–90s image pull) | Medium | Minimum 2 tasks always running per fleet; pre-warm on cron schedule | Phase 2 auto-scaling |
| U6 | Redis network latency > 50ms/URL (rate limit check) | Low | Ensure Redis in same VPC/AZ as ECS tasks | Phase 5 profiling |

---

## 4. Go / No-Go Criteria

The POC is successful when ALL of the following pass:

| Criterion | Target | How to Measure |
|---|---|---|
| All 3 test URLs return correct metadata | 100% | Manual API response validation |
| Crawl throughput | ≥ 5,000 URLs/min sustained | CloudWatch `SQS NumberOfMessagesDeleted` over 10 min |
| Crawl success rate | ≥ 95% (DLQ ≤ 5%) | `DLQ_count / total_messages` |
| End-to-end pipeline latency | URL enters Main Queue → stored in DynamoDB ≤ 60s | X-Ray trace, P99 |
| Data consistency | 100% of successful crawls in DynamoDB | Count: SQS sent vs DynamoDB items |
| Cost extrapolation | Actual 100K-URL cost × 10,000 within ±20% of $35,600/month estimate | AWS Cost Explorer |
| Recovery from failure | No data loss after killing 50% of tasks | Reprocessed count + DynamoDB total after chaos |
| Automated alerting | DLQ alarm fires within 5 min of breach | Inject 5% failures; confirm SNS |

---

## 5. Evaluation of the POC

### Test URL Set

```
Core (3 provided):
  - Amazon product page (JS-rendered SPA)
  - REI blog post (static HTML)
  - CNN news article (static HTML)

Extended (7 additional):
  - React SPA (client-side rendered)
  - Page with no meta description (fallback test)
  - 301 redirect chain
  - Non-English page (Japanese/Chinese)
  - Minimal HTML (just title + body)
  - PDF URL (should return 415 Unsupported)
  - robots.txt disallow (should skip or flag)
```

For each URL, validate:
1. `title` matches visible page title ✓/✗
2. `description` is meaningful (> 20 chars, not noise) ✓/✗
3. `topics` — ≥ 5 relevant topics with score > 0.5 ✓/✗
4. `word_count` > 0 ✓/✗
5. `crawled_at` within 10s of request ✓/✗

### Scale Validation

- Inject 100K URLs into SQS Main Queue
- Measure wall-clock time to drain queue across all 4 fleet stages
- Calculate URLs/min; validate ≥ 5,000 target
- Confirm DynamoDB item count matches input
- Record actual AWS cost; extrapolate to 1B

---

## 6. Release Plan (POC → Production)

### POC Release (End of Week 2.5)

The POC ships to a **demo environment** — not production. The release is:
1. Stakeholder demo: live crawl of all 3 test URLs through the full pipeline
2. Metrics shown on CloudWatch dashboard
3. Go/no-go form signed

### Production Release (Post-POC, Separate Scope)

Once POC is signed off, production release follows a 3-stage rollout:

```
Stage 1 (Week 1): Shadow mode — pipeline runs alongside existing system, writes to separate DynamoDB table
Stage 2 (Week 2): 10% traffic — route 10% of monthly crawl batch to new pipeline; monitor DLQ rate
Stage 3 (Week 3): 100% cutover — decommission old pipeline after 1 week of clean metrics
```

**Rollback trigger:** DLQ rate > 5% OR P0 alert during any stage → immediately reroute to old pipeline.
**Rollback time:** < 5 min (SQS routing change, no code deploy needed).

### Definition of Production-Ready

| Dimension | Standard |
|---|---|
| **Code** | ≥ 80% unit test coverage; integration tests for all 10 test URLs |
| **Docs** | README, architecture.md, runbook (top 5 failure scenarios) up to date |
| **Observability** | All CloudWatch alarms active; cost alert set at 80% monthly budget |
| **Security** | No hardcoded credentials; all secrets in AWS Secrets Manager; IAM least-privilege |
| **Sign-off** | Engineering lead + product owner sign go/no-go before Stage 3 cutover |

---

## 7. Post-POC Roadmap

| Priority | Feature | Rationale |
|---|---|---|
| P0 | Proxy rotation for anti-bot sites | Unblocks ~15% of URLs behind Cloudflare |
| P0 | Bloom filter dedup in Redis | Eliminates ~30% redundant crawls from UTM variants |
| P1 | Ollama on dedicated GPU EC2 | LLM-quality classification at zero API cost; ~$6K/month savings vs KeyBERT at scale |
| P1 | Incremental re-crawl (change detection via S3 ETag) | Re-crawl only changed URLs — reduces cost ~60% |
| P2 | Multi-region (us-east-1 + eu-west-1) | Geographic distribution; resilience |
| P2 | Content diff store (S3) | Track metadata changes over time for trend analysis |
| P3 | Domain-aware taxonomy | Better topic quality per vertical (e-commerce vs news vs editorial) |
