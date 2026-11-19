# Partners and team access — plan

Owner's note (Business UI → Notes, 19-11-2026): a business can have 2 or 3 partners. They should share the business
data, but notes, personal settings and anything that is not a business activity should stay private. Their teams
(HR, accountant, field managers, CFO, CEO …) should each see only the data that belongs to their job.

## What exists today

- One firm = one workspace database. Every login of the firm reads the same books (phone, web and desktop use the
  same `/api/v1/hop` routes).
- Owner + staff. Staff get `none` / `view` / `edit` for each of 8 areas (sales, purchase, parties, items, money,
  reports, crm, settings), with presets (Salesperson, Accountant, Store keeper, View only). The server checks every request.
- Owner-only actions: permissions, backups / restore, wipe data, government settings.

## The plan

1. **Partner role (co-owner).** A partner sees and edits all business data like the owner. Owner-only actions
   (wipe, restore, permissions) need the owner, or one partner's request plus a second partner's approval. Every
   change goes into the audit trail with the partner's name.
2. **Private and shared data.**
   - Shared: bills, payments, parties, items, stock, cash and bank, reports, GST, targets, dispatch, samples, complaints.
   - Private to each login: Notes (each note has an owner; a "Share with team" switch makes it shared), app theme,
     language, app lock, Home cards, Ask Nexora chat history, held bills.
   - Firm settings (bill numbers, GST, print): partners can change them, with audit.
3. **Role templates** on top of the areas:
   - CEO: views everything and approves.
   - CFO: money, reports, GST and loans (edit); sales and purchase (view).
   - Accountant: as today.
   - HR: a new *Staff & HR* area (team duty, attendance, salary, staff list). No sales or purchase money.
   - Field manager: sales, parties, dispatch, samples, complaints and visits, for their own team only.
   - Salesperson: only their own parties and bills.
   - Store keeper: as today.
4. **Row scope: all / my team / mine.**
   - Each bill, party and visit stores who made it and who it belongs to (salesperson / agent).
   - Each staff member has a "reports to" person, so a manager sees their team's data.
5. **Hidden fields.** Cost price, profit and margin are hidden from sales roles. Salary is visible only to HR, the
   owner and partners.
6. **One check everywhere.**
   - The server enforces every rule, for phone and web alike, and menus the login cannot use are hidden.
   - Ask Nexora answers only from data the login is allowed to see.
7. **Approvals.** Some actions go to a manager or partner to approve: a discount above a limit, deleting a bill,
   or going over a party's credit limit.
8. **AI.**
   - Ask Nexora answers per role, for example "my team's sales this month".
   - NEXORA Guard alerts go to the right role (overdue bills to the accountant, short stock to the store keeper).
   - AI suggests a role from the job title when a staff member is added.

## Build order (batches)

| Batch | What it builds |
|---|---|
| A | Partner role; private notes, personal settings and chat |
| B | Role templates (CEO, CFO, HR, Field manager) and the new HR area |
| C | "Reports to" tree; scope mine / my team / all on bills, parties and visits |
| D | Hidden fields: cost, profit, salary |
| E | Approvals |
| F | Web parity, tests and help text |

## Questions for the owner

1. Is a partner equal to the owner, including changing permissions?
2. Should shared notes be visible to the partners only, or to the whole team?
3. Which roles come first?
