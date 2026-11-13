/* Plan (7-day trial + activation codes), super-admin code desk, company invites,
 * and "join a company with an invite code" sign-up. Talks only to
 * /api/v1/subscription/*, /api/v1/invites/* and /api/v1/signup. */
(function () {
  'use strict';

  const SECRET_QUESTIONS = [
    'What city were you born in?',
    "What is your mother's maiden name?",
    'What was the name of your first school?',
    'What was the name of your first pet?',
    'What is your favorite childhood book?',
  ];
  const ROLE_LABELS = {
    sales_executive: 'Sales executive',
    distributor: 'Distributor',
    retailer: 'Retailer',
    hop_admin: 'Company admin',
  };

  let lastStatus = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function fmtDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function toast(msg, type) {
    if (typeof nexoraToast === 'function') nexoraToast(msg, type || 'info');
  }

  async function api(url, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    const res = typeof fetchWithAuth === 'function'
      ? await fetchWithAuth(url, opts)
      : await fetch(url, { ...opts, credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) {
      throw new Error(data.error?.message || `Request failed (${res.status})`);
    }
    return data.data;
  }

  function modal(id, title, bodyHtml) {
    let el = document.getElementById(id);
    if (el) el.remove();
    el = document.createElement('div');
    el.id = id;
    el.className = 'modal';
    el.style.zIndex = '10050';
    el.innerHTML = `
      <div class="modal-content" style="max-height:88vh;overflow-y:auto;max-width:560px;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
          <h2 style="margin:0;">${esc(title)}</h2>
          <button type="button" class="btn btn-secondary" data-close>Close</button>
        </div>
        <div data-body style="margin-top:14px;">${bodyHtml}</div>
      </div>`;
    el.querySelector('[data-close]').addEventListener('click', () => el.remove());
    document.body.appendChild(el);
    return el;
  }

  // ---------- Plan status / banner / lock ----------

  function renderBanner(st) {
    let bar = document.getElementById('nx-plan-banner');
    const show = st && (st.status === 'trial' || st.status === 'active') && st.days_left != null && st.days_left <= 3;
    if (!show) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'nx-plan-banner';
      bar.style.cssText = 'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:10040;'
        + 'background:#7c2d12;color:#fff;padding:10px 16px;border-radius:12px;box-shadow:0 6px 20px rgba(0,0,0,.3);'
        + 'display:flex;gap:12px;align-items:center;font-size:0.9rem;';
      document.body.appendChild(bar);
    }
    const what = st.status === 'trial' ? 'Free trial' : 'Your plan';
    const when = st.days_left <= 0 ? 'ends today' : `ends in ${st.days_left} day${st.days_left === 1 ? '' : 's'}`;
    bar.innerHTML = `<span>${what} ${when}.</span>
      <button type="button" class="btn btn-primary" style="padding:4px 12px;">Enter code</button>`;
    bar.querySelector('button').addEventListener('click', () => openPlan());
  }

  async function refresh() {
    try {
      const st = await api('/api/v1/subscription/status');
      lastStatus = st;
      const adminItem = document.getElementById('nx-menu-sub-codes');
      if (adminItem) adminItem.classList.toggle('hidden', !st.is_super_admin);
      renderBanner(st);
      if (st.status === 'expired') showLock();
      updateInviteMenu();
      return st;
    } catch (e) {
      return null;
    }
  }

  async function updateInviteMenu() {
    const item = document.getElementById('nx-menu-invites');
    if (!item) return;
    try {
      const d = await api('/api/v1/invites');
      item.classList.toggle('hidden', !d.can_invite);
    } catch (e) {
      item.classList.add('hidden');
    }
  }

  function redeemForm(onDone) {
    const wrap = document.createElement('form');
    wrap.innerHTML = `
      <div class="form-group">
        <label for="nx-sub-code">Activation code</label>
        <input id="nx-sub-code" type="text" placeholder="XXXX-XXXX-XXXX" autocomplete="off" autocapitalize="characters" spellcheck="false" style="text-transform:uppercase;letter-spacing:2px;">
      </div>
      <button type="submit" class="btn btn-primary">Activate</button>
      <p class="error" data-err style="margin-top:8px;"></p>`;
    wrap.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const errEl = wrap.querySelector('[data-err]');
      errEl.textContent = '';
      const code = wrap.querySelector('#nx-sub-code').value.trim();
      if (!code) { errEl.textContent = 'Enter the code you received.'; return; }
      try {
        const res = await api('/api/v1/subscription/redeem', { method: 'POST', body: { code } });
        toast(`Activated for ${res.duration_days} days — valid till ${fmtDate(res.ends_at)}`, 'success');
        onDone && onDone(res);
      } catch (e) {
        errEl.textContent = e.message;
      }
    });
    return wrap;
  }

  function showLock() {
    if (document.getElementById('nx-plan-lock')) return;
    const el = document.createElement('div');
    el.id = 'nx-plan-lock';
    el.className = 'modal';
    el.style.zIndex = '10060';
    el.innerHTML = `
      <div class="modal-content" style="max-width:460px;">
        <h2 style="margin-top:0;">Your NEXORA access has ended</h2>
        <p style="color:#94a3b8;">Your free trial or plan is over. Enter the activation code you got from NEXORA to continue. Your data is safe.</p>
        <div data-form></div>
        <button type="button" class="btn btn-secondary" data-logout style="margin-top:6px;">Logout</button>
      </div>`;
    el.querySelector('[data-form]').appendChild(redeemForm(() => {
      el.remove();
      window.location.reload();
    }));
    el.querySelector('[data-logout]').addEventListener('click', () => {
      el.remove();
      if (typeof logout === 'function') logout();
    });
    document.body.appendChild(el);
  }

  async function openPlan() {
    if (typeof closeUserProfileMenu === 'function') closeUserProfileMenu();
    const st = (await refresh()) || lastStatus || {};
    const label = {
      unlimited: 'Unlimited — no expiry',
      trial: `Free trial — ends ${fmtDate(st.ends_at)} (${st.days_left} days left)`,
      active: `Active — valid till ${fmtDate(st.ends_at)} (${st.days_left} days left)`,
      expired: `Expired on ${fmtDate(st.ends_at)}`,
    }[st.status] || 'Unknown';
    const el = modal('nx-plan-modal', 'My plan', `<p><strong>${esc(label)}</strong></p>`);
    if (st.status !== 'unlimited') {
      el.querySelector('[data-body]').appendChild(redeemForm(() => { el.remove(); refresh(); }));
    }
  }

  // ---------- Super admin: activation codes ----------

  async function openCodeDesk() {
    if (typeof closeUserProfileMenu === 'function') closeUserProfileMenu();
    const el = modal('nx-code-desk', 'Subscription codes', `
      <form data-make style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
        <div class="form-group"><label>Days</label>
          <select name="duration_days">
            <option value="7">7 days</option><option value="30" selected>30 days</option>
            <option value="90">90 days</option><option value="180">180 days</option><option value="365">365 days</option>
          </select></div>
        <div class="form-group"><label>How many codes</label><input name="count" type="number" min="1" max="50" value="1"></div>
        <div class="form-group"><label>Only for login (optional)</label><input name="for_username" placeholder="username"></div>
        <div class="form-group"><label>Note (optional)</label><input name="note" placeholder="e.g. paid by UPI"></div>
        <button type="submit" class="btn btn-primary" style="grid-column:1/-1;">Generate code</button>
      </form>
      <div data-new></div>
      <h3 style="margin-top:18px;">Logins</h3><div data-users>Loading…</div>
      <h3 style="margin-top:18px;">Recent codes</h3><div data-codes>Loading…</div>`);
    const body = el.querySelector('[data-body]');

    async function load() {
      try {
        const [u, c] = await Promise.all([
          api('/api/v1/subscription/users'),
          api('/api/v1/subscription/codes'),
        ]);
        body.querySelector('[data-users]').innerHTML = `<table style="width:100%;font-size:0.85rem;">
          <tr><th align="left">Login</th><th align="left">Status</th><th align="left">Till</th></tr>
          ${u.users.map((x) => `<tr><td>${esc(x.username)}</td><td>${esc(x.status)}${x.days_left != null ? ` (${x.days_left}d)` : ''}</td><td>${esc(fmtDate(x.ends_at))}</td></tr>`).join('')}
          </table>`;
        body.querySelector('[data-codes]').innerHTML = c.codes.length ? `<table style="width:100%;font-size:0.85rem;">
          <tr><th align="left">Code</th><th align="left">Days</th><th align="left">Status</th><th></th></tr>
          ${c.codes.map((x) => `<tr><td style="font-family:monospace;">${esc(x.code)}</td><td>${x.duration_days}</td>
            <td>${esc(x.status)}${x.redeemed_by_username ? ` · ${esc(x.redeemed_by_username)}` : ''}${x.for_username ? ` · for ${esc(x.for_username)}` : ''}</td>
            <td>${x.status === 'unused' ? `<button type="button" class="btn btn-secondary" data-revoke="${esc(x.code)}" style="padding:2px 8px;">Cancel</button>` : ''}</td></tr>`).join('')}
          </table>` : '<p>No codes yet.</p>';
      } catch (e) {
        body.querySelector('[data-codes]').textContent = e.message;
      }
    }

    body.addEventListener('click', async (ev) => {
      const code = ev.target?.getAttribute?.('data-revoke');
      if (!code) return;
      try {
        await api(`/api/v1/subscription/codes/${encodeURIComponent(code)}/revoke`, { method: 'POST' });
        load();
      } catch (e) { toast(e.message, 'error'); }
    });

    body.querySelector('[data-make]').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        const res = await api('/api/v1/subscription/codes', {
          method: 'POST',
          body: {
            duration_days: Number(f.get('duration_days')),
            count: Number(f.get('count') || 1),
            for_username: String(f.get('for_username') || '').trim() || null,
            note: String(f.get('note') || '').trim() || null,
          },
        });
        body.querySelector('[data-new]').innerHTML = `<div style="margin-top:10px;padding:12px;border-radius:10px;background:rgba(34,197,94,.12);">
          <strong>New code${res.codes.length > 1 ? 's' : ''}:</strong>
          ${res.codes.map((x) => `<div style="font-family:monospace;font-size:1.15rem;letter-spacing:2px;margin-top:4px;">${esc(x.code)} <small>(${x.duration_days} days)</small></div>`).join('')}
        </div>`;
        load();
      } catch (e) { toast(e.message, 'error'); }
    });
    load();
  }

  // ---------- Company admin: invite codes ----------

  async function openInvites() {
    if (typeof closeUserProfileMenu === 'function') closeUserProfileMenu();
    const el = modal('nx-invite-desk', 'Team & invites', '<p>Loading…</p>');
    const body = el.querySelector('[data-body]');

    async function load(newInvite) {
      try {
        const [inv, mem] = await Promise.all([api('/api/v1/invites'), api('/api/v1/invites/members')]);
        if (!inv.can_invite) { body.innerHTML = '<p>Only your company owner or admin can invite people.</p>'; return; }
        body.innerHTML = `
          <p style="color:#94a3b8;margin-top:0;">Share a code with a teammate. They choose “Join with company code” on the login screen and land in <strong>${esc(inv.company_name || 'your company')}</strong>.</p>
          <form data-make style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div class="form-group"><label>Role</label><select name="role">
              ${inv.roles.map((r) => `<option value="${esc(r)}">${esc(ROLE_LABELS[r] || r)}</option>`).join('')}</select></div>
            <div class="form-group"><label>How many people</label><input name="max_uses" type="number" min="1" max="100" value="1"></div>
            <div class="form-group"><label>Valid for (days)</label><input name="expires_in_days" type="number" min="1" max="90" value="7"></div>
            <div class="form-group"><label>Note (optional)</label><input name="note" placeholder="e.g. Rahul — Delhi"></div>
            <button type="submit" class="btn btn-primary" style="grid-column:1/-1;">Create invite code</button>
          </form>
          ${newInvite ? `<div style="margin-top:10px;padding:12px;border-radius:10px;background:rgba(34,197,94,.12);">
            Invite code: <span style="font-family:monospace;font-size:1.2rem;letter-spacing:2px;">${esc(newInvite.code)}</span>
            <small>(${esc(ROLE_LABELS[newInvite.role] || newInvite.role)}, ${newInvite.max_uses} use${newInvite.max_uses > 1 ? 's' : ''}, till ${esc(fmtDate(newInvite.expires_at))})</small></div>` : ''}
          <h3 style="margin-top:18px;">Codes</h3>
          ${inv.invites.length ? `<table style="width:100%;font-size:0.85rem;">
            <tr><th align="left">Code</th><th align="left">Role</th><th align="left">Used</th><th align="left">Status</th><th></th></tr>
            ${inv.invites.map((x) => `<tr><td style="font-family:monospace;">${esc(x.code)}</td><td>${esc(ROLE_LABELS[x.role] || x.role)}</td>
              <td>${x.uses}/${x.max_uses}${x.joined.length ? ` · ${x.joined.map((j) => esc(j.username)).join(', ')}` : ''}</td><td>${esc(x.status)}</td>
              <td>${x.status === 'active' ? `<button type="button" class="btn btn-secondary" data-revoke="${esc(x.code)}" style="padding:2px 8px;">Stop</button>` : ''}</td></tr>`).join('')}
            </table>` : '<p>No invite codes yet.</p>'}
          <h3 style="margin-top:18px;">Members</h3>
          <ul style="margin:0;padding-left:18px;">${mem.members.map((m) => `<li>${esc(m.full_name || m.username)} <small>(${esc(m.username)} · ${esc(ROLE_LABELS[m.role] || m.role)})</small></li>`).join('')}</ul>`;
        body.querySelector('[data-make]').addEventListener('submit', async (ev) => {
          ev.preventDefault();
          const f = new FormData(ev.target);
          try {
            const created = await api('/api/v1/invites', {
              method: 'POST',
              body: {
                role: f.get('role'),
                max_uses: Number(f.get('max_uses') || 1),
                expires_in_days: Number(f.get('expires_in_days') || 7),
                note: String(f.get('note') || '').trim() || null,
              },
            });
            load(created);
          } catch (e) { toast(e.message, 'error'); }
        });
      } catch (e) {
        body.innerHTML = `<p class="error">${esc(e.message)}</p>`;
      }
    }

    body.addEventListener('click', async (ev) => {
      const code = ev.target?.getAttribute?.('data-revoke');
      if (!code) return;
      try {
        await api(`/api/v1/invites/${encodeURIComponent(code)}/revoke`, { method: 'POST' });
        load();
      } catch (e) { toast(e.message, 'error'); }
    });
    load();
  }

  // ---------- Join a company with an invite code (sign-up) ----------

  function openJoin() {
    const el = modal('nx-join-modal', 'Join your company', `
      <form data-join>
        <div class="form-group"><label>Company invite code</label>
          <input name="invite_code" placeholder="XXXX-XXXX" autocomplete="off" style="text-transform:uppercase;letter-spacing:2px;" required></div>
        <p data-company style="margin:-4px 0 10px;color:#22c55e;"></p>
        <div class="form-group"><label>Your name</label><input name="full_name" required></div>
        <div class="form-group"><label>Phone (optional)</label><input name="phone" type="tel"></div>
        <div class="form-group"><label>Choose a username</label><input name="username" autocomplete="username" autocapitalize="none" required></div>
        <div class="form-group"><label>Email</label><input name="email" type="email" required></div>
        <div class="form-group"><label>Password (8+ characters)</label><input name="password" type="password" minlength="8" autocomplete="new-password" required></div>
        <div class="form-group"><label>Secret question</label><select name="secret_question">
          ${SECRET_QUESTIONS.map((q) => `<option>${esc(q)}</option>`).join('')}</select></div>
        <div class="form-group"><label>Answer</label><input name="secret_answer" required></div>
        <button type="submit" class="btn btn-primary">Create my login</button>
        <p class="error" data-err style="margin-top:8px;"></p>
      </form>`);
    const form = el.querySelector('[data-join]');
    const codeInput = form.querySelector('[name=invite_code]');
    const companyEl = form.querySelector('[data-company]');

    codeInput.addEventListener('blur', async () => {
      const code = codeInput.value.trim();
      companyEl.textContent = '';
      if (!code) return;
      try {
        const res = await fetch(`/api/v1/invites/check?code=${encodeURIComponent(code)}`);
        const d = (await res.json()).data || {};
        if (d.valid) {
          companyEl.style.color = '#22c55e';
          companyEl.textContent = `You will join ${d.company_name || 'this company'} as ${ROLE_LABELS[d.role] || d.role}.`;
        } else {
          companyEl.style.color = '#ef4444';
          companyEl.textContent = 'This code is not valid (wrong, expired or already used).';
        }
      } catch (e) { /* checked again on submit */ }
    });

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const errEl = form.querySelector('[data-err]');
      errEl.textContent = '';
      const f = new FormData(form);
      const payload = Object.fromEntries(f.entries());
      try {
        const res = await fetch('/api/v1/signup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) throw new Error(data.error?.message || 'Sign-up failed');
        el.remove();
        const u = document.getElementById('username');
        const p = document.getElementById('password');
        if (u && p && typeof login === 'function') {
          u.value = payload.username;
          p.value = payload.password;
          await login();
        }
        toast(`Welcome to ${data.data.company_name || 'NEXORA'} — 7-day free trial started`, 'success');
      } catch (e) {
        errEl.textContent = e.message;
      }
    });
  }

  window.nxSubscription = { refresh, showLock, openPlan, openCodeDesk, openInvites, openJoin };
})();
