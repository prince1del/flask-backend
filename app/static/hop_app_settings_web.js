/* HoP Settings (Transaction, Taxes & GST, Print, Party …) on the web.
 * Built from the server's list (GET /api/v1/hop/app-settings) — the phone uses the same list. */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };

  async function save(key, value) {
    try {
      const d = await hopApi('/api/v1/hop/app-settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [key]: value }),
      });
      hopState.appSettings = d.values;
      toast('Saved');
      return true;
    } catch (e) { toast(e?.message || 'Could not save', 'err'); return false; }
  }

  function fieldHtml(f) {
    const help = f.help ? `<div class="hop-doc-muted" style="font-size:.82rem;margin-top:2px;">${esc(f.help)}</div>` : '';
    if (f.type === 'bool') {
      return `<label style="display:flex;gap:10px;align-items:center;cursor:pointer;">
        <input type="checkbox" ${f.value ? 'checked' : ''} onchange="hopAppSettingSave('${f.key}', this.checked)" />
        <span>${esc(f.label)}</span></label>${help}`;
    }
    if (f.type === 'select') {
      return `<div>${esc(f.label)}</div><select class="nx-input" style="max-width:320px;" onchange="hopAppSettingSave('${f.key}', this.value)">
        ${(f.options || []).map(([v, l]) => `<option value="${esc(v)}"${f.value === v ? ' selected' : ''}>${esc(l)}</option>`).join('')}
      </select>${help}`;
    }
    const val = Array.isArray(f.value) ? f.value.join(', ') : (f.value ?? '');
    return `<div>${esc(f.label)}</div>
      <div style="display:flex;gap:8px;"><input class="nx-input" id="hop-set-${f.key}" value="${esc(val)}" style="max-width:420px;"
        ${f.type === 'number' ? 'type="number" min="0"' : ''} />
      <button type="button" class="nx-btn" onclick="hopAppSettingSave('${f.key}', document.getElementById('hop-set-${f.key}').value, '${f.type}')">Save</button></div>${help}`;
  }

  async function render(mount) {
    let d;
    try { d = await hopApi('/api/v1/hop/app-settings'); } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Settings', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    hopState.appSettings = d.values;
    const body = d.sections.map((s) => `
      <div class="nx-card" style="padding:14px;margin-bottom:12px;">
        <h3 style="margin:0 0 10px;">${esc(s.title)}</h3>
        ${s.fields.map((f) => `<div style="padding:8px 0;border-top:1px solid rgba(148,163,184,.15);">${fieldHtml(f)}</div>`).join('')}
      </div>`).join('');
    mount.innerHTML = hopModuleShell('Settings', 'Settings', 'Bills, GST, print and parties — applies on phone and web', '', body);
  }

  window.renderHopAppSettingsModule = render;
  window.hopAppSettingSave = (key, value, type) => save(key, type === 'number' ? Number(value || 0) : value);
})();
