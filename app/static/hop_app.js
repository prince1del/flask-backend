/* House of Prizm UI — project-centric ERP shell (hop_admin only). */

const hopState = {
  view: 'dashboard',
  viewHistory: [],
  customers: [],
  projects: [],
  leads: [],
  meetings: [],
  vendors: [],
  quotations: [],
  orders: [],
  invoices: [],
  hub: null,
  hubTab: 'overview',
  reloadTimers: {},
  search: {},
  rateMatrix: null,
  rateCart: [],
  rateFilters: { q: '' },
  vyaparBackupFile: null,
  vyaparImportPreview: null,
  contactSelect: {
    customers: { mode: false, ids: [] },
    vendors: { mode: false, ids: [] },
  },
  contactEdit: null,
  invoiceUi: {
    period: 'this_month',
    from: '',
    to: '',
    status: 'all',
    q: '',
    party: '',
  },
};

const HOP_RATE_CART_KEY = 'hop_rate_cart_v1';

function hopLoadRateCart() {
  try {
    const raw = localStorage.getItem(HOP_RATE_CART_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    hopState.rateCart = Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    hopState.rateCart = [];
  }
  return hopState.rateCart;
}

function hopSaveRateCart() {
  localStorage.setItem(HOP_RATE_CART_KEY, JSON.stringify(hopState.rateCart || []));
}

function hopRefreshRateMatrixCartButtons() {
  const count = (hopState.rateCart || []).length;
  const summaryEl = document.querySelector('.hop-rate-summary span:last-child');
  if (summaryEl) summaryEl.textContent = `${count} in quote cart`;
  document.querySelectorAll('.hop-rate-add-btn.is-in-cart').forEach((btn) => {
    btn.classList.remove('is-in-cart');
    if (btn.textContent.trim() === 'Added +') btn.textContent = 'Add to cart';
  });
}

async function hopClearRateQuote() {
  hopLoadRateCart();
  if (!(hopState.rateCart || []).length) {
    alert('Quote cart already empty.');
    return;
  }
  if (!(await nexoraConfirm('Clear quote cart? Selected rates will be removed.', {
    title: 'Clear quote cart',
    danger: true,
    okText: 'Clear cart',
  }))) return;
  hopState.rateCart = [];
  hopState.rateCartExpanded = false;
  hopSaveRateCart();
  hopRenderRateCartPanel();
  hopRefreshRateMatrixCartButtons();
}

function hopRateCartKey(item) {
  return `${item.product_key}::${item.sheet_id}`;
}

function hopAddToRateCart(productKeyEnc, sheetId) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  const matrix = hopState.rateMatrix;
  if (!matrix) return;
  const product = (matrix.products || []).find((p) => p.product_key === productKey);
  if (!product) return;
  const offer = (product.offers || {})[String(sheetId)];
  if (!offer || offer.missing || !(Number(offer.rate) > 0)) {
    alert('Is seller pe is product ka rate nahi hai.');
    return;
  }
  hopLoadRateCart();
  let qty = 1;
  const next = {
    product_key: productKey,
    label: product.label,
    size: product.size || '',
    quality: offer.quality || product.quality_hint || '',
    sheet_id: Number(sheetId),
    supplier_name: offer.supplier_name,
    rate: Number(offer.rate),
    gst_pct: Number(offer.gst_pct || 0),
    landed_rate: Number(offer.landed_rate || offer.rate),
    qty,
    line_id: offer.line_id || null,
  };
  const idx = hopState.rateCart.findIndex((c) => hopRateCartKey(c) === hopRateCartKey(next));
  if (idx >= 0) {
    hopState.rateCart[idx].qty = Number(hopState.rateCart[idx].qty || 0) + qty;
  } else {
    hopState.rateCart.push(next);
  }
  hopSaveRateCart();
  // Keep cart compact so matrix stays visible
  hopState.rateCartExpanded = false;
  hopRenderRateCartPanel();
  // Light feedback on the button without full page re-render
  const btn = document.querySelector(`button[onclick="hopAddToRateCart('${productKeyEnc}', ${sheetId})"]`);
  if (btn) {
    btn.textContent = 'Added +';
    btn.classList.add('is-in-cart');
  }
}

function hopAddBestToRateCart(productKeyEnc) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  const product = (hopState.rateMatrix?.products || []).find((p) => p.product_key === productKey);
  if (!product?.best || product.best.sheet_id == null) {
    alert('Is product pe best rate nahi mila.');
    return;
  }
  hopAddToRateCart(productKeyEnc, product.best.sheet_id);
}

function hopRemoveRateCartItem(productKeyEnc, sheetId) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  hopLoadRateCart();
  hopState.rateCart = hopState.rateCart.filter(
    (c) => !(c.product_key === productKey && String(c.sheet_id) === String(sheetId)),
  );
  hopSaveRateCart();
  hopRenderRateCartPanel();
}

function hopUpdateRateCartQty(productKeyEnc, sheetId, value) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  hopLoadRateCart();
  const item = hopState.rateCart.find(
    (c) => c.product_key === productKey && String(c.sheet_id) === String(sheetId),
  );
  if (!item) return;
  let qty = Number(value);
  if (!Number.isFinite(qty) || qty <= 0) qty = 1;
  item.qty = qty;
  hopSaveRateCart();
  hopRenderRateCartPanel();
}

function hopRateCartGroups() {
  hopLoadRateCart();
  const groups = new Map();
  for (const item of hopState.rateCart) {
    const key = `${item.sheet_id}::${item.supplier_name}`;
    if (!groups.has(key)) {
      groups.set(key, {
        sheet_id: item.sheet_id,
        supplier_name: item.supplier_name,
        items: [],
        subtotal: 0,
        landed_total: 0,
      });
    }
    const g = groups.get(key);
    const qty = Number(item.qty || 1);
    g.items.push(item);
    g.subtotal += Number(item.rate || 0) * qty;
    g.landed_total += Number(item.landed_rate || item.rate || 0) * qty;
  }
  return [...groups.values()];
}

function hopToggleRateCartExpand() {
  hopState.rateCartExpanded = !hopState.rateCartExpanded;
  hopRenderRateCartPanel();
}

function hopRenderRateCartPanel() {
  const el = document.getElementById('hop-rate-cart-panel');
  if (!el) return;
  const groups = hopRateCartGroups();
  const count = (hopState.rateCart || []).length;
  const open = Boolean(hopState.rateCartExpanded);
  el.classList.toggle('is-empty', !count);
  el.classList.toggle('is-collapsed', count > 0 && !open);

  if (!count) {
    el.innerHTML = `
      <div class="hop-rate-cart-bar">
        <div class="hop-rate-cart-bar-main">
          <strong>Quote cart</strong>
          <span class="nx-text-dim">empty — matrix me Add to cart dabao</span>
        </div>
      </div>`;
    return;
  }

  const landedAll = groups.reduce((s, g) => s + Number(g.landed_total || 0), 0);
  const supplierBits = groups.map((g) => `${hopCell(g.supplier_name)} (${g.items.length})`).join(' · ');

  const blocks = groups.map((g) => `
    <div class="hop-rate-cart-group">
      <div class="hop-rate-cart-group-head">
        <strong>${hopCell(g.supplier_name)}</strong>
        <span class="hop-rate-sub">${g.items.length} · ${hopMoney(g.landed_total)}</span>
      </div>
      <table class="hop-table hop-rate-cart-table"><thead><tr>
        <th>Product</th><th>Rate</th><th>Qty</th><th>Line</th><th></th>
      </tr></thead><tbody>
        ${g.items.map((it) => {
          const enc = encodeURIComponent(it.product_key);
          const line = Number(it.landed_rate || it.rate || 0) * Number(it.qty || 1);
          return `<tr>
            <td>${hopCell(it.label)}${it.size ? `<div class="hop-rate-sub">${hopCell(it.size)}</div>` : ''}</td>
            <td>${hopMoney(it.rate)}<div class="hop-rate-sub">+${hopCell(it.gst_pct)}%</div></td>
            <td><input type="number" min="1" step="1" value="${hopCell(it.qty)}" class="hop-rate-cart-qty"
              onchange="hopUpdateRateCartQty('${enc}', ${it.sheet_id}, this.value)" /></td>
            <td>${hopMoney(line)}</td>
            <td><button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopRemoveRateCartItem('${enc}', ${it.sheet_id})">✕</button></td>
          </tr>`;
        }).join('')}
      </tbody></table>
    </div>
  `).join('');

  el.innerHTML = `
    <div class="hop-rate-cart-bar">
      <div class="hop-rate-cart-bar-main">
        <strong>Quote cart</strong>
        <span class="hop-rate-cart-pill">${count}</span>
        <span class="nx-text-dim">${groups.length} supplier · ${hopMoney(landedAll)}</span>
        <span class="hop-rate-cart-suppliers nx-text-dim">${supplierBits}</span>
      </div>
      <div class="hop-rate-cart-bar-actions">
        <button type="button" class="nx-btn" onclick="hopToggleRateCartExpand()">${open ? 'Hide' : 'Details'}</button>
        <button type="button" class="nx-btn" onclick="hopClearRateQuote()">Clear</button>
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopPlaceRateCartOrders()">Place orders</button>
      </div>
    </div>
    <div class="hop-rate-cart-body${open ? '' : ' hidden'}">
      ${blocks}
      <p id="hop-rate-cart-status" class="nx-text-dim hop-rate-cart-status"></p>
    </div>
  `;
}

async function hopPlaceRateCartOrders() {
  const groups = hopRateCartGroups();
  if (!groups.length) {
    alert('Quote cart empty.');
    return;
  }
  const status = document.getElementById('hop-rate-cart-status');
  if (status) status.textContent = 'Creating supplier orders…';
  try {
    const data = await hopApi('/api/v1/hop/rate-cart/place-orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        groups: groups.map((g) => ({
          sheet_id: g.sheet_id,
          supplier_name: g.supplier_name,
          items: g.items,
          order_value: Math.round(g.landed_total * 100) / 100,
        })),
      }),
    });
    hopState.rateCart = [];
    hopSaveRateCart();
    const created = data?.orders || [];
    if (status) {
      status.textContent = `Created ${created.length} order(s): ${created.map((o) => o.po_number || o.id).join(', ')}`;
    }
    alert(`Orders placed for ${created.length} supplier(s). Check Orders / PO.`);
    openHopView('orders');
  } catch (e) {
    if (status) status.textContent = '';
    alert(e.message || 'Could not place orders');
  }
}

function hopPruneCartForClearedRates(productKeys, sheetId) {
  hopLoadRateCart();
  const keys = new Set((productKeys || []).map((k) => String(k)));
  hopState.rateCart = (hopState.rateCart || []).filter((c) => {
    if (sheetId != null && String(c.sheet_id) === String(sheetId) && (!keys.size || keys.has(c.product_key))) {
      return false;
    }
    if (keys.size && keys.has(c.product_key) && sheetId == null) return false;
    return true;
  });
  hopSaveRateCart();
}

function hopToggleSelectAllRates(checked) {
  document.querySelectorAll('.hop-rate-row-check').forEach((el) => {
    const tr = el.closest('tr');
    if (tr && tr.classList.contains('hop-rate-row-hidden')) {
      el.checked = false;
      return;
    }
    el.checked = Boolean(checked);
  });
}

function hopSelectedRateProductKeys() {
  return [...document.querySelectorAll('.hop-rate-row-check:checked')]
    .map((el) => decodeURIComponent(el.value || ''))
    .filter(Boolean);
}

async function hopClearRatesApi(payload) {
  const data = await hopApi('/api/v1/hop/rate-lines/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const n = Number(data?.deleted_lines || 0);
  if (payload?.clear_all) {
    nexoraToast(n ? `Cleared ${n} rate line(s) and all sheets.` : 'All rate data cleared.', 'success');
  } else if (n === 0) {
    nexoraToast('No matching rates found to delete.', 'warn');
  } else {
    nexoraToast(`Deleted ${n} rate line(s).`, 'success');
  }
  return data;
}

/** Stable line ids from the loaded matrix — preferred over product_key for deletes. */
function hopLineIdsForProductKey(productKey, sheetId = null) {
  const product = (hopState.rateMatrix?.products || []).find((p) => p.product_key === productKey);
  if (!product) return [];
  const ids = [];
  Object.values(product.offers || {}).forEach((offer) => {
    if (!offer || offer.missing || offer.line_id == null || offer.line_id === '') return;
    if (sheetId != null && String(offer.sheet_id) !== String(sheetId)) return;
    const n = Number(offer.line_id);
    if (Number.isFinite(n)) ids.push(n);
  });
  return ids;
}

async function hopClearSingleProduct(productKeyEnc) {
  const key = decodeURIComponent(productKeyEnc || '');
  if (!key) return;
  if (!(await nexoraConfirm('Delete this item from comparison? Rates for this product (all suppliers) will be removed.', {
    title: 'Delete item',
    danger: true,
    okText: 'Delete item',
  }))) return;
  try {
    const lineIds = hopLineIdsForProductKey(key);
    await hopClearRatesApi({ product_keys: [key], line_ids: lineIds });
    hopPruneCartForClearedRates([key], null);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearSingleRate(productKeyEnc, sheetId) {
  const key = decodeURIComponent(productKeyEnc || '');
  if (!key || sheetId == null) return;
  if (!(await nexoraConfirm('Remove this supplier rate for this item only?', {
    title: 'Remove rate',
    danger: true,
    okText: 'Remove',
  }))) return;
  try {
    const lineIds = hopLineIdsForProductKey(key, sheetId);
    await hopClearRatesApi({ product_keys: [key], line_ids: lineIds, sheet_id: sheetId });
    hopPruneCartForClearedRates([key], sheetId);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Remove failed');
  }
}

async function hopClearSelectedRates() {
  const keys = hopSelectedRateProductKeys();
  if (!keys.length) {
    alert('Pehle checkbox se items select karo.');
    return;
  }
  if (!(await nexoraConfirm(`Delete ${keys.length} selected item(s) from comparison?`, {
    title: 'Delete selected items',
    danger: true,
    okText: 'Delete items',
  }))) return;
  try {
    const lineIds = keys.flatMap((k) => hopLineIdsForProductKey(k));
    await hopClearRatesApi({ product_keys: keys, line_ids: lineIds });
    hopPruneCartForClearedRates(keys, null);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearAllRates() {
  if (!(await nexoraConfirm('Delete ALL items and rate sheets? Yeh undo nahi hoga.', {
    title: 'Delete everything',
    danger: true,
    okText: 'Delete all',
  }))) return;
  try {
    await hopClearRatesApi({ clear_all: true });
    hopState.rateCart = [];
    hopSaveRateCart();
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearSheetRates(sheetId, supplierNameEnc) {
  const label = decodeURIComponent(supplierNameEnc || '') || `sheet #${sheetId}`;
  if (!(await nexoraConfirm(`Clear all item rates for vendor ${label}? Sheet column bhi hata di jayegi.`, {
    title: 'Clear vendor rates',
    danger: true,
    okText: 'Clear vendor',
  }))) return;
  try {
    await hopClearRatesApi({ sheet_id: sheetId });
    hopPruneCartForClearedRates([], sheetId);
    // prune all cart items for this sheet
    hopLoadRateCart();
    hopState.rateCart = (hopState.rateCart || []).filter((c) => String(c.sheet_id) !== String(sheetId));
    hopSaveRateCart();
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Clear failed');
  }
}

const HOP_LEAD_STAGES = [
  'new_lead', 'contacted', 'meeting_scheduled', 'samples_sent', 'boq_received',
  'quotation_sent', 'negotiation', 'po_expected', 'order_won', 'lost',
];

const HOP_PROJECT_STAGES = [
  'lead', 'meeting', 'requirement', 'sample', 'boq', 'vendor', 'quotation',
  'negotiation', 'po', 'production', 'dispatch', 'invoice', 'payment',
  'after_sales', 'closed', 'lost',
];

function formatHopKpiValue(value) {
  if (value == null || value === '') return { text: 'N/A', na: true };
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return { text: 'N/A', na: true };
    if (Math.abs(value) >= 1000) return { text: value.toLocaleString('en-IN'), na: false };
    return { text: String(value), na: false };
  }
  return { text: String(value), na: false };
}

function renderHopKpiCard(label, value, moduleKey) {
  const formatted = formatHopKpiValue(value);
  const target = moduleKey ? `onclick="openHopView('${moduleKey}')"` : '';
  return `
    <button type="button" class="hop-kpi-card" ${target}>
      <p class="hop-kpi-label">${foEscapeText(label)}</p>
      <p class="hop-kpi-value${formatted.na ? ' is-na' : ''}">${foEscapeText(formatted.text)}</p>
    </button>`;
}

function hopFilled(value) {
  return value != null && String(value).trim() !== '';
}

function hopCell(value) {
  return hopFilled(value) ? foEscapeText(value) : '—';
}

function hopMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN');
}

function hopIsMobileView() {
  return !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
}

function hopDigitsOnly(value) {
  return String(value || '').replace(/\D/g, '');
}

function hopCallHref(mobile) {
  const raw = String(mobile || '').trim();
  if (!raw) return '';
  const clean = raw.replace(/[^\d+]/g, '');
  if (!clean) return '';
  return `tel:${clean}`;
}

function hopWhatsAppHref(mobile) {
  const digits = hopDigitsOnly(mobile);
  if (!digits) return '';
  const withCountry = digits.length === 10 ? `91${digits}` : digits;
  return `https://wa.me/${withCountry}`;
}

function hopContactSelectState(type) {
  if (!hopState.contactSelect[type]) {
    hopState.contactSelect[type] = { mode: false, ids: [] };
  }
  return hopState.contactSelect[type];
}

function hopContactApiBase(type) {
  return type === 'vendors' ? '/api/v1/hop/vendors' : '/api/v1/hop/customers';
}

function hopContactLabel(row) {
  return String(row?.company || row?.contact_person || `ID ${row?.id || ''}`).trim();
}

function hopContactIcon(name) {
  if (name === 'call') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.62 10.79a15.46 15.46 0 0 0 6.59 6.59l2.2-2.2a1 1 0 0 1 1-.24c1.1.37 2.28.57 3.49.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.3 21 3 13.7 3 4a1 1 0 0 1 1-1h3.49a1 1 0 0 1 1 1c0 1.21.2 2.39.57 3.49a1 1 0 0 1-.25 1l-2.19 2.3Z" fill="currentColor"/></svg>';
  }
  if (name === 'whatsapp') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.52 3.48A11.91 11.91 0 0 0 12.04 0C5.42 0 .04 5.38.04 12c0 2.11.55 4.17 1.6 5.99L0 24l6.18-1.62A11.94 11.94 0 0 0 12.03 24h.01c6.62 0 12-5.38 12-12 0-3.2-1.25-6.21-3.52-8.52ZM12.04 21.9h-.01a9.86 9.86 0 0 1-5.03-1.38l-.36-.21-3.67.96.98-3.58-.24-.37a9.86 9.86 0 0 1-1.52-5.27c0-5.44 4.42-9.86 9.86-9.86 2.63 0 5.1 1.02 6.96 2.89a9.78 9.78 0 0 1 2.89 6.96c0 5.44-4.43 9.86-9.86 9.86Zm5.41-7.39c-.3-.15-1.77-.87-2.05-.97-.27-.1-.47-.15-.66.15-.2.3-.76.97-.94 1.16-.17.2-.35.22-.65.08-.3-.15-1.27-.47-2.41-1.5a8.94 8.94 0 0 1-1.67-2.08c-.18-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.18.2-.3.3-.5.1-.2.05-.37-.02-.52-.08-.15-.67-1.62-.92-2.23-.24-.57-.48-.5-.66-.5h-.56c-.2 0-.52.08-.8.37-.27.3-1.04 1.01-1.04 2.47s1.07 2.86 1.22 3.06c.15.2 2.1 3.2 5.1 4.49.71.31 1.27.5 1.7.64.72.23 1.38.2 1.9.12.58-.09 1.77-.72 2.02-1.42.25-.69.25-1.28.18-1.41-.08-.13-.28-.2-.58-.35Z" fill="currentColor"/></svg>';
  }
  if (name === 'edit') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25Zm2.92 2.33H5v-.92l9.1-9.1.92.92-9.1 9.1ZM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.3a1 1 0 0 0-1.41 0l-1.7 1.7L19 8.74l1.71-1.7Z" fill="currentColor"/></svg>';
  }
  if (name === 'email') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2Zm0 4-8 5L4 8V6l8 5 8-5v2Z" fill="currentColor"/></svg>';
  }
  if (name === 'pin') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7Zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5Z" fill="currentColor"/></svg>';
  }
  if (name === 'gst') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm4 18H6V4h7v5h5v11ZM8 13h8v2H8v-2Zm0 4h5v2H8v-2Z" fill="currentColor"/></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7Zm3-3h6l1 2H8l1-2Z" fill="currentColor"/></svg>';
}

function hopRenderQuickContactActions(mobile, type, id, label) {
  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);
  const callBtn = callHref
    ? `<a class="nx-btn hop-contact-icon-btn" href="${callHref}" title="Call" onclick="event.stopPropagation()">${hopContactIcon('call')}</a>`
    : `<button type="button" class="nx-btn hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('call')}</button>`;
  const waBtn = waHref
    ? `<a class="nx-btn nx-btn-primary hop-contact-icon-btn" href="${waHref}" target="_blank" rel="noopener noreferrer" title="WhatsApp" onclick="event.stopPropagation()">${hopContactIcon('whatsapp')}</a>`
    : `<button type="button" class="nx-btn nx-btn-primary hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('whatsapp')}</button>`;
  const editBtn = `<button type="button" class="nx-btn hop-contact-icon-btn hop-contact-edit" onclick="event.stopPropagation();hopEditContact('${type}', ${id})" title="Edit">${hopContactIcon('edit')}</button>`;
  const delBtn = `<button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label || '')}')" title="Delete">${hopContactIcon('delete')}</button>`;
  return `
    <div class="hop-contact-actions">
      ${callBtn}
      ${waBtn}
      ${editBtn}
      ${delBtn}
    </div>`;
}

/** Prefer Parties workspace when that is the active CRM view. */
function hopContactReturnView(type) {
  if (hopState.view === 'parties') return 'parties';
  if (type === 'vendors' || type === 'vendor') return hopState.view === 'parties' ? 'parties' : 'vendors';
  if (type === 'customers' || type === 'customer') return hopState.view === 'parties' ? 'parties' : 'customers';
  return hopState.view || 'parties';
}

function hopRenderDesktopContactToolbar(type, count) {
  const state = hopContactSelectState(type);
  const selected = state.ids.length;
  const allChecked = selected === count && count > 0;
  return `
    <div class="hop-desk-toolbar" data-hop-contact-toolbar="${type}">
      <button type="button" class="nx-btn hop-desk-select-btn${allChecked ? ' is-active' : ''}" onclick="hopSelectAllContacts('${type}', ${allChecked ? 'false' : 'undefined'})">
        <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor"><path d="M3 4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4Z${allChecked ? 'M14.3 7.3a1 1 0 0 0-1.4-1.4L8.5 10.3 7.1 8.9a1 1 0 0 0-1.4 1.4l2.1 2.1a1 1 0 0 0 1.4 0l5.1-5.1Z' : ''}"/></svg>
        ${allChecked ? 'Deselect All' : 'Select All'}
      </button>
      <span class="hop-desk-count">${count} records</span>
      <span class="hop-desk-toolbar-spacer"></span>
      ${selected ? `<button type="button" class="nx-btn hop-desk-bulk-del-btn" onclick="hopBulkDeleteContacts('${type}')">${hopContactIcon('delete')} Delete ${selected} selected</button>` : ''}
    </div>`;
}

function hopRenderMobileContactToolbar(type, count) {
  const state = hopContactSelectState(type);
  const selected = state.ids.length;
  const mode = state.mode;
  return `
    <div class="hop-contact-toolbar" data-hop-contact-toolbar="${type}">
      <button type="button" class="nx-btn${mode ? ' nx-btn-primary' : ''}" onclick="hopToggleContactSelectMode('${type}')">${mode ? 'Done' : 'Select'}</button>
      <button type="button" class="nx-btn${mode ? '' : ' hidden'}" onclick="hopSelectAllContacts('${type}')">All (${count})</button>
      <button type="button" class="nx-btn hop-contact-icon-btn hop-toolbar-bulk-del${mode && selected ? '' : ' hidden'}" onclick="hopBulkDeleteContacts('${type}')" title="Delete selected (${selected})">${hopContactIcon('delete')}${selected ? ` ${selected}` : ''}</button>
    </div>`;
}

function hopRenderMobileContactCards(rows, type) {
  if (!rows.length) {
    return '<p class="nx-text-dim">No contacts yet — add your first record.</p>';
  }
  const isVendor = type === 'vendors';
  const state = hopContactSelectState(type);
  return `${hopRenderMobileContactToolbar(type, rows.length)}<div class="hop-contact-cards${state.mode ? ' is-select-mode' : ''}">${
    rows.map((r) => {
      const id = Number(r.id);
      const title = hopCell(r.company);
      const subtitle = hopCell(r.contact_person);
      const mobile = hopCell(r.mobile);
      const city = hopCell(r.city);
      const label = hopContactLabel(r);
      const checked = state.ids.includes(id) ? ' checked' : '';
      const detailRows = isVendor
        ? [
            ['Products', hopCell(r.products)],
            ['GST', hopCell(r.gst_no)],
            ['Email', hopCell(r.email)],
            ['Lead time', hopCell(r.lead_time_days)],
            ['Payment', hopCell(r.payment_terms)],
            ['On-time %', hopCell(r.on_time_pct)],
            ['Quality', hopCell(r.quality_rating)],
            ['Rating', hopCell(r.rating)],
          ]
        : [
            ['Type', hopCell(r.customer_type)],
            ['Hotel', hopCell(r.hotel_brand)],
            ['Architect', hopCell(r.architect)],
            ['Consultant', hopCell(r.consultant)],
            ['Potential', hopCell(r.annual_potential)],
            ['Rating', hopCell(r.potential_rating)],
            ['Status', hopCell(r.status)],
            ['Assigned', hopCell(r.assigned_to)],
          ];
      return `
        <article class="hop-contact-card${checked ? ' is-selected' : ''}" data-hop-contact-type="${type}" data-hop-contact-id="${id}" data-hop-contact-label="${foEscapeAttr(label)}">
          <div class="hop-contact-card-top">
            <label class="hop-contact-check-wrap">
              <input type="checkbox" class="hop-contact-check" value="${id}"${checked} onchange="hopToggleContactSelected('${type}', ${id}, this.checked)" />
            </label>
            <button type="button" class="hop-contact-main" onclick="hopToggleContactDetails(this)">
              <p class="hop-contact-company">${title}</p>
              <p class="hop-contact-sub">${subtitle}</p>
              <p class="hop-contact-sub">${mobile} · ${city}</p>
            </button>
          </div>
          ${hopRenderQuickContactActions(r.mobile, type, id, label)}
          <div class="hop-contact-details hidden">
            ${detailRows.map(([k, v]) => `<p><span>${k}</span><strong>${v}</strong></p>`).join('')}
          </div>
        </article>`;
    }).join('')
  }</div>`;
}

function hopToggleContactDetails(btn) {
  const card = btn?.closest('.hop-contact-card');
  if (!card) return;
  const type = card.getAttribute('data-hop-contact-type') || 'customers';
  const id = Number(card.getAttribute('data-hop-contact-id'));
  hopOpenContactDetail(type, id);
}

function hopOpenContactDetail(type, id) {
  const rows = type === 'vendors' ? (hopState.vendors || []) : (hopState.customers || []);
  const r = rows.find((x) => Number(x.id) === Number(id));
  if (!r) return;
  const isVendor = type === 'vendors';
  const label = hopContactLabel(r);
  const mobile = hopCell(r.mobile);
  const callHref = hopCallHref(r.mobile);
  const waHref = hopWhatsAppHref(r.mobile);

  const fields = isVendor
    ? [
        ['Company', r.company], ['Contact Person', r.contact_person], ['Mobile', r.mobile],
        ['Email', r.email], ['City', r.city], ['Products', r.products],
        ['GST No', r.gst_no], ['Lead Time (days)', r.lead_time_days],
        ['Payment Terms', r.payment_terms], ['On-time %', r.on_time_pct],
        ['Quality Rating', r.quality_rating], ['Rating', r.rating],
        ['Address', r.address], ['Remarks', r.remarks],
      ]
    : [
        ['Company', r.company], ['Contact Person', r.contact_person], ['Mobile', r.mobile],
        ['Email', r.email], ['City', r.city], ['Industry', r.industry],
        ['Customer Type', r.customer_type], ['Hotel Brand', r.hotel_brand],
        ['Architect', r.architect], ['Consultant', r.consultant],
        ['Annual Potential', r.annual_potential], ['Source', r.source],
        ['Rating', r.potential_rating], ['Status', r.status],
        ['Assigned To', r.assigned_to], ['GST No', r.gst_no],
        ['PAN', r.pan], ['Address', r.address], ['Source', r.source], ['Remarks', r.remarks],
      ];

  const callBtn = callHref
    ? `<a class="nx-btn hop-detail-action-btn" href="${callHref}">${hopContactIcon('call')} Call</a>`
    : '';
  const waBtn = waHref
    ? `<a class="nx-btn hop-detail-action-btn hop-detail-wa" href="${waHref}" target="_blank" rel="noopener noreferrer">${hopContactIcon('whatsapp')} WhatsApp</a>`
    : '';

  const overlay = document.createElement('div');
  overlay.id = 'hop-contact-detail-overlay';
  overlay.className = 'hop-detail-overlay';
  overlay.innerHTML = `
    <div class="hop-detail-backdrop" onclick="hopCloseContactDetail()"></div>
    <div class="hop-detail-panel">
      <div class="hop-detail-header">
        <button type="button" class="nx-btn hop-detail-back" onclick="hopCloseContactDetail()" title="Back">&larr;</button>
        <h3 class="hop-detail-title">${foEscapeText(label)}</h3>
        <button type="button" class="nx-btn hop-contact-icon-btn hop-detail-edit-btn" onclick="hopCloseContactDetail();hopEditContact('${type}', ${id})" title="Edit">${hopContactIcon('edit')}</button>
        <button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="hopCloseContactDetail();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label)}')" title="Delete">${hopContactIcon('delete')}</button>
      </div>
      <div class="hop-detail-quick-actions">
        ${callBtn}${waBtn}
        <button type="button" class="nx-btn hop-detail-action-btn" onclick="hopCloseContactDetail();hopEditContact('${type}', ${id})">${hopContactIcon('edit')} Edit</button>
        <button type="button" class="nx-btn hop-detail-action-btn hop-detail-del" onclick="hopCloseContactDetail();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label)}')">${hopContactIcon('delete')} Delete</button>
      </div>
      <div class="hop-detail-fields">
        ${fields.map(([k, v]) => {
          const val = hopCell(v);
          return `<div class="hop-detail-field"><span class="hop-detail-label">${foEscapeText(k)}</span><span class="hop-detail-value">${val}</span></div>`;
        }).join('')}
      </div>
    </div>`;
  document.getElementById('hop-contact-detail-overlay')?.remove();
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-open'));
}

function hopCloseContactDetail() {
  const el = document.getElementById('hop-contact-detail-overlay');
  if (!el) return;
  el.classList.remove('is-open');
  setTimeout(() => el.remove(), 250);
}

function hopCloseContactActionMenu() {
  document.getElementById('hop-contact-action-sheet')?.remove();
}

function hopOpenContactActionMenu(type, id, label) {
  hopCloseContactActionMenu();
  const sheet = document.createElement('div');
  sheet.id = 'hop-contact-action-sheet';
  sheet.className = 'hop-contact-sheet';
  sheet.innerHTML = `
    <div class="hop-contact-sheet-backdrop" onclick="hopCloseContactActionMenu()"></div>
    <div class="hop-contact-sheet-card">
      <p class="hop-contact-sheet-title">${foEscapeText(label || 'Contact actions')}</p>
      <button type="button" class="nx-btn hop-contact-sheet-btn" onclick="hopCloseContactActionMenu();hopEditContact('${type}', ${id});">
        ${hopContactIcon('edit')} Edit
      </button>
      <button type="button" class="nx-btn hop-contact-sheet-btn hop-contact-delete" onclick="hopCloseContactActionMenu();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label || '')}');">
        ${hopContactIcon('delete')} Delete
      </button>
      <button type="button" class="nx-btn hop-contact-sheet-btn" onclick="hopCloseContactActionMenu()">Cancel</button>
    </div>`;
  document.body.appendChild(sheet);
}

function hopBindMobileContactCards(type) {
  const root = document.querySelector(`[data-hop-contact-toolbar="${type}"]`)?.parentElement;
  if (!root) return;
  const mains = root.querySelectorAll('.hop-contact-main');
  mains.forEach((mainBtn) => {
    if (mainBtn.dataset.hopLongPressBound === '1') return;
    mainBtn.dataset.hopLongPressBound = '1';
    let timer = null;
    let moved = false;
    let startX = 0;
    let startY = 0;
    const clearTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };
    mainBtn.addEventListener('touchstart', (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;
      moved = false;
      startX = touch.clientX;
      startY = touch.clientY;
      clearTimer();
      timer = window.setTimeout(() => {
        timer = null;
        const card = mainBtn.closest('.hop-contact-card');
        if (!card) return;
        const contactType = card.getAttribute('data-hop-contact-type') || type;
        const contactId = Number(card.getAttribute('data-hop-contact-id'));
        const label = card.getAttribute('data-hop-contact-label') || '';
        if (navigator.vibrate) navigator.vibrate(20);
        hopOpenContactActionMenu(contactType, contactId, label);
      }, 650);
    }, { passive: true });
    mainBtn.addEventListener('touchmove', (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;
      if (Math.abs(touch.clientX - startX) > 12 || Math.abs(touch.clientY - startY) > 12) {
        moved = true;
        clearTimer();
      }
    }, { passive: true });
    mainBtn.addEventListener('touchend', () => {
      if (moved) clearTimer();
      clearTimer();
    }, { passive: true });
    mainBtn.addEventListener('touchcancel', clearTimer, { passive: true });
  });
}

function hopToggleContactSelectMode(type) {
  const state = hopContactSelectState(type);
  state.mode = !state.mode;
  if (!state.mode) state.ids = [];
  openHopView(type);
}

function hopToggleContactSelected(type, id, checked) {
  const state = hopContactSelectState(type);
  const numId = Number(id);
  if (checked) {
    if (!state.ids.includes(numId)) state.ids.push(numId);
  } else {
    state.ids = state.ids.filter((x) => x !== numId);
  }
  const card = document.querySelector(`.hop-contact-card[data-hop-contact-type="${type}"][data-hop-contact-id="${numId}"]`);
  card?.classList.toggle('is-selected', checked);
  // Update mobile toolbar
  const bulkBtn = document.querySelector(`[data-hop-contact-toolbar="${type}"] .hop-contact-delete`);
  if (bulkBtn) {
    bulkBtn.textContent = `Delete (${state.ids.length})`;
    bulkBtn.classList.toggle('hidden', !state.mode || !state.ids.length);
  }
  // Update desktop toolbar
  _hopUpdateDesktopBulkBtn(type);
}

function _hopUpdateDesktopBulkBtn(type) {
  const state = hopContactSelectState(type);
  const toolbar = document.querySelector(`.hop-desk-toolbar[data-hop-contact-toolbar="${type}"]`);
  if (!toolbar) return;
  const existing = toolbar.querySelector('.hop-desk-bulk-del-btn');
  if (state.ids.length) {
    if (existing) {
      existing.innerHTML = `${hopContactIcon('delete')} Delete ${state.ids.length} selected`;
    } else {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nx-btn hop-desk-bulk-del-btn';
      btn.onclick = () => hopBulkDeleteContacts(type);
      btn.innerHTML = `${hopContactIcon('delete')} Delete ${state.ids.length} selected`;
      toolbar.appendChild(btn);
    }
  } else if (existing) {
    existing.remove();
  }
}

function hopSelectAllContacts(type, checked) {
  const state = hopContactSelectState(type);
  const rows = type === 'vendors' ? (hopState.vendors || []) : (hopState.customers || []);
  if (checked === false) {
    state.ids = [];
  } else {
    state.ids = rows.map((r) => Number(r.id)).filter((id) => Number.isFinite(id));
  }
  openHopView(type);
}

