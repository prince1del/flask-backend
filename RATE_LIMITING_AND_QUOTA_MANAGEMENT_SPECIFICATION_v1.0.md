# Rate Limiting and Quota Management Specification v1.0

## Executive Summary

This specification defines the quota, protection, cost-control, and throttling policies for the Smart Bill Capture Engine. The goal is to preserve service quality, keep OCR spending within budget, and provide deterministic fallback behavior when provider quotas or infrastructure limits are reached.

## 1. Google Vision Quota Management

### 1.1 Quota Analysis
- Free tier: 500 requests per day for development and testing only.
- Paid tier: 500,000 requests per month.
- Cost: $1.50 per 1,000 images.
- Rate limit: 1000 QPS burst, with practical cap enforced by the application layer.
- Quota reset: Daily at midnight UTC.
- Monitoring: Google Cloud Console usage dashboard and exported metrics.

### 1.2 Quota Management Strategy
- Use Google Vision only for production-critical requests where accuracy is preferred.
- Set a monthly budget of $300 for Google Vision usage.
- Negotiate a volume discount target of 20%, aiming for $1.20 per 1,000 images.
- Alert at 50%, 80%, and 95% quota usage.
- If quota is exhausted, fallback to AWS Textract immediately.
- If AWS is also unavailable or constrained, route eligible requests to Paddle OCR.
- Show the user the message: "Processing with backup service..."
- Queue retries for the next available quota window only when the request is still relevant and not expired.

```yaml
GOOGLE_VISION_QUOTA_STRATEGY:
  free_tier:
    requests_per_day: 500
    use_case: "Development and testing only"
    monitoring: "Daily reset at midnight UTC"

  paid_tier:
    requests_per_month: 500000
    cost_per_1000: 1.50
    monthly_budget: 300
    volume_discount_target: 20%

  quota_monitoring:
    check_frequency: "Every 5 minutes"
    alert_at_50_percent: true
    alert_at_80_percent: true
    alert_at_95_percent: true

  quota_exhaustion_handling:
    action: "Fallback to AWS Textract"
    user_message: "Processing with backup service..."
    queue_for_retry: "Next month"
    max_queue_size: 1000

  cost_control:
    monthly_budget_usd: 300
    if_exceeded: "Reduce non-critical requests"
    alternative: "Route to Paddle OCR"
```

## 2. AWS Textract Quota Management

### 2.1 Quota Analysis
- Synchronous API: 2 requests per second.
- Asynchronous API: 1000 requests per month on the free tier.
- Cost: $1.50 per page for synchronous and $1.00 per page for asynchronous after free-tier coverage.
- Monitoring: CloudWatch metrics and cost dashboards.
- Error type: ThrottlingException on rate limit breach.
- Quota increase process: Support case through AWS.

### 2.2 Quota Management Strategy
- Use the synchronous API for urgent requests when the rate limit is not exceeded.
- Use the asynchronous API for batch/document-heavy scenarios where queueing is acceptable.
- Set a monthly budget of $200 for Textract usage.
- Alert at 75% of the practical throttle threshold.
- If throttling occurs, queue the request using FIFO semantics and apply exponential backoff.
- If throttling persists after retry budget exhaustion, fallback to Paddle OCR.
- Store the queue in Redis with a maximum retention window of 24 hours.

```yaml
AWS_TEXTRACT_QUOTA_STRATEGY:
  sync_api:
    requests_per_second: 2
    use_case: "Real-time / urgent processing"
    cost_per_page: 1.50

  async_api:
    requests_per_month: 1000
    cost_per_page: 1.00
    use_case: "Batch processing"

  quota_management:
    monitor_via: "CloudWatch metrics"
    alert_threshold: 1.5

  throttling_handling:
    error: "ThrottlingException"
    action: "Queue request with backoff"
    backoff_sequence: [1s, 2s, 4s, 8s, 16s]
    max_retries: 3
    if_still_throttled: "Fallback to Paddle OCR"

  cost_control:
    monthly_budget_usd: 200
    monthly_projection: "730,000 pages × $1.00 = $730,000"
    mitigation: "Use Paddle 80%, AWS 20%"
    actual_cost: "$146,000/month (with Paddle)"

  queue_management:
    queue_type: "FIFO (fair)"
    max_queue_size: 500
    queue_timeout: 24_hours
    queue_storage: "Redis"
    monitoring: "Queue depth alerts"
```

