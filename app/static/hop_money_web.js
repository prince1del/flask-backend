/**
 * HoP money flow on the web (Vyapar-style). Loaded after hop_app.js and hop_commercial_web.js.
 *  - Sale Invoice / Purchase Bill / Returns: "Received" / "Paid" now, payment type, balance.
 *  - Payment-In / Payment-Out: amount, account, and the open bills it clears.
 *  - Expense: category (Rent, Salary…) or a party, amount, account.
 *  - Cash & Bank: Cash in hand + banks, statement, deposit / withdraw / transfer, cheques.
 *  - Preview: Convert to … and linked payments.
 * Server: /api/v1/hop/open-bills, /cheques, /expense-categories, /party-transactions/<id>/convert|links.
 */
(function () {
  'use strict';

  const BILL_TYPES = { 1: 'Received', 21: 'Paid', 2: 'Paid', 16: 'Received' };
  const PAYMENT_TYPES = new Set([3, 4, 7]);
  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const money = (v) => (typeof hopMoney === 'function' ? hopMoney(v) : `₹${Number(v || 0).toFixed(2)}`);
  const num = (v) => { const n = Number(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : 0; };
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };

  // ---------- Reference data for the form ----------
  async function loadRefs() {
    const [acc, cats] = await Promise.all([
      hopApi('/api/v1/hop/bank-accounts').catch(() => ({ accounts: [] })),
      hopApi('/api/v1/hop/expense-categories').catch(() => ({ categories: [] })),
    ]);
    hopState.moneyAccounts = acc?.accounts || [];
    hopState.moneyCategories = cats?.categories || [];
  }

  function initDraft(d, txnType) {
    Object.assign(d, {
      receivedAmount: '', paymentChoice: '', paymentRef: '', chequeDate: '',
      amount: '', allocations: {}, openBills: null, expenseCategoryId: '', linkedFromTxnId: null, linkedFromLabel: '',
    });
    const cash = (hopState.moneyAccounts || []).find((a) => a.account_type === 'cash');
    if (cash) d.paymentChoice = `acct:${cash.id}`;
    if (txnType === 7) d.customerId = '';
  }

  function applyData(d, data, duplicate) {
    const choice = data.payment_mode === 'cheque' ? 'cheque'
      : (data.payment_account_id ? `acct:${data.payment_account_id}` : d.paymentChoice);
    Object.assign(d, {
      receivedAmount: data.received_amount != null && Number(data.received_amount) > 0 && !duplicate ? String(data.received_amount) : '',
      paymentChoice: choice || d.paymentChoice,
      paymentRef: duplicate ? '' : (data.payment_ref || ''),
      chequeDate: duplicate ? '' : (data.cheque_date || ''),
      amount: data.total_amount ? String(data.total_amount) : '',
      expenseCategoryId: data.expense_category_id ? String(data.expense_category_id) : '',
      allocations: {},
    });
    if (!duplicate) (data.allocations || []).forEach((a) => { d.allocations[a.txn_id] = String(a.amount); });
    if (data.linked_from_txn_id && duplicate === 'convert') {
      d.linkedFromTxnId = data.linked_from_txn_id;
      d.linkedFromLabel = data.linked_from_label || '';
    }
    if (Number(hopState.manualDoc?.txnType) === 7 && data.party_type === 'expense') d.customerId = '';
  }

  function paymentOptions(d) {
    const accts = hopState.moneyAccounts || [];
    return accts.map((a) => `<option value="acct:${a.id}"${d.paymentChoice === `acct:${a.id}` ? ' selected' : ''}>${esc(a.name)}${a.account_type === 'cash' ? '' : ' (bank)'}</option>`).join('')
      + `<option value="cheque"${d.paymentChoice === 'cheque' ? ' selected' : ''}>Cheque</option>`;
  }

  function paymentFieldsHtml(d) {
    return `
      <div class="hop-vyp-sum-row">
        <span>Payment type</span>
        <select class="hop-vyp-control hop-vyp-control--sm" onchange="hopMoneySet('paymentChoice',this.value,true)">${paymentOptions(d)}</select>
      </div>
      <div class="hop-vyp-sum-row">
        <span>${d.paymentChoice === 'cheque' ? 'Cheque no.' : 'Reference no.'}</span>
        <input class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.paymentRef)}" oninput="hopMoneySet('paymentRef',this.value)" placeholder="${d.paymentChoice === 'cheque' ? '000123' : 'UTR / note'}" />
      </div>
      ${d.paymentChoice === 'cheque' ? `<div class="hop-vyp-sum-row"><span>Cheque date</span>
        <input type="date" class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.chequeDate)}" onchange="hopMoneySet('chequeDate',this.value)" /></div>` : ''}`;
  }

  // ---------- Invoice / bill: received or paid now + balance ----------
  function billPanelHtml(grand) {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const word = BILL_TYPES[Number(md.txnType)];
    if (!word) return d.linkedFromLabel ? `<p class="hop-doc-muted">Converted from ${esc(d.linkedFromLabel)}</p>` : '';
    const got = num(d.receivedAmount);
    return `
      <div class="hop-vyp-money" id="hop-money-panel">
        <div class="hop-vyp-sum-row">
          <label style="display:flex;gap:6px;align-items:center;"><input type="checkbox" ${got > 0 && Math.abs(got - grand) < 0.01 ? 'checked' : ''} onchange="hopMoneyFull(this.checked)" /> Fully ${word.toLowerCase()}</label>
        </div>
        <div class="hop-vyp-sum-row">
          <span>${word}</span>
          <input id="hop-money-received" class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.receivedAmount)}" oninput="hopMoneySet('receivedAmount',this.value);hopMoneyRefreshBalance()" placeholder="0.00" inputmode="decimal" />
        </div>
        ${got > 0 ? paymentFieldsHtml(d) : ''}
        <div class="hop-vyp-sum-row hop-vyp-sum-grand">
          <span>Balance</span><strong id="hop-money-balance">${money(Math.max(0, grand - got))}</strong>
        </div>
        ${d.linkedFromLabel ? `<p class="hop-doc-muted">Converted from ${esc(d.linkedFromLabel)}</p>` : ''}
      </div>`;
  }

  function refreshBalance() {
    const el = document.getElementById('hop-money-balance');
    if (!el || typeof window.hopManualDocCalcGrandPublic !== 'function') return;
    const grand = window.hopManualDocCalcGrandPublic();
    el.textContent = money(Math.max(0, grand - num(hopState.manualDocDraft?.receivedAmount)));
  }

  function setField(key, val, rerender) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    d[key] = val;
    if (key === 'receivedAmount') {
      const show = num(val) > 0;
      const has = !!document.querySelector('#hop-money-panel select');
      if (show !== has) rerender = true;
    }
    if (rerender) rerenderForm();
  }

  function setFull(on) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    d.receivedAmount = on ? String(window.hopManualDocCalcGrandPublic ? window.hopManualDocCalcGrandPublic() : '') : '';
    rerenderForm();
  }

  function rerenderForm() {
    const el = document.getElementById('hop-manual-doc-body');
    if (el && typeof window.hopManualDocRenderBodyPublic === 'function') {
      el.innerHTML = window.hopManualDocRenderBodyPublic(hopState.manualDocCustomers, hopState.manualDocFirmTerms);
    }
  }

  // ---------- Payment-In / Payment-Out / Expense ----------
  function partyOptions(d, customers, placeholder) {
    return `<option value="">${esc(placeholder)}</option>` + (customers || []).map((c) =>
      `<option value="${c.id}"${String(d.customerId) === String(c.id) ? ' selected' : ''}>${esc(c.company || c.name || '—')}</option>`).join('');
  }

  function paymentBodyHtml(customers) {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const ty = Number(md.txnType);
    const isExpense = ty === 7;
    const isEdit = !!md.editId;
    const docNoLabel = ty === 3 ? 'Receipt No.' : (ty === 4 ? 'Payment No.' : 'Expense No.');
    const amount = num(d.amount);
    const linked = Object.values(d.allocations || {}).reduce((s, v) => s + num(v), 0);
    let billsHtml = '';
    if (!isExpense) {
      if (!d.customerId) billsHtml = '<p class="hop-doc-muted">Choose a party to see their unpaid bills.</p>';
      else if (d.openBills == null) billsHtml = '<p class="hop-doc-muted">Loading unpaid bills…</p>';
      else if (!d.openBills.length) billsHtml = '<p class="hop-doc-muted">No unpaid bills. The whole amount is kept as an advance.</p>';
      else {
        billsHtml = `
          <div class="hop-vyp-table-wrap"><table class="hop-vyp-table hop-vyp-table--simple">
            <thead><tr><th></th><th>${ty === 3 ? 'INVOICE' : 'BILL'}</th><th>DATE</th><th>TOTAL</th><th>DUE</th><th>LINK AMOUNT</th></tr></thead>
            <tbody>${d.openBills.map((b) => {
              const v = d.allocations[b.id] || '';
              return `<tr>
                <td><input type="checkbox" ${num(v) > 0 ? 'checked' : ''} onchange="hopMoneyToggleBill(${b.id},this.checked)" /></td>
                <td>${esc(b.txn_number || '—')}</td><td>${esc(b.txn_date || '')}</td>
                <td class="inv-num">${money(b.total_amount)}</td><td class="inv-num">${money(b.due)}</td>
                <td><input class="hop-vyp-control hop-vyp-control--sm" value="${esc(v)}" inputmode="decimal" oninput="hopMoneySetAlloc(${b.id},this.value)" /></td>
              </tr>`;
            }).join('')}</tbody></table></div>
          <div style="display:flex;gap:8px;align-items:center;margin-top:6px;">
            <button type="button" class="hop-vyp-btn hop-vyp-btn--sm" onclick="hopMoneyAutoLink()">Link oldest bills first</button>
            <span class="hop-doc-muted" id="hop-money-linked">Linked ${money(linked)} · Unused ${money(Math.max(0, amount - linked))}</span>
          </div>`;
      }
    }
    const cats = hopState.moneyCategories || [];
    return `
      <div class="hop-vyp-doc">
        <p id="hop-manual-doc-err" class="nx-oc-error"></p>
        ${aiBarHtml()}
        <div class="hop-vyp-top">
          <div class="hop-vyp-top-left">
            ${isExpense ? `<label class="hop-vyp-field"><span>Expense category</span>
              <select class="hop-vyp-control" onchange="hopMoneyPickCategory(this.value)">
                <option value="">— choose —</option>
                ${cats.map((c) => `<option value="${c.id}"${String(d.expenseCategoryId) === String(c.id) ? ' selected' : ''}>${esc(c.name)}</option>`).join('')}
                <option value="__new">+ New category…</option>
              </select></label>
              <label class="hop-vyp-field"><span>Party (optional)</span>
                <select id="hop-manual-customer" class="hop-vyp-control" onchange="hopManualDocSetCustomer(this.value)">${partyOptions(d, customers, 'No party')}</select></label>`
    : `<label class="hop-vyp-field"><span>${ty === 3 ? 'Customer' : 'Vendor'} *</span>
                <select id="hop-manual-customer" class="hop-vyp-control" onchange="hopManualDocSetCustomer(this.value);hopMoneyLoadBills()">${partyOptions(d, customers, 'Search / select party')}</select></label>`}
          </div>
          <div class="hop-vyp-top-right">
            <label class="hop-vyp-field"><span>${docNoLabel}</span>
              <input type="text" id="hop-manual-txn-number" class="hop-vyp-control" value="${esc(d.txnNumber || '')}" oninput="hopManualDocSetTxnNumber(this.value)" placeholder="Auto serial" /></label>
            <label class="hop-vyp-field"><span>Date</span>
              <input type="date" class="hop-vyp-control" value="${esc(d.txnDate)}" onchange="hopManualDocSetField('txnDate',this.value)" /></label>
          </div>
        </div>
        <div class="hop-vyp-bottom">
          <div class="hop-vyp-bottom-left">
            ${billsHtml}
            <label class="hop-vyp-field"><span>Description</span>
              <textarea class="hop-vyp-control" rows="2" oninput="hopManualDocSetField('notes',this.value)">${esc(d.notes || '')}</textarea></label>
          </div>
          <div class="hop-vyp-bottom-right">
            <div class="hop-vyp-sum-row hop-vyp-sum-grand">
              <span>${ty === 3 ? 'Received' : 'Paid'}</span>
              <input class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.amount)}" inputmode="decimal" placeholder="0.00" oninput="hopMoneySet('amount',this.value);hopMoneyRefreshLinked()" />
            </div>
            ${paymentFieldsHtml(d)}
          </div>
        </div>
        <div class="hop-vyp-foot">
          ${isEdit ? '<button type="button" class="hop-vyp-btn hop-vyp-btn--danger" onclick="hopManualDocDelete()">Delete</button>' : '<span></span>'}
          <div class="hop-vyp-foot-right">
            <button type="button" class="hop-vyp-btn" onclick="hopCloseManualDocOverlay()">Cancel</button>
            <button type="button" class="hop-vyp-btn hop-vyp-btn--primary" onclick="hopManualDocSave()">${isEdit ? 'Update' : 'Save'}</button>
          </div>
        </div>
      </div>`;
  }

  async function loadBills() {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft;
    const ty = Number(md.txnType);
    if (!d || !PAYMENT_TYPES.has(ty) || ty === 7) return;
    d.openBills = d.customerId ? null : [];
    rerenderForm();
    if (!d.customerId) return;
    const q = new URLSearchParams({
      payment_type: ty, party_type: ty === 3 ? 'customer' : 'vendor', party_id: d.customerId,
    });
    if (md.editId) q.set('payment_txn_id', md.editId);
    try {
      const out = await hopApi(`/api/v1/hop/open-bills?${q}`);
      d.openBills = out?.bills || [];
    } catch (e) {
      d.openBills = [];
      toast(e?.message || 'Could not load bills', 'err');
    }
    if (!md.editId && !d.openBillsSeenFor) d.allocations = {};
    rerenderForm();
  }

  function refreshLinked() {
    const d = hopState.manualDocDraft;
    const el = document.getElementById('hop-money-linked');
    if (!d || !el) return;
    const linked = Object.values(d.allocations || {}).reduce((s, v) => s + num(v), 0);
    el.textContent = `Linked ${money(linked)} · Unused ${money(Math.max(0, num(d.amount) - linked))}`;
  }

  function setAlloc(billId, val) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    d.allocations[billId] = val;
    refreshLinked();
  }

  function toggleBill(billId, on) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    const b = (d.openBills || []).find((x) => x.id === billId);
    if (!on) { delete d.allocations[billId]; rerenderForm(); return; }
    const used = Object.entries(d.allocations).filter(([k]) => Number(k) !== billId).reduce((s, [, v]) => s + num(v), 0);
    let left = num(d.amount) - used;
    if (num(d.amount) <= 0) { // no amount typed yet: take the full due and grow the amount
      left = b.due;
      d.amount = String(Math.round((used + b.due) * 100) / 100);
    }
    d.allocations[billId] = String(Math.max(0, Math.min(b.due, left)).toFixed(2));
    rerenderForm();
  }

  function autoLink() {
    // Smart match: oldest bills first, up to the amount received / paid.
    const d = hopState.manualDocDraft;
    if (!d?.openBills) return;
    let left = num(d.amount);
    if (left <= 0) {
      left = d.openBills.reduce((s, b) => s + b.due, 0);
      d.amount = String(Math.round(left * 100) / 100);
    }
    d.allocations = {};
    for (const b of d.openBills) {
      if (left <= 0.009) break;
      const take = Math.min(b.due, left);
      d.allocations[b.id] = take.toFixed(2);
      left -= take;
    }
    rerenderForm();
  }

  async function pickCategory(val) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    if (val !== '__new') { d.expenseCategoryId = val; return; }
    const name = (window.prompt('New expense category (e.g. Rent, Salary)') || '').trim();
    if (!name) { rerenderForm(); return; }
    try {
      const c = await hopApi('/api/v1/hop/expense-categories', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
      });
      await loadRefs();
      d.expenseCategoryId = String(c.id);
    } catch (e) { toast(e?.message || 'Could not add category', 'err'); }
    rerenderForm();
  }

  // ---------- AI: read a bill photo / PDF into the form ----------
  function aiBarHtml() {
    const ty = Number(hopState.manualDoc?.txnType);
    if (ty !== 2 && ty !== 7) return '';
    const st = hopState.manualDocDraft?.aiStatus || '';
    return `
      <div class="hop-vyp-ai" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px;padding:10px 12px;border-radius:12px;background:rgba(124,58,237,.08);">
        <input type="file" id="hop-ai-bill" accept="application/pdf,image/*" hidden onchange="hopMoneyReadBill(this.files[0])" />
        <button type="button" class="hop-vyp-btn hop-vyp-btn--sm" onclick="document.getElementById('hop-ai-bill').click()">✨ Read bill with AI</button>
        <span class="hop-doc-muted" style="flex:1;min-width:200px;">${st ? esc(st) : 'Upload a photo or PDF of the bill — AI fills the form, you check and save.'}</span>
      </div>`;
  }

  async function readBill(file) {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft;
    if (!file || !d) return;
    d.aiStatus = 'Reading the bill…';
    rerenderForm();
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetchWithAuth('/api/v1/hop/ai/read-bill', { method: 'POST', body: fd });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || out.success === false) throw new Error(out.error?.message || 'Could not read the bill');
      const r = out.data || {};
      if (r.vendor_id) d.customerId = String(r.vendor_id);
      if (r.bill_date) d.txnDate = r.bill_date;
      if (Number(md.txnType) === 2) {
        if (r.bill_no) { d.txnNumber = r.bill_no; d.txnNumberManual = true; }
        if (r.lines?.length) {
          d.lines = r.lines.map((l) => ({
            item_name: l.item_name, description: '', qty: String(l.qty), unit: l.unit || 'Pcs',
            rate: String(l.rate), discount_pct: '0', tax_pct: String(l.tax_pct || 0), hsn: l.hsn || '',
          })).concat([typeof hopEmptyDocLine === 'function' ? hopEmptyDocLine() : { item_name: '', qty: '', rate: '' }]);
        }
      } else {
        if (r.total) d.amount = String(r.total);
        if (r.expense_category_id) d.expenseCategoryId = String(r.expense_category_id);
        if (r.bill_no) d.notes = `Bill ${r.bill_no}${r.party_name ? ` · ${r.party_name}` : ''}`;
      }
      const checks = r.checks || [];
      d.aiStatus = `Read ${r.lines?.length || 0} item(s)${r.total ? `, total ₹${Number(r.total).toLocaleString('en-IN')}` : ''}.`
        + (checks.length ? ` Check: ${checks.join(' ')}` : ' Check the details, then save.');
    } catch (e) {
      d.aiStatus = e?.message || 'Could not read the bill';
    }
    rerenderForm();
  }

  // ---------- Building the save payload ----------
  function paymentFields(d) {
    if (d.paymentChoice === 'cheque') {
      return { payment_mode: 'cheque', payment_ref: d.paymentRef || '', cheque_date: d.chequeDate || '' };
    }
    const id = Number(String(d.paymentChoice || '').replace('acct:', '')) || null;
    const acct = (hopState.moneyAccounts || []).find((a) => a.id === id);
    return { payment_mode: acct?.account_type === 'cash' ? 'cash' : 'bank', payment_account_id: id, payment_ref: d.paymentRef || '' };
  }

  /** Returns {payload} or {error}. Called by hopManualDocSave for payment-type docs. */
  function paymentPayload() {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const ty = Number(md.txnType);
    const amount = num(d.amount);
    const partyId = Number(d.customerId || 0);
    if (amount <= 0) return { error: 'Enter the amount' };
    if (ty === 7 && !partyId && !d.expenseCategoryId) return { error: 'Choose an expense category or a party' };
    if (ty !== 7 && !partyId) return { error: ty === 3 ? 'Select a customer' : 'Select a vendor' };
    const partyKind = ty === 3 ? 'customer' : (ty === 4 ? 'vendor' : (hopState.manualDocPartyKind || 'vendor'));
    const payload = {
      txn_type: ty, txn_date: d.txnDate, amount, total_amount: amount,
      notes: String(d.notes || '').trim() || undefined,
      ...paymentFields(d),
    };
    if (partyId) {
      payload.party_type = partyKind;
      payload.party_id = partyId;
      if (partyKind === 'customer') payload.customer_id = partyId; else payload.vendor_id = partyId;
    } else {
      payload.party_id = 0;
    }
    if (ty === 7) payload.expense_category_id = d.expenseCategoryId ? Number(d.expenseCategoryId) : null;
    if (ty !== 7) {
      payload.allocations = Object.entries(d.allocations || {})
        .filter(([, v]) => num(v) > 0).map(([k, v]) => ({ txn_id: Number(k), amount: num(v) }));
    }
    const txnNum = String(d.txnNumber || '').trim();
    if (txnNum) payload.txn_number = txnNum;
    return { payload };
  }

  /** Extra fields for invoices / bills / returns. */
  function billFields() {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const out = {};
    if (BILL_TYPES[Number(md.txnType)]) {
      out.received_amount = num(d.receivedAmount);
      if (out.received_amount > 0) Object.assign(out, paymentFields(d));
      else Object.assign(out, { payment_mode: '', payment_account_id: null });
    }
    if (d.linkedFromTxnId && !md.editId) out.linked_from_txn_id = d.linkedFromTxnId;
    return out;
  }

  // ---------- Preview: Convert to … and linked payments ----------
  async function previewExtras(partyTxnId) {
    if (!partyTxnId) return;
    let info;
    try { info = await hopApi(`/api/v1/hop/party-transactions/${partyTxnId}/links`); } catch (e) { return; }
    const foot = document.querySelector('#hop-party-txn-overlay .hop-doc-preview-foot');
    const body = document.getElementById('hop-doc-preview-body');
    if (foot && info.convert_to?.length) {
      const wrap = document.createElement('span');
      wrap.className = 'hop-money-convert';
      wrap.innerHTML = info.convert_to.map((c) =>
        `<button type="button" class="nx-btn" onclick="hopMoneyConvert(${partyTxnId},${c.txn_type})">Convert to ${esc(c.label)}</button>`).join('');
      foot.insertBefore(wrap, foot.firstChild);
    }
    if (body && info.links?.length) {
      const rows = info.links.map((l) => Number(l.payment_txn_id) === Number(partyTxnId)
        ? `<tr><td>${esc(l.bill_number || '—')}</td><td>${esc((l.bill_date || '').slice(0, 10))}</td><td class="inv-num">${money(l.amount)}</td></tr>`
        : `<tr><td>${esc(l.payment_number || '—')}</td><td>${esc((l.payment_date || '').slice(0, 10))}</td><td class="inv-num">${money(l.amount)}</td></tr>`).join('');
      const isPayment = Number(info.links[0].payment_txn_id) === Number(partyTxnId);
      body.insertAdjacentHTML('beforeend', `
        <div class="hop-money-links" style="margin:14px 0;">
          <strong>${isPayment ? 'Bills cleared by this payment' : 'Payments on this bill'}</strong>
          <table class="hop-vyp-table hop-vyp-table--simple" style="margin-top:6px;">
            <thead><tr><th>${isPayment ? 'Bill' : 'Payment'}</th><th>Date</th><th>Amount</th></tr></thead><tbody>${rows}</tbody></table>
        </div>`);
    }
  }

  async function convert(partyTxnId, toType) {
    try {
      const draft = await hopApi(`/api/v1/hop/party-transactions/${partyTxnId}/convert?to=${toType}`);
      if (typeof hopClosePartyTxnDetail === 'function') hopClosePartyTxnDetail();
      window.hopOpenManualDocFromDraft(draft);
    } catch (e) { toast(e?.message || 'Could not convert', 'err'); }
  }

  // ---------- Cash & Bank ----------
  async function renderCashBank(mount) {
    let data; let entries; let cheques;
    try {
      [data, entries, cheques] = await Promise.all([
        hopApi('/api/v1/hop/bank-accounts'),
        hopApi('/api/v1/hop/bank-entries' + (hopState.cbAccount ? `?account_id=${hopState.cbAccount}` : '')),
        hopApi('/api/v1/hop/cheques'),
      ]);
    } catch (e) {
      mount.innerHTML = hopModuleShell('Books', 'Cash & Bank', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    const accounts = data?.accounts || [];
    hopState._bankAccounts = accounts;
    const byId = Object.fromEntries(accounts.map((a) => [a.id, a]));
    const cashTotal = accounts.filter((a) => a.account_type === 'cash').reduce((s, a) => s + Number(a.current_balance || 0), 0);
    const bankTotal = accounts.filter((a) => a.account_type !== 'cash').reduce((s, a) => s + Number(a.current_balance || 0), 0);
    const open = (cheques?.cheques || []).filter((c) => c.status === 'open');
    const openIn = open.filter((c) => c.direction === 'in').reduce((s, c) => s + Number(c.amount || 0), 0);
    const openOut = open.filter((c) => c.direction === 'out').reduce((s, c) => s + Number(c.amount || 0), 0);
    const sel = hopState.cbAccount ? byId[hopState.cbAccount] : null;
    const tab = hopState.cbTab || 'accounts';
    const accRows = accounts.map((a) => `<tr class="${hopState.cbAccount === a.id ? 'is-active' : ''}" style="cursor:pointer;" onclick="hopMoneyPickAccount(${a.id})">
        <td>${esc(a.name)}</td><td>${a.account_type === 'cash' ? 'Cash' : 'Bank'}</td>
        <td>${esc(a.account_number || '')}</td><td class="inv-num">${money(a.current_balance)}</td></tr>`).join('');
    const entRows = (entries || []).map((e) => {
      const isIn = e.entry_type === 'in';
      return `<tr><td>${esc(e.entry_date)}</td><td>${esc(byId[e.account_id]?.name || e.account_id)}</td>
        <td>${e.entry_type === 'transfer' ? 'Transfer out' : (isIn ? 'Money in' : 'Money out')}</td>
        <td>${esc(e.notes || '')}</td><td>${esc(e.reference || '')}</td>
        <td class="inv-num" style="color:${isIn ? '#16a34a' : '#dc2626'};">${isIn ? '+' : '−'}${money(e.amount)}</td></tr>`;
    }).join('');
    const chqRows = (cheques?.cheques || []).map((c) => `<tr>
        <td>${esc(c.cheque_date || '')}</td><td>${esc(c.party_name || '')}</td><td>${esc(c.cheque_no || '—')}</td>
        <td>${c.direction === 'in' ? 'Received' : 'Issued'}</td><td class="inv-num">${money(c.amount)}</td>
        <td>${c.status === 'open' ? 'Open' : `Closed ${esc(c.closed_on || '')} · ${esc(byId[c.account_id]?.name || '')}`}</td>
        <td>${c.status === 'open'
          ? `<button type="button" class="nx-btn nx-btn-primary" onclick="hopMoneyCloseCheque(${c.id},'${c.direction}')">${c.direction === 'in' ? 'Deposit' : 'Withdraw'}</button>`
          : `<button type="button" class="nx-btn" onclick="hopMoneyReopenCheque(${c.id})">Reopen</button>`}</td></tr>`).join('');
    const body = `
      <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
      ${hopTxCards([
        { label: 'Cash in hand', value: money(cashTotal), tone: cashTotal < 0 ? 'overdue' : 'paid' },
        { label: 'Bank balance', value: money(bankTotal), tone: bankTotal < 0 ? 'overdue' : 'total' },
        { label: 'Cheques to deposit', value: money(openIn), tone: 'unpaid' },
        { label: 'Cheques issued (open)', value: money(openOut), tone: 'unpaid' },
      ])}
      <div class="hop-books-actions">
        <button type="button" class="nx-btn ${tab === 'accounts' ? 'nx-btn-primary' : ''}" onclick="hopMoneyTab('accounts')">Accounts & statement</button>
        <button type="button" class="nx-btn ${tab === 'cheques' ? 'nx-btn-primary' : ''}" onclick="hopMoneyTab('cheques')">Cheques${open.length ? ` (${open.length})` : ''}</button>
        <span style="flex:1"></span>
        <button type="button" class="nx-btn" onclick="hopShowBankAccountForm()">+ Bank account</button>
        <button type="button" class="nx-btn" onclick="hopMoneyEntryForm('in')">Deposit / money in</button>
        <button type="button" class="nx-btn" onclick="hopMoneyEntryForm('out')">Withdraw / money out</button>
        <button type="button" class="nx-btn" onclick="hopMoneyEntryForm('transfer')">Transfer</button>
      </div>
      ${tab === 'cheques'
        ? hopTable(['Cheque date', 'Party', 'Cheque no.', 'Type', 'Amount', 'Status', ''], chqRows, 7, { label: 'Cheques', count: (cheques?.cheques || []).length })
        : `${hopTable(['Account', 'Type', 'Number', 'Balance'], accRows, 4, { label: 'Accounts — click one for its statement', count: accounts.length })}
           ${sel ? `<p style="margin:8px 0;"><strong>${esc(sel.name)}</strong> statement · opening ${money(sel.opening_balance)} · now ${money(sel.current_balance)}
             <button type="button" class="nx-btn" onclick="hopMoneyPickAccount(0)">Show all accounts</button></p>` : ''}
           ${hopTable(['Date', 'Account', 'Type', 'Details', 'Reference', 'Amount'], entRows, 6, { label: sel ? `${sel.name} entries` : 'All entries', count: (entries || []).length })}`}`;
    mount.innerHTML = hopModuleShell('Books', 'Cash & Bank', 'Cash in hand, bank accounts, transfers and cheques', '', body);
  }

  function entryForm(kind) {
    const slot = document.getElementById('hop-form-slot');
    if (!slot) return;
    const opts = (hopState._bankAccounts || []).map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
    const title = { in: 'Deposit / money in', out: 'Withdraw / money out', transfer: 'Transfer between accounts' }[kind];
    slot.classList.remove('hidden');
    slot.innerHTML = `
      <strong>${title}</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>${kind === 'transfer' ? 'From account' : 'Account'}<select id="f-me-acct">${opts}</select></label>
        ${kind === 'transfer' ? `<label>To account<select id="f-me-to">${opts}</select></label>` : ''}
        <label>Amount<input id="f-me-amt" type="number" min="0" step="0.01" /></label>
        <label>Date<input id="f-me-date" type="date" value="${new Date().toISOString().slice(0, 10)}" /></label>
        <label>Reference<input id="f-me-ref" /></label>
        <label>Notes<input id="f-me-notes" placeholder="${kind === 'in' ? 'Capital added, loan received…' : (kind === 'out' ? 'Drawings, bank charges…' : '')}" /></label>
      </div>
      <p id="f-me-err" class="nx-oc-error"></p>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopMoneySaveEntry('${kind}')">Save</button>
        <button type="button" class="nx-btn" onclick="document.getElementById('hop-form-slot').classList.add('hidden')">Cancel</button>
      </div>`;
  }

  async function saveEntry(kind) {
    const v = (id) => document.getElementById(id)?.value;
    try {
      await hopApi('/api/v1/hop/bank-entries', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_id: Number(v('f-me-acct')), entry_type: kind, amount: Number(v('f-me-amt')),
          counterparty_account_id: kind === 'transfer' ? Number(v('f-me-to')) : undefined,
          entry_date: v('f-me-date'), reference: v('f-me-ref'), notes: v('f-me-notes'),
        }),
      });
      toast('Saved');
      openHopView('banking');
    } catch (e) {
      const el = document.getElementById('f-me-err');
      if (el) el.textContent = e?.message || 'Could not save';
    }
  }

  async function closeCheque(id, direction) {
    const accts = (hopState._bankAccounts || []).filter((a) => a.account_type !== 'cash').concat(
      (hopState._bankAccounts || []).filter((a) => a.account_type === 'cash'));
    if (!accts.length) { toast('Add a bank account first', 'err'); return; }
    const list = accts.map((a, i) => `${i + 1}. ${a.name}`).join('\n');
    const pick = window.prompt(`${direction === 'in' ? 'Deposit to' : 'Withdraw from'} which account?\n${list}`, '1');
    const acct = accts[Number(pick) - 1];
    if (!acct) return;
    try {
      await hopApi(`/api/v1/hop/cheques/${id}/close`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_id: acct.id, date: new Date().toISOString().slice(0, 10) }),
      });
      toast(direction === 'in' ? 'Cheque deposited' : 'Cheque withdrawn');
      openHopView('banking');
    } catch (e) { toast(e?.message || 'Could not close the cheque', 'err'); }
  }

  async function reopenCheque(id) {
    try {
      await hopApi(`/api/v1/hop/cheques/${id}/reopen`, { method: 'POST' });
      toast('Cheque reopened');
      openHopView('banking');
    } catch (e) { toast(e?.message || 'Could not reopen', 'err'); }
  }

  window.hopMoney_ = {
    loadRefs, initDraft, applyData, billPanelHtml, paymentBodyHtml, paymentPayload, billFields,
    isPaymentType: (t) => PAYMENT_TYPES.has(Number(t)), previewExtras, refreshBalance, loadBills, aiBarHtml,
  };
  Object.assign(window, {
    hopMoneySet: setField,
    hopMoneyFull: setFull,
    hopMoneyRefreshBalance: refreshBalance,
    hopMoneyRefreshLinked: refreshLinked,
    hopMoneyLoadBills: loadBills,
    hopMoneySetAlloc: setAlloc,
    hopMoneyToggleBill: toggleBill,
    hopMoneyAutoLink: autoLink,
    hopMoneyPickCategory: pickCategory,
    hopMoneyReadBill: readBill,
    hopMoneyConvert: convert,
    hopRenderCashBank: renderCashBank,
    hopMoneyTab: (t) => { hopState.cbTab = t; openHopView('banking'); },
    hopMoneyPickAccount: (id) => { hopState.cbAccount = id || null; hopState.cbTab = 'accounts'; openHopView('banking'); },
    hopMoneyEntryForm: entryForm,
    hopMoneySaveEntry: saveEntry,
    hopMoneyCloseCheque: closeCheque,
    hopMoneyReopenCheque: reopenCheque,
  });
})();