async function hopEditContact(type, id) {
  const kind = type === 'vendors' || type === 'vendor' ? 'vendor' : 'customer';
  const rows = kind === 'vendor' ? (hopState.vendors || []) : (hopState.customers || []);
  let row = rows.find((r) => Number(r.id) === Number(id));
  if (!row && hopState._parties) {
    const p = hopState._parties.find((x) => Number(x.id) === Number(id) && x._type === (kind === 'vendor' ? 'vendor' : 'customer'));
    if (p) row = p;
  }
  if (!row) {
    try {
      row = await hopApi(`${hopContactApiBase(kind === 'vendor' ? 'vendors' : 'customers')}/${id}`);
    } catch (e) {
      alert(e.message || 'Could not load contact');
      return;
    }
  }
  hopOpenPartyEditModal(kind, row);
}

async function hopDeleteContact(type, id, label) {
  const name = label || `contact #${id}`;
  if (!(await nexoraConfirm(`Delete "${name}"?`, {
    title: 'Delete contact',
    danger: true,
    okText: 'Delete',
  }))) return;
  try {
    await hopApi(`${hopContactApiBase(type)}/${id}`, { method: 'DELETE' });
    const state = hopContactSelectState(type);
    state.ids = state.ids.filter((x) => x !== Number(id));
    if (type === 'vendors' || type === 'vendor') hopState.vendors = [];
    else hopState.customers = [];
    hopCloseContactDetail();
    hopClosePartyEditModal();
    openHopView(hopContactReturnView(type));
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopBulkDeleteContacts(type) {
  const state = hopContactSelectState(type);
  const ids = [...state.ids];
  if (!ids.length) return;
  if (!(await nexoraConfirm(`Delete ${ids.length} selected contact(s)?`, {
    title: 'Bulk delete',
    danger: true,
    okText: 'Delete all',
  }))) return;
  try {
    const result = await hopApi(`${hopContactApiBase(type)}/bulk-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    const deleted = (result?.deleted || []).length;
    const errors = result?.errors || [];
    state.ids = [];
    state.mode = false;
    if (type === 'vendors') hopState.vendors = [];
    else hopState.customers = [];
    openHopView(hopContactReturnView(type));
    if (errors.length) {
      alert(`Deleted ${deleted}. ${errors.length} could not be deleted (linked records).`);
    }
  } catch (e) {
    alert(e.message || 'Bulk delete failed');
  }
}

async function hopApi(path, options) {
  let response;
  try {
    response = await fetchWithAuth(path, options);
  } catch (e) {
    if (isSessionTimeoutError(e)) return null;
    throw e;
  }
  const data = await parseApiJson(response);
  if (!response.ok || !data.success) {
    throw new Error(getApiErrorMessage(data, 'Request failed'));
  }
  return data.data;
}

function hopMount() {
  return document.getElementById('hop-module-mount');
}

function hopHideAllViews() {
  document.querySelectorAll('.hop-view').forEach((el) => el.classList.add('hidden'));
  const mount = hopMount();
  if (mount) mount.innerHTML = '';
}

function hopScrollMainToTop() {
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  if (main) main.scrollTop = 0;
  const shell = document.getElementById('hop-executive-workspace');
  if (shell) shell.scrollTop = 0;
  const page = document.scrollingElement || document.documentElement;
  if (page) page.scrollTop = 0;
  window.scrollTo(0, 0);
  // After async content paints, keep top (layout can reflow and leave scroll low)
  requestAnimationFrame(() => {
    if (main) main.scrollTop = 0;
    window.scrollTo(0, 0);
  });
}

/** Scroll an element into view inside .hop-main (not the locked body). */
function hopScrollIntoMain(el, offset = 12) {
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  if (!main || !el) return;
  const mainRect = main.getBoundingClientRect();
  const elRect = el.getBoundingClientRect();
  main.scrollTop += (elRect.top - mainRect.top - offset);
}

function hopSetMainFullpage(enabled) {
  const on = !!enabled;
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  const shell = document.querySelector('#hop-executive-workspace .hop-shell');
  const ws = document.getElementById('hop-executive-workspace');
  main?.classList.toggle('hop-main--fullpage', on);
  shell?.classList.toggle('hop-shell--module', on);
  ws?.classList.toggle('hop-ws--fullscreen', on);
  // Overwrite NEXORA top bar (Ask NEXORA / Workspace / profile / bell) for max work room.
  document.body.classList.toggle('hop-module-fullscreen', on);
  document.documentElement.classList.toggle('hop-module-fullscreen', on);
}

/** Mobile / fullscreen back — closes overlays first, then pops view history. */
function hopGoBack() {
  if (document.getElementById('hop-party-edit-modal')) {
    hopClosePartyEditModal();
    return;
  }
  if (document.getElementById('nx-confirm-modal') && !document.getElementById('nx-confirm-modal').classList.contains('hidden')) {
    document.getElementById('nx-confirm-cancel')?.click();
    return;
  }
  if (document.getElementById('hop-party-txn-overlay')) {
    hopClosePartyTxnDetail();
    return;
  }
  if (document.getElementById('hop-contact-detail-overlay')) {
    hopCloseContactDetail();
    return;
  }
  if (document.getElementById('hop-contact-action-sheet')) {
    hopCloseContactActionMenu();
    return;
  }
  const stack = hopState.viewHistory || [];
  const prev = stack.pop();
  hopState.viewHistory = stack;
  openHopView(prev || 'dashboard', { skipHistory: true });
}

function hopBackButtonHtml(label) {
  const text = label || 'Back';
  return `<button type="button" class="nx-btn hop-mobile-back" onclick="hopGoBack()" aria-label="${foEscapeAttr(text)}" title="${foEscapeAttr(text)}">
    <svg viewBox="0 0 20 20" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M12.7 4.3a1 1 0 0 1 0 1.4L8.4 10l4.3 4.3a1 1 0 1 1-1.4 1.4l-5-5a1 1 0 0 1 0-1.4l5-5a1 1 0 0 1 1.4 0Z"/></svg>
    <span>${foEscapeText(text)}</span>
  </button>`;
}

function openHopView(viewName, opts) {
  // Legacy alias
  if (viewName === 'quotations') viewName = 'sale_estimates';
  const next = viewName || 'dashboard';
  const prev = hopState.view;
  if (!opts?.skipHistory && prev && prev !== next) {
    if (!Array.isArray(hopState.viewHistory)) hopState.viewHistory = [];
    hopState.viewHistory.push(prev);
    if (hopState.viewHistory.length > 40) hopState.viewHistory.shift();
  }
  hopState.view = next;
  hopHideAllViews();
  hopScrollMainToTop();
  document.querySelectorAll('.hop-nav-btn[data-hop-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.hopView === hopState.view);
  });
  // Keep Sale / Purchase / Settings folds open when a child is active
  const saleFold = document.querySelector('[data-hop-fold="sale"]');
  if (
    saleFold
    && (
      String(hopState.view || '').startsWith('sale_')
      || hopState.view === 'invoices'
      || hopState.view === 'payments'
      || hopState.view === 'commission'
    )
  ) {
    saleFold.classList.remove('is-collapsed');
    saleFold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', 'true');
  }
  const purchaseFold = document.querySelector('[data-hop-fold="purchase"]');
  if (purchaseFold && String(hopState.view || '').startsWith('purchase_')) {
    purchaseFold.classList.remove('is-collapsed');
    purchaseFold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', 'true');
  }
  const accountingFold = document.querySelector('[data-hop-fold="accounting"]');
  if (accountingFold && hopState.view === 'journal_entries') {
    accountingFold.classList.remove('is-collapsed');
    accountingFold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', 'true');
  }
  const settingsFold = document.querySelector('[data-hop-fold="settings"]');
  if (settingsFold && (hopState.view === 'vyapar_import' || hopState.view === 'wipe_data')) {
    settingsFold.classList.remove('is-collapsed');
    settingsFold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', 'true');
  }

  // Dashboard keeps padded scroll layout; every other menu opens as full-page workspace
  // (overwrites Ask NEXORA / Workspace / profile / bell top bar).
  hopSetMainFullpage(hopState.view !== 'dashboard');

  if (hopState.view === 'dashboard') {
    hopState.viewHistory = [];
    document.getElementById('hop-view-dashboard')?.classList.remove('hidden');
    document.getElementById('hop-view-project-hub')?.classList.remove('hop-view--fullpage');
    Promise.resolve(loadHopExecutiveSnapshot()).finally(() => hopScrollMainToTop());
  } else if (hopState.view === 'project-hub' || hopState.view === 'project_hub') {
    const hub = document.getElementById('hop-view-project-hub');
    hub?.classList.remove('hidden');
    hub?.classList.add('hop-view--fullpage', 'hop-view--module');
    hopSetMainFullpage(true);
    const pid = opts?.projectId || hopState.hub?.project?.id;
    if (pid) Promise.resolve(loadHopProjectHub(pid)).finally(() => hopScrollMainToTop());
  } else {
    const mount = hopMount();
    if (!mount) {
      console.error('HoP mount missing');
    } else {
      mount.innerHTML = '<div class="hop-view hop-view--fullpage hop-view--module"><div class="mod-page"><div class="mod-body"><p class="nx-text-dim">Loading…</p></div></div></div>';
      hopSetMainFullpage(true);
      const loaders = {
        parties: renderHopPartiesModule,
        customers: renderHopCustomersModule,
        vyapar_import: renderHopVyaparImportModule,
        wipe_data: renderHopWipeDataModule,
        visiting_card: () => {
          // Visiting card lives under Parties → Add Party → Scan
          openHopView('parties');
          requestAnimationFrame(() => hopOpenAddPartyChooser());
        },
        projects: renderHopProjectsModule,
        leads: renderHopLeadsModule,
        meetings: renderHopMeetingsModule,
        quotations: renderHopSaleEstimatesModule,
        sale_estimates: renderHopSaleEstimatesModule,
        sale_proforma: (m) => renderHopSaleLedgerModule(m, 'sale_proforma'),
        sale_orders: (m) => renderHopSaleLedgerModule(m, 'sale_orders'),
        sale_challan: (m) => renderHopSaleLedgerModule(m, 'sale_challan'),
        sale_returns: (m) => renderHopSaleLedgerModule(m, 'sale_returns'),
        sale_fa: (m) => renderHopSaleLedgerModule(m, 'sale_fa'),
        purchase_bills: (m) => renderHopSaleLedgerModule(m, 'purchase_bills'),
        purchase_payment_out: (m) => renderHopSaleLedgerModule(m, 'purchase_payment_out'),
        purchase_expenses: (m) => renderHopSaleLedgerModule(m, 'purchase_expenses'),
        purchase_orders: (m) => renderHopSaleLedgerModule(m, 'purchase_orders'),
        purchase_returns: (m) => renderHopSaleLedgerModule(m, 'purchase_returns'),
        purchase_fa: (m) => renderHopSaleLedgerModule(m, 'purchase_fa'),
        journal_entries: (m) => renderHopSaleLedgerModule(m, 'journal_entries'),
        vendors: renderHopVendorsModule,
        vendor_cmp: renderHopVendorCmpModule,
        samples: renderHopSamplesModule,
        products: renderHopProductsModule,
        fabric_preview: renderHopFabricPreviewModule,
        orders: renderHopOrdersModule,
        dispatches: renderHopDispatchesModule,
        invoices: renderHopInvoicesModule,
        commission: renderHopCommissionModule,
        payments: renderHopPaymentInModule,
        complaints: renderHopComplaintsModule,
        pipeline: renderHopPipelineModule,
        funnel: renderHopFunnelModule,
        receivables: renderHopReceivablesModule,
        customer_dash: renderHopCustomerDashModule,
        daily: renderHopDailyModule,
        profit: renderHopProfitModule,
        targets: renderHopTargetsModule,
      };
      const fn = loaders[hopState.view];
      if (fn) {
        Promise.resolve(fn(mount)).finally(() => {
          hopSetMainFullpage(true);
          hopScrollMainToTop();
        });
      } else {
        mount.innerHTML = hopModuleShell('HoP', 'Unknown', '', '', `<p class="nx-oc-error">Unknown view</p>`);
        hopSetMainFullpage(true);
      }
    }
  }

  // Close drawer after navigation so Android WebView does not cancel the tap.
  window.setTimeout(() => {
    if (typeof closeMobileNav === 'function') closeMobileNav();
  }, 80);
}

function hopToggleNavFold(id) {
  const fold = document.querySelector(`[data-hop-fold="${id}"]`);
  if (!fold) return;
  fold.classList.toggle('is-collapsed');
  const open = !fold.classList.contains('is-collapsed');
  fold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', open ? 'true' : 'false');
}

/** Reliable mobile taps — ignore scroll gestures; only open on a real tap. */
function bindHopNavClicks() {
  const nav = document.querySelector('#hop-executive-workspace .hop-nav');
  if (!nav || nav.dataset.hopNavBound === '1') return;
  nav.dataset.hopNavBound = '1';

  const TAP_MOVE_PX = 14;
  let startX = 0;
  let startY = 0;
  let moved = false;
  let startTarget = null;
  let suppressClickUntil = 0;
  let lastNavAt = 0;

  const isNavControl = (el) => {
    if (!el || !el.closest) return null;
    return el.closest('.hop-nav-btn[data-hop-view], button.hop-nav-logout');
  };

  const runNav = (btn) => {
    if (!btn || btn.disabled || btn.classList.contains('is-soon')) return;
    const now = Date.now();
    if (now - lastNavAt < 350) return;
    lastNavAt = now;
    if (btn.classList.contains('hop-nav-logout')) {
      if (typeof logout === 'function') logout();
      return;
    }
    const view = btn.getAttribute('data-hop-view');
    if (view) openHopView(view);
  };

  nav.addEventListener(
    'touchstart',
    (event) => {
      const touch = event.changedTouches && event.changedTouches[0];
      if (!touch) return;
      startX = touch.clientX;
      startY = touch.clientY;
      moved = false;
      startTarget = isNavControl(event.target);
    },
    { capture: true, passive: true },
  );

  nav.addEventListener(
    'touchmove',
    (event) => {
      const touch = event.changedTouches && event.changedTouches[0];
      if (!touch) return;
      if (
        Math.abs(touch.clientX - startX) > TAP_MOVE_PX
        || Math.abs(touch.clientY - startY) > TAP_MOVE_PX
      ) {
        moved = true;
      }
    },
    { capture: true, passive: true },
  );

  nav.addEventListener(
    'touchend',
    (event) => {
      const btn = isNavControl(event.target);
      // Finger moved = scroll — do not open the item under the finger.
      if (moved || !btn || btn !== startTarget) {
        startTarget = null;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      suppressClickUntil = Date.now() + 500;
      runNav(btn);
      startTarget = null;
    },
    { capture: true, passive: false },
  );

  nav.addEventListener(
    'click',
    (event) => {
      if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      // Mouse / trackpad only path (phones use touchend above).
      if (window.matchMedia('(pointer: coarse)').matches) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const btn = isNavControl(event.target);
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      runNav(btn);
    },
    true,
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bindHopNavClicks);
} else {
  bindHopNavClicks();
}

function hopDebouncedReload(kind) {
  clearTimeout(hopState.reloadTimers[kind]);
  hopState.reloadTimers[kind] = setTimeout(() => openHopView(kind), 280);
}

/** Locked compact page chrome (Vyapar density) for every full-page module. */
function hopModuleShell(eyebrow, title, subtitle, actionsHtml, bodyHtml) {
  const isFullpage = hopState.view && hopState.view !== 'dashboard';
  if (isFullpage) {
    return `
    <div class="hop-view hop-view--fullpage hop-view--module hop-view--tx">
      <div class="mod-page inv-shell">
        <div class="inv-topbar">
          <div class="inv-topbar-left">
            ${hopBackButtonHtml('Back')}
            <h2 class="inv-title">${foEscapeText(title)}</h2>
          </div>
          <div class="inv-topbar-actions">${actionsHtml || ''}</div>
        </div>
        <div class="inv-body hop-tx-body">${bodyHtml || ''}</div>
      </div>
    </div>`;
  }
  return `
    <div class="hop-view">
      <header class="hop-view-header hop-view-header-row">
        <div>
          <p class="nx-text-faint hop-eyebrow">${foEscapeText(eyebrow)}</p>
          <h2 class="nx-display">${foEscapeText(title)}</h2>
          ${subtitle ? `<p class="nx-text-dim">${subtitle}</p>` : ''}
        </div>
        <div class="hop-header-actions">${actionsHtml || ''}</div>
      </header>
      <div class="hop-view-body">${bodyHtml}</div>
    </div>`;
}

/** Compact KPI strip: Paid + Unpaid + … = Total style. */
function hopTxCards(cards) {
  if (!cards || !cards.length) return '';
  return `<div class="inv-cards">${cards.map((c) => {
    const op = c.op ? `<span class="inv-op">${foEscapeText(c.op)}</span>` : '';
    const tone = c.tone ? ` inv-card--${foEscapeAttr(c.tone)}` : '';
    const idAttr = c.id ? ` id="${foEscapeAttr(c.id)}"` : '';
    const val = c.valueHtml != null ? c.valueHtml : foEscapeText(String(c.value ?? '—'));
    return `${op}<div class="inv-card${tone}"><span>${foEscapeText(c.label || '')}</span><strong${idAttr}>${val}</strong></div>`;
  }).join('')}</div>`;
}

function hopTxToolbar(innerHtml) {
  if (!innerHtml) return '';
  return `<div class="inv-toolbar">${innerHtml}</div>`;
}

function hopFilterListTable(input, tbodyId) {
  const q = String(input?.value || '').trim().toLowerCase();
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.querySelectorAll('tr').forEach((tr) => {
    if (tr.querySelector('.inv-empty')) return;
    tr.style.display = !q || (tr.textContent || '').toLowerCase().includes(q) ? '' : 'none';
  });
}

/**
 * Locked transactions table — fills remaining viewport height.
 * opts: { label, count, tbodyId, search, searchValue, searchPlaceholder, searchId, className }
 */
function hopTable(headers, rowsHtml, emptyCols, opts) {
  let o = {};
  let cols = emptyCols;
  if (emptyCols && typeof emptyCols === 'object' && !Array.isArray(emptyCols)) {
    o = emptyCols;
    cols = o.emptyCols;
  } else if (opts && typeof opts === 'object') {
    o = opts;
  }
  cols = cols || (headers || []).length || 1;
  const tid = o.tbodyId || `hop-tb-${Math.random().toString(36).slice(2, 9)}`;
  const label = o.label || 'Transactions';
  const countHtml = o.count != null ? `<span class="inv-count">${foEscapeText(String(o.count))} rows</span>` : '';
  const searchHtml = o.search === false
    ? ''
    : `<input${o.searchId ? ` id="${foEscapeAttr(o.searchId)}"` : ''} class="inv-search" type="search" placeholder="${foEscapeAttr(o.searchPlaceholder || 'Search…')}"
         value="${foEscapeAttr(o.searchValue || '')}" oninput="hopFilterListTable(this, '${tid}')" />`;
  const body = rowsHtml || `<tr><td colspan="${cols}" class="inv-empty">No rows yet — add your first record.</td></tr>`;
  return `
    <div class="inv-table-card hop-tx-table${o.className ? ` ${foEscapeAttr(o.className)}` : ''}">
      <div class="inv-table-head">
        <strong>${foEscapeText(label)}</strong>
        ${countHtml}
        ${searchHtml}
      </div>
      <div class="inv-table-wrap">
        <table class="inv-table">
          <thead><tr>${(headers || []).map((h) => {
            const isNum = /amount|value|balance|paid|qty|%|hrs|outstanding/i.test(String(h));
            return `<th${isNum ? ' class="inv-num"' : ''}>${foEscapeText(h)}</th>`;
          }).join('')}</tr></thead>
          <tbody id="${tid}">${body}</tbody>
        </table>
      </div>
    </div>`;
}

async function loadHopExecutiveSnapshot() {
  const loading = document.getElementById('hop-exec-loading');
  const content = document.getElementById('hop-exec-content');
  const errorEl = document.getElementById('hop-exec-error');
  const grid = document.getElementById('hop-kpi-grid');
  const meta = document.getElementById('hop-exec-meta');
  const notes = document.getElementById('hop-exec-notes');
  if (!grid) return;
  loading?.classList.remove('hidden');
  content?.classList.add('hidden');
  errorEl?.classList.add('hidden');
  try {
    const snap = await hopApi('/api/v1/hop/executive/snapshot');
    const today = snap.today || {};
    const monthly = snap.monthly || {};
    const target = Number(monthly.target || 0);
    const sales = Number(monthly.sales || 0);
    const salesVsTarget = target > 0
      ? `${sales.toLocaleString('en-IN')} / ${target.toLocaleString('en-IN')}`
      : (sales > 0 ? `${sales.toLocaleString('en-IN')} / no target set` : '0 / no target set');
    grid.innerHTML = [
      renderHopKpiCard('New Leads', today.new_leads, 'leads'),
      renderHopKpiCard('Meetings Today', today.meetings_today, 'meetings'),
      renderHopKpiCard('Pending Follow-ups', today.pending_followups, 'leads'),
      renderHopKpiCard('Quotations Pending', today.quotations_pending, 'quotations'),
      renderHopKpiCard('Quotations Sent', today.quotations_sent, 'quotations'),
      renderHopKpiCard('Orders Won', today.orders_won, 'orders'),
      renderHopKpiCard('Orders Lost', today.orders_lost, 'pipeline'),
      renderHopKpiCard('Production', today.production_orders, 'orders'),
      renderHopKpiCard('Dispatch Due', today.dispatches_due, 'dispatches'),
      renderHopKpiCard('Outstanding', today.outstanding_receivables, 'receivables'),
      renderHopKpiCard('Collections Today', today.payments_received_today, 'payments'),
      renderHopKpiCard('Sales vs Target', salesVsTarget, 'targets'),
      renderHopKpiCard('Gross Profit %', snap.gross_profit_pct, 'profit'),
      renderHopKpiCard('Cash Available', snap.cash_available, null),
    ].join('');
    if (meta) meta.textContent = `Workspace ${snap.workspace_id || 'house_of_prizm'} · as of ${snap.as_of || '—'}`;
    if (notes) {
      const n = snap.notes || {};
      notes.textContent = [n.gross_profit_pct, n.cash_available].filter(Boolean).join(' · ');
    }
    loading?.classList.add('hidden');
    content?.classList.remove('hidden');
  } catch (error) {
    loading?.classList.add('hidden');
    if (errorEl) {
      errorEl.textContent = error.message || 'Unable to load snapshot';
      errorEl.classList.remove('hidden');
    }
  }
}

async function hopEnsureLookups() {
  try {
    if (!hopState.customers.length) hopState.customers = await hopApi('/api/v1/hop/customers') || [];
    if (!hopState.projects.length) hopState.projects = await hopApi('/api/v1/hop/projects') || [];
    if (!hopState.vendors.length) hopState.vendors = await hopApi('/api/v1/hop/vendors') || [];
    if (!hopState.invoices.length) hopState.invoices = await hopApi('/api/v1/hop/invoices') || [];
    if (!hopState.orders.length) hopState.orders = await hopApi('/api/v1/hop/orders') || [];
  } catch (_) { /* ignore */ }
}

function hopCustomerOptions(selectedId) {
  return ['<option value="">— Select customer —</option>']
    .concat(hopState.customers.map((c) => `<option value="${c.id}"${String(c.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(c.company)}</option>`))
    .join('');
}

function hopProjectOptions(selectedId) {
  return ['<option value="">— Select project —</option>']
    .concat(hopState.projects.map((p) => `<option value="${p.id}"${String(p.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(p.project_name)}</option>`))
    .join('');
}

function hopVendorOptions(selectedId) {
  return ['<option value="">— Select vendor —</option>']
    .concat(hopState.vendors.map((v) => `<option value="${v.id}"${String(v.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(v.company)}</option>`))
    .join('');
}

function hopInvoiceOptions(selectedId) {
  return ['<option value="">— Select invoice —</option>']
    .concat(hopState.invoices.map((i) => `<option value="${i.id}"${String(i.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(i.invoice_no)} · bal ${hopMoney(i.balance)}</option>`))
    .join('');
}

function hopOrderOptions(selectedId) {
  return ['<option value="">— Select order —</option>']
    .concat(hopState.orders.map((o) => `<option value="${o.id}"${String(o.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(o.po_number || `#${o.id}`)}</option>`))
    .join('');
}

function hopStageOptions(list, selected) {
  return list.map((s) => `<option value="${s}"${s === selected ? ' selected' : ''}>${foEscapeText(s)}</option>`).join('');
}

/* ---------- Vyapar-style Edit Party modal (Nexora theme) ---------- */
const HOP_INDIAN_STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh','Delhi','Goa','Gujarat','Haryana',
  'Himachal Pradesh','Jharkhand','Karnataka','Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya',
  'Mizoram','Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana','Tripura',
  'Uttar Pradesh','Uttarakhand','West Bengal','Jammu and Kashmir','Ladakh','Puducherry','Chandigarh',
];

/** GSTIN first-2-digit → state (same as backend decode). */
const HOP_GSTIN_STATE_CODES = {
  '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
  '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
  '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
  '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
  '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
  '27': 'Maharashtra', '29': 'Karnataka', '30': 'Goa', '32': 'Kerala', '33': 'Tamil Nadu',
  '34': 'Puducherry', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
};

let _hopGstLookupTimer = null;
let _hopGstLookupLast = '';

function hopClosePartyEditModal() {
  if (_hopGstLookupTimer) clearTimeout(_hopGstLookupTimer);
  _hopGstLookupTimer = null;
  _hopGstLookupLast = '';
  document.getElementById('hop-party-edit-modal')?.remove();
  hopState.contactEdit = null;
}

function hopPartyModalSetTab(tab) {
  const root = document.getElementById('hop-party-edit-modal');
  if (!root) return;
  root.querySelectorAll('[data-party-tab]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.getAttribute('data-party-tab') === tab);
  });
  root.querySelectorAll('[data-party-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.getAttribute('data-party-panel') !== tab);
  });
}

function hopPartyGstHint(msg, kind) {
  const el = document.getElementById('pm-gst-hint');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('is-error', kind === 'error');
  el.classList.toggle('is-ok', kind === 'ok');
}

function hopPartyApplyGstLocal(gstin) {
  const g = String(gstin || '').trim().toUpperCase();
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(g)) return false;
  const state = HOP_GSTIN_STATE_CODES[g.slice(0, 2)];
  const pan = g.slice(2, 12);
  const stateEl = document.getElementById('pm-state');
  const panEl = document.getElementById('pm-pan');
  const gstTypeEl = document.getElementById('pm-gst-type');
  if (stateEl && state) {
    const hasOpt = Array.from(stateEl.options).some((o) => o.value === state || o.textContent === state);
    if (hasOpt) stateEl.value = state;
  }
  if (panEl && pan && !String(panEl.value || '').trim()) panEl.value = pan;
  if (gstTypeEl && String(gstTypeEl.value || '').includes('Unregistered')) {
    gstTypeEl.value = 'Registered Business - Regular';
  }
  return true;
}

function hopPartyFillFromGstLookup(data, force) {
  if (!data) return;
  const setIf = (id, val) => {
    if (val == null || val === '') return;
    const el = document.getElementById(id);
    if (!el) return;
    if (force || !String(el.value || '').trim()) el.value = val;
  };
  if (data.state) {
    const stateEl = document.getElementById('pm-state');
    if (stateEl) {
      const hasOpt = Array.from(stateEl.options).some((o) => o.value === data.state || o.textContent === data.state);
      if (hasOpt) stateEl.value = data.state;
    }
  }
  setIf('pm-pan', data.pan);
  setIf('pm-billing', data.billing_name || data.company);
  setIf('pm-name', data.company || data.billing_name);
  setIf('pm-address', data.address);
  setIf('pm-shipping', data.shipping_address || data.address);
  setIf('pm-city', data.city);
  if (data.gst_type) {
    const gstTypeEl = document.getElementById('pm-gst-type');
    if (gstTypeEl && (force || String(gstTypeEl.value || '').includes('Unregistered'))) {
      gstTypeEl.value = data.gst_type;
    }
  }
  const nameEl = document.getElementById('pm-name');
  const hint = document.getElementById('pm-bill-hint');
  if (hint && nameEl?.value) hint.textContent = `“${nameEl.value}” will be printed on your invoice.`;
}

async function hopPartyFetchGstDetails(force) {
  const input = document.getElementById('pm-gstin');
  const btn = document.getElementById('pm-gst-fetch');
  const v = String(input?.value || '').trim().toUpperCase();
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(v)) {
    hopPartyGstHint('Enter a valid 15-character GSTIN first.', 'error');
    return;
  }
  hopPartyApplyGstLocal(v);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Fetching…';
  }
  hopPartyGstHint('Looking up GSTIN details…');
  try {
    const data = await hopApi(`/api/v1/hop/gstin-lookup?gstin=${encodeURIComponent(v)}`);
    hopPartyFillFromGstLookup(data, !!force);
    _hopGstLookupLast = v;
    const hasAddr = !!(data?.address);
    if (hasAddr) {
      hopPartyGstHint(data.message || 'Billing address filled from GSTIN.', 'ok');
      hopPartyModalSetTab('gst');
    } else {
      hopPartyGstHint(
        data?.message || 'State & PAN filled from GSTIN. Full address needs GST registry API key on server.',
        'ok'
      );
    }
  } catch (e) {
    hopPartyApplyGstLocal(v);
    hopPartyGstHint(e?.message || 'GST lookup failed. State/PAN still applied.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Fetch details';
    }
  }
}

function hopPartyGstCheck() {
  const input = document.getElementById('pm-gstin');
  const mark = document.getElementById('pm-gst-ok');
  if (!input || !mark) return;
  const v = String(input.value || '').trim().toUpperCase();
  input.value = v;
  const ok = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(v);
  mark.classList.toggle('is-valid', ok);
  mark.classList.toggle('hidden', !v);
  const fetchBtn = document.getElementById('pm-gst-fetch');
  if (fetchBtn) fetchBtn.disabled = !ok;
  if (!ok) {
    if (v.length >= 15) hopPartyGstHint('GSTIN format looks invalid.', 'error');
    else if (!v) hopPartyGstHint('');
    return;
  }
  hopPartyApplyGstLocal(v);
  hopPartyGstHint('Valid GSTIN — State/PAN applied. Click Fetch details for address.', 'ok');
  if (_hopGstLookupTimer) clearTimeout(_hopGstLookupTimer);
  // Auto-fetch once when a new valid GSTIN is fully typed (empty address only).
  _hopGstLookupTimer = setTimeout(() => {
    if (_hopGstLookupLast === v) return;
    const addr = String(document.getElementById('pm-address')?.value || '').trim();
    if (!addr) hopPartyFetchGstDetails(false);
  }, 650);
}

function hopPartyParseAdditionalFields(raw) {
  const fallback = [
    { enabled: true, name: 'Mobile Number', show_in_print: true, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
  ];
  if (!raw) return fallback;
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!Array.isArray(parsed) || !parsed.length) return fallback;
    while (parsed.length < 4) parsed.push({ enabled: false, name: '', show_in_print: false, value: '' });
    return parsed.slice(0, 4).map((f) => ({
      enabled: !!f.enabled,
      name: f.name || '',
      show_in_print: !!f.show_in_print,
      value: f.value || '',
    }));
  } catch (_) {
    return fallback;
  }
}

function hopPartyCollectAdditionalFields() {
  const out = [];
  for (let i = 0; i < 4; i += 1) {
    out.push({
      enabled: !!document.getElementById(`pm-af-en-${i}`)?.checked,
      name: document.getElementById(`pm-af-name-${i}`)?.value || '',
      show_in_print: !!document.getElementById(`pm-af-print-${i}`)?.checked,
      value: document.getElementById(`pm-af-val-${i}`)?.value || '',
    });
  }
  return JSON.stringify(out);
}

function hopPartySetCreditLimitMode(mode) {
  const custom = mode === 'custom';
  document.getElementById('pm-credit-no')?.classList.toggle('is-active', !custom);
  document.getElementById('pm-credit-custom')?.classList.toggle('is-active', custom);
  const wrap = document.getElementById('pm-credit-limit-wrap');
  if (wrap) wrap.classList.toggle('hidden', !custom);
  const flag = document.getElementById('pm-credit-no-limit');
  if (flag) flag.value = custom ? '0' : '1';
}

function hopPartyCopyBillingToShipping() {
  const bill = document.getElementById('pm-address')?.value || '';
  const ship = document.getElementById('pm-shipping');
  if (ship) ship.value = bill;
}

const HOP_DEFAULT_PARTY_GROUPS = ['Buyer', 'Supplier'];

function hopPartyGroupStorageKey() {
  return 'hop_party_groups_v1';
}

function hopPartyGroupSavedList() {
  try {
    const raw = localStorage.getItem(hopPartyGroupStorageKey());
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.map((x) => String(x || '').trim()).filter(Boolean) : [];
  } catch (_) {
    return [];
  }
}

function hopPartyGroupPersist(name) {
  const n = String(name || '').trim();
  if (!n) return;
  const all = new Set([...HOP_DEFAULT_PARTY_GROUPS, ...hopPartyGroupSavedList(), n]);
  localStorage.setItem(hopPartyGroupStorageKey(), JSON.stringify([...all].sort((a, b) => a.localeCompare(b))));
}

function hopPartyGroupAllOptions() {
  const fromData = [];
  (hopState.customers || []).forEach((c) => {
    if (c.customer_type) fromData.push(String(c.customer_type).trim());
  });
  (hopState._parties || []).forEach((p) => {
    if (p.customer_type) fromData.push(String(p.customer_type).trim());
    if (p._type === 'vendor') fromData.push('Supplier');
  });
  const set = new Set([...HOP_DEFAULT_PARTY_GROUPS, ...hopPartyGroupSavedList(), ...fromData.filter(Boolean)]);
  return [...set].sort((a, b) => a.localeCompare(b));
}

function hopPartyGroupCloseMenu() {
  document.getElementById('pm-group-menu')?.classList.add('hidden');
}

function hopPartyGroupOpenMenu() {
  const menu = document.getElementById('pm-group-menu');
  const input = document.getElementById('pm-group');
  if (!menu || !input) return;
  hopPartyGroupRenderMenu(input.value || '');
  menu.classList.remove('hidden');
}

function hopPartyGroupRenderMenu(query) {
  const menu = document.getElementById('pm-group-menu');
  if (!menu) return;
  const q = String(query || '').trim().toLowerCase();
  const groups = hopPartyGroupAllOptions().filter((g) => !q || g.toLowerCase().includes(q));
  const exact = hopPartyGroupAllOptions().some((g) => g.toLowerCase() === q);
  menu.innerHTML = `
    <button type="button" class="nx-party-group-new" onclick="hopPartyGroupCreateNew()">+ New Group</button>
    ${groups.map((g) => `
      <button type="button" class="nx-party-group-opt" onclick="hopPartyGroupSelect('${foEscapeAttr(g)}')">${foEscapeText(g)}</button>
    `).join('') || '<p class="nx-party-group-empty">No groups yet</p>'}
    ${q && !exact ? `<button type="button" class="nx-party-group-opt nx-party-group-create" onclick="hopPartyGroupSelect('${foEscapeAttr(String(query || '').trim())}')">Create “${foEscapeText(String(query || '').trim())}”</button>` : ''}
  `;
}

function hopPartyGroupSelect(name) {
  const n = String(name || '').trim();
  if (!n) return;
  hopPartyGroupPersist(n);
  const input = document.getElementById('pm-group');
  if (input) input.value = n;
  hopPartyGroupCloseMenu();
}

function hopPartyGroupCreateNew() {
  const typed = String(document.getElementById('pm-group')?.value || '').trim();
  const name = window.prompt('New party group name', typed || '');
  if (name == null) return;
  const clean = String(name).trim();
  if (!clean) return;
  hopPartyGroupSelect(clean);
}

function hopPartyGroupOnInput() {
  const input = document.getElementById('pm-group');
  hopPartyGroupOpenMenu();
  hopPartyGroupRenderMenu(input?.value || '');
}

function hopCloseAddPartyChooser() {
  document.getElementById('hop-add-party-chooser')?.remove();
}

function hopOpenAddPartyChooser() {
  hopCloseAddPartyChooser();
  hopClosePartyScanModal();
  const modal = document.createElement('div');
  modal.id = 'hop-add-party-chooser';
  modal.className = 'nx-party-modal hop-add-party-chooser';
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopCloseAddPartyChooser()"></div>
    <div class="nx-party-modal-card hop-add-party-card" role="dialog" aria-label="Add Party">
      <div class="nx-party-modal-head">
        <h2>Add Party</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopCloseAddPartyChooser()" title="Close">&times;</button>
      </div>
      <div class="hop-add-party-choices">
        <button type="button" class="hop-add-party-choice" onclick="hopAddPartyViaScan()">
          <strong>Scan visiting card</strong>
          <span>Capture card · auto-fill details</span>
        </button>
        <button type="button" class="hop-add-party-choice hop-add-party-choice--manual" onclick="hopAddPartyManual()">
          <strong>Manual</strong>
          <span>Enter details yourself</span>
        </button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('is-open'));
}

function hopAddPartyManual() {
  hopCloseAddPartyChooser();
  hopOpenPartyEditModal('customer', null);
}

function hopAddPartyViaScan() {
  hopCloseAddPartyChooser();
  hopOpenPartyScanModal();
}

function hopClosePartyScanModal() {
  document.getElementById('hop-party-scan-modal')?.remove();
  hopState.vcardMode = null;
}

function hopOpenPartyScanModal() {
  hopClosePartyScanModal();
  hopState.vcardMode = 'party';
  hopState.visitingCardFile = null;
  const modal = document.createElement('div');
  modal.id = 'hop-party-scan-modal';
  modal.className = 'nx-party-modal hop-party-scan-modal';
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopClosePartyScanModal()"></div>
    <div class="nx-party-modal-card hop-party-scan-card" role="dialog" aria-label="Scan visiting card">
      <div class="nx-party-modal-head">
        <h2>Scan visiting card</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopClosePartyScanModal()" title="Close">&times;</button>
      </div>
      <div class="hop-party-scan-body">
        <div class="hop-vcard-source">
          <button type="button" class="hop-vcard-src-btn hop-vcard-src-btn--primary" onclick="hopPickPhoto('hop-vcard-cam')">Camera</button>
          <button type="button" class="hop-vcard-src-btn" onclick="hopPickPhoto('hop-vcard-gal')">Gallery</button>
        </div>
        <input id="hop-vcard-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-vcard-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-vcard-preview" class="hop-vcard-stage is-empty">
          <div class="hop-vcard-empty">
            <strong>Take or upload card photo</strong>
            <span>Clear photo · good light · flat card</span>
          </div>
        </div>
        <div class="hop-vcard-capture-foot">
          <button type="button" class="nx-btn" onclick="hopClosePartyScanModal(); hopOpenAddPartyChooser();">Back</button>
          <button type="button" class="nx-btn nx-btn-primary" id="hop-vcard-scan-btn" onclick="hopScanVisitingCard()">Read card</button>
        </div>
        <p id="hop-vcard-status" class="hop-vcard-status">Waiting for photo</p>
        <div id="hop-vcard-form" class="hidden" aria-hidden="true"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('is-open'));

  const bindPreview = (inputId) => {
    const input = document.getElementById(inputId);
    input?.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const otherId = inputId === 'hop-vcard-cam' ? 'hop-vcard-gal' : 'hop-vcard-cam';
      const other = document.getElementById(otherId);
      if (other) other.value = '';
      hopState.visitingCardFile = file;
      const box = document.getElementById('hop-vcard-preview');
      if (box) {
        box.classList.remove('is-empty');
        box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
      }
      const status = document.getElementById('hop-vcard-status');
      if (status) {
        status.textContent = 'Photo ready — reading…';
        status.classList.add('is-busy');
        status.classList.remove('is-error', 'is-ok');
      }
      hopScheduleVisitingCardScan();
    });
  };
  bindPreview('hop-vcard-cam');
  bindPreview('hop-vcard-gal');
}

function hopPartyRowFromCardFields(f) {
  const fields = f || {};
  return {
    company: fields.company || '',
    billing_name: fields.company || '',
    contact_person: fields.contact_person || '',
    mobile: fields.mobile || '',
    email: fields.email || '',
    city: fields.city || '',
    customer_type: fields.customer_type || 'Buyer',
    gst_no: fields.gst_no || '',
    pan: fields.pan || '',
    address: fields.address || '',
    remarks: fields.remarks || '',
    source: 'visiting_card',
  };
}

function hopOpenPartyEditModal(kind, row) {
  const isVendor = kind === 'vendor';
  const isEdit = !!(row && row.id != null);
  const data = row || {};
  hopState.contactEdit = isEdit ? { kind, id: Number(data.id) } : { kind, id: null };
  const title = isEdit ? 'Edit Party' : 'Add Party';
  const partyName = data.company || '';
  const billingName = data.billing_name || data.company || '';
  const group = isVendor ? 'Supplier' : (data.customer_type || 'Buyer');
  const gstType = data.gst_type || data.industry || (data.gst_no ? 'Registered Business - Regular' : 'Unregistered');
  const stateVal = data.state || '';
  const txnBal = Number(data._balance || 0);
  const openingBal = data.opening_balance != null && data.opening_balance !== '' ? data.opening_balance : '';
  const openingDate = (data.opening_balance_date || new Date().toISOString().slice(0, 10));
  const noLimit = data.credit_no_limit == null ? 1 : Number(data.credit_no_limit);
  const creditLimit = data.credit_limit != null && data.credit_limit !== '' ? data.credit_limit : '';
  const shipping = data.shipping_address || data.address || '';
  const billingAddr = data.address || '';
  const addFields = hopPartyParseAdditionalFields(data.additional_fields);
  if (data.mobile && addFields[0] && addFields[0].name.toLowerCase().includes('mobile') && !addFields[0].value) {
    addFields[0].value = data.mobile;
  }
  const stateOpts = HOP_INDIAN_STATES.map((s) =>
    `<option value="${foEscapeAttr(s)}"${String(stateVal).toLowerCase() === s.toLowerCase() ? ' selected' : ''}>${foEscapeText(s)}</option>`
  ).join('');
  const afRows = addFields.map((f, i) => `
    <div class="nx-party-af-row">
      <label class="nx-party-af-check"><input type="checkbox" id="pm-af-en-${i}"${f.enabled ? ' checked' : ''} /></label>
      <input id="pm-af-name-${i}" class="nx-party-af-name" placeholder="Additional Field ${i + 1} Name" value="${foEscapeAttr(f.name)}" />
      <label class="nx-party-af-print">
        <span>Show in Print</span>
        <input type="checkbox" id="pm-af-print-${i}"${f.show_in_print ? ' checked' : ''} />
      </label>
      <input id="pm-af-val-${i}" class="nx-party-af-val" placeholder="Enter value" value="${foEscapeAttr(f.value)}" />
    </div>`).join('');

  document.getElementById('hop-party-edit-modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'hop-party-edit-modal';
  modal.className = 'nx-party-modal';
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopClosePartyEditModal()"></div>
    <div class="nx-party-modal-card" role="dialog" aria-label="${foEscapeAttr(title)}">
      <div class="nx-party-modal-head">
        <h2>${foEscapeText(title)}</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopClosePartyEditModal()" title="Close">&times;</button>
      </div>
      <div class="nx-party-modal-body">
        <div class="nx-party-primary">
          <label class="nx-party-field">
            <span>Party Name *</span>
            <input id="pm-name" value="${foEscapeAttr(partyName)}" placeholder="Party / company name" oninput="document.getElementById('pm-bill-hint').textContent=this.value?('“'+this.value+'” will be printed on your invoice.'):''" />
            <small id="pm-bill-hint" class="nx-party-hint">${partyName ? `“${foEscapeText(partyName)}” will be printed on your invoice.` : ''}</small>
          </label>
          <label class="nx-party-field">
            <span>Billing Name</span>
            <input id="pm-billing" value="${foEscapeAttr(billingName)}" placeholder="Name on invoice" />
          </label>
          <label class="nx-party-field nx-party-gst-wrap">
            <span>GSTIN</span>
            <div class="nx-party-gst-row">
              <div class="nx-party-gst-input-wrap">
                <input id="pm-gstin" value="${foEscapeAttr(data.gst_no || '')}" placeholder="22AAAAA0000A1Z5" maxlength="15"
                  oninput="hopPartyGstCheck()" onblur="hopPartyGstCheck()" />
                <span id="pm-gst-ok" class="nx-party-gst-ok hidden" title="Valid GSTIN">✓</span>
              </div>
              <button type="button" id="pm-gst-fetch" class="nx-party-gst-fetch" onclick="hopPartyFetchGstDetails(true)" disabled title="Auto-fill name, state &amp; address from GSTIN">Fetch details</button>
            </div>
            <small id="pm-gst-hint" class="nx-party-hint nx-party-gst-hint"></small>
          </label>
          <label class="nx-party-field">
            <span>Phone Number</span>
            <input id="pm-phone" value="${foEscapeAttr(data.mobile || '')}" placeholder="10-digit mobile" />
          </label>
          <label class="nx-party-field nx-party-group-field">
            <span>Party Group</span>
            <div class="nx-party-group-combo">
              <input id="pm-group" type="text" autocomplete="off" value="${foEscapeAttr(group === 'Vendor' ? 'Supplier' : group)}"
                placeholder="Search or select group"
                onfocus="hopPartyGroupOpenMenu()"
                oninput="hopPartyGroupOnInput()"
                onkeydown="if(event.key==='Escape'){hopPartyGroupCloseMenu();}" />
              <button type="button" class="nx-party-group-caret" onclick="hopPartyGroupOpenMenu()" tabindex="-1" aria-label="Open groups">▾</button>
              <div id="pm-group-menu" class="nx-party-group-menu hidden"></div>
            </div>
          </label>
        </div>

        <div class="nx-party-tabs">
          <button type="button" class="nx-party-tab is-active" data-party-tab="gst" onclick="hopPartyModalSetTab('gst')">GST &amp; Address</button>
          <button type="button" class="nx-party-tab" data-party-tab="credit" onclick="hopPartyModalSetTab('credit')">Credit &amp; Balance <span class="nx-party-tab-badge">New</span></button>
          <button type="button" class="nx-party-tab" data-party-tab="more" onclick="hopPartyModalSetTab('more')">Additional Fields</button>
        </div>

        <div class="nx-party-panels">
          <div class="nx-party-panel" data-party-panel="gst">
            <div class="nx-party-gst-grid">
              <div class="nx-party-gst-left">
                <label class="nx-party-field">
                  <span>GST Type</span>
                  <select id="pm-gst-type">
                    <option${String(gstType).includes('Unregistered') ? ' selected' : ''}>Unregistered</option>
                    <option${String(gstType).includes('Regular') ? ' selected' : ''}>Registered Business - Regular</option>
                    <option${String(gstType).includes('Composition') ? ' selected' : ''}>Registered Business - Composition</option>
                  </select>
                </label>
                <label class="nx-party-field">
                  <span>State</span>
                  <select id="pm-state">
                    <option value="">Select state</option>
                    ${stateOpts}
                  </select>
                </label>
                <label class="nx-party-field">
                  <span>Email ID</span>
                  <input id="pm-email" type="email" value="${foEscapeAttr(data.email || '')}" placeholder="name@company.com" />
                </label>
                <label class="nx-party-field">
                  <span>Contact Person</span>
                  <input id="pm-contact" value="${foEscapeAttr(data.contact_person || '')}" placeholder="Contact person" />
                </label>
                <label class="nx-party-field">
                  <span>City</span>
                  <input id="pm-city" value="${foEscapeAttr(data.city || '')}" placeholder="City" />
                </label>
              </div>
              <div class="nx-party-addr-col">
                <div class="nx-party-addr-head">
                  <strong>Billing Address</strong>
                  <button type="button" class="nx-party-addr-add" onclick="document.getElementById('pm-address')?.focus()">+ Add New Address</button>
                </div>
                <div class="nx-party-addr-card">
                  <textarea id="pm-address" rows="6" placeholder="Billing address">${foEscapeText(billingAddr)}</textarea>
                </div>
              </div>
              <div class="nx-party-addr-col">
                <div class="nx-party-addr-head">
                  <strong>Shipping Address</strong>
                  <button type="button" class="nx-party-addr-add" onclick="hopPartyCopyBillingToShipping()">+ Same as Billing</button>
                </div>
                <div class="nx-party-addr-card">
                  <textarea id="pm-shipping" rows="6" placeholder="Shipping address">${foEscapeText(shipping)}</textarea>
                </div>
              </div>
            </div>
          </div>

          <div class="nx-party-panel hidden" data-party-panel="credit">
            <div class="nx-party-credit-grid">
              <label class="nx-party-field">
                <span>Opening Balance</span>
                <input id="pm-opening-balance" type="number" step="0.01" value="${foEscapeAttr(openingBal)}" placeholder="0.00" />
              </label>
              <label class="nx-party-field">
                <span>As Of Date</span>
                <input id="pm-opening-date" type="date" value="${foEscapeAttr(String(openingDate).slice(0, 10))}" />
              </label>
              <div class="nx-party-field nx-party-span2">
                <span>Credit Limit</span>
                <input type="hidden" id="pm-credit-no-limit" value="${noLimit ? '1' : '0'}" />
                <div class="nx-party-credit-toggle">
                  <button type="button" id="pm-credit-no" class="nx-party-tog${noLimit ? ' is-active' : ''}" onclick="hopPartySetCreditLimitMode('none')">No Limit</button>
                  <button type="button" id="pm-credit-custom" class="nx-party-tog${!noLimit ? ' is-active' : ''}" onclick="hopPartySetCreditLimitMode('custom')">Custom Limit</button>
                </div>
                <div id="pm-credit-limit-wrap" class="nx-party-credit-limit-wrap${!noLimit ? '' : ' hidden'}">
                  <input id="pm-credit-limit" type="number" step="0.01" value="${foEscapeAttr(creditLimit)}" placeholder="Enter credit limit" />
                </div>
              </div>
              <label class="nx-party-field">
                <span>Current Receivable (from transactions)</span>
                <input type="text" value="₹ ${txnBal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}" readonly />
                <small class="nx-party-hint">Auto-calculated from party transactions.</small>
              </label>
              <label class="nx-party-field">
                <span>Status</span>
                <select id="pm-status">
                  <option value="active"${(data.status || 'active') === 'active' ? ' selected' : ''}>Active</option>
                  <option value="inactive"${data.status === 'inactive' ? ' selected' : ''}>Inactive</option>
                </select>
              </label>
            </div>
          </div>

          <div class="nx-party-panel hidden" data-party-panel="more">
            <div class="nx-party-af-list">
              ${afRows}
            </div>
            <div class="nx-party-more-grid" style="margin-top:16px;">
              <label class="nx-party-field"><span>PAN</span><input id="pm-pan" value="${foEscapeAttr(data.pan || '')}" /></label>
              <label class="nx-party-field"><span>Hotel Brand</span><input id="pm-hotel" value="${foEscapeAttr(data.hotel_brand || '')}" /></label>
              <label class="nx-party-field"><span>Architect</span><input id="pm-architect" value="${foEscapeAttr(data.architect || '')}" /></label>
              <label class="nx-party-field"><span>Consultant</span><input id="pm-consultant" value="${foEscapeAttr(data.consultant || '')}" /></label>
              <label class="nx-party-field"><span>Rating</span><input id="pm-rating" value="${foEscapeAttr(data.potential_rating || data.rating || '')}" /></label>
              <label class="nx-party-field"><span>Assigned To</span><input id="pm-assigned" value="${foEscapeAttr(data.assigned_to || '')}" /></label>
              <label class="nx-party-field"><span>Products (vendor)</span><input id="pm-products" value="${foEscapeAttr(data.products || '')}" /></label>
              <label class="nx-party-field"><span>Payment Terms</span><input id="pm-payterms" value="${foEscapeAttr(data.payment_terms || '')}" /></label>
              <label class="nx-party-field nx-party-span2"><span>Remarks</span><input id="pm-remarks" value="${foEscapeAttr(data.remarks || '')}" /></label>
            </div>
          </div>
        </div>
      </div>
      <div class="nx-party-modal-foot">
        ${isEdit ? `<button type="button" class="nx-btn nx-party-btn-delete" onclick="hopDeleteFromPartyModal()">Delete</button>` : '<span></span>'}
        <div class="nx-party-foot-right">
          <button type="button" class="nx-btn" onclick="hopClosePartyEditModal()">Cancel</button>
          <button type="button" id="pm-save-btn" class="nx-btn nx-btn-primary" onclick="hopSavePartyModal()">${isEdit ? 'Update' : 'Save'}</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  // Close group menu when clicking outside the combo.
  modal.addEventListener('mousedown', (ev) => {
    if (!ev.target.closest?.('.nx-party-group-combo')) hopPartyGroupCloseMenu();
  });
  requestAnimationFrame(() => {
    modal.classList.add('is-open');
    hopPartyGstCheck();
    hopPartySetCreditLimitMode(noLimit ? 'none' : 'custom');
    document.getElementById('pm-name')?.focus();
  });
}

async function hopDeleteFromPartyModal() {
  const edit = hopState.contactEdit;
  if (!edit?.id) return;
  const type = edit.kind === 'vendor' ? 'vendors' : 'customers';
  const name = document.getElementById('pm-name')?.value || `contact #${edit.id}`;
  await hopDeleteContact(type, edit.id, name);
}

function hopPartyDupBannerClear() {
  document.getElementById('pm-dup-banner')?.remove();
}

function hopPartyUniqueNames(matches) {
  const seen = new Set();
  const names = [];
  for (const m of matches || []) {
    const n = String(m?.company || '').trim();
    const key = n.toLowerCase();
    if (!n || seen.has(key)) continue;
    seen.add(key);
    names.push(n);
  }
  return names;
}

/** User-facing duplicate copy — names only, no scores / fuzzy jargon. */
function hopPartyDupConfirmMessage(matches, apiMessage) {
  const names = hopPartyUniqueNames(matches);
  if (names.length === 1) {
    return `A party is already saved as “${names[0]}”.\n\nDo you really want to save this as a new party?`;
  }
  if (names.length > 1) {
    const listed = names.slice(0, 3).map((n) => `“${n}”`).join(', ');
    return `Similar parties are already saved: ${listed}.\n\nDo you really want to save this as a new party?`;
  }
  return (
    apiMessage
    || 'A party with a similar name is already saved.\n\nDo you really want to save this as a new party?'
  );
}

function hopPartyDupBannerShow(message, matches) {
  hopPartyDupBannerClear();
  const body = document.querySelector('#hop-party-edit-modal .nx-party-modal-body');
  if (!body) return;
  const banner = document.createElement('div');
  banner.id = 'pm-dup-banner';
  banner.className = 'nx-party-dup-banner';
  banner.innerHTML = `
    <div class="nx-party-dup-title">Party already saved</div>
    <p>${foEscapeText(message || 'A party with a similar name is already saved.')}</p>
    <p class="nx-party-dup-hint">Choose <strong>Yes, save</strong> only if this is a different party.</p>`;
  body.prepend(banner);
  banner.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function hopSavePartyModal() {
  const btn = document.getElementById('pm-save-btn');
  const prevLabel = btn?.textContent || 'Update';
  try {
    const edit = hopState.contactEdit || { kind: 'customer', id: null };
    const group = String(document.getElementById('pm-group')?.value || 'Buyer').trim() || 'Buyer';
    hopPartyGroupPersist(group);
    const asVendor = /supplier|vendor/i.test(group);
    const kind = asVendor ? 'vendor' : 'customer';
    const saveKind = edit.id ? edit.kind : kind;
    const company = String(document.getElementById('pm-name')?.value || '').trim();
    if (!company) {
      alert('Party Name is required');
      document.getElementById('pm-name')?.focus();
      return;
    }
    hopPartyDupBannerClear();
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Saving…';
    }

    // Auto-fill state from address if user left it blank.
    let state = document.getElementById('pm-state')?.value || '';
    if (!state) {
      const blob = `${document.getElementById('pm-address')?.value || ''} ${document.getElementById('pm-shipping')?.value || ''}`;
      const hit = HOP_INDIAN_STATES.find((s) => blob.toLowerCase().includes(s.toLowerCase()));
      if (hit) {
        state = hit;
        const sel = document.getElementById('pm-state');
        if (sel) sel.value = hit;
      }
    }

    const city = String(document.getElementById('pm-city')?.value || '').trim() || null;
    const gstType = document.getElementById('pm-gst-type')?.value || '';
    const creditNoLimit = document.getElementById('pm-credit-no-limit')?.value === '1' ? 1 : 0;
    let additionalFields = '[]';
    try {
      additionalFields = hopPartyCollectAdditionalFields();
    } catch (_) {
      additionalFields = '[]';
    }
    const sharedExtra = {
      billing_name: document.getElementById('pm-billing')?.value || company,
      shipping_address: document.getElementById('pm-shipping')?.value || '',
      state,
      gst_type: gstType,
      opening_balance: document.getElementById('pm-opening-balance')?.value || '',
      opening_balance_date: document.getElementById('pm-opening-date')?.value || '',
      credit_limit: creditNoLimit ? '' : (document.getElementById('pm-credit-limit')?.value || ''),
      credit_no_limit: creditNoLimit,
      additional_fields: additionalFields,
    };
    const payloadCustomer = {
      company,
      contact_person: document.getElementById('pm-contact')?.value || '',
      mobile: document.getElementById('pm-phone')?.value || '',
      email: document.getElementById('pm-email')?.value || '',
      city,
      industry: gstType,
      customer_type: group,
      hotel_brand: document.getElementById('pm-hotel')?.value || '',
      architect: document.getElementById('pm-architect')?.value || '',
      consultant: document.getElementById('pm-consultant')?.value || '',
      potential_rating: document.getElementById('pm-rating')?.value || '',
      assigned_to: document.getElementById('pm-assigned')?.value || '',
      address: document.getElementById('pm-address')?.value || '',
      gst_no: document.getElementById('pm-gstin')?.value || '',
      pan: document.getElementById('pm-pan')?.value || '',
      status: document.getElementById('pm-status')?.value || 'active',
      remarks: document.getElementById('pm-remarks')?.value || '',
      ...sharedExtra,
    };
    const payloadVendor = {
      company,
      contact_person: document.getElementById('pm-contact')?.value || '',
      mobile: document.getElementById('pm-phone')?.value || '',
      email: document.getElementById('pm-email')?.value || '',
      city,
      products: document.getElementById('pm-products')?.value || '',
      gst_no: document.getElementById('pm-gstin')?.value || '',
      payment_terms: document.getElementById('pm-payterms')?.value || '',
      rating: document.getElementById('pm-rating')?.value || '',
      address: document.getElementById('pm-address')?.value || '',
      status: document.getElementById('pm-status')?.value || 'active',
      ...sharedExtra,
    };
    const payload = saveKind === 'vendor' ? payloadVendor : payloadCustomer;
    const urlBase = saveKind === 'vendor' ? '/api/v1/hop/vendors' : '/api/v1/hop/customers';
    const isEdit = !!(edit && edit.id);
    const saveUrl = isEdit ? `${urlBase}/${edit.id}` : urlBase;
    const result = await hopCreatePartyWithDupConfirm(saveUrl, payload, {
      method: isEdit ? 'PATCH' : 'POST',
    });
    if (result === false) {
      if (typeof nexoraToast === 'function') {
        nexoraToast('Save cancelled. Change the party name, or confirm if it is a new party.', 'warn', { duration: 5500 });
      }
      return;
    }
    if (result == null) {
      throw new Error('Session expired or save blocked. Please login again and retry Update.');
    }
    hopState.customers = [];
    hopState.vendors = [];
    hopClosePartyEditModal();
    if (typeof nexoraToast === 'function') {
      nexoraToast(isEdit ? 'Party updated.' : 'Party saved.', 'success');
    }
    openHopView(hopContactReturnView(saveKind === 'vendor' ? 'vendors' : 'customers'));
  } catch (e) {
    console.error('hopSavePartyModal failed', e);
    alert(e?.message || 'Save failed');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel;
    }
  }
}

/** Create/update party; on fuzzy duplicate ask user before force_save. Returns false if cancelled. */
async function hopCreatePartyWithDupConfirm(url, payload, options = {}) {
  const method = options.method || 'POST';
  let response;
  try {
    response = await fetchWithAuth(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    if (isSessionTimeoutError(e)) return null;
    throw e;
  }
  const data = await parseApiJson(response);
  if (response.status === 409 && (data.requires_confirmation || data?.error?.code === 'DUPLICATE_CONFIRM')) {
    const matches = data.data?.matches || [];
    const msg = hopPartyDupConfirmMessage(matches, data.message);
    hopPartyDupBannerShow(msg.replace(/\n+/g, ' '), matches);
    const ok = await nexoraConfirm(msg, {
      title: 'Party already saved',
      okText: 'Yes, save',
      cancelText: 'Cancel',
    });
    if (!ok) return false;
    hopPartyDupBannerClear();
    return hopApi(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, force_save: true }),
    });
  }
  if (!response.ok || !data.success) {
    throw new Error(getApiErrorMessage(data, 'Request failed'));
  }
  return data.data;
}

/* ---------- Parties (Vyapar-style unified view) ---------- */

/** Vyapar txn_type → Sale / Purchase menus (single source of truth). */
const HOP_TXN_SALE_INVOICE = new Set([1]);           // Sale Invoice only (real sale)
const HOP_TXN_ESTIMATE = new Set([27, 30]);          // 27 = Estimate in this Vyapar data (HOPPI…)
const HOP_TXN_SALE_RETURN = new Set([21]);           // Sale Return / Credit Note
const HOP_TXN_PAYMENT_IN = new Set([3]);            // Payment-In (Vyapar type 3)
const HOP_TXN_PAYMENT_OUT = new Set([4]);           // Payment-Out (Vyapar type 4)
const HOP_TXN_PURCHASE_BILL = new Set([2]);
const HOP_TXN_EXPENSE = new Set([7]);
const HOP_TXN_PURCHASE_RETURN = new Set([16]);
const HOP_TXN_OTHER_DOCS = new Set([65, 81, 82, 83]); // Sale Order, Journal, Challan, Proforma
const HOP_TXN_SALE_ORDER = new Set([65]);
const HOP_TXN_JOURNAL = new Set([81]);               // Journal Entry (menu hidden until Accounting)
const HOP_TXN_CHALLAN = new Set([82]);
const HOP_TXN_PROFORMA = new Set([83]);

/** Every imported type must appear in exactly one bucket (journal stays hidden in nav). */
const HOP_TXN_MENU_COVERAGE = new Set([
  ...HOP_TXN_SALE_INVOICE, ...HOP_TXN_ESTIMATE, ...HOP_TXN_PROFORMA,
  ...HOP_TXN_PAYMENT_IN, ...HOP_TXN_SALE_ORDER, ...HOP_TXN_CHALLAN,
  ...HOP_TXN_SALE_RETURN, ...HOP_TXN_PURCHASE_BILL, ...HOP_TXN_PAYMENT_OUT,
  ...HOP_TXN_EXPENSE, ...HOP_TXN_PURCHASE_RETURN, ...HOP_TXN_JOURNAL,
]);

function hopTxnTypeOf(row) {
  return Number(row?.txn_type || 0);
}

function hopTxnStatusOf(row) {
  return String(row?.status_text || row?.status || '').trim().toLowerCase();
}

/** Cancelled / draft / void — never count in Total Sale or Balance. */
function hopTxnIsCancelledOrDraft(row) {
  const s = hopTxnStatusOf(row);
  return /cancel|void|draft|rejected|deleted/.test(s);
}

/**
 * Final money docs for Total Sale / Balance.
 * Estimates (incl. Approved) are shown in the list but do not count as sale.
 * Journals count for Balance (write-off) but not Total Sale.
 */
function hopTxnIsFinalForSaleMath(row) {
  if (hopTxnIsCancelledOrDraft(row)) return false;
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_ESTIMATE.has(ty)) return false;
  if (HOP_TXN_JOURNAL.has(ty)) return true;
  const s = hopTxnStatusOf(row);
  if (!s) return true;
  if (s === 'approved' || s === 'approve') return false;
  return true;
}

/**
 * Total Sale contribution:
 *   + Sale Invoice amount
 *   − Sale Return / Credit Note amount
 * Estimate / Quotation / Proforma / Orders → 0
 */
function hopPartyTxnSaleContribution(row) {
  if (!hopTxnIsFinalForSaleMath(row)) return 0;
  const ty = hopTxnTypeOf(row);
  const amt = parseFloat(row?.total_amount || 0) || 0;
  if (HOP_TXN_SALE_INVOICE.has(ty)) return amt;
  if (HOP_TXN_SALE_RETURN.has(ty)) return -amt;
  return 0;
}

/**
 * Party closing balance (receivable).
 * Sale Invoice due − returns − unused Payment-In.
 *
 * Journals: do NOT subtract again here. In Vyapar, a journal settlement marks the
 * related Sale as Paid (balance 0). Counting journal −total on top of that creates
 * a bogus negative balance (e.g. Anjali −2895).
 * We never invent Payment-In / write-off rows — only trust imported invoice balances.
 */
function hopPartyTxnBalanceContribution(row) {
  if (!hopTxnIsFinalForSaleMath(row)) return 0;
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_ESTIMATE.has(ty)) return 0;
  if (HOP_TXN_JOURNAL.has(ty)) return 0;
  if (HOP_TXN_OTHER_DOCS.has(ty)) return 0;
  const bal = parseFloat(row?.balance_amount || 0) || 0;
  if (HOP_TXN_SALE_INVOICE.has(ty)) return bal;
  if (HOP_TXN_SALE_RETURN.has(ty) || HOP_TXN_PAYMENT_IN.has(ty)) return -bal;
  if (HOP_TXN_PURCHASE_BILL.has(ty) || HOP_TXN_EXPENSE.has(ty)) return bal;
  if (HOP_TXN_PAYMENT_OUT.has(ty)) return -bal;
  return 0;
}

function hopComputePartyTotalSale(txns) {
  return (txns || []).reduce((s, t) => s + hopPartyTxnSaleContribution(t), 0);
}

/** Primary party ledger rows (sale docs + estimates + payments). Rest go under Other. */
function hopTxnIsPrimaryPartyDoc(row) {
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_OTHER_DOCS.has(ty)) return false;
  if (hopTxnIsCancelledOrDraft(row)) return false;
  if (HOP_TXN_ESTIMATE.has(ty)) return true; // show Approved estimates like Vyapar
  if (!hopTxnIsFinalForSaleMath(row)) return false;
  return HOP_TXN_SALE_INVOICE.has(ty) || HOP_TXN_SALE_RETURN.has(ty) || HOP_TXN_PAYMENT_IN.has(ty);
}

function hopPartyTxnDisplayLabel(row) {
  const ty = hopTxnTypeOf(row);
  const raw = String(row?.txn_label || '').trim();
  if (HOP_TXN_ESTIMATE.has(ty)) {
    if (/^sale$/i.test(raw)) return 'Estimate';
    return raw || 'Estimate';
  }
  if (HOP_TXN_JOURNAL.has(ty)) return raw || 'Journal Entry';
  if (HOP_TXN_SALE_ORDER.has(ty)) return raw || 'Sale Order';
  if (ty === 1) return raw || 'Sale Invoice';
  if (ty === 21) return raw && !/^sale return$/i.test(raw) ? raw : 'Sale Return / Credit Note';
  return raw || (ty ? `Txn ${ty}` : '—');
}

function hopPartyTxnDisplayStatus(row) {
  const ty = hopTxnTypeOf(row);
  const raw = String(row.status_text || '').trim();
  // Estimates: Approved / Cancelled / Draft only — never Partial / Unpaid / Paid.
  if (HOP_TXN_ESTIMATE.has(ty)) {
    if (/cancel/i.test(raw)) return 'Cancelled';
    if (/draft/i.test(raw)) return 'Draft';
    return 'Approved';
  }
  // Journals are posted adjustments (NPA wipe-off etc.), not open bills.
  if (HOP_TXN_JOURNAL.has(ty)) {
    if (/cancel/i.test(raw)) return 'Cancelled';
    return 'Posted';
  }
  const amt = parseFloat(row.total_amount || 0) || 0;
  const bal = parseFloat(row.balance_amount || 0) || 0;
  if (bal <= 0.009 && amt > 0.009) return 'Paid';
  // Do not invent Partial from total−balance alone (line+tax often inflates total).
  // Only show Partial when backend says so AND balance is clearly below a trusted total.
  if (/partial/i.test(raw) && amt > bal + 0.05 && bal > 0.05) {
    const paid = amt - bal;
    // Tiny residual vs bill → treat as Open (rounding / tax noise).
    if (paid <= Math.max(1, amt * 0.001)) return 'Open';
    return 'Partial';
  }
  if (/^paid$/i.test(raw) && bal > 0.05) return 'Open';
  return raw && !/^partial$/i.test(raw) ? raw : 'Open';
}

function hopPartyTxnRowHtml(row) {
  const amt = parseFloat(row.total_amount || 0);
  const ty = hopTxnTypeOf(row);
  const isEstimate = HOP_TXN_ESTIMATE.has(ty);
  const isJournal = HOP_TXN_JOURNAL.has(ty);
  // For display total on unpaid sale docs, don't show inflated amount above due balance.
  const balRaw = parseFloat(row.balance_amount || 0) || 0;
  const displayAmt = (!isEstimate && !isJournal && balRaw > 0.05 && amt > balRaw + 0.05 && hopPartyTxnDisplayStatus(row) === 'Open')
    ? balRaw
    : amt;
  const bal = (isEstimate || isJournal) ? 0 : balRaw;
  const status = hopPartyTxnDisplayStatus(row);
  const s = String(status).toLowerCase();
  const statusClass = s.includes('paid') || s.includes('used') || s.includes('approved') || s.includes('posted')
    ? 'is-paid'
    : (s.includes('cancel') ? 'is-unpaid' : (s.includes('partial') ? 'is-partial' : 'is-partial'));
  const label = hopPartyTxnDisplayLabel(row);
  const tip = isEstimate
    ? 'Estimate — not counted in party Balance / Total Sale'
    : (isJournal ? 'Imported Journal (shown for history). Settlement is on the Sale (Paid) — not counted again in Balance' : '');
  const balCell = (isEstimate || isJournal)
    ? `<td class="pty-txn-amt pty-txn-bal-na" title="${foEscapeAttr(isJournal ? 'Journal history only — Balance comes from Sale Paid/Open' : 'Estimates have no receivable balance')}">—</td>`
    : `<td class="pty-txn-amt${bal > 0 ? ' is-due' : ''}">₹ ${bal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>`;
  const id = Number(row.id || 0);
  return `<tr class="pty-txn-row" role="button" tabindex="0"
    onclick="hopOpenPartyTxnDetail(${id})"
    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();hopOpenPartyTxnDetail(${id});}"
    title="Click to view transaction">
    <td title="${foEscapeAttr(tip || label)}">${foEscapeText(label)}</td>
    <td class="pty-txn-no" title="${foEscapeAttr(row.txn_number || '')}">${foEscapeText(row.txn_number || '—')}</td>
    <td>${foEscapeText((row.txn_date || '').slice(0, 10))}</td>
    <td class="pty-txn-amt">₹ ${displayAmt.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
    ${balCell}
    <td><span class="pty-status ${statusClass}">${foEscapeText(status)}</span></td>
    <td class="pty-txn-view"><span class="pty-txn-view-ico" aria-hidden="true">›</span></td>
  </tr>`;
}

function hopPartyTxnTableHtml(rows) {
  if (!rows.length) return '';
  return `<table class="pty-txn-table">
    <thead><tr>
      <th>Type</th><th>Number</th><th>Date</th><th>Total</th><th>Balance</th><th>Status</th><th></th>
    </tr></thead>
    <tbody>${rows.map(hopPartyTxnRowHtml).join('')}</tbody>
  </table>`;
}

function hopTxnModuleViewForType(ty) {
  const t = Number(ty || 0);
  if (HOP_TXN_SALE_INVOICE.has(t)) return 'sale_invoices';
  if (HOP_TXN_ESTIMATE.has(t)) return 'sale_estimates';
  if (HOP_TXN_PROFORMA.has(t)) return 'sale_proforma';
  if (HOP_TXN_PAYMENT_IN.has(t)) return 'sale_payment_in';
  if (HOP_TXN_SALE_ORDER.has(t)) return 'sale_orders';
  if (HOP_TXN_CHALLAN.has(t)) return 'sale_challan';
  if (HOP_TXN_SALE_RETURN.has(t)) return 'sale_returns';
  if (HOP_TXN_PURCHASE_BILL.has(t)) return 'purchase_bills';
  if (HOP_TXN_PAYMENT_OUT.has(t)) return 'purchase_payment_out';
  if (HOP_TXN_EXPENSE.has(t)) return 'purchase_expenses';
  if (HOP_TXN_PURCHASE_RETURN.has(t)) return 'purchase_returns';
  if (HOP_TXN_JOURNAL.has(t)) return 'journal_entries';
  return null;
}

function hopClosePartyTxnDetail() {
  const el = document.getElementById('hop-party-txn-overlay');
  if (!el) return;
  el.classList.remove('is-open');
  setTimeout(() => el.remove(), 220);
}

function hopOpenPartyTxnInModule(txnId) {
  const row = (hopState._partyTxns || []).find((t) => Number(t.id) === Number(txnId));
  if (!row) return;
  const view = hopTxnModuleViewForType(row.txn_type);
  if (!view) {
    if (typeof nexoraToast === 'function') nexoraToast('No list module for this document type yet.', 'warn');
    return;
  }
  hopClosePartyTxnDetail();
  const q = String(row.txn_number || '').trim();
  hopState.invoiceUi = {
    ...(hopState.invoiceUi || {}),
    period: 'all',
    from: '',
    to: '',
    status: 'all',
    q,
    party: String(row.party_name || '').trim().toLowerCase(),
  };
  openHopView(view);
}

function hopPreviewMoney(n) {
  const v = Number(n || 0);
  return `₹ ${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function hopPreviewDate(ymd) {
  const s = String(ymd || '').slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return s || '—';
  return `${m[3]}-${m[2]}-${m[1]}`;
}

function hopPrintPartyTxnPreview() {
  const sheet = document.getElementById('hop-doc-preview-sheet');
  if (!sheet) return;
  const w = window.open('', '_blank', 'noopener,noreferrer,width=900,height=1000');
  if (!w) {
    window.print();
    return;
  }
  w.document.write(`<!DOCTYPE html><html><head><title>Preview</title>
    <style>
      body{font-family:Arial,Helvetica,sans-serif;color:#111;margin:16px;background:#fff;}
      table{width:100%;border-collapse:collapse;}
      th,td{border:1px solid #cbd5e1;padding:6px 8px;font-size:12px;}
      th{background:#1d4ed8;color:#fff;text-align:left;}
      .num{text-align:right;}
      .title{text-align:center;color:#1d4ed8;font-size:22px;font-weight:700;margin:12px 0;}
      .muted{color:#64748b;font-size:12px;}
      .tot-bar{background:#0f172a;color:#fff;font-weight:700;}
      @media print{body{margin:0}}
    </style></head><body>${sheet.innerHTML}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => { w.print(); }, 250);
}

async function hopOpenPartyTxnDetail(txnId) {
  return hopOpenSaleDocPreview(txnId, 0);
}

/** Open Vyapar-style Preview from Sale/Purchase lists or Parties ledger. */
async function hopOpenSaleDocPreview(partyTxnId, sourceTxnId) {
  const pid = Number(partyTxnId || 0) || 0;
  const sid = Number(sourceTxnId || 0) || 0;
  if (!pid && !sid) {
    if (typeof nexoraToast === 'function') {
      nexoraToast('Preview not available for this row yet.', 'warn');
    } else {
      alert('Preview not available for this row yet.');
    }
    return;
  }
  hopClosePartyTxnDetail();
  const overlay = document.createElement('div');
  overlay.id = 'hop-party-txn-overlay';
  overlay.className = 'hop-doc-preview-overlay';
  const openListBtn = pid
    ? `<button type="button" class="nx-btn" onclick="hopOpenPartyTxnInModule(${pid})">Open in list</button>`
    : '';
  overlay.innerHTML = `
    <div class="hop-doc-preview-backdrop" onclick="hopClosePartyTxnDetail()"></div>
    <div class="hop-doc-preview-modal" role="dialog" aria-modal="true" aria-label="Preview">
      <div class="hop-doc-preview-head">
        <strong>Preview</strong>
        <button type="button" class="hop-doc-preview-x" onclick="hopClosePartyTxnDetail()" aria-label="Close">&times;</button>
      </div>
      <div class="hop-doc-preview-scroll" id="hop-doc-preview-body">
        <div class="hop-doc-preview-loading">Loading document…</div>
      </div>
      <div class="hop-doc-preview-foot">
        <button type="button" class="nx-btn" onclick="hopPrintPartyTxnPreview()">Print</button>
        ${openListBtn}
        <button type="button" class="nx-btn hop-doc-preview-close" onclick="hopClosePartyTxnDetail()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-open'));

  try {
    const url = pid
      ? `/api/v1/hop/party-transactions/${pid}/preview`
      : `/api/v1/hop/documents/preview?source_txn_id=${sid}`;
    const data = await hopApi(url);
    const body = document.getElementById('hop-doc-preview-body');
    if (body) body.innerHTML = hopRenderDocPreviewHtml(data);
  } catch (e) {
    const body = document.getElementById('hop-doc-preview-body');
    if (body) {
      body.innerHTML = `<div class="hop-doc-preview-loading">${foEscapeText(e?.message || 'Failed to load preview')}</div>`;
    }
  }
}

function hopRenderDocPreviewHtml(data) {
  if (!data) return '<div class="hop-doc-preview-loading">No data</div>';
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
      <div class="hop-doc-logo" aria-hidden="true">${foEscapeText((firm.name || 'HOP').slice(0, 1).toUpperCase())}</div>
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
      // Fallback when import missed Vyapar priceperunit.
      if (!(rate > 0) && qty > 0 && lineTotal > 0) {
        rate = (lineTotal - taxAmt + disc) / qty;
      }
      const taxCell = taxPct > 0
        ? `${hopPreviewMoney(taxAmt)} (${taxPct}%)`
        : hopPreviewMoney(taxAmt);
      return `<tr>
        <td>${i + 1}</td>
        <td>
          <div class="hop-doc-item-name">${foEscapeText(ln.item_name || 'Item')}</div>
          ${ln.description ? `<div class="hop-doc-muted">${foEscapeText(ln.description)}</div>` : ''}
        </td>
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
        <thead>
          <tr>
            <th>#</th><th>Item Name</th><th>HSN/SAC</th><th>Quantity</th>
            <th>Unit</th><th>Price/Unit</th><th>GST</th><th>Amount</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="num"><strong>Total</strong></td>
            <td class="num"><strong>${Number(totals.qty || 0).toLocaleString('en-IN')}</strong></td>
            <td></td><td></td>
            <td class="num"><strong>${hopPreviewMoney(totals.tax_total)}</strong></td>
            <td class="num"><strong>${hopPreviewMoney(totals.grand_total)}</strong></td>
          </tr>
        </tfoot>
      </table>`;
  } else {
    linesHtml = `
      <div class="hop-doc-missing">
        <strong>No item lines in this preview yet.</strong>
        <p>${foEscapeText(data.lines_missing_hint || 'Re-import Vyapar backup to load item details.')}</p>
        <p class="hop-doc-muted">Header total: <strong>${hopPreviewMoney(totals.grand_total || header.total_amount)}</strong>
        · Balance: <strong>${hopPreviewMoney(header.balance_amount)}</strong></p>
      </div>`;
  }

  const taxLabel = totals.tax_pct > 0 ? `Tax @ ${totals.tax_pct}%` : 'Tax';
  const notes = header.notes || '';
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
          <div class="hop-doc-tot-row"><span>${foEscapeText(taxLabel)}</span><strong>${hopPreviewMoney(totals.tax_total)}</strong></div>
          <div class="hop-doc-tot-row hop-doc-tot-grand"><span>Total</span><strong>${hopPreviewMoney(totals.grand_total)}</strong></div>
          <div class="hop-doc-sign">
            <div class="hop-doc-muted">For ${foEscapeText(firm.name || 'House of Prizm')}</div>
            <div class="hop-doc-sign-space"></div>
            <div>Authorized Signatory</div>
          </div>
        </div>
      </div>
    </div>`;
}

async function renderHopPartiesModule(mount) {
  let customers = [], vendors = [], partyTxns = [];
  try {
    const settled = await Promise.allSettled([
      hopApi('/api/v1/hop/customers'),
      hopApi('/api/v1/hop/vendors'),
      hopApi('/api/v1/hop/party-transactions'),
    ]);
    const pick = (i, fallback = []) => {
      const r = settled[i];
      if (r.status === 'fulfilled' && Array.isArray(r.value)) return r.value;
      if (r.status === 'fulfilled' && r.value == null) return fallback;
      return fallback;
    };
    customers = pick(0);
    vendors = pick(1);
    partyTxns = pick(2);
    const fatal = settled.slice(0, 2).every((r) => r.status === 'rejected');
    if (fatal) {
      const err = settled[0].status === 'rejected' ? settled[0].reason : settled[1].reason;
      throw err || new Error('Could not load parties');
    }
    hopState.customers = customers;
    hopState.vendors = vendors;
  } catch (e) {
    const msg = String(e?.message || e || 'Request failed');
    const hint = /502|503|error page/i.test(msg)
      ? `${msg} — Render server restart/deploy ho raha ho sakta hai. 30–60 sec wait karke Ctrl+Shift+R try karo.`
      : msg;
    mount.innerHTML = hopModuleShell('CRM', 'Parties', '', '', `<p class="nx-oc-error">${foEscapeText(hint)}</p>`);
    return;
  }

  const parties = [
    ...customers.map(c => ({ ...c, _type: 'customer', _balance: 0, _total_sale: 0 })),
    ...vendors.map(v => ({ ...v, _type: 'vendor', _balance: 0, _total_sale: 0 })),
  ];

  // Party Balance / Total Sale — Quotation & Proforma never count as sale.
  // Total Sale = Sale Invoice − Sale Return / Credit Note
  // Balance = receivable from money docs only (excludes Estimate/Proforma/SO/Challan).
  const balByParty = {};
  const saleByParty = {};
  for (const t of partyTxns) {
    const key = `${t.party_type}:${t.party_id}`;
    balByParty[key] = (balByParty[key] || 0) + hopPartyTxnBalanceContribution(t);
    saleByParty[key] = (saleByParty[key] || 0) + hopPartyTxnSaleContribution(t);
  }
  for (const p of parties) {
    const key = `${p._type}:${p.id}`;
    p._balance = balByParty[key] || 0;
    p._total_sale = saleByParty[key] || 0;
  }

  parties.sort((a, b) => (a.company || '').localeCompare(b.company || ''));

  hopState._parties = parties;
  hopState._partyTxns = partyTxns;
  hopState._partyFilter = hopState._partyFilter || '';
  // Keep previous selection if still present; otherwise auto-select first party.
  const prev = hopState._partySelected;
  const stillThere = prev && parties.some((p) => p._type === prev._type && Number(p.id) === Number(prev.id));
  hopState._partySelected = stillThere ? parties.find((p) => p._type === prev._type && Number(p.id) === Number(prev.id)) : (parties[0] || null);

  // True full-page side workspace (no CRM eyebrow shell).
  mount.innerHTML = `
    <div class="hop-view hop-view--fullpage hop-view--parties">
      <div class="pty-page">
        <div class="pty-topbar">
          <div class="pty-topbar-left">
            ${hopBackButtonHtml('Back')}
            <h2 class="pty-topbar-title">Parties</h2>
            <span class="pty-topbar-sub">${parties.length} contacts</span>
          </div>
          <div class="pty-topbar-actions">
            <button type="button" class="nx-btn nx-btn-primary" onclick="hopOpenAddPartyChooser()">+ Add Party</button>
          </div>
        </div>
        <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
        <div class="pty-layout" style="display:grid;grid-template-columns:minmax(280px,340px) minmax(0,1fr);flex:1;min-height:0;width:100%;height:100%;">
          <div class="pty-sidebar">
            <div class="pty-search-row">
              <input id="pty-search" class="pty-search" type="search" placeholder="Search Party Name" value="${foEscapeText(hopState._partyFilter)}" oninput="hopFilterParties(this.value)" />
            </div>
            <div class="pty-list-header">
              <span class="pty-lh-name">Party Name</span>
              <span class="pty-lh-amt">Balance</span>
            </div>
            <div id="pty-list" class="pty-list">
              ${_hopRenderPartyList(parties, hopState._partyFilter)}
            </div>
          </div>
          <div id="pty-detail" class="pty-detail" style="min-width:0;overflow:auto;">
            ${hopState._partySelected ? _hopRenderPartyDetail(hopState._partySelected, partyTxns) : _hopPartyEmptyDetail()}
          </div>
        </div>
      </div>
    </div>`;
  hopSetMainFullpage(true);
}

function _hopRenderPartyList(parties, filter) {
  const q = (filter || '').toLowerCase().trim();
  const filtered = q ? parties.filter(p => (p.company || '').toLowerCase().includes(q) || (p.contact_person || '').toLowerCase().includes(q) || String(p.customer_type || '').toLowerCase().includes(q)) : parties;
  if (!filtered.length) return '<p class="pty-empty-list">No parties found.</p>';
  return filtered.map(p => {
    const sel = hopState._partySelected && Number(hopState._partySelected.id) === Number(p.id) && hopState._partySelected._type === p._type;
    const bal = p._balance ? `₹ ${Number(p._balance).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '0.00';
    const group = String(p.customer_type || (p._type === 'vendor' ? 'Supplier' : 'Buyer')).trim() || (p._type === 'vendor' ? 'Supplier' : 'Buyer');
    const name = String(p.company || '—').trim();
    const initial = (name.charAt(0) || 'P').toUpperCase();
    const badgeClass = p._type === 'vendor' ? 'pty-badge-v' : 'pty-badge-c';
    return `<button type="button" class="pty-item${sel ? ' is-active' : ''}"
      onclick="hopSelectParty('${p._type}', ${p.id})"
      ondblclick="hopEditContact('${p._type === 'vendor' ? 'vendors' : 'customers'}', ${p.id})"
      title="${foEscapeAttr(group)} · Double-click to edit">
      <span class="pty-badge ${badgeClass}" title="${foEscapeAttr(group)}">${foEscapeText(initial)}</span>
      <span class="pty-item-main">
        <span class="pty-item-name">${foEscapeText(name)}</span>
      </span>
      <span class="pty-item-bal${p._balance > 0 ? ' is-due' : ''}">${bal}</span>
    </button>`;
  }).join('');
}

function _hopPartyEmptyDetail() {
  return `<div class="pty-no-selection">
    <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" style="opacity:.1"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4Zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4Z"/></svg>
    <p>Select a party to view details</p>
  </div>`;
}

function _hopRenderPartyDetail(party, partyTxns) {
  const isVendor = party._type === 'vendor';
  const partyGroup = String(
    party.customer_type || party.party_group || (isVendor ? 'Supplier' : 'Buyer')
  ).trim() || (isVendor ? 'Supplier' : 'Buyer');
  const address = party.address || '';
  const shipping = party.shipping_address || '';
  const mobile = party.mobile || '';
  const email = party.email || '';
  const gst = party.gst_no || '';
  const city = party.city || '';
  const state = party.state || '';
  const contactPerson = party.contact_person || '';
  const place = [city, state].filter(Boolean).join(', ');

  const partyRows = (partyTxns || [])
    .filter((t) => t.party_type === party._type && Number(t.party_id) === Number(party.id))
    .sort((a, b) => String(b.txn_date || '').localeCompare(String(a.txn_date || '')));
  const primaryRows = partyRows.filter((r) => hopTxnIsPrimaryPartyDoc(r));
  const otherRows = partyRows.filter((r) => !hopTxnIsPrimaryPartyDoc(r));

  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);
  const typeKey = isVendor ? 'vendors' : 'customers';

  return `
    <div class="pty-detail-header">
      <div class="pty-detail-info">
        <div class="pty-detail-title-row">
          <div class="pty-detail-heading">
            <h3 class="pty-detail-name">${foEscapeText(party.company || '—')}</h3>
            <span class="pty-detail-type" title="Party Group">${foEscapeText(partyGroup)}</span>
          </div>
          <div class="pty-detail-actions">
            ${callHref ? `<a class="pty-action-btn" href="${callHref}" title="Call">${hopContactIcon('call')}</a>` : ''}
            ${waHref ? `<a class="pty-action-btn pty-action-wa" href="${waHref}" target="_blank" title="WhatsApp">${hopContactIcon('whatsapp')}</a>` : ''}
            <button type="button" class="nx-btn nx-btn-primary pty-detail-edit" onclick="hopEditContact('${typeKey}', ${party.id})">${hopContactIcon('edit')} Edit</button>
            <button type="button" class="nx-btn hop-contact-icon-del pty-detail-del" onclick="hopDeleteContact('${typeKey}', ${party.id}, '${foEscapeAttr(party.company || '')}')">${hopContactIcon('delete')} Delete</button>
          </div>
        </div>

        <div class="pty-detail-meta">
          ${gst ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('gst')}</span><span class="pty-meta-text"><em>GSTIN</em> ${foEscapeText(gst)}</span></div>` : ''}
          ${contactPerson ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('edit')}</span><span class="pty-meta-text"><em>Contact</em> ${foEscapeText(contactPerson)}</span></div>` : ''}
          ${address ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('pin')}</span><span class="pty-meta-text"><em>Billing</em> ${foEscapeText(address)}</span></div>` : ''}
          ${shipping && shipping !== address ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('pin')}</span><span class="pty-meta-text"><em>Shipping</em> ${foEscapeText(shipping)}</span></div>` : ''}
        </div>

        <div class="pty-detail-contact">
          ${mobile ? `<${callHref ? `a href="${callHref}"` : 'span'} class="pty-chip pty-chip-phone">${hopContactIcon('call')}<span>${foEscapeText(mobile)}</span></${callHref ? 'a' : 'span'}>` : ''}
          ${email ? `<a class="pty-chip pty-chip-mail" href="mailto:${foEscapeAttr(email)}">${hopContactIcon('email')}<span>${foEscapeText(email)}</span></a>` : ''}
          ${place ? `<span class="pty-chip pty-chip-place">${hopContactIcon('pin')}<span>${foEscapeText(place)}</span></span>` : ''}
        </div>
      </div>
    </div>

    <div class="pty-txn-section">
      <div class="pty-txn-header">
        <strong>Transactions</strong>
        <span class="nx-text-dim">${primaryRows.length} sale docs${otherRows.length ? ` · ${otherRows.length} other` : ''}</span>
        <span class="pty-total-sale" title="Sale Invoice / Sale Bill − Credit Note / Sale Return. Excludes Quotation, Proforma, Orders, Cancelled &amp; Approved drafts.">
          Total Sale <em>${hopMoney(hopComputePartyTotalSale(partyRows))}</em>
        </span>
      </div>
      ${primaryRows.length
        ? hopPartyTxnTableHtml(primaryRows)
        : '<p class="pty-no-txn">No sale invoices / payments for this party yet.</p>'}
      ${otherRows.length ? `
        <details class="pty-txn-other">
          <summary>Other documents <span>${otherRows.length}</span></summary>
          <p class="pty-txn-other-hint">Journal is history only (Vyapar already marks Sale as Paid). Not re-counted in Balance. Proforma / Orders / Cancelled — not in Total Sale.</p>
          ${hopPartyTxnTableHtml(otherRows)}
        </details>
      ` : ''}
    </div>`;
}

function hopSelectParty(type, id) {
  const parties = hopState._parties || [];
  const party = parties.find(p => p._type === type && Number(p.id) === Number(id));
  if (!party) return;
  hopState._partySelected = party;
  const list = document.getElementById('pty-list');
  if (list) list.innerHTML = _hopRenderPartyList(hopState._parties || [], hopState._partyFilter || '');
  // Render detail pane (desktop split)
  const detail = document.getElementById('pty-detail');
  if (detail) detail.innerHTML = _hopRenderPartyDetail(party, hopState._partyTxns || []);
  // Mobile / narrow: open full contact card overlay so Edit / Delete / details are usable.
  if (hopIsMobileView()) {
    hopOpenContactDetail(type === 'vendor' ? 'vendors' : 'customers', party.id);
  }
}

function hopFilterParties(q) {
  hopState._partyFilter = q || '';
  const list = document.getElementById('pty-list');
  if (list) list.innerHTML = _hopRenderPartyList(hopState._parties || [], q);
}

/* ---------- Customers ---------- */
async function renderHopCustomersModule(mount) {
  const q = hopState.search.customers || '';
  let rows = [];
  try { rows = await hopApi(`/api/v1/hop/customers?q=${encodeURIComponent(q)}`) || []; hopState.customers = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('CRM', 'Customers', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div class="hop-toolbar">
      <input id="hop-q" class="hop-search" type="search" value="${foEscapeText(q)}" placeholder="Search company, contact, city…" oninput="hopFilterModule('customers')" />
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopIsMobileView()
      ? hopRenderMobileContactCards(rows, 'customers')
      : `${hopRenderDesktopContactToolbar('customers', rows.length)}
         ${hopTable(
        ['', 'Company', 'Contact', 'Mobile', 'City', 'Type', 'Hotel', 'Architect', 'Consultant', 'Potential', 'Rating', 'Status', 'Assigned', ''],
        rows.map((r) => {
          const state = hopContactSelectState('customers');
          const checked = state.ids.includes(Number(r.id)) ? ' checked' : '';
          return `<tr class="hop-clickable-row" onclick="hopOpenContactDetail('customers', ${r.id})" style="cursor:pointer">
          <td onclick="event.stopPropagation()"><input type="checkbox" class="hop-desk-check" value="${r.id}"${checked} onchange="hopToggleContactSelected('customers', ${r.id}, this.checked)" /></td>
          <td>${hopCell(r.company)}</td><td>${hopCell(r.contact_person)}</td><td>${hopCell(r.mobile)}</td>
          <td>${hopCell(r.city)}</td><td>${hopCell(r.customer_type)}</td><td>${hopCell(r.hotel_brand)}</td>
          <td>${hopCell(r.architect)}</td><td>${hopCell(r.consultant)}</td><td>${hopCell(r.annual_potential)}</td>
          <td>${hopCell(r.potential_rating)}</td><td>${hopCell(r.status)}</td><td>${hopCell(r.assigned_to)}</td>
          <td><button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('customers', ${r.id}, '${foEscapeAttr(hopContactLabel(r))}')" title="Delete">${hopContactIcon('delete')}</button></td>
        </tr>`}).join(''),
      )}`}`;
  mount.innerHTML = hopModuleShell('CRM', 'Customers', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopOpenAddPartyChooser()">+ Add Party</button>`, body);
  if (hopIsMobileView()) requestAnimationFrame(() => hopBindMobileContactCards('customers'));
}

/* ---------- Visiting Card Reader ---------- */

async function renderHopWipeDataModule(mount) {
  const body = `
    <div class="nx-card" style="max-width:560px;padding:22px 24px;">
      <p class="nx-text-dim" style="margin:0 0 14px;line-height:1.5">
        This permanently deletes all House of Prizm business data:
        parties, invoices, payments, products, quotations, party transactions, and related records.
      </p>
      <p class="nx-text-dim" style="margin:0 0 18px;line-height:1.5">
        Login users are kept. This cannot be undone.
      </p>
      <label class="hop-vcard-field" style="margin-bottom:14px">
        <span>Confirm by typing <strong>WIPE</strong></span>
        <input id="hop-wipe-confirm" type="text" autocomplete="off" placeholder="WIPE" />
      </label>
      <label class="hop-vcard-field" style="margin-bottom:18px;opacity:.85">
        <span>Password <em style="font-style:normal;color:#64748b">(optional for now — will be required later)</em></span>
        <input id="hop-wipe-password" type="password" autocomplete="new-password" placeholder="Leave blank for now" />
      </label>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button type="button" class="nx-btn" style="border-color:rgba(248,113,113,.45);color:#f87171" onclick="hopRunWipeData()">Wipe all data</button>
        <button type="button" class="nx-btn" onclick="openHopView('dashboard')">Cancel</button>
      </div>
      <p id="hop-wipe-status" class="nx-text-dim" style="margin:14px 0 0;font-size:.78rem"></p>
    </div>`;
  mount.innerHTML = hopModuleShell('Settings', 'Wipe all data', '', '', body);
}

async function hopRunWipeData() {
  const confirmEl = document.getElementById('hop-wipe-confirm');
  const statusEl = document.getElementById('hop-wipe-status');
  const password = document.getElementById('hop-wipe-password')?.value || '';
  const typed = String(confirmEl?.value || '').trim();
  if (typed !== 'WIPE') {
    if (statusEl) statusEl.textContent = 'Type WIPE exactly to confirm.';
    confirmEl?.focus();
    return;
  }
  if (!(await nexoraConfirm('Delete ALL business data now? This cannot be undone.', {
    title: 'Wipe all data',
    danger: true,
    okText: 'Wipe now',
  }))) return;
  if (statusEl) statusEl.textContent = 'Wiping…';
  try {
    const data = await hopApi('/api/v1/hop/settings/wipe-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, confirm: 'WIPE' }),
    });
    // Clear client caches so UI does not show stale rows.
    hopState.customers = [];
    hopState.vendors = [];
    hopState.invoices = [];
    hopState.quotations = [];
    hopState.orders = [];
    hopState._parties = [];
    hopState._partyTxns = [];
    hopState._partySelected = null;
    hopState.vyaparImportPreview = null;
    hopState.vyaparBackupFile = null;
    if (statusEl) {
      statusEl.textContent = `Done. Cleared ${data?.tables_cleared || 0} tables (${data?.rows_deleted || 0} rows).`;
    }
    alert(`Data wiped.\nTables: ${data?.tables_cleared || 0}\nRows deleted: ${data?.rows_deleted || 0}`);
    openHopView('parties');
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message || 'Wipe failed';
    alert(e.message || 'Wipe failed');
  }
}

async function renderHopVyaparImportModule(mount) {
  const preview = hopState.vyaparImportPreview;
  const src = preview?.source || {};
  const det = preview?.detected || {};
  const hasPreview = !!preview;

  const sizeKB = src.sqlite_bytes ? (src.sqlite_bytes / 1024).toFixed(0) : '—';
  const salesCount = (det.txn_type_split || {})['1'] || 0;
  const purchaseCount = (det.txn_type_split || {})['2'] || 0;
  const paymentInCount = (det.txn_type_split || {})['3'] || 0;
  const paymentOutCount = (det.txn_type_split || {})['4'] || 0;

  const body = `
    <div class="vyp-page">
      <input id="hop-vyapar-file" class="hop-file-hidden" type="file" accept=".vyb,.vyp,application/octet-stream" />

      <div class="vyp-row">
        <div class="vyp-col vyp-col-upload">
          <div class="vyp-drop-zone" onclick="hopPickVyaparBackup()">
            <svg viewBox="0 0 48 48" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 30V16a10 10 0 0 1 20 0v14"/><path d="M10 28a6 6 0 0 0 0 12h28a6 6 0 0 0 0-12"/><path d="M24 20v14m-5-5 5 5 5-5"/></svg>
            <p class="vyp-drop-label">Drop <strong>.vyb</strong> / <strong>.vyp</strong> here or click to browse</p>
            <p id="hop-vyapar-status" class="vyp-drop-file">${hasPreview ? foEscapeText((hopState.vyaparBackupFile?.name) || src.filename || '') : 'No file selected'}</p>
          </div>
          <div class="vyp-btn-row">
            <button type="button" class="vyp-btn vyp-btn-scan" onclick="hopPreviewVyaparBackup()">Preview</button>
            <button type="button" class="vyp-btn vyp-btn-import${hasPreview ? '' : ' is-disabled'}" onclick="hopRunVyaparImport()" ${hasPreview ? '' : 'disabled'}>Import</button>
          </div>
        </div>

        <div class="vyp-col vyp-col-preview">
          ${hasPreview ? `
            <div class="vyp-firm-row">
              <strong>${foEscapeText(src.firm_name || '—')}</strong>
              <span>${foEscapeText(src.tables || 0)} tables · ${sizeKB} KB</span>
            </div>
            <div class="vyp-stats-row">
              <div class="vyp-s"><span>${foEscapeText(det.parties_total || 0)}</span><small>Parties</small></div>
              <div class="vyp-s"><span>${foEscapeText(det.items_total || 0)}</span><small>Items</small></div>
              <div class="vyp-s"><span>${foEscapeText(det.transactions_total || 0)}</span><small>Txns</small></div>
            </div>
            <div class="vyp-tags">
              <span class="vyp-t vyp-t-s">${salesCount} Sales</span>
              <span class="vyp-t vyp-t-p">${purchaseCount} Purchase</span>
              <span class="vyp-t vyp-t-y">${paymentInCount} Pay-In · ${paymentOutCount} Pay-Out</span>
            </div>
            <p class="vyp-hint">Duplicates auto-skipped. Existing blank fields get updated.</p>
          ` : `
            <div class="vyp-empty">
              <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" style="opacity:.15"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm4 18H6V4h7v5h5v11Z"/></svg>
              <p>Select a backup file and click <strong>Preview</strong></p>
            </div>
          `}
        </div>
      </div>
    </div>`;

  mount.innerHTML = hopModuleShell(
    'Migration',
    'Vyapar Import',
    'Convert & import backup into House of Prizm',
    `<button type="button" class="nx-btn" onclick="openHopView('customers')">← Back</button>`,
    body,
  );

  const input = document.getElementById('hop-vyapar-file');
  input?.addEventListener('change', () => {
    const file = input.files && input.files[0];
    if (!file) return;
    hopState.vyaparBackupFile = file;
    const status = document.getElementById('hop-vyapar-status');
    if (status) { status.textContent = file.name; status.classList.add('is-active'); }
    document.querySelector('.vyp-btn-import')?.removeAttribute('disabled');
    document.querySelector('.vyp-btn-import')?.classList.remove('is-disabled');
  });
}

function _vypShowLoader(msg) {
  _vypHideLoader();
  const el = document.createElement('div');
  el.id = 'vyp-loader-overlay';
  el.className = 'vyp-loader';
  el.innerHTML = `
    <div class="vyp-loader-card">
      <div class="vyp-spinner"></div>
      <p class="vyp-loader-msg">${foEscapeText(msg)}</p>
    </div>`;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add('is-open'));
}
function _vypHideLoader() {
  const el = document.getElementById('vyp-loader-overlay');
  if (el) { el.classList.remove('is-open'); setTimeout(() => el.remove(), 200); }
}
function _vypResultDialog(success, title, lines) {
  _vypHideLoader();
  const el = document.createElement('div');
  el.id = 'vyp-result-overlay';
  el.className = 'vyp-result';
  el.innerHTML = `
    <div class="vyp-result-backdrop" onclick="document.getElementById('vyp-result-overlay')?.remove()"></div>
    <div class="vyp-result-card">
      <div class="vyp-result-icon ${success ? 'is-ok' : 'is-fail'}">
        ${success
          ? '<svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2Z"/></svg>'
          : '<svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm1 15h-2v-2h2v2Zm0-4h-2V7h2v6Z"/></svg>'
        }
      </div>
      <h4 class="vyp-result-title">${foEscapeText(title)}</h4>
      <div class="vyp-result-body">${lines.map(l => `<p>${l}</p>`).join('')}</div>
      <button type="button" class="vyp-btn vyp-btn-import" onclick="document.getElementById('vyp-result-overlay')?.remove()" style="margin-top:12px;width:100%">OK</button>
    </div>`;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add('is-open'));
}

async function hopPreviewVyaparBackup() {
  const file = hopState.vyaparBackupFile;
  if (!file) {
    _vypResultDialog(false, 'No file selected', ['Please select a .vyb or .vyp backup file first.']);
    return;
  }
  const fd = new FormData();
  fd.append('backup_file', file, file.name || 'backup.vyb');
  _vypShowLoader('Scanning backup…');
  try {
    const data = await hopApi('/api/v1/hop/vyapar-import/preview', { method: 'POST', body: fd });
    hopState.vyaparImportPreview = data;
    _vypHideLoader();
    openHopView('vyapar_import');
  } catch (e) {
    _vypResultDialog(false, 'Preview Failed', [e.message || 'Could not read the backup file.']);
  }
}

async function hopRunVyaparImport() {
  const file = hopState.vyaparBackupFile;
  if (!file) {
    _vypResultDialog(false, 'No file selected', ['Please select a .vyb or .vyp backup file first.']);
    return;
  }
  if (!(await nexoraConfirm(
    'Import / refresh from this Vyapar backup?\n\nSafe re-import: existing parties, invoices & txns update in place — duplicates nahi banenge. Sirf nayi Vyapar rows add hongi.',
    {
    title: 'Confirm Import',
    danger: true,
    okText: 'Import',
  }))) return;
  const fd = new FormData();
  fd.append('backup_file', file, file.name || 'backup.vyb');
  _vypShowLoader('Importing data…');
  try {
    const data = await hopApi('/api/v1/hop/vyapar-import/apply', { method: 'POST', body: fd });
    const lines = [
      `<span class="vyp-r-label">Customers</span> <strong>${data.customers_created}</strong> created, ${data.customers_skipped} skipped`,
      `<span class="vyp-r-label">Vendors</span> <strong>${data.vendors_created}</strong> created, ${data.vendors_skipped} skipped`,
      `<span class="vyp-r-label">Products</span> <strong>${data.products_created}</strong> created, ${data.products_skipped} skipped`,
      `<span class="vyp-r-label">Invoices</span> <strong>${data.invoices_created || 0}</strong> created, ${data.invoices_updated || 0} updated`,
      `<span class="vyp-r-label">Payments</span> <strong>${data.payments_created || 0}</strong> created, ${data.payments_skipped || 0} skipped`,
      `<span class="vyp-r-label">All Txns</span> <strong>${data.party_txns_created || 0}</strong> imported, ${data.party_txns_skipped || 0} refreshed`,
      `<span class="vyp-r-label">Fuzzy party match</span> <strong>${data.party_fuzzy_matched || 0}</strong> linked (no duplicate)`,
    ];
    if (data.errors && data.errors.length) {
      lines.push(`<span class="vyp-r-warn">${data.errors.length} error(s) during import</span>`);
    }
    _vypResultDialog(true, 'Import Successful', lines);
    hopState.customers = [];
    hopState.vendors = [];
  } catch (e) {
    _vypResultDialog(false, 'Import Failed', [e.message || 'Something went wrong during import.']);
  }
}

function hopPickVyaparBackup() {
  const input = document.getElementById('hop-vyapar-file');
  if (input) input.click();
}

/* ---------- Visiting Card Reader ---------- */
async function renderHopVisitingCardModule(mount) {
  const body = `
    <div class="hop-vcard-page">
      <div class="hop-vcard-layout">
        <section class="hop-vcard-panel hop-vcard-capture">
          <div class="hop-vcard-panel-head">
            <h3>Capture</h3>
            <span class="hop-vcard-hint">Camera or file · auto-reads on upload</span>
          </div>
          <div class="hop-vcard-source">
            <button type="button" class="hop-vcard-src-btn hop-vcard-src-btn--primary" onclick="hopPickPhoto('hop-vcard-cam')">
              <span class="hop-vcard-src-ico" aria-hidden="true"></span>
              Camera
            </button>
            <button type="button" class="hop-vcard-src-btn" onclick="hopPickPhoto('hop-vcard-gal')">
              <span class="hop-vcard-src-ico hop-vcard-src-ico--file" aria-hidden="true"></span>
              Gallery
            </button>
          </div>
          <input id="hop-vcard-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
          <input id="hop-vcard-gal" class="hop-file-hidden" type="file" accept="image/*" />
          <div id="hop-vcard-preview" class="hop-vcard-stage is-empty">
            <div class="hop-vcard-empty">
              <strong>Drop visiting card here</strong>
              <span>Clear photo · good light · flat card works best</span>
            </div>
          </div>
          <div class="hop-vcard-capture-foot">
            <button type="button" class="nx-btn nx-btn-primary" id="hop-vcard-scan-btn" onclick="hopScanVisitingCard()">Read card</button>
            <p id="hop-vcard-status" class="hop-vcard-status">Waiting for photo</p>
          </div>
        </section>
        <section id="hop-vcard-form" class="hop-vcard-panel hop-vcard-review is-idle">
          <div class="hop-vcard-idle">
            <strong>Review</strong>
            <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
          </div>
        </section>
      </div>
    </div>
  `;
  mount.innerHTML = hopModuleShell(
    'CRM',
    'Visiting Card',
    '',
    `<button type="button" class="nx-btn" onclick="openHopView('customers')">← Customers</button>`,
    body,
  );

  const setPreview = (file) => {
    const box = document.getElementById('hop-vcard-preview');
    if (!file || !box) return;
    hopState.visitingCardFile = file;
    box.classList.remove('is-empty');
    box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
    const status = document.getElementById('hop-vcard-status');
    if (status) {
      status.textContent = 'Photo ready — reading…';
      status.classList.add('is-busy');
      status.classList.remove('is-error', 'is-ok');
    }
    const form = document.getElementById('hop-vcard-form');
    if (form) {
      form.classList.add('is-idle');
      form.classList.remove('is-ready');
      form.innerHTML = `
        <div class="hop-vcard-idle">
          <strong>Review</strong>
          <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
        </div>`;
    }
    hopScheduleVisitingCardScan();
  };

  const bindPreview = (inputId) => {
    const input = document.getElementById(inputId);
    input?.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const otherId = inputId === 'hop-vcard-cam' ? 'hop-vcard-gal' : 'hop-vcard-cam';
      const other = document.getElementById(otherId);
      if (other) other.value = '';
      setPreview(file);
    });
  };
  bindPreview('hop-vcard-cam');
  bindPreview('hop-vcard-gal');
}

function hopScheduleVisitingCardScan() {
  window.clearTimeout(hopState._vcardScanTimer);
  hopState._vcardScanTimer = window.setTimeout(() => hopScanVisitingCard(), 450);
}

function hopIsLikelyMobileDevice() {
  const ua = navigator.userAgent || '';
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
  if (navigator.maxTouchPoints > 2 && Math.min(window.screen.width, window.screen.height) < 920) {
    return true;
  }
  return false;
}

function hopCloseWebcamCapture() {
  const overlay = document.getElementById('hop-webcam-overlay');
  const stream = hopState._webcamStream;
  if (stream) {
    try {
      stream.getTracks().forEach((t) => t.stop());
    } catch (_) { /* ignore */ }
    hopState._webcamStream = null;
  }
  if (overlay) overlay.remove();
}

/**
 * Cross-platform photo pick:
 * - Phone / Android app: native camera or gallery via <input capture>
 * - Desktop browser / desktop app: live webcam modal (getUserMedia), else file picker
 */
function hopPickPhoto(inputId) {
  const el = document.getElementById(inputId);
  if (!el) return;
  const wantsCamera = inputId.includes('-cam') || el.hasAttribute('capture');

  if (
    wantsCamera
    && !hopIsLikelyMobileDevice()
    && navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
  ) {
    hopOpenWebcamCapture(
      (file) => hopApplyCapturedFile(inputId, el, file),
      () => {
        // Webcam blocked — fall back to file chooser (same as Gallery/Files)
        try { el.removeAttribute('capture'); } catch (_) { /* ignore */ }
        el.click();
      },
    );
    return;
  }
  el.click();
}

function hopApplyCapturedFile(inputId, inputEl, file) {
  if (!file) return;
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    if (inputEl) inputEl.files = dt.files;
  } catch (_) { /* Safari / some WebViews block DataTransfer */ }

  if (inputId.startsWith('hop-vcard')) {
    hopState.visitingCardFile = file;
    const box = document.getElementById('hop-vcard-preview');
    if (box) {
      box.classList.remove('is-empty');
      box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
    }
    const status = document.getElementById('hop-vcard-status');
    if (status) {
      status.textContent = 'Photo ready — reading…';
      status.classList.add('is-busy');
      status.classList.remove('is-error', 'is-ok');
    }
    const form = document.getElementById('hop-vcard-form');
    if (form) {
      form.classList.add('is-idle');
      form.classList.remove('is-ready');
      form.innerHTML = `
        <div class="hop-vcard-idle">
          <strong>Review</strong>
          <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
        </div>`;
    }
    hopScheduleVisitingCardScan();
    return;
  }

  hopState.fabricPreview = hopState.fabricPreview || {};
  if (inputId.includes('fabric-item')) {
    hopState.fabricPreview.itemFile = file;
    const box = document.getElementById('hop-fabric-item-preview');
    if (box) box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Preview" />`;
  } else if (inputId.includes('fabric-swatch')) {
    hopState.fabricPreview.fabricFile = file;
    const box = document.getElementById('hop-fabric-swatch-preview');
    if (box) box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Preview" />`;
  }

  try {
    inputEl?.dispatchEvent(new Event('change', { bubbles: true }));
  } catch (_) { /* ignore */ }
}

function hopOpenWebcamCapture(onCapture, onFallback) {
  hopCloseWebcamCapture();
  const overlay = document.createElement('div');
  overlay.id = 'hop-webcam-overlay';
  overlay.className = 'hop-webcam-overlay';
  overlay.innerHTML = `
    <div class="hop-webcam-dialog" role="dialog" aria-modal="true" aria-label="Camera">
      <header class="hop-webcam-header">
        <strong>Camera</strong>
        <span class="nx-text-dim">Works on web &amp; desktop — allow camera access if asked</span>
      </header>
      <video id="hop-webcam-video" class="hop-webcam-video" playsinline autoplay muted></video>
      <p id="hop-webcam-error" class="nx-oc-error hidden"></p>
      <div class="hop-webcam-actions">
        <button type="button" class="nx-btn nx-btn-primary" id="hop-webcam-snap">Capture</button>
        <button type="button" class="nx-btn" id="hop-webcam-files">Use file instead</button>
        <button type="button" class="nx-btn" id="hop-webcam-cancel">Cancel</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const video = document.getElementById('hop-webcam-video');
  const errEl = document.getElementById('hop-webcam-error');
  const finishFallback = () => {
    hopCloseWebcamCapture();
    if (typeof onFallback === 'function') onFallback();
  };

  document.getElementById('hop-webcam-cancel')?.addEventListener('click', () => hopCloseWebcamCapture());
  document.getElementById('hop-webcam-files')?.addEventListener('click', finishFallback);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) hopCloseWebcamCapture();
  });

  document.getElementById('hop-webcam-snap')?.addEventListener('click', () => {
    if (!video || !video.videoWidth) {
      if (errEl) {
        errEl.textContent = 'Camera not ready yet — wait a second or use file instead.';
        errEl.classList.remove('hidden');
      }
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) {
        finishFallback();
        return;
      }
      const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
      hopCloseWebcamCapture();
      if (typeof onCapture === 'function') onCapture(file);
    }, 'image/jpeg', 0.92);
  });

  navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    audio: false,
  }).then((stream) => {
    hopState._webcamStream = stream;
    if (video) {
      video.srcObject = stream;
      video.play?.().catch(() => {});
    }
  }).catch((err) => {
    if (errEl) {
      errEl.textContent = (err && err.message) ? err.message : 'Camera permission denied';
      errEl.classList.remove('hidden');
    }
    // Auto-offer file fallback after a beat
    setTimeout(finishFallback, 600);
  });
}

async function hopScanVisitingCard() {
  const status = document.getElementById('hop-vcard-status');
  const btn = document.getElementById('hop-vcard-scan-btn');
  const formWrap = document.getElementById('hop-vcard-form');
  const file = hopState.visitingCardFile
    || document.getElementById('hop-vcard-cam')?.files?.[0]
    || document.getElementById('hop-vcard-gal')?.files?.[0];
  if (!file) {
    if (status) {
      status.textContent = 'Add a card photo first.';
      status.classList.add('is-error');
      status.classList.remove('is-busy', 'is-ok');
    }
    return;
  }
  if (status) {
    status.textContent = 'Reading card…';
    status.classList.add('is-busy');
    status.classList.remove('is-error', 'is-ok');
  }
  if (btn) btn.disabled = true;
  try {
    const fd = new FormData();
    fd.append('card_image', file, file.name || 'visiting_card.jpg');
    const response = await fetchWithAuth('/api/v1/hop/customers/scan-card', { method: 'POST', body: fd });
    let data;
    try {
      data = await parseApiJson(response);
    } catch (parseErr) {
      if (response.status === 502 || response.status === 503) {
        throw new Error(
          'Server crash / busy (502). Render pe GEMINI_API_KEY set karo, 1 min wait karke dubara try karo.',
        );
      }
      throw new Error(
        response.status === 401
          ? 'Session expire ho gayi — dubara login karo.'
          : (parseErr.message || 'Server ne JSON ki jagah error page bheja. Hard refresh karo.'),
      );
    }
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Card scan failed'));
    }
    const payload = data.data || {};
    const f = payload.fields || {};
    hopState.visitingCardDraft = f;
    if (hopState.vcardMode === 'party') {
      if (status) {
        status.textContent = 'Card read — opening form…';
        status.classList.remove('is-busy', 'is-error');
        status.classList.add('is-ok');
      }
      hopClosePartyScanModal();
      hopOpenPartyEditModal('customer', hopPartyRowFromCardFields(f));
      return;
    }
    const preview = (payload.raw_text_preview || '').trim();
    if (status) {
      let msg = payload.note || 'Verify fields, then save';
      if (payload.engine) msg += ` · ${payload.engine}`;
      if (payload.confidence) msg += ` · ${payload.confidence}`;
      if (!payload.gemini_configured) msg += ' · Tip: GEMINI_API_KEY for best accuracy';
      if (preview && (payload.confidence === 'low' || !f.company)) {
        msg += ` · OCR: ${preview.slice(0, 90)}${preview.length > 90 ? '…' : ''}`;
      }
      status.textContent = msg;
      status.classList.remove('is-busy', 'is-error');
      status.classList.add('is-ok');
    }
    if (formWrap) {
      formWrap.classList.remove('is-idle', 'hidden');
      formWrap.classList.add('is-ready');
      formWrap.innerHTML = `
        <div class="hop-vcard-panel-head">
          <h3>Review &amp; save</h3>
          <span class="hop-vcard-hint">Edit before save · nothing is stored until Save</span>
        </div>
        <div class="hop-vcard-form-grid">
          <label class="hop-vcard-field hop-vcard-span2"><span>Company *</span><input id="vc-company" value="${foEscapeAttr(f.company || '')}" placeholder="Company name" /></label>
          <label class="hop-vcard-field"><span>Contact</span><input id="vc-contact" value="${foEscapeAttr(f.contact_person || '')}" placeholder="Contact person" /></label>
          <label class="hop-vcard-field"><span>Mobile</span><input id="vc-mobile" value="${foEscapeAttr(f.mobile || '')}" placeholder="Mobile" /></label>
          <label class="hop-vcard-field"><span>Email</span><input id="vc-email" value="${foEscapeAttr(f.email || '')}" placeholder="Email" /></label>
          <label class="hop-vcard-field"><span>City</span><input id="vc-city" value="${foEscapeAttr(f.city || '')}" placeholder="City" /></label>
          <label class="hop-vcard-field"><span>Type</span><input id="vc-type" value="${foEscapeAttr(f.customer_type || '')}" placeholder="Hotel / Designer / …" /></label>
          <label class="hop-vcard-field"><span>GSTIN</span><input id="vc-gst" value="${foEscapeAttr(f.gst_no || '')}" placeholder="GSTIN" /></label>
          <label class="hop-vcard-field"><span>PAN</span><input id="vc-pan" value="${foEscapeAttr(f.pan || '')}" placeholder="PAN" /></label>
          <label class="hop-vcard-field hop-vcard-span2"><span>Address</span><input id="vc-address" value="${foEscapeAttr(f.address || '')}" placeholder="Address" /></label>
          <label class="hop-vcard-field hop-vcard-span2"><span>Remarks</span><input id="vc-remarks" value="${foEscapeAttr(f.remarks || '')}" placeholder="Remarks" /></label>
        </div>
        <div class="hop-vcard-form-actions">
          <button type="button" class="nx-btn" onclick="hopResetVisitingCardReview()">Discard</button>
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveVisitingCardCustomer()">Save as customer</button>
        </div>`;
      requestAnimationFrame(() => hopScrollIntoMain(formWrap, 8));
    }
  } catch (e) {
    if (status) {
      status.textContent = e.message || 'Card scan failed';
      status.classList.add('is-error');
      status.classList.remove('is-busy', 'is-ok');
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function hopResetVisitingCardReview() {
  const form = document.getElementById('hop-vcard-form');
  if (!form) return;
  form.classList.add('is-idle');
  form.classList.remove('is-ready');
  form.innerHTML = `
    <div class="hop-vcard-idle">
      <strong>Review</strong>
      <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
    </div>`;
}

function foEscapeAttr(value) {
  return foEscapeText(value).replace(/"/g, '&quot;');
}

async function hopSaveVisitingCardCustomer() {
  const company = document.getElementById('vc-company')?.value?.trim();
  if (!company) {
    alert('Company name required — card se miss hua ho to type karo.');
    return;
  }
  const payload = {
    company,
    contact_person: document.getElementById('vc-contact')?.value,
    mobile: document.getElementById('vc-mobile')?.value,
    email: document.getElementById('vc-email')?.value,
    city: document.getElementById('vc-city')?.value,
    customer_type: document.getElementById('vc-type')?.value,
    gst_no: document.getElementById('vc-gst')?.value,
    pan: document.getElementById('vc-pan')?.value,
    address: document.getElementById('vc-address')?.value,
    remarks: document.getElementById('vc-remarks')?.value,
    source: 'visiting_card',
  };
  try {
    await hopApi('/api/v1/hop/customers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    alert('Party saved.');
    openHopView('parties');
  } catch (e) {
    alert(e.message || 'Save failed');
  }
}

/* ---------- Projects ---------- */
async function renderHopProjectsModule(mount) {
  const q = hopState.search.projects || '';
  let rows = [];
  try { rows = await hopApi(`/api/v1/hop/projects?q=${encodeURIComponent(q)}`) || []; hopState.projects = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Projects', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Projects', value: rows.length, tone: 'unpaid' },
    ])}
    ${hopTable(
      ['Project', 'Customer', 'Hotel', 'Stage', 'Value', 'Prob %', 'Completion %', 'Next Milestone', 'Assigned', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.customer_company || r.client_name)}</td>
        <td>${hopCell(r.hotel_name)}</td><td><span class="hop-stage-pill">${hopCell(r.stage)}</span></td>
        <td class="inv-num">${hopMoney(r.project_value ?? r.expected_value)}</td><td>${hopCell(r.probability_pct)}</td>
        <td>${hopCell(r.completion_pct)}</td><td>${hopCell(r.next_milestone)}</td><td>${hopCell(r.assigned_to)}</td>
        <td><button type="button" class="nx-btn" onclick="openHopProjectHub(${r.id})">Open hub</button></td>
      </tr>`).join(''),
      { label: 'Projects', count: rows.length, searchValue: q, searchId: 'hop-q' },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Projects', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('project')">+ New Project</button>`, body);
}

/* ---------- Leads ---------- */
async function renderHopLeadsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/leads') || []; hopState.leads = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Leads', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'Leads', value: rows.length, tone: 'unpaid' }])}
    ${hopTable(
      ['Lead No', 'Customer', 'Project', 'Source', 'Value', 'Priority', 'Sales', 'Stage', 'Prob %', 'Follow-up', 'Status', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.lead_number)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.source)}</td><td class="inv-num">${hopMoney(r.expected_value)}</td><td>${hopCell(r.priority)}</td>
        <td>${hopCell(r.assigned_to)}</td>
        <td><select onchange="hopPatchLead(${r.id}, this.value)">${hopStageOptions(HOP_LEAD_STAGES, r.stage)}</select></td>
        <td>${hopCell(r.probability_pct)}</td><td>${hopCell(r.next_follow_up)}</td><td>${hopCell(r.status)}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Project</button>` : '—'}</td>
      </tr>`).join(''),
      { label: 'Leads', count: rows.length, searchId: 'hop-q' },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Leads', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('lead')">+ New Lead</button>`, body);
}

