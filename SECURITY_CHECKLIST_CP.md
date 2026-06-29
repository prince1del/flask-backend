# Security Checklist for Critical Path

## Authentication and API protection
- [x] JWT auth enforced on protected API endpoints under `app/routes/data.py`
- [x] Runtime dashboard config endpoints require auth: `/api/ui/dashboard-config`
- [ ] Validate JWT key strength in production (current test env uses weak `SECRET_KEY`)

## Config and secrets
- [x] No hardcoded production secrets in repository code
- [x] `.env.example` contains placeholder values only
- [x] Database path managed via `DATABASE_PATH` config in Flask app
- [x] Firebase credentials path uses `FIREBASE_CREDENTIALS_JSON` environment variable

## Runtime config safety
- [x] `dashboard_config.json` is JSON-backed and persisted under `app/config`
- [x] Runtime config updates are validated and limited to allowed fields only
- [x] `enabled_modules` only accepts list values in `/api/ui/dashboard-config`

## Data handling and output
- [x] JSON endpoints return `application/json` content type
- [x] Manifest route `GET /manifest.json` draws branding values from runtime config
- [x] Static PWA assets served explicitly by `app/routes/data.py`

## Observed issues to fix before production
- [ ] Increase JWT secret entropy to at least 32 bytes for HS256
- [ ] Add TLS/HTTPS enforcement for production deployment
- [ ] Add explicit request validation for all runtime config fields beyond `enabled_modules`
