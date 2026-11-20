# Global single backend (App / Desktop / Web)

NEXORA clients **must** share one Flask API and one workspace database.

- Do not invent `/api/v1/bd/...` or mobile-only persistence for business data.
- Prefer existing routes under `app/routes/` that `app/static/app.js` already calls.
- New features: ship Flask route → wire Android **and** desktop/web workspace.
- Production = AWS Lightsail (see `deploy/README.md`); Render is no longer used.

See `.cursor/rules/global-single-backend.mdc` for the shared endpoint map.

**App map:** `docs/NEXORA_APP_MAP.md` — every module, its routes, code, tables and
phone/web screens. Read it before changing a feature; update it when you add one.

# NEXORA is fully AI-driven (owner's rule, 2026-11-15)

Every module and every new feature ships **with** AI help — never as a later add-on:
- Reading documents/photos into forms, smart suggestions/matching, warnings that
  catch mistakes early, "AI insights" on reports, and an **Ask Nexora tool** for
  every data area (Hinglish questions must work).
- AI never saves data by itself — the user checks and saves.
- Keep it cheap: one capped Gemini call, inside the daily AI limit, usage logged.
- Before calling a feature done, fill its row in the AI checklist
  (`docs/HOP_VYAPAR_PARITY_PROGRESS.md` for HoP).

# Live data safety (owner's hard rule, 2026-11-20)

- **Never write to the production database from a script** (no UPDATE / INSERT / DELETE / DROP / ALTER through
  sqlite3, psql or `python -c`) — not even small clean-ups. Read-only access only (`sqlite3 -readonly`, `?mode=ro`).
- Data changes happen only through the app (the owner) or code shipped in the app. Fix code, not data.
- Each assistant uses its own SSH key (Cursor: `~/.ssh/nexora_server_cursor`), so the server log shows who did what.
- Ask the owner before deploying or changing server config. Read `CLAUDE.md` first; update it with every change.
- Full rules: `.cursor/rules/live-server-safety.mdc`.
