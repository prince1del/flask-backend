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
    const isOwner = typeof window.hopPermsIsOwner !== 'function' || window.hopPermsIsOwner();
    const canBackup = isOwner || (typeof window.hopPermsCanBackup === 'function' && window.hopPermsCanBackup());
    const backup = !canBackup ? `<div class="nx-card" style="padding:14px;margin-bottom:12px;"><h3 style="margin:0 0 10px;">Backup</h3><p class="hop-doc-muted">Only the company owner can download or restore backups.</p></div>` : `<div class="nx-card" style="padding:14px;margin-bottom:12px;"><h3 style="margin:0 0 10px;">Backup</h3>
      <p class="hop-doc-muted">Download all your business data (parties, items, bills, payments, bank…) as one locked .kunwar file — it opens only in NEXORA, only for your company.</p>
      <button type="button" class="nx-btn" onclick="hopBackupDownload()">Download backup</button>
      ${isOwner ? `<button type="button" class="nx-btn" onclick="document.getElementById('hop-restore-file').click()">Restore a backup into a new firm</button>` : ''}
      <input type="file" id="hop-restore-file" style="display:none" onchange="hopRestorePick(this)" />
      <p class="hop-doc-muted">Restoring never changes your current data — the backup becomes a separate firm you can switch to.</p>
      <div id="hop-restore-out"></div>
      <h4 style="margin:16px 0 6px;">Backups on server</h4>
      <p class="hop-doc-muted">Saved every 6 hours when your data changes, and before import / wipe / replace. Kept 30 days — open them from any phone or computer with your login.</p>
      ${isOwner ? '<button type="button" class="nx-btn" onclick="hopSrvBackupNow()">Save a backup now</button>' : ''}
      <div id="hop-srv-backups"><p class="hop-doc-muted">Loading…</p></div></div>
      ${isOwner ? '<div class="nx-card" style="padding:14px;margin-bottom:12px;"><h3 style="margin:0 0 6px;">Government &amp; approvals</h3><p class="hop-doc-muted">E-Invoice, E-Way Bill, GSTIN verify, SMS and online payments. Each starts Off; use Test with the provider\'s sandbox keys, then Live after approval. Keys are stored locked and never shown again.</p><div id="hop-gov"></div></div>' : ''}`;
    mount.innerHTML = hopModuleShell('Settings', 'Settings', 'Bills, GST, print and parties — applies on phone and web', '', body + backup);
    if (canBackup) srvList(isOwner);
    if (isOwner) govRender();
  }

  window.renderHopAppSettingsModule = render;
  window.hopBackupDownload = async () => {
    try {
      const res = await fetchWithAuth('/api/v1/hop/backup');
      if (!res.ok) throw new Error(`Backup failed (${res.status})`);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `nexora_backup_${new Date().toISOString().slice(0, 10)}.kunwar`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (e) { toast(e?.message || 'Backup failed', 'err'); }
  };
  const restoreSend = async (file, name, preview) => {
    const fd = new FormData();
    fd.append('backup_file', file, file.name || 'backup.kunwar');
    if (name) fd.append('name', name);
    return hopApi(`/api/v1/hop/firms/restore${preview ? '?preview=1' : ''}`, { method: 'POST', body: fd });
  };
  window.hopRestorePick = async (input) => {
    const file = input.files && input.files[0];
    input.value = '';
    const out = document.getElementById('hop-restore-out');
    if (!file || !out) return;
    out.innerHTML = '<p>Reading the backup…</p>';
    try {
      const p = await restoreSend(file, '', true);
      const name = window.prompt(`In this backup: ${p.parties} parties, ${p.items} items, ${p.documents} bills / documents.

Name of the new firm:`,
        `${p.firm_name || 'Restored firm'} (restored)`);
      if (!name) { out.innerHTML = ''; return; }
      out.innerHTML = '<p>Restoring… this can take a minute.</p>';
      const d = await restoreSend(file, name.trim(), false);
      out.innerHTML = `<p>✓ “${esc(d.name)}” is ready: ${d.parties} parties, ${d.items} items, ${d.documents} bills. Switch to it from the firm name at the top.</p>`;
    } catch (e) { out.innerHTML = `<p class="nx-oc-error">${esc(e.message)}</p>`; }
  };
  async function srvList(isOwner) {
    const box = document.getElementById('hop-srv-backups');
    if (!box) return;
    try {
      const d = await hopApi('/api/v1/hop/backups');
      box.innerHTML = (d.backups || []).length ? `<table class="inv-table"><tbody>${d.backups.slice(0, 15).map((b) => `<tr>
        <td>${esc(b.at_ist)}</td><td>${esc(b.reason)}</td><td>${esc(b.size_kb)} KB</td><td>
        <button type="button" class="nx-btn" onclick="hopSrvDownload('${esc(b.name)}')">Download</button>
        ${isOwner ? `<button type="button" class="nx-btn" onclick="hopSrvRestore('${esc(b.name)}','new')">New firm</button>
        <button type="button" class="nx-btn" onclick="hopSrvRestore('${esc(b.name)}','replace')">Replace this firm</button>` : ''}</td></tr>`).join('')}</tbody></table>`
        : '<p class="hop-doc-muted">No backup on the server yet.</p>';
    } catch (e) { box.innerHTML = `<p class="nx-oc-error">${esc(e.message)}</p>`; }
  }
  window.hopSrvBackupNow = async () => {
    try { await hopApi('/api/v1/hop/backups', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); toast('Backup saved on the server'); srvList(true); }
    catch (e) { toast(e.message, 'err'); }
  };
  window.hopSrvDownload = async (name) => {
    try {
      const res = await fetchWithAuth(`/api/v1/hop/backups/${encodeURIComponent(name)}`);
      if (!res.ok) throw new Error(`Download failed (${res.status})`);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(await res.blob());
      a.download = `nexora_backup_${name}`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    } catch (e) { toast(e.message, 'err'); }
  };
  window.hopSrvRestore = async (name, mode) => {
    const body = { mode };
    if (mode === 'new') {
      const n = window.prompt('Name of the new firm:', 'Restored firm');
      if (!n) return;
      body.name = n.trim();
    } else {
      if (!window.confirm('Replace the data of this firm with this backup?\n\nToday\'s data is saved first as a backup on the server, so this can be undone.')) return;
      const pw = window.prompt('Type your login password to confirm:');
      if (!pw) return;
      body.password = pw;
    }
    try {
      const d = await hopApi(`/api/v1/hop/backups/${encodeURIComponent(name)}/restore`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast(mode === 'new' ? `“${d.name}” is ready — switch to it from the firm name` : 'Done — this firm is back to the backup');
      srvList(true);
    } catch (e) { toast(e.message, 'err'); }
  };
  async function govRender() {
    const box = document.getElementById('hop-gov');
    if (!box) return;
    let list;
    try { list = await hopApi('/api/v1/hop/gov'); } catch (e) { box.innerHTML = `<p class="nx-oc-error">${esc(e.message)}</p>`; return; }
    box.innerHTML = list.map((s) => `<div style="border-top:1px solid rgba(148,163,184,.25);padding:10px 0;">
      <h4 style="margin:0 0 4px;">${esc(s.title)} — <span class="hop-doc-muted">${esc(s.mode.toUpperCase())}</span></h4>
      <p class="hop-doc-muted" style="margin:0 0 6px;">${esc(s.apply)} <a href="${esc(s.where)}" target="_blank" rel="noopener">${esc(s.where)}</a></p>
      ${s.webhook_url ? `<p class="hop-doc-muted">Webhook address for Razorpay: <code>${esc(s.webhook_url)}</code></p>` : ''}
      ${s.fields.map((f) => `<label style="display:block;margin:4px 0;">${esc(f.label)}
        ${f.key === 'public_key' ? `<textarea class="nx-input" rows="2" data-gov="${s.service}" data-k="${f.key}" placeholder="${f.saved ? 'saved ✓ — paste again to change' : ''}"></textarea>`
          : `<input class="nx-input" ${f.secret ? 'type="password"' : ''} data-gov="${s.service}" data-k="${f.key}" value="${esc(f.value)}" placeholder="${esc(f.hint || '')}">`}</label>`).join('')}
      <div class="hop-books-actions">
        ${['off', 'test', 'live'].map((m) => `<button type="button" class="nx-btn ${s.mode === m ? 'nx-btn-primary' : ''}" onclick="hopGovSave('${s.service}','${m}')">${m === 'off' ? 'Off' : m === 'test' ? 'Test (sandbox)' : 'Live'}</button>`).join('')}
        <button type="button" class="nx-btn" onclick="hopGovSave('${s.service}', null)">Save keys</button>
        <button type="button" class="nx-btn" onclick="hopGovTest('${s.service}')">Test now</button></div>
      ${s.last_test ? `<p class="${s.last_test.ok ? 'hop-doc-muted' : 'nx-oc-error'}">Last test: ${s.last_test.ok ? '✓' : '✗'} ${esc(s.last_test.message)}</p>` : ''}</div>`).join('');
  }
  window.hopGovSave = async (service, mode) => {
    const values = {};
    document.querySelectorAll(`[data-gov="${service}"]`).forEach((el) => { values[el.dataset.k] = el.value; });
    try {
      await hopApi(`/api/v1/hop/gov/${service}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, values }) });
      toast('Saved'); govRender();
    } catch (e) { toast(e.message, 'err'); }
  };
  window.hopGovTest = async (service) => {
    const body = {};
    if (service === 'sms') { const ph = window.prompt('Send a test SMS to which mobile number?'); if (!ph) return; body.phone = ph; }
    try {
      const r = await hopApi(`/api/v1/hop/gov/${service}/test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast(r.message, r.ok ? 'ok' : 'err'); govRender();
    } catch (e) { toast(e.message, 'err'); }
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
