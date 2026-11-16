/**
 * HoP Reports (Vyapar-style, Phase 2). Server: GET /api/v1/hop/books and /api/v1/hop/books/<key>.
 * Left: report list. Right: filters, the report, totals, Excel (CSV) / Print, and AI insights.
 */
(function () {
  'use strict';

  const GROUPS = [
    ['Accounts', ['profit_loss', 'balance_sheet', 'trial_balance', 'cash_flow']],
    ['Transactions', ['day_book', 'all_transactions', 'sale_report', 'purchase_report', 'expense_report', 'other_income', 'expense_items', 'bill_profit', 'discount_report', 'sale_orders', 'purchase_orders']],
    ['Parties', ['party_statement', 'outstanding', 'party_pl', 'party_item', 'group_sale', 'group_purchase']],
    ['Items & stock', ['stock_summary', 'low_stock', 'stock_movement', 'item_report', 'item_pl', 'category_report']],
    ['GST', ['gstr1', 'gstr2', 'gstr3b', 'gstr9', 'hsn_sale', 'hsn_purchase']],
  ];
  const TITLES = {
    trial_balance: 'Trial Balance', profit_loss: 'Profit & Loss', balance_sheet: 'Balance Sheet', cash_flow: 'Cash Flow',
    day_book: 'Day Book', all_transactions: 'All Transactions', party_statement: 'Party Statement',
    outstanding: 'Party-wise Outstanding', sale_report: 'Sale Report', purchase_report: 'Purchase Report',
    item_report: 'Item-wise Report', stock_summary: 'Stock Summary', low_stock: 'Low Stock',
    stock_movement: 'Stock Movement', bill_profit: 'Bill-wise Profit', expense_report: 'Expense Report', other_income: 'Other Income',
    gstr1: 'GSTR-1', gstr2: 'GSTR-2', gstr3b: 'GSTR-3B', hsn_sale: 'HSN Summary (Sales)', hsn_purchase: 'HSN Summary (Purchases)',
    party_pl: 'Party-wise Profit & Loss', item_pl: 'Item-wise Profit & Loss', party_item: 'Party Report by Item',
    group_sale: 'Sale by Party Group', group_purchase: 'Purchase by Party Group', category_report: 'Item Category Report',
    discount_report: 'Discount Report', expense_items: 'Expense Item Report', sale_orders: 'Sale Order Report',
    purchase_orders: 'Purchase Order Report', gstr9: 'GSTR-9 (Annual)',
  };
  const AS_OF = new Set(['trial_balance', 'balance_sheet', 'outstanding']);
  const NO_DATES = new Set(['stock_summary', 'low_stock']);
  const AI_OK = new Set(['profit_loss', 'balance_sheet', 'cash_flow', 'outstanding', 'expense_report', 'other_income', 'bill_profit', 'sale_report', 'gstr3b',
    'party_pl', 'item_pl', 'category_report', 'discount_report', 'sale_orders', 'gstr9']);

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const money = (v) => (typeof hopMoney === 'function' ? hopMoney(v) : Number(v || 0).toFixed(2));
  const today = () => new Date().toISOString().slice(0, 10);
  const fyStart = () => { const d = new Date(); const y = d.getMonth() >= 3 ? d.getFullYear() : d.getFullYear() - 1; return `${y}-04-01`; };

  function st() {
    hopState.rep = hopState.rep || { key: 'profit_loss', from: fyStart(), to: today(), date: today(), partyKey: '', productId: '', lowAt: '0' };
    return hopState.rep;
  }

  // ---------- small builders ----------
  function table(cols, rows, opts = {}) {
    if (!rows || !rows.length) return `<p class="hop-doc-muted" style="margin:10px 0;">${esc(opts.empty || 'Nothing in this period.')}</p>`;
    const head = cols.map((c) => `<th${c.num ? ' style="text-align:right"' : ''}>${esc(c.label)}</th>`).join('');
    const body = rows.map((r) => {
      const click = opts.clickId && r[opts.clickId] ? ` style="cursor:pointer" onclick="hopRepOpenDoc(${Number(r[opts.clickId])})"` : '';
      return `<tr${click}>${cols.map((c) => {
        const v = r[c.k];
        return c.num ? `<td class="inv-num" style="text-align:right">${v == null || v === '' ? '' : money(v)}</td>` : `<td>${esc(v ?? '')}</td>`;
      }).join('')}</tr>`;
    }).join('');
    const foot = opts.totals ? `<tfoot><tr>${cols.map((c, i) => {
      if (i === 0) return '<td><strong>Total</strong></td>';
      return c.num && opts.totals[c.k] != null ? `<td class="inv-num" style="text-align:right"><strong>${money(opts.totals[c.k])}</strong></td>` : '<td></td>';
    }).join('')}</tr></tfoot>` : '';
    return `<div class="hop-vyp-table-wrap" style="margin:8px 0 16px;"><table class="hop-vyp-table hop-vyp-table--simple hop-rep-table" data-title="${esc(opts.title || '')}">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody>${foot}</table></div>`;
  }

  function stmt(rows) { // [label, amount, {bold, indent, tone}]
    return `<table class="hop-vyp-table hop-vyp-table--simple hop-rep-table" style="max-width:620px;margin:8px 0 16px;"><tbody>${rows.map(([l, v, o = {}]) => `
      <tr${o.bold ? ' style="font-weight:700;border-top:2px solid rgba(148,163,184,.4)"' : ''}>
        <td style="padding-left:${o.indent ? 22 : 8}px">${esc(l)}</td>
        <td class="inv-num" style="text-align:right;${o.tone === 'neg' ? 'color:#dc2626' : (o.tone === 'pos' ? 'color:#16a34a' : '')}">${v == null ? '' : money(v)}</td></tr>`).join('')}</tbody></table>`;
  }

  const badge = (ok, yes, no) => `<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:.8rem;background:${ok ? 'rgba(22,163,74,.15);color:#16a34a' : 'rgba(220,38,38,.15);color:#dc2626'}">${esc(ok ? yes : no)}</span>`;
  const docCols = [
    { k: 'date', label: 'Date' }, { k: 'type', label: 'Type' }, { k: 'number', label: 'No.' }, { k: 'party', label: 'Party' },
    { k: 'taxable', label: 'Taxable', num: true }, { k: 'gst', label: 'GST', num: true }, { k: 'total', label: 'Total', num: true },
    { k: 'balance', label: 'Balance', num: true }, { k: 'status', label: 'Status' },
  ];
  const gstCols = [
    { k: 'date', label: 'Date' }, { k: 'number', label: 'No.' }, { k: 'party', label: 'Party' }, { k: 'gstin', label: 'GSTIN' },
    { k: 'rate', label: 'Rate %' }, { k: 'taxable', label: 'Taxable', num: true }, { k: 'igst', label: 'IGST', num: true },
    { k: 'cgst', label: 'CGST', num: true }, { k: 'sgst', label: 'SGST', num: true },
  ];

  // ---------- renderers ----------
  const R = {
    profit_loss: (d) => stmt([
      ['Sales', d.sales], ['Less: Sale returns', -d.sale_returns, { indent: true }], ['Net sales', d.net_sales, { bold: true }],
      ['Opening stock', d.opening_stock], ['Add: Purchases', d.purchases, { indent: true }],
      ['Less: Purchase returns', -d.purchase_returns, { indent: true }], ['Less: Closing stock', -d.closing_stock, { indent: true }],
      ['Cost of goods sold', d.cost_of_goods_sold, { bold: true }],
      ['Gross profit', d.gross_profit, { bold: true, tone: d.gross_profit < 0 ? 'neg' : 'pos' }],
      ...(d.other_income || []).map((e) => [`Other income: ${e.category}`, e.amount, { indent: true }]),
      ...((d.other_income || []).length ? [['Total other income', d.total_other_income, { bold: true }]] : []),
      ...d.expenses.map((e) => [`Expense: ${e.category}`, e.amount, { indent: true }]),
      ['Total expenses', d.total_expenses, { bold: true }],
      [d.net_profit < 0 ? 'Net loss' : 'Net profit', d.net_profit, { bold: true, tone: d.net_profit < 0 ? 'neg' : 'pos' }],
    ]),
    balance_sheet: (d) => `<p>${badge(d.balanced, 'Balanced', 'Not balanced — please report')}</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;">
        <div><h3>Assets</h3>${stmt([...d.assets.map((a) => [a.name, a.amount]), ['Total assets', d.total_assets, { bold: true }]])}</div>
        <div><h3>Liabilities & capital</h3>${stmt([...d.liabilities.map((a) => [a.name, a.amount]), ...d.equity.map((a) => [a.name, a.amount]),
          ['Total', d.total_liabilities_equity, { bold: true }]])}</div></div>`,
    trial_balance: (d) => `<p>${badge(d.balanced, 'Debit = Credit', 'Not balanced — please report')}</p>`
      + table([{ k: 'account', label: 'Account' }, { k: 'group', label: 'Group' }, { k: 'debit', label: 'Debit', num: true }, { k: 'credit', label: 'Credit', num: true }],
        d.rows, { totals: { debit: d.total_debit, credit: d.total_credit }, title: 'Trial Balance' }),
    cash_flow: (d) => stmt([
      ['Opening cash & bank', d.opening_cash, { bold: true }],
      ...d.inflows.map((x) => [`+ ${x.head}`, x.amount, { indent: true, tone: 'pos' }]), ['Total money in', d.total_in, { bold: true }],
      ...d.outflows.map((x) => [`− ${x.head}`, -x.amount, { indent: true, tone: 'neg' }]), ['Total money out', -d.total_out, { bold: true }],
      ['Closing cash & bank', d.closing_cash, { bold: true }],
    ]),
    day_book: (d) => `<p>Money in <strong>${money(d.money_in)}</strong> · Money out <strong>${money(d.money_out)}</strong> · Net <strong>${money(d.net)}</strong></p>`
      + table([...docCols.slice(0, 4), { k: 'total', label: 'Total', num: true }, { k: 'money_in', label: 'Money in', num: true }, { k: 'money_out', label: 'Money out', num: true }, { k: 'status', label: 'Status' }],
        d.rows, { clickId: 'id', title: 'Day Book', empty: 'No transactions on this day.' })
      + (d.other_money?.length ? `<h3>Other cash / bank entries</h3>${table([{ k: 'type', label: 'Details' }, { k: 'account', label: 'Account' }, { k: 'amount', label: 'Amount', num: true }], d.other_money)}` : ''),
    all_transactions: (d) => table(docCols, d.rows, { clickId: 'id', title: 'All Transactions', totals: { total: d.total } }),
    sale_report: (d) => table(docCols, d.rows, { clickId: 'id', title: 'Sale Report', totals: { taxable: d.net_taxable, gst: d.net_gst, total: d.net_total } })
      + `<h3>By party</h3>${table([{ k: 'party', label: 'Party' }, { k: 'amount', label: 'Amount', num: true }], d.by_party)}`,
    purchase_report: (d) => R.sale_report(d).replace('Sale Report', 'Purchase Report'),
    other_income: (d) => `<h3>By category</h3>${table([{ k: 'category', label: 'Category' }, { k: 'amount', label: 'Amount', num: true }], d.by_category, { totals: { amount: d.total } })}`,
    expense_report: (d) => `<h3>By category</h3>${table([{ k: 'category', label: 'Category' }, { k: 'amount', label: 'Amount', num: true }], d.by_category, { totals: { amount: d.total } })}`
      + `<h3>All expenses</h3>${table([{ k: 'date', label: 'Date' }, { k: 'number', label: 'No.' }, { k: 'category', label: 'Category' }, { k: 'party', label: 'Party' }, { k: 'notes', label: 'Notes' }, { k: 'amount', label: 'Amount', num: true }], d.rows, { clickId: 'id' })}`,
    bill_profit: (d) => `<p class="hop-doc-muted">${esc(d.note)}</p>` + table([...docCols.slice(0, 4), { k: 'taxable', label: 'Sale (taxable)', num: true }, { k: 'cost', label: 'Cost', num: true }, { k: 'profit', label: 'Profit', num: true }, { k: 'lines_without_cost', label: 'Items without cost' }],
      d.rows, { clickId: 'id', totals: { taxable: d.total_sale, cost: d.total_cost, profit: d.total_profit } }),
    party_statement: (d) => `<p><strong>${esc(d.party || '')}</strong> · Opening ${money(d.opening_balance)} · Closing <strong>${money(d.closing_balance)}</strong> ${d.closing_balance >= 0 ? '(to receive)' : '(to pay)'}</p>`
      + table([{ k: 'date', label: 'Date' }, { k: 'type', label: 'Type' }, { k: 'number', label: 'No.' }, { k: 'debit', label: 'Debit', num: true }, { k: 'credit', label: 'Credit', num: true }, { k: 'balance', label: 'Balance', num: true }],
        d.lines, { clickId: 'txn_id', totals: { debit: d.total_debit, credit: d.total_credit }, title: 'Party Statement' })
      + `<p class="hop-doc-muted">${esc(d.meaning)}</p>`,
    outstanding: (d) => `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;">
      <div><h3>To receive · ${money(d.total_to_receive)}</h3>${table([{ k: 'party', label: 'Party' }, { k: 'amount', label: 'Amount', num: true }], d.to_receive, { empty: 'Nobody owes you.' })}</div>
      <div><h3>To pay · ${money(d.total_to_pay)}</h3>${table([{ k: 'party', label: 'Party' }, { k: 'amount', label: 'Amount', num: true }], d.to_pay, { empty: 'You owe nobody.' })}</div></div>`,
    item_report: (d) => table([{ k: 'item', label: 'Item' }, { k: 'sale_qty', label: 'Sold qty' }, { k: 'sale_amount', label: 'Sale amount', num: true }, { k: 'purchase_qty', label: 'Bought qty' }, { k: 'purchase_amount', label: 'Purchase amount', num: true }], d.rows),
    stock_summary: (d) => table([{ k: 'item', label: 'Item' }, { k: 'code', label: 'Code' }, { k: 'category', label: 'Category' }, { k: 'qty', label: 'Qty' }, { k: 'purchase_price', label: 'Purchase price', num: true }, { k: 'sale_price', label: 'Sale price', num: true }, { k: 'stock_value', label: 'Stock value', num: true }],
      d.rows, { totals: { stock_value: d.total_value }, empty: d.low_at != null ? 'No item is at or below this quantity.' : 'No items.' }),
    low_stock: (d) => R.stock_summary(d),
    stock_movement: (d) => `<p><strong>${esc(d.item)}</strong> · Opening ${esc(d.opening_qty)} · Closing <strong>${esc(d.closing_qty)}</strong></p>`
      + table([{ k: 'date', label: 'Date' }, { k: 'details', label: 'Details' }, { k: 'in', label: 'In' }, { k: 'out', label: 'Out' }, { k: 'balance', label: 'Balance' }], d.lines),
    gstr1: (d) => `<h3>B2B invoices (customers with GSTIN)</h3>${table(gstCols, d.b2b, { clickId: 'id' })}
      <h3>B2C (no GSTIN), by rate</h3>${table([{ k: 'rate', label: 'Rate %' }, { k: 'taxable', label: 'Taxable', num: true }, { k: 'igst', label: 'IGST', num: true }, { k: 'cgst', label: 'CGST', num: true }, { k: 'sgst', label: 'SGST', num: true }], d.b2c_by_rate)}
      <h3>Credit notes (sale returns)</h3>${table(gstCols, d.credit_notes, { clickId: 'id' })}
      <h3>Net totals</h3>${stmt([['Taxable', d.totals.taxable], ['IGST', d.totals.igst], ['CGST', d.totals.cgst], ['SGST', d.totals.sgst]])}`,
    gstr2: (d) => `<p class="hop-doc-muted">${esc(d.itc_note)}</p><h3>Bills with GSTIN</h3>${table(gstCols, d.with_gstin, { clickId: 'id' })}
      <h3>Bills without GSTIN</h3>${table(gstCols, d.without_gstin, { clickId: 'id' })}
      <h3>Net totals</h3>${stmt([['Taxable', d.totals.taxable], ['IGST', d.totals.igst], ['CGST', d.totals.cgst], ['SGST', d.totals.sgst]])}`,
    gstr3b: (d) => {
      const row = (o) => [o.igst, o.cgst, o.sgst];
      const t = (label, o) => `<tr><td>${esc(label)}</td>${row(o).map((v) => `<td class="inv-num" style="text-align:right">${money(v)}</td>`).join('')}</tr>`;
      return `<table class="hop-vyp-table hop-vyp-table--simple hop-rep-table" style="max-width:700px"><thead><tr><th></th><th style="text-align:right">IGST</th><th style="text-align:right">CGST</th><th style="text-align:right">SGST</th></tr></thead><tbody>
        ${t('3.1 Outward supplies (tax)', d.outward_supplies)}${t('4. Eligible ITC', d.eligible_itc)}${t('Tax payable', d.tax_payable)}${t('Credit carried forward', d.credit_carried_forward)}
        </tbody></table><p>Taxable outward value: <strong>${money(d.outward_supplies.taxable)}</strong> · Total tax payable: <strong>${money(d.total_payable)}</strong></p>
        <p class="hop-doc-muted">${esc(d.note)}</p>`;
    },
    hsn_sale: (d) => table([{ k: 'hsn', label: 'HSN' }, { k: 'rate', label: 'Rate %' }, { k: 'qty', label: 'Qty' }, { k: 'taxable', label: 'Taxable', num: true }, { k: 'tax', label: 'Tax', num: true }], d.rows, { totals: { taxable: d.taxable_total, tax: d.tax_total } }),
    hsn_purchase: (d) => R.hsn_sale(d),
  };

  // Any report: totals first, then each list as a table.
  const HIDE = new Set(['id', 'txn_id', 'party_id', 'product_id', 'party_type', 'txn_type']);
  const NOT_MONEY = /(^|_)(qty|count|bills|rate|pct|lines|in|out|invoices|notes)$|^(date|number|hsn|gstin|status|type|kind)$/;
  function generic(d) {
    const label = (k) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
    const val = (k, v) => (typeof v === 'number' && !NOT_MONEY.test(k) ? money(v) : esc(v ?? ''));
    const scal = Object.entries(d).filter(([k, v]) => v !== null && typeof v !== 'object' && !HIDE.has(k));
    let html = scal.length ? `<table class="hop-vyp-table hop-vyp-table--simple hop-rep-table" style="max-width:620px;margin:8px 0 16px;"><tbody>
      ${scal.map(([k, v]) => `<tr><td>${esc(label(k))}</td><td class="inv-num" style="text-align:right">${val(k, v)}</td></tr>`).join('')}</tbody></table>` : '';
    Object.entries(d).forEach(([k, v]) => {
      if (Array.isArray(v)) {
        const cols = v.length && typeof v[0] === 'object' ? Object.keys(v[0]).filter((c) => !HIDE.has(c) && (typeof v[0][c] !== 'object' || v[0][c] === null)) : [];
        html += `<h3>${esc(label(k))} (${v.length})</h3>` + table(cols.map((c) => ({ k: c, label: label(c), num: typeof v[0][c] === 'number' && !NOT_MONEY.test(c) })),
          v.map((r) => ({ ...r, id: r.id || r.txn_id })), { clickId: 'id', title: label(k) });
      } else if (v && typeof v === 'object') {
        html += `<h3>${esc(label(k))}</h3>` + stmt(Object.entries(v).map(([a, b]) => [label(a), typeof b === 'number' ? b : null]));
      }
    });
    return html;
  }

  // ---------- screen ----------
  async function loadPickers() {
    if (hopState.repParties) return;
    const [c, v, p] = await Promise.all([
      hopApi('/api/v1/hop/customers').catch(() => []), hopApi('/api/v1/hop/vendors').catch(() => []),
      hopApi('/api/v1/hop/products').catch(() => []),
    ]);
    hopState.repParties = [...(c || []).map((x) => ({ key: `customer:${x.id}`, name: x.company })),
      ...(v || []).map((x) => ({ key: `vendor:${x.id}`, name: `${x.company} (vendor)` }))].sort((a, b) => String(a.name).localeCompare(String(b.name)));
    hopState.repProducts = (p?.items || p || []).map((x) => ({ id: x.id, name: x.name }));
  }

  function filtersHtml(s) {
    const k = s.key;
    const parts = [];
    if (k === 'day_book') parts.push(`<label>Date <input type="date" value="${esc(s.date)}" onchange="hopRepSet('date',this.value)"></label>`);
    else if (AS_OF.has(k)) parts.push(`<label>As on <input type="date" value="${esc(s.to)}" onchange="hopRepSet('to',this.value)"></label>`);
    else if (!NO_DATES.has(k)) {
      parts.push(`<label>From <input type="date" value="${esc(s.from)}" onchange="hopRepSet('from',this.value)"></label>`);
      parts.push(`<label>To <input type="date" value="${esc(s.to)}" onchange="hopRepSet('to',this.value)"></label>`);
    }
    if (['party_statement', 'party_item', 'sale_report', 'purchase_report', 'all_transactions'].includes(k)) {
      parts.push(`<label>Party <select onchange="hopRepSet('partyKey',this.value)"><option value="">${k === 'party_statement' ? 'Choose party' : 'All parties'}</option>
        ${(hopState.repParties || []).map((p) => `<option value="${p.key}"${s.partyKey === p.key ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>`);
    }
    if (k === 'stock_movement') {
      parts.push(`<label>Item <select onchange="hopRepSet('productId',this.value)"><option value="">Choose item</option>
        ${(hopState.repProducts || []).map((p) => `<option value="${p.id}"${String(s.productId) === String(p.id) ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select></label>`);
    }
    if (k === 'low_stock') parts.push(`<label>Qty at or below <input type="number" value="${esc(s.lowAt)}" style="width:90px" onchange="hopRepSet('lowAt',this.value)"></label>`);
    return parts.join('');
  }

  function query(s) {
    const q = new URLSearchParams();
    if (s.key === 'day_book') q.set('date', s.date);
    else if (AS_OF.has(s.key)) q.set('to', s.to);
    else if (!NO_DATES.has(s.key)) { q.set('from', s.from); q.set('to', s.to); }
    if (s.partyKey) { const [t, id] = s.partyKey.split(':'); q.set('party_type', t); q.set('party_id', id); }
    if (s.key === 'stock_movement' && s.productId) q.set('product_id', s.productId);
    if (s.key === 'low_stock') q.set('low_at', s.lowAt || '0');
    return q.toString();
  }

  async function render(mount, preset) {
    const s = st();
    if (preset) s.key = preset;
    await loadPickers();
    const nav = GROUPS.map(([g, keys]) => `<div style="margin-bottom:10px;"><div class="hop-doc-muted" style="font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;margin:6px 0;">${esc(g)}</div>
      ${keys.map((k) => `<button type="button" class="nx-btn${s.key === k ? ' nx-btn-primary' : ''}" style="display:block;width:100%;text-align:left;margin:2px 0;" onclick="hopRepPick('${k}')">${esc(TITLES[k])}</button>`).join('')}</div>`).join('');
    const body = `
      <div style="display:grid;grid-template-columns:220px 1fr;gap:16px;align-items:start;" class="hop-rep-grid">
        <div class="nx-card" style="padding:10px;">${nav}</div>
        <div class="nx-card" style="padding:14px;" id="hop-rep-panel">
          <div style="display:flex;gap:12px;align-items:end;flex-wrap:wrap;">
            <h2 style="margin:0;flex:1;min-width:180px;">${esc(TITLES[s.key])}</h2>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end;" class="hop-rep-filters">${filtersHtml(s)}</div>
            <button type="button" class="nx-btn" onclick="hopRepCsv()">Excel (CSV)</button>
            <button type="button" class="nx-btn" onclick="hopRepPrint()">Print</button>
            ${AI_OK.has(s.key) ? '<button type="button" class="nx-btn" onclick="hopRepInsights()">✨ AI insights</button>' : ''}
          </div>
          <div id="hop-rep-ai"></div>
          <div id="hop-rep-body" style="margin-top:12px;"><p class="hop-doc-muted">Loading…</p></div>
        </div>
      </div>
      <style>@media (max-width:820px){.hop-rep-grid{grid-template-columns:1fr !important;}}</style>`;
    mount.innerHTML = hopModuleShell('Books', 'Reports', 'Accounts, transactions, parties, stock and GST', '', body);
    const out = document.getElementById('hop-rep-body');
    if ((s.key === 'party_statement' || s.key === 'party_item') && !s.partyKey) { out.innerHTML = '<p class="hop-doc-muted">Choose a party to see this report.</p>'; return; }
    if (s.key === 'stock_movement' && !s.productId) { out.innerHTML = '<p class="hop-doc-muted">Choose an item to see its movement.</p>'; return; }
    try {
      const data = await hopApi(`/api/v1/hop/books/${s.key}?${query(s)}`);
      hopState.repData = data;
      out.innerHTML = (R[s.key] || generic)(data);
    } catch (e) {
      out.innerHTML = `<p class="nx-oc-error">${esc(e?.message || 'Could not load the report')}</p>`;
    }
  }

  function rerender() { const m = typeof hopMount === 'function' ? hopMount() : null; if (m) render(m); }

  function csv() {
    const s = st();
    const tables = [...document.querySelectorAll('#hop-rep-body table')];
    if (!tables.length) return;
    const cell = (t) => `"${String(t || '').replace(/"/g, '""').trim()}"`;
    const lines = [cell(TITLES[s.key]), ''];
    tables.forEach((t) => {
      t.querySelectorAll('tr').forEach((tr) => lines.push([...tr.children].map((c) => cell(c.innerText.replace(/[₹,]/g, ''))).join(',')));
      lines.push('');
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${TITLES[s.key].replace(/[^a-z0-9]+/gi, '_')}_${s.from || ''}_${s.to || s.date || ''}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  function print() {
    const s = st();
    const body = document.getElementById('hop-rep-body')?.innerHTML || '';
    const w = window.open('', '_blank');
    if (!w) return;
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(TITLES[s.key])}</title>
      <style>body{font-family:system-ui,Arial;padding:24px;color:#111}table{border-collapse:collapse;width:100%;margin:8px 0 16px}
      td,th{border:1px solid #ccc;padding:6px 8px;font-size:12px}th{background:#f3f4f6;text-align:left}h3{margin:16px 0 4px}</style></head>
      <body><h2>${esc(TITLES[s.key])}</h2><p>${esc(s.key === 'day_book' ? s.date : (AS_OF.has(s.key) ? `As on ${s.to}` : `${s.from} to ${s.to}`))}</p>${body}</body></html>`);
    w.document.close();
    w.focus();
    w.print();
  }

  async function insights() {
    const s = st();
    const box = document.getElementById('hop-rep-ai');
    if (!box) return;
    box.innerHTML = '<p class="hop-doc-muted" style="margin-top:10px;">✨ Reading the report…</p>';
    try {
      const out = await hopApi(`/api/v1/hop/books/${s.key}/insights?${query(s)}`);
      box.innerHTML = `<div style="margin-top:10px;padding:12px 14px;border-radius:12px;background:rgba(124,58,237,.08);">
        <strong>✨ AI insights</strong><ul style="margin:6px 0 0 18px;">${(out.points || []).map((p) => `<li>${esc(p)}</li>`).join('')}</ul>
        <p class="hop-doc-muted" style="margin:6px 0 0;font-size:.8rem;">AI can be wrong — check the numbers in the report.</p></div>`;
    } catch (e) {
      box.innerHTML = `<p class="nx-oc-error" style="margin-top:10px;">${esc(e?.message || 'AI insights failed')}</p>`;
    }
  }

  Object.assign(window, {
    hopRenderReports: render,
    hopRepPick: (k) => { st().key = k; rerender(); },
    hopRepSet: (k, v) => { st()[k] = v; rerender(); },
    hopRepOpenDoc: (id) => { if (typeof hopOpenSaleDocPreview === 'function') hopOpenSaleDocPreview(id, 0); },
    hopRepCsv: csv,
    hopRepPrint: print,
    hopRepInsights: insights,
  });
})();