## 3. Paddle OCR Resource Management

### 3.1 Resource Constraints
- CPU minimum: 4 cores.
- Memory per process: approximately 1GB.
- Recommended concurrent instances: 4.
- Model size: 100MB standard, 50MB with quantization.
- First load time: approximately 5 seconds.
- Inference time: 8-12 seconds on CPU and 2-3 seconds on GPU.
- Direct cost: Free open-source, infrastructure only.

### 3.2 Resource Allocation Plan
- Staging environment: 1 optional GPU instance for testing.
- Production: 1 CPU host with 4 cores plus optional GPU acceleration for peak throughput.
- Maximum concurrent local jobs: 4.
- If the queue exceeds 4 concurrent jobs, queue the excess requests.
- Queue timeout: 60 seconds per request.
- Memory alert threshold: 80% utilization.
- CPU alert threshold: 90% utilization.
- Disk alert threshold: 90% utilization.

```yaml
PADDLE_OCR_RESOURCE_MANAGEMENT:
  system_requirements:
    cpu_cores: 4
    memory_gb: 8
    disk_gb: 200
    gpu_optional: true

  model_sizing:
    model_size_mb: 100
    model_size_quantized_mb: 50
    first_load_time_seconds: 5

  concurrent_processing:
    max_concurrent: 4
    inference_time_cpu_seconds: 10
    inference_time_gpu_seconds: 2

  queue_management:
    if_exceeds_4_concurrent: "Queue request"
    queue_timeout_seconds: 60
    queue_max_size: 100

  monitoring:
    memory_usage_alert: 80%
    cpu_usage_alert: 90%
    disk_usage_alert: 90%
    inference_time_alert: 30_seconds

  cost:
    direct_cost: 0
    infrastructure_cost: "Included in compute"

  backup_during_outage:
    if_paddle_down: "Fallback to Google/AWS"
    recovery_time: "< 5 minutes restart"
```

## 4. System-Level Rate Limiting

### 4.1 User-Level Limits
- Per user: 10 uploads per minute.
- Burst capacity: 5x the normal rate for short, approved bursts.
- Enforcement key: JWT user_id.
- When exceeded: Return HTTP 429 and a Retry-After header.

### 4.2 Workspace-Level Limits
- Per workspace: 100 uploads per hour.
- Burst capacity: 5x the normal rate for small spikes.
- Enforcement key: workspace_id from the JWT.
- When exceeded: Return HTTP 429 and optionally queue the request for later processing.

### 4.3 IP-Level Limits
- Per IP: 1000 requests per hour.
- Enforcement point: WAF or edge gateway before the application layer.
- Burst capacity: 5x normal rate.
- When exceeded: Block or challenge the request at the edge.

### 4.4 Enforcement Model
- Implementation: Redis-backed sliding window limiter.
- Key format: rate_limit:{user_id}:{minute}
- Hard limit behavior: Reject immediately with 429.
- Exception path: Batch imports with prior approval may bypass the standard rate limit after manual escalation.

```yaml
SYSTEM_LEVEL_RATE_LIMITING:
  per_user:
    limit: 10
    enforcement: "JWT user_id"
    burst_capacity: 5x
    when_hit: "HTTP 429 + Retry-After header"

  per_workspace:
    limit: 100
    enforcement: "workspace_id from JWT"
    burst_capacity: 5x
    when_hit: "HTTP 429 + queue message"

  per_ip:
    limit: 1000
    enforcement: "CloudFront + WAF rules"
    burst_capacity: 5x
    when_hit: "Block at WAF level"

  exceptions:
    allowed: "Batch imports with pre-approval"
    process: "Request quota increase via support"

  monitoring:
    track_violations: true
    alert_if_user_hits_limit: "> 5 times/day"
    escalation: "Block user account (manual review)"
```

