# Test Evidence Summary

## Status
- Full pytest suite: `197 passed`
- Coverage run: `69%` for `app/` package
- Current branch: `inventory-feature`
- Current commit: `dc0a0e4d713cff4726c71b4e634c1a92b225d81e`
- Evidence capture timestamp: `2026-06-29 17:36:30 +05:30`

## Verified runtime feature coverage
- API route: `GET /api/ui/dashboard-config`
- API route: `PUT /api/ui/dashboard-config`
- Runtime config persistence: `app/config/dashboard_config.json`
- Branding propagation: `GET /manifest.json` reflects `app_name`
- Dashboard module toggles: `enabled_modules` list update preserved

## Test details
- `tests/test_web_app.py::test_dashboard_config_api_returns_branding_details`
- `tests/test_web_app.py::test_dashboard_config_api_put_updates_runtime_config`

## Notes
- Existing user-facing PWA and manifest routes are exercised by tests.
- `app/routes/data.py` now supports runtime config file overrides via `DASHBOARD_CONFIG_PATH`.
