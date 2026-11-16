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
    const backup = `<div class="nx-card" style="padding:14px;margin-bottom:12px;"><h3 style="margin:0 0 10px;">Backup</h3>
      <p class="hop-doc-muted">Download all your House of Prizm data (parties, items, bills, payments, bank…) as one zip file.</p>
      <button type="button" class="nx-btn" onclick="hopBackupDownload()">Download backup</button></div>`;
    mount.innerHTML = hopModuleShell('Settings', 'Settings', 'Bills, GST, print and parties — applies on phone and web', '', body + backup);
  }

  window.renderHopAppSettingsModule = render;
  window.hopBackupDownload = async () => {
    try {
      const res = await fetchWithAuth('/api/v1/hop/backup');
      if (!res.ok) throw new Error(`Backup failed (${res.status})`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `nexora_hop_backup_${new Date().toISOString().slice(0, 10)}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (e) { toast(e?.message || 'Backup failed', 'err'); }
  };
  window.hopAppSettingSave = (key, value, type) => save(key, type === 'number' ? Number(value || 0) : value);
})();

/* Audit trail: who created / changed / deleted what (same list as the phone). */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const f = { q: '', user: '', from: '', to: '' };

  async function render(mount) {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
    let d;
    try { d = await hopApi(`/api/v1/hop/audit-log${qs ? `?${qs}` : ''}`); } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Audit trail', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    const rows = (d.rows || []).map((r) => `<tr><td>${esc((r.at || '').replace('T', ' ').slice(0, 16))}</td>
      <td>${esc(r.user_name || '')}</td><td>${esc(r.action)}</td><td>${esc(r.entity)}</td><td>${esc(r.text)}</td></tr>`).join('');
    const filters = `<div class="hop-books-actions" style="flex-wrap:wrap;">
      <input class="nx-input" style="max-width:220px;" placeholder="Search bill no / name" value="${esc(f.q)}" onchange="hopAuditSet('q',this.value)" />
      <select class="nx-input" style="max-width:180px;" onchange="hopAuditSet('user',this.value)"><option value="">Everyone</option>
        ${(d.users || []).map((u) => `<option${f.user === u ? ' selected' : ''}>${esc(u)}</option>`).join('')}</select>
      <input type="date" class="nx-input" style="max-width:160px;" value="${esc(f.from)}" onchange="hopAuditSet('from',this.value)" />
      <input type="date" class="nx-input" style="max-width:160px;" value="${esc(f.to)}" onchange="hopAuditSet('to',this.value)" />
    </div>`;
    mount.innerHTML = hopModuleShell('Settings', 'Audit trail', 'Every change made on phone or web — who, when, and the old amount', '',
      filters + hopTable(['When (UTC)', 'Who', 'Action', 'What', 'Details'], rows, 5, { label: 'Changes', count: (d.rows || []).length }));
  }

  window.renderHopAuditModule = render;
  window.hopAuditSet = (k, v) => {
    f[k] = v;
    const m = document.getElementById('hop-module-mount') || document.querySelector('[data-hop-mount]');
    if (m) render(m); else if (typeof openHopView === 'function') openHopView('audit_log');
  };
})();
