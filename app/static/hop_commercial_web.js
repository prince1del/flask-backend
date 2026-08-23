/**
 * HoP web parity: commercial quotation preview, firm profile, manual Estimate/Proforma create.
 * Loaded after hop_app.js — replaces hopRenderDocPreviewHtml and adds Settings modules.
 */
(function () {
  'use strict';

  const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;

  function round2(n) {
    return Math.round(Number(n || 0) * 100) / 100;
  }

  function hopCommIsCommercial(data) {
    const lines = data?.lines || [];
    const title = String(data?.header?.doc_title || '');
    return /commercial/i.test(title)
      || lines.some((ln) => String(ln.section_title || '').trim())
      || lines.some((ln) => Number(ln.discount_pct || 0) > 0.009);
  }

  function hopCommCalcLine(ln) {
    const qty = Number(ln.qty || 0);
    let rate = Number(ln.rate || 0);
    if (!(rate > 0) && qty > 0 && Number(ln.line_total || 0) > 0) {
      rate = Number(ln.line_total || 0) / qty;
    }
    const gross = round2(qty * rate);
    let disc = Number(ln.discount_amount || 0);
    const discPct = Number(ln.discount_pct || 0);
    if (discPct > 0.009) disc = round2(gross * discPct / 100);
    const taxable = Math.max(0, gross - disc);
    const taxPct = Number(ln.tax_pct || 0);
    let tax = Number(ln.tax_amount || 0);
    if (!(tax > 0.009) && taxPct > 0) tax = round2(taxable * taxPct / 100);
    let net = Number(ln.line_total || 0);
    if (!(net > 0.009)) net = round2(taxable + tax);
    return { gross, disc, taxable, tax, net, taxPct, discPct, qty, rate };
  }

  function hopCommGroupSections(lines) {
    if (!lines?.length) return [];
    const out = [];
    let currentTitle = String(lines[0].section_title || '').trim() || 'Items';
    let bucket = [];
    const flush = () => {
      if (!bucket.length) return;
      out.push({
        title: currentTitle,
        lines: bucket.slice(),
        sectionTotal: round2(bucket.reduce((s, ln) => s + hopCommCalcLine(ln).net, 0)),
      });
      bucket = [];
    };
    for (const ln of lines) {
      const st = String(ln.section_title || '').trim();
      if (st && st !== currentTitle && bucket.length) {
        flush();
        currentTitle = st;
      } else if (st) {
        currentTitle = st;
      }
      bucket.push(ln);
    }
    flush();
    return out;
  }

  function hopCommPctLabel(v) {
    const n = Number(v || 0);
    if (n <= 0.009) return '—';
    return Math.abs(n - Math.round(n)) < 0.01 ? `${Math.round(n)}%` : `${n}%`;
  }

  function hopCommQtyLabel(v) {
    const n = Number(v || 0);
    if (Math.abs(n - Math.round(n)) < 0.001) return n.toLocaleString('en-IN');
    return n.toLocaleString('en-IN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  /** FAIRFIELD table amounts — comma decimals, no ₹ prefix in cells. */
  function hopCommMoney(n) {
    const v = Number(n || 0);
    return v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function hopCommIntroHtml(notes) {
    if (!notes) return '';
    let text = String(notes).trim();
    if (!/^dear\s+sir/i.test(text)) text = `Dear Sir,\n\n${text}`;
    return text.split(/\n+/).filter(Boolean).map((p) => `<p>${foEscapeText(p)}</p>`).join('');
  }

  function hopCommBuildUnifiedTable(sections, grand) {
    let bodyRows = '';
    for (const sec of sections) {
      bodyRows += `<tr class="hop-doc-comm-section-row"><td colspan="9">${foEscapeText(sec.title)}</td></tr>`;
      sec.lines.forEach((ln, i) => {
        const c = hopCommCalcLine(ln);
        bodyRows += `<tr class="hop-doc-comm-line-row">
          <td class="cen">${i + 1}</td>
          <td class="item">${foEscapeText(ln.item_name || 'Item')}${ln.description ? `<div class="hop-doc-muted">${foEscapeText(ln.description)}</div>` : ''}</td>
          <td class="num">${hopCommQtyLabel(c.qty)}</td>
          <td class="cen">${foEscapeText(ln.unit || 'MTR')}</td>
          <td class="num">${hopCommMoney(c.rate)}</td>
          <td class="num">${hopCommMoney(c.gross)}</td>
          <td class="cen">${hopCommPctLabel(c.discPct)}</td>
          <td class="cen">${hopCommPctLabel(c.taxPct)}</td>
          <td class="num"><strong>${hopCommMoney(c.net)}</strong></td>
        </tr>`;
      });
      bodyRows += `<tr class="hop-doc-comm-total-row">
        <td colspan="8" class="num"><strong>TOTAL:</strong></td>
        <td class="num"><strong>${hopCommMoney(sec.sectionTotal)}</strong></td>
      </tr>`;
    }
    bodyRows += `<tr class="hop-doc-comm-grand-row">
      <td colspan="8" class="num"><strong>GRAND TOTAL:</strong></td>
      <td class="num"><strong>${hopCommMoney(grand)}</strong></td>
    </tr>`;
    return `
      <div class="hop-doc-comm-wrap">
        <table class="hop-doc-table hop-doc-table-commercial hop-doc-comm-unified">
          <thead><tr>
            <th class="cen">Sl.</th>
            <th>Item Description</th>
            <th class="num">Qty.</th>
            <th class="cen">Unit</th>
            <th class="num">Project Rate</th>
            <th class="num">Amount</th>
            <th class="cen">Discount</th>
            <th class="cen">GST %</th>
            <th class="num">Amount</th>
          </tr></thead>
          <tbody>${bodyRows}</tbody>
        </table>
      </div>`;
  }

  function hopDocLogoHtml(firm) {
    const url = String(firm?.logo_url || '').trim();
    if (url.startsWith('data:') || url.startsWith('http')) {
      return `<img class="hop-doc-logo-img" src="${foEscapeText(url)}" alt="Logo" />`;
    }
    return `<div class="hop-doc-logo" aria-hidden="true">${foEscapeText((firm.name || 'HOP').slice(0, 1).toUpperCase())}</div>`;
  }

  function hopDocSignHtml(firm) {
    const url = String(firm?.signature_url || '').trim();
    if (url.startsWith('data:') || url.startsWith('http')) {
      return `<img class="hop-doc-sign-img" src="${foEscapeText(url)}" alt="Signature" />`;
    }
    return '<div class="hop-doc-sign-space"></div>';
  }

  function hopRenderDocPreviewCommercialHtml(data) {
    const firm = data.firm || {};
    const party = data.party || {};
    const header = data.header || {};
    const lines = data.lines || [];
    const title = header.doc_title || 'Commercial Quotation';
    const sections = hopCommGroupSections(lines);
    const grand = round2(lines.reduce((s, ln) => s + hopCommCalcLine(ln).net, 0));
    const notes = header.notes || '';
    const terms = data.terms || '';
    const delivery = data.delivery_terms || '';

    let tablesHtml = '';
    if (sections.length) {
      tablesHtml = hopCommBuildUnifiedTable(sections, grand);
    } else {
      tablesHtml = `<div class="hop-doc-missing"><strong>No line items.</strong></div>`;
    }

    const bankBits = [firm.bank_name, firm.bank_account, firm.bank_ifsc, firm.bank_holder].filter(Boolean);
    const bankHtml = bankBits.length
      ? `<div class="hop-doc-bank">
          <div class="hop-doc-meta-label">Bank Details</div>
          ${firm.bank_name ? `<div>Bank: ${foEscapeText(firm.bank_name)}</div>` : ''}
          ${firm.bank_account ? `<div>A/C: ${foEscapeText(firm.bank_account)}</div>` : ''}
          ${firm.bank_ifsc ? `<div>IFSC: ${foEscapeText(firm.bank_ifsc)}</div>` : ''}
          ${firm.bank_holder ? `<div>Holder: ${foEscapeText(firm.bank_holder)}</div>` : ''}
        </div>`
      : '';

    return `
      <div class="hop-doc-preview-sheet" id="hop-doc-preview-sheet">
        <div class="hop-doc-firm">
          <div class="hop-doc-firm-text">
            <div class="hop-doc-firm-name">${foEscapeText(firm.name || 'House of Prizm')}</div>
            ${firm.address ? `<div class="hop-doc-muted">${foEscapeText(firm.address)}</div>` : ''}
            ${firm.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(firm.phone)}</div>` : ''}
            ${firm.email ? `<div class="hop-doc-muted">Email: ${foEscapeText(firm.email)}</div>` : ''}
            ${firm.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(firm.gstin)}</div>` : ''}
            ${firm.state ? `<div class="hop-doc-muted">State: ${foEscapeText(firm.state)}</div>` : ''}
          </div>
          ${hopDocLogoHtml(firm)}
        </div>
        <div class="hop-doc-title">${foEscapeText(title)}</div>
        <div class="hop-doc-meta">
          <div>
            <div class="hop-doc-meta-label">${foEscapeText(title)} For</div>
            <div class="hop-doc-party-name">${foEscapeText(party.billing_name || party.name || '—')}</div>
            ${party.address ? `<div class="hop-doc-muted">${foEscapeText(party.address)}</div>` : ''}
            ${party.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(party.gstin)}</div>` : ''}
            ${party.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(party.phone)}</div>` : ''}
          </div>
          <div class="hop-doc-meta-right">
            <div><span class="hop-doc-meta-label">No.</span> ${foEscapeText(header.doc_number || '—')}</div>
            <div><span class="hop-doc-meta-label">Date</span> ${foEscapeText(hopPreviewDate(header.doc_date))}</div>
          </div>
        </div>
        ${notes ? `<div class="hop-doc-letter">${hopCommIntroHtml(notes)}</div>` : ''}
        ${tablesHtml}
        <div class="hop-doc-bottom hop-doc-bottom--commercial">
          <div class="hop-doc-bottom-left">
            ${delivery ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Delivery Terms</div><div>${foEscapeText(delivery)}</div></div>` : ''}
            ${terms ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Terms and Conditions</div><div>${foEscapeText(terms)}</div></div>` : ''}
            ${bankHtml}
          </div>
          <div class="hop-doc-sign-block">
            <div class="hop-doc-muted">For ${foEscapeText(firm.name || 'House of Prizm')}</div>
            ${hopDocSignHtml(firm)}
            <div>Authorized Signatory</div>
          </div>
        </div>
      </div>`;
  }

  function hopRenderDocPreviewVyaparHtml(data) {
    const firm = data.firm || {};
    const party = data.party || {};
    const header = data.header || {};
    const totals = data.totals || {};
    const lines = data.lines || [];
    const title = header.doc_title || 'Document';
    const forLabel = /purchase|payment\s*out|expense/i.test(title) ? `${title} From` : `${title} For`;

    const firmBlock = `
      <div class="hop-doc-firm">
        <div class="hop-doc-firm-text">
          <div class="hop-doc-firm-name">${foEscapeText(firm.name || 'House of Prizm')}</div>
          ${firm.address ? `<div class="hop-doc-muted">${foEscapeText(firm.address)}</div>` : ''}
          ${firm.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(firm.phone)}</div>` : ''}
          ${firm.email ? `<div class="hop-doc-muted">Email: ${foEscapeText(firm.email)}</div>` : ''}
          ${firm.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(firm.gstin)}</div>` : ''}
          ${firm.state ? `<div class="hop-doc-muted">State: ${foEscapeText(firm.state)}</div>` : ''}
        </div>
        ${hopDocLogoHtml(firm)}
      </div>`;

    const metaBlock = `
      <div class="hop-doc-meta">
        <div>
          <div class="hop-doc-meta-label">${foEscapeText(forLabel)}</div>
          <div class="hop-doc-party-name">${foEscapeText(party.billing_name || party.name || '—')}</div>
          ${party.address ? `<div class="hop-doc-muted">${foEscapeText(party.address)}</div>` : ''}
          ${party.contact_person ? `<div class="hop-doc-muted">Contact: ${foEscapeText(party.contact_person)}</div>` : ''}
          ${party.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(party.phone)}</div>` : ''}
          ${party.email ? `<div class="hop-doc-muted">Email: ${foEscapeText(party.email)}</div>` : ''}
          ${party.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(party.gstin)}</div>` : ''}
          ${party.state ? `<div class="hop-doc-muted">State: ${foEscapeText(party.state)}</div>` : ''}
        </div>
        <div class="hop-doc-meta-right">
          <div><span class="hop-doc-meta-label">${foEscapeText(title)} No.</span> ${foEscapeText(header.doc_number || '—')}</div>
          <div><span class="hop-doc-meta-label">Date</span> ${foEscapeText(hopPreviewDate(header.doc_date))}</div>
          ${header.status ? `<div><span class="hop-doc-meta-label">Status</span> ${foEscapeText(header.status)}</div>` : ''}
        </div>
      </div>`;

    let linesHtml = '';
    if (lines.length) {
      const rows = lines.map((ln, i) => {
        const taxPct = Number(ln.tax_pct || 0);
        const taxAmt = Number(ln.tax_amount || 0);
        const qty = Number(ln.qty || 0);
        const disc = Number(ln.discount_amount || 0);
        const lineTotal = Number(ln.line_total || 0);
        let rate = Number(ln.rate || 0);
        if (!(rate > 0) && qty > 0 && lineTotal > 0) {
          rate = (lineTotal - taxAmt + disc) / qty;
        }
        const taxCell = taxPct > 0 ? `${hopPreviewMoney(taxAmt)} (${taxPct}%)` : hopPreviewMoney(taxAmt);
        return `<tr>
          <td>${i + 1}</td>
          <td><div class="hop-doc-item-name">${foEscapeText(ln.item_name || 'Item')}</div>${ln.description ? `<div class="hop-doc-muted">${foEscapeText(ln.description)}</div>` : ''}</td>
          <td>${foEscapeText(ln.hsn || '')}</td>
          <td class="num">${qty.toLocaleString('en-IN')}</td>
          <td>${foEscapeText(ln.unit || 'Pcs')}</td>
          <td class="num">${hopPreviewMoney(rate)}</td>
          <td class="num">${taxCell}</td>
          <td class="num">${hopPreviewMoney(lineTotal)}</td>
        </tr>`;
      }).join('');
      linesHtml = `
        <table class="hop-doc-table">
          <thead><tr>
            <th>#</th><th>Item Name</th><th>HSN/SAC</th><th>Quantity</th>
            <th>Unit</th><th>Price/Unit</th><th>GST</th><th>Amount</th>
          </tr></thead>
          <tbody>${rows}</tbody>
          <tfoot><tr>
            <td colspan="3" class="num"><strong>Total</strong></td>
            <td class="num"><strong>${Number(totals.qty || 0).toLocaleString('en-IN')}</strong></td>
            <td></td><td></td>
            <td class="num"><strong>${hopPreviewMoney(totals.tax_total)}</strong></td>
            <td class="num"><strong>${hopPreviewMoney(totals.grand_total)}</strong></td>
          </tr></tfoot>
        </table>`;
    } else {
      linesHtml = `
        <div class="hop-doc-missing">
          <strong>No item lines in this preview yet.</strong>
          <p>${foEscapeText(data.lines_missing_hint || 'Re-import Vyapar backup to load item details.')}</p>
          <p class="hop-doc-muted">Header total: <strong>${hopPreviewMoney(totals.grand_total || header.total_amount)}</strong></p>
        </div>`;
    }

    const taxLabel = totals.tax_pct > 0 ? `Tax @ ${totals.tax_pct}%` : 'Tax';
    const notes = header.notes || '';
    const delivery = data.delivery_terms || '';
    const bankBits = [firm.bank_name, firm.bank_account, firm.bank_ifsc, firm.bank_holder].filter(Boolean);
    const bankHtml = bankBits.length
      ? `<div class="hop-doc-bank">
          <div class="hop-doc-meta-label">Bank Details</div>
          ${firm.bank_name ? `<div>Bank Name: ${foEscapeText(firm.bank_name)}</div>` : ''}
          ${firm.bank_account ? `<div>Account No: ${foEscapeText(firm.bank_account)}</div>` : ''}
          ${firm.bank_ifsc ? `<div>IFSC: ${foEscapeText(firm.bank_ifsc)}</div>` : ''}
          ${firm.bank_holder ? `<div>Account Holder: ${foEscapeText(firm.bank_holder)}</div>` : ''}
        </div>`
      : '';

    return `
      <div class="hop-doc-preview-sheet" id="hop-doc-preview-sheet">
        ${firmBlock}
        <div class="hop-doc-title">${foEscapeText(title)}</div>
        ${metaBlock}
        ${linesHtml}
        <div class="hop-doc-bottom">
          <div class="hop-doc-bottom-left">
            ${notes ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Description</div><div>${foEscapeText(notes)}</div></div>` : ''}
            ${delivery ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Delivery Terms</div><div>${foEscapeText(delivery)}</div></div>` : ''}
            <div class="hop-doc-section">
              <div class="hop-doc-meta-label">${foEscapeText(title)} Amount in Words</div>
              <div>${foEscapeText(totals.amount_in_words || '')}</div>
            </div>
            <div class="hop-doc-section">
              <div class="hop-doc-meta-label">Terms and Conditions</div>
              <div>${foEscapeText(data.terms || 'Thanks for doing business with us!')}</div>
            </div>
            ${bankHtml}
          </div>
          <div class="hop-doc-totals">
            <div class="hop-doc-tot-row"><span>Sub Total</span><strong>${hopPreviewMoney(totals.sub_total)}</strong></div>
            ${Number(totals.discount_amount || 0) > 0.009 ? `<div class="hop-doc-tot-row"><span>Discount</span><strong>${hopPreviewMoney(totals.discount_amount)}</strong></div>` : ''}
            ${Number(totals.shipping_amount || 0) > 0.009 ? `<div class="hop-doc-tot-row"><span>Shipping</span><strong>${hopPreviewMoney(totals.shipping_amount)}</strong></div>` : ''}
            <div class="hop-doc-tot-row"><span>${foEscapeText(taxLabel)}</span><strong>${hopPreviewMoney(totals.tax_total)}</strong></div>
            <div class="hop-doc-tot-row hop-doc-tot-grand"><span>Total</span><strong>${hopPreviewMoney(totals.grand_total)}</strong></div>
            <div class="hop-doc-sign">
              <div class="hop-doc-muted">For ${foEscapeText(firm.name || 'House of Prizm')}</div>
              ${hopDocSignHtml(firm)}
              <div>Authorized Signatory</div>
            </div>
          </div>
        </div>
      </div>`;
  }

  /** Replaces basic hop_app.js preview — commercial + Vyapar with logo/signature. */
  function hopRenderDocPreviewHtml(data) {
    if (!data) return '<div class="hop-doc-preview-loading">No data</div>';
    if (hopCommIsCommercial(data)) return hopRenderDocPreviewCommercialHtml(data);
    return hopRenderDocPreviewVyaparHtml(data);
  }

  /* ---------- Company profile (Settings) ---------- */
  let _hopFirmDraft = null;

  function hopFirmGstApplyLocal(gstin) {
    const code = gstin.slice(0, 2);
    const stateEl = document.getElementById('hop-firm-state');
    const panEl = document.getElementById('hop-firm-pan');
    if (stateEl && HOP_GSTIN_STATE_CODES[code] && !stateEl.value) stateEl.value = HOP_GSTIN_STATE_CODES[code];
    if (panEl && !panEl.value) panEl.value = gstin.slice(2, 12);
  }

  function hopFirmGstOnInput() {
    const input = document.getElementById('hop-firm-gstin');
    const mark = document.getElementById('hop-firm-gst-ok');
    const hint = document.getElementById('hop-firm-gst-hint');
    const btn = document.getElementById('hop-firm-gst-fetch');
    if (!input) return;
    const v = String(input.value || '').trim().toUpperCase();
    input.value = v;
    const ok = GSTIN_RE.test(v);
    if (mark) {
      mark.classList.toggle('is-valid', ok);
      mark.classList.toggle('hidden', !v);
    }
    if (btn) btn.disabled = !ok;
    if (!ok) {
      if (hint) hint.textContent = v.length >= 15 ? 'Invalid GSTIN format' : '';
      return;
    }
    hopFirmGstApplyLocal(v);
    if (hint) hint.textContent = 'Valid GSTIN — click Verify for registry details';
  }

  async function hopFirmFetchGst() {
    const input = document.getElementById('hop-firm-gstin');
    const hint = document.getElementById('hop-firm-gst-hint');
    const mark = document.getElementById('hop-firm-gst-ok');
    const btn = document.getElementById('hop-firm-gst-fetch');
    const v = String(input?.value || '').trim().toUpperCase();
    if (!GSTIN_RE.test(v)) {
      if (hint) hint.textContent = 'Enter a valid 15-character GSTIN';
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = 'Verifying…'; }
    if (hint) hint.textContent = 'Looking up GSTIN…';
    try {
      const data = await hopApi(`/api/v1/hop/gstin-lookup?gstin=${encodeURIComponent(v)}`);
      if (data?.state) {
        const st = document.getElementById('hop-firm-state');
        if (st && HOP_INDIAN_STATES.includes(data.state)) st.value = data.state;
      }
      if (data?.pan) {
        const pan = document.getElementById('hop-firm-pan');
        if (pan && !pan.value) pan.value = data.pan;
      }
      if (data?.address) {
        const addr = document.getElementById('hop-firm-address');
        if (addr && !addr.value) addr.value = data.address;
        const pin = data.address.match(/\b\d{6}\b/);
        const pc = document.getElementById('hop-firm-pincode');
        if (pc && pin && !pc.value) pc.value = pin[0];
      }
      (data?.company || data?.billing_name)?.trim?.() && (() => {
        const nm = document.getElementById('hop-firm-name');
        if (nm && !nm.value) nm.value = data.company || data.billing_name;
      })();
      if (mark) mark.classList.add('is-verified');
      if (hint) hint.textContent = data?.message || 'GSTIN verified';
    } catch (e) {
      hopFirmGstApplyLocal(v);
      if (hint) hint.textContent = e?.message || 'GST lookup failed';
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Verify GSTIN'; }
    }
  }

  function hopFirmReadImage(inputId, previewId, key) {
    const input = document.getElementById(inputId);
    const file = input?.files?.[0];
    if (!file || !_hopFirmDraft) return;
    const reader = new FileReader();
    reader.onload = () => {
      _hopFirmDraft[key] = reader.result;
      const prev = document.getElementById(previewId);
      if (prev) prev.innerHTML = `<img src="${reader.result}" alt="" />`;
    };
    reader.readAsDataURL(file);
  }

  async function hopFirmSaveProfile() {
    const status = document.getElementById('hop-firm-status');
    const name = String(document.getElementById('hop-firm-name')?.value || '').trim();
    if (!name) {
      if (status) status.textContent = 'Business name is required';
      return;
    }
    const payload = {
      firm_name: name,
      address: String(document.getElementById('hop-firm-address')?.value || '').trim(),
      phone: String(document.getElementById('hop-firm-phone')?.value || '').trim(),
      email: String(document.getElementById('hop-firm-email')?.value || '').trim(),
      gstin: String(document.getElementById('hop-firm-gstin')?.value || '').trim().toUpperCase(),
      pan: String(document.getElementById('hop-firm-pan')?.value || '').trim().toUpperCase(),
      state: String(document.getElementById('hop-firm-state')?.value || '').trim(),
      pincode: String(document.getElementById('hop-firm-pincode')?.value || '').trim(),
      business_type: String(document.getElementById('hop-firm-btype')?.value || '').trim(),
      business_category: String(document.getElementById('hop-firm-bcat')?.value || '').trim(),
      bank_name: String(document.getElementById('hop-firm-bank')?.value || '').trim(),
      bank_account: String(document.getElementById('hop-firm-acct')?.value || '').trim(),
      bank_ifsc: String(document.getElementById('hop-firm-ifsc')?.value || '').trim().toUpperCase(),
      bank_holder: String(document.getElementById('hop-firm-holder')?.value || '').trim(),
      terms_default: String(document.getElementById('hop-firm-terms')?.value || '').trim(),
      delivery_terms: String(document.getElementById('hop-firm-delivery')?.value || '').trim(),
    };
    if (_hopFirmDraft?.logo_url) payload.logo_url = _hopFirmDraft.logo_url;
    if (_hopFirmDraft?.signature_url) payload.signature_url = _hopFirmDraft.signature_url;
    if (status) status.textContent = 'Saving…';
    try {
      await hopApi('/api/v1/hop/firm-profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (status) status.textContent = 'Profile saved — used on all documents';
      if (typeof nexoraToast === 'function') nexoraToast('Company profile saved', 'ok');
    } catch (e) {
      if (status) status.textContent = e?.message || 'Save failed';
    }
  }

  async function renderHopFirmProfileModule(mount) {
    let p = {};
    try {
      p = await hopApi('/api/v1/hop/firm-profile') || {};
    } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Company profile', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
      return;
    }
    _hopFirmDraft = {
      logo_url: p.logo_url || '',
      signature_url: p.signature_url || '',
    };
    const stateOpts = HOP_INDIAN_STATES.map((s) =>
      `<option value="${foEscapeText(s)}"${s === p.state ? ' selected' : ''}>${foEscapeText(s)}</option>`).join('');
    const logoPrev = p.logo_url ? `<img src="${foEscapeText(p.logo_url)}" alt="" />` : '<span class="hop-firm-img-ph">Logo</span>';
    const signPrev = p.signature_url ? `<img src="${foEscapeText(p.signature_url)}" alt="" />` : '<span class="hop-firm-img-ph">Signature</span>';

    const body = `
      <div class="hop-firm-profile">
        <p class="nx-text-dim">Business details printed on estimates, proformas and invoices.</p>
        <div class="hop-firm-grid">
          <label class="hop-firm-field hop-firm-field--wide"><span>Business name *</span>
            <input id="hop-firm-name" class="nx-input" value="${foEscapeText(p.firm_name || '')}" /></label>
          <label class="hop-firm-field"><span>Phone</span>
            <input id="hop-firm-phone" class="nx-input" value="${foEscapeText(p.phone || '')}" /></label>
          <label class="hop-firm-field"><span>Email</span>
            <input id="hop-firm-email" class="nx-input" type="email" value="${foEscapeText(p.email || '')}" /></label>
          <label class="hop-firm-field hop-firm-field--wide"><span>Address</span>
            <textarea id="hop-firm-address" class="nx-input" rows="2">${foEscapeText(p.address || '')}</textarea></label>
          <label class="hop-firm-field"><span>State</span>
            <select id="hop-firm-state" class="nx-input"><option value="">—</option>${stateOpts}</select></label>
          <label class="hop-firm-field"><span>Pincode</span>
            <input id="hop-firm-pincode" class="nx-input" value="${foEscapeText(p.pincode || '')}" /></label>
          <label class="hop-firm-field"><span>Business type</span>
            <input id="hop-firm-btype" class="nx-input" value="${foEscapeText(p.business_type || '')}" placeholder="e.g. Retailer" /></label>
          <label class="hop-firm-field"><span>Category</span>
            <input id="hop-firm-bcat" class="nx-input" value="${foEscapeText(p.business_category || '')}" placeholder="e.g. Furnishing" /></label>
          <label class="hop-firm-field hop-firm-field--wide hop-firm-gst-row">
            <span>GSTIN</span>
            <div class="hop-firm-gst-wrap">
              <input id="hop-firm-gstin" class="nx-input" value="${foEscapeText(p.gstin || '')}" maxlength="15" oninput="hopFirmGstOnInput()" />
              <span id="hop-firm-gst-ok" class="hop-gst-tick${p.gstin ? ' is-valid' : ''}${p.gstin ? '' : ' hidden'}" aria-hidden="true">✓</span>
              <button type="button" id="hop-firm-gst-fetch" class="nx-btn" onclick="hopFirmFetchGst()">Verify GSTIN</button>
            </div>
            <small id="hop-firm-gst-hint" class="hop-firm-hint"></small>
          </label>
          <label class="hop-firm-field"><span>PAN</span>
            <input id="hop-firm-pan" class="nx-input" value="${foEscapeText(p.pan || '')}" maxlength="10" /></label>
          <div class="hop-firm-field"><span>Logo</span>
            <div id="hop-firm-logo-prev" class="hop-firm-img-prev">${logoPrev}</div>
            <input type="file" accept="image/*" id="hop-firm-logo-file" class="hop-file-hidden" onchange="hopFirmReadImage('hop-firm-logo-file','hop-firm-logo-prev','logo_url')" />
            <button type="button" class="nx-btn" onclick="document.getElementById('hop-firm-logo-file').click()">Upload logo</button>
          </div>
          <div class="hop-firm-field"><span>Signature</span>
            <div id="hop-firm-sign-prev" class="hop-firm-img-prev hop-firm-sign-prev">${signPrev}</div>
            <input type="file" accept="image/*" id="hop-firm-sign-file" class="hop-file-hidden" onchange="hopFirmReadImage('hop-firm-sign-file','hop-firm-sign-prev','signature_url')" />
            <button type="button" class="nx-btn" onclick="document.getElementById('hop-firm-sign-file').click()">Upload signature</button>
          </div>
          <label class="hop-firm-field"><span>Bank name</span>
            <input id="hop-firm-bank" class="nx-input" value="${foEscapeText(p.bank_name || '')}" /></label>
          <label class="hop-firm-field"><span>Account no</span>
            <input id="hop-firm-acct" class="nx-input" value="${foEscapeText(p.bank_account || '')}" /></label>
          <label class="hop-firm-field"><span>IFSC</span>
            <input id="hop-firm-ifsc" class="nx-input" value="${foEscapeText(p.bank_ifsc || '')}" /></label>
          <label class="hop-firm-field"><span>Account holder</span>
            <input id="hop-firm-holder" class="nx-input" value="${foEscapeText(p.bank_holder || '')}" /></label>
          <label class="hop-firm-field hop-firm-field--wide"><span>Default terms</span>
            <textarea id="hop-firm-terms" class="nx-input" rows="3">${foEscapeText(p.terms_default || '')}</textarea></label>
          <label class="hop-firm-field hop-firm-field--wide"><span>Delivery terms</span>
            <textarea id="hop-firm-delivery" class="nx-input" rows="2">${foEscapeText(p.delivery_terms || '')}</textarea></label>
        </div>
        <div class="hop-firm-actions">
          <p id="hop-firm-status" class="hop-firm-hint"></p>
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopFirmSaveProfile()">Save profile</button>
        </div>
      </div>`;
    mount.innerHTML = hopModuleShell('Settings', 'Company profile', 'Logo, GSTIN, bank & terms for documents', '', body);
    hopFirmGstOnInput();
  }

  /* ---------- Manual Estimate / Proforma create ---------- */
  function hopEmptyDocLine() {
    return { item_name: '', qty: '', unit: 'MTR', rate: '', discount_pct: '0', tax_pct: '5', hsn: '' };
  }

  function hopManualDocInit(txnType, mode) {
    const isComm = mode === 'commercial' && txnType === 27;
    hopState.manualDoc = {
      txnType: Number(txnType),
      mode: mode || 'standard',
      returnView: hopState.view || (txnType === 83 ? 'sale_proforma' : 'sale_estimates'),
    };
    hopState.manualDocDraft = {
      customerId: '',
      txnDate: new Date().toISOString().slice(0, 10),
      notes: isComm
        ? 'Dear Sir,\n\nWe thank you for the enquiry. Having completed our review of your requirements, we are pleased to submit the Commercial offer for it.'
        : '',
      docTerms: '',
      sections: isComm
        ? [{ title: 'Shortlisted-1 (Sheer + Chair Fabric)', lines: [hopEmptyDocLine(), hopEmptyDocLine()] }]
        : null,
      lines: isComm ? null : [hopEmptyDocLine(), hopEmptyDocLine()],
    };
  }

  function hopOpenManualDocCreate(txnType, mode) {
    hopManualDocInit(txnType, mode);
    openHopView('hop_manual_doc_create');
  }

  function hopManualDocLineFilled(ln) {
    const q = Number(ln.qty);
    const r = Number(ln.rate);
    return String(ln.item_name || '').trim() && q > 0 && !Number.isNaN(r) && r >= 0;
  }

  function hopManualDocCalcGrand() {
    const d = hopState.manualDocDraft;
    if (!d) return 0;
    const allLines = d.sections
      ? d.sections.flatMap((s) => s.lines.filter(hopManualDocLineFilled))
      : d.lines.filter(hopManualDocLineFilled);
    return round2(allLines.reduce((sum, ln) => {
      const qty = Number(ln.qty);
      const rate = Number(ln.rate);
      const gross = round2(qty * rate);
      const discPct = Number(ln.discount_pct || 0);
      const disc = discPct > 0 ? round2(gross * discPct / 100) : 0;
      const taxable = Math.max(0, gross - disc);
      const gst = Number(ln.tax_pct || 0);
      const tax = round2(taxable * gst / 100);
      return sum + round2(taxable + tax);
    }, 0));
  }

  function hopManualDocLineRowHtml(ln, si, li, showDisc) {
    return `<tr>
      <td class="cen hop-comm-sl">${li + 1}</td>
      <td><input class="nx-input hop-comm-inp" value="${foEscapeText(ln.item_name)}" oninput="hopManualDocSetLine(${si},${li},'item_name',this.value)" placeholder="Item Description" /></td>
      <td><input class="nx-input hop-comm-inp num" value="${foEscapeText(ln.qty)}" oninput="hopManualDocSetLine(${si},${li},'qty',this.value)" inputmode="decimal" placeholder="Qty" /></td>
      <td><input class="nx-input hop-comm-inp cen" value="${foEscapeText(ln.unit)}" oninput="hopManualDocSetLine(${si},${li},'unit',this.value)" /></td>
      <td><input class="nx-input hop-comm-inp num" value="${foEscapeText(ln.rate)}" oninput="hopManualDocSetLine(${si},${li},'rate',this.value)" inputmode="decimal" placeholder="Rate" /></td>
      ${showDisc ? `<td><input class="nx-input hop-comm-inp cen" value="${foEscapeText(ln.discount_pct)}" oninput="hopManualDocSetLine(${si},${li},'discount_pct',this.value)" inputmode="decimal" /></td>` : ''}
      <td><input class="nx-input hop-comm-inp cen" value="${foEscapeText(ln.tax_pct)}" oninput="hopManualDocSetLine(${si},${li},'tax_pct',this.value)" inputmode="decimal" /></td>
      ${!showDisc ? `<td><input class="nx-input hop-comm-inp" value="${foEscapeText(ln.hsn)}" oninput="hopManualDocSetLine(${si},${li},'hsn',this.value)" /></td>` : ''}
      <td><button type="button" class="nx-btn nx-btn-ghost hop-comm-del" onclick="hopManualDocRemoveLine(${si},${li})" title="Remove">×</button></td>
    </tr>`;
  }

  function hopManualDocRenderBody(customers, firmTerms) {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    if (!d.docTerms && firmTerms) d.docTerms = firmTerms;
    const isComm = md.mode === 'commercial' && md.txnType === 27;
    const title = md.txnType === 83 ? 'New Proforma Invoice' : (isComm ? 'Commercial quotation' : 'New Estimate');
    const custOpts = (customers || []).map((c) =>
      `<option value="${c.id}"${String(d.customerId) === String(c.id) ? ' selected' : ''}>${foEscapeText(c.company || c.name || '—')}</option>`).join('');

    let linesBlock = '';
    if (isComm && d.sections) {
      linesBlock = d.sections.map((sec, si) => `
        <div class="hop-comm-section">
          <div class="hop-comm-section-head">
            <input class="nx-input" value="${foEscapeText(sec.title)}" oninput="hopManualDocSetSectionTitle(${si},this.value)" placeholder="Section e.g. Shortlisted-1 (Sheer + Chair Fabric)" />
            <button type="button" class="nx-btn" onclick="hopManualDocAddLine(${si})">+ Line</button>
          </div>
          <table class="hop-comm-table hop-comm-table--form">
            <thead><tr>
              <th class="cen">Sl.</th><th>Item Description</th><th class="num">Qty.</th><th class="cen">Unit</th>
              <th class="num">Project Rate</th><th class="cen">Discount</th><th class="cen">GST %</th><th></th>
            </tr></thead>
            <tbody>${sec.lines.map((ln, li) => hopManualDocLineRowHtml(ln, si, li, true)).join('')}</tbody>
          </table>
        </div>`).join('');
      linesBlock += `<button type="button" class="nx-btn" onclick="hopManualDocAddSection()">+ Add section</button>`;
    } else {
      const lines = d.lines || [];
      linesBlock = `
        <table class="hop-comm-table">
          <thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Rate</th><th>GST%</th><th>HSN</th><th></th></tr></thead>
          <tbody>${lines.map((ln, li) => hopManualDocLineRowHtml(ln, -1, li, false)).join('')}</tbody>
        </table>
        <button type="button" class="nx-btn" onclick="hopManualDocAddLine(-1)">+ Add line</button>`;
    }

    const grand = hopManualDocCalcGrand();
    return `
      <div class="hop-manual-doc">
        <p id="hop-manual-doc-err" class="nx-oc-error"></p>
        <div class="hop-manual-doc-sticky">
          <div class="hop-manual-doc-grid">
          <label><span>Customer *</span>
            <select id="hop-manual-customer" class="nx-input" onchange="hopManualDocSetCustomer(this.value)">
              <option value="">Select customer</option>${custOpts}
            </select></label>
          <label><span>Date</span>
            <input type="date" class="nx-input" value="${foEscapeText(d.txDate)}" onchange="hopManualDocSetField('txnDate',this.value)" /></label>
        </div>
        </div>
        ${isComm ? `<label class="hop-manual-wide"><span>Cover letter (Dear Sir…)</span>
          <textarea class="nx-input" rows="4" oninput="hopManualDocSetField('notes',this.value)">${foEscapeText(d.notes)}</textarea></label>` : ''}
        ${linesBlock}
        <label class="hop-manual-wide"><span>Terms & conditions</span>
          <textarea class="nx-input" rows="3" oninput="hopManualDocSetField('docTerms',this.value)">${foEscapeText(d.docTerms)}</textarea></label>
        <div class="hop-manual-doc-foot">
          <strong id="hop-manual-doc-grand">Grand total: ${hopPreviewMoney(grand)}</strong>
          <div class="hop-manual-doc-btns">
            <button type="button" class="nx-btn" onclick="openHopView('${foEscapeText(md.returnView || 'sale_estimates')}')">Cancel</button>
            <button type="button" class="nx-btn nx-btn-primary" onclick="hopManualDocSave()">Save & preview</button>
          </div>
        </div>
      </div>`;
  }

  function hopManualDocRefresh(customers, firmTerms) {
    const el = document.getElementById('hop-manual-doc-body');
    if (el) el.innerHTML = hopManualDocRenderBody(customers, firmTerms);
  }

  function hopManualDocUpdateGrandTotal() {
    const el = document.getElementById('hop-manual-doc-grand');
    if (el) el.textContent = `Grand total: ${hopPreviewMoney(hopManualDocCalcGrand())}`;
  }

  function hopManualDocSetCustomer(v) {
    if (hopState.manualDocDraft) hopState.manualDocDraft.customerId = v;
  }

  function hopManualDocSetField(key, val) {
    if (hopState.manualDocDraft) hopState.manualDocDraft[key] = val;
  }

  function hopManualDocSetSectionTitle(si, val) {
    if (hopState.manualDocDraft?.sections?.[si]) hopState.manualDocDraft.sections[si].title = val;
  }

  function hopManualDocSetLine(si, li, key, val) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    const bucket = si >= 0 ? d.sections?.[si]?.lines : d.lines;
    if (!bucket?.[li]) return;
    bucket[li][key] = val;
    if (key === 'qty' || key === 'rate' || key === 'discount_pct' || key === 'tax_pct') {
      hopManualDocUpdateGrandTotal();
    }
  }

  function hopManualDocAddSection() {
    const d = hopState.manualDocDraft;
    if (!d?.sections) return;
    const n = d.sections.length + 1;
    d.sections.push({ title: `Shortlisted-${n}`, lines: [hopEmptyDocLine(), hopEmptyDocLine()] });
    hopManualDocRefresh(hopState.manualDocCustomers, hopState.manualDocFirmTerms);
  }

  function hopManualDocAddLine(si) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    if (si >= 0 && d.sections?.[si]) {
      d.sections[si].lines.push(hopEmptyDocLine());
    } else if (d.lines) {
      d.lines.push(hopEmptyDocLine());
    }
    hopManualDocRefresh(hopState.manualDocCustomers, hopState.manualDocFirmTerms);
  }

  function hopManualDocRemoveLine(si, li) {
    const d = hopState.manualDocDraft;
    if (!d) return;
    if (si >= 0 && d.sections?.[si]) {
      d.sections[si].lines.splice(li, 1);
      if (!d.sections[si].lines.length) d.sections[si].lines.push(hopEmptyDocLine());
    } else if (d.lines) {
      d.lines.splice(li, 1);
      if (!d.lines.length) d.lines.push(hopEmptyDocLine());
    }
    hopManualDocRefresh(hopState.manualDocCustomers, hopState.manualDocFirmTerms);
  }

  async function hopManualDocSave() {
    const md = hopState.manualDoc || {};
    const d = hopState.manualDocDraft || {};
    const errEl = document.getElementById('hop-manual-doc-err');
    const custId = Number(d.customerId || document.getElementById('hop-manual-customer')?.value || 0);
    if (!custId) {
      if (errEl) errEl.textContent = 'Select a customer';
      return;
    }
    const isComm = md.mode === 'commercial' && md.txnType === 27;
    const rawLines = [];
    if (isComm && d.sections) {
      for (const sec of d.sections) {
        for (const ln of sec.lines) {
          if (!hopManualDocLineFilled(ln)) continue;
          rawLines.push({
            item_name: String(ln.item_name).trim(),
            qty: Number(ln.qty),
            unit: String(ln.unit || 'MTR').trim() || 'MTR',
            rate: Number(ln.rate),
            tax_pct: Number(ln.tax_pct || 0),
            discount_pct: Number(ln.discount_pct || 0),
            section_title: String(sec.title || '').trim(),
          });
        }
      }
    } else {
      for (const ln of (d.lines || [])) {
        if (!hopManualDocLineFilled(ln)) continue;
        rawLines.push({
          item_name: String(ln.item_name).trim(),
          qty: Number(ln.qty),
          unit: String(ln.unit || 'Pcs').trim() || 'Pcs',
          rate: Number(ln.rate),
          tax_pct: Number(ln.tax_pct || 0),
          hsn: String(ln.hsn || '').trim(),
        });
      }
    }
    if (!rawLines.length) {
      if (errEl) errEl.textContent = 'Add at least one item with qty and rate';
      return;
    }
    const payload = {
      txn_type: md.txnType,
      customer_id: custId,
      txn_date: d.txnDate,
      notes: String(d.notes || '').trim() || undefined,
      doc_terms: String(d.docTerms || '').trim() || undefined,
      txn_label: isComm ? 'Commercial Quotation' : (md.txnType === 83 ? 'Proforma Invoice' : 'Estimate / Quotation'),
      lines: rawLines,
    };
    if (errEl) errEl.textContent = 'Saving…';
    try {
      const row = await hopApi('/api/v1/hop/party-transactions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const id = row?.party_txn_id || row?.id;
      if (!id) throw new Error('Save failed — no document id returned');
      if (typeof nexoraToast === 'function') nexoraToast('Document saved', 'ok');
      openHopView(md.returnView || 'sale_estimates');
      hopOpenSaleDocPreview(Number(id), 0);
    } catch (e) {
      if (errEl) errEl.textContent = e?.message || 'Save failed';
    }
  }

  async function renderHopManualDocCreateModule(mount) {
    if (!hopState.manualDoc || !hopState.manualDocDraft) {
      hopManualDocInit(27, 'commercial');
    }
    let customers = [];
    let firmTerms = '';
    try {
      const [cust, firm] = await Promise.all([
        hopApi('/api/v1/hop/customers'),
        hopApi('/api/v1/hop/firm-profile').catch(() => ({})),
      ]);
      customers = cust || [];
      firmTerms = firm?.terms_default || '';
      hopState.manualDocCustomers = customers;
      hopState.manualDocFirmTerms = firmTerms;
    } catch (e) {
      mount.innerHTML = hopModuleShell('Sale', 'Create document', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
      return;
    }
    const md = hopState.manualDoc;
    const title = md.txnType === 83 ? 'New Proforma Invoice' : (md.mode === 'commercial' ? 'Commercial quotation' : 'New Estimate');
    const body = `<div id="hop-manual-doc-body">${hopManualDocRenderBody(customers, firmTerms)}</div>`;
    mount.innerHTML = hopModuleShell('Sale', title, '', '', body);
  }

  /* ---------- Expose globals (hop_app.js loaders + onclick) ---------- */
  window.hopRenderDocPreviewHtml = hopRenderDocPreviewHtml;
  window.renderHopFirmProfileModule = renderHopFirmProfileModule;
  window.renderHopManualDocCreateModule = renderHopManualDocCreateModule;
  window.hopOpenManualDocCreate = hopOpenManualDocCreate;
  window.hopFirmGstOnInput = hopFirmGstOnInput;
  window.hopFirmFetchGst = hopFirmFetchGst;
  window.hopFirmReadImage = hopFirmReadImage;
  window.hopFirmSaveProfile = hopFirmSaveProfile;
  window.hopManualDocSetCustomer = hopManualDocSetCustomer;
  window.hopManualDocSetField = hopManualDocSetField;
  window.hopManualDocSetSectionTitle = hopManualDocSetSectionTitle;
  window.hopManualDocSetLine = hopManualDocSetLine;
  window.hopManualDocAddSection = hopManualDocAddSection;
  window.hopManualDocAddLine = hopManualDocAddLine;
  window.hopManualDocRemoveLine = hopManualDocRemoveLine;
  window.hopManualDocSave = hopManualDocSave;
})();
