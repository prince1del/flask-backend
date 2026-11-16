/* HoP staff permissions on the web: hide menus this login cannot open, and the owner's
 * "Staff permissions" screen (same server routes as the phone). */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };
  const OWNER_ONLY = new Set(['wipe_data', 'vyapar_import', 'staff_permissions']);
  const AREA = {
    parties: 'parties', invoices: 'sales', commission: 'sales', payments: 'sales',
    other_income: 'purchase', products: 'items', stock: 'items', godowns: 'items', stock_reorder: 'items', item_settings: 'items',
    banking: 'money', journal_entries: 'money', loans: 'money', fixed_assets: 'money',
    gst_reports: 'reports', books: 'reports', reports: 'reports', receivables: 'reports', overdue_bills: 'reports',
    profit: 'reports', tally_export: 'reports',
    company_profile: 'settings', app_settings: 'settings', audit_log: 'settings', print_theme: 'settings',
    recycle_bin: 'settings', financial_year: 'settings',
  };
  let perms = null;

  function areaOf(view) {
    if (AREA[view]) return AREA[view];
    if (view.startsWith('sale_')) return 'sales';
    if (view.startsWith('purchase_')) return 'purchase';
    return 'crm';
  }

  function canOpen(view) {
    if (!perms || perms.owner) return true;
    if (OWNER_ONLY.has(view)) return false;
    return (perms.areas[areaOf(view)] || 'edit') !== 'none';
  }

  async function apply() {
    try { perms = await hopApi('/api/v1/hop/my-permissions'); } catch (_) { return; }
    document.querySelectorAll('[data-hop-view]').forEach((b) => {
      const v = b.getAttribute('data-hop-view');
      if (v === 'staff_permissions') b.style.display = perms.owner ? '' : 'none';
      else b.style.display = canOpen(v) ? '' : 'none';
    });
  }

  async function render(mount) {
    let d;
    try { d = await hopApi('/api/v1/hop/staff-permissions'); } catch (e) {
      mount.innerHTML = hopModuleShell('Settings', 'Staff permissions', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    const levels = [['none', 'No access'], ['view', 'Can see'], ['edit', 'Can change']];
    const cards = (d.staff || []).map((s) => `<div class="nx-card" style="padding:14px;margin-bottom:12px;">
        <h3 style="margin:0 0 4px;">${esc(s.name)}</h3><p class="hop-doc-muted" style="margin:0 0 8px;">${esc(s.summary)}</p>
        <div class="hop-books-actions">Quick choice:
          ${Object.keys(d.presets || {}).map((p) => `<button type="button" class="nx-btn ${s.preset === p ? 'nx-btn-primary' : ''}" onclick="hopStaffPreset(${s.user_id},'${esc(p)}')">${esc(p)}</button>`).join('')}</div>
        <table class="inv-table"><tbody>${(d.areas || []).map((a) => `<tr><td>${esc(a.label)}</td><td>
          <select class="nx-input" onchange="hopStaffArea(${s.user_id},'${a.key}',this.value)">
            ${levels.map(([v, l]) => `<option value="${v}"${s.areas[a.key] === v ? ' selected' : ''}>${l}</option>`).join('')}
          </select></td></tr>`).join('')}</tbody></table></div>`).join('');
    window._hopStaffData = d;
    mount.innerHTML = hopModuleShell('Settings', 'Staff permissions',
      'What each staff login can see or change — you (the owner) always have full access', '',
      cards || '<p>No staff logins yet. Invite staff from Team, then set their permissions here.</p>');
  }

  async function put(body) {
    try {
      await hopApi('/api/v1/hop/staff-permissions', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      toast('Saved');
    } catch (e) { toast(e?.message || 'Could not save', 'err'); }
    const m = document.getElementById('hop-module-mount');
    if (m) render(m);
  }

  window.renderHopStaffPermsModule = render;
  window.hopStaffPreset = (uid, preset) => put({ user_id: uid, preset });
  window.hopStaffArea = async (uid, key, val) => {
    const s = (window._hopStaffData?.staff || []).find((x) => x.user_id === uid);
    const areas = { ...(s?.areas || {}), [key]: val };
    try {
      const chk = await hopApi('/api/v1/hop/staff-permissions/check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ areas }) });
      if (chk.warnings?.length) toast(chk.warnings.join(' '), 'warn');
    } catch (_) { /* advice only */ }
    put({ user_id: uid, areas });
  };
  window.hopPermsApply = apply;
  window.hopPermsCanOpen = canOpen;
})();
