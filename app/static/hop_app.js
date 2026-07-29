/* House of Prizm UI — project-centric ERP shell (hop_admin only). */

const hopState = {
  view: 'dashboard',
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
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7Zm3-3h6l1 2H8l1-2Z" fill="currentColor"/></svg>';
}

function hopRenderQuickContactActions(mobile, type, id, label) {
  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);
  const callBtn = callHref
    ? `<a class="nx-btn hop-contact-icon-btn" href="${callHref}" title="Call">${hopContactIcon('call')}</a>`
    : `<button type="button" class="nx-btn hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('call')}</button>`;
  const waBtn = waHref
    ? `<a class="nx-btn nx-btn-primary hop-contact-icon-btn" href="${waHref}" target="_blank" rel="noopener noreferrer" title="WhatsApp">${hopContactIcon('whatsapp')}</a>`
    : `<button type="button" class="nx-btn nx-btn-primary hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('whatsapp')}</button>`;
  const delBtn = `<button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label || '')}')" title="Delete">${hopContactIcon('delete')}</button>`;
  return `
    <div class="hop-contact-actions">
      ${callBtn}
      ${waBtn}
      ${delBtn}
    </div>`;
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
  const kind = type === 'vendors' ? 'vendor' : 'customer';
  const rows = type === 'vendors' ? (hopState.vendors || []) : (hopState.customers || []);
  let row = rows.find((r) => Number(r.id) === Number(id));
  if (!row) {
    try {
      row = await hopApi(`${hopContactApiBase(type)}/${id}`);
    } catch (e) {
      alert(e.message || 'Could not load contact');
      return;
    }
  }
  await hopShowForm(kind, row);
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
    if (type === 'vendors') hopState.vendors = [];
    else hopState.customers = [];
    openHopView(type);
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
    openHopView(type);
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
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  const shell = document.querySelector('#hop-executive-workspace .hop-shell');
  main?.classList.toggle('hop-main--fullpage', !!enabled);
  shell?.classList.toggle('hop-shell--module', !!enabled);
}

