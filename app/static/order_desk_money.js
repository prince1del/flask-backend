/** Order Desk money tabs — Pending SO, Distributor Hisaab, SO payment mails.
 *  Same server routes as the phone app: /order-fulfillment/pending-so,
 *  /distributor-ledger and /so-mail/*. Loaded after app.js (fetchWithAuth, foEscapeText). */

function odmInr(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return (n < 0 ? '−₹' : '₹') + Math.round(Math.abs(n)).toLocaleString('en-IN');
}

function odmQty(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return Math.round(n).toLocaleString('en-IN');
}

function odmEsc(v) {
  return foEscapeText(v);
}

async function odmJson(url, options) {
  const response = await fetchWithAuth(url, options);
  let data = {};
  try {
    data = await response.json();
  } catch (_) {
    data = {};
  }
  if (!response.ok || data.success === false) {
    const err = data.error;
    throw new Error((err && err.message) || (typeof err === 'string' ? err : '') || `Request failed (${response.status})`);
  }
  return data.data || {};
}

/* ---------------------------------------------------------------- Season */
// One season choice for these tabs (same idea as the phone's season bar).
// '' = all seasons. Remembered in this browser.

let odmSeason = '';
try { odmSeason = localStorage.getItem('nexora_season') || ''; } catch (_) { odmSeason = ''; }
let _odmSeasonsLoaded = false;

