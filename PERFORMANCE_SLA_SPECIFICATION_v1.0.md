# Performance SLA Specification v1.0

## Executive Summary

This document defines the performance targets, benchmark thresholds, and operational guardrails for the OCR-driven bill capture pipeline in Phase 2. The goals are to ensure user-visible responsiveness, protect provider uptime and cost budgets, and provide a clear go/no-go basis for ASG rollout.

## Performance Targets Summary

| Component | Target | p95 | p99 | Max Acceptable |
|-----------|--------|-----|-----|----------------|
| Image Quality Check | 1.0s | 0.8s | 1.5s | 2.0s |
| OCR Extraction (Google Vision) | 10s | 8s | 12s | 30s |
| OCR Extraction (AWS Textract) | 10s | 9s | 15s | 20s |
| OCR Extraction (Paddle OCR) | 15s | 12s | 20s | 60s |
| Parsing | 5s | 4s | 6s | 10s |
| Validation | 2s | 1.5s | 3s | 5s |
| End-to-End | 30s | 25s | 35s | 60s |

## 1. Image Quality Check SLA

### Target Performance
- Target: < 1000ms per image
- p50 (median): 400ms
- p95: 800ms
- p99: 1500ms
- Max acceptable: 2000ms

### By Image Size
- 1MB image: 200ms target, 400ms p95
- 5MB image: 500ms target, 800ms p95
- 10MB image: 1000ms target, 1500ms p95

### Rationale
- OpenCV-based quality checks are estimated at 200-400ms for typical images.
- Additional buffer is reserved for resizing, normalization, and image preprocessing.
- Sub-second feedback is expected for common image validation flows.

## 2. OCR Extraction Performance

### Google Vision
- Target: < 10 seconds
- p50: 5 seconds
- p95: 8 seconds
- p99: 12 seconds
- Max acceptable: 30 seconds
- Failure/Retry Policy: Switch to AWS Textract on quota, timeout, or provider outage

### AWS Textract
- Target: < 10 seconds
- p50: 6 seconds
- p95: 9 seconds
- p99: 15 seconds
- Max acceptable: 20 seconds
- Failure/Retry Policy: Switch to Paddle OCR on validation or service errors

### Paddle OCR
- Target: < 15 seconds
- p50: 8 seconds
- p95: 12 seconds
- p99: 20 seconds
- Max acceptable: 60 seconds
- Deployment Note: Local/offline inference with no external network dependency

## 3. End-to-End Processing SLA

### Pipeline Path
Upload → Image Quality Check → OCR Extraction → Parsing → Validation → Response

### Target Budget
- Target: < 30 seconds end-to-end
- p50: 15 seconds
- p95: 25 seconds
- p99: 35 seconds
- Max acceptable: 60 seconds

### Latency Breakdown
- Upload and Storage: 500ms
- Image Quality Check: 500ms
- OCR Extraction: 10000ms
- Parsing: 5000ms
- Validation: 2000ms
- Response Preparation: 1000ms
- Buffer/Overhead: 2000ms
- Total: 21000ms

### Test Conditions
- 5MB average image size
- 50ms network latency
- Providers healthy
- No sustained throttling

## 4. Throughput & Concurrency Targets

### Daily Throughput
- Target: 1000 bills/day
- Average per hour: 42 bills
- Average per minute: 0.7 bills
- Average per second: 0.012 bills

### Peak Scenarios
- Peak hour: 200 bills
- Peak rate: 0.06 bills/second
- Concurrent users: 20
- Concurrent uploads: 100

### System Capacity Requirements
- Database connections: 20 default pool, 50 max peak
- Memory: 4GB minimum heap
- CPU: 4 cores minimum
- Storage: Cloud-backed object storage with scaling support
- OCR Provider Capacity: Minimum 1000 QPS or equivalent provider quota available for production bursts

## 5. Database & Cache Performance

### Query Response Time Targets
- Get bill by ID: < 10ms
- List bills for workspace: < 100ms
- Insert bill capture: < 50ms
- Update bill status: < 50ms
- Search by invoice number: < 100ms
- Get validation results: < 20ms

### Required Indexes
- Primary key: id
- Composite: (workspace_id, status)
- Composite: (workspace_id, invoice_number)
- Single: created_at
- Single: supplier_name

### Cache Strategy
- Bill metadata: 30 minutes TTL, 80% hit target
- Validation results: 60 minutes TTL
- Supplier list: 60 minutes TTL
- Workspace settings: 1 day TTL
- User preferences: 1 day TTL

## 6. Load Testing Plan

### Test Scenarios
1. Normal Load — 10 concurrent users, 15 minutes
2. Moderate Load — 50 concurrent users, 15 minutes
3. Peak Load — 100 concurrent users, 15 minutes
4. Stress Test — 500 concurrent users, 5 minutes

### Measurement Points
- Response time p50, p95, p99
- Error rate
- Provider throttling or quota exhaustion
- Database connections in use
- CPU and memory utilization
- Cache hit rate
- Queue depth if asynchronous processing is enabled

### Go/No-Go Criteria
- All scenarios meet p95 under the applicable end-to-end SLA
- Error rate remains under 1% during sustained test runs
- No database connection exhaustion or lock contention anomalies
- CPU remains under 80% during peak test runs

## 7. Monitoring & Alerting Thresholds

### Critical Alerts
- Error rate > 10%
- All OCR providers unavailable simultaneously
- Database unreachable or connection pool exhausted
- Storage utilization > 90%
- p95 latency > 60 seconds on end-to-end path

### Warning Alerts
- Error rate > 5%
- p95 latency > 40 seconds
- Cache hit rate < 70%
- Provider quota usage > 80% of monthly budget
- Cost overrun > 120% of planned weekly budget

### Informational Alerts
- Provider fallback triggered
- Health check failover events
- Unexpected increase in queue depth
- Quota milestone events at 50%, 75%, and 90%

### Dashboard Requirements
- Real-time latency dashboards for p50, p95, p99
- Provider health status and fallback counts
- Concurrent user count and requests per second
- Cost accrual by provider
- Queue depth and backlog monitoring

## 8. Performance Improvement Roadmap

### If image quality checks exceed 1s
- Reduce image size before analysis
- Parallelize quality checks where possible
- Use GPU acceleration if available for high-volume environments
- Cache repeated results for identical file fingerprints

### If OCR extraction exceeds target
- Enable provider-specific retry with exponential backoff
- Add asynchronous queueing for large jobs
- Optimize image preprocessing and resize thresholds
- Consider fallback routing to the next provider in the chain

### If end-to-end time exceeds 30s
- Move long-running OCR processing to async workers
- Optimize parse/validation logic
- Add Redis-backed cache for repeated metadata lookups
- Improve database query paths and transaction scope

## 9. Benchmark Test Data Set

### Test image composition (100 images)
- Clear images: 60
- Blurry images: 10
- Low-light images: 10
- Large images: 10
- Small images: 10

### Expected benchmark characteristics
- Clear images: 8-10s OCR time
- Blurry images: 10-15s with lower confidence
- Low-light images: 12-18s and more retry pressure
- Large images: 12-15s due to higher OCR compute load
- Small images: 5-7s

## Sign-Off

- [ ] CTO Review & Approval
- [ ] Performance Engineer Validation
- [ ] Backend Lead Confirmation
- [ ] Ready for ASG Phase 2