async function hopPatchLead(id, stage) {
  try {
    await hopApi(`/api/v1/hop/leads/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }) });
    openHopView('leads');
  } catch (e) { alert(e.message); }
}

/* ---------- Meetings ---------- */
async function renderHopMeetingsModule(mount) {
  let rows = [];
  let dash = null;
  try {
    rows = await hopApi('/api/v1/hop/meetings') || [];
    hopState.meetings = rows;
    dash = await hopApi('/api/v1/hop/reports/meetings');
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Meetings', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const c = dash?.counts || {};
  const body = `
    ${hopTxCards([
      { label: 'Today', value: c.today ?? 0, tone: 'paid' },
      { label: 'Upcoming', value: c.upcoming ?? 0, tone: 'unpaid', op: '+' },
      { label: 'Missed', value: c.missed ?? 0, tone: 'overdue', op: '+' },
      { label: 'Follow-up Due', value: c.follow_up_due ?? 0, tone: 'total', op: '=' },
    ])}
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['When', 'Title', 'Customer', 'Project', 'Location', 'Status', 'Outcome', 'Next Action', 'Follow-up', 'Expected Value', 'Prob %'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.scheduled_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td>${hopCell(r.title)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.location)}</td><td>${hopCell(r.status)}</td><td>${hopCell(r.outcome)}</td>
        <td>${hopCell(r.next_action)}</td><td>${hopCell(r.follow_up_at)}</td>
        <td class="inv-num">${hopMoney(r.expected_order_value)}</td><td>${hopCell(r.probability_pct)}</td>
      </tr>`).join(''),
      { label: 'Meetings', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Meetings', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('meeting')">+ New Meeting</button>`, body);
}

/* ---------- Sale ledger docs (Estimate / Proforma / SO / Challan / Credit Note / FA) ---------- */
const HOP_SALE_LEDGER = {
  sale_estimates: {
    title: 'Estimate / Quotation',
    types: [...HOP_TXN_ESTIMATE],
    empty: 'No estimates / quotations in this filter.',
    numberLabel: 'Estimate no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopShowForm('quotation')">+ New Quotation</button>`,
  },
  sale_proforma: {
    title: 'Proforma Invoice',
    types: [...HOP_TXN_PROFORMA],
    empty: 'No proforma invoices in this filter.',
    numberLabel: 'Proforma no',
  },
  sale_orders: {
    title: 'Sale Order',
    types: [...HOP_TXN_SALE_ORDER],
    empty: 'No sale orders in this filter.',
    numberLabel: 'Order no',
  },
  sale_challan: {
    title: 'Delivery Challan',
    types: [...HOP_TXN_CHALLAN],
    empty: 'No delivery challans in this filter.',
    numberLabel: 'Challan no',
  },
  sale_returns: {
    title: 'Sale Return / Credit Note',
    types: [...HOP_TXN_SALE_RETURN],
    empty: 'No sale returns / credit notes in this filter.',
    numberLabel: 'Credit note no',
  },
  sale_fa: {
    title: 'Sale FA',
    types: [],
    labelQ: 'fa',
    empty: 'No Sale FA records in this filter.',
    numberLabel: 'FA no',
  },
  sale_payment_in: {
    title: 'Payment-In',
    types: [...HOP_TXN_PAYMENT_IN],
    empty: 'No payment-in records in this filter.',
    numberLabel: 'Receipt no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopShowForm('payment')">+ Record Payment</button>`,
  },
  invoices: {
    title: 'Sale Invoices',
    empty: 'No invoices in this filter.',
    numberLabel: 'Invoice no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopShowForm('invoice')">+ Add Sale</button>`,
  },
  purchase_bills: {
    title: 'Purchase Bills',
    types: [...HOP_TXN_PURCHASE_BILL],
    empty: 'No purchase bills in this filter.',
    numberLabel: 'Bill no',
  },
  purchase_payment_out: {
    title: 'Payment-Out',
    types: [...HOP_TXN_PAYMENT_OUT],
    empty: 'No payment-out records in this filter.',
    numberLabel: 'Voucher no',
  },
  purchase_expenses: {
    title: 'Expenses',
    types: [...HOP_TXN_EXPENSE],
    empty: 'No expenses in this filter.',
    numberLabel: 'Expense no',
  },
  purchase_orders: {
    title: 'Purchase Order',
    types: [],
    labelQ: 'purchase order',
    empty: 'No purchase orders in this filter.',
    numberLabel: 'PO no',
  },
  journal_entries: {
    title: 'Journal Entry',
    types: [...HOP_TXN_JOURNAL],
    empty: 'No journal entries in this filter.',
    numberLabel: 'Entry no',
  },
  purchase_returns: {
    title: 'Purchase Return / Dr. Note',
    types: [...HOP_TXN_PURCHASE_RETURN],
    empty: 'No purchase returns / debit notes in this filter.',
    numberLabel: 'Return no',
  },
  purchase_fa: {
    title: 'Purchase FA',
    types: [],
    labelQ: 'fa',
    empty: 'No Purchase FA records in this filter.',
    numberLabel: 'FA no',
  },
};

const HOP_NON_SALE_NOTE_RE = /\[(estimate\/quotation|estimate|proforma invoice|sale return|sales order|delivery challan)\]/i;

async function hopFilterSaleInvoicesOnly(invoices) {
  const nonSaleNos = new Set();
  const saleNos = new Set();
  try {
    const [nonSale, sale] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=21,27,30,65,81,82,83'),
      hopApi('/api/v1/hop/party-transactions?txn_types=1'),
    ]);
    (nonSale || []).forEach((t) => {
      const n = String(t.txn_number || '').trim().toLowerCase();
      if (n) nonSaleNos.add(n);
    });
    (sale || []).forEach((t) => {
      const n = String(t.txn_number || '').trim().toLowerCase();
      if (n) saleNos.add(n);
    });
  } catch (_) { /* ledger optional */ }
  return (invoices || []).filter((inv) => {
    const no = String(inv.invoice_no || '').trim().toLowerCase();
    const notes = String(inv.notes || '');
    if (HOP_NON_SALE_NOTE_RE.test(notes)) return false;
    // Legacy bad import: Vyapar type 27 Estimates were tagged "[Sale]" (not "[Sale Invoice]").
    if (/\[Sale\]/i.test(notes) && !/\[Sale Invoice\]/i.test(notes)) return false;
    if (no && nonSaleNos.has(no) && !saleNos.has(no)) return false;
    return true;
  });
}

