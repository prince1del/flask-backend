/* Payment reminders on the web — same routes as the phone (/api/v1/hop/reminders…). */
(function () {
  'use strict';

  const esc = (v) => (typeof foEscapeText === 'function' ? foEscapeText(v) : String(v ?? ''));
  const toast = (m, t) => { if (typeof nexoraToast === 'function') nexoraToast(m, t || 'ok'); };
  const post = (path) => hopApi(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });

  async function render(mount) {
    let d;
    try { d = await hopApi('/api/v1/hop/reminders'); } catch (e) {
      mount.innerHTML = hopModuleShell('Sale', 'Payment reminders', '', '', `<p class="nx-oc-error">${esc(e.message)}</p>`);
      return;
    }
    const groups = [['overdue', 'Overdue', '#dc2626'], ['today', 'Due today', '#d97706'], ['soon', `Due in ${d.soon_days} days`, '#2563eb']];
    const any = groups.some(([k]) => (d[k] || []).length);
    const body = `
      <div class="hop-books-actions">
        <button type="button" class="nx-btn" onclick="hopRemSendMe('summary')">Email today's list</button>
        <button type="button" class="nx-btn" onclick="hopRemSendMe('backup')">Email backup</button>
      </div>
      ${any ? '' : '<p>Nothing to collect right now. 🎉</p>'}
      ${groups.map(([k, title, color]) => (d[k] || []).length ? `
        <h3 style="color:${color};margin:16px 0 6px;">${esc(title)} · ${d[k].length} · ${hopMoney(d.totals[k])}</h3>
        <table class="inv-table"><thead><tr><th>Customer</th><th>Bill</th><th>Due</th><th class="num">Balance</th><th></th></tr></thead><tbody>
        ${d[k].map((it) => `<tr><td>${esc(it.party)}</td><td>${esc(it.number)}</td>
          <td>${esc(it.due_date)}${it.days < 0 ? ` <small style="color:#dc2626;">(${-it.days} days late)</small>` : ''}
            ${it.last_reminded ? `<div class="hop-doc-muted">Last reminded ${esc(String(it.last_reminded).slice(0, 10))}</div>` : ''}</td>
          <td class="num">${hopMoney(it.balance)}</td>
          <td><a class="nx-btn" href="${esc(it.whatsapp_url)}" target="_blank" rel="noopener" onclick="hopRemNote(${it.id})">WhatsApp</a>
            ${it.email ? `<button type="button" class="nx-btn" onclick="hopRemEmail(${it.id})">Email</button>` : ''}</td></tr>`).join('')}
        </tbody></table>` : '').join('')}
      <p class="hop-doc-muted" style="margin-top:12px;">Turn on automatic reminders and the morning email in Settings → Reminders.</p>`;
    mount.innerHTML = hopModuleShell('Sale', 'Payment reminders', 'Money to collect — remind in one tap', '', body);
  }

  const rerender = () => { const m = document.getElementById('hop-module-mount'); if (m) render(m); };
  window.renderHopRemindersModule = render;
  window.hopRemNote = (id) => { post(`/api/v1/hop/reminders/${id}/whatsapp`).then(() => setTimeout(rerender, 800)).catch(() => {}); };
  window.hopRemEmail = async (id) => {
    try { const r = await post(`/api/v1/hop/reminders/${id}/email`); toast(`Emailed ${r.sent_to}`); rerender(); } catch (e) { toast(e.message, 'err'); }
  };
  window.hopRemSendMe = async (what) => {
    try { const r = await post(`/api/v1/hop/reminders/send-me/${what}`); toast(`Sent to ${r.sent_to}`); } catch (e) { toast(e.message, 'err'); }
  };
})();
