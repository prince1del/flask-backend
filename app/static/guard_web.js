/* NEXORA Guard on the web: a shield button with the number of open problems, an inbox
 * (Open / Fixed / Dismissed), "✨ Explain & fix" (AI), and today's AI note.
 * Server: /api/v1/guard/issues, /issues/<id>/explain|dismiss|reopen, /digest. */
(function () {
  'use strict';

  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const COLORS = { alert: '#dc2626', warn: '#d97706', info: '#2563eb' };
  const LABEL = { alert: 'Urgent', warn: 'Check', info: 'Tip' };

  async function api(url, options = {}) {
    const res = typeof fetchWithAuth === 'function' ? await fetchWithAuth(url, options) : await fetch(url, options);
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.success === false) throw new Error(data.error?.message || `Request failed (${res.status})`);
    return data.data;
  }

  function badge(count, alerts) {
    let b = document.getElementById('nx-guard-btn');
    if (!b) {
      b = document.createElement('button');
      b.id = 'nx-guard-btn';
      b.type = 'button';
      b.title = 'NEXORA Guard — problems found in your data';
      b.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:10030;border:0;border-radius:999px;padding:9px 14px;'
        + 'font-weight:700;cursor:pointer;box-shadow:0 6px 18px rgba(0,0,0,.25);color:#fff;font-size:.9rem;';
      b.addEventListener('click', () => open());
      document.body.appendChild(b);
    }
    b.style.background = alerts ? '#dc2626' : (count ? '#d97706' : '#16a34a');
    b.textContent = count ? `🛡 Guard · ${count}` : '🛡 Guard';
    b.classList.toggle('hidden', !document.getElementById('dashboard') || document.getElementById('dashboard').classList.contains('hidden'));
  }

  async function check() {
    try {
      const d = await api('/api/v1/guard/issues?status=open');
      badge(d.issues.length, d.alerts);
      return d;
    } catch (e) { return null; }
  }

  function goTo(link) {
    const [where, view] = String(link || '').split(':');
    document.getElementById('nx-guard-modal')?.remove();
    if (where === 'hop' && typeof openHopView === 'function') openHopView(view === 'reports' ? 'reports' : view);
    else if (where === 'bd' && typeof showOfSection === 'function') showOfSection(view);
  }

  async function open(tab) {
    tab = tab || 'open';
    document.getElementById('nx-guard-modal')?.remove();
    const el = document.createElement('div');
    el.id = 'nx-guard-modal';
    el.className = 'modal';
    el.style.zIndex = '10050';
    el.innerHTML = `
      <div class="modal-content" style="max-width:760px;width:94vw;max-height:88vh;overflow-y:auto;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
          <h2 style="margin:0;">🛡 NEXORA Guard</h2>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            ${['open', 'resolved', 'dismissed'].map((t) => `<button type="button" class="btn ${t === tab ? 'btn-primary' : 'btn-secondary'}" data-tab="${t}">${{ open: 'Open', resolved: 'Fixed', dismissed: 'Dismissed' }[t]}</button>`).join('')}
            <button type="button" class="btn btn-secondary" data-close>Close</button>
          </div>
        </div>
        <p style="color:#94a3b8;margin:6px 0 0;">AI keeps checking your data and tells you what needs attention. Problems move to "Fixed" by themselves once corrected.</p>
        <div data-digest style="margin-top:12px;"></div>
        <div data-list style="margin-top:12px;"><p>Checking…</p></div>
      </div>`;
    el.querySelector('[data-close]').addEventListener('click', () => el.remove());
    el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => open(b.getAttribute('data-tab'))));
    document.body.appendChild(el);

    const list = el.querySelector('[data-list]');
    const dg = el.querySelector('[data-digest]');
    if (tab === 'open') {
      dg.innerHTML = `<button type="button" class="btn btn-secondary" data-digest-btn>✨ Today's AI note</button>`;
      dg.querySelector('[data-digest-btn]').addEventListener('click', async () => {
        dg.innerHTML = '<p style="color:#94a3b8;">✨ Writing today\'s note…</p>';
        try {
          const d = await api('/api/v1/guard/digest');
          dg.innerHTML = `<div style="padding:12px 14px;border-radius:12px;background:rgba(124,58,237,.1);white-space:pre-wrap;">
            <strong>✨ Today's note</strong>\n${esc(d.text)}</div>`;
        } catch (e) { dg.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
      });
    }
    let data;
    try { data = await api(`/api/v1/guard/issues?status=${tab}`); } catch (e) { list.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    if (tab === 'open') badge(data.issues.length, data.alerts);
    if (!data.issues.length) {
      list.innerHTML = `<p style="padding:18px;text-align:center;color:#16a34a;font-weight:600;">${tab === 'open' ? '✅ All clear — nothing needs your attention.' : 'Nothing here.'}</p>`;
      return;
    }
    list.innerHTML = data.issues.map((i) => `
      <div data-issue="${i.id}" style="padding:12px 14px;margin-bottom:8px;border-radius:12px;border:1px solid rgba(148,163,184,.3);border-left:4px solid ${COLORS[i.severity] || '#64748b'};">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <span style="font-size:.72rem;font-weight:700;color:${COLORS[i.severity]};text-transform:uppercase;">${LABEL[i.severity] || ''}</span>
          <span style="font-size:.75rem;color:#94a3b8;">${esc(i.area)}</span>
        </div>
        <div style="font-weight:600;margin-top:2px;">${esc(i.title)}</div>
        ${i.detail ? `<div style="color:#94a3b8;font-size:.88rem;margin-top:2px;">${esc(i.detail)}</div>` : ''}
        <div data-ai style="margin-top:6px;"></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;">
          ${tab !== 'resolved' ? '<button type="button" class="btn btn-secondary" data-explain style="padding:3px 10px;">✨ Explain & fix</button>' : ''}
          ${i.link ? `<button type="button" class="btn btn-secondary" data-go="${esc(i.link)}" style="padding:3px 10px;">Open</button>` : ''}
          ${tab === 'open' ? '<button type="button" class="btn btn-secondary" data-dismiss style="padding:3px 10px;">Dismiss</button>' : ''}
          ${tab === 'dismissed' ? '<button type="button" class="btn btn-secondary" data-reopen style="padding:3px 10px;">Show again</button>' : ''}
        </div>
      </div>`).join('');
    list.addEventListener('click', async (ev) => {
      const card = ev.target.closest('[data-issue]');
      if (!card) return;
      const id = card.getAttribute('data-issue');
      if (ev.target.matches('[data-go]')) { goTo(ev.target.getAttribute('data-go')); return; }
      if (ev.target.matches('[data-explain]')) {
        const box = card.querySelector('[data-ai]');
        box.innerHTML = '<span style="color:#94a3b8;">✨ Thinking…</span>';
        try {
          const d = await api(`/api/v1/guard/issues/${id}/explain`);
          box.innerHTML = `<div style="padding:10px 12px;border-radius:10px;background:rgba(124,58,237,.08);white-space:pre-wrap;font-size:.9rem;">${esc(d.text)}</div>`;
        } catch (e) { box.innerHTML = `<span class="error">${esc(e.message)}</span>`; }
        return;
      }
      const action = ev.target.matches('[data-dismiss]') ? 'dismiss' : (ev.target.matches('[data-reopen]') ? 'reopen' : null);
      if (!action) return;
      try {
        await api(`/api/v1/guard/issues/${id}/${action}`, { method: 'POST' });
        open(tab);
      } catch (e) { if (typeof nexoraToast === 'function') nexoraToast(e.message, 'error'); }
    });
  }

  // Check after sign-in and then every 15 minutes.
  let timer = null;
  function start() {
    if (timer) return;
    setTimeout(check, 4000);
    timer = setInterval(check, 15 * 60 * 1000);
  }
  document.addEventListener('DOMContentLoaded', () => { if (window.authState?.accessToken) start(); });
  window.nxGuard = { open, check, start };
})();