function hopNormalizeLedgerToInvoice(r, fallbackLabel) {
  let amount = Number(r.total_amount ?? r.amount ?? r.value ?? 0);
  let balance = Number(r.balance_amount ?? r.balance ?? 0);
  const txnType = r.txn_type != null && r.txn_type !== '' ? Number(r.txn_type) : null;
  const isEstimate = Number.isFinite(txnType) && HOP_TXN_ESTIMATE.has(txnType);
  if (isEstimate) balance = 0;
  if (!isEstimate && balance > amount + 0.05) amount = balance;
  const paidExplicit = r.paid_amount;
  let paid = isEstimate
    ? 0
    : (paidExplicit != null
      ? Number(paidExplicit || 0)
      : Math.max(0, amount - balance));
  const statusRaw = String(r.status_text || r.status || '').toLowerCase();
  let status = String(r.status || 'open');
  if (isEstimate) {
    status = statusRaw.includes('cancel') ? 'cancelled'
      : (statusRaw.includes('partial') ? 'partial'
        : (statusRaw || 'approved'));
  } else if (statusRaw === 'paid' || statusRaw.includes('paid') || statusRaw.includes('used') || balance <= 0.009) status = 'paid';
  else if (statusRaw.includes('partial')) status = 'partial';
  else if (statusRaw.includes('overdue')) status = 'overdue';
  else if (paid > 0 && balance > 0) status = 'partial';
  else if (amount > 0 && paid <= 0) status = 'unpaid';
  // Legacy tax-inclusive inflate: amount>due with no receipt → snap amount to balance (fully unpaid).
  if (!isEstimate && balance > 0.05 && amount > balance + 0.05 && paidExplicit == null) {
    if (status === 'unpaid' || status === 'open' || status === 'overdue' || statusRaw.includes('open')) {
      amount = balance;
      paid = 0;
      status = statusRaw.includes('overdue') ? 'overdue' : 'unpaid';
    }
  }
  return {
    invoice_date: String(r.txn_date || r.invoice_date || r.quote_date || r.paid_at || '').slice(0, 10),
    invoice_no: r.txn_number || r.invoice_no || r.quote_no || '',
    customer_company: r.party_name || r.customer_company || '',
    project_name: r.project_name || '',
    amount,
    paid_amount: paid,
    balance,
    due_date: String(r.due_date || r.txn_due_date || '').slice(0, 10),
    status,
    notes: r.txn_label || r.notes || fallbackLabel || '',
    txn_type: Number.isFinite(txnType) ? txnType : undefined,
    txn_label: r.txn_label || fallbackLabel || '',
    // Preview keys: party ledger id preferred; else Vyapar source_txn_id.
    party_txn_id: (() => {
      if (r.party_txn_id != null && r.party_txn_id !== '') return Number(r.party_txn_id);
      // hop_party_transactions rows expose txn_number (ledger API).
      if (r.txn_number !== undefined && r.id != null && r.id !== '') return Number(r.id);
      return null;
    })(),
    source_txn_id: r.source_txn_id != null && r.source_txn_id !== '' ? Number(r.source_txn_id) : null,
  };
}

