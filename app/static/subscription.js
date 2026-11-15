/* Plan (7-day trial + activation codes + online payment), super-admin code desk
 * and payments/prices, company invites, and "join a company with an invite code"
 * sign-up. Talks to /api/v1/subscription/*, /api/v1/billing/*, /api/v1/invites/*
 * and /api/v1/signup. Payment happens on the hosted page /pay/<token>. */
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

  const REMIND_DAYS = 7;
  const REMIND_KEY = 'nxPlanReminder';

  // 2–3 random times a day (10:00–20:00), not on every page load.
  function reminderDue() {
    const now = new Date();
    const today = now.toDateString();
    let s = null;
    try { s = JSON.parse(localStorage.getItem(REMIND_KEY) || 'null'); } catch (e) { s = null; }
    if (!s || s.date !== today) {
      const count = 2 + Math.floor(Math.random() * 2);
      const slots = [];
      while (slots.length < count) {
        const m = 600 + Math.floor(Math.random() * 600);
        if (slots.every((x) => Math.abs(x - m) >= 90)) slots.push(m);
      }
      s = { date: today, slots: slots.sort((a, b) => a - b), shown: 0 };
    }
    const mins = now.getHours() * 60 + now.getMinutes();
    const passed = s.slots.filter((m) => m <= mins).length;
    const due = passed > s.shown;
    if (due) s.shown = passed;
    localStorage.setItem(REMIND_KEY, JSON.stringify(s));
    return due;
  }

  function renderBanner(st) {
    let bar = document.getElementById('nx-plan-banner');
    const ending = st && (st.status === 'trial' || st.status === 'active')
      && st.days_left != null && st.days_left <= REMIND_DAYS;
    if (!ending) { if (bar) bar.remove(); return; }
    if (!bar && !reminderDue()) return;
    const endingSoon = st.days_left <= 3;
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
    bar.querySelector('[data-code]').addEventListener('click', () => { bar.remove(); openPlan(); });
    bar.querySelector('[data-later]').addEventListener('click', () => bar.remove());
  }

  setInterval(() => { if (lastStatus) renderBanner(lastStatus); }, 10 * 60 * 1000);

  // ---------- Sign-in approval (team members) ----------

  let approvalState = null;   // pending / approved / denied / ended / not_needed
  let approvalTimer = null;
  let requestTimer = null;

  function signInNeedsOwner() { return approvalState === 'approved' || approvalState === 'pending'; }

  function fullNotice(id, title, text, busy, buttonLabel, onButton) {
    let el = document.getElementById(id);
    if (el) el.remove();
    el = document.createElement('div');
    el.id = id;
    el.style.cssText = 'position:fixed;inset:0;z-index:10060;background:rgba(15,23,42,.96);color:#e2e8f0;'
      + 'display:flex;align-items:center;justify-content:center;padding:24px;';
    el.innerHTML = `<div style="max-width:440px;">
        ${busy ? '<div class="nx-spin" style="width:34px;height:34px;border:3px solid rgba(148,163,184,.35);border-top-color:#60a5fa;border-radius:50%;animation:nxspin 1s linear infinite;margin-bottom:18px;"></div><style>@keyframes nxspin{to{transform:rotate(360deg)}}</style>' : ''}
        <h2 style="margin:0 0 8px;">${esc(title)}</h2>
        <p style="color:#94a3b8;line-height:1.5;">${esc(text)}</p>
        <button type="button" class="btn ${busy ? 'btn-secondary' : 'btn-primary'}" data-go style="margin-top:12px;">${esc(buttonLabel)}</button>
      </div>`;
    el.querySelector('[data-go]').addEventListener('click', onButton);
    document.body.appendChild(el);
    return el;
  }

  function forceSignOut() {
    approvalState = null;
    document.getElementById('nx-approval-wait')?.remove();
    document.getElementById('nx-approval-ended')?.remove();
    if (typeof logout === 'function') logout('forced');
  }

  function setApproval(state) {
    if (state === approvalState) return;
    approvalState = state;
    clearInterval(approvalTimer);
    document.getElementById('nx-approval-wait')?.remove();
    document.getElementById('nx-approval-ended')?.remove();
    if (state === 'pending') {
      fullNotice('nx-approval-wait', 'Waiting for approval',
        'Your company owner has been asked to allow this sign-in. NEXORA opens by itself as soon as they approve.',
        true, 'Log out', forceSignOut);
      approvalTimer = setInterval(async () => {
        try {
          const d = await api('/api/v1/auth/approval');
          if (d.approval !== 'pending') {
            clearInterval(approvalTimer);
            if (d.approval === 'approved' || d.approval === 'not_needed') window.location.reload();
            else setApproval(d.approval);
          }
        } catch (e) { /* keep waiting */ }
      }, 5000);
    } else if (state === 'denied' || state === 'ended') {
      fullNotice('nx-approval-ended', state === 'denied' ? 'Sign-in not allowed' : 'You were signed out',
        state === 'denied' ? 'Your company owner did not allow this sign-in. Contact them if this was a mistake.'
          : 'This sign-in was ended. Sign in again; your company owner will need to approve it.',
        false, 'Go to sign in', forceSignOut);
    }
  }

  // Any API answer can say the sign-in is waiting or was ended.
  if (!window.__nxApprovalFetchHook) {
    window.__nxApprovalFetchHook = true;
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const res = await nativeFetch(...args);
      if (res.status === 403) {
        res.clone().json().then((d) => {
          const c = d && d.error && d.error.code;
          if (c === 'LOGIN_PENDING') setApproval('pending');
          else if (c === 'LOGIN_DENIED') setApproval('denied');
          else if (c === 'SESSION_REVOKED') setApproval('ended');
        }).catch(() => {});
      }
      return res;
    };
  }

  /** Owner / admin: a teammate's sign-in waits here (checked every 20 seconds). */
  function watchLoginRequests() {
    clearInterval(requestTimer);
    const shown = new Set();
    const check = async () => {
      if (document.getElementById('nx-login-request')) return;
      let reqs;
      try { reqs = (await api('/api/v1/invites/login-requests')).requests || []; } catch (e) { clearInterval(requestTimer); return; }
      const r = reqs.find((x) => !shown.has(x.id));
      if (!r) return;
      shown.add(r.id);
      const when = r.requested_at ? new Date(r.requested_at).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' }) : '';
      const el = modal('nx-login-request', 'Sign-in request', `
        <p style="margin-top:0;font-size:1.1rem;"><strong>${esc(r.full_name || r.username)}</strong>
          <small style="color:#94a3b8;">${esc(r.username)} · ${esc(ROLE_LABELS[r.role] || r.role || '')}</small></p>
        <p>Device: <strong>${esc(r.device_label || 'Unknown device')}</strong></p>
        <p style="color:#94a3b8;">${esc([when && `Asked ${when}`, r.phone, r.email].filter(Boolean).join(' · '))}</p>
        <p style="color:#94a3b8;font-size:0.9rem;">Allow only if this is your team member on their own device. Their earlier device is signed out.</p>
        ${reqs.length > 1 ? `<p style="color:#60a5fa;">${reqs.length - 1} more waiting</p>` : ''}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
          <button type="button" class="btn btn-secondary" data-deny>Don't allow</button>
          <button type="button" class="btn btn-primary" data-allow>Allow</button>
        </div>`);
      const decide = async (approve) => {
        try {
          await api(`/api/v1/invites/login-requests/${r.id}`, { method: 'POST', body: { approve } });
          toast(approve ? `${r.full_name || r.username} can sign in now` : 'Sign-in not allowed', 'success');
        } catch (e) { toast(e.message, 'error'); }
        el.remove();
        check();
      };
      el.querySelector('[data-allow]').addEventListener('click', () => decide(true));
      el.querySelector('[data-deny]').addEventListener('click', () => decide(false));
    };
    check();
    requestTimer = setInterval(check, 20000);
  }

  async function refresh() {
    try {
      api('/api/v1/auth/approval').then((d) => setApproval(d.approval)).catch(() => {});
      const st = await api('/api/v1/subscription/status');
      lastStatus = st;
      const show = (id, on) => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', !on);
      };
      show('nx-menu-sub-codes', st.is_super_admin);
      show('nx-menu-invites', st.can_invite);
      show('nx-menu-team', st.can_invite);
      if (st.can_invite && !requestTimer) watchLoginRequests();
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

  // ---------- Online payment (Razorpay) ----------

  function rupees(v) {
    return `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
  }

  function productLabel(p) {
    if (p.kind === 'plan') return `${p.days} days`;
    return `+${p.seats} team member${p.seats === 1 ? '' : 's'}`;
  }

  /** Opens the pay page and waits until it is paid (or 10 minutes pass).
   *  autoRenew = Razorpay subscription (UPI Autopay / card mandate). */
  async function buy(productCode, quantity, onPaid, autoRenew) {
    const order = autoRenew
      ? await api('/api/v1/billing/subscriptions', { method: 'POST', body: { product_code: productCode } })
      : await api('/api/v1/billing/orders', { method: 'POST', body: { product_code: productCode, quantity } });
    const win = window.open(order.pay_path, 'nexora-pay', 'width=480,height=720');
    if (!win) window.location.href = order.pay_path;
    toast('Complete the payment in the new window…', 'info');
    const started = Date.now();
    const timer = setInterval(async () => {
      try {
        let paid = false;
        if (autoRenew) {
          const list = await api('/api/v1/billing/subscriptions');
          paid = (list.subscriptions || []).some((x) => x.token === order.token && x.charges > 0);
        } else {
          const o = await api(`/api/v1/billing/orders/${encodeURIComponent(order.token)}`);
          paid = o.status === 'paid';
        }
        if (paid) {
          clearInterval(timer);
          toast('Payment received — thank you!', 'success');
          onPaid && onPaid();
        } else if (Date.now() - started > 10 * 60 * 1000) {
          clearInterval(timer);
        }
      } catch (e) { /* keep waiting */ }
    }, 3000);
  }

  /** "Buy online" block for one kind of product, or null when payments are off. */
  async function buyBlock(kind, onPaid) {
    let data;
    try { data = await api('/api/v1/billing/products'); } catch (e) { return null; }
    const items = (data.products || []).filter((p) => p.kind === kind);
    if (!data.enabled || !items.length) return null;
    let running = [];
    if (kind === 'plan') {
      try {
        running = ((await api('/api/v1/billing/subscriptions')).subscriptions || [])
          .filter((x) => ['active', 'authenticated', 'pending', 'halted'].includes(x.status));
      } catch (e) { running = []; }
    }
    const box = document.createElement('div');
    box.style.cssText = 'margin:12px 0;padding:12px;border-radius:12px;border:1px solid rgba(148,163,184,.3);';
    box.innerHTML = `<div style="font-weight:600;margin-bottom:8px;">${kind === 'plan' ? 'Buy online' : 'Add team members online'}
        ${data.mode === 'test' ? '<small style="color:#f59e0b;">(test mode)</small>' : ''}</div>
      ${running.map((x) => `<div style="padding:8px 10px;margin-bottom:8px;border-radius:10px;background:rgba(34,197,94,.12);">
        <strong>Auto-renew ${x.cancel_at_end ? 'stops after this period' : 'on'}</strong> · ${esc(x.product_name)} · ${rupees(x.amount)} / ${esc(x.period)}
        ${x.status === 'halted' ? '<br><small style="color:#f87171;">Last charge failed — update your payment method in the Razorpay mail.</small>' : ''}
        ${x.cancel_at_end ? '' : `<button type="button" class="btn btn-secondary" data-stop="${esc(x.token)}" style="padding:2px 10px;margin-left:8px;">Stop auto-renew</button>`}
      </div>`).join('')}
      ${items.map((p) => `<div style="display:flex;justify-content:space-between;align-items:center;gap:10px;padding:6px 0;flex-wrap:wrap;">
        <span>${esc(p.name)} <small style="color:#94a3b8;">· ${esc(productLabel(p))}</small></span>
        <span style="display:flex;gap:6px;">
          <button type="button" class="btn btn-primary" data-buy="${esc(p.code)}" style="padding:4px 14px;">Pay ${rupees(p.price)}</button>
          ${p.renewable && !running.some((x) => !x.cancel_at_end) ? `<button type="button" class="btn btn-secondary" data-renew="${esc(p.code)}" style="padding:4px 14px;">Auto-renew ${rupees(p.price)}/${esc(p.period_label || '')}</button>` : ''}
        </span>
      </div>`).join('')}
      <p class="error" data-err style="margin:6px 0 0;"></p>`;
    box.addEventListener('click', async (ev) => {
      const code = ev.target?.getAttribute?.('data-buy');
      const renew = ev.target?.getAttribute?.('data-renew');
      const stop = ev.target?.getAttribute?.('data-stop');
      if (!code && !renew && !stop) return;
      const err = box.querySelector('[data-err]');
      err.textContent = '';
      try {
        if (stop) {
          if (!confirm('Stop auto-renew? The time already paid stays.')) return;
          await api(`/api/v1/billing/subscriptions/${encodeURIComponent(stop)}/cancel`, { method: 'POST' });
          toast('Auto-renew will stop after the current period', 'success');
          onPaid && onPaid();
        } else {
          await buy(code || renew, 1, onPaid, !!renew);
        }
      } catch (e) { err.textContent = e.message; }
    });
    return box;
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
        <p style="color:#94a3b8;">Your free trial or plan is over. Buy a plan online or enter an activation code from NEXORA. Your data is safe.</p>
        <div data-buy></div>
        <div data-form></div>
        <button type="button" class="btn btn-secondary" data-logout style="margin-top:6px;">Logout</button>
      </div>`;
    el.querySelector('[data-form]').appendChild(redeemForm(() => {
      el.remove();
      window.location.reload();
    }));
    buyBlock('plan', () => { el.remove(); window.location.reload(); })
      .then((b) => { if (b) el.querySelector('[data-buy]').appendChild(b); });
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
      active: st.days_left > REMIND_DAYS
        ? `Active ✓ — valid till ${fmtDate(st.ends_at)}`
        : `Active — valid till ${fmtDate(st.ends_at)} (${st.days_left} days left)`,
      expired: `Expired on ${fmtDate(st.ends_at)}`,
    }[st.status] || 'Unknown';
    const el = modal('nx-plan-modal', 'My plan', `<p><strong>${esc(label)}</strong></p>
      ${st.via_company ? '<p style="color:#94a3b8;margin-top:-6px;">Your company\'s plan</p>' : ''}`);
    if (st.status !== 'unlimited') {
      const body = el.querySelector('[data-body]');
      const b = await buyBlock('plan', () => { el.remove(); refresh(); openPlan(); });
      if (b) body.appendChild(b);
      body.appendChild(redeemForm(() => { el.remove(); refresh(); }));
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
      <button type="button" class="btn btn-secondary" data-billing style="margin-top:12px;">Online payments & prices</button>
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
      if (t?.hasAttribute?.('data-billing')) { openBilling(); return; }
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

  // ---------- Super admin: online payments (Razorpay keys, prices, sales) ----------

  async function openBilling() {
    const el = modal('nx-billing-desk', 'Online payments & prices', '<p>Loading…</p>');
    const body = el.querySelector('[data-body]');

    async function load() {
      try {
        const [cfg, pr, sales] = await Promise.all([
          api('/api/v1/billing/config'), api('/api/v1/billing/admin/products'), api('/api/v1/billing/admin/sales'),
        ]);
        body.innerHTML = `
          <h3 style="margin-top:0;">Razorpay keys</h3>
          <p style="color:#94a3b8;margin-top:0;">Razorpay Dashboard → Account & Settings → API Keys. Start with Test Mode keys.
            Status: <strong>${cfg.configured ? `connected (${esc(cfg.mode)} mode)` : 'not connected'}</strong></p>
          <form data-keys style="display:grid;gap:8px;">
            <input name="key_id" placeholder="Key ID (rzp_test_… or rzp_live_…)" value="${esc(cfg.key_id || '')}" autocomplete="off">
            <input name="key_secret" type="password" placeholder="${cfg.key_secret ? `Key secret saved ${esc(cfg.key_secret)} — type to replace` : 'Key secret'}" autocomplete="new-password">
            <input name="webhook_secret" type="password" placeholder="${cfg.webhook_secret ? `Webhook secret saved ${esc(cfg.webhook_secret)} — type to replace` : 'Webhook secret'}" autocomplete="new-password">
            <small style="color:#94a3b8;">Webhook URL for Razorpay: <code>${esc(location.origin)}/api/v1/billing/webhook</code> · events: order.paid, payment.captured</small>
            <button type="submit" class="btn btn-primary">Save keys</button>
          </form>
          <h3 style="margin-top:18px;">Plans & prices</h3>
          <table style="width:100%;font-size:0.85rem;">
            <tr><th align="left">Name</th><th align="left">Gives</th><th align="left">Price</th><th align="left">On sale</th></tr>
            ${pr.products.map((p) => `<tr><td>${esc(p.name)}<br><small style="font-family:monospace;">${esc(p.code)}</small></td>
              <td>${esc(productLabel(p))}${p.renewable ? ' · auto-renew' : ''}</td><td>${rupees(p.price)}</td>
              <td><button type="button" class="btn btn-secondary" data-toggle='${esc(JSON.stringify(p))}' style="padding:2px 8px;">${p.active ? 'Yes — stop' : 'No — start'}</button></td></tr>`).join('') || '<tr><td colspan="4">No plans yet.</td></tr>'}
          </table>
          <form data-product style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px;">
            <input name="name" placeholder="Name, e.g. Monthly plan" required>
            <input name="code" placeholder="Code, e.g. plan_monthly" required>
            <select name="kind"><option value="plan">Plan (days of access)</option><option value="seats">Team members (extra logins)</option></select>
            <input name="amount" type="number" min="1" placeholder="Days or members" required>
            <input name="price" type="number" min="1" step="0.01" placeholder="Price ₹ (incl. GST)" required>
            <label style="display:flex;align-items:center;gap:6px;"><input name="renewable" type="checkbox"> Auto-renew allowed (plans only)</label>
            <button type="submit" class="btn btn-primary">Add / update</button>
          </form>
          <h3 style="margin-top:18px;">Sales</h3>
          <p style="margin-top:0;">This month: <strong>${rupees(sales.month_revenue)}</strong> (${sales.month_orders} orders) · All time: <strong>${rupees(sales.revenue)}</strong> (${sales.paid_orders})</p>
          ${sales.orders.length ? `<table style="width:100%;font-size:0.85rem;">
            <tr><th align="left">When</th><th align="left">Login</th><th align="left">Bought</th><th align="left">Amount</th><th align="left">Status</th></tr>
            ${sales.orders.map((o) => `<tr><td>${esc(fmtDate(o.paid_at || o.created_at))}</td><td>${esc(o.username || '')}</td>
              <td>${esc(o.product_name)}${o.quantity > 1 ? ` × ${o.quantity}` : ''}</td><td>${rupees(o.amount)}</td><td>${esc(o.status)}</td></tr>`).join('')}
            </table>` : '<p>No orders yet.</p>'}
          <h3 style="margin-top:18px;">Auto-renewals</h3>
          ${(sales.subscriptions || []).length ? `<table style="width:100%;font-size:0.85rem;">
            <tr><th align="left">Login</th><th align="left">Plan</th><th align="left">Charges</th><th align="left">Status</th></tr>
            ${sales.subscriptions.map((x) => `<tr><td>${esc(x.username || '')}</td><td>${esc(x.product_name)} · ${rupees(x.amount)}/${esc(x.period)}</td>
              <td>${x.charges}</td><td>${esc(x.status)}${x.cancel_at_end ? ' (stopping)' : ''}</td></tr>`).join('')}
            </table>` : '<p>No auto-renewals yet.</p>'}`;
      } catch (e) {
        body.innerHTML = `<p class="error">${esc(e.message)}</p>`;
      }
    }

    body.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        if (ev.target.hasAttribute('data-keys')) {
          await api('/api/v1/billing/config', { method: 'POST', body: Object.fromEntries(f.entries()) });
          toast('Razorpay keys saved', 'success');
        } else if (ev.target.hasAttribute('data-product')) {
          const kind = f.get('kind');
          await api('/api/v1/billing/admin/products', {
            method: 'POST',
            body: {
              name: f.get('name'), code: f.get('code'), kind, price: Number(f.get('price')),
              days: kind === 'plan' ? Number(f.get('amount')) : 0,
              seats: kind === 'seats' ? Number(f.get('amount')) : 0,
              renewable: kind === 'plan' && f.get('renewable') === 'on',
            },
          });
          toast('Saved', 'success');
        }
        load();
      } catch (e) { toast(e.message, 'error'); }
    });
    body.addEventListener('click', async (ev) => {
      const raw = ev.target?.getAttribute?.('data-toggle');
      if (!raw) return;
      const p = JSON.parse(raw);
      try {
        await api('/api/v1/billing/admin/products', { method: 'POST', body: { ...p, active: !p.active } });
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
        const [inv, team] = await Promise.all([api('/api/v1/invites'), api('/api/v1/invites/team').catch(() => null)]);
        const catalog = team?.features || [];
        const mem = { members: team?.members || [] };
        if (!inv.can_manage) { body.innerHTML = '<p>Only your company owner or admin can invite people.</p>'; return; }
        if (!inv.plan_active) { body.innerHTML = '<p>Activate your account with a NEXORA code to add your team.</p>'; return; }
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
            </form>
            <h3 style="margin-top:18px;">Team</h3>
            ${teamHtml(mem.members, catalog)}`;
          buyBlock('seats', () => load()).then((b) => { if (b) body.insertBefore(b, body.querySelector('[data-request]')); });
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
          <p style="color:#94a3b8;margin-top:0;">Share a code with a teammate. They choose “Join with company code” on the login screen and join <strong>${esc(inv.company_name || 'your company')}</strong>. New members use your company's plan.${placesLeft}</p>
          <form data-make style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
            <div class="form-group"><label>Role</label><select name="role">
              ${inv.roles.map((r) => `<option value="${esc(r)}">${esc(ROLE_LABELS[r] || r)}</option>`).join('')}</select></div>
            <div class="form-group"><label>How many people</label><input name="max_uses" type="number" min="1" max="100" value="1"></div>
            <div class="form-group"><label>Code expires in (days)</label><input name="expires_in_days" type="number" min="1" max="30" value="7"></div>
            <div class="form-group"><label>Note (optional)</label><input name="note" placeholder="e.g. Rahul — Delhi"></div>
            <div style="grid-column:1/-1;">
              <label style="font-weight:600;">What can this person open?</label>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;margin-top:6px;">
                ${catalog.map((f) => `<label style="display:flex;gap:6px;align-items:center;font-size:0.9rem;">
                  <input type="checkbox" name="feature" value="${esc(f.key)}" checked> ${esc(f.label)}</label>`).join('')}
              </div>
            </div>
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
          <h3 style="margin-top:18px;">Team</h3>
          ${teamHtml(mem.members, catalog)}`;
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
                features: f.getAll('feature'),
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
      const block = ev.target?.getAttribute?.('data-block');
      const save = ev.target?.getAttribute?.('data-save-access');
      const signout = ev.target?.getAttribute?.('data-signout');
      try {
        if (signout) {
          if (!confirm('Sign out this member now? Their next sign-in will need your approval.')) return;
          await api(`/api/v1/invites/members/${signout}/signout`, { method: 'POST' });
          toast('Signed out', 'success');
        } else if (code) {
          await api(`/api/v1/invites/${encodeURIComponent(code)}/revoke`, { method: 'POST' });
        } else if (block) {
          const turnOff = ev.target.getAttribute('data-blocked') !== '1';
          if (turnOff && !confirm('Switch off this login? They will be signed out at once.')) return;
          await api(`/api/v1/invites/members/${block}/access`, { method: 'POST', body: { blocked: turnOff } });
          toast(turnOff ? 'Login switched off' : 'Login switched on', 'success');
        } else if (save) {
          const card = body.querySelector(`[data-member="${save}"]`);
          const features = [...card.querySelectorAll('input[name=mfeature]:checked')].map((x) => x.value);
          await api(`/api/v1/invites/members/${save}/access`, { method: 'POST', body: { features } });
          toast('Access saved', 'success');
        } else {
          return;
        }
        load();
      } catch (e) { toast(e.message, 'error'); }
    });
    load();
  }

  /** One card per teammate: features (tick to open) and switch the login off/on. */
  function teamHtml(members, catalog) {
    if (!members.length) return '<p>No members yet.</p>';
    return members.map((m) => {
      const fixed = m.is_owner || m.is_me;
      const feats = m.features; // null = everything
      return `<div data-member="${m.id}" style="padding:10px 12px;margin:8px 0;border-radius:12px;border:1px solid rgba(148,163,184,.3);${m.blocked ? 'opacity:.75;' : ''}">
        <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;">
          <div><strong>${esc(m.full_name || m.username)}</strong>
            <small style="color:#94a3b8;">${esc(m.username)} · ${esc(ROLE_LABELS[m.role] || m.role || '')}${m.is_owner ? ' · Owner' : ''}${m.is_me ? ' · You' : ''}</small>
            ${m.blocked ? '<br><small style="color:#f87171;">Login switched off</small>' : ''}
            ${!m.is_owner && m.device && (m.device.status === 'approved' || m.device.status === 'pending')
              ? `<br><small style="color:#94a3b8;">${m.device.status === 'pending' ? 'Waiting to sign in on' : 'Signed in on'}: ${esc(m.device.device || 'Unknown device')}${m.device.since ? ` · since ${esc(fmtDate(m.device.since))}` : ''}</small>`
              : ''}</div>
          ${fixed ? '' : `<button type="button" class="btn ${m.blocked ? 'btn-primary' : 'btn-secondary'}" data-block="${m.id}" data-blocked="${m.blocked ? 1 : 0}" style="padding:2px 10px;">${m.blocked ? 'Switch on' : 'Switch off'}</button>`}
        </div>
        ${fixed ? '<small style="color:#94a3b8;">Every feature</small>' : `
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;margin-top:8px;">
            ${catalog.map((f) => `<label style="display:flex;gap:6px;align-items:center;font-size:0.85rem;">
              <input type="checkbox" name="mfeature" value="${esc(f.key)}" ${!feats || feats.includes(f.key) ? 'checked' : ''}> ${esc(f.label)}</label>`).join('')}
          </div>
          <button type="button" class="btn btn-secondary" data-save-access="${m.id}" style="padding:2px 10px;margin-top:6px;">Save access</button>
          ${m.device && m.device.status === 'approved' ? `<button type="button" class="btn btn-secondary" data-signout="${m.id}" style="padding:2px 10px;margin-top:6px;">Sign out this device</button>` : ''}`}
      </div>`;
    }).join('');
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
        <p style="font-size:0.85rem;color:#94a3b8;line-height:1.45;padding:10px 12px;border-radius:10px;background:rgba(148,163,184,.1);">
          Your company owner can see the work you do in NEXORA, and your location only while you are on duty
          (between Start day and End day). Your first sign-in waits for the owner's approval.</p>
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
      if (typeof webDeviceLabel === 'function') payload.device_label = webDeviceLabel();
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
        toast(`Welcome to ${data.data.company_name || 'NEXORA'}`, 'success');
      } catch (e) {
        errEl.textContent = e.message;
      }
    });
  }

  window.nxSubscription = { refresh, showLock, openPlan, openCodeDesk, openInvites, openJoin, openBilling, signInNeedsOwner };
})();
