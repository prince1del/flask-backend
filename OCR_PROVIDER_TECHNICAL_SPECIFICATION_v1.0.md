# OCR Provider Technical Specification v1.0

## 1. Objective

This specification defines the baseline architecture, provider comparison, operational constraints, and fallback strategy for OCR processing in the NEXORA platform.

## 2. Provider Comparison Matrix

| Metric | Google Vision | AWS Textract | Paddle OCR |
|--------|---|---|---|
| Accuracy (invoices) | 95%+ | 93-95% | 90-92% |
| Speed (avg) | 5-8s | 6-9s | 8-12s |
| Cost per bill | $1.50 | $1.50 | Free |
| Uptime SLA | 99.9% | 99.95% | N/A (local) |
| Setup time | 30 min | 30 min | 5 min |
| Regional availability | Global | Global | N/A |
| Data privacy | DPA available | BAA available | Local only |
| Recommended for | High accuracy | Form processing | Offline/backup |

## 3. Provider Specifications

### 3.1 Google Vision API Specification

## Authentication
- Service account method (recommended)
- API key method (not recommended for production)
- Credential file location: ~/.gcp/credentials.json
- Environment variable: GOOGLE_APPLICATION_CREDENTIALS

## Pricing
- Per 1000 images: $1.50
- Free tier: 500 images/day
- Paid tier: $1.50/1000 images
- Monthly budget estimate: $300-500

## Quotas & Limits
- QPS: 1000 queries per second
- Daily limit free tier: 500 requests
- Burst limit: 100 QPS initially
- Rate limit error: 429 Too Many Requests
- Backoff strategy: Exponential (1s, 2s, 4s)

## API Response Format
{
  "responses": [
    {
      "fullTextAnnotation": {
        "text": "...",
        "confidence": 0.95
      },
      "error": null
    }
  ]
}

## Error Codes
- 429: Rate limit exceeded
- 503: Service unavailable
- 401: Unauthorized (bad credentials)
- 400: Bad request (invalid image)

## SLA
- Uptime: 99.9%
- Support: Standard (email)
- Response time: < 30 seconds average

### 3.2 AWS Textract API Specification

## Authentication
- IAM user with textract:DetectDocumentText
- AWS access key & secret key
- Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
- Region: us-east-1 (default)

## APIs
### Synchronous: detect_document_text()
- RPS: 2 requests per second
- Response time: 5-10 seconds
- Max file size: 5MB
- Cost: $1.50 per page
- Best for: Real-time, small documents

### Asynchronous: start_document_text_detection()
- RPS: 1000/month free tier
- Processing time: 1-5 minutes
- SNS/SQS notifications
- Cost: $1.00 per page
- Best for: Batch, large documents

## Pricing
- Per page: $1.50 (sync), $1.00 (async)
- Monthly budget: $200-300
- Volume discount: Available for 1M+ pages/month

## Error Codes
- ThrottlingException: Rate limit exceeded
- ServiceUnavailableException: Service down
- ValidationException: Invalid input
- AccessDeniedException: Bad credentials

## Response Format
{
  "DocumentMetadata": {...},
  "Blocks": [
    {
      "BlockType": "LINE",
      "Text": "Invoice No. INV-2026-001",
      "Confidence": 0.95
    }
  ]
}

### 3.3 Paddle OCR Specification

## Model Selection
- Recommended: PP-OCRv3
- Size: 100MB (with quantization: 50MB)
- Language: English, Chinese, Hindi
- Accuracy: ~90-92% on invoices
- Speed: 8-12 seconds (CPU), 2-3 seconds (GPU)

## Performance
- CPU (8 cores): ~10 seconds per image
- GPU (V100): ~2 seconds per image
- Memory: ~1GB RAM
- Concurrent instances: 4 (recommended)

## Deployment
- Local installation (pip install paddleocr)
- Docker image available
- Model download: ~100MB first run
- Caching: Models cached in ~/.paddleocr/

## Pricing
- Free (open source)
- No API costs
- Infrastructure only (CPU/GPU)

## License
- Apache 2.0
- Commercial use: Allowed
- Attribution: Required in documentation
- Modifications: Allowed

## 4. Recommended Strategy

- Primary: Paddle OCR (free, offline)
- Fallback 1: Google Vision (best accuracy)
- Fallback 2: AWS Textract (form-friendly)
- Annual cost estimate: $150,000 (20% cloud, 80% paddle)

## 5. Implementation Priority

1. Google Vision (primary for Phase 2, Week 3)
2. AWS Textract (fallback, Phase 2, Week 4)
3. Paddle OCR (backup, Phase 2, Week 4)
4. Router logic (combines all 3, Phase 2, Week 4)

## 6. Decision Framework

- Use Paddle OCR when offline processing, low-cost inference, or local-only deployment is required.
- Use Google Vision when document readability and accuracy are critical and cloud connectivity is available.
- Use AWS Textract when form-field extraction and structured invoice layout analysis are required.
- Route to the next provider automatically when the current provider returns a validation error, timeout, quota error, or poor confidence threshold breach.

## 7. Next Steps

- Negotiate contracts with Google & AWS
- Set up development accounts
- Begin implementation with Google Vision