/** Defense: keep only allowed Vyapar txn types (API filter can be stale/missing). */
function hopFilterRowsByTxnTypes(rows, types) {
  const allow = new Set((types || []).map(Number).filter((n) => Number.isFinite(n)));
  if (!allow.size) return rows || [];
  return (rows || []).filter((r) => {
    const ty = Number(r.txn_type);
    if (Number.isFinite(ty)) return allow.has(ty);
    // Normalized rows without txn_type: fall back to label text for payment filters.
    if (allow.size === 1 && allow.has(3)) {
      return /payment\s*in/i.test(String(r.txn_label || r.notes || ''));
    }
    if (allow.size === 1 && allow.has(4)) {
      return /payment\s*out/i.test(String(r.txn_label || r.notes || ''));
    }
    return false;
  });
}

async function hopLoadSaleDocRows(kind) {
  const cfg = HOP_SALE_LEDGER[kind] || HOP_SALE_LEDGER.invoices;
  if (kind === 'invoices') {
    // Source of truth = Vyapar type 1 ledger. hop_invoices can miss newer imports
    // and reuse the same invoice_no across years (e.g. 106 in 2025 and 2026).
    const [ledgerRaw, invoices] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=1').catch(() => []),
      hopApi('/api/v1/hop/invoices').catch(() => []),
    ]);
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw || [], [1]);
    const fromLedger = ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Sale Invoice'));
    const ledgerKeys = new Set(
      ledger.map((r) => {
        const no = String(r.txn_number || '').trim().toLowerCase();
        const d = String(r.txn_date || '').slice(0, 10);
        return `${no}|${d}`;
      }).filter((k) => k !== '|'),
    );
    const ledgerNos = new Set(
      ledger.map((r) => String(r.txn_number || '').trim().toLowerCase()).filter(Boolean),
    );
    const fromInv = (await hopFilterSaleInvoicesOnly(invoices || [])).filter((inv) => {
      const no = String(inv.invoice_no || '').trim().toLowerCase();
      const d = String(inv.invoice_date || '').slice(0, 10);
      if (ledgerKeys.has(`${no}|${d}`)) return false;
      // Prefer ledger when the same serial exists there (avoids showing stale year collision).
      if (no && ledgerNos.has(no)) return false;
      return true;
    }).map((inv) => hopNormalizeLedgerToInvoice({
      invoice_date: inv.invoice_date,
      invoice_no: inv.invoice_no,
      customer_company: inv.customer_company,
      amount: inv.amount,
      paid_amount: inv.paid_amount,
      balance: inv.balance,
      due_date: inv.due_date,
      status: inv.status,
      notes: inv.notes || 'Sale Invoice',
      txn_label: 'Sale Invoice',
      txn_type: 1,
      source_txn_id: inv.source_txn_id,
      project_name: inv.project_name,
    }, 'Sale Invoice'));
    return [...fromLedger, ...fromInv];
  }
  if (kind === 'sale_estimates') {
    const [ledgerRaw, quotes] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=27,30').catch(() => []),
      hopApi('/api/v1/hop/quotations').catch(() => []),
    ]);
    hopState.quotations = quotes || [];
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw, [27, 30]);
    const fromLedger = ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Estimate/Quotation'));
    const ledgerNos = new Set(
      ledger.map((r) => String(r.txn_number || '').trim().toLowerCase()).filter(Boolean),
    );
    const fromQuotes = (quotes || [])
      .filter((q) => !ledgerNos.has(String(q.quote_no || '').trim().toLowerCase()))
      .map((q) => hopNormalizeLedgerToInvoice({
        quote_date: q.quote_date,
        quote_no: q.quote_no,
        customer_company: q.customer_company,
        value: q.value,
        balance: Number(q.value || 0),
        paid_amount: 0,
        status: q.status || 'sent',
        notes: 'Estimate/Quotation',
        txn_label: 'Estimate/Quotation',
        txn_type: 30,
        project_name: q.project_name,
      }, 'Estimate/Quotation'));
    return [...fromLedger, ...fromQuotes];
  }
  if (kind === 'sale_payment_in') {
    const [ledgerRaw, payments] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=3').catch(() => []),
      hopApi('/api/v1/hop/payments').catch(() => []),
    ]);
    // Never trust API alone — Payment-Out (4) must not appear here.
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw, [3]);
    if (ledger.length) {
      return ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Payment In'));
    }
    return (payments || []).map((p) => hopNormalizeLedgerToInvoice({
      paid_at: p.paid_at,
      invoice_no: p.invoice_no || `PAY-${p.id}`,
      customer_company: p.customer_company,
      amount: p.amount,
      paid_amount: p.amount,
      balance: 0,
      status: 'paid',
      notes: p.notes || 'Payment In',
      txn_label: 'Payment In',
      txn_type: 3,
      project_name: p.project_name,
    }, 'Payment In'));
  }
  const qs = cfg.types?.length
    ? `txn_types=${cfg.types.join(',')}`
    : (cfg.labelQ ? `label_q=${encodeURIComponent(cfg.labelQ)}` : '');
  let rows = await hopApi(`/api/v1/hop/party-transactions${qs ? `?${qs}` : ''}`) || [];
  // Always enforce type filter client-side (stale API may ignore txn_types).
  if (cfg.types?.length) {
    rows = hopFilterRowsByTxnTypes(rows, cfg.types);
  } else if (cfg.labelQ) {
    const q = String(cfg.labelQ).toLowerCase();
    rows = (rows || []).filter((r) => {
      const label = String(r.txn_label || '').toLowerCase();
      if (!label.includes(q)) return false;
      // Never treat Journal (81) / Sales Order as Purchase Order.
      if (kind === 'purchase_orders' && (
        HOP_TXN_JOURNAL.has(Number(r.txn_type))
        || HOP_TXN_SALE_ORDER.has(Number(r.txn_type))
        || /sales?\s*order|journal/i.test(label)
      )) {
        return false;
      }
      return true;
    });
  }
  if (kind === 'sale_fa') {
    rows = rows.filter((r) => /fa/i.test(String(r.txn_label || '')) && !/proforma/i.test(String(r.txn_label || '')) && !/purchase/i.test(String(r.txn_label || '')));
  }
  if (kind === 'purchase_fa') {
    rows = rows.filter((r) => /fa/i.test(String(r.txn_label || '')) && /purchase/i.test(String(r.txn_label || '')));
  }
  // Purchase Return: only type 16. Never fall back to unfiltered "all txns".
  if (kind === 'purchase_returns') {
    rows = hopFilterRowsByTxnTypes(rows, [16]).filter((r) => {
      const label = String(r.txn_label || '').toLowerCase();
      const no = String(r.txn_number || '').toUpperCase();
      if (/estimate|quotation|sale|proforma|payment|hoppi/i.test(label)) return false;
      if (no.startsWith('HOPPI')) return false;
      return true;
    });
  }
  return rows.map((r) => hopNormalizeLedgerToInvoice(r, cfg.title));
}

function hopSaleDocMeta() {
  return hopState.saleDocMeta || HOP_SALE_LEDGER.invoices;
}

function hopBindSaleDocPartyOutsideClick() {
  hopInvoicePartyLabelSync();
  if (!window.__hopInvoicePartyDocBound) {
    window.__hopInvoicePartyDocBound = true;
    document.addEventListener('mousedown', (ev) => {
      if (!ev.target.closest?.('.inv-party-dd') && !ev.target.closest?.('#inv-party-panel')) {
        hopInvoicePartyCloseMenu();
      }
    });
    window.addEventListener('scroll', () => hopInvoicePartyCloseMenu(), true);
    window.addEventListener('resize', () => hopInvoicePartyCloseMenu());
  }
}

