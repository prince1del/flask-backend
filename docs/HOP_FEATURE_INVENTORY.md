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
| Paper size (A4 / A5 / thermal 2"/3") | ❌ |
| Show/hide: amount in words, signature, bank details, terms + footer text | ✅ Settings (HSN/tax breakup columns ❌) |
| Copies (Original / Duplicate / Triplicate) | ❌ |
| Header/footer text, page margins | ❌ |

### Messages & reminders
| Setting | State |
|---|---|
| WhatsApp / SMS after saving a bill | ❌ |
| Automatic payment reminders | 🟡 (overdue list + WhatsApp link, Cursor) |
| Message templates | ❌ |

### Party
| Setting | State |
|---|---|
| Party groups (list on server) | 🟡 list in Settings; party form still reads browser list |
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
| Other income | ❌ |
| Bank reconciliation | ❌ |
| 32 reports + AI insights | ✅ |
| GSTR-1/2/3B/9, HSN | ✅ |
| Excel import (items, parties), bulk update | ❌ |
| Tally export | ✅ (Cursor) |
| Recycle bin | ✅ |
| User roles & permissions for staff, audit trail | ❌ |
| Online store / catalogue sharing | 🟡 (catalogue exists) |
| Payment link / UPI QR on invoice | ❌ |
| Share invoice on WhatsApp | ❌ |
| Thermal printing | ❌ |
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
