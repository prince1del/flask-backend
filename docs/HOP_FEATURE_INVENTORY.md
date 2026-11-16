# HoP — full feature & settings inventory (vs desktop billing software)

Every feature and setting of a full desktop billing/accounting app, and where NEXORA HoP stands.
✅ done (server + web + phone) · 🟡 partly · ❌ missing. Update this file whenever a row changes.
Rule: no setting without real behaviour behind it; every setting shows on web **and** phone.

## A. Settings

### General
| Setting | State |
|---|---|
| Business name, GSTIN, address, logo, signature, bank, terms (Company profile) | ✅ |
| Financial year + year close / lock | ✅ (Cursor, Phase 3) |
| Passcode / app lock | ❌ |
| Multiple firms (companies) in one login | ❌ |
| Backup / restore by the user | ❌ (server backups only) |
| Date format, decimal places (qty, amount) | ❌ |
| Currency symbol | ❌ (₹ fixed) |

### Transaction
| Setting | State |
|---|---|
| Document number prefix per type (Invoice, Estimate, Order, Challan, Proforma, Receipt) | ✅ Settings |
| Auto round-off of total (nearest ₹1) | ✅ Settings |
| Rates include GST (tax-inclusive entry) | ✅ Settings |
| Default payment terms / due days | ✅ Settings (party credit days first) |
| Show last sale / purchase price while billing | ✅ (rate suggestion) |
| Cash sale by default (fully received) | ✅ Settings |
| Additional charges on bills (packing, freight…) | ✅ any number, each with own GST (no stock) |
| Discount on bill + per item | ✅ |
| Free quantity, item description on bill | ✅ (free qty moves stock, not charged) |
| Transaction time on bills | ✅ |
| PO number/date on sale invoice | ✅ (invoice, sale order, challan) |
| Estimate / Sale order / Challan / Proforma / Purchase order on/off | ✅ (all available) |
| Link payment to invoices | ✅ |
| Warn on duplicate bill number | ✅ |
| Allow / block sale when stock is short | ✅ Settings (warn / block, enforced on server) |