async function renderHopSaleDocListModule(mount, kind) {
  const cfg = HOP_SALE_LEDGER[kind] || HOP_SALE_LEDGER.invoices;
  hopState.saleDocMeta = cfg;
  hopState.saleDocKind = kind;
  let rows = [];
  try {
    rows = await hopLoadSaleDocRows(kind);
    hopState.invoices = rows;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', cfg.title, '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  if (!hopState.invoiceUi.from && hopState.invoiceUi.period !== 'all' && hopState.invoiceUi.period !== 'custom') {
    const range = hopInvoicePeriodRange(hopState.invoiceUi.period || 'this_month');
    hopState.invoiceUi.from = range.from;
    hopState.invoiceUi.to = range.to;
  }
  // Delivery Challan / older docs: default "This Month" can hide everything.
  // If the range filters to zero but data exists, open All Time automatically.
  if (
    String(hopState.invoiceUi.period || '') === 'this_month'
    && rows.length > 0
    && hopFilteredInvoices().length === 0
  ) {
    hopState.invoiceUi.period = 'all';
    hopState.invoiceUi.from = '';
    hopState.invoiceUi.to = '';
  }
  const ui = hopState.invoiceUi;
  const parties = [...new Set(rows.map((r) => r.customer_company || r.party_name || r.client_name).filter(Boolean))]
    .sort((a, b) => String(a).localeCompare(String(b)));
  hopState.invoicePartyNames = parties;

  const filtered = hopFilteredInvoices();
  const sum = hopInvoiceSummary(filtered);
  const numberLabel = cfg.numberLabel || 'Number';
  const isPayVoucher = hopIsPaymentVoucherKind(kind);
  const paidLabel = isPayVoucher ? 'Used' : 'Paid';
  const unpaidLabel = isPayVoucher ? 'Unused' : 'Unpaid';

  document.querySelectorAll('body > #inv-party-panel').forEach((el) => el.remove());

  const body = `
        <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
        ${hopTxToolbar(`
          <select id="inv-period" class="inv-ctrl" onchange="hopInvoiceOnPeriodChange()">
            <option value="today"${ui.period === 'today' ? ' selected' : ''}>Today</option>
            <option value="this_week"${ui.period === 'this_week' ? ' selected' : ''}>This Week</option>
            <option value="this_month"${ui.period === 'this_month' ? ' selected' : ''}>This Month</option>
            <option value="last_month"${ui.period === 'last_month' ? ' selected' : ''}>Last Month</option>
            <option value="this_quarter"${ui.period === 'this_quarter' ? ' selected' : ''}>This Quarter</option>
            <option value="this_year"${ui.period === 'this_year' ? ' selected' : ''}>This Year</option>
            <option value="all"${ui.period === 'all' ? ' selected' : ''}>All Time</option>
            <option value="custom"${ui.period === 'custom' ? ' selected' : ''}>Custom</option>
          </select>
          <input type="date" id="inv-from" class="inv-ctrl inv-date" value="${foEscapeAttr(ui.from || '')}" onchange="hopInvoiceApplyFilters()" />
          <span class="inv-sep">to</span>
          <input type="date" id="inv-to" class="inv-ctrl inv-date" value="${foEscapeAttr(ui.to || '')}" onchange="hopInvoiceApplyFilters()" />
          <select id="inv-status" class="inv-ctrl" onchange="hopInvoiceApplyFilters()">
            <option value="all"${ui.status === 'all' ? ' selected' : ''}>All Status</option>
            <option value="paid"${ui.status === 'paid' ? ' selected' : ''}>${paidLabel}</option>
            <option value="unpaid"${ui.status === 'unpaid' ? ' selected' : ''}>${unpaidLabel}</option>
            <option value="partial"${ui.status === 'partial' ? ' selected' : ''}>Partial</option>
            ${isPayVoucher ? '' : `<option value="overdue"${ui.status === 'overdue' ? ' selected' : ''}>Overdue</option>
            <option value="open"${ui.status === 'open' ? ' selected' : ''}>Open</option>`}
          </select>
          <div class="inv-party-dd">
            <button type="button" id="inv-party-toggle" class="inv-ctrl inv-party-toggle" onclick="hopInvoicePartyToggle(event)" aria-haspopup="listbox" aria-expanded="false">
              <span id="inv-party-label" class="inv-party-label${!ui.party ? ' is-placeholder' : ''}">${foEscapeText(ui.party || 'All Parties')}</span>
              <span class="inv-party-caret" aria-hidden="true">▾</span>
            </button>
            <div id="inv-party-panel" class="inv-party-panel hidden" role="listbox">
              <div class="inv-party-search-wrap">
                <input id="inv-party-q" class="inv-party-q" type="search" autocomplete="off"
                  placeholder="Type to search party…"
                  oninput="hopInvoicePartyOnInput()"
                  onkeydown="if(event.key==='Escape'){hopInvoicePartyCloseMenu();}" />
              </div>
              <div id="inv-party-menu" class="inv-party-menu-list"></div>
            </div>
          </div>
          <button type="button" class="inv-text-btn" onclick="hopInvoiceResetFilters()">Reset</button>
          <span class="inv-toolbar-spacer"></span>
          <button type="button" class="inv-text-btn" onclick="hopInvoiceExportCsv()">Excel</button>
          <button type="button" class="inv-text-btn" onclick="window.print()">Print</button>
        `)}
        ${hopTxCards(isPayVoucher
          ? [
              { label: 'Used', valueHtml: hopMoney(sum.received), tone: 'paid', id: 'inv-sum-paid' },
              { label: 'Unused', valueHtml: hopMoney(sum.unpaid), tone: 'unpaid', op: '+', id: 'inv-sum-unpaid' },
              { label: 'Total', valueHtml: hopMoney(sum.total), tone: 'total', op: '=', id: 'inv-sum-total' },
            ]
          : [
              { label: 'Paid', valueHtml: hopMoney(sum.received), tone: 'paid', id: 'inv-sum-paid' },
              { label: 'Unpaid', valueHtml: hopMoney(sum.unpaid), tone: 'unpaid', op: '+', id: 'inv-sum-unpaid' },
              { label: 'Overdue', valueHtml: hopMoney(sum.overdue), tone: 'overdue', op: '+', id: 'inv-sum-overdue' },
              { label: 'Total', valueHtml: hopMoney(sum.total), tone: 'total', op: '=', id: 'inv-sum-total' },
            ])}
        <div class="inv-table-card">
          <div class="inv-table-head">
            <strong>Transactions</strong>
            <span id="inv-sum-count" class="inv-count">${sum.count} txns</span>
            <input id="inv-search" class="inv-search" type="search" placeholder="Search…"
              value="${foEscapeAttr(ui.q || '')}" oninput="hopInvoiceApplyFilters()" />
          </div>
          <div class="inv-table-wrap">
            <table class="inv-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>${foEscapeText(numberLabel)}</th>
                  <th>Party Name</th>
                  <th class="inv-num">Amount</th>
                  <th class="inv-num">Balance</th>
                  <th>Due date</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="inv-tbody">${hopRenderInvoiceRows(filtered)}</tbody>
            </table>
          </div>
        </div>`;
  const section = String(kind || '').startsWith('purchase_')
    ? 'Purchase'
    : (kind === 'journal_entries' ? 'Accounting' : 'Sale');
  mount.innerHTML = hopModuleShell(section, cfg.title, '', cfg.addHtml || '', body);
  hopBindSaleDocPartyOutsideClick();
}

async function renderHopSaleLedgerModule(mount, kind) {
  return renderHopSaleDocListModule(mount, kind);
}

async function renderHopSaleEstimatesModule(mount) {
  return renderHopSaleDocListModule(mount, 'sale_estimates');
}

async function renderHopPaymentInModule(mount) {
  return renderHopSaleDocListModule(mount, 'sale_payment_in');
}

/* ---------- Quotations ---------- */
async function renderHopQuotationsModule(mount) {
  return renderHopSaleEstimatesModule(mount);
}

async function hopPatchQuote(id, status) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    openHopView('sale_estimates');
  } catch (e) { alert(e.message); }
}

async function hopReviseQuote(id) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}/revise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    openHopView('sale_estimates');
  } catch (e) { alert(e.message); }
}

/* ---------- Vendors / Compare / Samples / Products ---------- */
async function renderHopVendorsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/vendors') || []; hopState.vendors = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Procurement', 'Vendors', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopIsMobileView()
      ? hopRenderMobileContactCards(rows, 'vendors')
      : `${hopRenderDesktopContactToolbar('vendors', rows.length)}
         ${hopTable(
        ['', 'Company', 'Products', 'GST', 'Contact', 'Mobile', 'City', 'Rating', 'Lead Time', 'Payment Terms', 'On-time %', 'Quality', ''],
        rows.map((r) => {
          const state = hopContactSelectState('vendors');
          const checked = state.ids.includes(Number(r.id)) ? ' checked' : '';
          return `<tr class="hop-clickable-row" onclick="hopOpenContactDetail('vendors', ${r.id})" style="cursor:pointer">
          <td onclick="event.stopPropagation()"><input type="checkbox" class="hop-desk-check" value="${r.id}"${checked} onchange="hopToggleContactSelected('vendors', ${r.id}, this.checked)" /></td>
          <td>${hopCell(r.company)}</td><td>${hopCell(r.products)}</td><td>${hopCell(r.gst_no)}</td>
          <td>${hopCell(r.contact_person)}</td><td>${hopCell(r.mobile)}</td><td>${hopCell(r.city)}</td>
          <td>${hopCell(r.rating)}</td><td>${hopCell(r.lead_time_days)}</td><td>${hopCell(r.payment_terms)}</td>
          <td>${hopCell(r.on_time_pct)}</td><td>${hopCell(r.quality_rating)}</td>
          <td><button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('vendors', ${r.id}, '${foEscapeAttr(hopContactLabel(r))}')" title="Delete">${hopContactIcon('delete')}</button></td>
        </tr>`}).join(''),
      )}`}`;
  mount.innerHTML = hopModuleShell('Procurement', 'Vendors', 'Supplier performance & terms',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('vendor')">+ New Vendor</button>`, body);
  if (hopIsMobileView()) requestAnimationFrame(() => hopBindMobileContactCards('vendors'));
}

function hopReadRateFiltersFromDom() {
  const f = hopState.rateFilters || (hopState.rateFilters = { q: '' });
  const qEl = document.getElementById('hop-rate-filter-q');
  if (qEl) f.q = String(qEl.value || '').trim();
  return f;
}

function hopResetRateFilters() {
  hopState.rateFilters = { q: '' };
  const qEl = document.getElementById('hop-rate-filter-q');
  if (qEl) qEl.value = '';
  hopApplyRateMatrixFilters();
}

function hopProductMatchesRateFilters(p, filters) {
  const f = filters || hopState.rateFilters || {};
  const q = String(f.q || '').trim().toLowerCase();
  if (!q) return true;
  const blob = `${p.label || ''} ${p.size || ''} ${p.quality_hint || ''} ${p.product_key || ''}`.toLowerCase();
  return blob.includes(q);
}

function hopApplyRateMatrixFilters() {
  const f = hopReadRateFiltersFromDom();
  const rows = document.querySelectorAll('#hop-rate-matrix-body tr[data-product-key]');
  let visible = 0;
  rows.forEach((tr) => {
    const p = {
      product_key: decodeURIComponent(tr.getAttribute('data-product-key') || ''),
      label: tr.getAttribute('data-label') || '',
      size: tr.getAttribute('data-size') || '',
      quality_hint: tr.getAttribute('data-quality') || '',
    };
    const ok = hopProductMatchesRateFilters(p, f);
    tr.classList.toggle('hop-rate-row-hidden', !ok);
    if (ok) visible += 1;
  });

  const countEl = document.getElementById('hop-rate-filter-count');
  if (countEl) {
    const total = rows.length;
    countEl.textContent = !f.q || visible === total ? `${total} products` : `${visible} / ${total}`;
  }

  document.querySelectorAll('#hop-rate-suggest-list li[data-product-key]').forEach((li) => {
    const key = decodeURIComponent(li.getAttribute('data-product-key') || '');
    const product = (hopState.rateMatrix?.products || []).find((x) => x.product_key === key);
    if (!product) {
      li.classList.add('hop-rate-row-hidden');
      return;
    }
    li.classList.toggle('hop-rate-row-hidden', !hopProductMatchesRateFilters(product, f));
  });
}

async function renderHopVendorCmpModule(mount) {
  let matrix = { suppliers: [], products: [], suggestions: [], summary: {} };
  let sheets = [];
  hopLoadRateCart();
  try {
    matrix = await hopApi('/api/v1/hop/rate-compare') || matrix;
    sheets = await hopApi('/api/v1/hop/rate-sheets') || [];
  } catch (e) {
    mount.innerHTML = hopModuleShell('Procurement', 'Vendor Comparison', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  hopState.rateMatrix = matrix;
  if (!hopState.rateFilters) hopState.rateFilters = { q: '' };
  const filters = hopState.rateFilters;
  const suppliers = matrix.suppliers || [];
  const products = matrix.products || [];
  const suggestions = matrix.suggestions || [];
  const summary = matrix.summary || {};
  const cartCount = (hopState.rateCart || []).length;
  const cartKeys = new Set((hopState.rateCart || []).map((c) => c.product_key));

  const headCells = [
    `<input type="checkbox" id="hop-rate-select-all" title="Select all" onchange="hopToggleSelectAllRates(this.checked)" />`,
    'Product',
    ...suppliers.map((s) => hopCell(s.supplier_name)),
    'Best / cart',
    'Item',
  ];
  const bodyRows = products.map((p) => {
    const enc = encodeURIComponent(p.product_key || '');
    const quotedIds = Object.entries(p.offers || {})
      .filter(([, o]) => o && !o.missing && Number(o.rate) > 0)
      .map(([sid]) => sid)
      .join(',');
    const inCart = cartKeys.has(p.product_key);
    const cells = suppliers.map((s) => {
      const offer = (p.offers || {})[String(s.sheet_id)];
      if (!offer || offer.missing) {
        return '<td class="hop-rate-empty"><div class="hop-rate-val">₹0</div><div class="hop-rate-sub">not quoted</div></td>';
      }
      if (offer.rate == null || Number(offer.rate) === 0) {
        return '<td class="hop-rate-empty"><div class="hop-rate-val">₹0</div></td>';
      }
      const isBest = p.best && String(p.best.sheet_id) === String(s.sheet_id);
      const cellInCart = (hopState.rateCart || []).some(
        (c) => c.product_key === p.product_key && String(c.sheet_id) === String(s.sheet_id),
      );
      return `<td class="${isBest ? 'hop-rate-best' : ''}">
        <div class="hop-rate-val">${hopMoney(offer.rate)}</div>
        <div class="hop-rate-sub">+${hopCell(offer.gst_pct)}% → ${hopMoney(offer.landed_rate)}</div>
        <div class="hop-rate-cell-actions">
          <button type="button" class="nx-btn hop-rate-add-btn${cellInCart ? ' is-in-cart' : ''}"
            onclick="hopAddToRateCart('${enc}', ${s.sheet_id})">${cellInCart ? 'Added +' : 'Add to cart'}</button>
          <button type="button" class="nx-btn hop-rate-clear-btn" title="Remove this supplier rate for this item"
            onclick="hopClearSingleRate('${enc}', ${s.sheet_id})">✕</button>
        </div>
      </td>`;
    }).join('');
    let bestCell = '<td>—</td>';
    if (p.best) {
      const tag = (p.supplier_count || 0) >= 2 ? 'cheapest' : 'only this seller';
      bestCell = `<td class="hop-rate-best-col">
        <strong>${hopCell(p.best.supplier_name)}</strong>
        <div class="hop-rate-sub">${hopMoney(p.best.landed_rate)} landed · ${tag}</div>
        <button type="button" class="nx-btn hop-rate-add-btn" onclick="hopAddBestToRateCart('${enc}')">Add best</button>
      </td>`;
    }
    return `<tr data-product-key="${enc}"
      data-label="${foEscapeText(p.label || '')}"
      data-size="${foEscapeText(p.size || '')}"
      data-quality="${foEscapeText(p.quality_hint || '')}"
      data-category="${foEscapeText(p.category || '')}"
      data-scount="${Number(p.supplier_count || 0)}"
      data-quoted="${foEscapeText(quotedIds)}"
      data-in-cart="${inCart ? '1' : '0'}">
      <td><input type="checkbox" class="hop-rate-row-check" value="${enc}" title="Select item" /></td>
      <td><strong>${hopCell(p.label)}</strong>${p.size ? `<div class="hop-rate-sub">${hopCell(p.size)}</div>` : ''}${p.quality_hint ? `<div class="hop-rate-sub">${hopCell(p.quality_hint)}</div>` : ''}</td>
      ${cells}${bestCell}
      <td><button type="button" class="nx-btn hop-rate-clear-btn" title="Delete this item from all suppliers"
        onclick="hopClearSingleProduct('${enc}')">Delete item</button></td>
    </tr>`;
  }).join('');

  const matrixTableHtml = products.length
    ? `<div class="hop-table-wrap"><table class="data-table hop-table hop-rate-matrix-table">
        <thead><tr>${headCells.map((h) => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody id="hop-rate-matrix-body">${bodyRows}</tbody>
      </table></div>`
    : '<p class="nx-text-dim">Matrix empty — upload supplier rates.</p>';

  const suggestHtml = suggestions.length
    ? `<div class="hop-rate-suggest nx-card">
        <h4>Suggestion — cheapest where 2+ sellers quoted</h4>
        <ul id="hop-rate-suggest-list">${suggestions.map((s) =>
          `<li data-product-key="${encodeURIComponent(s.product_key || '')}"><strong>${hopCell(s.label)}</strong> → <strong>${hopCell(s.best_supplier)}</strong> at ${hopMoney(s.best_landed)} landed · ${s.alternatives} sellers compared
            <button type="button" class="nx-btn hop-rate-add-btn" onclick="hopAddBestToRateCart('${encodeURIComponent(s.product_key || '')}')">Add best</button>
          </li>`
        ).join('')}</ul>
      </div>`
    : `<div class="hop-rate-suggest nx-card"><p class="nx-text-dim">Jab same product 2+ suppliers pe milega, yahan cheapest suggestion aayegi.</p></div>`;

  const sheetList = sheets.length
    ? hopTable(
      ['Supplier', 'Source', 'File', 'Lines', 'Notes', ''],
      sheets.map((r) => `<tr>
        <td>${hopCell(r.supplier_name)}</td>
        <td>${hopCell(r.source_type)}</td>
        <td>${hopCell(r.source_filename || '—')}</td>
        <td>${hopCell(r.line_count)}</td>
        <td>${hopCell(r.notes)}</td>
        <td class="hop-rate-cell-actions">
          <button type="button" class="nx-btn hop-rate-clear-btn" title="Vendor-wise: clear all items for this supplier"
            onclick="hopClearSheetRates(${r.id}, '${encodeURIComponent(r.supplier_name || '')}')">Clear vendor</button>
          <button type="button" class="nx-btn" onclick="hopDeleteRateSheet(${r.id})">Delete sheet</button>
        </td>
      </tr>`).join(''),
    )
    : '<p class="nx-text-dim">No rate sheets yet. Upload a supplier file or paste rates.</p>';

  const body = `
    <div class="hop-rate-summary">
      <span>${summary.supplier_count || 0} suppliers</span>
      <span>${summary.product_count || 0} products</span>
      <span>${summary.comparable_count || 0} multi-quote</span>
      <span>${cartCount} in quote cart</span>
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    <div id="hop-rate-cart-panel" class="nx-card hop-rate-cart-card"></div>
    <div class="nx-card hop-rate-matrix-card">
      <div class="hop-rate-matrix-toolbar">
        <h4>Full rate matrix</h4>
        <div class="hop-rate-product-filter">
          <input id="hop-rate-filter-q" class="hop-search hop-rate-filter-q" type="search"
            placeholder="Filter product…"
            value="${foEscapeText(filters.q || '')}"
            oninput="hopApplyRateMatrixFilters()" />
          <span id="hop-rate-filter-count" class="hop-rate-filter-count nx-text-dim">${products.length} products</span>
          <button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopClearSelectedRates()">Delete selected</button>
        </div>
      </div>
      <div class="hop-rate-scroll">
        ${matrixTableHtml}
      </div>
    </div>
    ${suggestHtml}
    <div class="nx-card hop-rate-sheets-card">
      <h4>Supplier rate sheets</h4>
      ${sheetList}
    </div>
    <div class="nx-card hop-rate-legacy-card">
      <h4>Legacy project comparisons</h4>
      <p class="nx-text-dim">Old single-row winner picks (still available).</p>
      <button type="button" class="nx-btn" onclick="hopShowForm('vendor_cmp')">+ Add project comparison row</button>
      <div id="hop-legacy-cmp"></div>
    </div>
  `;

  mount.innerHTML = hopModuleShell(
    'Procurement',
    'Vendor Comparison',
    'Compare → clear unwanted rates → cart → supplier orders',
    `<button type="button" class="nx-btn" onclick="hopClearRateQuote()">Clear quote cart</button>
     <button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopClearAllRates()">Clear all rate data</button>
     <button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('rate_sheet')">+ Upload / add rates</button>`,
    body,
  ).replace('class="hop-view hop-view--fullpage hop-view--module"', 'class="hop-view hop-view--fullpage hop-view--module hop-vendor-cmp-compact"')
   .replace('class="hop-view hop-view--fullpage"', 'class="hop-view hop-view--fullpage hop-vendor-cmp-compact"')
   .replace('class="hop-view"', 'class="hop-view hop-vendor-cmp-compact"');

  hopRenderRateCartPanel();
  hopApplyRateMatrixFilters();

  hopApi('/api/v1/hop/vendor-comparisons').then((rows) => {
    const el = document.getElementById('hop-legacy-cmp');
    if (!el || !rows?.length) return;
    el.innerHTML = hopTable(
      ['Project', 'Product', 'Vendor', 'Rate', 'Winner'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.product_name)}</td>
        <td>${hopCell(r.vendor_company)}</td><td>${hopMoney(r.rate)}</td>
        <td>${r.is_winner ? '★' : '—'}</td>
      </tr>`).join(''),
    );
  }).catch(() => {});
}

async function hopSeedRateSamples() {
  alert('Sample quotes disabled. Upload your supplier files — rates come only from uploads.');
}

async function hopDeleteRateSheet(id) {
  if (!(await nexoraConfirm('Delete this supplier rate sheet?', {
    title: 'Delete rate sheet',
    danger: true,
    okText: 'Delete',
  }))) return;
  try {
    await hopApi(`/api/v1/hop/rate-sheets/${id}`, { method: 'DELETE' });
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

function hopSupplierFromFilename(filename) {
  const base = String(filename || '').replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
  let name = base
    .replace(/[_\-+.]+/g, ' ')
    .replace(/\b(20\d{2}|19\d{2})([01]\d)([0-3]\d)(?:[_\s-]?\d{4,6})?\b/g, ' ')
    .replace(/\b(quote|rate|rates|sheet|price|list|supplier|vendor|scan|img|image|photo|copy|final|new)\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!name) name = base.replace(/[_\-+.]+/g, ' ').trim() || 'Supplier';
  // Title-case words, keep short ALLCAPS (UMD, GSB)
  return name.replace(/\b([A-Za-z][A-Za-z0-9]*)\b/g, (w) => {
    if (w.length <= 4 && w === w.toUpperCase()) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  });
}

function hopSourceFromFilename(filename) {
  const ext = String(filename || '').split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'pdf';
  if (['xlsx', 'xlsm', 'xls', 'ods', 'csv', 'tsv'].includes(ext)) return 'excel';
  if (['doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'word';
  if (['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tif', 'tiff', 'heic', 'heif'].includes(ext)) return 'image';
  if (['txt'].includes(ext)) return 'quote';
  return 'manual';
}

function hopOnRateFileSelected() {
  const fileInput = document.getElementById('f-rs-file');
  const file = fileInput?.files?.[0];
  const status = document.getElementById('f-rs-upload-status');
  if (!file) return;
  const supplierEl = document.getElementById('f-rs-supplier');
  const sourceEl = document.getElementById('f-rs-source');
  const titleEl = document.getElementById('f-rs-title');
  const detectedSupplier = hopSupplierFromFilename(file.name);
  const detectedSource = hopSourceFromFilename(file.name);
  if (supplierEl) {
    supplierEl.value = detectedSupplier;
    supplierEl.dataset.autoFilled = '1';
  }
  if (sourceEl) {
    if ([...sourceEl.options].some((o) => o.value === detectedSource)) {
      sourceEl.value = detectedSource;
    }
    sourceEl.dataset.autoFilled = '1';
  }
  if (titleEl && !titleEl.value.trim()) {
    titleEl.value = `${detectedSupplier} — ${file.name}`;
  }
  if (status) {
    status.textContent = `Detected: supplier “${detectedSupplier}” · source ${detectedSource} (from ${file.name})`;
  }
}

async function hopUploadRateSheet() {
  const status = document.getElementById('f-rs-upload-status');
  const fileInput = document.getElementById('f-rs-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    alert('Choose a file first (PDF, Excel, Word/RTF, JPG/BMP/PNG, CSV…)');
    fileInput?.click();
    return;
  }
  // Always sync from filename when empty / still auto-filled
  hopOnRateFileSelected();
  let supplier = document.getElementById('f-rs-supplier')?.value?.trim();
  if (!supplier) {
    supplier = hopSupplierFromFilename(file.name);
    const el = document.getElementById('f-rs-supplier');
    if (el) el.value = supplier;
  }
  const source = document.getElementById('f-rs-source')?.value || hopSourceFromFilename(file.name);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('supplier_name', supplier);
  fd.append('source_type', source);
  fd.append('title', document.getElementById('f-rs-title')?.value || `${supplier} — ${file.name}`);
  fd.append('notes', document.getElementById('f-rs-notes')?.value || '');
  fd.append('create_sheet', '1');
  if (status) status.textContent = 'Uploading & OCR… (handwriting 15–40 sec lag sakta hai)';
  try {
    const data = await hopApi('/api/v1/hop/rate-sheets/upload', { method: 'POST', body: fd });
    const lines = data?.lines || [];
    const warnings = data?.warnings || [];
    const lineCount = data?.line_count ?? lines.length;
    if (lines.length && document.getElementById('f-rs-lines')) {
      document.getElementById('f-rs-lines').value = lines.map((ln) =>
        [ln.product_name, ln.size || '', ln.rate, ln.gst_pct ?? 5].join(' | ')
      ).join('\n');
    }
    if (data?.source_type && document.getElementById('f-rs-source')) {
      const sel = document.getElementById('f-rs-source');
      if ([...sel.options].some((o) => o.value === data.source_type)) sel.value = data.source_type;
    }
    if (data?.created && lineCount > 0) {
      const msg = `Saved ${lineCount} rates for ${data.sheet?.supplier_name || supplier} (${data.parse_method || source})`;
      if (status) status.textContent = warnings.length ? `${msg}. ${warnings[0]}` : msg;
      alert(msg);
      openHopView('vendor_cmp');
      return;
    }
    if (!lines.length) {
      const detail = (warnings || []).join('\n') || 'No rates detected from this file.';
      if (status) status.textContent = detail;
      alert(`${detail}\n\nPaste lines manually (Product | Size | Rate | GST) then click Save pasted lines.`);
      return;
    }
    // Parsed but not created — keep form filled for manual save
    if (status) status.textContent = `Parsed ${lineCount} lines — click Save pasted lines to store.`;
    alert(`Parsed ${lineCount} lines. Click “Save pasted lines” to add to comparison.`);
  } catch (e) {
    if (status) status.textContent = '';
    alert(e.message || 'Upload failed');
  }
}

async function renderHopSamplesModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/samples') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Samples', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'Samples', value: rows.length, tone: 'unpaid' }])}
    ${hopTable(
      ['Sample', 'Customer', 'Project', 'Sent', 'Courier', 'Tracking', 'Return', 'Approval', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.sample_name)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.sent_at)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.tracking_number)}</td>
        <td>${hopCell(r.return_status)}</td><td>${hopCell(r.approval_status)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
      { label: 'Samples', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Sample Management', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('sample')">+ New Sample</button>`, body);
}

async function renderHopProductsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/products') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Catalogue', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'SKUs', value: rows.length, tone: 'total' }])}
    ${hopTable(
      ['Code', 'Name', 'Brand', 'Category', 'Sell', 'Buy', 'Logistics', 'GST%', 'Comm%', 'Net Profit', 'Margin %', 'Stock', 'Vendor'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.code)}</td><td>${hopCell(r.name)}</td><td>${hopCell(r.brand)}</td><td>${hopCell(r.category)}</td>
        <td class="inv-num">${hopMoney(r.selling_price)}</td><td class="inv-num">${hopMoney(r.purchase_price)}</td><td class="inv-num">${hopMoney(r.logistics_cost)}</td>
        <td>${hopCell(r.gst_pct)}</td><td>${hopCell(r.commission_pct)}</td>
        <td class="inv-num">${hopMoney(r.net_profit)}</td><td>${hopCell(r.margin_pct)}</td><td>${hopCell(r.stock_qty)}</td>
        <td>${hopCell(r.vendor_company)}</td>
      </tr>`).join(''),
      { label: 'Catalogue', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Product Catalogue', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('product')">+ New Product</button>`, body);
}

async function renderHopFabricPreviewModule(mount) {
  hopState.fabricPreview = hopState.fabricPreview || {
    demoFabricId: 'demo-linen-sand',
    fabricLabel: 'Linen Sand',
  };
  let bank = { demo_fabrics: [], catalogue: [], engine: 'demo', hint: '' };
  try {
    bank = await hopApi('/api/v1/hop/fabric-preview/fabrics') || bank;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Fabric Preview', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  const demos = bank.demo_fabrics || [];
  const demoOptions = demos.map((f) => {
    const sel = f.id === hopState.fabricPreview.demoFabricId ? ' selected' : '';
    return `<option value="${foEscapeText(f.id)}"${sel}>${foEscapeText(f.name)} (${foEscapeText(f.category)})</option>`;
  }).join('');

  const body = `
    <div class="hop-fabric-banner nx-card">
      <strong>DEMO mode</strong> — free partner pitch. Paid AI render unlocks after budget approval.
      <span class="nx-text-dim">Engine: ${foEscapeText(bank.engine || 'demo')} · Same on app, web &amp; desktop</span>
    </div>
    <div class="hop-fabric-grid">
      <div class="nx-card hop-fabric-card">
        <h3>1. Client item (sofa / chair)</h3>
        <p class="nx-text-dim">Camera (phone/webcam) ya Files se photo</p>
        <div class="hop-photo-actions">
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopPickPhoto('hop-fabric-item-cam')">Camera</button>
          <button type="button" class="nx-btn" onclick="hopPickPhoto('hop-fabric-item-gal')">Files / Gallery</button>
        </div>
        <input id="hop-fabric-item-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-fabric-item-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-fabric-item-preview" class="hop-fabric-thumb nx-text-dim">No photo yet</div>
      </div>
      <div class="nx-card hop-fabric-card">
        <h3>2. Fabric</h3>
        <label class="hop-fabric-label">Demo bank (ready now)</label>
        <select id="hop-fabric-demo-select" onchange="hopFabricDemoChanged()">
          ${demoOptions}
        </select>
        <label class="hop-fabric-label">Ya fabric photo</label>
        <div class="hop-photo-actions">
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopPickPhoto('hop-fabric-swatch-cam')">Camera</button>
          <button type="button" class="nx-btn" onclick="hopPickPhoto('hop-fabric-swatch-gal')">Files / Gallery</button>
        </div>
        <input id="hop-fabric-swatch-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-fabric-swatch-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-fabric-swatch-preview" class="hop-fabric-thumb nx-text-dim">Optional fabric photo</div>
        <p class="nx-text-dim hop-fabric-hint">${foEscapeText(bank.hint || '')}</p>
      </div>
    </div>
    <div class="hop-fabric-actions">
      <button type="button" class="nx-btn nx-btn-primary" id="hop-fabric-render-btn" onclick="hopRunFabricPreview()">Render preview</button>
    </div>
    <p id="hop-fabric-status" class="nx-text-dim"></p>
    <div id="hop-fabric-result" class="hop-fabric-result hidden"></div>
  `;

  mount.innerHTML = hopModuleShell(
    'Field Sales',
    'Fabric Preview',
    'Sofa photo + fabric → instant demo render for the client',
    '',
    body,
  );

  const wireFabricFile = (camId, galId, previewId, storeKey) => {
    const onPick = (input) => {
      const file = input.files && input.files[0];
      const box = document.getElementById(previewId);
      if (!file) return;
      hopState.fabricPreview = hopState.fabricPreview || {};
      hopState.fabricPreview[storeKey] = file;
      const other = document.getElementById(input.id === camId ? galId : camId);
      if (other) other.value = '';
      if (box) {
        const url = URL.createObjectURL(file);
        box.innerHTML = `<img src="${url}" alt="Preview" />`;
      }
    };
    document.getElementById(camId)?.addEventListener('change', (e) => onPick(e.target));
    document.getElementById(galId)?.addEventListener('change', (e) => onPick(e.target));
  };
  wireFabricFile('hop-fabric-item-cam', 'hop-fabric-item-gal', 'hop-fabric-item-preview', 'itemFile');
  wireFabricFile('hop-fabric-swatch-cam', 'hop-fabric-swatch-gal', 'hop-fabric-swatch-preview', 'fabricFile');
}

function hopFabricDemoChanged() {
  const sel = document.getElementById('hop-fabric-demo-select');
  if (!sel) return;
  hopState.fabricPreview = hopState.fabricPreview || {};
  hopState.fabricPreview.demoFabricId = sel.value;
  hopState.fabricPreview.fabricLabel = sel.options[sel.selectedIndex]?.text || sel.value;
}

async function hopRunFabricPreview() {
  const status = document.getElementById('hop-fabric-status');
  const result = document.getElementById('hop-fabric-result');
  const btn = document.getElementById('hop-fabric-render-btn');
  const demoSel = document.getElementById('hop-fabric-demo-select');
  const itemFile = hopState.fabricPreview?.itemFile
    || document.getElementById('hop-fabric-item-cam')?.files?.[0]
    || document.getElementById('hop-fabric-item-gal')?.files?.[0];
  if (!itemFile) {
    if (status) status.textContent = 'Pehle sofa / chair ki photo lo (Camera ya Gallery).';
    return;
  }

  const form = new FormData();
  form.append('item_image', itemFile);
  const fabricFile = hopState.fabricPreview?.fabricFile
    || document.getElementById('hop-fabric-swatch-cam')?.files?.[0]
    || document.getElementById('hop-fabric-swatch-gal')?.files?.[0];
  if (fabricFile) {
    form.append('fabric_image', fabricFile);
    form.append('fabric_label', fabricFile.name || 'Fabric photo');
  } else if (demoSel?.value) {
    form.append('demo_fabric_id', demoSel.value);
    form.append('fabric_label', demoSel.options[demoSel.selectedIndex]?.text || demoSel.value);
  } else {
    if (status) status.textContent = 'Demo fabric choose karo ya fabric photo lo.';
    return;
  }

  if (status) status.textContent = 'Rendering demo preview…';
  if (result) {
    result.classList.add('hidden');
    result.innerHTML = '';
  }
  if (btn) btn.disabled = true;

  try {
    const response = await fetchWithAuth('/api/v1/hop/fabric-preview/render', {
      method: 'POST',
      body: form,
    });
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Render failed'));
    }
    const payload = data.data || {};
    const src = `data:${payload.mime || 'image/jpeg'};base64,${payload.image_base64}`;
    if (status) {
      status.textContent = payload.note || 'Done';
    }
    if (result) {
      result.classList.remove('hidden');
      result.innerHTML = `
        <div class="nx-card hop-fabric-result-card">
          <div class="hop-fabric-result-meta">
            <span>${foEscapeText(payload.fabric_label || 'Fabric')}</span>
            <span class="nx-text-dim">${foEscapeText(payload.engine || 'demo')} · ${foEscapeText(payload.fabric_source || '')}</span>
          </div>
          <img src="${src}" alt="Fabric preview result" />
          <p class="nx-text-dim">Client ko phone pe dikhao. Paid AI baad me same button se unlock hoga.</p>
        </div>`;
      requestAnimationFrame(() => hopScrollIntoMain(result, 8));
    }
  } catch (e) {
    if (status) status.textContent = e.message || 'Render failed';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------- Orders / Dispatch / Invoices / Payments / Complaints ---------- */
async function renderHopOrdersModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/orders') || []; hopState.orders = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Orders', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const totalVal = rows.reduce((s, r) => s + Number(r.order_value || 0), 0);
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Orders', value: rows.length, tone: 'unpaid' },
      { label: 'Total Value', valueHtml: hopMoney(totalVal), tone: 'total', op: '=' },
    ])}
    ${hopTable(
      ['PO No', 'Client', 'Project', 'Value', 'Supplier', 'Expected Delivery', 'Production', 'Dispatch', 'Invoice', 'Won At', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.po_number)}</td><td>${hopCell(r.client_name || r.customer_company)}</td>
        <td>${hopCell(r.project_name)}</td><td class="inv-num">${hopMoney(r.order_value)}</td><td>${hopCell(r.supplier || r.vendor_company)}</td>
        <td>${hopCell(r.expected_delivery)}</td>
        <td><select onchange="hopPatchOrder(${r.id}, 'production_status', this.value)">
          ${['pending','ordered','in_production','qc','packed','ready','completed','delayed'].map((s) => `<option value="${s}"${s === r.production_status ? ' selected' : ''}>${s}</option>`).join('')}
        </select></td>
        <td>${hopCell(r.dispatch_status)}</td><td>${hopCell(r.invoice_status)}</td><td>${hopCell((r.won_at || '').slice(0, 10))}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Hub</button>` : '—'}</td>
      </tr>`).join(''),
      { label: 'Orders / PO', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Sales Ops', 'Orders / PO', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('order')">+ New Order</button>`, body);
}

