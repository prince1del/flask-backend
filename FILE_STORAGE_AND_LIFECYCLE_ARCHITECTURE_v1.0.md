# File Storage and Lifecycle Architecture v1.0

## Executive Summary

This specification defines the storage architecture, lifecycle policy, backup strategy, and compliance controls for bill images and related documents used by the Smart Bill Capture Engine. The design targets resilient, low-cost, audit-friendly storage with a 7-year retention requirement and strong tenant isolation.

## 1. Cloud Storage Provider Selection & Justification

**Selected Provider: AWS S3**

### Rationale
- Native integration with AWS RDS, KMS, IAM, and monitoring services.
- Strong encryption defaults and lifecycle controls.
- Cross-region replication for disaster recovery.
- High durability and mature lifecycle management.
- Compliance-friendly for GDPR and RBI-style data handling.

### Configuration
```yaml
provider: AWS S3
primary_region: ap-south-1
backup_region: ap-south-2
eu_region: eu-west-1
versioning: enabled
encryption: AES-256 (default)
mfa_delete: enabled
public_access: BLOCKED
```

### Cost Estimate
- 1000 bills/day × 5MB = 150GB/month.
- Annual storage: 1.8TB.
- With lifecycle policy: approximately $327.60/year in storage alone.
- With egress and transfer: approximately $500-$600/year.
- Per bill cost: approximately $0.0013.
- 7-year total estimated cost: approximately $3,500.

## 2. S3 Bucket Architecture

### Bucket Naming Convention
- Bucket name: nexora-bill-captures-ap-south-1
- Region: ap-south-1
- Versioning: Enabled
- Encryption: AES-256 default
- MFA delete: Enabled
- Access logging: Enabled

### Object Naming & Directory Structure
Structure: /{workspace_id}/{year}/{month}/{capture_id}.{extension}

Examples:
- /org_123/2026/01/bill_cap_abc123xyz.jpg
- /org_456/2026/01/bill_cap_def456uvw.pdf
- /org_789/2026/02/bill_cap_ghi789rst.png

### Object Metadata
```yaml
S3 Tags:
  workspace_id: org_123
  organization_id: org_123
  created_date: 2026-01-15
  bill_type: invoice
  status: pending
  content_type: image/jpeg

S3 Object Metadata:
  - Upload timestamp
  - Uploader user ID
  - File hash (SHA256)
  - Original filename
  - Bill amount (if available)
  - Supplier GSTIN (if available)
```

## 3. Access Control & Security

### Authentication & Authorization
```yaml
Public Access: DENIED
Access Method: AWS IAM roles
Application Role: nexora-bill-capture-app
Permissions:
  - s3:GetObject
  - s3:PutObject
  - s3:DeleteObject
Workspace Isolation: Enforced by app-level policy and object tags
```

### File Upload
- Protocol: HTTPS with TLS 1.2+
- Method: Direct upload from application to S3
- Checksum: MD5 or SHA256 verification before persistence
- Encryption: TLS in transit and AES-256 at rest
- Max file size: 50MB enforced at the application and bucket boundaries

### File Download
- Method: Presigned URLs with short expiry
- Expiration: 15 minutes
- Access logging: Every download is audited
- Audit includes user ID, timestamp, IP address, object key, and status

## 4. Lifecycle Policy & Tiering Strategy

### Storage Classes Over Time
- Days 0-90: S3 Standard (hot tier)
- Days 91-365: S3 Standard-IA (warm tier)
- Days 366-2555: S3 Glacier Deep Archive (cold tier)
- After 2555 days: object deletion after the 7-year requirement window

### Lifecycle Policy Implementation
```json
{
  "Rules": [
    {
      "Id": "TransitionToIA",
      "Status": "Enabled",
      "Transitions": [
        { "Days": 90, "StorageClass": "STANDARD_IA" }
      ]
    },
    {
      "Id": "TransitionToGlacier",
      "Status": "Enabled",
      "Transitions": [
        { "Days": 366, "StorageClass": "DEEP_ARCHIVE" }
      ]
    },
    {
      "Id": "DeleteAfter7Years",
      "Status": "Enabled",
      "Expiration": { "Days": 2555 }
    }
  ]
}
```

### Cost Analysis (1000 bills/day)
- Monthly ingest: 150GB
- Annual ingest: 1.8TB
- Year 1 estimated cost: approximately $1,164
- 7-year projected total: approximately $3,500
- Cost per bill (7-year average): approximately $0.00137

## 5. Backup & Disaster Recovery

