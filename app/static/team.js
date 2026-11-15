/* Owner / admin: team on duty — live map with today's route, monthly attendance
 * (P/H/A, PEND, NOEND), requests to approve, and work places with a radius.
 * Talks to /api/v1/team/*. Map tiles: OpenStreetMap through Leaflet. */
(function () {
  'use strict';

  const LEAFLET_CSS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';
  const LEAFLET_JS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';
  const COLORS = { P: '#2e7d32', H: '#b26a00', A: '#c62828', PEND: '#1565c0', NOEND: '#6a1b9a' };

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }
  function toast(msg, type) { if (typeof nexoraToast === 'function') nexoraToast(msg, type || 'info'); }

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
    if (!res.ok || data.success === false) throw new Error(data.error?.message || `Request failed (${res.status})`);
    return data.data;
  }

  let leafletReady = null;
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletReady) return leafletReady;
    leafletReady = new Promise((resolve, reject) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = LEAFLET_CSS;
      document.head.appendChild(css);
      const js = document.createElement('script');
      js.src = LEAFLET_JS;
      js.onload = () => resolve();
      js.onerror = () => { leafletReady = null; reject(new Error('Could not load the map')); };
      document.head.appendChild(js);
    });
    return leafletReady;
  }

  function newMap(el, center) {
    const map = window.L.map(el).setView(center || [28.6139, 77.209], 11);
    window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);
    return map;
  }

  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : '');
  const fmtDay = (iso) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  const ago = (m) => (m == null ? '' : m < 1 ? 'just now' : m < 60 ? `${m} min ago` : `${Math.floor(m / 60)} h ${m % 60} min ago`);
  const todayIso = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };

  function shell() {
    document.getElementById('nx-team-desk')?.remove();
    const el = document.createElement('div');
    el.id = 'nx-team-desk';
    el.className = 'modal';
    el.style.zIndex = '10050';
    el.innerHTML = `
      <div class="modal-content" style="max-width:1100px;width:96vw;max-height:92vh;overflow-y:auto;">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;">
          <h2 style="margin:0;">Team on duty</h2>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button type="button" class="btn btn-primary" data-tab="map">Live map</button>
            <button type="button" class="btn btn-secondary" data-tab="attendance">Attendance</button>
            <button type="button" class="btn btn-secondary" data-tab="requests">Requests <span data-req-count></span></button>
            <button type="button" class="btn btn-secondary" data-tab="places">Work places</button>
            <button type="button" class="btn btn-secondary" data-close>Close</button>
          </div>
        </div>
        <div data-body style="margin-top:14px;"></div>
      </div>`;
    el.querySelector('[data-close]').addEventListener('click', () => el.remove());
    document.body.appendChild(el);
    return el;
  }

  // ---------- Live map + a member's day ----------

  async function renderMap(body) {
    body.innerHTML = `<div style="display:grid;grid-template-columns:minmax(220px,300px) 1fr;gap:12px;" class="nx-team-grid">
        <div data-list style="max-height:62vh;overflow-y:auto;"><p>Loading…</p></div>
        <div><div data-map style="height:62vh;border-radius:12px;overflow:hidden;background:#1e293b;"></div>
          <p data-day-info style="color:#94a3b8;font-size:0.85rem;margin:6px 0 0;"></p></div>
      </div>
      <style>@media (max-width:720px){.nx-team-grid{grid-template-columns:1fr !important;}}</style>`;
    const list = body.querySelector('[data-list]');
    const info = body.querySelector('[data-day-info]');
    let members;
    try {
      [members] = await Promise.all([api('/api/v1/team/live').then((d) => d.members || []), loadLeaflet()]);
    } catch (e) { list.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    const map = newMap(body.querySelector('[data-map]'));
    const layer = window.L.layerGroup().addTo(map);
    const name = (m) => m.full_name || m.username;

    function showAll() {
      layer.clearLayers();
      const pts = [];
      members.forEach((m) => {
        if (!m.last) return;
        pts.push([m.last.lat, m.last.lng]);
        window.L.circleMarker([m.last.lat, m.last.lng], {
          radius: 9, color: '#fff', weight: 2, fillColor: m.on_duty ? '#16a34a' : '#64748b', fillOpacity: 0.95,
        }).bindTooltip(`${esc(name(m))} · ${esc(ago(m.last.minutes_ago))}`).addTo(layer);
      });
      if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
      info.textContent = pts.length ? 'Green = on duty now. Pick a person to see their day.' : 'Nobody has shared a location yet. Locations appear after Start day.';
    }

    async function showDay(m, date) {
      info.textContent = 'Loading the day…';
      try {
        const d = await api(`/api/v1/team/members/${m.id}/day?date=${date}`);
        layer.clearLayers();
        const line = (d.points || []).map((p) => [p.lat, p.lng]);
        if (line.length) {
          window.L.polyline(line, { color: '#3b82f6', weight: 4 }).addTo(layer);
          window.L.circleMarker(line[0], { radius: 7, color: '#fff', weight: 2, fillColor: '#16a34a', fillOpacity: 1 })
            .bindTooltip(`Start ${fmtTime(d.points[0].at)}`).addTo(layer);
          window.L.circleMarker(line[line.length - 1], { radius: 7, color: '#fff', weight: 2, fillColor: '#dc2626', fillOpacity: 1 })
            .bindTooltip(`Last ${fmtTime(d.points[d.points.length - 1].at)}`).addTo(layer);
          map.fitBounds(line, { padding: [40, 40], maxZoom: 16 });
        }
        (d.stops || []).forEach((s) => {
          window.L.circleMarker([s.lat, s.lng], { radius: 6, color: '#f59e0b', fillColor: '#f59e0b', fillOpacity: 0.8 })
            .bindTooltip(`Stopped ${s.minutes} min · ${fmtTime(s.from)}–${fmtTime(s.to)}`).addTo(layer);
        });
        const duty = (d.duty || []).map((w) => `${fmtTime(w.started_at)}–${w.ended_at ? fmtTime(w.ended_at) : 'now'}`).join(', ');
        const work = Object.entries(d.work || {}).map(([k, v]) => `${v.length} ${k.replace(/_/g, ' ')}`).join(' · ');
        info.innerHTML = `<strong>${esc(name(m))}</strong> · ${esc(fmtDay(date))} · ${duty ? `On duty ${esc(duty)}` : 'Not on duty'}
          · ${d.km || 0} km · ${(d.stops || []).length} stops${work ? ` · ${esc(work)}` : ''}
          · <a href="#" data-back>Show everyone</a>`;
        info.querySelector('[data-back]').addEventListener('click', (ev) => { ev.preventDefault(); showAll(); });
        if (!line.length) info.insertAdjacentHTML('afterbegin', 'No location on this day. ');
      } catch (e) { info.textContent = e.message; }
    }

    list.innerHTML = members.length ? members.map((m) => `
      <div data-pick="${m.id}" style="padding:10px;border-radius:10px;border:1px solid rgba(148,163,184,.25);margin-bottom:6px;cursor:pointer;">
        <div style="display:flex;justify-content:space-between;gap:6px;"><strong>${esc(name(m))}</strong>
          <span style="font-size:0.75rem;padding:1px 8px;border-radius:999px;background:${m.on_duty ? 'rgba(22,163,74,.2);color:#4ade80' : 'rgba(100,116,139,.2);color:#94a3b8'};">${m.on_duty ? 'On duty' : 'Off'}</span></div>
        <small style="color:#94a3b8;">${esc([m.on_duty && m.duty_started_at ? `Since ${fmtTime(m.duty_started_at)}` : '',
          m.last ? `Seen ${ago(m.last.minutes_ago)}` : 'No location yet'].filter(Boolean).join(' · '))}</small>
        <div data-dayrow="${m.id}" style="display:none;margin-top:6px;"><input type="date" value="${todayIso()}" max="${todayIso()}" style="width:100%;"></div>
      </div>`).join('') : '<p>No team members yet.</p>';
    list.addEventListener('click', (ev) => {
      const card = ev.target.closest('[data-pick]');
      if (!card || ev.target.tagName === 'INPUT') return;
      const m = members.find((x) => String(x.id) === card.getAttribute('data-pick'));
      list.querySelectorAll('[data-dayrow]').forEach((r) => { r.style.display = 'none'; });
      const row = card.querySelector('[data-dayrow]');
      row.style.display = 'block';
      showDay(m, row.querySelector('input').value);
    });
    list.addEventListener('change', (ev) => {
      const card = ev.target.closest('[data-pick]');
      if (!card) return;
      const m = members.find((x) => String(x.id) === card.getAttribute('data-pick'));
      showDay(m, ev.target.value);
    });
    showAll();
  }

  // ---------- Attendance ----------

  async function renderAttendance(body, month) {
    month = month || todayIso().slice(0, 7);
    body.innerHTML = '<p>Loading…</p>';
    let s;
    try { s = await api(`/api/v1/team/attendance?month=${month}`); } catch (e) { body.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    const [y, mo] = month.split('-').map(Number);
    const shift = (n) => { const d = new Date(y, mo - 1 + n, 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; };
    const label = new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    const days = s.people[0]?.days || [];
    const cell = (d) => {
      const c = COLORS[d.status];
      const title = `${d.date}: ${d.hours} h${d.note ? ` — ${d.note}` : ''}`;
      return `<td title="${esc(title)}" style="text-align:center;padding:2px;"><span style="display:inline-block;min-width:26px;padding:3px 2px;border-radius:5px;font-size:0.7rem;font-weight:600;${c ? `background:${c};color:#fff;` : 'color:#64748b;'}">${esc(d.status === '•' ? '·' : d.status || '')}</span></td>`;
    };
    body.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <button type="button" class="btn btn-secondary" data-month="${shift(-1)}">‹</button>
        <strong>${esc(label)}</strong>
        <button type="button" class="btn btn-secondary" data-month="${shift(1)}" ${month >= todayIso().slice(0, 7) ? 'disabled' : ''}>›</button>
        <span style="color:#94a3b8;font-size:0.85rem;">P ${s.rules.full_day_hours} h+ · H ${s.rules.half_day_hours} h+ · A less · PEND approval pending · NOEND End day missing</span>
      </div>
      <div style="overflow-x:auto;margin-top:10px;">
        <table style="border-collapse:collapse;font-size:0.8rem;">
          <tr><th align="left" style="position:sticky;left:0;background:var(--card-bg,#0f172a);padding:4px 8px;">Name</th>
            ${days.map((d) => `<th style="padding:2px;font-weight:500;color:#94a3b8;">${Number(d.date.slice(8))}</th>`).join('')}
            <th style="padding:4px 8px;">P</th><th style="padding:4px 8px;">H</th><th style="padding:4px 8px;">A</th><th style="padding:4px 8px;">Hours</th></tr>
          ${s.people.map((p) => `<tr style="border-top:1px solid rgba(148,163,184,.15);">
            <td style="position:sticky;left:0;background:var(--card-bg,#0f172a);padding:4px 8px;white-space:nowrap;">${esc(p.full_name || p.username)}${p.pending ? ` <small style="color:#60a5fa;">(${p.pending} waiting)</small>` : ''}</td>
            ${p.not_tracked ? `<td colspan="${days.length}" style="color:#64748b;padding:4px;">Attendance not tracked</td>` : p.days.map(cell).join('')}
            <td align="center">${p.present}</td><td align="center">${p.half}</td><td align="center">${p.absent}</td><td align="center">${p.total_hours}</td></tr>`).join('')}
        </table>
      </div>
      <form data-rules style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-top:16px;">
        <div class="form-group" style="margin:0;"><label>Full day (hours)</label><input name="full" type="number" step="0.5" min="1" max="24" value="${s.rules.full_day_hours}"></div>
        <div class="form-group" style="margin:0;"><label>Half day (hours)</label><input name="half" type="number" step="0.5" min="0.5" max="24" value="${s.rules.half_day_hours}"></div>
        <button type="submit" class="btn btn-primary">Save day rule</button>
      </form>`;
    body.querySelectorAll('[data-month]').forEach((b) => b.addEventListener('click', () => renderAttendance(body, b.getAttribute('data-month'))));
    body.querySelector('[data-rules]').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      try {
        await api('/api/v1/team/attendance/rules', { method: 'POST', body: { full_day_hours: Number(f.get('full')), half_day_hours: Number(f.get('half')) } });
        toast('Day rule saved', 'success');
        renderAttendance(body, month);
      } catch (e) { toast(e.message, 'error'); }
    });
  }

  // ---------- Requests ----------

  async function renderRequests(body, desk) {
    body.innerHTML = '<p>Loading…</p>';
    let reqs;
    try { reqs = (await api('/api/v1/team/attendance/requests')).requests || []; } catch (e) { body.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    updateCount(desk, reqs.length);
    if (!reqs.length) { body.innerHTML = '<p>No requests waiting.</p>'; return; }
    body.innerHTML = reqs.map((r) => {
      const far = r.distance_m == null ? '' : r.distance_m >= 1000 ? ` (${(r.distance_m / 1000).toFixed(1)} km away)` : ` (${r.distance_m} m away)`;
      const day = r.started_at ? new Date(r.started_at).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) : '';
      return `<div data-req="${r.id}" style="padding:12px;border-radius:12px;border:1px solid rgba(148,163,184,.3);margin-bottom:8px;">
        <strong>${esc(r.full_name || r.username)}</strong> <span style="color:#94a3b8;">· ${esc(day)}</span>
        <div style="color:#60a5fa;font-size:0.9rem;">${r.kind === 'no_end' ? 'End day missing' : `Ended away from the work place${esc(far)}`}</div>
        <div style="color:#94a3b8;font-size:0.85rem;">Start ${esc(fmtTime(r.started_at))}${r.ended_at ? ` · End ${esc(fmtTime(r.ended_at))}` : ''}</div>
        ${r.note ? `<div style="margin-top:4px;">Why: ${esc(r.note)}</div>` : ''}
        ${r.approver_name ? `<div style="font-size:0.85rem;">Allowed by: ${esc(r.approver_name)}</div>` : ''}
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap;">
          <input data-reply placeholder="Reply (optional)" style="flex:1;min-width:160px;">
          <button type="button" class="btn btn-primary" data-decide="1">Approve</button>
          <button type="button" class="btn btn-secondary" data-decide="0">Reject</button>
        </div></div>`;
    }).join('');
    body.onclick = async (ev) => {
      const btn = ev.target.closest('[data-decide]');
      if (!btn) return;
      const card = btn.closest('[data-req]');
      const reply = card.querySelector('[data-reply]').value.trim();
      try {
        await api(`/api/v1/team/attendance/requests/${card.getAttribute('data-req')}`, {
          method: 'POST', body: { approve: btn.getAttribute('data-decide') === '1', reply: reply || null },
        });
        toast(btn.getAttribute('data-decide') === '1' ? 'Approved' : 'Rejected', 'success');
        renderRequests(body, desk);
      } catch (e) { toast(e.message, 'error'); }
    };
  }

  function updateCount(desk, n) {
    const el = desk.querySelector('[data-req-count]');
    if (el) el.textContent = n ? `(${n})` : '';
  }

  // ---------- Work places ----------

  async function renderPlaces(body) {
    body.innerHTML = '<p>Loading…</p>';
    let sites; let team;
    try {
      [sites, team] = await Promise.all([
        api('/api/v1/team/sites').then((d) => d.sites || []),
        api('/api/v1/invites/team').then((d) => d.members || []),
        loadLeaflet(),
      ]);
    } catch (e) { body.innerHTML = `<p class="error">${esc(e.message)}</p>`; return; }
    const people = team.filter((m) => !m.is_owner);
    body.innerHTML = `
      <p style="color:#94a3b8;margin-top:0;">A member linked to a work place can Start day and End day only there.
        Ending the day somewhere else needs a request that you approve. Click the map to set a new place.</p>
      <div data-map style="height:46vh;border-radius:12px;overflow:hidden;"></div>
      <form data-new style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;margin-top:10px;">
        <div class="form-group" style="margin:0;flex:1;min-width:160px;"><label>Name</label><input name="name" placeholder="e.g. Head office" required></div>
        <div class="form-group" style="margin:0;width:120px;"><label>Radius (m)</label><input name="radius" type="number" min="50" max="5000" value="200"></div>
        <input type="hidden" name="lat"><input type="hidden" name="lng">
        <button type="submit" class="btn btn-primary">Add work place</button>
        <small data-picked style="color:#94a3b8;width:100%;">Click the map to choose the spot.</small>
      </form>
      <div data-sites style="margin-top:14px;"></div>`;
    const map = newMap(body.querySelector('[data-map]'));
    const layer = window.L.layerGroup().addTo(map);
    let pick = null;
    const form = body.querySelector('[data-new]');
    sites.forEach((s) => {
      window.L.circle([s.lat, s.lng], { radius: s.radius_m, color: '#3b82f6' }).bindTooltip(esc(s.name)).addTo(layer);
    });
    if (sites.length) map.fitBounds(sites.map((s) => [s.lat, s.lng]), { padding: [60, 60], maxZoom: 15 });
    map.on('click', (e) => {
      form.lat.value = e.latlng.lat.toFixed(6);
      form.lng.value = e.latlng.lng.toFixed(6);
      if (pick) pick.remove();
      pick = window.L.circle(e.latlng, { radius: Number(form.radius.value) || 200, color: '#16a34a' }).addTo(map);
      body.querySelector('[data-picked]').textContent = `Spot: ${form.lat.value}, ${form.lng.value}`;
    });
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (!form.lat.value) { toast('Click the map to choose the spot', 'error'); return; }
      try {
        await api('/api/v1/team/sites', { method: 'POST', body: {
          name: form.name.value.trim(), lat: Number(form.lat.value), lng: Number(form.lng.value), radius_m: Number(form.radius.value) || 200,
        } });
        toast('Work place added', 'success');
        renderPlaces(body);
      } catch (e) { toast(e.message, 'error'); }
    });
    const box = body.querySelector('[data-sites]');
    box.innerHTML = sites.length ? sites.map((s) => `
      <div data-site="${s.id}" style="padding:10px 12px;border-radius:12px;border:1px solid rgba(148,163,184,.3);margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;gap:8px;"><strong>${esc(s.name)}</strong>
          <span><small style="color:#94a3b8;">${s.radius_m} m</small>
          <button type="button" class="btn btn-secondary" data-del="${s.id}" style="padding:2px 8px;margin-left:6px;">Delete</button></span></div>
        <div style="display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px;">
          ${people.map((m) => `<label style="display:flex;gap:6px;align-items:center;font-size:0.85rem;">
            <input type="checkbox" data-member="${m.id}" ${(s.member_ids || []).includes(m.id) ? 'checked' : ''}> ${esc(m.full_name || m.username)}</label>`).join('') || '<small>No members yet.</small>'}
        </div></div>`).join('') : '<p>No work places yet. Members without one can start their day anywhere.</p>';
    box.addEventListener('click', async (ev) => {
      const id = ev.target.getAttribute?.('data-del');
      if (!id || !confirm('Delete this work place?')) return;
      try { await api(`/api/v1/team/sites/${id}`, { method: 'DELETE' }); renderPlaces(body); } catch (e) { toast(e.message, 'error'); }
    });
    box.addEventListener('change', async (ev) => {
      const memberId = ev.target.getAttribute?.('data-member');
      if (!memberId) return;
      // A member's places = every place whose box is ticked for them.
      const ids = [...box.querySelectorAll(`[data-member="${memberId}"]:checked`)].map((x) => Number(x.closest('[data-site]').getAttribute('data-site')));
      try {
        await api(`/api/v1/team/members/${memberId}/sites`, { method: 'POST', body: { site_ids: ids } });
        toast('Saved', 'success');
      } catch (e) { toast(e.message, 'error'); ev.target.checked = !ev.target.checked; }
    });
  }

  function openTeam(tab) {
    if (typeof closeUserProfileMenu === 'function') closeUserProfileMenu();
    const desk = shell();
    const body = desk.querySelector('[data-body]');
    const go = (t) => {
      desk.querySelectorAll('[data-tab]').forEach((b) => {
        b.classList.toggle('btn-primary', b.getAttribute('data-tab') === t);
        b.classList.toggle('btn-secondary', b.getAttribute('data-tab') !== t);
      });
      body.onclick = null;
      if (t === 'attendance') renderAttendance(body);
      else if (t === 'requests') renderRequests(body, desk);
      else if (t === 'places') renderPlaces(body);
      else renderMap(body);
    };
    desk.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => go(b.getAttribute('data-tab'))));
    api('/api/v1/team/attendance/requests').then((d) => updateCount(desk, (d.requests || []).length)).catch(() => {});
    go(tab || 'map');
  }

  window.nxTeam = { openTeam };
})();
