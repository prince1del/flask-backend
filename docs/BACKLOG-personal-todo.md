# Personal To-Do — backlog

## Phase 1 — shipped

Diary CRUD, quick add, sections, filters, search, Hold/Done/Undo, My Day/dashboard widget, Android bottom nav + desktop/web module.

## Phase 2 — reminders (in progress / shipping)

- [x] Fire reminder at `reminder_datetime` (Android local notification)
- [x] Notification actions: **Done** · **Remind Later** · **Open**
- [x] Snooze options: 30 min · 1 hour · Tomorrow morning (+ custom via API/ISO)
- [x] Desktop/web in-app reminder alert when due (`/due-reminders` poll)
- [ ] Optional: FCM / cross-device push later
- [ ] Richer custom date-time picker UI on Android (ISO field works today)

## Phase 3 — nice-to-haves

- [ ] Optional CRM party link (`linked_party_id` / distributor / retailer)
- [ ] Recurring tasks
- [ ] Share / assign to another Nexora user
- [ ] Export completed history (date range)

## Locked product decisions

- Bottom nav (Android) for fast access; desktop sidebar nav
- App + Desktop + Web = one `/api/v1/personal-todos` API
- Categories: English only
- Status: Pending | Hold | Done only
- Not CRM workflow; personal work diary
- Snooze updates `reminder_datetime` only — never duplicates the task
