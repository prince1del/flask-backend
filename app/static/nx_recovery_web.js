/* Account recovery on the web — same server routes as the phone.
   Login page: "Forgot user ID?" (email it, or answer the secret question) and "Forgot password?"
   (6-digit email code, or the secret question). After login: "Secure your account" asks once for a
   secret question + email so recovery always works ("Later" asks again after 3 days). */
(function () {
  'use strict';

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  async function post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    let j = {};
    try { j = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok || j.success === false) throw new Error((j.error && j.error.message) || j.message || 'Something went wrong — try again');
    return j;
  }

  function modal(title, html) {
    let m = document.getElementById('nx-recovery-modal');
    if (!m) {
      m = document.createElement('div');
      m.id = 'nx-recovery-modal';
      m.className = 'modal';
      document.body.appendChild(m);
    }
    m.classList.remove('hidden');
    m.innerHTML = `<div class="modal-content" style="max-width:440px;">
      <h2 style="margin-top:0;">${esc(title)}</h2>${html}
      <p id="nx-rec-msg" class="card-description" style="min-height:1.2em;"></p>
      <div class="modal-actions"><button type="button" class="btn btn-secondary" onclick="nxRecoveryClose()">Close</button></div></div>`;
    return m;
  }
  const msg = (t) => { const el = document.getElementById('nx-rec-msg'); if (el) el.textContent = t || ''; };
  const val = (id) => (document.getElementById(id)?.value || '').trim();
  window.nxRecoveryClose = () => document.getElementById('nx-recovery-modal')?.classList.add('hidden');

  /* ---------- Forgot user ID ---------- */
  window.nxForgotUserId = () => {
    modal('Forgot user ID?', `<p class="card-description">Type the email or mobile number on your account.</p>
      <div class="form-group"><input id="nx-rec-login" placeholder="Email or mobile number" autocomplete="email" /></div>
      <div id="nx-rec-step" style="display:flex;gap:8px;flex-wrap:wrap;">
        <button type="button" class="btn btn-secondary" onclick="nxRecUidEmail()">Email it to me</button>
        <button type="button" class="btn btn-primary" onclick="nxRecUidQuestion()">Secret question</button></div>`);
  };
  window.nxRecUidEmail = async () => {
    try {
      const j = await post('/api/v1/auth/forgot-user-id/email', { login: val('nx-rec-login') });
      msg(`If an account matches, its user ID was sent to ${j.data?.email_hint || 'the email on file'}.`);
    } catch (e) { msg(e.message); }
  };
  window.nxRecUidQuestion = async () => {
    try {
      const j = await post('/api/v1/auth/forgot-user-id/question', { login: val('nx-rec-login') });
      const q = j.data?.secret_question;
      if (!q) { msg('No secret question found for that — try email, or ask your company admin.'); return; }
      document.getElementById('nx-rec-step').innerHTML = `<p style="width:100%;font-weight:600;">${esc(q)}</p>
        <div class="form-group" style="width:100%;"><input id="nx-rec-answer" placeholder="Your answer" /></div>
        <button type="button" class="btn btn-primary" onclick="nxRecUidAnswer()">Show my user ID</button>`;
      msg('');
    } catch (e) { msg(e.message); }
  };
  window.nxRecUidAnswer = async () => {
    try {
      const j = await post('/api/v1/auth/forgot-user-id', { login: val('nx-rec-login'), secret_answer: val('nx-rec-answer') });
      const id = j.data?.username || '';
      document.getElementById('nx-rec-step').innerHTML = `<p style="width:100%;">Your user ID is <strong style="font-size:1.3em;">${esc(id)}</strong></p>
        <button type="button" class="btn btn-primary" onclick="nxRecUseId(${JSON.stringify(id).replace(/"/g, '&quot;')})">Use this ID</button>`;
      msg('You can also sign in with your email address.');
    } catch (e) { msg(e.message); }
  };
  window.nxRecUseId = (id) => {
    const u = document.getElementById('username');
    if (u) u.value = id;
    window.nxRecoveryClose();
    document.getElementById('password')?.focus();
  };

  /* ---------- Forgot password ---------- */
  window.nxForgotPassword = () => {
    const pre = (document.getElementById('username')?.value || '').trim();
    modal('Forgot password?', `<div class="form-group"><input id="nx-rec-user" placeholder="User ID or email" value="${esc(pre)}" /></div>
      <div id="nx-rec-step" style="display:flex;gap:8px;flex-wrap:wrap;">
        <button type="button" class="btn btn-primary" onclick="nxRecPwEmail()">Email me a code</button>
        <button type="button" class="btn btn-secondary" onclick="nxRecPwQuestion()">Secret question</button></div>`);
  };
  function newPasswordFields(extra) {
    return `${extra}<div class="form-group" style="width:100%;"><input id="nx-rec-new" type="password" placeholder="New password (min 8)" autocomplete="new-password" /></div>
      <div class="form-group" style="width:100%;"><input id="nx-rec-new2" type="password" placeholder="Confirm new password" autocomplete="new-password" /></div>`;
  }
  function passwordsOk() {
    if (val('nx-rec-new').length < 8) { msg('Password must be at least 8 characters'); return false; }
    if (val('nx-rec-new') !== val('nx-rec-new2')) { msg('The two passwords do not match'); return false; }
    return true;
  }
  window.nxRecPwEmail = async () => {
    try {
      const j = await post('/api/v1/auth/request-password-reset', { login: val('nx-rec-user') });
      document.getElementById('nx-rec-step').innerHTML = newPasswordFields(
        '<div class="form-group" style="width:100%;"><input id="nx-rec-code" inputmode="numeric" maxlength="6" placeholder="6-digit code" /></div>')
        + '<button type="button" class="btn btn-primary" onclick="nxRecPwCodeSave()">Reset password</button>';
      msg(`${j.message || 'Code sent.'}${j.data?.email_hint ? ` (${j.data.email_hint})` : ''} It is valid for 15 minutes.`);
    } catch (e) { msg(e.message); }
  };
  window.nxRecPwCodeSave = async () => {
    if (!passwordsOk()) return;
    try {
      await post('/api/v1/auth/reset-password-with-code', { login: val('nx-rec-user'), code: val('nx-rec-code'), new_password: val('nx-rec-new') });
      msg('Password changed — sign in with the new password.');
      document.getElementById('nx-rec-step').innerHTML = '';
    } catch (e) { msg(e.message); }
  };
  window.nxRecPwQuestion = async () => {
    try {
      const j = await post('/api/v1/auth/secret-question-hint', { login: val('nx-rec-user') });
      const q = j.data?.secret_question;
      if (!q) { msg('No secret question is set for this account — try the email code, or ask your company admin.'); return; }
      document.getElementById('nx-rec-step').innerHTML = newPasswordFields(`<p style="width:100%;font-weight:600;">${esc(q)}</p>
        <div class="form-group" style="width:100%;"><input id="nx-rec-answer" placeholder="Your answer" /></div>`)
        + '<button type="button" class="btn btn-primary" onclick="nxRecPwQuestionSave()">Reset password</button>';
      msg('');
    } catch (e) { msg(e.message); }
  };
  window.nxRecPwQuestionSave = async () => {
    if (!passwordsOk()) return;
    try {
      await post('/api/v1/auth/forgot-password', { username: val('nx-rec-user'), secret_answer: val('nx-rec-answer'), new_password: val('nx-rec-new') });
      msg('Password changed — sign in with the new password.');
      document.getElementById('nx-rec-step').innerHTML = '';
    } catch (e) { msg(e.message); }
  };

  /* ---------- After login: Secure your account ---------- */
  async function authed(path, opts) {
    if (typeof fetchWithAuth !== 'function') throw new Error('not ready');
    const r = await fetchWithAuth(path, opts);
    const j = await r.json();
    if (!r.ok || j.success === false) throw new Error((j.error && j.error.message) || 'Could not save');
    return j.data;
  }
  async function maybeAskSetup() {
    try {
      if (typeof authState === 'undefined' || !authState.accessToken) return false;
      const snooze = Number(localStorage.getItem('nxRecoverySnooze') || 0);
      if (Date.now() < snooze) return true;
      const st = await authed('/api/v1/auth/recovery-pin-status');
      if (st.has_secret_question) return true;
      const prof = await authed('/api/v1/me/profile');
      const qs = (await (await fetch('/api/v1/auth/secret-questions')).json()).data.questions || [];
      modal('Secure your account', `<p class="card-description">If you ever forget your password or user ID, this gets you back in.</p>
        <div class="form-group"><select id="nx-rec-q">${qs.map((q) => `<option>${esc(q)}</option>`).join('')}</select></div>
        <div class="form-group"><input id="nx-rec-a" placeholder="Your answer (easy to remember, hard to guess)" /></div>
        <div class="form-group"><input id="nx-rec-mail" type="email" placeholder="Email (for reset codes)" value="${esc(prof.email || '')}" /></div>
        <div style="display:flex;gap:8px;"><button type="button" class="btn btn-primary" onclick="nxRecSetupSave(${JSON.stringify(prof.email || '').replace(/"/g, '&quot;')})">Save</button>
        <button type="button" class="btn btn-secondary" onclick="nxRecSetupLater()">Later</button></div>`);
      return true;
    } catch (_) { return false; }
  }
  window.nxRecSetupSave = async (hadEmail) => {
    try {
      await authed('/api/v1/me/secret-question', { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ secret_question: val('nx-rec-q'), secret_answer: val('nx-rec-a') }) });
      const mail = val('nx-rec-mail');
      if (mail && mail !== hadEmail) {
        await authed('/api/v1/me/profile', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: mail }) });
      }
      window.nxRecoveryClose();
      if (typeof nexoraToast === 'function') nexoraToast('Account secured', 'ok');
    } catch (e) { msg(e.message); }
  };
  window.nxRecSetupLater = () => {
    try { localStorage.setItem('nxRecoverySnooze', String(Date.now() + 3 * 24 * 3600 * 1000)); } catch (_) { /* private mode */ }
    window.nxRecoveryClose();
  };
  // Wait for login, then ask once.
  let tries = 0;
  (function loop() {
    tries += 1;
    maybeAskSetup().then((done) => { if (!done && tries < 120) setTimeout(loop, 10000); });
  })();
})();
