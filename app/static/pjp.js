/** Monthly PJP — Excel travel-plan format (Date / Place / From-To / Activity / Particulars). */

let pjpYearMonth = (() => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
})();

function pjpShiftMonth(delta) {
  const [y, m] = pjpYearMonth.split('-').map(Number);
  const dt = new Date(y, m - 1 + delta, 1);
  pjpYearMonth = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`;
  loadPjpWorkspace();
}

function pjpMonthLabel(ym) {
  const [y, m] = ym.split('-').map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
}

async function loadPjpWorkspace() {
  const status = document.getElementById('pjp-status');
  const list = document.getElementById('pjp-day-list');
  const title = document.getElementById('pjp-month-title');
  if (title) title.textContent = pjpMonthLabel(pjpYearMonth);
  if (status) status.textContent = 'Loading…';
  try {
    const response = await fetchWithAuth(`/api/v1/pjp/months/${pjpYearMonth}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to load PJP');
    }
    const payload = data.data || {};
    const stats = payload.stats || {};
    const meta = payload.meta || {};
    const elStats = document.getElementById('pjp-stats');
    if (elStats) {
      elStats.textContent = `${stats.planned_days || 0} planned · ${stats.outstation_nights || 0} night stays`;
    }
    const elMeta = document.getElementById('pjp-meta');
    if (elMeta) {
      const bits = [meta.sm_name, meta.zone ? `Zone · ${meta.zone}` : null].filter(Boolean);
      elMeta.textContent = bits.join('  ·  ') || 'Travel plan for the month';
    }
    const days = (payload.days || []).filter(
      (d) => (d.place_to_visit || '').trim() || ['holiday', 'leave'].includes(d.day_type)
    );
    if (!list) return;
    if (!days.length) {
      list.innerHTML =
        '<p class="subtitle">No days planned yet. Use Edit on a blank day row below, or plan from the Android app.</p>';
      // still show all days compact
    }
    const all = payload.days || [];
    list.innerHTML = all
      .map((d) => {
        const place = (d.place_to_visit || '').trim();
        const empty = !place && d.day_type !== 'holiday' && d.day_type !== 'leave';
        const route =
          d.from_place || d.to_place
            ? `${d.from_place || '—'} → ${d.to_place || '—'}`
            : '';
        const night = d.night_stay ? `<span class="pjp-night">Night · ${escapeHtml(d.night_stay)}</span>` : '';
        return `<article class="pjp-day-card ${empty ? 'is-empty' : ''}" data-date="${escapeHtml(d.plan_date)}">
          <div class="pjp-day-head">
            <div>
              <div class="pjp-day-date">${escapeHtml(d.day_name || '')} · ${escapeHtml(d.plan_date || '')}</div>
              <div class="pjp-day-place">${escapeHtml(place || (d.day_type === 'weekend' ? 'Weekend' : 'Not planned'))}</div>
            </div>
            <button type="button" class="btn btn-secondary btn-sm" onclick="pjpEditDay('${escapeHtml(d.plan_date)}')">Edit</button>
          </div>
          ${route ? `<div class="pjp-day-route">${escapeHtml(route)}</div>` : ''}
          ${d.business_activity ? `<div class="pjp-day-activity">${escapeHtml(d.business_activity)}</div>` : ''}
          ${d.particulars ? `<div class="pjp-day-note">${escapeHtml(d.particulars)}</div>` : ''}
          ${night}
        </article>`;
      })
      .join('');
    if (status) status.textContent = '';
  } catch (error) {
    if (status) status.textContent = error.message || 'Unable to load PJP';
  }
}

function pjpEditDay(planDate) {
  const card = document.querySelector(`.pjp-day-card[data-date="${planDate}"]`);
  const place = prompt('Places to Visit', '') || '';
  if (place === null) return;
  const fromPlace = prompt('From', '') || '';
  const toPlace = prompt('To', '') || '';
  const activity = prompt('Business Activities / Purpose', '') || '';
  const particulars = prompt('Particulars', '') || '';
  const nightStay = prompt('Night stay city (optional)', '') || '';
  pjpSaveDay(planDate, {
    place_to_visit: place.trim() || null,
    from_place: fromPlace.trim() || null,
    to_place: toPlace.trim() || null,
    business_activity: activity.trim() || null,
    particulars: particulars.trim() || null,
    night_stay: nightStay.trim() || null,
    day_type: place.trim().toLowerCase() === 'holiday' ? 'holiday'
      : place.trim().toLowerCase() === 'leave' ? 'leave' : 'work',
  });
  void card;
}

async function pjpSaveDay(planDate, body) {
  try {
    const response = await fetchWithAuth(`/api/v1/pjp/days/${planDate}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to save');
    }
    await loadPjpWorkspace();
  } catch (error) {
    alert(error.message || 'Unable to save PJP day');
  }
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function pjpImportExcel(input) {
  const file = input?.files?.[0];
  if (!file) return;
  const status = document.getElementById('pjp-status');
  if (status) status.textContent = `Importing ${file.name}…`;
  try {
    const form = new FormData();
    form.append('file', file);
    const response = await fetchWithAuth('/api/v1/pjp/import', {
      method: 'POST',
      body: form,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to import Excel');
    }
    const ym = data.data?.year_month;
    if (ym) pjpYearMonth = ym;
    const planned = data.data?.stats?.planned_days ?? data.data?.import?.planned_days ?? 0;
    if (status) status.textContent = `Imported ${ym || ''} · ${planned} planned days`;
    await loadPjpWorkspace();
    if (typeof loadPjpWeekWidgets === 'function') loadPjpWeekWidgets();
  } catch (error) {
    if (status) status.textContent = error.message || 'Import failed';
    alert(error.message || 'Unable to import PJP Excel');
  } finally {
    if (input) input.value = '';
  }
}

async function loadPjpWeekWidgets() {
  try {
    const response = await fetchWithAuth('/api/v1/pjp/week');
    const data = await response.json();
    if (!response.ok || !data.success) return;
    const payload = data.data || {};
    const days = payload.days || [];
    const range = `Mon–Sun · ${payload.start_date || ''} – ${payload.end_date || ''} · ${payload.planned_days || 0} planned`;
    ['pjp-week-range', 'pjp-dash-week-range'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.textContent = range;
    });
    const html = days
      .map((d) => {
        const place = (d.place_to_visit || '').trim();
        const label = place || (d.day_type === 'weekend' ? 'Weekend' : 'Not planned');
        const route =
          d.from_place || d.to_place
            ? `${d.from_place || '—'} → ${d.to_place || '—'}`
            : '';
        const act = d.business_activity || '';
        const meta = [route, act].filter(Boolean).join(' · ');
        return `<div class="pjp-week-row">
          <div class="pjp-week-date">${escapeHtml((d.day_name || '').slice(0, 3))}<strong>${escapeHtml((d.plan_date || '').slice(8))}</strong></div>
          <div class="pjp-week-body">
            <div class="pjp-week-place">${escapeHtml(label)}</div>
            ${meta ? `<div class="pjp-week-meta">${escapeHtml(meta)}</div>` : ''}
          </div>
        </div>`;
      })
      .join('');
    ['pjp-week-list', 'pjp-dash-week-list'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html || '<p class="subtitle">No days planned yet.</p>';
    });
  } catch (_) {
    /* ignore — My Day remains usable */
  }
}
