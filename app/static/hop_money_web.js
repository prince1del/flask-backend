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
  const STATES = [['01', 'Jammu and Kashmir'], ['02', 'Himachal Pradesh'], ['03', 'Punjab'], ['04', 'Chandigarh'],
    ['05', 'Uttarakhand'], ['06', 'Haryana'], ['07', 'Delhi'], ['08', 'Rajasthan'], ['09', 'Uttar Pradesh'], ['10', 'Bihar'],
    ['11', 'Sikkim'], ['12', 'Arunachal Pradesh'], ['13', 'Nagaland'], ['14', 'Manipur'], ['15', 'Mizoram'], ['16', 'Tripura'],
    ['17', 'Meghalaya'], ['18', 'Assam'], ['19', 'West Bengal'], ['20', 'Jharkhand'], ['21', 'Odisha'], ['22', 'Chhattisgarh'],
    ['23', 'Madhya Pradesh'], ['24', 'Gujarat'], ['26', 'Dadra & Nagar Haveli and Daman & Diu'], ['27', 'Maharashtra'],
    ['29', 'Karnataka'], ['30', 'Goa'], ['31', 'Lakshadweep'], ['32', 'Kerala'], ['33', 'Tamil Nadu'], ['34', 'Puducherry'],
    ['35', 'Andaman and Nicobar Islands'], ['36', 'Telangana'], ['37', 'Andhra Pradesh'], ['38', 'Ladakh']];
  const PAYMENT_TYPES = new Set([3, 4, 7, 29]);
  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const money = (v) => (typeof hopMoney === 'function' ? hopMoney(v) : `₹${Number(v || 0).toFixed(2)}`);
  const num = (v) => { const n = Number(String(v ?? '').replace(/,/g, '')); return Number.isFinite(n) ? n : 0; };
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };

  // ---------- Reference data for the form ----------
  async function loadRefs() {
    const [acc, cats, inc] = await Promise.all([
      hopApi('/api/v1/hop/bank-accounts').catch(() => ({ accounts: [] })),
      hopApi('/api/v1/hop/expense-categories').catch(() => ({ categories: [] })),
      hopApi('/api/v1/hop/expense-categories?kind=income').catch(() => ({ categories: [] })),
    ]);
    hopState.moneyIncomeCategories = inc?.categories || [];
    try { hopState.moneyGodowns = (await hopApi('/api/v1/hop/godowns')).godowns || []; } catch (_) { hopState.moneyGodowns = []; }
    hopState.moneyAccounts = acc?.accounts || [];
    hopState.moneyCategories = cats?.categories || [];
  }

  function initDraft(d, txnType) {
    Object.assign(d, {
      receivedAmount: '', paymentChoice: '', paymentRef: '', chequeDate: '',
      amount: '', allocations: {}, openBills: null, expenseCategoryId: '', linkedFromTxnId: null, linkedFromLabel: '',
      placeOfSupply: '', reverseCharge: false,
      charges: [], poNumber: '', poDate: '', txnTime: '', tcsPct: '', tdsPct: '', godownId: '',
    });
    const cash = (hopState.moneyAccounts || []).find((a) => a.account_type === 'cash');
    if (cash) d.paymentChoice = `acct:${cash.id}`;
    if (txnType === 7 || txnType === 29) d.customerId = '';
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
      placeOfSupply: data.place_of_supply || '',
      reverseCharge: !!data.reverse_charge,
      charges: (data.charges || []).map((c) => ({ name: c.name || '', amount: String(c.amount ?? ''), tax_pct: String(c.tax_pct ?? 0) })),
      poNumber: data.po_number || '',
      poDate: data.po_date || '',
      txnTime: duplicate ? '' : (data.txn_time || ''),
      tcsPct: Number(data.tcs_pct || 0) > 0 ? String(data.tcs_pct) : '',
      tdsPct: Number(data.tds_pct || 0) > 0 ? String(data.tds_pct) : '',
      godownId: data.godown_id ? String(data.godown_id) : '',
      allocations: {},
    });
    if (!duplicate) (data.allocations || []).forEach((a) => { d.allocations[a.txn_id] = String(a.amount); });
    if (data.linked_from_txn_id && duplicate === 'convert') {
      d.linkedFromTxnId = data.linked_from_txn_id;
      d.linkedFromLabel = data.linked_from_label || '';
    }
    if ([7, 29].includes(Number(hopState.manualDoc?.txnType)) && data.party_type === 'expense') d.customerId = '';
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

  // ---------- Bill extras: additional charges, PO no/date, time, TCS ----------
  const TCS_TYPES = new Set([1, 2]);
  const PO_TYPES = new Set([1, 65, 30]);

  function chargesTotal(d) {
    return (d.charges || []).reduce((s, c) => {
      const a = num(c.amount);
      return String(c.name || '').trim() && a > 0 ? s + Math.round(a * (1 + num(c.tax_pct) / 100) * 100) / 100 : s;
    }, 0);
  }

  function withExtras(d, base) {
    const ty = Number(hopState.manualDoc?.txnType);
    const before = Math.round((base + chargesTotal(d)) * 100) / 100;
    const tcs = TCS_TYPES.has(ty) ? Math.round(before * num(d.tcsPct)) / 100 : 0;
    return Math.round((before + tcs) * 100) / 100;
  }

  function extrasHtml() {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const ty = Number(md.txnType);
    const rows = (d.charges || []).map((c, i) => `
      <div class="hop-vyp-sum-row" style="gap:6px;">
        <input class="hop-vyp-control hop-vyp-control--sm" style="flex:1.4;" value="${esc(c.name)}" placeholder="Charge name" oninput="hopMoneyCharge(${i},'name',this.value)" />
        <input class="hop-vyp-control hop-vyp-control--sm" style="flex:1;" value="${esc(c.amount)}" placeholder="₹" inputmode="decimal" oninput="hopMoneyCharge(${i},'amount',this.value)" />
        <input class="hop-vyp-control hop-vyp-control--sm" style="flex:.7;" value="${esc(c.tax_pct)}" placeholder="GST %" inputmode="decimal" oninput="hopMoneyCharge(${i},'tax_pct',this.value)" />
        <button type="button" class="hop-vyp-trash" onclick="hopMoneyChargeRemove(${i})" title="Remove">✕</button>
      </div>`).join('');
    return `
      <div class="hop-vyp-money" id="hop-extras-panel">
        ${BILL_TYPES[ty] && (hopState.moneyGodowns || []).length > 1 ? `<div class="hop-vyp-sum-row"><span>Godown</span>
          <select class="hop-vyp-control hop-vyp-control--sm" onchange="hopMoneySet('godownId',this.value)">
            ${(hopState.moneyGodowns || []).map((g) => `<option value="${g.is_default ? '' : g.id}"${String(d.godownId || '') === String(g.is_default ? '' : g.id) ? ' selected' : ''}>${esc(g.name)}</option>`).join('')}
          </select></div>` : ''}
        <div class="hop-vyp-sum-row"><strong>Additional charges</strong></div>
        ${rows}
        <div class="hop-vyp-sum-row" style="gap:4px;flex-wrap:wrap;">
          ${['Freight', 'Packing', 'Labour'].map((n) => `<button type="button" class="nx-btn" onclick="hopMoneyChargeAdd('${n}')">+ ${n}</button>`).join('')}
          <button type="button" class="nx-btn" onclick="hopMoneyChargeAdd('')">+ Other</button>
        </div>
        ${PO_TYPES.has(ty) ? `<div class="hop-vyp-sum-row"><span>PO no.</span>
          <input class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.poNumber)}" oninput="hopMoneySet('poNumber',this.value)" /></div>
          <div class="hop-vyp-sum-row"><span>PO date</span>
          <input type="date" class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.poDate)}" onchange="hopMoneySet('poDate',this.value)" /></div>` : ''}
        <div class="hop-vyp-sum-row"><span>Time</span>
          <input type="time" class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.txnTime)}" onchange="hopMoneySet('txnTime',this.value)" /></div>
        ${TCS_TYPES.has(ty) ? `<div class="hop-vyp-sum-row"><span>TCS % <small class="hop-doc-muted">(added to total)</small></span>
          <input class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.tcsPct)}" inputmode="decimal" placeholder="0" oninput="hopMoneySet('tcsPct',this.value);hopMoneyRefreshTotal()" /></div>
          <div class="hop-vyp-sum-row"><span>TDS % <small class="hop-doc-muted">(${ty === 2 ? 'we deduct from the vendor' : 'customer deducts'})</small></span>
          <input class="hop-vyp-control hop-vyp-control--sm" value="${esc(d.tdsPct)}" inputmode="decimal" placeholder="0" oninput="hopMoneySet('tdsPct',this.value)" /></div>` : ''}
      </div>`;
  }

  function extrasFields() {
    const ty = Number(hopState.manualDoc?.txnType);
    const d = hopState.manualDocDraft || {};
    if (PAYMENT_TYPES.has(ty)) return {};
    const out = {
      charges: (d.charges || []).filter((c) => String(c.name || '').trim() && num(c.amount) > 0)
        .map((c) => ({ name: String(c.name).trim(), amount: num(c.amount), tax_pct: num(c.tax_pct) })),
      txn_time: d.txnTime || '',
    };
    if (PO_TYPES.has(ty)) Object.assign(out, { po_number: String(d.poNumber || '').trim(), po_date: d.poDate || '' });
    if (TCS_TYPES.has(ty)) { out.tcs_pct = num(d.tcsPct); out.tds_pct = num(d.tdsPct); }
    return out;
  }

  function refreshTotal() {
    const el = document.getElementById('hop-manual-doc-grand');
    if (el && typeof window.hopManualDocCalcGrandPublic === 'function') {
      el.textContent = typeof hopPreviewMoney === 'function' ? hopPreviewMoney(window.hopManualDocCalcGrandPublic()) : money(window.hopManualDocCalcGrandPublic());
    }
    refreshBalance();
  }

  // ---------- Invoice / bill: received or paid now + balance ----------
  function billPanelHtml(grand) {
    return extrasHtml() + billMoneyHtml(grand);
  }

  function billMoneyHtml(grand) {
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
        <div class="hop-vyp-sum-row">
          <span>Place of supply</span>
          <select class="hop-vyp-control hop-vyp-control--sm" onchange="hopMoneySet('placeOfSupply',this.value)">
            <option value="">From party (auto)</option>
            ${STATES.map(([c, n]) => `<option value="${c}"${d.placeOfSupply === c ? ' selected' : ''}>${esc(n)} (${c})</option>`).join('')}
          </select>
        </div>
        ${Number(md.txnType) === 2 ? `<div class="hop-vyp-sum-row"><label style="display:flex;gap:6px;align-items:center;">
          <input type="checkbox" ${d.reverseCharge ? 'checked' : ''} onchange="hopMoneySet('reverseCharge',this.checked)" />
          Reverse charge (we pay the GST)</label></div>` : ''}
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
    const isIncome = ty === 29;
    const isExpense = ty === 7 || isIncome;   // category-based: no bills to link
    const isEdit = !!md.editId;
    const docNoLabel = ty === 3 ? 'Receipt No.' : (ty === 4 ? 'Payment No.' : (isIncome ? 'Receipt No.' : 'Expense No.'));
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
    const cats = (isIncome ? hopState.moneyIncomeCategories : hopState.moneyCategories) || [];
    return `
      <div class="hop-vyp-doc">
        <p id="hop-manual-doc-err" class="nx-oc-error"></p>
        ${aiBarHtml()}
        <div class="hop-vyp-top">
          <div class="hop-vyp-top-left">
            ${isExpense ? `<label class="hop-vyp-field"><span>${isIncome ? 'Income category' : 'Expense category'}</span>
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
              <span>${ty === 3 || isIncome ? 'Received' : 'Paid'}</span>
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
    if (!d || !PAYMENT_TYPES.has(ty) || ty === 7 || ty === 29) return;
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
    const income = Number(hopState.manualDoc?.txnType) === 29;
    const name = (window.prompt(income ? 'New income category (e.g. Rent received, Interest)' : 'New expense category (e.g. Rent, Salary)') || '').trim();
    if (!name) { rerenderForm(); return; }
    try {
      const c = await hopApi('/api/v1/hop/expense-categories', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, kind: income ? 'income' : 'expense' }),
      });
      await loadRefs();
      d.expenseCategoryId = String(c.id);
    } catch (e) { toast(e?.message || 'Could not add category', 'err'); }
    rerenderForm();
  }

  // ---------- AI: read a bill photo / PDF into the form ----------
  function aiBarHtml() {
    const ty = Number(hopState.manualDoc?.txnType);
    const kind = scanKind(ty);
    if (!kind || hopState.manualDoc?.editId) return '';
    const st = hopState.manualDocDraft?.aiStatus || '';
    const hint = kind === 'purchase'
      ? 'Upload a photo or PDF of the bill — AI fills the form, you check and save.'
      : 'Photo or PDF of any bill, order or handwritten note (Hindi / English) — AI fills customer and items; you check and save.';
    return `
      <div class="hop-vyp-ai" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:0 0 12px;padding:10px 12px;border-radius:12px;background:rgba(124,58,237,.08);">
        <input type="file" id="hop-ai-bill" accept="application/pdf,image/*" capture="environment" hidden onchange="hopMoneyReadBill(this.files[0])" />
        <button type="button" class="hop-vyp-btn hop-vyp-btn--sm" onclick="document.getElementById('hop-ai-bill').click()">✨ ${kind === 'purchase' ? 'Read bill with AI' : 'Scan bill / note with AI'}</button>
        <span class="hop-doc-muted" style="flex:1;min-width:200px;">${st ? esc(st) : hint}</span>
      </div>`;
  }

  function scanKind(ty) {
    if ([2, 7, 66].includes(ty)) return 'purchase';
    if ([1, 65, 30].includes(ty)) return 'sale';
    if ([27, 83].includes(ty)) return 'quote';
    return '';
  }

  /** Sale invoice / quotation: AI-read lines go into the form (standard lines or commercial sections). */
  function applySaleScan(d, r) {
    if (r.customer_id) d.customerId = String(r.customer_id);
    if (r.date) d.txnDate = r.date;
    if (r.notes) d.notes = [d.notes, r.notes].filter(Boolean).join('\n');
    const rows = (r.lines || []).map((l) => ({
      item_name: l.item_name, description: l.description || '', qty: String(l.qty || 1), unit: l.unit || 'PCS',
      rate: l.rate ? String(l.rate) : '', discount_pct: String(l.discount_pct || 0), tax_pct: String(l.tax_pct || 0),
      hsn: l.hsn || '', product_id: l.product_id || undefined,
    }));
    const blank = typeof hopEmptyDocLine === 'function' ? hopEmptyDocLine() : { item_name: '', qty: '', rate: '' };
    if (!rows.length) return;
    if (Array.isArray(d.sections) && d.sections.length) d.sections = [{ title: d.sections[0].title || 'Shortlisted-1', lines: rows.concat([blank]) }];
    else d.lines = rows.concat([blank]);
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
      const kind = scanKind(Number(md.txnType));
      fd.append('kind', kind || 'purchase');
      const res = await fetchWithAuth('/api/v1/hop/ai/read-bill', { method: 'POST', body: fd });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || out.success === false) throw new Error(out.error?.message || 'Could not read the bill');
      const r = out.data || {};
      if (kind === 'sale' || kind === 'quote') {
        applySaleScan(d, r);
        const checks = r.checks || [];
        d.aiStatus = `Read ${r.lines?.length || 0} item(s)${r.handwritten ? ' from handwriting' : ''}.`
          + (checks.length ? ` Check: ${checks.join(' ')}` : ' Check the details, then save.');
        rerenderForm();
        return;
      }
      if (r.vendor_id) d.customerId = String(r.vendor_id);
      if (r.bill_date) d.txnDate = r.bill_date;
      if (Number(md.txnType) === 2) {
        if (r.bill_no) { d.txnNumber = r.bill_no; d.txnNumberManual = true; }
        if (r.lines?.length) {
          d.lines = r.lines.map((l) => ({
            item_name: l.item_name, description: '', qty: String(l.qty), unit: l.unit || 'Pcs',
            rate: String(l.rate), discount_pct: '0', tax_pct: String(l.tax_pct || 0), hsn: l.hsn || '',
            free_qty: Number(l.free_qty || 0) > 0 ? String(l.free_qty) : '',
          })).concat([typeof hopEmptyDocLine === 'function' ? hopEmptyDocLine() : { item_name: '', qty: '', rate: '' }]);
        }
        if (r.charges?.length) d.charges = r.charges.map((c) => ({ name: c.name, amount: String(c.amount), tax_pct: String(c.tax_pct || 0) }));
        const tcs = Number(r.tcs_amount || 0), tot = Number(r.total || 0);
        if (tcs > 0 && tot > tcs) d.tcsPct = String(Math.round(tcs / (tot - tcs) * 100000) / 1000);
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
    const catDoc = ty === 7 || ty === 29;
    if (catDoc && !partyId && !d.expenseCategoryId) return { error: 'Choose a category or a party' };
    if (!catDoc && !partyId) return { error: ty === 3 ? 'Select a customer' : 'Select a vendor' };
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
    if (catDoc) payload.expense_category_id = d.expenseCategoryId ? Number(d.expenseCategoryId) : null;
    if (!catDoc) {
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
    Object.assign(out, extrasFields());
    if (BILL_TYPES[Number(md.txnType)]) out.godown_id = d.godownId ? Number(d.godownId) : 0;
    if (BILL_TYPES[Number(md.txnType)]) {
      out.place_of_supply = d.placeOfSupply || '';
      if (Number(md.txnType) === 2) out.reverse_charge = !!d.reverseCharge;
    }
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
    if (foot) {
      const sh = document.createElement('span');
      sh.className = 'hop-money-share';
      sh.innerHTML = `<button type="button" class="nx-btn" onclick="hopMoneyShare(${partyTxnId},'bill')">WhatsApp bill</button>
        <button type="button" class="nx-btn" onclick="hopMoneyShare(${partyTxnId},'reminder')">Payment reminder</button>`;
      foot.insertBefore(sh, foot.firstChild);
    }
    if (foot && Number(info.txn_type) === 1) {
      const g = document.createElement('span');
      g.className = 'hop-money-gst';
      g.innerHTML = `<button type="button" class="nx-btn" onclick="hopMoneyGstFile(${partyTxnId},'einvoice')">E-Invoice file</button>
        <button type="button" class="nx-btn" onclick="hopMoneyGstFile(${partyTxnId},'eway')">E-Way Bill file</button>
        <button type="button" class="nx-btn" onclick="hopMoneyGstRefs(${partyTxnId})">Save IRN / E-Way no</button>`;
      foot.insertBefore(g, foot.firstChild);
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

  // ---------- GST portal files (E-Invoice / E-Way Bill JSON, no GSP account needed) ----------
  async function gstFile(id, kind) {
    let transport;
    if (kind === 'eway') {
      const vehicle = window.prompt('Vehicle number (for road transport)', '');
      if (vehicle === null) return;
      const km = window.prompt('Distance in km', '0');
      transport = { mode: 'road', vehicle_no: vehicle, distance_km: Number(km || 0) };
    }
    try {
      const out = await hopApi(`/api/v1/hop/gst-files/${kind}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ txn_ids: [id], transport }),
      });
      const probs = out.problems || [];
      if (probs.length && !window.confirm('Fix these before uploading:\n\n- ' + probs.join('\n- ') + '\n\nDownload the file anyway?')) return;
      const blob = new Blob([JSON.stringify(out.json, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${kind === 'einvoice' ? 'einvoice' : 'ewaybill'}_${id}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      toast(kind === 'einvoice' ? 'Upload it on the e-invoice portal (Bulk Upload)' : 'Upload it on the e-way bill portal (Bulk Generation)');
    } catch (e) { toast(e?.message || 'Could not make the file', 'err'); }
  }

  async function gstRefs(id) {
    const irn = window.prompt('IRN (from the e-invoice portal)', '');
    if (irn === null) return;
    const eway = window.prompt('E-Way Bill no. (leave blank if none)', '') || '';
    try {
      await hopApi(`/api/v1/hop/party-transactions/${id}/gst-refs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ irn, eway_bill_no: eway }),
      });
      toast('Saved');
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
  }

  // ---------- Bank reconciliation ----------
  const recon = { id: 0, data: null, picked: new Set(), notInBooks: [], msg: '' };

  async function reconcileOpen(id) {
    Object.assign(recon, { id, data: null, picked: new Set(), notInBooks: [], msg: '' });
    try { recon.data = await hopApi(`/api/v1/hop/bank-accounts/${id}/reconcile`); } catch (e) { toast(e?.message || 'Could not load', 'err'); return; }
    reconcileRender();
  }

  function reconcileRender() {
    const slot = document.getElementById('hop-form-slot');
    const d = recon.data;
    if (!slot || !d) return;
    slot.classList.remove('hidden');
    const today = new Date().toISOString().slice(0, 10);
    const rows = (d.entries || []).map((e) => {
      const on = !!e.cleared_on || recon.picked.has(e.id);
      return `<tr><td><input type="checkbox" ${on ? 'checked' : ''} onchange="hopReconcileTick(${e.id},this.checked,${e.cleared_on ? 'true' : 'false'})" /></td>
        <td>${esc(e.date)}</td><td>${e.type === 'in' ? 'In' : 'Out'}</td><td>${esc(e.notes)}</td><td>${esc(e.reference)}</td>
        <td class="inv-num">${money(e.amount)}</td><td>${e.cleared_on ? esc(e.cleared_on) : ''}</td></tr>`;
    }).join('');
    const missing = (recon.notInBooks || []).map((m) => `<tr><td>${esc(m.date)}</td><td>${esc(m.description)}</td>
        <td class="inv-num">${money(m.amount)}</td><td>${esc(m.hint)}</td></tr>`).join('');
    slot.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
        <strong>Reconcile · ${esc(d.account?.name || '')}</strong><span style="flex:1"></span>
        <button type="button" class="nx-btn" onclick="hopReconcileClose()">Close</button>
      </div>
      <p>In your books <strong>${money(d.book_balance)}</strong> · Ticked (as per bank) <strong>${money(d.cleared_balance)}</strong>
        · Not yet ticked: in ${money(d.uncleared_in)}, out ${money(d.uncleared_out)}</p>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0;">
        <label class="nx-btn">✨ Match bank statement (CSV)<input type="file" accept=".csv,text/csv" hidden onchange="hopReconcileUpload(this.files[0])" /></label>
        <span>Cleared on</span><input type="date" id="hop-recon-date" class="nx-input" value="${today}" style="max-width:170px;" />
        <button type="button" class="nx-btn nx-btn-primary" ${recon.picked.size ? '' : 'disabled'} onclick="hopReconcileSave()">Save ticks (${recon.picked.size})</button>
        ${recon.msg ? `<span class="hop-doc-muted">${esc(recon.msg)}</span>` : ''}
      </div>
      ${missing ? `<h4>On the statement but not in your books</h4>${hopTable(['Date', 'Description', 'Amount', 'What to do'], missing, 4, { label: 'Missing in books', count: recon.notInBooks.length })}` : ''}
      ${hopTable(['', 'Date', 'Type', 'Details', 'Reference', 'Amount', 'Cleared'], rows, 7, { label: 'Entries', count: (d.entries || []).length })}`;
  }

  async function reconcileMark(ids, date) {
    try {
      recon.data = await hopApi(`/api/v1/hop/bank-accounts/${recon.id}/reconcile`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entry_ids: ids, cleared_on: date }),
      });
      recon.picked = new Set();
      recon.msg = date ? `Ticked ${ids.length}` : 'Unticked';
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
    reconcileRender();
  }

  async function reconcileUpload(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetchWithAuth(`/api/v1/hop/bank-accounts/${recon.id}/reconcile/match`, { method: 'POST', body: fd });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || out.success === false) throw new Error(out.error?.message || 'Could not read the statement');
      const m = out.data || {};
      recon.picked = new Set((m.matches || []).map((x) => x.entry_id));
      recon.notInBooks = m.not_in_books || [];
      recon.msg = `${m.summary || ''} Check the ticks, then Save ticks.`;
    } catch (e) { toast(e?.message || 'Could not read the statement', 'err'); }
    reconcileRender();
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
             <button type="button" class="nx-btn" onclick="hopMoneyPickAccount(0)">Show all accounts</button>
             ${sel.account_type !== 'cash' ? `<button type="button" class="nx-btn nx-btn-primary" onclick="hopReconcileOpen(${sel.id})">Reconcile with bank statement</button>` : ''}</p>` : ''}
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
    withExtras,
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
    hopMoneyGstFile: gstFile,
    hopMoneyGstRefs: gstRefs,
    hopRenderCashBank: renderCashBank,
    hopMoneyTab: (t) => { hopState.cbTab = t; openHopView('banking'); },
    hopMoneyPickAccount: (id) => { hopState.cbAccount = id || null; hopState.cbTab = 'accounts'; openHopView('banking'); },
    hopMoneyEntryForm: entryForm,
    hopMoneySaveEntry: saveEntry,
    hopMoneyCloseCheque: closeCheque,
    hopMoneyReopenCheque: reopenCheque,
  });
  window.hopMoneyRefreshTotal = refreshTotal;
  window.hopMoneyCharge = (i, k, v) => {
    const d = hopState.manualDocDraft;
    if (!d?.charges?.[i]) return;
    d.charges[i][k] = v;
    refreshTotal();
  };
  window.hopMoneyChargeAdd = (name) => {
    const d = hopState.manualDocDraft;
    if (!d) return;
    (d.charges = d.charges || []).push({ name, amount: '', tax_pct: name ? '18' : '0' });
    rerenderForm();
  };
  window.hopMoneyChargeRemove = (i) => {
    const d = hopState.manualDocDraft;
    if (!d?.charges) return;
    d.charges.splice(i, 1);
    rerenderForm();
  };
  window.hopReconcileOpen = reconcileOpen;
  window.hopReconcileClose = () => { const el = document.getElementById('hop-form-slot'); if (el) { el.innerHTML = ''; el.classList.add('hidden'); } };
  window.hopReconcileTick = (id, on, wasCleared) => {
    if (wasCleared) { if (!on) reconcileMark([id], null); return; }
    if (on) recon.picked.add(id); else recon.picked.delete(id);
    reconcileRender();
  };
  window.hopReconcileSave = () => reconcileMark([...recon.picked], document.getElementById('hop-recon-date')?.value || null);
  window.hopReconcileUpload = reconcileUpload;
  // Share on WhatsApp: the owner's message (Settings → Sharing) with the UPI link; the user taps Send.
  window.hopMoneyShare = async (id, kind) => {
    try {
      const d = await hopApi(`/api/v1/hop/party-transactions/${id}/share?kind=${kind}`);
      window.open(d.whatsapp_url, '_blank', 'noopener');
      if (!d.phone) toast('No mobile number on this party — pick the chat in WhatsApp.', 'warn');
    } catch (e) { toast(e?.message || 'Could not prepare the message', 'err'); }
  };
})();