async function hopPatchOrder(id, field, value) {
  try {
    await hopApi(`/api/v1/hop/orders/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [field]: value }) });
    openHopView('orders');
  } catch (e) { alert(e.message); }
}

async function renderHopDispatchesModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/dispatches') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Dispatch', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const pending = rows.filter((r) => !r.delivered_at && String(r.delivery_status || '').toLowerCase() !== 'delivered').length;
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Dispatches', value: rows.length, tone: 'unpaid' },
      { label: 'In Transit / Open', value: pending, tone: 'overdue', op: '+' },
    ])}
    ${hopTable(
      ['Project', 'PO', 'Status', 'Tracking', 'Courier', 'Delivery', 'Dispatched', 'Delivered', 'E-way', 'Docket', 'POD', 'Install'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.po_number)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.delivery_status)}</td>
        <td>${hopCell(r.dispatched_at)}</td><td>${hopCell(r.delivered_at)}</td>
        <td>${hopCell(r.eway_bill)}</td><td>${hopCell(r.docket_number)}</td>
        <td>${r.pod_received ? 'Yes' : '—'}</td><td>${r.installation_pending ? 'Pending' : '—'}</td>
      </tr>`).join(''),
      { label: 'Dispatch', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Dispatch', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('dispatch')">+ New Dispatch</button>`, body);
}

function hopDateYmd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function hopInvoicePeriodRange(period) {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  if (period === 'all') return { from: '', to: '' };
  if (period === 'today') {
    const t = hopDateYmd(now);
    return { from: t, to: t };
  }
  if (period === 'this_week') {
    const day = (now.getDay() + 6) % 7; // Mon=0
    const start = new Date(y, m, now.getDate() - day);
    return { from: hopDateYmd(start), to: hopDateYmd(now) };
  }
  if (period === 'last_month') {
    const start = new Date(y, m - 1, 1);
    const end = new Date(y, m, 0);
    return { from: hopDateYmd(start), to: hopDateYmd(end) };
  }
  if (period === 'this_quarter') {
    const q = Math.floor(m / 3) * 3;
    return { from: hopDateYmd(new Date(y, q, 1)), to: hopDateYmd(now) };
  }
  if (period === 'this_year') {
    return { from: `${y}-01-01`, to: hopDateYmd(now) };
  }
  // this_month (default)
  return { from: hopDateYmd(new Date(y, m, 1)), to: hopDateYmd(new Date(y, m + 1, 0)) };
}

function hopIsPaymentVoucherKind(kind) {
  const k = String(kind || hopState.saleDocKind || hopState.view || '').trim();
  // Payment-In (Sale) + Payment-Out (Purchase) — Vyapar: Used / Unused, not Paid / Unpaid.
  return k === 'purchase_payment_out'
    || k === 'sale_payment_in'
    || k === 'payments';
}

function hopInvoiceEffectiveStatus(r) {
  if (HOP_TXN_ESTIMATE.has(Number(r.txn_type))) {
    const s = String(r.status || r.notes || 'approved').toLowerCase();
    if (s.includes('cancel')) return 'cancelled';
    if (s.includes('partial')) return 'partial';
    if (s.includes('unpaid') || s.includes('open')) return 'approved';
    return s.includes('approved') || s.includes('sent') ? s.replace(/\s+/g, '') : (s || 'approved');
  }
  const bal = Number(r.balance || 0);
  const paid = Number(r.paid_amount || 0);
  const amount = Number(r.amount || 0);
  const due = String(r.due_date || '').slice(0, 10);
  const today = hopDateYmd(new Date());
  // Payment-In / Payment-Out: Used = fully applied, Unused = balance left (Vyapar wording).
  if (hopIsPaymentVoucherKind() || HOP_TXN_PAYMENT_IN.has(Number(r.txn_type)) || HOP_TXN_PAYMENT_OUT.has(Number(r.txn_type))) {
    if (bal <= 0.009) return 'paid'; // Used
    if (paid > 0 && bal > 0) return 'partial';
    return 'unpaid'; // Unused
  }
  if (bal <= 0.009 || String(r.status || '').toLowerCase() === 'paid') return 'paid';
  if (due && due < today && bal > 0) return 'overdue';
  if (paid > 0 && bal > 0) return 'partial';
  if (amount > 0 && paid <= 0) return 'unpaid';
  return String(r.status || 'open').toLowerCase() || 'open';
}

function hopInvoiceStatusBadge(status) {
  const s = String(status || 'open').toLowerCase();
  let label;
  if (hopIsPaymentVoucherKind()) {
    if (s === 'paid') label = 'Used';
    else if (s === 'unpaid' || s === 'open') label = 'Unused';
    else if (s === 'partial') label = 'Partial';
    else label = s.charAt(0).toUpperCase() + s.slice(1);
  } else {
    label = s === 'partial' ? 'Partial' : s === 'unpaid' ? 'Unpaid' : s.charAt(0).toUpperCase() + s.slice(1);
  }
  return `<span class="inv-badge inv-badge--${foEscapeAttr(s)}">${foEscapeText(label)}</span>`;
}

function hopFilteredInvoices() {
  const ui = hopState.invoiceUi || {};
  const q = String(ui.q || '').trim().toLowerCase();
  const party = String(ui.party || '').trim().toLowerCase();
  const status = String(ui.status || 'all');
  const from = String(ui.from || '').slice(0, 10);
  const to = String(ui.to || '').slice(0, 10);
  return (hopState.invoices || []).filter((r) => {
    const d = String(r.invoice_date || r.created_at || '').slice(0, 10);
    if (from && d && d < from) return false;
    if (to && d && d > to) return false;
    if (!d && (from || to) && ui.period !== 'all') return false;
    const eff = hopInvoiceEffectiveStatus(r);
    if (status !== 'all') {
      if (status === 'open' && !['open', 'unpaid', 'partial', 'overdue'].includes(eff)) return false;
      else if (status !== 'open' && eff !== status) return false;
    }
    if (party) {
      const name = String(r.customer_company || r.party_name || r.client_name || '').toLowerCase();
      if (!name.includes(party)) return false;
    }
    if (q) {
      const hay = [
        r.invoice_no, r.customer_company, r.project_name, r.status, r.notes, r.due_date,
      ].map((x) => String(x || '').toLowerCase()).join(' ');
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function hopInvoiceSummary(rows) {
  let total = 0;
  let received = 0;
  let balance = 0;
  let overdue = 0;
  rows.forEach((r) => {
    total += Number(r.amount || 0);
    received += Number(r.paid_amount || 0);
    const bal = Number(r.balance || 0);
    balance += bal;
    if (hopInvoiceEffectiveStatus(r) === 'overdue') overdue += bal;
  });
  const unpaid = Math.max(0, balance - overdue);
  return { total, received, balance, overdue, unpaid, count: rows.length };
}

function hopRenderInvoiceRows(rows) {
  const meta = hopSaleDocMeta();
  const empty = meta.empty || 'No invoices in this filter.';
  if (!rows.length) {
    const total = (hopState.invoices || []).length;
    const tip = total > 0
      ? `${empty} (${total} total outside this date/status filter — switch period to All Time.)`
      : empty;
    return `<tr><td colspan="8" class="inv-empty">${foEscapeText(tip)}</td></tr>`;
  }
  return rows.map((r) => {
    const eff = hopInvoiceEffectiveStatus(r);
    const party = r.customer_company || r.party_name || r.client_name || '';
    const partyTxnId = Number(r.party_txn_id || 0) || 0;
    const sourceTxnId = Number(r.source_txn_id || 0) || 0;
    const canPreview = partyTxnId > 0 || sourceTxnId > 0;
    const click = canPreview
      ? ` class="inv-row is-clickable" role="button" tabindex="0" title="Click to preview"
         onclick="hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId})"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId});}"`
      : ' class="inv-row"';
    return `<tr${click}>
      <td class="inv-date">${hopCell(String(r.invoice_date || '').slice(0, 10))}</td>
      <td class="inv-no" title="${foEscapeAttr(r.invoice_no || '')}">${hopCell(r.invoice_no)}</td>
      <td>${hopCell(party)}</td>
      <td class="inv-num">${hopMoney(r.amount)}</td>
      <td class="inv-num">${hopMoney(r.balance)}</td>
      <td>${hopCell(r.due_date)}</td>
      <td>${hopInvoiceStatusBadge(eff)}</td>
      <td class="inv-actions" onclick="event.stopPropagation()">
        ${canPreview ? `<button type="button" class="inv-ico-btn" title="Preview" onclick="hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId})">👁</button>` : ''}
        <button type="button" class="inv-ico-btn" title="Record payment" onclick="hopShowForm('payment')">₹</button>
      </td>
    </tr>`;
  }).join('');
}

function hopRefreshInvoiceUi() {
  const rows = hopFilteredInvoices();
  const sum = hopInvoiceSummary(rows);
  const tbody = document.getElementById('inv-tbody');
  if (tbody) tbody.innerHTML = hopRenderInvoiceRows(rows);
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('inv-sum-paid', hopMoney(sum.received));
  setTxt('inv-sum-unpaid', hopMoney(sum.unpaid));
  setTxt('inv-sum-overdue', hopMoney(sum.overdue));
  setTxt('inv-sum-total', hopMoney(sum.total));
  setTxt('inv-sum-count', `${sum.count} txns`);
}

function hopInvoiceOnPeriodChange() {
  const period = document.getElementById('inv-period')?.value || 'this_month';
  hopState.invoiceUi.period = period;
  if (period !== 'custom') {
    const range = hopInvoicePeriodRange(period);
    hopState.invoiceUi.from = range.from;
    hopState.invoiceUi.to = range.to;
    const fromEl = document.getElementById('inv-from');
    const toEl = document.getElementById('inv-to');
    if (fromEl) fromEl.value = range.from;
    if (toEl) toEl.value = range.to;
  }
  hopRefreshInvoiceUi();
}

function hopInvoiceApplyFilters() {
  const ui = hopState.invoiceUi;
  ui.period = document.getElementById('inv-period')?.value || ui.period;
  ui.from = document.getElementById('inv-from')?.value || '';
  ui.to = document.getElementById('inv-to')?.value || '';
  ui.status = document.getElementById('inv-status')?.value || 'all';
  // Party filter uses selected value (exact), not the search box text.
  ui.party = hopState.invoiceUi.party || '';
  ui.q = document.getElementById('inv-search')?.value || '';
  if (ui.from || ui.to) {
    const preset = hopInvoicePeriodRange(ui.period);
    if (ui.period !== 'custom' && (ui.from !== preset.from || ui.to !== preset.to)) {
      ui.period = 'custom';
      const periodEl = document.getElementById('inv-period');
      if (periodEl) periodEl.value = 'custom';
    }
  }
  hopRefreshInvoiceUi();
}

function hopInvoicePartyLabelSync() {
  const label = document.getElementById('inv-party-label');
  if (!label) return;
  const party = String(hopState.invoiceUi?.party || '').trim();
  label.textContent = party || 'All Parties';
  label.classList.toggle('is-placeholder', !party);
}

function hopInvoicePartyCloseMenu() {
  const panel = document.getElementById('inv-party-panel');
  if (panel) panel.classList.add('hidden');
  const toggle = document.getElementById('inv-party-toggle');
  if (toggle) {
    toggle.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  }
}

function hopInvoicePartyToggle(ev) {
  ev?.preventDefault?.();
  ev?.stopPropagation?.();
  const panel = document.getElementById('inv-party-panel');
  if (!panel) return;
  if (panel.classList.contains('hidden')) hopInvoicePartyOpenMenu();
  else hopInvoicePartyCloseMenu();
}

function hopInvoicePartyOpenMenu() {
  const panel = document.getElementById('inv-party-panel');
  const toggle = document.getElementById('inv-party-toggle');
  const search = document.getElementById('inv-party-q');
  if (!panel || !toggle) return;
  // Escape overflow:hidden ancestors so the dropdown is visible.
  if (panel.parentElement !== document.body) document.body.appendChild(panel);
  const rect = toggle.getBoundingClientRect();
  const width = Math.max(rect.width, 280);
  let left = rect.left;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
  panel.style.position = 'fixed';
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(rect.bottom + 4)}px`;
  panel.style.width = `${Math.round(width)}px`;
  panel.style.zIndex = '5000';
  if (search) search.value = '';
  hopInvoicePartyRenderMenu('');
  panel.classList.remove('hidden');
  toggle.classList.add('is-open');
  toggle.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => search?.focus());
}

function hopInvoicePartyRenderMenu(query) {
  const menu = document.getElementById('inv-party-menu');
  if (!menu) return;
  const q = String(query || '').trim().toLowerCase();
  const all = hopState.invoicePartyNames || [];
  const selected = String(hopState.invoiceUi?.party || '').trim();
  const matches = q ? all.filter((p) => p.toLowerCase().includes(q)) : all;
  const rows = [];
  rows.push(`<button type="button" class="inv-party-opt${!selected ? ' is-active' : ''}" data-party="" onclick="hopInvoicePartySelect('')">All Parties</button>`);
  matches.slice(0, 100).forEach((p) => {
    const active = selected === p ? ' is-active' : '';
    rows.push(`<button type="button" class="inv-party-opt${active}" data-party="${foEscapeAttr(p)}" onclick="hopInvoicePartySelect(this.getAttribute('data-party') || '')">${foEscapeText(p)}</button>`);
  });
  if (!matches.length && q) {
    rows.push(`<div class="inv-party-empty">No party match</div>`);
  }
  menu.innerHTML = rows.join('');
}

function hopInvoicePartyOnInput() {
  const q = document.getElementById('inv-party-q')?.value || '';
  hopInvoicePartyRenderMenu(q);
  const panel = document.getElementById('inv-party-panel');
  if (panel?.classList.contains('hidden')) hopInvoicePartyOpenMenu();
}

function hopInvoicePartySelect(name) {
  hopState.invoiceUi.party = name || '';
  hopInvoicePartyLabelSync();
  hopInvoicePartyCloseMenu();
  hopInvoiceApplyFilters();
}

function hopInvoiceResetFilters() {
  hopState.invoiceUi = { period: 'this_month', from: '', to: '', status: 'all', q: '', party: '' };
  const range = hopInvoicePeriodRange('this_month');
  hopState.invoiceUi.from = range.from;
  hopState.invoiceUi.to = range.to;
  const periodEl = document.getElementById('inv-period');
  const fromEl = document.getElementById('inv-from');
  const toEl = document.getElementById('inv-to');
  const statusEl = document.getElementById('inv-status');
  const searchEl = document.getElementById('inv-search');
  const partyQ = document.getElementById('inv-party-q');
  if (periodEl) periodEl.value = 'this_month';
  if (fromEl) fromEl.value = range.from;
  if (toEl) toEl.value = range.to;
  if (statusEl) statusEl.value = 'all';
  if (searchEl) searchEl.value = '';
  if (partyQ) partyQ.value = '';
  hopInvoicePartyLabelSync();
  hopInvoicePartyCloseMenu();
  hopRefreshInvoiceUi();
}

function hopInvoiceExportCsv() {
  const rows = hopFilteredInvoices();
  const meta = hopSaleDocMeta();
  const header = ['Date', meta.numberLabel || 'Number', 'Party', 'Project', 'Amount', 'Paid', 'Balance', 'Due', 'Status'];
  const lines = [header.join(',')];
  rows.forEach((r) => {
    const cells = [
      r.invoice_date, r.invoice_no, r.customer_company, r.project_name,
      r.amount, r.paid_amount, r.balance, r.due_date, hopInvoiceEffectiveStatus(r),
    ].map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`);
    lines.push(cells.join(','));
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const slug = String(meta.title || 'sale-docs').toLowerCase().replace(/[^a-z0-9]+/g, '-');
  a.download = `hop-${slug}-${hopDateYmd(new Date())}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function renderHopInvoicesModule(mount) {
  return renderHopSaleDocListModule(mount, 'invoices');
}

async function renderHopPaymentsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/payments') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Payments', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const total = rows.reduce((s, r) => s + Number(r.amount || 0), 0);
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Payments', value: rows.length, tone: 'unpaid' },
      { label: 'Collected', valueHtml: hopMoney(total), tone: 'paid', op: '=' },
    ])}
    ${hopTable(
      ['Paid At', 'Invoice', 'Customer', 'Project', 'Amount', 'Method', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.paid_at || '').slice(0, 16))}</td><td>${hopCell(r.invoice_no)}</td>
        <td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td class="inv-num">${hopMoney(r.amount)}</td><td>${hopCell(r.method)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
      { label: 'Payment-In', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Payments', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('payment')">+ Record Payment</button>`, body);
}

async function renderHopComplaintsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/complaints') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Support', 'Complaints', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const open = rows.filter((r) => !/closed|resolved|done/i.test(String(r.status || ''))).length;
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Total', value: rows.length, tone: 'total' },
      { label: 'Open', value: open, tone: 'overdue', op: '+' },
    ])}
    ${hopTable(
      ['Date', 'Customer', 'Project', 'Issue', 'Assigned', 'Status', 'Resolution Hrs', 'Feedback'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.complaint_date)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.issue)}</td><td>${hopCell(r.assigned_to)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.resolution_time_hours)}</td><td>${hopCell(r.feedback)}</td>
      </tr>`).join(''),
      { label: 'Complaints', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Support', 'Complaints & After Sales', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('complaint')">+ New Complaint</button>`, body);
}

/* ---------- Reports ---------- */
async function renderHopPipelineModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/lead_pipeline') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Lead Pipeline', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const k = data.kpis || {};
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Conversion %', k.conversion_rate_pct, null)}
      ${renderHopKpiCard('Win Ratio %', k.win_ratio_pct, null)}
      ${renderHopKpiCard('Open Value', k.total_open_value, null)}
      ${renderHopKpiCard('Avg Sales Cycle', k.average_sales_cycle_days, null)}
    </div>
    ${hopTable(['Stage', 'Count', 'Value'],
      (data.stages || []).map((r) => `<tr><td>${hopCell(r.stage)}</td><td>${hopCell(r.count)}</td><td>${hopMoney(r.value)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Lead Pipeline', 'Stage × count × value', '', body);
}

async function renderHopFunnelModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/reports/funnel') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Sales Funnel', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <p class="nx-text-dim">Every project sits in exactly one funnel stage.</p>
    <div class="hop-funnel">
      ${rows.map((r) => `
        <div class="hop-funnel-step">
          <div class="hop-funnel-label">${foEscapeText(r.stage)}</div>
          <div class="hop-funnel-count">${foEscapeText(String(r.count))}</div>
          <div class="hop-funnel-value">${hopMoney(r.value)}</div>
        </div>`).join('<div class="hop-funnel-arrow">↓</div>')}
    </div>
    ${hopTable(['Stage', 'Projects', 'Value'],
      rows.map((r) => `<tr><td>${hopCell(r.stage)}</td><td>${hopCell(r.count)}</td><td>${hopMoney(r.value)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Sales Funnel', 'Lead → … → Payment', '', body);
}

async function renderHopCommissionModule(mount) {
  if (!hopState.commissionUi) {
    hopState.commissionUi = { q: '', selectedId: null, sheet: null };
  }
  const ui = hopState.commissionUi;
  let invoices = [];
  try {
    const qs = ui.q ? `?q=${encodeURIComponent(ui.q)}` : '';
    invoices = await hopApi(`/api/v1/hop/commission/invoices${qs}`) || [];
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  let sheet = ui.sheet;
  if (ui.selectedId) {
    try {
      sheet = await hopApi(`/api/v1/hop/commission/worksheet?party_txn_id=${ui.selectedId}`) || null;
      ui.sheet = sheet;
    } catch (e) {
      sheet = null;
      ui.sheet = null;
    }
  }

  const bill = sheet?.bill || null;
  const entry = sheet?.entry || {};
  const cPct = entry.commission_pct != null ? entry.commission_pct : '';
  const tPct = entry.tds_pct != null ? entry.tds_pct : '';
  const notes = entry.notes || '';

  const worksheetHtml = bill ? `
    <div class="nx-card hop-form-card hop-comm-sheet">
      <div class="hop-comm-bill-head">
        <div>
          <div class="hop-doc-meta-label">Selected bill</div>
          <div class="hop-doc-party-name">Invoice ${foEscapeText(bill.invoice_no)} · ${foEscapeText(bill.party_name)}</div>
          <div class="hop-doc-muted">${foEscapeText(bill.invoice_date || '')} · ${foEscapeText(bill.status || '')}</div>
        </div>
        <button type="button" class="nx-btn" onclick="hopOpenSaleDocPreview(${Number(bill.party_txn_id) || 0}, ${Number(bill.source_txn_id) || 0})">Preview</button>
      </div>
      ${hopTxCards([
        { label: 'Invoice Total', valueHtml: hopMoney(bill.invoice_total), tone: 'total' },
        { label: 'Tax', valueHtml: hopMoney(bill.tax_amount), tone: 'neutral' },
        { label: 'Before Tax', valueHtml: hopMoney(bill.amount_before_tax), tone: 'unpaid' },
      ])}
      <div class="hop-form-grid" style="margin-top:12px">
        <label>Commission %
          <input id="hop-comm-pct" type="number" min="0" step="0.01" value="${foEscapeAttr(String(cPct))}"
            oninput="hopCommissionRecalc()" />
        </label>
        <label>TDS %
          <input id="hop-comm-tds" type="number" min="0" step="0.01" value="${foEscapeAttr(String(tPct))}"
            oninput="hopCommissionRecalc()" />
        </label>
        <label class="hop-form-span-2">Notes
          <input id="hop-comm-notes" type="text" value="${foEscapeAttr(notes)}" placeholder="Optional" />
        </label>
      </div>
      ${hopTxCards([
        { label: 'Commission', valueHtml: `<span id="hop-comm-amt">${hopMoney(entry.commission_amount || 0)}</span>`, tone: 'paid', id: null },
        { label: 'TDS', valueHtml: `<span id="hop-comm-tds-amt">${hopMoney(entry.tds_amount || 0)}</span>`, tone: 'overdue' },
        { label: 'Net Payable', valueHtml: `<span id="hop-comm-net">${hopMoney(entry.net_commission || 0)}</span>`, tone: 'unpaid' },
      ])}
      <p class="nx-text-dim hop-comm-rule" style="margin-top:8px">
        Commission = Before Tax × Comm% · TDS = Commission × TDS% · Net = Commission − TDS
      </p>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopCommissionSave()">Save against this bill</button>
        <span id="hop-comm-status" class="nx-text-dim"></span>
      </div>
    </div>` : `
    <div class="nx-card hop-form-card">
      <p class="nx-text-dim">Neeche list se bill select karo — phir Commission % aur TDS % fill karo.</p>
    </div>`;

  const body = `
    <p class="nx-text-dim">Har tax invoice pe apna <strong>Commission %</strong> aur <strong>TDS %</strong> daalo. Amount before tax pe calculate hota hai.</p>
    ${worksheetHtml}
    ${hopTable(
      ['Date', 'Invoice', 'Party', 'Total', 'Comm %', 'TDS %', 'Net Comm', ''],
      invoices.map((r) => {
        const sel = Number(ui.selectedId) === Number(r.party_txn_id);
        return `<tr class="inv-row${sel ? ' is-selected' : ''}" style="cursor:pointer"
          onclick="hopCommissionSelectBill(${Number(r.party_txn_id) || 0})">
          <td>${hopCell(r.invoice_date)}</td>
          <td>${hopCell(r.invoice_no)}</td>
          <td>${hopCell(r.party_name)}</td>
          <td class="inv-num">${hopMoney(r.invoice_total)}</td>
          <td class="inv-num">${r.has_entry ? hopCell(r.commission_pct) + '%' : '—'}</td>
          <td class="inv-num">${r.has_entry ? hopCell(r.tds_pct) + '%' : '—'}</td>
          <td class="inv-num">${r.has_entry ? hopMoney(r.net_commission) : '—'}</td>
          <td>${r.has_entry ? '<span class="hop-doc-muted">Saved</span>' : '<span class="hop-doc-muted">Set</span>'}</td>
        </tr>`;
      }).join(''),
      {
        label: 'Tax invoices',
        count: invoices.length,
        searchPlaceholder: 'Search party / invoice…',
        searchValue: ui.q || '',
        searchId: 'hop-comm-q',
      },
    )}`;

  mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', body);

  const search = document.getElementById('hop-comm-q');
  if (search) {
    search.oninput = () => {
      // live filter table only
      hopFilterListTable(search, search.closest('.inv-table-wrap')?.querySelector('tbody')?.id || '');
    };
    search.onkeydown = (ev) => {
      if (ev.key === 'Enter') {
        hopState.commissionUi.q = search.value.trim();
        openHopView('commission', { skipHistory: true });
      }
    };
  }
  hopCommissionRecalc();
}

function hopCommissionSelectBill(partyTxnId) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.selectedId = Number(partyTxnId) || null;
  hopState.commissionUi.sheet = null;
  openHopView('commission', { skipHistory: true });
}

function hopCommissionRecalc() {
  const sheet = hopState.commissionUi?.sheet;
  const before = Number(sheet?.bill?.amount_before_tax || 0);
  const cPct = Number(document.getElementById('hop-comm-pct')?.value || 0);
  const tPct = Number(document.getElementById('hop-comm-tds')?.value || 0);
  const commission = Math.round(before * (Number.isFinite(cPct) ? cPct : 0) / 100 * 100) / 100;
  const tds = Math.round(commission * (Number.isFinite(tPct) ? tPct : 0) / 100 * 100) / 100;
  const net = Math.round((commission - tds) * 100) / 100;
  const amt = document.getElementById('hop-comm-amt');
  const tdsEl = document.getElementById('hop-comm-tds-amt');
  const netEl = document.getElementById('hop-comm-net');
  if (amt) amt.innerHTML = hopMoney(commission);
  if (tdsEl) tdsEl.innerHTML = hopMoney(tds);
  if (netEl) netEl.innerHTML = hopMoney(net);
}

async function hopCommissionSave() {
  const id = hopState.commissionUi?.selectedId;
  if (!id) return;
  const status = document.getElementById('hop-comm-status');
  if (status) status.textContent = 'Saving…';
  try {
    const data = await hopApi('/api/v1/hop/commission/worksheet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        party_txn_id: id,
        commission_pct: Number(document.getElementById('hop-comm-pct')?.value || 0),
        tds_pct: Number(document.getElementById('hop-comm-tds')?.value || 0),
        notes: document.getElementById('hop-comm-notes')?.value || '',
      }),
    });
    hopState.commissionUi.sheet = data;
    if (status) status.textContent = 'Saved';
    if (typeof nexoraToast === 'function') nexoraToast('Commission saved against bill', 'ok');
    openHopView('commission', { skipHistory: true });
  } catch (e) {
    if (status) status.textContent = e.message || 'Save failed';
    if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Save failed', 'error');
  }
}

async function renderHopReceivablesModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/receivables') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Receivables', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const a = data.ageing || {};
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('0–30 Days', a['0_30'], null)}
      ${renderHopKpiCard('31–60 Days', a['31_60'], null)}
      ${renderHopKpiCard('61–90 Days', a['61_90'], null)}
      ${renderHopKpiCard('90+ Days', a['90_plus'], null)}
    </div>
    <h3 class="hop-section-title">Highest outstanding</h3>
    ${hopTable(['Customer', 'Outstanding'],
      (data.top_customers || []).map((r) => `<tr><td>${hopCell(r.customer)}</td><td>${hopMoney(r.outstanding)}</td></tr>`).join(''))}
    <h3 class="hop-section-title">Open invoices</h3>
    ${hopTable(['Invoice', 'Customer', 'Balance', 'Due'],
      (data.invoices || []).map((r) => `<tr><td>${hopCell(r.invoice_no)}</td><td>${hopCell(r.customer_company)}</td><td>${hopMoney(r.balance)}</td><td>${hopCell(r.due_date)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Receivable Ageing', '', '', body);
}

async function renderHopCustomerDashModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/reports/customers') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Customer Dashboard', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = hopTable(
    ['Company', 'City', 'Rating', 'Total Business', 'Projects', 'AOV', 'Outstanding', 'Last Meeting', 'Last Purchase'],
    rows.map((r) => `<tr>
      <td>${hopCell(r.company)}</td><td>${hopCell(r.city)}</td><td>${hopCell(r.potential_rating)}</td>
      <td>${hopMoney(r.total_business)}</td><td>${hopCell(r.projects)}</td><td>${hopMoney(r.average_order_value)}</td>
      <td>${hopMoney(r.outstanding)}</td><td>${hopCell((r.last_meeting || '').slice(0, 10))}</td>
      <td>${hopCell((r.last_purchase || '').slice(0, 10))}</td>
    </tr>`).join(''),
  );
  mount.innerHTML = hopModuleShell('Reports', 'Customer Dashboard', 'Business · projects · outstanding · last touch', '', body);
}

async function renderHopDailyModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/daily') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Daily Activity', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <p class="nx-text-dim">Day: ${foEscapeText(data.day || '')}</p>
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Leads', data.leads_created, null)}
      ${renderHopKpiCard('Meetings', data.meetings, null)}
      ${renderHopKpiCard('Samples', data.samples_sent, null)}
      ${renderHopKpiCard('Follow-ups', data.follow_ups, null)}
      ${renderHopKpiCard('Quotes', data.quotes_sent, null)}
      ${renderHopKpiCard('Orders Closed', data.orders_closed, null)}
      ${renderHopKpiCard('Collections', data.collections, null)}
      ${renderHopKpiCard('Calls', data.calls, null)}
    </div>`;
  mount.innerHTML = hopModuleShell('Reports', 'Daily Activity Report', '', '', body);
}

async function renderHopProfitModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/profit') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Profitability', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Revenue', data.revenue, null)}
      ${renderHopKpiCard('COGS', data.cogs, null)}
      ${renderHopKpiCard('Gross Profit', data.gross_profit, null)}
      ${renderHopKpiCard('Gross Margin %', data.gross_margin_pct, null)}
      ${renderHopKpiCard('Expenses', data.expenses, null)}
      ${renderHopKpiCard('Net Profit', data.net_profit, null)}
    </div>
    <p class="nx-text-dim">${foEscapeText((data.notes && data.notes.cogs) || '')}</p>`;
  mount.innerHTML = hopModuleShell('Reports', 'Profitability', 'Order & catalogue based', '', body);
}

async function renderHopTargetsModule(mount) {
  let snap = {};
  try { snap = await hopApi('/api/v1/hop/executive/snapshot') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Monthly Target', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const m = snap.monthly || {};
  const sales = Number(m.sales || 0);
  const target = Number(m.target || 0);
  const pending = Math.max(0, target - sales);
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Period', m.period_label, null)}
      ${renderHopKpiCard('Target', target, null)}
      ${renderHopKpiCard('Achieved', sales, null)}
      ${renderHopKpiCard('Pending / Gap', pending, null)}
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card">
      <strong>Set / update monthly target</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Period (YYYY-MM)<input id="hop-t-period" value="${foEscapeText(m.period_label || '')}" /></label>
        <label>Target Amount<input id="hop-t-amount" type="number" step="any" value="${target || ''}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveTarget()">Save Target</button>
      </div>
    </div>`;
  mount.innerHTML = hopModuleShell('Reports', 'Monthly Target', '', '', body);
}

async function hopSaveTarget() {
  try {
    await hopApi('/api/v1/hop/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        period_label: document.getElementById('hop-t-period')?.value,
        target_amount: document.getElementById('hop-t-amount')?.value,
      }),
    });
    openHopView('targets');
  } catch (e) { alert(e.message); }
}

/* ---------- Project Hub ---------- */
async function openHopProjectHub(projectId) {
  hopState.view = 'project-hub';
  hopHideAllViews();
  hopScrollMainToTop();
  document.getElementById('hop-view-project-hub')?.classList.remove('hidden');
  document.querySelectorAll('.hop-nav-btn[data-hop-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.hopView === 'projects');
  });
  await loadHopProjectHub(projectId);
}

async function loadHopProjectHub(projectId) {
  const body = document.getElementById('hop-hub-body');
  const tabs = document.getElementById('hop-hub-tabs');
  if (body) body.innerHTML = '<p class="nx-text-dim">Loading project hub…</p>';
  try {
    const hub = await hopApi(`/api/v1/hop/projects/${projectId}/hub`);
    hopState.hub = hub;
    const p = hub.project || {};
    document.getElementById('hop-hub-title').textContent = p.project_name || 'Project';
    document.getElementById('hop-hub-sub').textContent = [
      hub.customer?.company || p.customer_company,
      p.hotel_name,
      p.architect,
      p.consultant,
    ].filter(Boolean).join(' · ');
    const stageSel = document.getElementById('hop-hub-stage');
    if (stageSel) {
      stageSel.innerHTML = hopStageOptions(hub.funnel_stages || HOP_PROJECT_STAGES, p.stage);
    }
    const tabDefs = [
      ['overview', 'Overview'],
      ['meetings', 'Meetings'],
      ['samples', 'Samples'],
      ['vendors', 'Vendors'],
      ['quotations', 'Quotations'],
      ['orders', 'PO'],
      ['dispatches', 'Dispatch'],
      ['invoices', 'Invoices'],
      ['payments', 'Payments'],
      ['complaints', 'Complaints'],
      ['timeline', 'Timeline'],
    ];
    if (!tabDefs.find((t) => t[0] === hopState.hubTab)) hopState.hubTab = 'overview';
    tabs.innerHTML = tabDefs.map(([k, label]) => `
      <button type="button" class="hop-tab${hopState.hubTab === k ? ' active' : ''}" onclick="hopSetHubTab('${k}')">${label}</button>
    `).join('');
    renderHopHubTab();
  } catch (e) {
    if (body) body.innerHTML = `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`;
  }
}

function hopSetHubTab(tab) {
  hopState.hubTab = tab;
  hopScrollMainToTop();
  const tabs = document.getElementById('hop-hub-tabs');
  if (tabs && hopState.hub) {
    const tabDefs = [
      ['overview', 'Overview'], ['meetings', 'Meetings'], ['samples', 'Samples'], ['vendors', 'Vendors'],
      ['quotations', 'Quotations'], ['orders', 'PO'], ['dispatches', 'Dispatch'], ['invoices', 'Invoices'],
      ['payments', 'Payments'], ['complaints', 'Complaints'], ['timeline', 'Timeline'],
    ];
    tabs.innerHTML = tabDefs.map(([k, label]) => `
      <button type="button" class="hop-tab${hopState.hubTab === k ? ' active' : ''}" onclick="hopSetHubTab('${k}')">${label}</button>
    `).join('');
  }
  renderHopHubTab();
}

