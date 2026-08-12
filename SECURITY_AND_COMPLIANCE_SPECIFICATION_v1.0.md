# Security and Compliance Specification v1.0

## Executive Summary

This specification defines the security architecture, access controls, compliance posture, audit requirements, and incident response obligations for the Smart Bill Capture Engine during Phase 2. The principal rule is that one workspace must never be able to access another workspace's bill data under any circumstance.

## 1. Data Security Architecture

### 1.1 Encryption at Rest

#### Database Encryption
- Technology: Transparent Data Encryption (TDE) or equivalent database-native encryption.
- Algorithm: AES-256.
- Scope: All bill capture, audit, and workspace configuration tables.
- Key storage: AWS KMS (managed or customer-managed key).
- Rotation: Annual or customer-initiated rotation.
- Performance impact: Expected to remain below 5% for typical workloads.
- Compliance alignment: GDPR, SOC 2, ISO 27001.

#### File Storage Encryption
- Technology: Server-side encryption on object storage.
- Algorithm: AES-256.
- Scope: All uploaded invoices, images, and PDFs.
- Default key source: AWS-managed key with KMS enforcement.
- Customer option: Customer-managed key for enterprise tenants.
- Performance impact: Negligible.

#### Backup Encryption
- Technology: Encrypted backups inherited from the primary storage layer.
- Scope: Database snapshots, object store backups, and disaster recovery copies.
- Recovery requirement: Only authorized production identities may restore encrypted backups.

### 1.2 Encryption in Transit

- Protocol: TLS 1.2 or higher only.
- Certificate: Valid public CA-issued certificate, not self-signed.
- HTTP: All HTTP traffic must redirect to HTTPS with a 301 response.
- HSTS: Enabled with max-age 31536000.
- File upload/download: HTTPS only with presigned URLs valid for short expiry.
- Internal service communication: TLS enforced for all service-to-service traffic.

### 1.3 Key Management

- Primary keys: AWS KMS. 
- Secrets: AWS Secrets Manager for database credentials, API keys, and tokens.
- JWT signing key: Environment variable managed in the deployment platform.
- Rotation policy: Database credentials quarterly; API keys annually; JWT secret quarterly.
- Auditability: CloudTrail must record key usage and secret access events.

## 2. Access Control & Authentication

### 2.1 Authentication Strategy

- Token type: JWT.
- Claims: user_id, workspace_id, roles, permissions, exp, iat.
- Signing algorithm: HS256.
- Token lifetime: 1 hour access token, 30 day refresh token.
- Service authentication: API keys for internal provider callbacks, stored in Secrets Manager.

### 2.2 Multi-Tenant Workspace Isolation

- Every query must enforce workspace_id filtering.
- The JWT must carry the authenticated workspace scope.
- Access to resources from a mismatched workspace must return 403 Forbidden.
- Database-level isolation should be enforced in application code and, where supported, through row-level security policies.

### 2.3 Role-Based Access Control (RBAC)

#### Roles
- Admin: Can upload, approve, administer users, view logs, and export data.
- Approver: Can approve or reject bills and view audit trails within the workspace.
- Uploader: Can upload and view own submissions only.
- Viewer: Read-only access to authorized workspace documents.

#### Minimum Permission Rules
- Upload bill: Admin and Uploader.
- Approve/reject bill: Admin and Approver.
- Export data: Admin only.
- Audit log access: Admin only or approved compliance users.
- Workspace management: Admin only.

## 3. Data Privacy & Compliance

### 3.1 GDPR Compliance (EU Users)

- Data residency: EU customer data remains in EU-only storage regions.
- DPA: Signed with cloud processors and reviewed annually.
- Right to deletion: Completed within 30 days of verified request.
- Right to access: DSAR responses within 30 days.
- Retention: Output artifacts and audit data retained according to the approved policy.
- Breach reporting: Notify authorities and affected users within 72 hours when required.

### 3.2 RBI Compliance (India Financial Data)

- Data localization: Sensitive financial data stays in India-only regions.
- Backup and primary data storage: Must remain in ap-south-1 unless customer-specific exceptions are approved.
- Audit access: All access events must be loggable for regulatory review.
- Cross-border transfer: Prohibited without explicit customer consent and documented controls.

### 3.3 Data Retention Policy

- Active bill data: 90 days in hot storage.
- Warm archive: 365 days in lower-cost warm storage.
- Compliance retention: Up to 7 years for financial records as needed by policy.
- Audit logs: 10 years immutable retention.
- Backup retention: 30 days daily, 1 year monthly, 10 years annual archives.

## 4. Audit & Logging

### 4.1 Audit Trail Requirements

