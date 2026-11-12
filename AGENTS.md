# Global single backend (App / Desktop / Web)

NEXORA clients **must** share one Flask API and one workspace database.

- Do not invent `/api/v1/bd/...` or mobile-only persistence for business data.
- Prefer existing routes under `app/routes/` that `app/static/app.js` already calls.
- New features: ship Flask route → wire Android **and** desktop/web workspace.
- Production cloud DB = Render when clients use that host.

See `.cursor/rules/global-single-backend.mdc` for the shared endpoint map.

**App map:** `docs/NEXORA_APP_MAP.md` — every module, its routes, code, tables and
phone/web screens. Read it before changing a feature; update it when you add one.
