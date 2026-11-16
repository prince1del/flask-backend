/* HoP godowns on the web: list, add, stock per godown, move stock (same routes as the phone). */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };
  const fmt = (n) => String(Math.round(Number(n || 0) * 100) / 100);
  let last = null;

  async function render(mount) {
    let d;
    try { d = await hopApi('/api/v1/hop/godowns'); } catch (e) {
      mount.innerHTML = hopModuleShell('Stock', 'Godowns', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    last = d;
    const gs = d.godowns || [];
    const head = gs.map((g) => `<th class="num">${esc(g.name)}</th>`).join('');
    const rows = (d.stock || []).map((s) => `<tr><td>${esc(s.name)}</td><td class="num">${fmt(s.total)} ${esc(s.unit)}</td>
      ${gs.map((g) => `<td class="num">${fmt(s.by_godown[g.id] || 0)}</td>`).join('')}
      <td>${gs.length > 1 ? `<button type="button" class="nx-btn" onclick="hopGodownMoveForm(${s.product_id})">Move</button>` : ''}</td></tr>`).join('');
    const cards = gs.map((g) => `<div class="nx-card" style="padding:10px 14px;min-width:180px;"><strong>${esc(g.name)}</strong>${g.is_default ? ' <span class="hop-doc-muted">(main)</span>' : ''}
      <div class="hop-doc-muted">${g.items} item(s) · ${hopMoney(g.stock_value)}</div></div>`).join('');
    const body = `<div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:10px;">${cards}</div>
      <div class="hop-books-actions"><input id="hop-godown-new" class="nx-input" style="max-width:240px;" placeholder="New godown name" />
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopGodownAdd()">Add godown</button></div>
      <table class="inv-table"><thead><tr><th>Item</th><th class="num">Total</th>${head}<th></th></tr></thead><tbody>${rows}</tbody></table>`;
    mount.innerHTML = hopModuleShell('Stock', 'Godowns', 'Stock in each store / warehouse — pick the godown on a bill', '', body);
  }

  function rerender() {
    const m = document.getElementById('hop-module-mount');
    if (m) render(m);
  }

  window.renderHopGodownsModule = render;
  window.hopGodownAdd = async () => {
    const name = document.getElementById('hop-godown-new')?.value?.trim();
    if (!name) return;
    try {
      await hopApi('/api/v1/hop/godowns', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
      toast(`Added ${name}`);
      hopState.moneyGodowns = null;
      rerender();
    } catch (e) { toast(e?.message || 'Could not add', 'err'); }
  };
  window.hopGodownMoveForm = (pid) => {
    const s = (last?.stock || []).find((x) => x.product_id === pid);
    const gs = last?.godowns || [];
    const slot = document.getElementById('hop-form-slot');
    if (!s || !slot) return;
    const opts = gs.map((g) => `<option value="${g.id}">${esc(g.name)} (${fmt(s.by_godown[g.id] || 0)})</option>`).join('');
    slot.classList.remove('hidden');
    slot.innerHTML = `<strong>Move ${esc(s.name)}</strong>
      <div class="hop-form-grid" style="margin-top:8px;"><label>From<select id="hop-gm-from">${opts}</select></label>
      <label>To<select id="hop-gm-to">${opts}</select></label><label>Quantity<input id="hop-gm-qty" type="number" min="0" /></label></div>
      <div class="hop-form-actions"><button type="button" class="nx-btn nx-btn-primary" onclick="hopGodownMove(${pid})">Move</button>
      <button type="button" class="nx-btn" onclick="this.closest('#hop-form-slot').classList.add('hidden')">Cancel</button></div>`;
    const to = document.getElementById('hop-gm-to');
    if (to && gs[1]) to.value = String(gs[1].id);
  };
  window.hopGodownMove = async (pid) => {
    const body = {
      product_id: pid, from_godown_id: Number(document.getElementById('hop-gm-from')?.value),
      to_godown_id: Number(document.getElementById('hop-gm-to')?.value), qty: Number(document.getElementById('hop-gm-qty')?.value || 0),
    };
    try {
      await hopApi('/api/v1/hop/godowns/transfer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast('Moved');
      rerender();
    } catch (e) { toast(e?.message || 'Could not move', 'err'); }
  };
})();