The system must record the following events:
- Login, logout, token refresh, failed auth, and privilege denial.
- Bill upload, OCR execution, validation result, approval, correction, and download.
- Provider fallback or failover event.
- Security event such as access denied or suspicious activity.

Each audit entry must include:
- workspace_id
- actor_id
- actor_type
- event_type
- resource_type
- resource_id
- timestamp
- ip_address
- user_agent
- changes or meaningful result details

### 4.2 Immutable Logging

- Audit logs must be write-once and append-only.
- Update and delete privileges for audit tables must be restricted.
- Audit exports must be archived to immutable storage for long-term retention.

### 4.3 Log Retention

- Operational logs: 90 days in hot storage.
- Audit logs: 10 years in immutable archive storage.
- Security incidents: Retained for the full legal and compliance period.

## 5. Vulnerability Management

### 5.1 OWASP Top 10 Protection

The system must address the following categories:
- Injection attacks: Parameterized queries only.
- Broken authentication: Short-lived JWTs and secure token validation.
- Sensitive data exposure: TLS 1.2+, encryption at rest, secret management.
- Broken access control: Workspace filtering and RBAC checks.
- Security misconfiguration: Strict headers, least-privilege deploy roles.
- XSS: Output encoding and input validation on all user-managed content.
- Insecure deserialization: JSON-only processing.
- Known vulnerable components: Dependency scanning and patch management.
- Insufficient logging: Audit trails for all sensitive operations.

### 5.2 Dependency and Code Security

- Dependency scanning: Run daily vulnerability scans on all Python packages.
- Static analysis: Use Bandit in CI for secrets, injection, and weak crypto checks.
- Pull request policy: Security review required for changes touching authentication, storage, or OCR workflows.
- Patch SLA: Critical vulnerabilities patched within 48 hours.

### 5.3 Penetration Testing Schedule

- Internal tests: Quarterly.
- Third-party tests: Semi-annually.
- Full audit: Annually.
- Scope includes API, authentication, upload paths, and workspace isolation.

## 6. Incident Response

### 6.1 Security Breach Protocol

1. Detect the incident via dashboards, alerts, or user reports.
2. Contain the incident by revoking credentials, isolating systems, and blocking suspicious activity.
3. Preserve evidence via logs and snapshots.
4. Notify internal leadership and the on-call security contact.
5. Assess impact and determine regulatory obligations.
6. Restore services with validated rollback-safe patches.
7. Conduct a post-incident review and update controls.

### 6.2 Notification Requirements

- GDPR: Notify affected users and authorities within 72 hours when required.
- RBI: Notify affected stakeholders promptly when finance-related data is impacted.
- Customer-facing incident notice: Include impact, mitigation, and next steps.

## 7. Compliance Certifications

### 7.1 ISO 27001 Roadmap
- Gap analysis in the first quarter.
- Document controls and policies.
- Internal audit in the first six months.
- External certification path over 9-12 months.

### 7.2 SOC 2 Type II Roadmap
- Define evidence collection for security, availability, and confidentiality.
- Operate controls for a minimum audit period.
- Engage an external auditor for certification review.

### 7.3 GDPR Checklist
- DPA in place.
- Privacy notice published.
- Deletion workflow implemented.
- Export workflow implemented.
- Retention policy documented.
- Breach response procedure approved.

### 7.4 RBI Checklist
- Data residency in India enforced.
- No unchecked cross-border transfer.
- Access logs retained and reviewable.
- Incident procedures documented.
- Security audit evidence available.

## 8. Security Testing Plan

### 8.1 Test Matrix
- Unit tests: workspace isolation, JWT validation, input sanitization.
- Integration tests: auth enforcement, audit log creation, permission denial.
- Penetration tests: injection, broken access control, token replay, path traversal.
- Load tests: concurrent misuse and DDoS-style bursts while verifying access control.

### 8.2 Expected Security Controls
- No workspace can access another's bill data.
- Invalid or expired tokens are rejected.
- Untrusted file uploads are sanitized, validated, and size-limited.
- Audit logs are immutable and reviewable.
- Encryption is enforced in transit and at rest.

## Appendix: Security Metrics & Monitoring

- Failed authentication rate.
- Access denied counts by workspace.
- Encryption status per storage layer.
- Audit log write success rate.
- Pending vulnerability count by severity.
- Provider credential rotation status.
- Incident count and recovery time.

## Sign-Off

- [ ] CTO Review & Approval
- [ ] Security Engineer Validation
- [ ] Compliance Officer Approval
- [ ] Legal Review (if needed)
- [ ] Ready for ASG Phase 2