### Backup Strategy
```yaml
Primary backup: Cross-region replication
source_region: ap-south-1
destination_region: ap-south-2
replication: Asynchronous
actionable_latency: < 15 minutes
versioning: enabled
mfa_delete: enabled
```

### Disaster Recovery Procedures
- Single object deleted: restore from version history in less than 1 minute.
- Bucket unavailable: fail over to replica bucket in less than 5 minutes.
- Region-wide outage: activate secondary region and update application configuration.

### RTO and RPO
```yaml
RTO:
  single_object: < 1 minute
  bucket_outage: < 5 minutes
  region_outage: < 15 minutes

RPO:
  data_loss: < 15 minutes
  version_history: 30 versions retained
  deleted_object: recoverable within 30 days
```

## 6. Data Retention & Compliance

### Retention Schedule
- Bill data: retain for 7 years after creation.
- Audit logs: retain for 10 years in immutable storage.
- Backups: keep daily, weekly, monthly, and annual snapshots according to policy.
- Exceptions: legal hold overrides normal deletion.

### Compliance Controls
- GDPR: EU data stays in eu-west-1 with encryption and deletion controls.
- RBI: India financial data stays in ap-south-1 with auditability and secure retention.
- Archival verification: quarterly validation of lifecycle behavior and restore checks.

## 7. Monitoring & Cost Tracking

### Real-Time Metrics
```yaml
Daily Metrics:
  current_bucket_size_gb: Real-time
  daily_growth_gb: Calculated
  monthly_projection: Extrapolated
  cost_today_usd: Real-time
  cost_monthly_projection: Extrapolated
  replication_lag_minutes: Real-time
```

### Alerts and Thresholds
- Storage at 50%: yellow alert.
- Storage at 80%: orange alert.
- Storage at 100%: red alert to operations.
- Storage cost over budget: email finance.
- Replication failure: page ops.
- Lifecycle transition failure: page ops.

## 8. Access & Audit Trail

### File Access Logging
Every upload, download, delete, and restore should be logged with:
- timestamp
- actor_id
- action
- object_key
- workspace_id
- file_size
- result status
- IP address and user agent when available

### Audit Trail Preservation
- Audit logs are written to the primary audit store and retained for 10 years.
- S3 access logs are stored separately and archived according to policy.

## 9. CloudFormation Template (IaC)

```yaml
Resources:
  BillCapturesBucket:
    Type: AWS::S3::Bucket
    Properties:
      BucketName: nexora-bill-captures-ap-south-1
      VersioningConfiguration:
        Status: Enabled
      PublicAccessBlockConfiguration:
        BlockPublicAcls: true
        BlockPublicPolicy: true
        IgnorePublicAcls: true
        RestrictPublicBuckets: true
      BucketEncryption:
        ServerSideEncryptionConfiguration:
          - ServerSideEncryptionByDefault:
              SSEAlgorithm: AES256
      LoggingConfiguration:
        DestinationBucketName: nexora-bill-logs
        LogFilePrefix: s3-access-logs/
      LifecycleConfiguration:
        Rules:
          - Id: TransitionToIA
            Status: Enabled
            Transitions:
              - Days: 90
                StorageClass: STANDARD_IA
          - Id: TransitionToGlacier
            Status: Enabled
            Transitions:
              - Days: 366
                StorageClass: DEEP_ARCHIVE
          - Id: DeleteAfter7Years
            Status: Enabled
            ExpirationInDays: 2555
      ReplicationConfiguration:
        Role: !GetAtt S3ReplicationRole.Arn
        Rules:
          - Id: ReplicateToBackup
            Status: Enabled
            Priority: 1
            Destination:
              Bucket: arn:aws:s3:::nexora-bill-captures-ap-south-2
```

## 10. Runbooks & Procedures

### Recover Deleted File
1. Query audit logs for the deletion event.
2. Identify the object key and the timestamp.
3. Restore from S3 version history.
4. Verify checksum integrity.
5. Record the restoration event in audit logs.

### Emergency Failover to Backup Region
1. Detect primary region unavailability from health checks.
2. Update application configuration to use the backup region.
3. Verify data replication health.
4. Notify teams and customers of the failover status.
5. Complete failback after service restoration.

## Sign-Off Checklist

- [ ] CTO reviewed & approved
- [ ] DevOps Lead implementation feasible
- [ ] Security controls verified
- [ ] Compliance requirements met (GDPR/RBI)
- [ ] Cost projections validated
- [ ] CloudFormation template tested
- [ ] Disaster recovery procedures documented
- [ ] Monitoring & alerting configured
- [ ] Ready for ASG Phase 2 implementation