### Taxes & GST
| Setting | State |
|---|---|
| GST on/off, HSN/SAC on items | ✅ |
| Place of supply, reverse charge | ✅ (Night 2) |
| Composition scheme | ✅ Settings (Bill of Supply, no GST on sales) |
| Additional cess | ❌ |
| TCS on sales / TDS on purchases | ✅ TCS on sale invoice (liability) + purchase bill (to claim); TDS on commission only |
| E-Invoice / E-Way Bill | 🟡 (JSON files; direct portal API needs owner's GSP account) |

### Print
| Setting | State |
|---|---|
| Print theme / colour | ✅ (Cursor) |
| Paper size (A4 / A5 / thermal 2"/3") | ✅ Settings → Print (web prints all; phone PDF A4/A5) |
| Show/hide: amount in words, signature, bank details, terms + footer text | ✅ Settings (HSN/tax breakup columns ❌) |
| Copies (Original / Duplicate / Triplicate) | ✅ Settings → Print (web print + phone PDF) |
| Header/footer text, page margins | ❌ |

### Messages & reminders
| Setting | State |
|---|---|
| WhatsApp / SMS after saving a bill | ✅ WhatsApp bill + reminder buttons (opens WhatsApp with your message; SMS ❌) |
| Automatic payment reminders | 🟡 (overdue list + WhatsApp link, Cursor) |
| Message templates | ✅ Settings → Sharing (bill + reminder, with {party} {amount} {due} {upi_link}…) |

### Party
| Setting | State |
|---|---|
| Party groups (list on server) | ✅ Settings list; web party form reads it and adds new groups to it |
| Shipping address | ✅ |
| Credit limit warn / block | ✅ (block enforced on server) |
| Party custom fields | ✅ (additional fields) |
| Party-wise item rates | ✅ (Cursor) |

### Item
| Setting | State |
|---|---|
| Product / service items | ✅ (service items never move stock) |
| Stock maintenance on/off | ✅ (stock moves with bills) |
| Item category, units | ✅ (Cursor) |
| Unit conversion (1 box = 10 pcs) | ✅ 2nd unit on the item; a bill line in BOX moves qty × factor |
| MRP, wholesale price, price lists | ✅ MRP + wholesale (auto at min qty); party-wise rates = price list |
| Barcode | ✅ field + lookup API (phone camera scanner ❌) |
| Batch / expiry, serial numbers | ❌ |
| Godowns / multiple stores | ❌ |
| Item custom fields | ✅ (name: value list) |

## B. Features (outside settings)
| Feature | State |
|---|---|
| Sales: invoice, estimate, proforma, order, challan, return, payment-in, convert | ✅ |
| Purchase: bill, order, return, payment-out, expense | ✅ |
| Cash & Bank, cheques, transfers, loans, fixed assets | ✅ |
| Other income | ✅ (own categories; money in; shown in P&L; Other Income report) |
| Bank reconciliation | ✅ tick entries (cleared date), statement CSV auto-match + hints for missing lines |
| 32 reports + AI insights | ✅ |
| GSTR-1/2/3B/9, HSN | ✅ |
| Excel import (items, parties), bulk update | ✅ .xlsx / .csv, preview with checks, bulk update of items (phone + web) |
| Tally export | ✅ (Cursor) |
| Recycle bin | ✅ |
| User roles & permissions for staff, audit trail | 🟡 audit trail ✅ (phone + web + Ask Nexora); staff roles per HoP screen ❌ |
| Online store / catalogue sharing | 🟡 (catalogue exists) |
| Payment link / UPI QR on invoice | 🟡 UPI payment link on bill + WhatsApp; QR picture needs a QR library (owner OK) |
| Share invoice on WhatsApp | ✅ (text + UPI link; PDF attach is by the phone share sheet) |
| Thermal printing | 🟡 thermal paper layout from web print; Bluetooth printer from phone ❌ |
| Barcode scanning on phone | ❌ |
| Offline mode | ❌ |
| AI: bill reading, insights, Guard, Ask Nexora | ✅ |

## Order of work
1. **Settings engine** — one server list of settings, one screen on web + phone (built from the same
   list), each setting wired to real behaviour. First batch: prefixes, auto round-off, tax-inclusive
   rates, default due days, cash sale default, block/warn on short stock, print show/hide options,
   composition scheme flag, party groups on server.
2. Additional charges, MRP/wholesale/price lists, unit conversion, service items.
3. Other income, bank reconciliation, payment link/UPI QR, WhatsApp share.
4. Barcode (+ phone scanner), batch/expiry/serial, godowns.
5. Excel import / bulk update, staff roles + audit trail, multiple firms, passcode, backup/restore.

## Changelog
- 2026-11-16: Settings system (server + web + phone, same list): prefixes, round-off, GST-inclusive
  rates, due days, cash sale, short stock, composition, print options, party groups, credit limit.
  Place of supply + reverse charge. Guard: HoP and BD inboxes separate.
- 2026-11-16: Bill extras — additional charges (own GST), free qty, PO no/date, bill time, TCS on
  sale invoices and purchase bills (books: TCS payable / TCS paid). Phone form + preview + PDF, web
  form + preview. AI bill reader also reads charges, free qty and TCS.
- 2026-11-16: Items — service items, MRP, wholesale price (suggested at the minimum qty), 2nd unit
  with conversion, barcode (+ lookup), custom fields, item checks (above MRP, wholesale below cost…).
  Phone + web item forms; unit + free qty on phone bill lines. Ask Nexora: "items" topic.
- 2026-11-16: Other Income (rent received, interest, scrap sale…) — income categories, money into
  Cash/Bank, P&L "other income", report, Ask Nexora topic. Web menu + phone drawer.
- 2026-11-16: Bank reconciliation — cleared date per entry (kept when a bill is edited), book vs bank
  balance, statement CSV upload auto-matches (same amount, ±5 days), hints for bank charges /
  interest. Phone: tap a bank account. Web: Cash & Bank → account → Reconcile. Ask Nexora shows it.
- 2026-11-16: Sharing — UPI ID in Settings, "Pay by UPI" link on sale invoices with money due,
  WhatsApp bill / payment reminder buttons (phone + web) using the owner's message templates.
- 2026-11-16: Print — paper size (A4, A5, thermal 80/58 mm) and copies (Original / Duplicate /
  Triplicate) in Settings; web print and phone PDF follow them.
- 2026-11-16: Import from Excel / CSV — items and parties, columns matched by name, every row checked
  (missing name, bad GSTIN / mobile, odd GST rate, duplicates); bulk update of existing items.
  Party groups now come from Settings on web and phone.
- 2026-11-16: Audit trail — every change on phone or web is logged (who, when, old vs new amount for
  bills); screen under Settings on both; Ask Nexora "kisne bill badla?".
