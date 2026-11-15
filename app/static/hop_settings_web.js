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
            : `Catalogue rate: ₹${sug.suggested_rate}`,
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

  window.hopRenderItemSettingsModule = hopRenderItemSettingsModule;
  window.hopRenderOverdueModule = hopRenderOverdueModule;
  window.hopRenderReorderModule = hopRenderReorderModule;
  window.hopAddItemCategory = hopAddItemCategory;
  window.hopAddUnit = hopAddUnit;
  window.hopPaymentReminder = hopPaymentReminder;
  window.hopMaybeSuggestLineRate = hopMaybeSuggestLineRate;
  window.hopCheckEntryWarningsBeforeSave = hopCheckEntryWarningsBeforeSave;
})();
