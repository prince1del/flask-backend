# Personal To-Do — backlog

Phase 1 (shipped / in progress): diary CRUD, quick add, sections, filters, search, Hold/Done/Undo, My Day/dashboard widget, Android bottom nav + desktop/web module. Reminder **fields** saved; no push yet.

## Phase 2 — reminders & notifications

- [ ] Fire reminder at `reminder_datetime` (Android local notification)
- [ ] Notification actions: **Done** · **Remind Later** · **Open**
- [ ] Snooze options: 30 min · 1 hour · Tomorrow morning · Custom (update reminder only; no duplicate task)
- [ ] Desktop/web in-app reminder alert when due
- [ ] Optional: FCM / cross-device push later

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
