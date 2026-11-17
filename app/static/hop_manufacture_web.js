/* HoP Manufacturing on the web: raw-material list per item (✨ AI suggest), manufacture entries, undo.
   Same routes as the phone: /api/v1/hop/products/<id>/bom, /manufacture/plan, /manufacture. */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };
  const n = (v) => String(Math.round(Number(v || 0) * 1000) / 1000);
  const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let products = [];
  let bomRows = [];
  let bomFor = null;

  function itemOptions(selected, skipId) {
    return '<option value="">— pick an item —</option>' + products.filter((p) => p.id !== skipId)
      .map((p) => `<option value="${p.id}"${Number(selected) === p.id ? ' selected' : ''}>${esc(p.name)}${p.unit ? ` (${esc(p.unit)})` : ''}</option>`).join('');
  }

  async function render(mount) {
    let rows = [];
    try {
      products = (await hopApi('/api/v1/hop/products')) || [];
      rows = ((await hopApi('/api/v1/hop/manufacture')) || {}).rows || [];
    } catch (e) {
      mount.innerHTML = hopModuleShell('Stock', 'Manufacturing', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    const list = rows.length ? rows.map((r) => `<tr><td>${esc(hopPreviewDate ? hopPreviewDate(r.date) : r.date)}</td>
        <td><strong>${esc(r.product)}</strong><div class="hop-doc-muted">${r.used.map((u) => `${esc(u.name)} ${n(u.qty)}`).join(' · ')}</div></td>
        <td class="num">${n(r.qty)}</td><td class="num">${hopMoney(r.unit_cost)}</td>
        <td><button type="button" class="nx-btn" onclick="hopMfgUndo(${r.id})">Undo</button></td></tr>`).join('')
      : '<tr><td colspan="5" class="hop-doc-muted">Nothing made yet. Set the raw materials of an item, then tap Manufacture.</td></tr>';
    const body = `
      <div class="hop-books-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopMfgForm()">Manufacture</button>
        <button type="button" class="nx-btn" onclick="hopMfgBomForm()">Raw materials of an item</button>
      </div>
      <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
      <table class="inv-table"><thead><tr><th>Date</th><th>Made</th><th class="num">Qty</th><th class="num">Cost / piece</th><th></th></tr></thead>
      <tbody>${list}</tbody></table>`;
    mount.innerHTML = hopModuleShell('Stock', 'Manufacturing', 'Raw materials go down, the finished item goes up — with the cost per piece', '', body);
  }

  function rerender() {
    const m = document.getElementById('hop-module-mount');
    if (m) render(m);
  }

  function slot(html) {
    const s = document.getElementById('hop-form-slot');
    if (!s) return null;
    s.classList.remove('hidden');
    s.innerHTML = html;
    return s;
  }

  /* ---------- raw-material list ---------- */
  function drawBom() {
    const body = bomRows.map((r, i) => `<div class="hop-form-grid" style="grid-template-columns:2fr 1fr auto;align-items:end;">
        <label>Raw material<select onchange="hopMfgBomSet(${i},'raw_product_id',this.value)">${itemOptions(r.raw_product_id, bomFor)}</select></label>
        <label>Qty per piece<input type="number" min="0" step="any" value="${esc(r.qty)}" oninput="hopMfgBomSet(${i},'qty',this.value)" /></label>
        <button type="button" class="nx-btn" onclick="hopMfgBomDel(${i})">Remove</button></div>
        ${r.why ? `<div class="hop-doc-muted" style="margin:-4px 0 6px;">✨ ${esc(r.why)}</div>` : ''}`).join('');
    const box = document.getElementById('hop-mfg-bom-rows');
    if (box) box.innerHTML = body || '<p class="hop-doc-muted">No raw materials yet — add a line or let AI suggest.</p>';
  }

  window.hopMfgBomForm = () => {
    slot(`<strong>Raw materials for one piece</strong>
      <div class="hop-form-grid" style="margin-top:8px;"><label>Finished item<select id="hop-mfg-bom-item" onchange="hopMfgBomLoad(this.value)">${itemOptions(bomFor)}</select></label></div>
      <div id="hop-mfg-bom-rows"></div><div id="hop-mfg-bom-note" class="hop-doc-muted"></div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn" onclick="hopMfgBomAdd()">+ Add line</button>
        <button type="button" class="nx-btn" onclick="hopMfgBomAi()">✨ Suggest with AI</button>
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopMfgBomSave()">Save list</button>
        <button type="button" class="nx-btn" onclick="this.closest('#hop-form-slot').classList.add('hidden')">Close</button></div>`);
    if (bomFor) window.hopMfgBomLoad(bomFor); else drawBom();
  };
  window.hopMfgBomLoad = async (pid) => {
    bomFor = Number(pid) || null;
    bomRows = [];
    if (bomFor) {
      try { bomRows = ((await hopApi(`/api/v1/hop/products/${bomFor}/bom`)) || {}).raw || []; } catch (e) { toast(e.message, 'err'); }
    }
    drawBom();
  };
  window.hopMfgBomSet = (i, k, v) => { if (bomRows[i]) bomRows[i][k] = v; };
  window.hopMfgBomAdd = () => { bomRows.push({ raw_product_id: '', qty: '' }); drawBom(); };
  window.hopMfgBomDel = (i) => { bomRows.splice(i, 1); drawBom(); };
  window.hopMfgBomAi = async () => {
    if (!bomFor) { toast('Pick the finished item first', 'err'); return; }
    const note = document.getElementById('hop-mfg-bom-note');
    if (note) note.textContent = '✨ Asking AI…';
    try {
      const d = await hopApi(`/api/v1/hop/products/${bomFor}/bom/ai-suggest`, { method: 'POST' });
      bomRows = (d.raw || []).map((r) => ({ raw_product_id: r.raw_product_id, qty: r.qty, why: r.why }));
      drawBom();
      const miss = (d.not_in_items || []).map((m) => `${m.name} (${n(m.qty)})`).join(', ');
      if (note) note.textContent = `${d.note || ''}${miss ? ` Not in your items yet: ${miss}.` : ''}`;
    } catch (e) { if (note) note.textContent = e?.message || 'AI could not suggest'; }
  };
  window.hopMfgBomSave = async () => {
    if (!bomFor) { toast('Pick the finished item first', 'err'); return; }
    const raw = bomRows.filter((r) => r.raw_product_id).map((r) => ({ raw_product_id: Number(r.raw_product_id), qty: Number(r.qty) }));
    try {
      const d = await hopApi(`/api/v1/hop/products/${bomFor}/bom`, json('PUT', { raw }));
      toast(`Saved — raw material cost ${hopMoney(d.cost_per_piece)} per piece`);
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
  };

  /* ---------- manufacture ---------- */
  window.hopMfgForm = () => {
    slot(`<strong>Manufacture</strong>
      <div class="hop-form-grid" style="margin-top:8px;">
        <label>Item to make<select id="hop-mfg-item">${itemOptions(bomFor)}</select></label>
        <label>Quantity<input id="hop-mfg-qty" type="number" min="0" step="any" /></label>
        <label>Extra cost (labour, power…)<input id="hop-mfg-extra" type="number" min="0" step="any" value="0" /></label>
        <label>Date<input id="hop-mfg-date" type="date" value="${new Date().toISOString().slice(0, 10)}" /></label>
        <label style="grid-column:1/-1;">Note<input id="hop-mfg-notes" /></label></div>
      <div id="hop-mfg-plan"></div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn" onclick="hopMfgPlan()">Check stock & cost</button>
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopMfgSave()">Save</button>
        <button type="button" class="nx-btn" onclick="this.closest('#hop-form-slot').classList.add('hidden')">Cancel</button></div>`);
  };
  function formBody() {
    return {
      product_id: Number(document.getElementById('hop-mfg-item')?.value || 0),
      qty: Number(document.getElementById('hop-mfg-qty')?.value || 0),
      extra_cost: Number(document.getElementById('hop-mfg-extra')?.value || 0),
      entry_date: document.getElementById('hop-mfg-date')?.value || '',
      notes: document.getElementById('hop-mfg-notes')?.value || '',
    };
  }
  window.hopMfgPlan = async () => {
    const box = document.getElementById('hop-mfg-plan');
    try {
      const p = await hopApi('/api/v1/hop/manufacture/plan', json('POST', formBody()));
      if (!box) return;
      box.innerHTML = `<table class="inv-table" style="margin-top:8px;"><thead><tr><th>Raw material</th><th class="num">Needed</th><th class="num">In stock</th><th class="num">Cost</th></tr></thead>
        <tbody>${p.raw.map((r) => `<tr${r.short > 0 ? ' style="color:#b91c1c;"' : ''}><td>${esc(r.name)}</td><td class="num">${n(r.needed)} ${esc(r.unit)}</td>
          <td class="num">${n(r.stock_qty)}</td><td class="num">${hopMoney(r.cost)}</td></tr>`).join('')}</tbody></table>
        <p><strong>Cost per piece ${hopMoney(p.unit_cost)}</strong> · total ${hopMoney(p.raw_cost + p.extra_cost)} · you can make ${n(p.can_make_now)} now</p>
        ${p.warnings.map((w) => `<p class="nx-oc-error">⚠ ${esc(w)}</p>`).join('')}`;
    } catch (e) { if (box) box.innerHTML = `<p class="nx-oc-error">${esc(e.message)}</p>`; }
  };
  window.hopMfgSave = async () => {
    try {
      const d = await hopApi('/api/v1/hop/manufacture', json('POST', formBody()));
      toast(`Made ${n(d.qty)} × ${d.product} at ${hopMoney(d.unit_cost)} each`);
      rerender();
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
  };
  window.hopMfgUndo = async (id) => {
    if (!confirm('Undo this manufacture entry? Raw materials go back into stock and the made items come out.')) return;
    try {
      await hopApi(`/api/v1/hop/manufacture/${id}`, { method: 'DELETE' });
      toast('Undone');
      rerender();
    } catch (e) { toast(e?.message || 'Could not undo', 'err'); }
  };

  window.renderHopManufactureModule = render;
})();
