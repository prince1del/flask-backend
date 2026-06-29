# Runtime Config Change Log

## Change event: Disable non-core modules
- Timestamp: `2026-06-29 17:36:30 +05:30`
- Action: Runtime dashboard config updated to remove `file_library` and `party_match`
- API request:
  - `PUT /api/ui/dashboard-config`
  - body:
    ```json
    {
      "enabled_modules": ["dashboard", "verification", "analytics", "masters", "sales", "inventory", "reports"],
      "app_name": "NEXORA ENTERPRISE"
    }
    ```
- API response:
  - `200 OK`
  - `app_name`: `NEXORA ENTERPRISE`
  - `enabled_modules`: `["dashboard", "verification", "analytics", "masters", "sales", "inventory", "reports"]`

## Change event: Runtime branding name changed
- Timestamp: `2026-06-29 17:36:30 +05:30`
- Effect: `GET /manifest.json` returned `name` = `NEXORA ENTERPRISE`
- Note: Runtime branding is persisted in `app/config/dashboard_config.json`
