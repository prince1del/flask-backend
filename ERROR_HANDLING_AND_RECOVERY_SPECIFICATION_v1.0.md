# Error Handling and Recovery Specification v1.0

## Executive Summary

This specification defines the fault-tolerance, retry, fallback, user communication, and recovery behavior for the Smart Bill Capture Engine during OCR and invoice-processing operations. The objective is to preserve reliability, reduce user-visible failures, maintain data integrity, and provide deterministic escalation paths when provider or infrastructure issues occur.

## 1. OCR Provider Failure Scenarios & Recovery

### 1.1 Google Vision Failures

#### Failure Mode: API Timeout (> 30 seconds)
- Scenario: Google Vision takes longer than the configured timeout window.
- Cause: Provider overload, large image, or transient network delay.
- Detection: Response time exceeds 30 seconds.
- Action: Abort the Google request, immediately fallback to AWS Textract.
- Retry: No retry on the same request after timeout; allow the backup provider to process.
- User message: "Processing with backup service..."
- Logging: ERROR with provider name, latency, and timeout threshold.
- Monitoring: Alert if timeout rate exceeds 5% over a rolling window.

#### Failure Mode: Rate Limit Exceeded (429)
- Scenario: Google Vision quota is exceeded or provider throttles the request.
- Cause: Too many requests or exhausted quota.
- Detection: HTTP 429 response.
- Action: Fallback to AWS Textract immediately.
- Retry: Exponential backoff with jitter, up to 3 attempts, then move to the next provider.
- User message: "Service busy, retrying with a backup provider..."
- Monitoring: Alert when quota usage reaches 80%.

#### Failure Mode: Service Unavailable (503)
- Scenario: Google Vision service is down or unhealthy.
- Cause: Regional incident or provider degradation.
- Detection: HTTP 503 or equivalent platform error.
- Action: Fallback to AWS Textract.
- Retry: Exponential backoff with 1s, 2s, 4s, and 8s delays, capped at 3 total attempts.
- User message: "Service temporarily unavailable, retrying with backup OCR..."
- Escalation: If the outage lasts more than 30 minutes, page the on-call operations team.

#### Failure Mode: Authentication Failure (401/403)
- Scenario: Invalid credentials, revoked key, or reduced permissions.
- Cause: Credential expiration or misconfiguration.
- Detection: HTTP 401 or 403.
- Action: Do not retry automatically; fallback to AWS Textract only if credentials are not required for the next provider.
- Logging: ERROR with credential status and provider name.
- Alert: Critical, page the on-call engineer immediately.
- Recovery: Manual credential rotation or IAM permission remediation.

#### Failure Mode: Bad Request (400)
- Scenario: Image cannot be processed due to invalid format or corrupted payload.
- Cause: Invalid image encoding, corrupted bytes, or unsupported image structure.
- Detection: HTTP 400 response.
- Action: Reject the request and display a clear validation message.
- Retry: No retry.
- User message: "Invalid image format. Please upload a JPEG or PNG file."
- Logging: WARNING with sanitized file metadata.

### 1.2 AWS Textract Failures

#### Failure Mode: Throttling (ThrottlingException)
- Scenario: AWS Textract rejects requests due to rate limiting.
- Cause: Too many concurrent requests or burst traffic.
- Detection: ThrottlingException response.
- Action: Queue the request in FIFO order and fallback to Paddle OCR if the queue exceeds the allowed retry budget.
- Retry: Exponential backoff with 1s, 2s, 4s, and 8s delays, up to 3 attempts.
- User message: "Request queued, processing in order..."
- Monitoring: Alert if queue depth exceeds 50 pending items.

#### Failure Mode: Service Unavailable
- Scenario: AWS Textract is degraded or unavailable.
- Cause: Regional service issue or dependency fault.
- Detection: ServiceUnavailableException.
- Action: Fallback to Paddle OCR.
- Retry: Exponential backoff with 2 total attempts.
- User message: "Using local processing service..."
- Monitoring: AWS health checks every 5 minutes.

#### Failure Mode: Invalid PDF or Document
- Scenario: Uploaded document is encrypted, malformed, or not readable by the provider.
- Cause: Corrupted PDF, encrypted document, unsupported structure.
- Detection: InvalidDocumentException or parser failure.
- Action: Reject with a clear message and request re-upload or file conversion.
- Retry: No automatic retry.
- User message: "PDF is corrupted or encrypted. Please try another file."
- Logging: WARNING with document fingerprint reference.

### 1.3 Paddle OCR Failures

