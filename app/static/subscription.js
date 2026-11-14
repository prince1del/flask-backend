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
    const endingSoon = st && st.days_left != null && st.days_left <= 3;
    const show = st && (st.status === 'trial' || (st.status === 'active' && endingSoon))
      && sessionStorage.getItem('nxPlanBannerHidden') !== '1';
    if (!show) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'nx-plan-banner';
      bar.style.cssText = 'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:10040;'
        + 'background:#7c2d12;color:#fff;padding:10px 16px;border-radius:12px;box-shadow:0 6px 20px rgba(0,0,0,.3);'
        + 'display:flex;gap:12px;align-items:center;font-size:0.9rem;';
      document.body.appendChild(bar);
    }
    bar.style.background = endingSoon ? '#7c2d12' : '#1e3a5f';
    const what = st.status === 'trial' ? 'Free trial' : 'Your plan';
    const when = st.days_left <= 0 ? 'ends today' : `ends in ${st.days_left} day${st.days_left === 1 ? '' : 's'}`;
    bar.innerHTML = `<span>${what} ${when}.</span>
      <button type="button" class="btn btn-primary" data-code style="padding:4px 12px;">Enter code</button>
      <button type="button" data-later style="background:none;border:0;color:#cbd5e1;cursor:pointer;">Later</button>`;
    bar.querySelector('[data-code]').addEventListener('click', () => openPlan());
    bar.querySelector('[data-later]').addEventListener('click', () => {
      sessionStorage.setItem('nxPlanBannerHidden', '1');
      bar.remove();
    });
  }

  async function refresh() {
    try {
      const st = await api('/api/v1/subscription/status');
      lastStatus = st;
      const show = (id, on) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', !on);
      };
      show('nx-menu-sub-codes', st.is_super_admin);
      show('nx-menu-invites', st.can_invite);
      show('nx-menu-plan', st.is_super_admin || st.can_invite || st.status !== 'unlimited');
      renderBanner(st);
      if (st.status === 'expired') showLock();
      return st;
    } catch (e) {
      return null;
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
      <h3 style="margin-top:18px;">Team requests</h3><div data-teams>Loading…</div>
      <h3 style="margin-top:18px;">Logins</h3><div data-users>Loading…</div>
      <h3 style="margin-top:18px;">Recent codes</h3><div data-codes>Loading…</div>`);
    const body = el.querySelector('[data-body]');

    async function load() {
      try {
        const [u, c, t] = await Promise.all([
          api('/api/v1/subscription/users'),
          api('/api/v1/subscription/codes'),
          api('/api/v1/subscription/team-requests'),
        ]);
        const teams = t.requests || [];
        body.querySelector('[data-teams]').innerHTML = teams.length ? `<table style="width:100%;font-size:0.85rem;">
          <tr><th align="left">Company</th><th align="left">Asked</th><th align="left">Logins now</th><th align="left">Status</th><th></th></tr>
          ${teams.map((x) => `<tr><td>${esc(x.company_name || x.workspace_id)}<br><small>${esc(x.username || '')}${x.note ? ` · ${esc(x.note)}` : ''}</small></td>
            <td>+${x.members}</td><td>${x.member_count}${x.team_limit != null ? ` / ${x.team_limit}` : ' / no limit'}</td><td>${esc(x.status)}</td>
            <td>${x.status === 'pending' ? `<button type="button" class="btn btn-primary" data-allow="${x.company_id}" data-members="${x.member_count + x.members}" style="padding:2px 8px;">Allow</button>
              <button type="button" class="btn btn-secondary" data-decline="${x.id}" style="padding:2px 8px;">Decline</button>` : ''}</td></tr>`).join('')}
          </table>` : '<p>No team requests.</p>';
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
      const t = ev.target;
      const code = t?.getAttribute?.('data-revoke');
      const allow = t?.getAttribute?.('data-allow');
      const decline = t?.getAttribute?.('data-decline');
      try {
        if (code) {
          await api(`/api/v1/subscription/codes/${encodeURIComponent(code)}/revoke`, { method: 'POST' });
        } else if (allow) {
          const total = window.prompt('Total logins allowed for this company (owner included, 0 = no limit):', t.getAttribute('data-members'));
          if (total == null || total.trim() === '') return;
          await api(`/api/v1/subscription/companies/${allow}/team`, { method: 'POST', body: { max_members: Number(total) } });
          toast('Team places allowed', 'success');
        } else if (decline) {
          await api(`/api/v1/subscription/team-requests/${decline}/decline`, { method: 'POST' });
        } else {
          return;
        }
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
        if (!inv.can_create) {
          const req = inv.team_request;
          body.innerHTML = `
            <p style="margin-top:0;">Your plan includes <strong>${inv.team_limit} login${inv.team_limit === 1 ? '' : 's'}</strong>
              (${inv.member_count} in use). To add your salesmen, distributors or retailers, ask NEXORA for team access.</p>
            ${req ? `<div style="padding:12px;border-radius:10px;background:rgba(59,130,246,.12);margin-bottom:10px;">
              Request sent for <strong>${req.members}</strong> people on ${esc(fmtDate(req.created_at))}. NEXORA will contact you.</div>` : ''}
            <form data-request style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
              <div class="form-group"><label>People to add</label><input name="members" type="number" min="1" max="500" value="${req ? req.members : 1}"></div>
              <div class="form-group"><label>Note (optional)</label><input name="note" placeholder="e.g. 3 salesmen in Delhi" value="${esc(req?.note || '')}"></div>
              <button type="submit" class="btn btn-primary" style="grid-column:1/-1;">${req ? 'Update request' : 'Request team access'}</button>
            </form>`;
          body.querySelector('[data-request]').addEventListener('submit', async (ev) => {
            ev.preventDefault();
            const f = new FormData(ev.target);
            try {
              await api('/api/v1/invites/team-request', {
                method: 'POST',
                body: { members: Number(f.get('members') || 1), note: String(f.get('note') || '').trim() || null },
              });
              toast('Request sent to NEXORA', 'success');
              load();
            } catch (e) { toast(e.message, 'error'); }
          });
          return;
        }
        const placesLeft = inv.seats_left == null ? '' : ` You can add <strong>${inv.seats_left}</strong> more.`;
        body.innerHTML = `
          <p style="color:#94a3b8;margin-top:0;">Share a code with a teammate. They choose “Join with company code” on the login screen and join <strong>${esc(inv.company_name || 'your company')}</strong>. Each new member gets their own 7-day free trial.${placesLeft}</p>
          <form data-make style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div class="form-group"><label>Role</label><select name="role">
              ${inv.roles.map((r) => `<option value="${esc(r)}">${esc(ROLE_LABELS[r] || r)}</option>`).join('')}</select></div>
            <div class="form-group"><label>How many people</label><input name="max_uses" type="number" min="1" max="100" value="1"></div>
            <div class="form-group"><label>Code expires in (days)</label><input name="expires_in_days" type="number" min="1" max="30" value="7"></div>
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