function odmSeasonKey(v) {
  const m = String(v || '').match(/\b(AW|SS)\s*[-_/ ]?\s*'?(\d{2})\b/i);
  return m ? (m[1].toUpperCase() + m[2]) : String(v || '').trim().toUpperCase();
}

function odmSeasonMatches(v) {
  if (!odmSeason) return true;
  if (!String(v || '').trim()) return true; // unknown season stays visible
  return odmSeasonKey(v) === odmSeasonKey(odmSeason);
}

async function odmLoadSeasons() {
  if (_odmSeasonsLoaded) return;
  try {
    const data = await odmJson('/api/v1/seasons');
    const seasons = data.seasons || [];
    document.querySelectorAll('.of-odm-season').forEach((sel) => {
      sel.innerHTML = '<option value="">All seasons</option>' + seasons.map((s) => {
        const due = s.to_collect > 1 ? ` · ${odmInr(s.to_collect)} due` : (s.open ? ' · open' : ' ✓');
        return `<option value="${odmEsc(s.season)}">${odmEsc(s.season)}${odmEsc(due)}</option>`;
      }).join('');
      sel.value = odmSeason;
    });
    _odmSeasonsLoaded = true;
  } catch (_) { /* picker stays on "All seasons" */ }
}

function odmSetSeason(value) {
  odmSeason = value || '';
  try { localStorage.setItem('nexora_season', odmSeason); } catch (_) { /* ignore */ }
  document.querySelectorAll('.of-odm-season').forEach((sel) => { sel.value = odmSeason; });
  renderPendingSo();
  loadDistributorHisaab();
}

/* ------------------------------------------------------------ Pending SO */

let _odmPendingItems = [];

async function loadPendingSo() {
  const board = document.getElementById('of-pending-board');
  const summary = document.getElementById('of-pending-summary');
  if (board) board.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;">Loading…</p>';
  odmLoadSeasons();
  try {
    const data = await odmJson('/api/v1/order-fulfillment/pending-so');
    _odmPendingItems = data.items || [];
    if (summary) {
      summary.textContent = `${data.so_count || 0} SO · Pending ${odmInr(data.pending_net_total)}`;
    }
    renderPendingSo();
  } catch (error) {
    if (board) board.innerHTML = `<p class="nx-text-dim" style="padding:0.75rem;">${odmEsc(error.message)}</p>`;
    if (summary) summary.textContent = '';
  }
}

function renderPendingSo() {
  const board = document.getElementById('of-pending-board');
  if (!board) return;
  const q = (document.getElementById('of-pending-search')?.value || '').trim().toLowerCase();
  const items = _odmPendingItems.filter((i) => odmSeasonMatches(i.season) && (!q
    || String(i.distributor_name || '').toLowerCase().includes(q)
    || String(i.so_number || '').toLowerCase().includes(q)));
  if (!items.length) {
    board.innerHTML = `<p class="nx-text-dim" style="padding:0.75rem;">${_odmPendingItems.length
      ? 'No SO matches the search.'
      : 'No pending SO. Every matched SO is fully billed by a CI.'}</p>`;
    return;
  }
  const groups = new Map();
  items.forEach((i) => {
    const key = `${i.distributor_id ?? ''}|${i.distributor_name || ''}`;
    if (!groups.has(key)) groups.set(key, { name: i.distributor_name || 'Distributor', rows: [] });
    groups.get(key).rows.push(i);
  });
  board.innerHTML = [...groups.values()].map((g) => {
    const total = g.rows.reduce((s, r) => s + Number(r.pending_net || 0), 0);
    const qty = g.rows.reduce((s, r) => s + Number(r.pending_qty || 0), 0);
    const sos = g.rows.map((r) => {
      const articles = (r.articles || []).map((a) => `
        <tr>
          <td>${odmEsc(a.name)}</td>
          <td class="of-pay-num">${odmQty(a.so_qty)}</td>
          <td class="of-pay-num">${odmQty(a.pending_qty)}</td>
          <td class="of-pay-num">${odmInr(a.pending_net)}</td>
        </tr>`).join('');
      const partial = r.status === 'partial';
      return `
        <details class="of-pay-order">
          <summary class="of-pay-order-sum">
            <span class="of-pay-order-ref">${odmEsc(r.so_number)}</span>
            <span class="of-pay-pill ${partial ? 'is-partial' : 'is-due'}">${partial ? 'PART BILLED' : 'OPEN'}</span>
            <span class="of-pay-meta">${odmEsc([r.category, r.season].filter(Boolean).join(' · '))}</span>
            <span class="of-pay-meta">Pending ${odmQty(r.pending_qty)} pcs · ${odmInr(r.pending_net)}</span>
            ${partial ? `<span class="of-pay-meta">CI billed ${odmQty(r.ci_qty)} pcs · ${odmInr(r.ci_amount)} of ${odmInr(r.so_net)}</span>` : ''}
          </summary>
          <div class="of-pay-order-body">
            ${articles ? `
            <table class="data-table of-pay-table">
              <thead><tr><th>Article</th><th>SO qty</th><th>Pending qty</th><th>Pending value</th></tr></thead>
              <tbody>${articles}</tbody>
            </table>
            ${r.articles_prorated ? '<p class="of-pane-help">Partly billed: article pending is shared out in proportion to what is still unbilled.</p>' : ''}`
            : '<p class="of-pane-help">No article lines saved for this SO.</p>'}
          </div>
        </details>`;
    }).join('');
    return `
      <section class="of-pay-dist">
        <header class="of-pay-dist-head">
          <strong>${odmEsc(g.name)}</strong>
          <span class="of-pay-meta">${g.rows.length} SO · ${odmQty(qty)} pcs · Pending ${odmInr(total)}</span>
        </header>
        ${sos}
      </section>`;
  }).join('');
}

/* ------------------------------------------------- Edit a payment entry */

const ODM_PAYMENT_EDIT = {
  deposit: {
    url: (id) => `/api/v1/distributor-payments/deposits/${id}`,
    reload: () => loadDistributorHisaab(),
  },
  collection: {
    url: (id) => `/api/v1/order-fulfillment/payment-collection/entries/${id}`,
    reload: () => loadPaymentCollection(),
  },
};

function odmPaymentEditButton(kind, p) {
  return `<button type="button" class="nx-btn of-pay-edit" data-kind="${kind}" data-id="${odmEsc(p.id)}"
    data-amount="${odmEsc(p.amount)}" data-date="${odmEsc(p.payment_date || '')}" data-note="${odmEsc(p.note || '')}"
    onclick="odmStartPaymentEdit(this)">Edit</button>`;
}

function odmStartPaymentEdit(btn) {
  const row = btn.closest('tr');
  if (!row || row.hidden) return;
  const editRow = document.createElement('tr');
  editRow.innerHTML = `<td colspan="${row.children.length}">
    <form class="of-pay-add-form of-pay-edit-form">
      <label>Date <input type="date" name="payment_date" required value="${odmEsc(btn.dataset.date.slice(0, 10))}" /></label>
      <label>Amount <input type="number" name="amount" min="1" step="0.01" required value="${odmEsc(btn.dataset.amount)}" /></label>
      <label>Note <input type="text" name="note" value="${odmEsc(btn.dataset.note)}" /></label>
      <button type="submit" class="nx-btn nx-btn-primary">Save</button>
      <button type="button" class="nx-btn" data-cancel>Cancel</button>
    </form></td>`;
  row.hidden = true;
  row.after(editRow);
  const form = editRow.querySelector('form');
  form.querySelector('[data-cancel]').addEventListener('click', () => {
    editRow.remove();
    row.hidden = false;
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    odmSavePaymentEdit(form, btn.dataset.kind, btn.dataset.id, Number(btn.dataset.amount));
  });
  form.querySelector('input[name="amount"]').focus();
}

async function odmSavePaymentEdit(form, kind, id, oldAmount) {
  const cfg = ODM_PAYMENT_EDIT[kind];
  const amount = Number(form.amount.value);
  if (!cfg || !(amount > 0) || !form.payment_date.value) return;
  if (Math.abs(amount - oldAmount) >= 1
    && !confirm(`Change amount from ${odmInr(oldAmount)} to ${odmInr(amount)}?`)) return;
  form.querySelectorAll('button, input').forEach((el) => { el.disabled = true; });
  try {
    const response = await fetchWithAuth(cfg.url(id), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        amount,
        payment_date: form.payment_date.value,
        note: form.note.value.trim(),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false || data.ok === false) {
      const err = data.error;
      throw new Error((err && err.message) || (typeof err === 'string' ? err : '') || 'Save failed');
    }
    await cfg.reload();
  } catch (error) {
    alert(error.message || 'Save failed');
    form.querySelectorAll('button, input').forEach((el) => { el.disabled = false; });
  }
}

/* --------------------------------------------------------------- Hisaab */

const ODM_ALERT_CLASS = { alert: 'is-due', warn: 'is-partial', info: '' };

async function loadDistributorHisaab() {
  const board = document.getElementById('of-hisaab-board');
  const summary = document.getElementById('of-hisaab-summary');
  if (board) board.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;">Loading…</p>';
  try {
    odmLoadSeasons();
    const data = await odmJson('/api/v1/distributor-ledger' + (odmSeason ? `?season=${encodeURIComponent(odmSeason)}` : ''));
    const t = data.totals || {};
    if (summary) {
      summary.textContent = `Order ${odmInr(t.order_payable)} · Received ${odmInr(t.received)} · `
        + `Billed ${odmInr(t.billed)} · To collect ${odmInr(t.payment_due)}`;
    }
    renderDistributorHisaab(data.distributors || []);
  } catch (error) {
    if (board) board.innerHTML = `<p class="nx-text-dim" style="padding:0.75rem;">${odmEsc(error.message)}</p>`;
    if (summary) summary.textContent = '';
  }
}

function renderDistributorHisaab(distributors) {
  const board = document.getElementById('of-hisaab-board');
  if (!board) return;
  if (!distributors.length) {
    board.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;">No distributor payments yet. Match SOs in Order Desk and add deposits first.</p>';
    return;
  }
  board.innerHTML = distributors.map((d) => {
    const t = d.totals || {};
    const alerts = (d.alerts || []).map((a) => `
      <li><span class="of-pay-pill ${ODM_ALERT_CLASS[a.level] || ''}">${odmEsc(String(a.level || 'info').toUpperCase())}</span> ${odmEsc(a.text)}</li>`).join('');
    const cats = (d.categories || []).map((c) => {
      const deposits = (c.deposits || []).map((p) => `
        <tr><td>${odmEsc(p.payment_date || '—')}</td><td class="of-pay-num">${odmInr(p.amount)}</td><td>${odmEsc(p.note || '')}</td>
        <td>${p.id != null ? odmPaymentEditButton('deposit', p) : ''}</td></tr>`).join('');
      const cis = (c.cis || []).map((ci) => {
        const cls = ci.status === 'paid' ? 'is-paid' : (ci.status === 'part_paid' ? 'is-partial' : 'is-due');
        const label = ci.status === 'paid' ? 'PAID' : (ci.status === 'part_paid' ? 'PART PAID' : 'UNPAID');
        return `
          <tr>
            <td>${odmEsc(ci.invoice_no || '—')}</td>
            <td>${odmEsc(ci.invoice_date || '—')}</td>
            <td>${odmEsc(ci.so_number || '—')}</td>
            <td class="of-pay-num">${odmInr(ci.gross)}</td>
            <td class="of-pay-num">${odmInr(ci.net)}</td>
            <td class="of-pay-num">${odmInr(ci.covered)}</td>
            <td><span class="of-pay-pill ${cls}">${label}</span></td>
          </tr>`;
      }).join('');
      const cns = (c.credit_notes || []).map((n) => `
        <tr><td>${odmEsc(n.cn_no || '—')}</td><td>${odmEsc(n.cn_date || '—')}</td>
        <td>${odmEsc(n.reason || '')}</td><td class="of-pay-num">${odmInr(n.amount)}</td>
        <td>${n.has_file ? `<button type="button" class="nx-btn nx-btn--ghost" onclick="odmOpenCreditNote(${n.id})">View</button>` : ''}
        <button type="button" class="nx-btn nx-btn--ghost" onclick="odmDeleteCreditNote(${n.id})">Delete</button></td></tr>`).join('');
      const cnSlot = odmEsc(JSON.stringify({ distributor_id: d.distributor_id, season: c.season, category: c.category }));
      const pend = (c.pending_sos || []).map((p) => `
        <tr><td>${odmEsc(p.so_number)}</td><td>${p.status === 'partial' ? 'Part billed' : 'Open'}</td>
        <td class="of-pay-num">${odmQty(p.pending_qty)}</td><td class="of-pay-num">${odmInr(p.pending_net)}</td></tr>`).join('');
      const terms = [
        `CD ${Number(c.cd_percent || 0)}%`,
        c.tds_percent ? `TDS ${c.tds_percent}%` : 'No TDS',
        c.due_date ? `Pay by ${c.due_date}` : null,
      ].filter(Boolean).join(' · ');
      return `
        <details class="of-pay-order">
          <summary class="of-pay-order-sum">
            <span class="of-pay-order-ref">${odmEsc([c.category, c.season].filter(Boolean).join(' · ') || 'Category')}</span>
            <span class="of-pay-meta">Order ${odmInr(c.order_payable)} · Received ${odmInr(c.received)} · Billed ${odmInr(c.billed)} (${c.ci_count || 0} CI)</span>
            <span class="of-pay-meta">To collect ${odmInr(c.payment_due)}${c.credit_total ? ` (after credit notes ${odmInr(c.credit_total)})` : ''} · Received, CI not made ${odmInr(c.unbilled_advance)}</span>
          </summary>
          <div class="of-pay-order-body">
            <p class="of-pane-help">${odmEsc(terms)}</p>
            <h4 class="of-hisaab-h">Payments received</h4>
            <table class="data-table of-pay-table">
              <thead><tr><th>Date</th><th>Amount</th><th>Note</th><th></th></tr></thead>
              <tbody>${deposits || '<tr><td colspan="4">No deposits yet.</td></tr>'}</tbody>
            </table>
            <h4 class="of-hisaab-h">CI (bills) — oldest payment pays the oldest CI first</h4>
            <table class="data-table of-pay-table">
              <thead><tr><th>CI No</th><th>Date</th><th>SO</th><th>Gross</th><th>Net (after CD/TDS)</th><th>Covered</th><th>Status</th></tr></thead>
              <tbody>${cis || '<tr><td colspan="7">No CI yet.</td></tr>'}</tbody>
            </table>
            <h4 class="of-hisaab-h">Credit notes — lower the balance to collect</h4>
            <table class="data-table of-pay-table">
              <thead><tr><th>CN No</th><th>Date</th><th>Reason</th><th>Amount</th><th></th></tr></thead>
              <tbody>${cns || '<tr><td colspan="5">No credit notes.</td></tr>'}</tbody>
            </table>
            <form class="of-cn-form" data-slot="${cnSlot}" onsubmit="odmAddCreditNote(event)">
              <label>Read from PDF / photo <input type="file" name="file" accept="application/pdf,image/*" onchange="odmReadCreditNote(this)"></label>
              <input name="cn_no" placeholder="CN number">
              <input name="cn_date" type="date">
              <input name="amount" type="number" step="0.01" min="0" placeholder="Amount ₹" required>
              <input name="reason" placeholder="Reason (return, rate difference…)">
              <input name="file_token" type="hidden">
              <button type="submit" class="nx-btn">Add credit note</button>
              <span class="of-cn-msg nx-text-dim"></span>
            </form>
            ${pend ? `
            <h4 class="of-hisaab-h">SO still to be billed</h4>
            <table class="data-table of-pay-table">
              <thead><tr><th>SO</th><th>Status</th><th>Pending qty</th><th>Pending value</th></tr></thead>
              <tbody>${pend}</tbody>
            </table>` : ''}
          </div>
        </details>`;
    }).join('');
    const catList = d.categories || [];
    const grand = catList.length > 1 ? `
        <table class="data-table of-pay-table of-grand-total">
          <thead><tr><th>Category</th><th>Order</th><th>Received</th><th>Credit notes</th><th>Invoiced</th><th>To collect</th></tr></thead>
          <tbody>${catList.map((c) => `
            <tr><td>${odmEsc([c.category, c.season].filter(Boolean).join(' · '))}</td>
            <td class="of-pay-num">${odmInr(c.order_payable)}</td><td class="of-pay-num">${odmInr(c.received)}</td>
            <td class="of-pay-num">${odmInr(c.credit_total || 0)}</td><td class="of-pay-num">${odmInr(c.billed)}</td>
            <td class="of-pay-num">${odmInr(c.payment_due)}</td></tr>`).join('')}
            <tr><th>Grand total</th><th class="of-pay-num">${odmInr(t.order_payable)}</th><th class="of-pay-num">${odmInr(t.received)}</th>
            <th class="of-pay-num">${odmInr(t.credit_total || 0)}</th><th class="of-pay-num">${odmInr(t.billed)}</th>
            <th class="of-pay-num">${odmInr(t.payment_due)}</th></tr>
          </tbody>
        </table>` : '';
    const unlinked = (d.unlinked_cis || []).map((ci) => `
      <tr><td>${odmEsc(ci.invoice_no || '—')}</td><td>${odmEsc(ci.invoice_date || '—')}</td>
      <td>${odmEsc(ci.so_number || '—')}</td><td class="of-pay-num">${odmInr(ci.gross)}</td></tr>`).join('');
    return `
      <section class="of-pay-dist">
        <header class="of-pay-dist-head">
          <strong>${odmEsc(d.distributor_name || 'Distributor')}</strong>
          <span class="of-pay-meta">Order ${odmInr(t.order_payable)} · Received ${odmInr(t.received)} · Billed ${odmInr(t.billed)} · To collect ${odmInr(t.payment_due)}</span>
          <label class="nx-btn nx-btn--ghost" title="Upload the company ledger (Excel or PDF) to find missing CIs, payments and credit notes">
            Check with party ledger
            <input type="file" hidden accept=".xls,.xlsx,.csv,application/pdf,image/*" onchange="odmCheckLedger(this, ${Number(d.distributor_id)})">
          </label>
        </header>
        <div class="of-ledger-check" id="of-ledger-check-${Number(d.distributor_id)}"></div>
        ${alerts ? `<ul class="of-hisaab-alerts">${alerts}</ul>` : ''}
        ${grand}
        ${cats}
        ${unlinked ? `
        <details class="of-pay-order">
          <summary class="of-pay-order-sum"><span class="of-pay-order-ref">CI not linked to a matched SO</span></summary>
          <div class="of-pay-order-body">
            <table class="data-table of-pay-table">
              <thead><tr><th>CI No</th><th>Date</th><th>SO</th><th>Gross</th></tr></thead>
              <tbody>${unlinked}</tbody>
            </table>
          </div>
        </details>` : ''}
      </section>`;
  }).join('');
}

/* ------------------------------------------------- Check with party ledger */

async function odmCheckLedger(input, distributorId) {
  const box = document.getElementById(`of-ledger-check-${distributorId}`);
  const file = input.files && input.files[0];
  input.value = '';
  if (!box || !file) return;
  box.innerHTML = '<p class="nx-text-dim" style="padding:0.5rem 0;">Reading the ledger and matching it with NEXORA…</p>';
  try {
    const fd = new FormData();
    fd.append('distributor_id', String(distributorId));
    fd.append('file', file);
    const r = await odmJson('/api/v1/distributor-ledger/reconcile', { method: 'POST', body: fd });
    const findings = r.findings || [];
    const compare = (r.compare || []).map((c) => `
      <tr><td>${odmEsc(c.label)}</td><td class="of-pay-num">${odmInr(c.ledger)}</td>
      <td class="of-pay-num">${odmInr(c.nexora)}</td>
      <td class="of-pay-num">${Math.abs(c.ledger - c.nexora) < 2 ? '✓' : odmInr(c.ledger - c.nexora)}</td></tr>`).join('');
    const rows = findings.map((f) => `
      <li><span class="of-pay-pill ${f.level === 'alert' ? 'is-due' : (f.level === 'info' ? 'is-paid' : 'is-partial')}">${f.level === 'alert' ? 'MISSING' : (f.level === 'info' ? 'OK' : 'CHECK')}</span>
      <strong>${odmEsc(f.title)}</strong><br><span class="nx-text-dim">${odmEsc(f.detail || '')}</span><br>→ ${odmEsc(f.action || '')}</li>`).join('');
    box.innerHTML = `
      <div class="of-pay-order-body">
        <h4 class="of-hisaab-h">Ledger check ${r.period_from ? `· ${odmEsc(r.period_from)} to ${odmEsc(r.period_to)}` : ''}
          ${r.read_by === 'ai' ? '<span class="nx-text-dim">(read by AI)</span>' : ''}</h4>
        <table class="data-table of-pay-table">
          <thead><tr><th></th><th>Ledger</th><th>NEXORA</th><th>Difference</th></tr></thead>
          <tbody>${compare}</tbody>
        </table>
        ${r.summary ? `<p class="of-ledger-summary"><strong>✨ AI summary:</strong> ${odmEsc(r.summary)}</p>` : ''}
        ${r.skipped_older ? `<p class="nx-text-dim">Compared from ${odmEsc(r.compared_from)} (when NEXORA's data for this distributor starts). ${Number(r.skipped_older)} older ledger lines not checked.</p>` : ''}
        ${r.mismatch_count ? `<p><strong>${Number(r.mismatch_count)} mismatch${r.mismatch_count === 1 ? '' : 'es'} found</strong></p>` : '<p><strong>✓ Everything matches the ledger.</strong></p>'}
        ${findings.length ? `<ul class="of-hisaab-alerts">${rows}</ul>` : ''}
      </div>`;
  } catch (error) {
    box.innerHTML = `<p class="nx-text-dim" style="padding:0.5rem 0;">${odmEsc(error.message)}</p>`;
  }
}

/* ---------------------------------------------------------- Credit notes */

async function odmReadCreditNote(input) {
  const form = input.closest('form');
  const msg = form.querySelector('.of-cn-msg');
  const file = input.files && input.files[0];
  if (!file) return;
  msg.textContent = 'Reading…';
  try {
    const fd = new FormData();
    fd.append('file', file);
    const data = await odmJson('/api/v1/distributor-ledger/credit-notes/read', { method: 'POST', body: fd });
    form.file_token.value = data.file_token || '';
    const f = data.fields;
    if (f) {
      if (f.cn_no) form.cn_no.value = f.cn_no;
      if (f.cn_date) form.cn_date.value = f.cn_date;
      if (f.amount) form.amount.value = f.amount;
      if (f.reason) form.reason.value = f.reason;
      msg.textContent = 'Check the details, then click Add credit note.';
    } else {
      msg.textContent = data.message || 'Could not read it. Type the details in.';
    }
  } catch (error) {
    msg.textContent = error.message;
  }
}

async function odmAddCreditNote(event) {
  event.preventDefault();
  const form = event.target;
  const slot = JSON.parse(form.dataset.slot || '{}');
  const body = {
    ...slot,
    cn_no: form.cn_no.value,
    cn_date: form.cn_date.value,
    amount: Number(form.amount.value || 0),
    reason: form.reason.value,
    file_token: form.file_token.value || null,
  };
  try {
    await odmJson('/api/v1/distributor-ledger/credit-notes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    loadDistributorHisaab();
  } catch (error) {
    form.querySelector('.of-cn-msg').textContent = error.message;
  }
}

async function odmDeleteCreditNote(id) {
  if (!confirm('Delete this credit note? The balance to collect will go up again.')) return;
  try {
    await odmJson(`/api/v1/distributor-ledger/credit-notes/${id}`, { method: 'DELETE' });
    loadDistributorHisaab();
  } catch (error) {
    alert(error.message);
  }
}

async function odmOpenCreditNote(id) {
  try {
    const res = await fetchWithAuth(`/api/v1/distributor-ledger/credit-notes/${id}/file`);
    if (!res.ok) throw new Error('No file for this credit note');
    window.open(URL.createObjectURL(await res.blob()), '_blank');
  } catch (error) {
    alert(error.message);
  }
}

/* ------------------------------------------------------- SO payment mails */

const ODM_MAIL_STATUS = {
  review: ['Needs review', 'is-partial'],
  already_imported: ['Already in Order Desk', ''],
  confirmed: ['Confirmed', 'is-paid'],
  auto_saved: ['Saved automatically', 'is-paid'],
  rejected: ['Ignored', ''],
  error: ['Could not read', 'is-due'],
};

let _odmMailItems = [];
const _odmMailAnswers = {};
let _odmMailBusy = false;

function odmMailMessage(text) {
  const el = document.getElementById('of-somail-message');
  if (el) el.textContent = text || '';
}

async function loadSoMailGmailStatus() {
  const el = document.getElementById('of-somail-gmail');
  const btn = document.getElementById('of-somail-connect');
  try {
    const data = await odmJson('/api/v1/mail-sync/status');
    if (el) el.textContent = data.connected ? 'Gmail connected' : 'Gmail not connected';
    if (btn) btn.hidden = !!data.connected;
  } catch (_) {
    if (el) el.textContent = '';
  }
}

async function connectSoMailGmail() {
  try {
    const data = await odmJson('/api/v1/mail-sync/connect', { method: 'POST' });
    if (!data.oauth_url) throw new Error('No Google sign-in link returned');
    window.open(data.oauth_url, 'nexora-gmail', 'width=520,height=680');
    odmMailMessage('Finish the Google sign-in in the new window.');
  } catch (error) {
    odmMailMessage(error.message);
  }
}

window.addEventListener('message', (event) => {
  if (event?.data?.type === 'gmail_sync_connected') {
    odmMailMessage('Gmail connected. Click "Check SO mails".');
    loadSoMailGmailStatus();
  }
});

async function loadSoMailImports() {
  const board = document.getElementById('of-somail-board');
  if (board && !_odmMailItems.length) board.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;">Loading…</p>';
  loadSoMailGmailStatus();
  try {
    const data = await odmJson('/api/v1/so-mail/imports');
    _odmMailItems = data.items || [];
    renderSoMailImports();
  } catch (error) {
    if (board) board.innerHTML = `<p class="nx-text-dim" style="padding:0.75rem;">${odmEsc(error.message)}</p>`;
  }
}

async function odmMailRun(fn) {
  if (_odmMailBusy) return;
  _odmMailBusy = true;
  document.querySelectorAll('#of-somail-pane button').forEach((b) => { b.disabled = true; });
  try {
    await fn();
  } catch (error) {
    odmMailMessage(error.message || 'Something went wrong');
  } finally {
    _odmMailBusy = false;
    document.querySelectorAll('#of-somail-pane button').forEach((b) => { b.disabled = false; });
    renderSoMailImports();
  }
}

function scanSoMails() {
  odmMailMessage('Checking mails… this can take a minute while SO PDFs are read.');
  return odmMailRun(async () => {
    const d = await odmJson('/api/v1/so-mail/scan', { method: 'POST' });
    if (d.connected === false) {
      odmMailMessage('Connect Gmail first.');
    } else {
      const saved = d.auto_saved ? ` · ${d.auto_saved} saved automatically` : '';
      odmMailMessage(`Found ${d.matched || 0} SO mails · ${d.staged || 0} new${saved}`);
    }
    const list = await odmJson('/api/v1/so-mail/imports');
    _odmMailItems = list.items || [];
  });
}

function odmMailAutoAnswers(item) {
  const out = {};
  ((item.proposal || {}).questions || []).forEach((q) => {
    if (q.key && q.auto) out[q.key] = q.auto;
  });
  return out;
}

function pickSoMailAnswer(id, key, value) {
  if (value === 'ignore') {
    confirmSoMail(id, { [key]: 'ignore' });
    return;
  }
  _odmMailAnswers[id] = { ...(_odmMailAnswers[id] || {}), [key]: value };
  renderSoMailImports();
}

function confirmSoMail(id, answersOverride) {
  const item = _odmMailItems.find((i) => i.id === id);
  if (!item) return;
  const answers = answersOverride || _odmMailAnswers[id] || {};
  const body = { answers };
  const guess = (item.proposal || {}).distributor || {};
  if (answers.distributor === 'pick' && guess.id) body.distributor_id = guess.id;
  return odmMailRun(async () => {
    await odmJson(`/api/v1/so-mail/imports/${id}/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    delete _odmMailAnswers[id];
    odmMailMessage(Object.values(answers).includes('ignore')
      ? 'Mail ignored.'
      : 'Saved: SOs matched and payment settings updated.');
    const list = await odmJson('/api/v1/so-mail/imports');
    _odmMailItems = list.items || [];
  });
}

function rejectSoMail(id) {
  return odmMailRun(async () => {
    await odmJson(`/api/v1/so-mail/imports/${id}/reject`, { method: 'POST' });
    odmMailMessage('Mail ignored.');
    const list = await odmJson('/api/v1/so-mail/imports');
    _odmMailItems = list.items || [];
  });
}

function undoSoMail(id) {
  if (!confirm('Undo this mail? Its SO matches are removed and CD, TDS and due date go back to what they were.')) return;
  return odmMailRun(async () => {
    await odmJson(`/api/v1/so-mail/imports/${id}/undo`, { method: 'POST' });
    odmMailMessage('Undone: SO matches and payment changes were removed.');
    const list = await odmJson('/api/v1/so-mail/imports');
    _odmMailItems = list.items || [];
  });
}

function renderSoMailImports() {
  const board = document.getElementById('of-somail-board');
  if (!board) return;
  const showIgnored = !!document.getElementById('of-somail-show-ignored')?.checked;
  const items = _odmMailItems.filter((i) => showIgnored || i.status !== 'rejected');
  const reviewCount = _odmMailItems.filter((i) => i.status === 'review' || i.status === 'already_imported').length;
  const summary = document.getElementById('of-somail-summary');
  if (summary) summary.textContent = reviewCount ? `${reviewCount} waiting for you` : '';
  if (!items.length) {
    board.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;">No SO payment mails yet. Click "Check SO mails".</p>';
    return;
  }
  board.innerHTML = items.map((item) => odmMailCard(item)).join('');
}

function odmMailCard(item) {
  const id = item.id;
  const status = item.status;
  const [label, cls] = ODM_MAIL_STATUS[status] || [status || '—', ''];
  const parsed = item.parsed || {};
  const summary = parsed.summary || {};
  const proposal = item.proposal || {};
  const soCount = (parsed.so_rows || []).length;
  const subject = String(item.subject || '').replace(/^(Fwd|FW|Fw):\s*/i, '');
  const fos = (proposal.filled_order_candidates || [])
    .map((f) => [f.source_filename, f.season].filter(Boolean).join(' · ')).join(', ');
  const checks = (proposal.checks || []).map((c) => `
    <li class="${c.ok ? 'of-somail-ok' : 'of-somail-bad'}">${c.ok ? '✓' : '✗'} ${odmEsc(c.text)}</li>`).join('');
  const decisions = ((item.result || {}).auto_decisions || []).map((d) => `<li>${odmEsc(d)}</li>`).join('');
  const open = status === 'review' || status === 'already_imported';
  const questions = proposal.questions || [];
  const answers = _odmMailAnswers[id] || odmMailAutoAnswers(item);
  if (open && !_odmMailAnswers[id]) _odmMailAnswers[id] = answers;
  const guess = proposal.distributor || {};

  let actions = '';
  if (open) {
    const qHtml = questions.map((q) => {
      const opts = (q.options || []).map((o) => {
        if (o.value === 'pick' && !guess.id) return '';
        const text = o.value === 'pick' ? `Use ${guess.name || 'best guess'}` : (o.label || o.value);
        const picked = answers[q.key] === o.value;
        return `<button type="button" class="nx-btn ${picked ? 'nx-btn-primary' : ''}"
          onclick="pickSoMailAnswer(${id}, '${odmEsc(q.key)}', '${odmEsc(o.value)}')">${odmEsc(text)}</button>`;
      }).join('');
      return `<div class="of-somail-q"><p>${odmEsc(q.text)}</p><div class="of-somail-opts">${opts}</div></div>`;
    }).join('');
    const allAnswered = questions.every((q) => answers[q.key]);
    const distOk = !!item.distributor_name || answers.distributor === 'pick';
    const confirmText = !allAnswered ? 'Answer the questions above'
      : (status === 'review' ? 'Confirm & save' : 'Apply CD/TDS/due date');
    actions = `${qHtml}
      <div class="of-somail-actions">
        <button type="button" class="nx-btn nx-btn-primary" ${allAnswered && distOk ? '' : 'disabled'}
          onclick="confirmSoMail(${id})">${confirmText}</button>
        <button type="button" class="nx-btn" onclick="rejectSoMail(${id})">Ignore</button>
      </div>`;
  } else if (status === 'auto_saved' || status === 'confirmed') {
    actions = `<div class="of-somail-actions"><button type="button" class="nx-btn" onclick="undoSoMail(${id})">Undo</button></div>`;
  } else if (status === 'error') {
    actions = `<div class="of-somail-actions"><button type="button" class="nx-btn" onclick="rejectSoMail(${id})">Ignore</button></div>`;
  }

  const tds = proposal.tds_applicable ? 'yes' : 'no';
  const cd = proposal.cd_percent != null ? `${proposal.cd_percent}%` : '—';
  return `
    <section class="of-pay-dist of-somail-card">
      <header class="of-pay-dist-head">
        <strong>${odmEsc([item.distributor_name || 'Distributor not recognised', item.category].filter(Boolean).join(' · '))}</strong>
        <span class="of-pay-pill ${cls}">${odmEsc(label)}</span>
      </header>
      <div class="of-pay-order-body of-somail-body">
        <p class="of-pay-meta">${odmEsc(subject)}${item.email_date ? ` · ${odmEsc(item.email_date)}` : ''}</p>
        <p>${soCount} SO · ${odmQty(summary.qty)} pcs · Gross ${odmInr(summary.gross)}</p>
        <p>CD ${odmEsc(cd)} · TDS ${tds} · Payable ${odmInr(proposal.mail_total_payable)}${proposal.due_date ? ` · <strong>Pay by ${odmEsc(proposal.due_date)}</strong>` : ''}</p>
        ${fos ? `<p class="of-pay-meta">Filled Order: ${odmEsc(fos)}</p>` : ''}
        ${checks ? `<ul class="of-somail-checks">${checks}</ul>` : ''}
        ${item.error ? `<p class="of-somail-bad">${odmEsc(item.error)}</p>` : ''}
        ${status === 'auto_saved' && decisions ? `<p class="of-pay-meta"><strong>Decided by NEXORA:</strong></p><ul class="of-somail-checks">${decisions}</ul>` : ''}
        ${actions}
      </div>
    </section>`;
}