#### Failure Mode: Model Load Error
- Scenario: OCR model cannot initialize on the local runtime.
- Cause: Missing model assets, corrupted cache files, disk permissions, or missing dependencies.
- Detection: ModelLoadError or configuration error during startup.
- Action: Log the error, stop the local OCR path, and escalate to manual review.
- Retry: None; operator recovery is required.
- User message: "Automatic OCR is unavailable at the moment. Manual review is required."
- Alert: Critical to the operations team.
- Recovery: Restart the service, verify model cache, and ensure disk space.

#### Failure Mode: Out of Memory (OOM)
- Scenario: GPU or CPU memory is exhausted during inference.
- Cause: Large image or excessive concurrent inference jobs.
- Detection: MemoryError or process termination due to OOM.
- Action: Fall back to CPU processing if GPU mode fails; otherwise request manual review.
- Retry: One CPU retry only if memory allows.
- User message: "Processing large image, please wait..."
- Monitoring: Memory usage alerts at 80% and 95% threshold.

#### Failure Mode: Timeout (> 60 seconds)
- Scenario: Local OCR processing exceeds the allowed runtime threshold.
- Cause: Very large image, slow hardware, or CPU saturation.
- Detection: Timeout exceeded after 60 seconds.
- Action: Cancel the request, store the raw image for later review, and ask the user to try a smaller or clearer image.
- Retry: No automatic retry.
- User message: "Image too large or complex. Please upload a smaller or clearer image."
- Logging: WARNING with timing metrics.

### 1.4 Complete Provider Failure

#### Failure Mode: Google → AWS → Paddle All Fail
- Scenario: All OCR providers are unavailable at the same time.
- Cause: Provider outage, network partition, or system-wide infrastructure issue.
- Detection: All three provider paths return errors or time out.
- Action: Stop automatic processing and route the case to manual entry or review.
- User experience: "All automatic processing is temporarily unavailable. Please enter the bill data manually or contact support."
- Escalation: Immediate alert to the CTO and platform operations team.
- Recovery: Restore provider health one at a time and run a synthetic OCR smoke test.

## 2. Fallback Logic & Retry Strategies

### 2.1 Fallback Order

Decision flow:
1. Try Google Vision first for maximum accuracy.
2. If Google returns 429, 503, or timeout, fallback to AWS Textract.
3. If AWS returns throttling or unavailable, fallback to Paddle OCR.
4. If all providers fail, route to manual review.
5. If the request is a client error (400), reject with guidance and do not retry.

### 2.2 Retry Strategy: Exponential Backoff

Algorithm:
- Retry delays: 1s, 2s, 4s, 8s, 16s for transient provider faults.
- Add jitter to avoid synchronized retries.
- Stop retrying when the retry budget is exhausted or the provider returns a non-transient error.
- Keep the total provider retry budget within the overall end-to-end SLA budget.

### 2.3 Circuit Breaker Pattern

States:
- CLOSED: Normal operation.
- OPEN: Provider is unhealthy; short-circuit requests to this provider for a cooldown period.
- HALF_OPEN: Send a small number of test requests to check recovery.

Thresholds:
- Failure rate threshold: 5% over a 5 minute window.
- Cooldown period: 5 minutes.
- Recovery test: one synthetic request before closing the circuit.

## 3. File Handling Errors

### 3.1 Corrupted Image File
- Detection: Image decode failure or invalid magic bytes.
- Response: Reject upload before OCR attempt.
- User message: "Image file is damaged. Please upload a different copy."
- Logging: WARNING with approximate file size and mime type.

### 3.2 Unsupported Format
- Detection: MIME type is not JPEG, PNG, or PDF.
- Response: Reject immediately.
- User message: "Only JPEG, PNG, and PDF are supported."
- Suggestion: "Convert the file to JPEG or PNG before upload."

### 3.3 File Too Large (> 50MB)
- Detection: File size exceeds the upload limit.
- Response: Reject before processing starts.
- User message: "File exceeds 50MB. Please compress the image."
- Logging: WARNING with file size and upload path.

### 3.4 PDF Parsing Failure
- Detection: PDF convert or page extraction fails.
- Response: Either route to AWS Textract for native document handling or reject with conversion guidance.
- User message: "Unable to process this PDF. Please upload a JPEG or PNG version."

## 4. Database Error Handling

### 4.1 Connection Pool Exhausted
- Detection: All pooled connections are busy or timed out.
- Response: Queue the write or return a temporary outage message.
- User message: "System busy. Please try again in a moment."
- Monitoring: Alert if pool utilization exceeds 90% for more than 2 minutes.

