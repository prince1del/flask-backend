# SQLite → Google Drive daily backup — Phase 1

## What was built

| Piece | Location |
|-------|----------|
| Backup service | `app/services/sqlite_backup.py` |
| Cron HTTP API | `POST/GET /api/v1/ops/backup/sqlite` |
| Status | `GET /api/v1/ops/backup/sqlite/status` |
| Manual CLI | `python scripts/run_sqlite_backup.py` |
| Optional in-app scheduler | `BACKUP_SCHEDULE_ENABLED=1` |

**Naming:** `centralized_db_YYYYMMDD.sqlite3` under Drive folder `NEXORA/Backups`  
**Retention:** deletes matching files older than **30 days**  
**On failure:** ERROR log + email if `BACKUP_ALERT_EMAIL` + SMTP env set

## Render env vars (set today)

```
BACKUP_CRON_SECRET=<long random secret>
BACKUP_ALERT_EMAIL=you@houseofprizm.com
# optional — reuse Zoho SMTP already used for app mail:
BACKUP_ALERT_SMTP_HOST=smtp.zoho.in
BACKUP_ALERT_SMTP_PORT=465
BACKUP_ALERT_SMTP_USER=...
BACKUP_ALERT_SMTP_PASSWORD=...
BACKUP_ALERT_FROM=...

# optional: pin which connected user owns the Drive token
BACKUP_GDRIVE_USER_ID=<sqlite users.id>

# optional in-app daily job (prefer Render Cron instead)
# BACKUP_SCHEDULE_ENABLED=1
# BACKUP_SCHEDULE_HOUR_UTC=2
# BACKUP_SCHEDULE_MINUTE_UTC=15
```

Also ensure `DATABASE_PATH=/var/data/centralized_db.sqlite3` (already used on disk).

## Render Cron Job (recommended)

1. Dashboard → **New → Cron Job**
2. Schedule: `15 2 * * *` (02:15 UTC daily) or your preferred time
3. Command / HTTP: call the web service:

```bash
curl -X POST "https://flask-backend-wnlq.onrender.com/api/v1/ops/backup/sqlite" \
  -H "Authorization: Bearer $BACKUP_CRON_SECRET"
```

If using Render’s “HTTP request” cron type, set header `Authorization: Bearer <same secret>`.

## One-time Drive connect

Any admin must connect Google Drive once in the app (Cloud Hub / GDrive connect) so `storage_accounts` (or legacy `users.gdrive_*`) has a refresh token. Backups use that token.

## Manual verification

### Local dry-run (size + rows match)

```bash
python scripts/run_sqlite_backup.py --dry-run
```

Proof saved: `Output/backup_dry_run_verify_20260720.log`

```
Source size:  6569984 bytes | tables=117 | rows~11620
Backup size:  6569984 bytes | tables=117 | rows~11620
OK
```

### Local full upload attempt (2026-07-20)

Copy + integrity OK (size/rows matched), then Drive OAuth failed:

`invalid_grant: Token has been expired or revoked`

Alert path fired (ERROR log; email skipped until `BACKUP_ALERT_EMAIL` set).  
Log: `Output/backup_manual_test_20260720.log`

**Action required on live:** reconnect Google Drive in Cloud Hub on Render, then run the curl below once for Drive proof (screenshot of `NEXORA/Backups/centralized_db_YYYYMMDD.sqlite3`).

### Production (Drive upload)

```bash
curl -sS -X POST "https://flask-backend-wnlq.onrender.com/api/v1/ops/backup/sqlite" \
  -H "Authorization: Bearer $BACKUP_CRON_SECRET" | tee backup_run.json
```

Check response for `data.drive.webViewLink`, `data.source` vs `data.backup` size/rows.  
Open Drive → **NEXORA → Backups → centralized_db_YYYYMMDD.sqlite3**.

### Status

```bash
curl -sS "https://flask-backend-wnlq.onrender.com/api/v1/ops/backup/sqlite/status" \
  -H "Authorization: Bearer $BACKUP_CRON_SECRET"
```