function openHopView(viewName, opts) {
  hopState.view = viewName || 'dashboard';
  hopHideAllViews();
  hopScrollMainToTop();
  document.querySelectorAll('.hop-nav-btn[data-hop-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.hopView === hopState.view);
  });

  // Dashboard keeps padded scroll layout; every other menu opens as full-page workspace.
  hopSetMainFullpage(hopState.view !== 'dashboard');

  if (hopState.view === 'dashboard') {
    document.getElementById('hop-view-dashboard')?.classList.remove('hidden');
    Promise.resolve(loadHopExecutiveSnapshot()).finally(() => hopScrollMainToTop());
  } else if (hopState.view === 'project-hub' || hopState.view === 'project_hub') {
    document.getElementById('hop-view-project-hub')?.classList.remove('hidden');
    const pid = opts?.projectId || hopState.hub?.project?.id;
    if (pid) Promise.resolve(loadHopProjectHub(pid)).finally(() => hopScrollMainToTop());
  } else {
    const mount = hopMount();
    if (!mount) {
      console.error('HoP mount missing');
    } else {
      mount.innerHTML = '<div class="hop-view"><p class="nx-text-dim">Loading…</p></div>';
      const loaders = {
        parties: renderHopPartiesModule,
        customers: renderHopCustomersModule,
        vyapar_import: renderHopVyaparImportModule,
        visiting_card: renderHopVisitingCardModule,
        projects: renderHopProjectsModule,
        leads: renderHopLeadsModule,
        meetings: renderHopMeetingsModule,
        quotations: renderHopQuotationsModule,
        vendors: renderHopVendorsModule,
        vendor_cmp: renderHopVendorCmpModule,
        samples: renderHopSamplesModule,
        products: renderHopProductsModule,
        fabric_preview: renderHopFabricPreviewModule,
        orders: renderHopOrdersModule,
        dispatches: renderHopDispatchesModule,
        invoices: renderHopInvoicesModule,
        payments: renderHopPaymentsModule,
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
      if (fn) Promise.resolve(fn(mount)).finally(() => hopScrollMainToTop());
      else mount.innerHTML = `<div class="hop-view"><p class="nx-oc-error">Unknown view</p></div>`;
    }
  }

  // Close drawer after navigation so Android WebView does not cancel the tap.
  window.setTimeout(() => {
    if (typeof closeMobileNav === 'function') closeMobileNav();
  }, 80);
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

function hopModuleShell(eyebrow, title, subtitle, actionsHtml, bodyHtml) {
  const isFullpage = hopState.view && hopState.view !== 'dashboard';
  return `
    <div class="hop-view${isFullpage ? ' hop-view--fullpage' : ''}">
      <header class="hop-view-header hop-view-header-row${isFullpage ? ' hop-view-header--compact' : ''}">
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

function hopTable(headers, rowsHtml, emptyCols) {
  return `
    <div class="hop-table-wrap">
      <table class="data-table hop-table">
        <thead><tr>${headers.map((h) => `<th>${foEscapeText(h)}</th>`).join('')}</tr></thead>
        <tbody>${rowsHtml || `<tr><td colspan="${emptyCols || headers.length}">No rows yet — add your first record.</td></tr>`}</tbody>
      </table>
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

/* ---------- Parties (Vyapar-style unified view) ---------- */
async function renderHopPartiesModule(mount) {
  let customers = [], vendors = [], partyTxns = [];
  try {
    [customers, vendors, partyTxns] = await Promise.all([
      hopApi('/api/v1/hop/customers') || [],
      hopApi('/api/v1/hop/vendors') || [],
      hopApi('/api/v1/hop/party-transactions') || [],
    ]);
    hopState.customers = customers;
    hopState.vendors = vendors;
  } catch (e) {
    mount.innerHTML = hopModuleShell('CRM', 'Parties', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  const parties = [
    ...customers.map(c => ({ ...c, _type: 'customer', _balance: 0 })),
    ...vendors.map(v => ({ ...v, _type: 'vendor', _balance: 0 })),
  ];

  // Calculate balances from imported party transactions.
  const balByParty = {};
  for (const t of partyTxns) {
    const key = `${t.party_type}:${t.party_id}`;
    balByParty[key] = (balByParty[key] || 0) + parseFloat(t.balance_amount || 0);
  }
  for (const p of parties) {
    const key = `${p._type}:${p.id}`;
    p._balance = balByParty[key] || 0;
  }

  parties.sort((a, b) => (a.company || '').localeCompare(b.company || ''));

  hopState._parties = parties;
  hopState._partyTxns = partyTxns;
  hopState._partySelected = hopState._partySelected || null;
  hopState._partyFilter = hopState._partyFilter || '';

  const body = `
    <div class="pty-layout">
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
      <div id="pty-detail" class="pty-detail">
        ${hopState._partySelected ? _hopRenderPartyDetail(hopState._partySelected, partyTxns) : _hopPartyEmptyDetail()}
      </div>
    </div>`;

  mount.innerHTML = hopModuleShell('CRM', 'Parties', 'Customers & Vendors',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('customer')">+ Add Party</button>
     <button type="button" class="nx-btn" onclick="openHopView('vyapar_import')">Import Vyapar</button>`, body);
}

function _hopRenderPartyList(parties, filter) {
  const q = (filter || '').toLowerCase().trim();
  const filtered = q ? parties.filter(p => (p.company || '').toLowerCase().includes(q) || (p.contact_person || '').toLowerCase().includes(q)) : parties;
  if (!filtered.length) return '<p class="pty-empty-list">No parties found.</p>';
  return filtered.map(p => {
    const sel = hopState._partySelected && hopState._partySelected.id === p.id && hopState._partySelected._type === p._type;
    const bal = p._balance ? `₹ ${Number(p._balance).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '0.00';
    const badge = p._type === 'vendor' ? '<span class="pty-badge pty-badge-v">V</span>' : '<span class="pty-badge pty-badge-c">C</span>';
    return `<button type="button" class="pty-item${sel ? ' is-active' : ''}" onclick="hopSelectParty('${p._type}', ${p.id})">
      ${badge}
      <span class="pty-item-name">${foEscapeText(p.company || '—')}</span>
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
  const typeName = isVendor ? 'Vendor' : 'Customer';
  const address = party.address || '';
  const mobile = party.mobile || '';
  const email = party.email || '';
  const gst = party.gst_no || '';
  const city = party.city || '';

  const partyRows = (partyTxns || [])
    .filter((t) => t.party_type === party._type && Number(t.party_id) === Number(party.id))
    .sort((a, b) => String(b.txn_date || '').localeCompare(String(a.txn_date || '')));

  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);

  return `
    <div class="pty-detail-header">
      <div class="pty-detail-info">
        <h3 class="pty-detail-name">${foEscapeText(party.company || '—')}</h3>
        <span class="pty-detail-type">${typeName}</span>
        ${gst ? `<p class="pty-detail-gst">GSTIN: ${foEscapeText(gst)}</p>` : ''}
        ${address ? `<p class="pty-detail-addr">${foEscapeText(address)}</p>` : ''}
        <div class="pty-detail-contact">
          ${mobile ? `<span>${foEscapeText(mobile)}</span>` : ''}
          ${email ? `<span>${foEscapeText(email)}</span>` : ''}
          ${city ? `<span>${foEscapeText(city)}</span>` : ''}
        </div>
      </div>
      <div class="pty-detail-actions">
        ${callHref ? `<a class="pty-action-btn" href="${callHref}" title="Call">${hopContactIcon('call')}</a>` : ''}
        ${waHref ? `<a class="pty-action-btn pty-action-wa" href="${waHref}" target="_blank" title="WhatsApp">${hopContactIcon('whatsapp')}</a>` : ''}
        <button type="button" class="pty-action-btn" onclick="hopEditContact('${party._type}s', ${party.id})" title="Edit">${hopContactIcon('edit')}</button>
        <button type="button" class="pty-action-btn pty-action-del" onclick="hopDeleteContact('${party._type}s', ${party.id}, '${foEscapeAttr(party.company || '')}')" title="Delete">${hopContactIcon('delete')}</button>
      </div>
    </div>

    <div class="pty-txn-section">
      <div class="pty-txn-header">
        <strong>Transactions</strong>
        <span class="nx-text-dim">${partyRows.length} records</span>
      </div>
      ${partyRows.length ? `
        <table class="pty-txn-table">
          <thead><tr>
            <th>Type</th><th>Number</th><th>Date</th><th>Total</th><th>Balance</th><th>Status</th>
          </tr></thead>
          <tbody>
            ${partyRows.map((row) => {
              const amt = parseFloat(row.total_amount || 0);
              const bal = parseFloat(row.balance_amount || 0);
              const status = row.status_text || (bal <= 0 ? 'Paid' : 'Open');
              const s = String(status).toLowerCase();
              const statusClass = s.includes('paid')
                ? 'is-paid'
                : (s.includes('cancel') ? 'is-unpaid' : (s.includes('open') ? 'is-partial' : 'is-partial'));
              return `<tr>
                <td>${foEscapeText(row.txn_label || `Txn ${row.txn_type || ''}`)}</td>
                <td>${foEscapeText(row.txn_number || '—')}</td>
                <td>${foEscapeText((row.txn_date || '').slice(0, 10))}</td>
                <td class="pty-txn-amt">₹ ${amt.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td class="pty-txn-amt${bal > 0 ? ' is-due' : ''}">₹ ${bal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                <td><span class="pty-status ${statusClass}">${status}</span></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      ` : '<p class="pty-no-txn">No transactions yet.</p>'}
    </div>`;
}

function hopSelectParty(type, id) {
  const parties = hopState._parties || [];
  const party = parties.find(p => p._type === type && p.id === id);
  if (!party) return;
  hopState._partySelected = party;
  const list = document.getElementById('pty-list');
  if (list) list.innerHTML = _hopRenderPartyList(hopState._parties || [], hopState._partyFilter || '');
  // Render detail
  const detail = document.getElementById('pty-detail');
  if (detail) detail.innerHTML = _hopRenderPartyDetail(party, hopState._partyTxns || []);
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
  mount.innerHTML = hopModuleShell('CRM', 'Customers', 'Hospitality clients, designers, consultants',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('customer')">+ New Customer</button>
     <button type="button" class="nx-btn" onclick="openHopView('visiting_card')">Scan visiting card</button>
     <button type="button" class="nx-btn" onclick="openHopView('vyapar_import')">Import Vyapar backup</button>`, body);
  if (hopIsMobileView()) requestAnimationFrame(() => hopBindMobileContactCards('customers'));
}

/* ---------- Visiting Card Reader ---------- */
async function renderHopVyaparImportModule(mount) {
  const preview = hopState.vyaparImportPreview;
  const src = preview?.source || {};
  const det = preview?.detected || {};
  const hasPreview = !!preview;

  const sizeKB = src.sqlite_bytes ? (src.sqlite_bytes / 1024).toFixed(0) : '—';
  const salesCount = (det.txn_type_split || {})['27'] || 0;
  const purchaseCount = (det.txn_type_split || {})['2'] || 0;
  const paymentCount = (det.txn_type_split || {})['4'] || 0;

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
              <span class="vyp-t vyp-t-y">${paymentCount} Payment</span>
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
  if (!(await nexoraConfirm('Import Vyapar data into House of Prizm?', {
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
      `<span class="vyp-r-label">Invoices</span> <strong>${data.invoices_created || 0}</strong> created`,
      `<span class="vyp-r-label">Payments</span> <strong>${data.payments_created || 0}</strong> created`,
      `<span class="vyp-r-label">All Txns</span> <strong>${data.party_txns_created || 0}</strong> imported`,
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
    <div class="hop-fabric-banner nx-card">
      <strong>Visiting Card Reader</strong>
      <span class="nx-text-dim">Phone app, browser, ya desktop — same feature. Camera / file se card padho → verify → Save. Auto-save nahi hota.</span>
    </div>
    <div class="nx-card hop-fabric-card">
      <h3>1. Capture card</h3>
      <div class="hop-photo-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopPickPhoto('hop-vcard-cam')">Camera</button>
        <button type="button" class="nx-btn" onclick="hopPickPhoto('hop-vcard-gal')">Files / Gallery</button>
      </div>
      <input id="hop-vcard-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
      <input id="hop-vcard-gal" class="hop-file-hidden" type="file" accept="image/*" />
      <div id="hop-vcard-preview" class="hop-fabric-thumb nx-text-dim">No card photo yet</div>
      <div class="hop-fabric-actions">
        <button type="button" class="nx-btn nx-btn-primary" id="hop-vcard-scan-btn" onclick="hopScanVisitingCard()">Read card</button>
      </div>
      <p id="hop-vcard-status" class="nx-text-dim"></p>
    </div>
    <div id="hop-vcard-form" class="nx-card hop-form-card hidden" style="margin-top:14px;"></div>
  `;
  mount.innerHTML = hopModuleShell(
    'CRM',
    'Visiting Card Reader',
    'Any font · any colour · review before save',
    `<button type="button" class="nx-btn" onclick="openHopView('customers')">← Customers</button>`,
    body,
  );

  const bindPreview = (inputId) => {
    const input = document.getElementById(inputId);
    input?.addEventListener('change', () => {
      const file = input.files && input.files[0];
      const box = document.getElementById('hop-vcard-preview');
      if (!file || !box) return;
      hopState.visitingCardFile = file;
      // Clear the other input so we don't mix sources
      const otherId = inputId === 'hop-vcard-cam' ? 'hop-vcard-gal' : 'hop-vcard-cam';
      const other = document.getElementById(otherId);
      if (other) other.value = '';
      const url = URL.createObjectURL(file);
      box.innerHTML = `<img src="${url}" alt="Visiting card" />`;
      const status = document.getElementById('hop-vcard-status');
      if (status) status.textContent = 'Photo ready — reading card…';
      document.getElementById('hop-vcard-form')?.classList.add('hidden');
      hopScheduleVisitingCardScan();
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
    if (box) box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
    const status = document.getElementById('hop-vcard-status');
    if (status) status.textContent = 'Photo ready — reading card…';
    document.getElementById('hop-vcard-form')?.classList.add('hidden');
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
    if (status) status.textContent = 'Pehle visiting card ki photo lo (Camera / Gallery).';
    return;
  }
  if (status) status.textContent = 'Reading card…';
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
    const preview = (payload.raw_text_preview || '').trim();
    if (status) {
      let msg = payload.note || 'Neeche fields verify karo';
      if (payload.engine) msg += ` · Engine: ${payload.engine}`;
      if (payload.confidence) msg += ` · ${payload.confidence}`;
      if (!payload.gemini_configured) msg += ' · Tip: GEMINI_API_KEY Render pe set karo (best accuracy).';
      if (preview && (payload.confidence === 'low' || !f.company)) {
        msg += ` · OCR preview: ${preview.slice(0, 120)}${preview.length > 120 ? '…' : ''}`;
      }
      status.textContent = msg;
    }
    if (formWrap) {
      formWrap.classList.remove('hidden');
      formWrap.innerHTML = `
        <strong>2. Review &amp; edit before save</strong>
        <p class="nx-text-dim" style="font-size:0.78rem;margin:6px 0 10px;">Kuch galat ho to yahin correct karo — Save dabane se pehle kuch bhi database mein nahi jata.</p>
        <div class="hop-form-grid" style="margin-top:10px;">
          <label>Company *<input id="vc-company" value="${foEscapeAttr(f.company || '')}" /></label>
          <label>Contact person<input id="vc-contact" value="${foEscapeAttr(f.contact_person || '')}" /></label>
          <label>Mobile<input id="vc-mobile" value="${foEscapeAttr(f.mobile || '')}" /></label>
          <label>Email<input id="vc-email" value="${foEscapeAttr(f.email || '')}" /></label>
          <label>City<input id="vc-city" value="${foEscapeAttr(f.city || '')}" /></label>
          <label>Type<input id="vc-type" value="${foEscapeAttr(f.customer_type || '')}" placeholder="Hotel / Designer / …" /></label>
          <label>GST<input id="vc-gst" value="${foEscapeAttr(f.gst_no || '')}" /></label>
          <label>PAN<input id="vc-pan" value="${foEscapeAttr(f.pan || '')}" /></label>
          <label class="hop-form-span-2">Address<input id="vc-address" value="${foEscapeAttr(f.address || '')}" /></label>
          <label class="hop-form-span-2">Remarks<input id="vc-remarks" value="${foEscapeAttr(f.remarks || '')}" /></label>
        </div>
        <div class="hop-form-actions">
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveVisitingCardCustomer()">Save as customer</button>
          <button type="button" class="nx-btn" onclick="document.getElementById('hop-vcard-form').classList.add('hidden')">Discard</button>
        </div>`;
      requestAnimationFrame(() => hopScrollIntoMain(formWrap, 8));
    }
  } catch (e) {
    if (status) status.textContent = e.message || 'Card scan failed';
  } finally {
    if (btn) btn.disabled = false;
  }
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
    alert('Customer saved.');
    openHopView('customers');
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
    <div class="hop-toolbar"><input id="hop-q" class="hop-search" type="search" value="${foEscapeText(q)}" placeholder="Search project…" oninput="hopFilterModule('projects')" /></div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Project', 'Customer', 'Hotel', 'Stage', 'Value', 'Prob %', 'Completion %', 'Next Milestone', 'Assigned', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.customer_company || r.client_name)}</td>
        <td>${hopCell(r.hotel_name)}</td><td><span class="hop-stage-pill">${hopCell(r.stage)}</span></td>
        <td>${hopMoney(r.project_value ?? r.expected_value)}</td><td>${hopCell(r.probability_pct)}</td>
        <td>${hopCell(r.completion_pct)}</td><td>${hopCell(r.next_milestone)}</td><td>${hopCell(r.assigned_to)}</td>
        <td><button type="button" class="nx-btn" onclick="openHopProjectHub(${r.id})">Open hub</button></td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Sales · Heart of ERP', 'Projects', 'Every enquiry is one project — open hub for full funnel',
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
    <div class="hop-toolbar"><input id="hop-q" class="hop-search" type="search" placeholder="Search leads…" oninput="hopFilterModule('leads')" /></div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Lead No', 'Customer', 'Project', 'Source', 'Value', 'Priority', 'Sales', 'Stage', 'Prob %', 'Follow-up', 'Status', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.lead_number)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.source)}</td><td>${hopMoney(r.expected_value)}</td><td>${hopCell(r.priority)}</td>
        <td>${hopCell(r.assigned_to)}</td>
        <td><select onchange="hopPatchLead(${r.id}, this.value)">${hopStageOptions(HOP_LEAD_STAGES, r.stage)}</select></td>
        <td>${hopCell(r.probability_pct)}</td><td>${hopCell(r.next_follow_up)}</td><td>${hopCell(r.status)}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Project</button>` : '—'}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Leads', 'Creating a lead auto-creates / links a Project',
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
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Today', c.today, null)}
      ${renderHopKpiCard('Upcoming', c.upcoming, null)}
      ${renderHopKpiCard('Missed', c.missed, null)}
      ${renderHopKpiCard('Follow-up Due', c.follow_up_due, null)}
    </div>
    <div class="hop-toolbar"><input id="hop-q" class="hop-search" type="search" placeholder="Search meetings…" /></div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['When', 'Title', 'Customer', 'Project', 'Location', 'Status', 'Outcome', 'Next Action', 'Follow-up', 'Expected Value', 'Prob %'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.scheduled_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td>${hopCell(r.title)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.location)}</td><td>${hopCell(r.status)}</td><td>${hopCell(r.outcome)}</td>
        <td>${hopCell(r.next_action)}</td><td>${hopCell(r.follow_up_at)}</td>
        <td>${hopMoney(r.expected_order_value)}</td><td>${hopCell(r.probability_pct)}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Meetings', 'Every meeting needs an outcome',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('meeting')">+ New Meeting</button>`, body);
}

/* ---------- Quotations ---------- */
async function renderHopQuotationsModule(mount) {
  let kpis = {};
  let rows = [];
  try {
    kpis = await hopApi('/api/v1/hop/reports/quotations') || {};
    rows = kpis.rows || (await hopApi('/api/v1/hop/quotations')) || [];
    hopState.quotations = rows;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Quotations', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Sent This Month', kpis.quotes_sent_this_month, null)}
      ${renderHopKpiCard('Pending', kpis.quotes_pending, null)}
      ${renderHopKpiCard('Converted', kpis.quotes_converted, null)}
      ${renderHopKpiCard('Avg Quote Value', kpis.average_quote_value, null)}
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Quote No', 'Ver', 'Customer', 'Project', 'Date', 'Value', 'Margin %', 'Status', 'Follow-up', 'Expected Closure', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.quote_no)}</td><td>${hopCell(r.version)}</td><td>${hopCell(r.customer_company)}</td>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.quote_date)}</td><td>${hopMoney(r.value)}</td>
        <td>${hopCell(r.margin_pct)}</td>
        <td>
          <select onchange="hopPatchQuote(${r.id}, this.value)">
            ${['draft','pending','sent','negotiation','follow_up','converted','lost'].map((s) => `<option value="${s}"${s === r.status ? ' selected' : ''}>${s}</option>`).join('')}
          </select>
        </td>
        <td>${hopCell(r.last_follow_up)}</td><td>${hopCell(r.expected_closure_date)}</td>
        <td><button type="button" class="nx-btn" onclick="hopReviseQuote(${r.id})">Revise</button></td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Sales Ops', 'Quotations', 'Multi-version quotes linked to projects',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('quotation')">+ New Quotation</button>`, body);
}

async function hopPatchQuote(id, status) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    openHopView('quotations');
  } catch (e) { alert(e.message); }
}

async function hopReviseQuote(id) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}/revise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    openHopView('quotations');
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
  ).replace('class="hop-view hop-view--fullpage"', 'class="hop-view hop-view--fullpage hop-vendor-cmp-compact"')
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
    ${hopTable(
      ['Sample', 'Customer', 'Project', 'Sent', 'Courier', 'Tracking', 'Return', 'Approval', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.sample_name)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.sent_at)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.tracking_number)}</td>
        <td>${hopCell(r.return_status)}</td><td>${hopCell(r.approval_status)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Sample Management', 'Sent → tracking → approval',
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
    ${hopTable(
      ['Code', 'Name', 'Brand', 'Category', 'Sell', 'Buy', 'Logistics', 'GST%', 'Comm%', 'Net Profit', 'Margin %', 'Stock', 'Vendor'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.code)}</td><td>${hopCell(r.name)}</td><td>${hopCell(r.brand)}</td><td>${hopCell(r.category)}</td>
        <td>${hopMoney(r.selling_price)}</td><td>${hopMoney(r.purchase_price)}</td><td>${hopMoney(r.logistics_cost)}</td>
        <td>${hopCell(r.gst_pct)}</td><td>${hopCell(r.commission_pct)}</td>
        <td>${hopMoney(r.net_profit)}</td><td>${hopCell(r.margin_pct)}</td><td>${hopCell(r.stock_qty)}</td>
        <td>${hopCell(r.vendor_company)}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Product Catalogue', 'Margin visible per SKU',
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
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['PO No', 'Client', 'Project', 'Value', 'Supplier', 'Expected Delivery', 'Production', 'Dispatch', 'Invoice', 'Won At', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.po_number)}</td><td>${hopCell(r.client_name || r.customer_company)}</td>
        <td>${hopCell(r.project_name)}</td><td>${hopMoney(r.order_value)}</td><td>${hopCell(r.supplier || r.vendor_company)}</td>
        <td>${hopCell(r.expected_delivery)}</td>
        <td><select onchange="hopPatchOrder(${r.id}, 'production_status', this.value)">
          ${['pending','ordered','in_production','qc','packed','ready','completed','delayed'].map((s) => `<option value="${s}"${s === r.production_status ? ' selected' : ''}>${s}</option>`).join('')}
        </select></td>
        <td>${hopCell(r.dispatch_status)}</td><td>${hopCell(r.invoice_status)}</td><td>${hopCell((r.won_at || '').slice(0, 10))}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Hub</button>` : '—'}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Sales Ops', 'Orders / PO', 'Customer PO & execution status',
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
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Project', 'PO', 'Status', 'Tracking', 'Courier', 'Delivery', 'Dispatched', 'Delivered', 'E-way', 'Docket', 'POD', 'Install'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.po_number)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.delivery_status)}</td>
        <td>${hopCell(r.dispatched_at)}</td><td>${hopCell(r.delivered_at)}</td>
        <td>${hopCell(r.eway_bill)}</td><td>${hopCell(r.docket_number)}</td>
        <td>${r.pod_received ? 'Yes' : '—'}</td><td>${r.installation_pending ? 'Pending' : '—'}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Dispatch', 'Ready → courier → POD → installation',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('dispatch')">+ New Dispatch</button>`, body);
}

async function renderHopInvoicesModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/invoices') || []; hopState.invoices = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Invoices', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Invoice No', 'Customer', 'Project', 'Date', 'Amount', 'Paid', 'Balance', 'Due', 'Status'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.invoice_no)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.invoice_date)}</td><td>${hopMoney(r.amount)}</td><td>${hopMoney(r.paid_amount)}</td>
        <td>${hopMoney(r.balance)}</td><td>${hopCell(r.due_date)}</td><td>${hopCell(r.status)}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Invoices', 'GST invoices with partial payments',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('invoice')">+ New Invoice</button>`, body);
}

async function renderHopPaymentsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/payments') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Payments', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Paid At', 'Invoice', 'Customer', 'Project', 'Amount', 'Method', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.paid_at || '').slice(0, 16))}</td><td>${hopCell(r.invoice_no)}</td>
        <td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopMoney(r.amount)}</td><td>${hopCell(r.method)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Payments', 'Collections against invoices',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('payment')">+ Record Payment</button>`, body);
}

async function renderHopComplaintsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/complaints') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Support', 'Complaints', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['Date', 'Customer', 'Project', 'Issue', 'Assigned', 'Status', 'Resolution Hrs', 'Feedback'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.complaint_date)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.issue)}</td><td>${hopCell(r.assigned_to)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.resolution_time_hours)}</td><td>${hopCell(r.feedback)}</td>
      </tr>`).join(''),
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
      view: 'quotations',
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
    openHopView(cfg.view);
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
window.hopSelectParty = hopSelectParty;
window.hopFilterParties = hopFilterParties;
window.hopPreviewVyaparBackup = hopPreviewVyaparBackup;
window.hopRunVyaparImport = hopRunVyaparImport;
window.hopPickVyaparBackup = hopPickVyaparBackup;