### 4.2 Query Timeout
- Detection: Query exceeds 30 seconds.
- Response: Log slow query, retry once if safe, otherwise return a temporary processing error.
- Monitoring: Alert on repeated query timeout patterns.

### 4.3 Constraint Violation (Duplicate Invoice)
- Detection: Unique integrity constraint conflict.
- Response: Return a duplicate detection message and stop the write.
- User message: "This invoice already exists. Please review the duplicate record."
- Logging: INFO or WARNING depending on the operational context.

## 5. User Notifications & Error Messages

### 5.1 Error Message Framework
- Use actionable language, not raw stack traces.
- Include an immediate next step whenever possible.
- Replace generic messages with contextual guidance.

### 5.2 Recommended Messages
- Low image quality: "Image quality is too low. Please retake the photo in better lighting."
- Provider busy: "Service is busy. Retrying with a backup provider..."
- Duplicate invoice: "This invoice already exists in the system."
- Provider unavailable: "Automatic OCR is temporary unavailable. You can continue with manual entry."

### 5.3 Support Escalation Rules
- Three repeated failures for the same user and same file type escalate to a support case.
- Provider-wide incident triggers an immediate on-call escalation.
- Authentication or authorization failures trigger an immediate security alert.

## 6. Monitoring & Alerting

### 6.1 Provider Health Checks
- Run synthetic OCR checks every 5 minutes against each provider.
- Track response times, success rate, and quota usage.
- Record fallback counts, queue depth, and provider-specific error rates.

### 6.2 Failure Rate Alerts
- Critical: overall error rate exceeds 10% or all providers fail.
- Warning: failure rate exceeds 5% or fallback rate exceeds 10%.
- Info: queue depth exceeds 50 or quota usage exceeds 80%.

### 6.3 Dashboard Metrics
- Provider availability by name
- p50, p95, p99 latency
- Rate limit events
- Manual review volume
- Retry counts and fallback counts
- Cost per provider and predicted spend trend

## 7. Graceful Degradation

### 7.1 Partial Success Handling
- If OCR succeeds but key fields are uncertain, display raw OCR text and highlight low-confidence fields for user review.
- Do not auto-publish uncertain data without explicit confirmation.

### 7.2 Manual Review Fallback
- If all providers fail or confidence remains below threshold, route the bill for manual review.
- Preserve the original uploaded image and OCR artifacts for auditability.

### 7.3 Data Consistency Assurance
- Never write partial invoice data without a successful validation step.
- Ensure audit logs retain the original error state and fallback path.
- Apply idempotent behavior for retries to prevent duplicate invoice creations.

## 8. Error Testing Strategy

### 8.1 Chaos Scenarios
- Inject 429, 503, and timeout errors per provider.
- Simulate corrupted images and oversized file uploads.
- Force database connection pool saturation.
- Verify that the fallback path uses the next provider in order.

### 8.2 Acceptance Tests
- Google timeout should fallback to AWS within the configured timeout budget.
- AWS throttling should queue or retry within the permitted policy.
- Corrupted files should be rejected without OCR invocation.
- Complete provider failure should display a manual review path.

### 8.3 Validation Checklist
- [ ] All provider-specific failure types are covered.
- [ ] Retry backoff and timeout values are configurable.
- [ ] No duplicate invoice posts occur during retries.
- [ ] User messaging is actionable and non-technical.
- [ ] Monitoring triggers alerts on critical provider failures.
- [ ] Manual review workflow is available when all automation fails.

## Appendix: Error Codes Reference

| Code | Provider / Layer | Meaning | Action |
|------|------------------|---------|--------|
| 503 | Google Vision | Service unavailable | Fallback to AWS |
| 429 | Google Vision | Rate limit exceeded | Retry with backoff and fallback |
| 401/403 | Google Vision | Auth failure | Immediate ops alert |
| 400 | Google Vision | Bad request | Reject user input |
| ThrottlingException | AWS Textract | Rate limit | Queue or fallback |
| ServiceUnavailableException | AWS Textract | Service down | Fallback to Paddle |
| OOMError | Paddle OCR | Memory issue | CPU fallback or manual review |
| ModelLoadError | Paddle OCR | Model init failure | Manual review |
| ERR_IMAGE_CORRUPTED | App | Corrupt image | Reject and advise reupload |
| ERR_DUPLICATE_INVOICE | App | Duplicate invoice | Block duplicate record |

## Sign-Off

- [ ] Technical Architect Review
- [ ] Backend Lead Confirmation
- [ ] QA Approval
- [ ] Ready for ASG Phase 2 Implementation
