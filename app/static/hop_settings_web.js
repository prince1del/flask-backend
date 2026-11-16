/**
 * HoP Phase 3 — items & settings on the web.
 * Loaded after hop_money_web / hop_reports_web.
 */
(function () {
  'use strict';

  async function hopRenderItemSettingsModule(mount) {
    let cats = [];
    let units = [];
    try {
      const [c, u] = await Promise.all([
        hopApi('/api/v1/hop/item-categories'),
        hopApi('/api/v1/hop/units'),
      ]);
      cats = (c && c.categories) || [];
      units = (u && u.units) || [];
    } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Items & units', '', '',
        `<p class="nx-text-dim">${foEscapeText(e.message || 'Could not load')}</p>`);
      return;
    }
    const catRows = cats.map((c) =>
      `<tr><td>${foEscapeText(c.name)}</td><td class="num">${c.product_count || 0}</td></tr>`
    ).join('') || '<tr><td colspan="2" class="nx-text-dim">No categories yet</td></tr>';
    const unitRows = units.map((u) =>
      `<tr><td>${foEscapeText(u.name)}</td></tr>`
    ).join('') || '<tr><td class="nx-text-dim">No units yet</td></tr>';
    const body = `
      <div class="hop-form-grid" style="margin-bottom:18px;">
        <label>New category
          <span style="display:flex;gap:8px;">
            <input id="hop-new-item-cat" placeholder="e.g. Bed Linen" />
            <button type="button" class="nx-btn nx-btn-primary" onclick="hopAddItemCategory()">Add</button>
          </span>
        </label>
        <label>New unit
          <span style="display:flex;gap:8px;">
            <input id="hop-new-unit" placeholder="e.g. MTR" />
            <button type="button" class="nx-btn nx-btn-primary" onclick="hopAddUnit()">Add</button>
          </span>
        </label>
      </div>
      <div class="hop-form-grid">
        <div>
          <h3 class="nx-text-dim" style="margin:0 0 8px;font-size:.85rem;">Categories</h3>
          <table class="nx-table"><thead><tr><th>Name</th><th>Products</th></tr></thead>
          <tbody>${catRows}</tbody></table>
        </div>
        <div>
          <h3 class="nx-text-dim" style="margin:0 0 8px;font-size:.85rem;">Units</h3>
          <table class="nx-table"><thead><tr><th>Name</th></tr></thead>
          <tbody>${unitRows}</tbody></table>
        </div>
      </div>`;
    mount.innerHTML = hopModuleShell('Settings', 'Items & units',
      'Categories and units used on products and bills', '', body);
  }

  async function hopAddItemCategory() {
    const name = document.getElementById('hop-new-item-cat')?.value?.trim();
    if (!name) return;
    try {
      await hopApi('/api/v1/hop/item-categories', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      openHopView('item_settings');
    } catch (e) {
      if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err');
    }
  }

  async function hopAddUnit() {
    const name = document.getElementById('hop-new-unit')?.value?.trim();
    if (!name) return;
    try {
      await hopApi('/api/v1/hop/units', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      openHopView('item_settings');
    } catch (e) {
      if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err');
    }
  }

  async function hopRenderOverdueModule(mount) {
    let data = { bills: [], total_overdue: 0, count: 0 };
    try {
      data = await hopApi('/api/v1/hop/overdue-bills') || data;
    } catch (e) {
      mount.innerHTML = hopModuleShell('Sale', 'Overdue', '', '',
        `<p class="nx-text-dim">${foEscapeText(e.message || 'Could not load')}</p>`);
      return;
    }
    const rows = (data.bills || []).map((b) => `
      <tr>
        <td>${foEscapeText(b.party_name)}</td>
        <td>${foEscapeText(b.txn_number)}</td>
        <td>${foEscapeText(b.due_date)}</td>
        <td class="num">${b.days_overdue || 0}d</td>
        <td class="num">${typeof hopMoney === 'function' ? hopMoney(b.balance_amount) : b.balance_amount}</td>
        <td><button type="button" class="nx-btn" onclick="hopPaymentReminder(${Number(b.id)})">WhatsApp reminder</button></td>
      </tr>`).join('')
      || '<tr><td colspan="6" class="nx-text-dim">No overdue bills with a due date</td></tr>';
    const body = `
      <p class="nx-text-dim" style="margin:0 0 12px;">${data.count || 0} bills · total
        <strong>${typeof hopMoney === 'function' ? hopMoney(data.total_overdue) : data.total_overdue}</strong>
        (as of ${foEscapeText(data.as_of || '')})</p>
      <table class="nx-table">
        <thead><tr><th>Party</th><th>Bill</th><th>Due</th><th>Late</th><th>Balance</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    mount.innerHTML = hopModuleShell('Sale', 'Overdue bills', 'Due-date reminders', '', body);
  }

  async function hopPaymentReminder(txnId) {
    try {
      const out = await hopApi('/api/v1/hop/ai/payment-reminder', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ txn_id: txnId }),
      });
      const text = (out && out.text) || '';
      if (!text) throw new Error('No reminder text');
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      const wa = `https://wa.me/?text=${encodeURIComponent(text)}`;
      window.open(wa, '_blank', 'noopener');
      if (typeof nexoraToast === 'function') {
        nexoraToast(out.source === 'ai' ? 'AI reminder ready' : 'Reminder copied', 'ok');
      }
    } catch (e) {
      if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err');
    }
  }

  async function hopRenderReorderModule(mount) {
    let data = { items: [] };
    try {
      data = await hopApi('/api/v1/hop/stock/reorder?days=30&low_at=0') || data;
    } catch (e) {
      mount.innerHTML = hopModuleShell('Stock', 'Reorder', '', '',
        `<p class="nx-text-dim">${foEscapeText(e.message || 'Could not load')}</p>`);
      return;
    }
    const rows = (data.items || []).map((it) => `
      <tr>
        <td>${foEscapeText(it.name)}</td>
        <td class="num">${it.stock_qty}</td>
        <td class="num">${it.sold_last_days || 0}</td>
        <td class="num">${it.suggested_reorder_qty != null ? it.suggested_reorder_qty : '—'}</td>
        <td class="nx-text-dim">${foEscapeText(it.hint || '')}</td>
      </tr>`).join('')
      || '<tr><td colspan="5" class="nx-text-dim">Nothing low in stock</td></tr>';
    const body = `
      <p class="nx-text-dim" style="margin:0 0 12px;">Based on last ${data.days || 30} days of sales</p>
      <table class="nx-table">
        <thead><tr><th>Item</th><th>Stock</th><th>Sold</th><th>Suggest</th><th>Hint</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    mount.innerHTML = hopModuleShell('Stock', 'Reorder suggestions', 'Low stock from sales speed', '', body);
  }

  /** Suggest rate when item name blurs on a sale/purchase draft. */
  async function hopMaybeSuggestLineRate(si, li) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    const bucket = si >= 0 ? d.sections?.[si]?.lines : d.lines;
    const ln = bucket?.[li];
    if (!ln || !String(ln.item_name || '').trim()) return;
    if (String(ln.rate ?? '').trim() !== '') return;
    const md = hopState.manualDoc || {};
    const partyId = Number(
      d.party_id || d.customerId || d.customer_id || d.vendor_id
      || document.getElementById('hop-manual-customer')?.value || 0
    );
    if (!partyId) return;
    const partyKind = md.partyKind || hopState.manualDocPartyKind || '';
    const partyType = d.party_type
      || (partyKind === 'vendor' || Number(md.txnType) === 2 ? 'vendor' : 'customer');
    try {
      const q = new URLSearchParams({
        party_type: partyType,
        party_id: String(partyId),
        item_name: String(ln.item_name || '').trim(),
        qty: String(Number(ln.qty || 0)),
      });
      if (ln.product_id) q.set('product_id', String(ln.product_id));
      const sug = await hopApi(`/api/v1/hop/suggest-rate?${q}`);
      if (!sug || !(Number(sug.suggested_rate) > 0)) return;
      ln.rate = String(sug.suggested_rate);
      if (sug.unit && !String(ln.unit || '').trim()) ln.unit = sug.unit;
      if (sug.hsn && !String(ln.hsn || '').trim()) ln.hsn = sug.hsn;
      if (sug.tax_pct != null && (ln.tax_pct === '' || ln.tax_pct == null)) ln.tax_pct = String(sug.tax_pct);
      const rateEl = document.querySelector(
        `[data-hop-line-si="${si}"][data-hop-line-li="${li}"][data-hop-line-field="rate"]`
      );
      if (rateEl) rateEl.value = ln.rate;
      if (typeof window.hopManualDocUpdateLineTotals === 'function') window.hopManualDocUpdateLineTotals(si, li);
      if (typeof nexoraToast === 'function') {
        nexoraToast(
          sug.source === 'party_last'
            ? `Rate from last bill: ₹${sug.suggested_rate}`
            : (sug.source === 'wholesale' ? `Wholesale rate: ₹${sug.suggested_rate}` : `Catalogue rate: ₹${sug.suggested_rate}`),
          'ok'
        );
      }
    } catch (_e) { /* silent — suggestion is optional */ }
  }

  async function hopCheckEntryWarningsBeforeSave() {
    const d = hopState.manualDocDraft;
    if (!d) return true;
    const lines = [];
    (d.sections || []).forEach((s) => (s.lines || []).forEach((ln) => lines.push(ln)));
    (d.lines || []).forEach((ln) => lines.push(ln));
    try {
      const out = await hopApi('/api/v1/hop/entry-warnings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          txn_type: d.txn_type || hopState.manualDoc?.txnType,
          party_type: d.party_type,
          party_id: d.party_id || d.customerId || d.customer_id || d.vendor_id,
          txn_number: d.txn_number || d.txnNumber,
          exclude_txn_id: d.party_txn_id || hopState.manualDoc?.editTxnId,
          lines,
        }),
      });
      const warns = (out && out.warnings) || [];
      if (!warns.length) return true;
      const msg = warns.map((w) => w.message).join('\n');
      return window.confirm(`${msg}\n\nSave anyway?`);
    } catch (_e) {
      return true;
    }
  }

  async function hopRenderPrintThemeModule(mount) {
    let data = { presets: [], active: {} };
    try { data = await hopApi('/api/v1/hop/print-theme') || data; } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Print theme', '', '', `<p class="nx-text-dim">${foEscapeText(e.message || '')}</p>`);
      return;
    }
    const activeId = (data.active && data.active.id) || 'classic_blue';
    const cards = (data.presets || []).map((t) => {
      const rgb = (t.accent || []).join(',');
      const sel = t.id === activeId ? ' outline:2px solid var(--nx-accent, #1d4ed8);' : '';
      return `<button type="button" class="nx-btn" style="text-align:left;padding:12px;${sel}" onclick="hopSetPrintTheme('${foEscapeAttr(t.id)}')">
        <span style="display:inline-block;width:18px;height:18px;border-radius:4px;background:rgb(${rgb});vertical-align:middle;margin-right:8px;"></span>
        ${foEscapeText(t.name)}${t.id === activeId ? ' · active' : ''}
      </button>`;
    }).join('');
    mount.innerHTML = hopModuleShell('Settings', 'Print theme', 'Accent colour on PDF invoices', '',
      `<div style="display:grid;gap:10px;max-width:420px;">${cards}</div>`);
  }

  async function hopSetPrintTheme(id) {
    try {
      await hopApi('/api/v1/hop/print-theme', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ theme_id: id }) });
      openHopView('print_theme');
      if (typeof nexoraToast === 'function') nexoraToast('Print theme saved', 'ok');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  async function hopRenderRecycleModule(mount) {
    let data = { docs: [], products: [] };
    try { data = await hopApi('/api/v1/hop/recycle-bin') || data; } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Recycle bin', '', '', `<p class="nx-text-dim">${foEscapeText(e.message || '')}</p>`);
      return;
    }
    const docs = (data.docs || []).map((d) =>
      `<tr><td>${foEscapeText(d.txn_number || d.txn_label)}</td><td>${foEscapeText(d.party_name)}</td><td>${foEscapeText(d.deleted_at)}</td>
       <td><button type="button" class="nx-btn" onclick="hopRestoreRecycle('docs',${Number(d.id)})">Restore</button></td></tr>`
    ).join('') || '<tr><td colspan="4" class="nx-text-dim">No deleted documents</td></tr>';
    const prods = (data.products || []).map((p) =>
      `<tr><td>${foEscapeText(p.name)}</td><td>${foEscapeText(p.code)}</td><td>${foEscapeText(p.deleted_at)}</td>
       <td><button type="button" class="nx-btn" onclick="hopRestoreRecycle('products',${Number(p.id)})">Restore</button></td></tr>`
    ).join('') || '<tr><td colspan="4" class="nx-text-dim">No deleted products</td></tr>';
    mount.innerHTML = hopModuleShell('Settings', 'Recycle bin', 'Soft-deleted docs & products', '', `
      <h3 class="nx-text-dim" style="font-size:.85rem;">Documents</h3>
      <table class="nx-table"><thead><tr><th>Doc</th><th>Party</th><th>Deleted</th><th></th></tr></thead><tbody>${docs}</tbody></table>
      <h3 class="nx-text-dim" style="font-size:.85rem;margin-top:18px;">Products</h3>
      <table class="nx-table"><thead><tr><th>Name</th><th>Code</th><th>Deleted</th><th></th></tr></thead><tbody>${prods}</tbody></table>`);
  }

  async function hopRestoreRecycle(kind, id) {
    try {
      await hopApi(`/api/v1/hop/recycle-bin/${kind}/${id}/restore`, { method: 'POST' });
      openHopView('recycle_bin');
      if (typeof nexoraToast === 'function') nexoraToast('Restored', 'ok');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  async function hopRenderLoansModule(mount) {
    let loans = [];
    try { const d = await hopApi('/api/v1/hop/loan-accounts'); loans = (d && d.loans) || []; } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Loans', '', '', `<p class="nx-text-dim">${foEscapeText(e.message || '')}</p>`);
      return;
    }
    const rows = loans.map((l) =>
      `<tr><td>${foEscapeText(l.name)}</td><td>${foEscapeText(l.lender)}</td>
       <td class="num">${typeof hopMoney === 'function' ? hopMoney(l.outstanding) : l.outstanding}</td></tr>`
    ).join('') || '<tr><td colspan="3" class="nx-text-dim">No loans</td></tr>';
    mount.innerHTML = hopModuleShell('Settings', 'Loan accounts', 'Shown on Balance Sheet',
      `<button type="button" class="nx-btn nx-btn-primary" onclick="hopAddLoanPrompt()">+ Loan</button>`,
      `<table class="nx-table"><thead><tr><th>Name</th><th>Lender</th><th>Outstanding</th></tr></thead><tbody>${rows}</tbody></table>`);
  }

  async function hopAddLoanPrompt() {
    const name = window.prompt('Loan name');
    if (!name) return;
    const opening = Number(window.prompt('Opening outstanding', '0') || 0);
    try {
      await hopApi('/api/v1/hop/loan-accounts', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, opening_balance: opening, principal: opening }),
      });
      openHopView('loans');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  async function hopRenderFixedAssetsModule(mount) {
    let assets = [];
    try { const d = await hopApi('/api/v1/hop/fixed-assets'); assets = (d && d.assets) || []; } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Fixed assets', '', '', `<p class="nx-text-dim">${foEscapeText(e.message || '')}</p>`);
      return;
    }
    const rows = assets.map((a) =>
      `<tr><td>${foEscapeText(a.name)}</td><td class="num">${a.purchase_cost}</td>
       <td class="num">${a.accumulated_dep || 0}</td><td class="num">${a.book_value}</td></tr>`
    ).join('') || '<tr><td colspan="4" class="nx-text-dim">No assets</td></tr>';
    mount.innerHTML = hopModuleShell('Settings', 'Fixed assets', 'Cost − depreciation on Balance Sheet',
      `<button type="button" class="nx-btn nx-btn-primary" onclick="hopAddAssetPrompt()">+ Asset</button>
       <button type="button" class="nx-btn" onclick="hopRunDepreciation()">Run this month’s depreciation</button>`,
      `<table class="nx-table"><thead><tr><th>Name</th><th>Cost</th><th>Accum. dep</th><th>Book</th></tr></thead><tbody>${rows}</tbody></table>`);
  }

  async function hopAddAssetPrompt() {
    const name = window.prompt('Asset name');
    if (!name) return;
    const cost = Number(window.prompt('Purchase cost', '0') || 0);
    const life = Number(window.prompt('Life (months)', '60') || 60);
    try {
      await hopApi('/api/v1/hop/fixed-assets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, purchase_cost: cost, life_months: life }),
      });
      openHopView('fixed_assets');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  async function hopRunDepreciation() {
    try {
      const out = await hopApi('/api/v1/hop/fixed-assets/run-depreciation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (typeof nexoraToast === 'function') nexoraToast(`Posted ${(out && out.count) || 0} depreciation lines`, 'ok');
      openHopView('fixed_assets');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  async function hopRenderFyModule(mount) {
    let years = [];
    try { const d = await hopApi('/api/v1/hop/financial-years'); years = (d && d.years) || []; } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Financial year', '', '', `<p class="nx-text-dim">${foEscapeText(e.message || '')}</p>`);
      return;
    }
    const rows = years.map((y) =>
      `<tr><td>${foEscapeText(y.label)}</td><td>${foEscapeText(y.start_date)} → ${foEscapeText(y.end_date)}</td>
       <td>${foEscapeText(y.status)}</td>
       <td>${y.status === 'open' ? `<button type="button" class="nx-btn" onclick="hopCloseFy(${Number(y.id)})">Close year</button>` : '—'}</td></tr>`
    ).join('');
    mount.innerHTML = hopModuleShell('Settings', 'Financial year', 'Closing locks documents in that period', '',
      `<table class="nx-table"><thead><tr><th>Year</th><th>Dates</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table>`);
  }

  async function hopCloseFy(id) {
    if (!window.confirm('Close this financial year? Documents dated in it cannot be saved until reopened.')) return;
    try {
      await hopApi(`/api/v1/hop/financial-years/${id}/close`, { method: 'POST' });
      openHopView('financial_year');
      if (typeof nexoraToast === 'function') nexoraToast('Year closed', 'ok');
    } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Failed', 'err'); }
  }

  function hopRenderTallyExportModule(mount) {
    const body = `
      <p class="nx-text-dim">Download vouchers for the date range (XML for Tally import, CSV day book).</p>
      <div class="hop-form-grid" style="max-width:480px;">
        <label>From<input type="date" id="hop-tally-from" /></label>
        <label>To<input type="date" id="hop-tally-to" /></label>
      </div>
      <div style="display:flex;gap:10px;margin-top:14px;">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopDownloadTally('xml')">Download XML</button>
        <button type="button" class="nx-btn" onclick="hopDownloadTally('csv')">Download CSV</button>
      </div>`;
    mount.innerHTML = hopModuleShell('Settings', 'Export to Tally', '', '', body);
  }

  function hopDownloadTally(kind) {
    const frm = document.getElementById('hop-tally-from')?.value || '';
    const to = document.getElementById('hop-tally-to')?.value || '';
    const q = new URLSearchParams();
    if (frm) q.set('from', frm);
    if (to) q.set('to', to);
    const path = kind === 'xml' ? '/api/v1/hop/export/tally.xml' : '/api/v1/hop/export/tally.csv';
    window.open(`${path}?${q}`, '_blank');
  }

  window.hopRenderItemSettingsModule = hopRenderItemSettingsModule;
  window.hopRenderOverdueModule = hopRenderOverdueModule;
  window.hopRenderReorderModule = hopRenderReorderModule;
  window.hopRenderPrintThemeModule = hopRenderPrintThemeModule;
  window.hopRenderRecycleModule = hopRenderRecycleModule;
  window.hopRenderLoansModule = hopRenderLoansModule;
  window.hopRenderFixedAssetsModule = hopRenderFixedAssetsModule;
  window.hopRenderFyModule = hopRenderFyModule;
  window.hopRenderTallyExportModule = hopRenderTallyExportModule;
  window.hopAddItemCategory = hopAddItemCategory;
  window.hopAddUnit = hopAddUnit;
  window.hopPaymentReminder = hopPaymentReminder;
  window.hopMaybeSuggestLineRate = hopMaybeSuggestLineRate;
  window.hopCheckEntryWarningsBeforeSave = hopCheckEntryWarningsBeforeSave;
  window.hopSetPrintTheme = hopSetPrintTheme;
  window.hopRestoreRecycle = hopRestoreRecycle;
  window.hopAddLoanPrompt = hopAddLoanPrompt;
  window.hopAddAssetPrompt = hopAddAssetPrompt;
  window.hopRunDepreciation = hopRunDepreciation;
  window.hopCloseFy = hopCloseFy;
  window.hopDownloadTally = hopDownloadTally;
})();