function renderHopHubTab() {
  const hub = hopState.hub;
  const body = document.getElementById('hop-hub-body');
  if (!hub || !body) return;
  const p = hub.project || {};
  const tab = hopState.hubTab;
  if (tab === 'overview') {
    body.innerHTML = `
      <div class="hop-hub-overview">
        <div class="nx-card hop-form-card">
          <div class="hop-form-grid">
            <div><span class="nx-text-faint">Customer</span><div>${hopCell(hub.customer?.company || p.customer_company)}</div></div>
            <div><span class="nx-text-faint">Hotel / Site</span><div>${hopCell(p.hotel_name)}</div></div>
            <div><span class="nx-text-faint">Site Address</span><div>${hopCell(p.site_address)}</div></div>
            <div><span class="nx-text-faint">Architect</span><div>${hopCell(p.architect)}</div></div>
            <div><span class="nx-text-faint">Consultant</span><div>${hopCell(p.consultant)}</div></div>
            <div><span class="nx-text-faint">Stage</span><div>${hopCell(p.stage)}</div></div>
            <div><span class="nx-text-faint">Project Value</span><div>${hopMoney(p.project_value ?? p.expected_value)}</div></div>
            <div><span class="nx-text-faint">Probability</span><div>${hopCell(p.probability_pct)}%</div></div>
            <div><span class="nx-text-faint">Completion</span><div>${hopCell(p.completion_pct)}%</div></div>
            <div><span class="nx-text-faint">Next Milestone</span><div>${hopCell(p.next_milestone)}</div></div>
            <div><span class="nx-text-faint">Issues</span><div>${hopCell(p.issues)}</div></div>
            <div><span class="nx-text-faint">Assigned</span><div>${hopCell(p.assigned_to)}</div></div>
          </div>
        </div>
        <div class="hop-kpi-grid hop-kpi-grid-sm">
          ${renderHopKpiCard('Meetings', (hub.meetings || []).length, null)}
          ${renderHopKpiCard('Quotations', (hub.quotations || []).length, null)}
          ${renderHopKpiCard('Orders', (hub.orders || []).length, null)}
          ${renderHopKpiCard('Invoices', (hub.invoices || []).length, null)}
        </div>
      </div>`;
    return;
  }
  if (tab === 'timeline') {
    const rows = hub.timeline || [];
    body.innerHTML = hopTable(
      ['When', 'Type', 'Title', 'Detail'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.activity_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td>${hopCell(r.activity_type)}</td><td>${hopCell(r.title)}</td><td>${hopCell(r.detail)}</td>
      </tr>`).join(''),
    );
    return;
  }
  const map = {
    meetings: {
      headers: ['When', 'Title', 'Outcome', 'Next Action'],
      rows: (hub.meetings || []).map((r) => `<tr><td>${hopCell((r.scheduled_at || '').slice(0, 16))}</td><td>${hopCell(r.title)}</td><td>${hopCell(r.outcome)}</td><td>${hopCell(r.next_action)}</td></tr>`),
    },
    samples: {
      headers: ['Sample', 'Sent', 'Tracking', 'Approval'],
      rows: (hub.samples || []).map((r) => `<tr><td>${hopCell(r.sample_name)}</td><td>${hopCell(r.sent_at)}</td><td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.approval_status)}</td></tr>`),
    },
    vendors: {
      headers: ['Product', 'Vendor', 'Rate', 'Winner'],
      rows: (hub.vendor_comparisons || []).map((r) => `<tr><td>${hopCell(r.product_name)}</td><td>${hopCell(r.vendor_company)}</td><td>${hopMoney(r.rate)}</td><td>${r.is_winner ? '★' : '—'}</td></tr>`),
    },
    quotations: {
      headers: ['Quote', 'Ver', 'Value', 'Status'],
      rows: (hub.quotations || []).map((r) => `<tr><td>${hopCell(r.quote_no)}</td><td>${hopCell(r.version)}</td><td>${hopMoney(r.value)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
    orders: {
      headers: ['PO', 'Value', 'Production', 'Dispatch'],
      rows: (hub.orders || []).map((r) => `<tr><td>${hopCell(r.po_number)}</td><td>${hopMoney(r.order_value)}</td><td>${hopCell(r.production_status)}</td><td>${hopCell(r.dispatch_status)}</td></tr>`),
    },
    dispatches: {
      headers: ['Status', 'Tracking', 'Courier', 'Delivery'],
      rows: (hub.dispatches || []).map((r) => `<tr><td>${hopCell(r.status)}</td><td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.delivery_status)}</td></tr>`),
    },
    invoices: {
      headers: ['Invoice', 'Amount', 'Balance', 'Status'],
      rows: (hub.invoices || []).map((r) => `<tr><td>${hopCell(r.invoice_no)}</td><td>${hopMoney(r.amount)}</td><td>${hopMoney(r.balance)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
    payments: {
      headers: ['Paid At', 'Amount', 'Method'],
      rows: (hub.payments || []).map((r) => `<tr><td>${hopCell((r.paid_at || '').slice(0, 16))}</td><td>${hopMoney(r.amount)}</td><td>${hopCell(r.method)}</td></tr>`),
    },
    complaints: {
      headers: ['Date', 'Issue', 'Status'],
      rows: (hub.complaints || []).map((r) => `<tr><td>${hopCell(r.complaint_date)}</td><td>${hopCell(r.issue)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
  };
  const block = map[tab];
  if (!block) {
    body.innerHTML = '<p class="nx-text-dim">Empty</p>';
    return;
  }
  body.innerHTML = hopTable(block.headers, block.rows.join(''));
}

async function hopChangeProjectStage() {
  const hub = hopState.hub;
  if (!hub?.project?.id) return;
  const stage = document.getElementById('hop-hub-stage')?.value;
  try {
    await hopApi(`/api/v1/hop/projects/${hub.project.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage }),
    });
    await loadHopProjectHub(hub.project.id);
  } catch (e) { alert(e.message); }
}

/* ---------- Forms ---------- */
async function hopShowForm(kind, editRow) {
  // Party create/edit uses Vyapar-style modal (Nexora theme).
  if (kind === 'customer' || kind === 'vendor') {
    hopOpenPartyEditModal(kind, editRow || null);
    return;
  }
  // Rate sheet form does not need CRM lookups — open immediately
  if (kind !== 'rate_sheet') {
    await hopEnsureLookups();
  }
  const slot = document.getElementById('hop-form-slot');
  if (!slot) {
    alert('Upload form area missing — refresh Vendor Compare (Ctrl+Shift+R).');
    return;
  }
  slot.classList.remove('hidden');
  hopState.contactEdit = null;
  if (editRow && editRow.id != null && (kind === 'customer' || kind === 'vendor')) {
    hopState.contactEdit = { kind, id: Number(editRow.id) };
  }
  const cancel = `hopState.contactEdit=null; document.getElementById('hop-form-slot').classList.add('hidden')`;

  if (kind === 'customer') {
    const row = editRow || {};
    slot.innerHTML = `
      <strong>${row.id ? 'Edit Customer' : 'New Customer'}</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Company *<input id="f-company" value="${foEscapeAttr(row.company || '')}" /></label>
        <label>Contact<input id="f-contact" value="${foEscapeAttr(row.contact_person || '')}" /></label>
        <label>Mobile<input id="f-mobile" value="${foEscapeAttr(row.mobile || '')}" /></label>
        <label>Email<input id="f-email" value="${foEscapeAttr(row.email || '')}" /></label>
        <label>City<input id="f-city" value="${foEscapeAttr(row.city || '')}" /></label>
        <label>Type<input id="f-type" placeholder="Hotel / Designer" value="${foEscapeAttr(row.customer_type || '')}" /></label>
        <label>Hotel Brand<input id="f-hotel" value="${foEscapeAttr(row.hotel_brand || '')}" /></label>
        <label>Architect<input id="f-architect" value="${foEscapeAttr(row.architect || '')}" /></label>
        <label>Consultant<input id="f-consultant" value="${foEscapeAttr(row.consultant || '')}" /></label>
        <label>Potential<input id="f-potential" type="number" value="${foEscapeAttr(row.annual_potential ?? '')}" /></label>
        <label>Rating A/B/C<input id="f-rating" value="${foEscapeAttr(row.potential_rating || '')}" /></label>
        <label>Assigned<input id="f-assigned" value="${foEscapeAttr(row.assigned_to || '')}" /></label>
        <label class="hop-form-span-2">Address<input id="f-address" value="${foEscapeAttr(row.address || '')}" /></label>
        <label>GST<input id="f-gst" value="${foEscapeAttr(row.gst_no || '')}" /></label>
        <label>PAN<input id="f-pan" value="${foEscapeAttr(row.pan || '')}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('customer')">${row.id ? 'Update' : 'Save'}</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'project') {
    slot.innerHTML = `
      <strong>New Project</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project Name *<input id="f-pname" /></label>
        <label>Customer<select id="f-pcustomer">${hopCustomerOptions()}</select></label>
        <label>Hotel<input id="f-photel" /></label>
        <label>Site Address<input id="f-psite" /></label>
        <label>Architect<input id="f-parch" /></label>
        <label>Consultant<input id="f-pcons" /></label>
        <label>Stage<select id="f-pstage">${hopStageOptions(HOP_PROJECT_STAGES, 'lead')}</select></label>
        <label>Value<input id="f-pvalue" type="number" /></label>
        <label>Probability %<input id="f-pprob" type="number" /></label>
        <label>Assigned<input id="f-passigned" /></label>
        <label>Next Milestone<input id="f-pmilestone" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('project')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'lead') {
    slot.innerHTML = `
      <strong>New Lead</strong>
      <p class="nx-text-dim" style="font-size:0.78rem;">No project selected → project auto-created.</p>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Customer<select id="f-lcustomer">${hopCustomerOptions()}</select></label>
        <label>Project<select id="f-lproject">${hopProjectOptions()}</select></label>
        <label>New Project Name<input id="f-lpname" placeholder="Holiday Inn Dwarka" /></label>
        <label>Source<input id="f-lsource" /></label>
        <label>Expected Value<input id="f-lvalue" type="number" /></label>
        <label>Priority<input id="f-lpriority" /></label>
        <label>Sales Person<input id="f-lassigned" /></label>
        <label>Stage<select id="f-lstage">${hopStageOptions(HOP_LEAD_STAGES, 'new_lead')}</select></label>
        <label>Probability %<input id="f-lprob" type="number" /></label>
        <label>Next Follow-up<input id="f-lfollow" type="date" /></label>
        <label>Expected Closure<input id="f-lclosure" type="date" /></label>
        <label class="hop-form-span-2">Products Interested<input id="f-lproducts" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('lead')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'meeting') {
    const nowLocal = new Date();
    nowLocal.setMinutes(nowLocal.getMinutes() - nowLocal.getTimezoneOffset());
    slot.innerHTML = `
      <strong>New Meeting</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Title<input id="f-mtitle" value="Client Meeting" /></label>
        <label>When *<input id="f-mwhen" type="datetime-local" value="${nowLocal.toISOString().slice(0, 16)}" /></label>
        <label>Customer<select id="f-mcustomer">${hopCustomerOptions()}</select></label>
        <label>Project<select id="f-mproject">${hopProjectOptions()}</select></label>
        <label>Location<input id="f-mloc" /></label>
        <label>Agenda<input id="f-magenda" /></label>
        <label>Outcome<input id="f-moutcome" /></label>
        <label>Next Action<input id="f-mnext" /></label>
        <label>Follow-up<input id="f-mfollow" type="date" /></label>
        <label>Expected Value<input id="f-mvalue" type="number" /></label>
        <label>Probability %<input id="f-mprob" type="number" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('meeting')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'quotation') {
    slot.innerHTML = `
      <strong>New Quotation</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-qproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-qcustomer">${hopCustomerOptions()}</select></label>
        <label>Value<input id="f-qvalue" type="number" /></label>
        <label>Margin %<input id="f-qmargin" type="number" /></label>
        <label>Status<select id="f-qstatus"><option>draft</option><option>sent</option><option>negotiation</option></select></label>
        <label>Sales Person<input id="f-qsales" /></label>
        <label>Expected Closure<input id="f-qclosure" type="date" /></label>
        <label>Payment Terms<input id="f-qpay" /></label>
        <label class="hop-form-span-2">Notes<input id="f-qnotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('quotation')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'vendor') {
    const row = editRow || {};
    slot.innerHTML = `
      <strong>${row.id ? 'Edit Vendor' : 'New Vendor'}</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Company *<input id="f-vcompany" value="${foEscapeAttr(row.company || '')}" /></label>
        <label>Products<input id="f-vproducts" value="${foEscapeAttr(row.products || '')}" /></label>
        <label>GST<input id="f-vgst" value="${foEscapeAttr(row.gst_no || '')}" /></label>
        <label>Contact<input id="f-vcontact" value="${foEscapeAttr(row.contact_person || '')}" /></label>
        <label>Mobile<input id="f-vmobile" value="${foEscapeAttr(row.mobile || '')}" /></label>
        <label>Email<input id="f-vemail" value="${foEscapeAttr(row.email || '')}" /></label>
        <label>City<input id="f-vcity" value="${foEscapeAttr(row.city || '')}" /></label>
        <label>Rating<input id="f-vrating" type="number" step="0.1" value="${foEscapeAttr(row.rating ?? '')}" /></label>
        <label>Lead Time Days<input id="f-vlead" type="number" value="${foEscapeAttr(row.lead_time_days ?? '')}" /></label>
        <label>Payment Terms<input id="f-vpay" value="${foEscapeAttr(row.payment_terms || '')}" /></label>
        <label>On-time %<input id="f-vontime" type="number" value="${foEscapeAttr(row.on_time_pct ?? '')}" /></label>
        <label>Quality Rating<input id="f-vqual" type="number" step="0.1" value="${foEscapeAttr(row.quality_rating ?? '')}" /></label>
        <label>Certificates<input id="f-vcert" value="${foEscapeAttr(row.certificates || '')}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('vendor')">${row.id ? 'Update' : 'Save'}</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'vendor_cmp') {
    slot.innerHTML = `
      <strong>Vendor Comparison Row</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project *<select id="f-vcproject">${hopProjectOptions()}</select></label>
        <label>Vendor *<select id="f-vcvendor">${hopVendorOptions()}</select></label>
        <label>Product<input id="f-vcproduct" /></label>
        <label>Rate<input id="f-vcrate" type="number" /></label>
        <label>Lead Time<input id="f-vclead" type="number" /></label>
        <label>MOQ<input id="f-vcmoq" /></label>
        <label>Quality<input id="f-vcqual" /></label>
        <label>Certification<input id="f-vccert" /></label>
        <label>Payment Terms<input id="f-vcpay" /></label>
        <label>Winner?<select id="f-vcwin"><option value="0">No</option><option value="1">Yes</option></select></label>
        <label class="hop-form-span-2">Recommendation<input id="f-vcrec" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('vendor_cmp')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'rate_sheet') {
    slot.innerHTML = `
      <strong>Add supplier rate sheet</strong>
      <p class="nx-text-dim" style="margin:6px 0 0;">Handwriting OCR stack: <strong>Gemini Vision</strong> → Azure Read → EasyOCR → PaddleOCR → RapidOCR. Sirf is file se rates — koi demo data nahi.</p>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label class="hop-form-span-2">Upload file *
          <input id="f-rs-file" type="file" onchange="hopOnRateFileSelected()" accept=".pdf,.doc,.docx,.rtf,.odt,.txt,.csv,.tsv,.xlsx,.xlsm,.xls,.ods,.jpg,.jpeg,.png,.bmp,.gif,.webp,.tif,.tiff,.heic,.heif,application/pdf,image/*,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/rtf" />
        </label>
        <label>Supplier name <span class="nx-text-dim">(auto)</span><input id="f-rs-supplier" placeholder="Auto from filename" /></label>
        <label>Source <span class="nx-text-dim">(auto)</span><select id="f-rs-source"><option value="manual">Manual</option><option value="pdf">PDF</option><option value="excel">Excel</option><option value="word">Word / WordPad</option><option value="image">Image / photo</option><option value="handwritten">Handwritten</option><option value="quote">Quote</option></select></label>
        <label class="hop-form-span-2">Title<input id="f-rs-title" placeholder="Auto from file" /></label>
        <label class="hop-form-span-2">Notes<input id="f-rs-notes" placeholder="Freight extra, advance, etc." /></label>
        <label class="hop-form-span-2">Rate lines (optional if file parses)
          <textarea id="f-rs-lines" rows="8" placeholder="Bedsheet | 110x112 | 715 | 5&#10;Duvet Cover | 110x114 | 1498 | 5&#10;Bath Towel | 30x60 | 432 | 5"></textarea>
        </label>
      </div>
      <p id="f-rs-upload-status" class="nx-text-dim" style="margin-top:8px;"></p>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopUploadRateSheet()">Upload &amp; save</button>
        <button type="button" class="nx-btn" onclick="hopSave('rate_sheet')">Save pasted lines</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'sample') {
    slot.innerHTML = `
      <strong>New Sample</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Sample Name *<input id="f-sname" /></label>
        <label>Project<select id="f-sproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-scustomer">${hopCustomerOptions()}</select></label>
        <label>Courier<input id="f-scourier" /></label>
        <label>Tracking<input id="f-strack" /></label>
        <label>Approval<select id="f-sapproval"><option>pending</option><option>approved</option><option>rejected</option></select></label>
        <label>Return Status<input id="f-sreturn" /></label>
        <label class="hop-form-span-2">Notes<input id="f-snotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('sample')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'product') {
    slot.innerHTML = `
      <strong>New Product</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Name *<input id="f-prname" /></label>
        <label>Code<input id="f-prcode" /></label>
        <label>Brand<input id="f-prbrand" /></label>
        <label>Category<input id="f-prcat" placeholder="Bed Linen / Fabric…" /></label>
        <label>Sell Price<input id="f-prsell" type="number" /></label>
        <label>Purchase Price<input id="f-prbuy" type="number" /></label>
        <label>Logistics<input id="f-prlog" type="number" /></label>
        <label>GST %<input id="f-prgst" type="number" /></label>
        <label>Commission %<input id="f-prcomm" type="number" /></label>
        <label>Stock<input id="f-prstock" type="number" /></label>
        <label>Vendor<select id="f-prvendor">${hopVendorOptions()}</select></label>
        <label>MOQ<input id="f-prmoq" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('product')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'order') {
    slot.innerHTML = `
      <strong>New Order / PO</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>PO Number<input id="f-opo" /></label>
        <label>Project<select id="f-oproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-ocustomer">${hopCustomerOptions()}</select></label>
        <label>Client Name<input id="f-oclient" /></label>
        <label>Order Value<input id="f-ovalue" type="number" /></label>
        <label>Vendor<select id="f-ovendor">${hopVendorOptions()}</select></label>
        <label>Supplier Text<input id="f-osupplier" /></label>
        <label>Expected Delivery<input id="f-odelivery" type="date" /></label>
        <label>Mark Won?<select id="f-owon"><option value="1">Yes</option><option value="0">No</option></select></label>
        <label>Type<select id="f-otype"><option value="customer_po">Customer PO</option><option value="vendor_po">Vendor PO</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('order')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'dispatch') {
    slot.innerHTML = `
      <strong>New Dispatch</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-dproject">${hopProjectOptions()}</select></label>
        <label>Order<select id="f-dorder">${hopOrderOptions()}</select></label>
        <label>Status<select id="f-dstatus"><option>ready</option><option>dispatched</option><option>in_transit</option><option>delivered</option></select></label>
        <label>Tracking<input id="f-dtrack" /></label>
        <label>Courier<input id="f-dcourier" /></label>
        <label>E-way Bill<input id="f-deway" /></label>
        <label>Docket<input id="f-ddocket" /></label>
        <label>Due Date<input id="f-ddue" type="date" /></label>
        <label>Installation Pending?<select id="f-dinstall"><option value="0">No</option><option value="1">Yes</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('dispatch')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'invoice') {
    slot.innerHTML = `
      <strong>New Invoice</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-iproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-icustomer">${hopCustomerOptions()}</select></label>
        <label>Amount *<input id="f-iamount" type="number" /></label>
        <label>GST Amount<input id="f-igst" type="number" /></label>
        <label>Due Date<input id="f-idue" type="date" /></label>
        <label>Paid Already<input id="f-ipaid" type="number" value="0" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('invoice')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'payment') {
    slot.innerHTML = `
      <strong>Record Payment</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Invoice *<select id="f-payinvoice">${hopInvoiceOptions()}</select></label>
        <label>Amount *<input id="f-payamount" type="number" /></label>
        <label>Method<input id="f-paymethod" placeholder="NEFT / Cheque / UPI" /></label>
        <label>Paid At<input id="f-payat" type="date" /></label>
        <label class="hop-form-span-2">Notes<input id="f-paynotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('payment')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'complaint') {
    slot.innerHTML = `
      <strong>New Complaint</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-cproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-ccustomer">${hopCustomerOptions()}</select></label>
        <label class="hop-form-span-2">Issue *<input id="f-cissue" /></label>
        <label>Assigned<input id="f-cassigned" /></label>
        <label>Status<select id="f-cstatus"><option>open</option><option>in_progress</option><option>resolved</option><option>closed</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('complaint')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }

  // Bring form into the .hop-main scrollport (body is locked under HoP)
  try {
    requestAnimationFrame(() => {
      hopScrollIntoMain(slot, 8);
      const focusEl = slot.querySelector('input:not([type="file"]), select, textarea');
      if (focusEl) setTimeout(() => focusEl.focus(), 80);
    });
  } catch (_) { /* ignore */ }
}

async function hopSave(kind) {
  const map = {
    customer: {
      url: '/api/v1/hop/customers',
      view: 'customers',
      payload: () => ({
        company: document.getElementById('f-company')?.value,
        contact_person: document.getElementById('f-contact')?.value,
        mobile: document.getElementById('f-mobile')?.value,
        email: document.getElementById('f-email')?.value,
        city: document.getElementById('f-city')?.value,
        customer_type: document.getElementById('f-type')?.value,
        hotel_brand: document.getElementById('f-hotel')?.value,
        architect: document.getElementById('f-architect')?.value,
        consultant: document.getElementById('f-consultant')?.value,
        annual_potential: document.getElementById('f-potential')?.value,
        potential_rating: document.getElementById('f-rating')?.value,
        assigned_to: document.getElementById('f-assigned')?.value,
        address: document.getElementById('f-address')?.value,
        gst_no: document.getElementById('f-gst')?.value,
        pan: document.getElementById('f-pan')?.value,
      }),
    },
    project: {
      url: '/api/v1/hop/projects',
      view: 'projects',
      payload: () => ({
        project_name: document.getElementById('f-pname')?.value,
        customer_id: document.getElementById('f-pcustomer')?.value,
        hotel_name: document.getElementById('f-photel')?.value,
        site_address: document.getElementById('f-psite')?.value,
        architect: document.getElementById('f-parch')?.value,
        consultant: document.getElementById('f-pcons')?.value,
        stage: document.getElementById('f-pstage')?.value,
        project_value: document.getElementById('f-pvalue')?.value,
        probability_pct: document.getElementById('f-pprob')?.value,
        assigned_to: document.getElementById('f-passigned')?.value,
        next_milestone: document.getElementById('f-pmilestone')?.value,
      }),
    },
    lead: {
      url: '/api/v1/hop/leads',
      view: 'leads',
      payload: () => ({
        customer_id: document.getElementById('f-lcustomer')?.value,
        project_id: document.getElementById('f-lproject')?.value,
        project_name: document.getElementById('f-lpname')?.value,
        source: document.getElementById('f-lsource')?.value,
        expected_value: document.getElementById('f-lvalue')?.value,
        priority: document.getElementById('f-lpriority')?.value,
        assigned_to: document.getElementById('f-lassigned')?.value,
        stage: document.getElementById('f-lstage')?.value,
        probability_pct: document.getElementById('f-lprob')?.value,
        next_follow_up: document.getElementById('f-lfollow')?.value,
        expected_closure_date: document.getElementById('f-lclosure')?.value,
        products_interested: document.getElementById('f-lproducts')?.value,
      }),
    },
    meeting: {
      url: '/api/v1/hop/meetings',
      view: 'meetings',
      payload: () => {
        const when = document.getElementById('f-mwhen')?.value;
        return {
          title: document.getElementById('f-mtitle')?.value,
          scheduled_at: when ? new Date(when).toISOString() : '',
          customer_id: document.getElementById('f-mcustomer')?.value,
          project_id: document.getElementById('f-mproject')?.value,
          location: document.getElementById('f-mloc')?.value,
          agenda: document.getElementById('f-magenda')?.value,
          outcome: document.getElementById('f-moutcome')?.value,
          next_action: document.getElementById('f-mnext')?.value,
          follow_up_at: document.getElementById('f-mfollow')?.value,
          expected_order_value: document.getElementById('f-mvalue')?.value,
          probability_pct: document.getElementById('f-mprob')?.value,
        };
      },
    },
    quotation: {
      url: '/api/v1/hop/quotations',
      view: 'sale_estimates',
      payload: () => ({
        project_id: document.getElementById('f-qproject')?.value,
        customer_id: document.getElementById('f-qcustomer')?.value,
        value: document.getElementById('f-qvalue')?.value,
        margin_pct: document.getElementById('f-qmargin')?.value,
        status: document.getElementById('f-qstatus')?.value,
        sales_person: document.getElementById('f-qsales')?.value,
        expected_closure_date: document.getElementById('f-qclosure')?.value,
        payment_terms: document.getElementById('f-qpay')?.value,
        notes: document.getElementById('f-qnotes')?.value,
      }),
    },
    vendor: {
      url: '/api/v1/hop/vendors',
      view: 'vendors',
      payload: () => ({
        company: document.getElementById('f-vcompany')?.value,
        products: document.getElementById('f-vproducts')?.value,
        gst_no: document.getElementById('f-vgst')?.value,
        contact_person: document.getElementById('f-vcontact')?.value,
        mobile: document.getElementById('f-vmobile')?.value,
        email: document.getElementById('f-vemail')?.value,
        city: document.getElementById('f-vcity')?.value,
        rating: document.getElementById('f-vrating')?.value,
        lead_time_days: document.getElementById('f-vlead')?.value,
        payment_terms: document.getElementById('f-vpay')?.value,
        on_time_pct: document.getElementById('f-vontime')?.value,
        quality_rating: document.getElementById('f-vqual')?.value,
        certificates: document.getElementById('f-vcert')?.value,
      }),
    },
    vendor_cmp: {
      url: '/api/v1/hop/vendor-comparisons',
      view: 'vendor_cmp',
      payload: () => ({
        project_id: document.getElementById('f-vcproject')?.value,
        vendor_id: document.getElementById('f-vcvendor')?.value,
        product_name: document.getElementById('f-vcproduct')?.value,
        rate: document.getElementById('f-vcrate')?.value,
        lead_time_days: document.getElementById('f-vclead')?.value,
        moq: document.getElementById('f-vcmoq')?.value,
        quality_note: document.getElementById('f-vcqual')?.value,
        certification: document.getElementById('f-vccert')?.value,
        payment_terms: document.getElementById('f-vcpay')?.value,
        is_winner: document.getElementById('f-vcwin')?.value,
        recommendation: document.getElementById('f-vcrec')?.value,
      }),
    },
    rate_sheet: {
      url: '/api/v1/hop/rate-sheets',
      view: 'vendor_cmp',
      payload: () => {
        const text = document.getElementById('f-rs-lines')?.value || '';
        const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
          const parts = line.split('|').map((p) => p.trim());
          if (parts.length >= 3) {
            return {
              product_name: parts[0],
              size: parts[1],
              rate: parts[2],
              gst_pct: parts[3] || 5,
            };
          }
          // fallback: "Bedsheet 110x112 715 +5%"
          const m = line.match(/^(.+?)\s+(\d{1,3}\s*[x×]\s*\d{1,3}|[Ll]|Free\s*Size)\s+(\d+(?:\.\d+)?)\s*(?:\+?\s*(\d+(?:\.\d+)?)\s*%?)?$/i);
          if (m) {
            return { product_name: m[1].trim(), size: m[2], rate: m[3], gst_pct: m[4] || 5 };
          }
          return null;
        }).filter(Boolean);
        return {
          supplier_name: document.getElementById('f-rs-supplier')?.value,
          title: document.getElementById('f-rs-title')?.value,
          source_type: document.getElementById('f-rs-source')?.value,
          notes: document.getElementById('f-rs-notes')?.value,
          lines,
        };
      },
    },
    sample: {
      url: '/api/v1/hop/samples',
      view: 'samples',
      payload: () => ({
        sample_name: document.getElementById('f-sname')?.value,
        project_id: document.getElementById('f-sproject')?.value,
        customer_id: document.getElementById('f-scustomer')?.value,
        courier: document.getElementById('f-scourier')?.value,
        tracking_number: document.getElementById('f-strack')?.value,
        approval_status: document.getElementById('f-sapproval')?.value,
        return_status: document.getElementById('f-sreturn')?.value,
        notes: document.getElementById('f-snotes')?.value,
      }),
    },
    product: {
      url: '/api/v1/hop/products',
      view: 'products',
      payload: () => ({
        name: document.getElementById('f-prname')?.value,
        code: document.getElementById('f-prcode')?.value,
        brand: document.getElementById('f-prbrand')?.value,
        category: document.getElementById('f-prcat')?.value,
        selling_price: document.getElementById('f-prsell')?.value,
        purchase_price: document.getElementById('f-prbuy')?.value,
        logistics_cost: document.getElementById('f-prlog')?.value,
        gst_pct: document.getElementById('f-prgst')?.value,
        commission_pct: document.getElementById('f-prcomm')?.value,
        stock_qty: document.getElementById('f-prstock')?.value,
        vendor_id: document.getElementById('f-prvendor')?.value,
        moq: document.getElementById('f-prmoq')?.value,
      }),
    },
    order: {
      url: '/api/v1/hop/orders',
      view: 'orders',
      payload: () => ({
        po_number: document.getElementById('f-opo')?.value,
        project_id: document.getElementById('f-oproject')?.value,
        customer_id: document.getElementById('f-ocustomer')?.value,
        client_name: document.getElementById('f-oclient')?.value,
        order_value: document.getElementById('f-ovalue')?.value,
        vendor_id: document.getElementById('f-ovendor')?.value,
        supplier: document.getElementById('f-osupplier')?.value,
        expected_delivery: document.getElementById('f-odelivery')?.value,
        mark_won: document.getElementById('f-owon')?.value === '1',
        order_type: document.getElementById('f-otype')?.value,
      }),
    },
    dispatch: {
      url: '/api/v1/hop/dispatches',
      view: 'dispatches',
      payload: () => ({
        project_id: document.getElementById('f-dproject')?.value,
        order_id: document.getElementById('f-dorder')?.value,
        status: document.getElementById('f-dstatus')?.value,
        tracking_number: document.getElementById('f-dtrack')?.value,
        courier: document.getElementById('f-dcourier')?.value,
        eway_bill: document.getElementById('f-deway')?.value,
        docket_number: document.getElementById('f-ddocket')?.value,
        due_date: document.getElementById('f-ddue')?.value,
        installation_pending: document.getElementById('f-dinstall')?.value,
        dispatched_at: document.getElementById('f-dstatus')?.value === 'dispatched' || document.getElementById('f-dstatus')?.value === 'in_transit'
          ? new Date().toISOString().slice(0, 10) : null,
      }),
    },
    invoice: {
      url: '/api/v1/hop/invoices',
      view: 'invoices',
      payload: () => ({
        project_id: document.getElementById('f-iproject')?.value,
        customer_id: document.getElementById('f-icustomer')?.value,
        amount: document.getElementById('f-iamount')?.value,
        gst_amount: document.getElementById('f-igst')?.value,
        due_date: document.getElementById('f-idue')?.value,
        paid_amount: document.getElementById('f-ipaid')?.value,
      }),
    },
    payment: {
      url: '/api/v1/hop/payments',
      view: 'payments',
      payload: () => ({
        invoice_id: document.getElementById('f-payinvoice')?.value,
        amount: document.getElementById('f-payamount')?.value,
        method: document.getElementById('f-paymethod')?.value,
        paid_at: document.getElementById('f-payat')?.value,
        notes: document.getElementById('f-paynotes')?.value,
      }),
    },
    complaint: {
      url: '/api/v1/hop/complaints',
      view: 'complaints',
      payload: () => ({
        project_id: document.getElementById('f-cproject')?.value,
        customer_id: document.getElementById('f-ccustomer')?.value,
        issue: document.getElementById('f-cissue')?.value,
        assigned_to: document.getElementById('f-cassigned')?.value,
        status: document.getElementById('f-cstatus')?.value,
      }),
    },
  };
  const cfg = map[kind];
  if (!cfg) return;
  const edit = hopState.contactEdit;
  const isEdit = edit && edit.kind === kind && edit.id;
  try {
    await hopApi(isEdit ? `${cfg.url}/${edit.id}` : cfg.url, {
      method: isEdit ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg.payload()),
    });
    hopState.contactEdit = null;
    document.getElementById('hop-form-slot')?.classList.add('hidden');
    // invalidate caches
    hopState.customers = [];
    hopState.projects = [];
    hopState.vendors = [];
    hopState.invoices = [];
    hopState.orders = [];
    const nextView = (kind === 'customer' || kind === 'vendor')
      ? hopContactReturnView(kind === 'vendor' ? 'vendors' : 'customers')
      : cfg.view;
    openHopView(nextView);
  } catch (e) {
    alert(e.message);
  }
}

function hopFilterModule(view) {
  hopState.search[view] = document.getElementById('hop-q')?.value || '';
  hopDebouncedReload(view);
}

window.loadHopExecutiveSnapshot = loadHopExecutiveSnapshot;
window.openHopView = openHopView;
window.hopGoBack = hopGoBack;
window.hopToggleNavFold = hopToggleNavFold;
window.openHopProjectHub = openHopProjectHub;
window.hopDebouncedReload = hopDebouncedReload;
window.hopShowForm = hopShowForm;
window.hopSave = hopSave;
window.hopPatchLead = hopPatchLead;
window.hopPatchQuote = hopPatchQuote;
window.hopReviseQuote = hopReviseQuote;
window.hopPatchOrder = hopPatchOrder;
window.hopSaveTarget = hopSaveTarget;
window.hopSetHubTab = hopSetHubTab;
window.hopChangeProjectStage = hopChangeProjectStage;
window.hopFilterModule = hopFilterModule;
window.hopSeedRateSamples = hopSeedRateSamples;
window.hopDeleteRateSheet = hopDeleteRateSheet;
window.hopUploadRateSheet = hopUploadRateSheet;
window.hopOnRateFileSelected = hopOnRateFileSelected;
window.hopClearRateQuote = hopClearRateQuote;
window.hopAddToRateCart = hopAddToRateCart;
window.hopAddBestToRateCart = hopAddBestToRateCart;
window.hopRemoveRateCartItem = hopRemoveRateCartItem;
window.hopUpdateRateCartQty = hopUpdateRateCartQty;
window.hopToggleRateCartExpand = hopToggleRateCartExpand;
window.hopPlaceRateCartOrders = hopPlaceRateCartOrders;
window.hopToggleSelectAllRates = hopToggleSelectAllRates;
window.hopClearSingleProduct = hopClearSingleProduct;
window.hopClearSingleRate = hopClearSingleRate;
window.hopClearSelectedRates = hopClearSelectedRates;
window.hopClearAllRates = hopClearAllRates;
window.hopClearSheetRates = hopClearSheetRates;
window.hopApplyRateMatrixFilters = hopApplyRateMatrixFilters;
window.hopResetRateFilters = hopResetRateFilters;
window.hopToggleContactDetails = hopToggleContactDetails;
window.hopToggleContactSelectMode = hopToggleContactSelectMode;
window.hopToggleContactSelected = hopToggleContactSelected;
window.hopSelectAllContacts = hopSelectAllContacts;
window.hopDeleteContact = hopDeleteContact;
window.hopEditContact = hopEditContact;
window.hopBulkDeleteContacts = hopBulkDeleteContacts;
window.hopCloseContactActionMenu = hopCloseContactActionMenu;
window.hopOpenContactDetail = hopOpenContactDetail;
window.hopCloseContactDetail = hopCloseContactDetail;
window.hopOpenAddPartyChooser = hopOpenAddPartyChooser;
window.hopCloseAddPartyChooser = hopCloseAddPartyChooser;
window.hopAddPartyManual = hopAddPartyManual;
window.hopAddPartyViaScan = hopAddPartyViaScan;
window.hopOpenPartyScanModal = hopOpenPartyScanModal;
window.hopClosePartyScanModal = hopClosePartyScanModal;
window.hopOpenPartyEditModal = hopOpenPartyEditModal;
window.hopClosePartyEditModal = hopClosePartyEditModal;
window.hopPartyModalSetTab = hopPartyModalSetTab;
window.hopPartyGstCheck = hopPartyGstCheck;
window.hopPartyFetchGstDetails = hopPartyFetchGstDetails;
window.hopSavePartyModal = hopSavePartyModal;
window.hopCreatePartyWithDupConfirm = hopCreatePartyWithDupConfirm;
window.hopDeleteFromPartyModal = hopDeleteFromPartyModal;
window.hopPartySetCreditLimitMode = hopPartySetCreditLimitMode;
window.hopPartyCopyBillingToShipping = hopPartyCopyBillingToShipping;
window.hopPartyGroupOpenMenu = hopPartyGroupOpenMenu;
window.hopPartyGroupCloseMenu = hopPartyGroupCloseMenu;
window.hopPartyGroupOnInput = hopPartyGroupOnInput;
window.hopPartyGroupSelect = hopPartyGroupSelect;
window.hopPartyGroupCreateNew = hopPartyGroupCreateNew;
window.hopResetVisitingCardReview = hopResetVisitingCardReview;
window.hopFilterListTable = hopFilterListTable;
window.hopInvoicePartyOpenMenu = hopInvoicePartyOpenMenu;
window.hopInvoicePartyCloseMenu = hopInvoicePartyCloseMenu;
window.hopInvoicePartyToggle = hopInvoicePartyToggle;
window.hopInvoicePartyOnInput = hopInvoicePartyOnInput;
window.hopInvoicePartySelect = hopInvoicePartySelect;
window.hopInvoiceOnPeriodChange = hopInvoiceOnPeriodChange;
window.hopInvoiceApplyFilters = hopInvoiceApplyFilters;
window.hopInvoiceResetFilters = hopInvoiceResetFilters;
window.hopInvoiceExportCsv = hopInvoiceExportCsv;
window.hopSelectParty = hopSelectParty;
window.hopFilterParties = hopFilterParties;
window.hopOpenPartyTxnDetail = hopOpenPartyTxnDetail;
window.hopOpenSaleDocPreview = hopOpenSaleDocPreview;
window.hopClosePartyTxnDetail = hopClosePartyTxnDetail;
window.hopOpenPartyTxnInModule = hopOpenPartyTxnInModule;
window.hopPrintPartyTxnPreview = hopPrintPartyTxnPreview;
window.hopPreviewVyaparBackup = hopPreviewVyaparBackup;
window.hopRunVyaparImport = hopRunVyaparImport;
window.hopPickVyaparBackup = hopPickVyaparBackup;
window.hopRunWipeData = hopRunWipeData;
