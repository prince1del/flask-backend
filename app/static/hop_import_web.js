/* HoP import from Excel / CSV (items, parties) on the web.
 * Upload → preview with checks (nothing saved) → the user ticks rows → Save.
 * Same server routes as the phone: /api/v1/hop/import/<kind>/preview|commit. */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };
  const st = { kind: '', data: null, pick: new Set(), update: false };
  const STATUS = { new: 'New', existing: 'Already in app', duplicate_in_file: 'Repeated in file', error: 'Cannot import' };

  function overlay() {
    let el = document.getElementById('hop-import-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'hop-import-overlay';
      el.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:9999;display:flex;align-items:flex-start;justify-content:center;overflow:auto;padding:24px 12px;';
      document.body.appendChild(el);
    }
    return el;
  }

  function close() { document.getElementById('hop-import-overlay')?.remove(); }

  function render() {
    const d = st.data;
    const label = st.kind === 'items' ? 'items' : 'parties';
    let inner = `<p class="hop-doc-muted">Pick an Excel (.xlsx) or CSV file. Column names in the first row, e.g.
      ${st.kind === 'items' ? 'Item Name, HSN, GST %, Sale Price, Purchase Price, Opening Stock, Unit, MRP, Barcode'
        : 'Party Name, Phone, GSTIN, Type (Customer/Supplier), Group, City, State, Opening Balance'}.
      Nothing is saved until you press Save.</p>
      <input type="file" accept=".xlsx,.csv" onchange="hopImportUpload(this.files[0])" />`;
    if (d) {
      const counts = Object.entries(d.counts || {}).map(([k, v]) => `${STATUS[k] || k}: ${v}`).join(' · ');
      const cols = (d.columns || []).map((c) => `${esc(c.heading)} → ${esc(c.field)}`).join(', ');
      const fields = (d.columns || []).map((c) => c.field);
      const rows = (d.rows || []).map((r, i) => {
        const can = r.status === 'new' || (r.status === 'existing' && st.kind === 'items');
        return `<tr><td>${can ? `<input type="checkbox" ${st.pick.has(i) ? 'checked' : ''} onchange="hopImportPick(${i},this.checked)" />` : ''}</td>
          <td>${esc(STATUS[r.status] || r.status)}</td>
          ${fields.map((f) => `<td>${esc(r.data[f] ?? '')}</td>`).join('')}
          <td style="color:#b45309;">${esc((r.problems || []).join('; '))}</td></tr>`;
      }).join('');
      inner += `<p style="margin-top:10px;"><strong>${counts}</strong></p>
        <p class="hop-doc-muted">Columns found: ${cols}${d.ignored_columns?.length ? ` · Not used: ${esc(d.ignored_columns.join(', '))}` : ''}</p>
        ${st.kind === 'items' ? `<label style="display:flex;gap:6px;align-items:center;"><input type="checkbox" ${st.update ? 'checked' : ''}
          onchange="hopImportUpdate(this.checked)" /> Update items already in the app (price, stock, GST…) — bulk update</label>` : ''}
        <div style="max-height:52vh;overflow:auto;margin-top:8px;"><table class="inv-table"><thead><tr><th></th><th>Status</th>
          ${fields.map((f) => `<th>${esc(f.replace(/_/g, ' '))}</th>`).join('')}<th>Check</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
          <button type="button" class="nx-btn" onclick="hopImportClose()">Cancel</button>
          <button type="button" class="nx-btn nx-btn-primary" ${st.pick.size ? '' : 'disabled'} onclick="hopImportSave()">Save ${st.pick.size} ${label}</button>
        </div>`;
    }
    overlay().innerHTML = `<div class="nx-card" style="max-width:1100px;width:100%;padding:16px;">
      <div style="display:flex;align-items:center;"><h3 style="margin:0;flex:1;">Import ${label} from Excel</h3>
      <button type="button" class="nx-btn" onclick="hopImportClose()">Close</button></div>${inner}</div>`;
  }

  function autoPick() {
    st.pick = new Set();
    (st.data?.rows || []).forEach((r, i) => {
      if (r.status === 'new' || (st.update && r.status === 'existing' && st.kind === 'items')) st.pick.add(i);
    });
  }

  async function upload(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await fetchWithAuth(`/api/v1/hop/import/${st.kind}/preview`, { method: 'POST', body: fd });
      const out = await res.json().catch(() => ({}));
      if (!res.ok || out.success === false) throw new Error(out.error?.message || 'Could not read the file');
      st.data = out.data;
      autoPick();
    } catch (e) { toast(e?.message || 'Could not read the file', 'err'); }
    render();
  }

  async function save() {
    const rows = [...st.pick].map((i) => st.data.rows[i].data);
    try {
      const r = await hopApi(`/api/v1/hop/import/${st.kind}/commit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows, update_existing: st.update }),
      });
      toast(`Added ${r.added}, updated ${r.updated}, skipped ${r.skipped}${r.errors?.length ? ` · ${r.errors.length} problem(s)` : ''}`);
      if (r.errors?.length) console.warn('Import problems', r.errors);
      close();
      if (typeof openHopView === 'function') openHopView(st.kind === 'items' ? 'products' : 'parties');
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
  }

  window.hopImportOpen = (kind) => { Object.assign(st, { kind, data: null, pick: new Set(), update: false }); render(); };
  window.hopImportClose = close;
  window.hopImportUpload = upload;
  window.hopImportPick = (i, on) => { if (on) st.pick.add(i); else st.pick.delete(i); render(); };
  window.hopImportUpdate = (on) => { st.update = on; autoPick(); render(); };
  window.hopImportSave = save;
})();