## 5. Quota Monitoring & Cost Control

### 5.1 Dashboards
- Real-time dashboard: current spend, current month projected spend, quota usage, rate limit violations, queue depth.
- Hourly dashboard: requests per provider, cost accrual, error rates, fallback triggers.
- Daily dashboard: bills processed, cost per bill, provider distribution, alert totals.

### 5.2 Alert Strategy
- Google Vision: 50% usage -> email; 80% -> Slack + email; 95% -> page + Slack + email.
- AWS Textract: throttling rate above 5% -> Slack; cost spike -> email.
- System: budget at 50% -> email; 80% -> Slack; 100% -> page CTO.

### 5.3 Escalation / Emergency Procedures
- If Google Vision quota is hit, fallback to AWS Textract.
- If AWS quota is hit, fallback to Paddle OCR.
- If Paddle OCR is down, queue requests and direct the user to manual review after timeout.
- If the monthly budget is exceeded, request CTO approval before increasing spend and route more traffic to the free local path.

```yaml
QUOTA_MONITORING_AND_COST_CONTROL:
  dashboards:
    real_time:
      - Current day spend
      - Current month projected spend
      - Quota usage by provider
      - Rate limit violations
      - Queue depth

    hourly:
      - Requests per provider
      - Cost accrual per provider
      - Error rates
      - Fallback triggers

    daily:
      - Bills processed
      - Cost per bill
      - Provider distribution
      - Alerts triggered

  alerts:
    google_vision:
      50_percent: "Email"
      80_percent: "Slack + Email"
      95_percent: "Page + Slack + Email"

    aws_textract:
      throttling_rate_gt_5_percent: "Slack"
      cost_spike: "Email"

    system:
      budget_50_percent: "Email"
      budget_80_percent: "Slack"
      budget_100_percent: "Page CTO"

  escalation:
    if_google_quota_hit: "Fallback to AWS"
    if_aws_quota_hit: "Fallback to Paddle"
    if_paddle_down: "Queue + manual review"
    if_monthly_budget_exceeded: "CTO approval for increase"

  cost_reporting:
    frequency: "Daily + Monthly"
    recipients: ["CTO", "Finance", "Engineering Lead"]
    content:
      - Spend by provider
      - Spend per bill
      - Spend per customer
      - Trend analysis
      - Budget vs actual
```

## 6. Cost Control Policy

- Monthly budget ceiling: $500 total.
- Recommended allocation: Google Vision $300 (60%), AWS Textract $200 (40%), Paddle OCR $0.
- If spend exceeds 80% of budget, reduce non-critical cloud OCR traffic.
- If spend exceeds 100%, stop non-critical cloud-dependent OCR flows and rely on local inference plus manual review.
- Finance review should occur monthly with engineering and operations stakeholders.

## 7. Emergency Procedures

- On provider quota exhaustion, immediately activate the next fallback provider.
- On sustained throttling, pause non-urgent cloud OCR jobs and drain the queue with local inference where possible.
- On budget overrun, freeze non-critical provider usage until the CTO approves a budget change.
- On a provider outage, route traffic to the next provider and notify the incident channel.

## Appendix: Monitoring Dashboard Layout

- Current daily spend by provider
- Rolling 30-day spend projection
- Provider-specific quota gauges at 50/80/95%
- Fallback trigger count and queue depth
- Request latency benchmarks for each provider
- Cost per bill output by provider

## Sign-Off

- [ ] CTO Review & Approval
- [ ] Finance Approval
- [ ] Engineering Manager Confirmation
- [ ] Ready for ASG Lockdown
