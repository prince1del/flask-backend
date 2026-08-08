/** Personal To-Do / work diary — Phase 1 (reminder notify = Phase 2 backlog). */

const PTODO_CATEGORIES = [
  'Boss Task',
  'Retailer Query',
  'Distributor Query',
  'Call',
  'Report',
  'Internal Work',
  'Other',
];

let ptodoState = {
  bucket: 'all',
  searchTimer: null,
  lastTodos: [],
  undoTimer: null,
  undoId: null,
};

function ptodoEsc(value) {
  if (typeof foEscapeText === 'function') return foEscapeText(value || '');
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function ptodoSetStatus(msg, isError) {
  const el = document.getElementById('ptodo-status');
  if (!el) return;
  el.textContent = msg || '';
  el.style.color = isError ? '#b91c1c' : '';
}

function ptodoSetBucket(bucket) {
  ptodoState.bucket = bucket || 'all';
  document.querySelectorAll('#ptodo-bucket-filters .ptodo-chip').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.bucket === ptodoState.bucket);
  });
  loadPersonalTodoWorkspace();
}

function ptodoDebouncedReload() {
  clearTimeout(ptodoState.searchTimer);
  ptodoState.searchTimer = setTimeout(() => loadPersonalTodoWorkspace(), 280);
}

function ptodoOnDuePreset() {
  const preset = document.getElementById('ptodo-due-preset')?.value || 'none';
  const wrap = document.getElementById('ptodo-due-date-wrap');
  const dateInput = document.getElementById('ptodo-due-date');
  if (!wrap || !dateInput) return;
  if (preset === 'date') {
    wrap.style.display = '';
  } else {
    wrap.style.display = 'none';
    const today = new Date();
    const yyyy = today.getFullYear();
    const mm = String(today.getMonth() + 1).padStart(2, '0');
    const dd = String(today.getDate()).padStart(2, '0');
    const todayStr = `${yyyy}-${mm}-${dd}`;
    if (preset === 'today') dateInput.value = todayStr;
    else if (preset === 'tomorrow') {
      const t = new Date(today);
      t.setDate(t.getDate() + 1);
      dateInput.value = `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
    } else dateInput.value = '';
  }
}

function ptodoResolveDueDate() {
  const preset = document.getElementById('ptodo-due-preset')?.value || 'none';
  if (preset === 'none') return null;
  const dateInput = document.getElementById('ptodo-due-date')?.value || '';
  if (preset === 'date') return dateInput || null;
  return dateInput || null;
}

function ptodoOpenForm(todo) {
  const modal = document.getElementById('ptodo-form-modal');
  if (!modal) return;
  document.getElementById('ptodo-form-title').textContent = todo?.id ? 'Edit To-Do' : 'Add To-Do';
  document.getElementById('ptodo-edit-id').value = todo?.id || '';
  document.getElementById('ptodo-task-title').value = todo?.task_title || '';
  document.getElementById('ptodo-category').value = todo?.category || '';
  document.getElementById('ptodo-person').value = todo?.person_party || '';
  document.getElementById('ptodo-given-by').value = todo?.given_by || '';
  document.getElementById('ptodo-priority').value = todo?.priority || 'normal';
  document.getElementById('ptodo-due-time').value = todo?.due_time || '';
  document.getElementById('ptodo-remarks').value = todo?.remarks || '';
  const rem = todo?.reminder_datetime || '';
  document.getElementById('ptodo-reminder').value = rem
    ? rem.slice(0, 16).replace('T', 'T')
    : '';

  const due = todo?.due_date || '';
  const presetEl = document.getElementById('ptodo-due-preset');
  const dateEl = document.getElementById('ptodo-due-date');
  if (!due) {
    presetEl.value = 'none';
    dateEl.value = '';
  } else {
    const today = new Date();
    const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    const tmr = new Date(today);
    tmr.setDate(tmr.getDate() + 1);
    const tmrStr = `${tmr.getFullYear()}-${String(tmr.getMonth() + 1).padStart(2, '0')}-${String(tmr.getDate()).padStart(2, '0')}`;
    if (due === todayStr) presetEl.value = 'today';
    else if (due === tmrStr) presetEl.value = 'tomorrow';
    else presetEl.value = 'date';
    dateEl.value = due;
  }
  ptodoOnDuePreset();
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.getElementById('ptodo-task-title')?.focus();
}

function ptodoCloseForm() {
  const modal = document.getElementById('ptodo-form-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  document.getElementById('ptodo-form')?.reset();
  document.getElementById('ptodo-edit-id').value = '';
}

async function ptodoSaveForm(event) {
  event.preventDefault();
  const editId = document.getElementById('ptodo-edit-id')?.value;
  const body = {
    task_title: document.getElementById('ptodo-task-title').value.trim(),
    category: document.getElementById('ptodo-category').value || null,
    person_party: document.getElementById('ptodo-person').value.trim() || null,
    given_by: document.getElementById('ptodo-given-by').value.trim() || null,
    priority: document.getElementById('ptodo-priority').value || 'normal',
    due_date: ptodoResolveDueDate(),
    due_time: document.getElementById('ptodo-due-time').value || null,
    reminder_datetime: document.getElementById('ptodo-reminder').value
      ? new Date(document.getElementById('ptodo-reminder').value).toISOString()
      : null,
    remarks: document.getElementById('ptodo-remarks').value.trim() || null,
  };
  if (!body.task_title) {
    alert('Task title is required.');
    return false;
  }
  try {
    const url = editId
      ? `/api/v1/personal-todos/${editId}`
      : '/api/v1/personal-todos';
    const response = await fetchWithAuth(url, {
      method: editId ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to save To-Do');
    }
    ptodoCloseForm();
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to save To-Do');
  }
  return false;
}

async function ptodoQuickAdd() {
  const input = document.getElementById('ptodo-quick-input');
  const title = (input?.value || '').trim();
  if (!title) return;
  try {
    const response = await fetchWithAuth('/api/v1/personal-todos', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_title: title,
        priority: 'normal',
        status: 'pending',
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to add To-Do');
    }
    if (input) input.value = '';
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to add To-Do');
  }
}

function ptodoMetaLine(todo) {
  const bits = [];
  if (todo.category) bits.push(todo.category);
  if (todo.person_party) bits.push(todo.person_party);
  if (todo.due_date) {
    let due = todo.due_date;
    if (todo.due_time) due += ` · ${todo.due_time}`;
    bits.push(due);
  }
  if (todo.reminder_datetime) bits.push('🔔');
  if (todo.priority && todo.priority !== 'normal') {
    bits.push(`Priority: ${todo.priority}`);
  }
  return bits.join(' · ');
}

function ptodoRenderList(todos) {
  const host = document.getElementById('ptodo-list');
  if (!host) return;
  ptodoState.lastTodos = todos || [];
  if (!todos.length) {
    host.innerHTML = '<p class="subtitle">No To-Dos in this view.</p>';
    return;
  }

  const sections = [
    { key: 'overdue', title: 'Overdue' },
    { key: 'today', title: 'Today' },
    { key: 'upcoming', title: 'Upcoming' },
    { key: 'no_due', title: 'No Due Date' },
    { key: 'hold', title: 'Hold' },
    { key: 'completed', title: 'Completed' },
  ];

  const bucketFilter = ptodoState.bucket;
  const html = [];
  sections.forEach((sec) => {
    if (bucketFilter !== 'all' && bucketFilter !== sec.key) return;
    const rows = todos.filter((t) => t.bucket === sec.key);
    if (!rows.length && bucketFilter === 'all') return;
    html.push(`<div class="ptodo-section"><h3>${sec.title}</h3>`);
    if (!rows.length) {
      html.push('<p class="subtitle">None</p></div>');
      return;
    }
    rows.forEach((t) => {
      const done = t.status === 'done';
      const urgent = t.priority === 'urgent';
      const overdue = t.bucket === 'overdue';
      html.push(`
        <article class="ptodo-card${urgent ? ' is-urgent' : ''}${overdue ? ' is-overdue' : ''}${done ? ' is-done' : ''}" data-id="${t.id}">
          <label class="ptodo-check">
            <input type="checkbox" ${done ? 'checked' : ''} onchange="ptodoToggleDone(${t.id}, this.checked)" ${done ? '' : ''}>
          </label>
          <div class="ptodo-card-body" onclick="ptodoOpenForm(ptodoFind(${t.id}))">
            <div class="ptodo-title">${ptodoEsc(t.task_title)}</div>
            <div class="ptodo-meta">${ptodoEsc(ptodoMetaLine(t))}</div>
            ${overdue ? '<div class="ptodo-overdue-tag">Overdue</div>' : ''}
            ${done && t.completed_at ? `<div class="ptodo-meta">Completed: ${ptodoEsc(t.completed_at.slice(0, 16).replace('T', ' '))}</div>` : ''}
          </div>
          <div class="ptodo-card-actions">
            ${!done ? `<button type="button" class="btn btn-secondary" onclick="event.stopPropagation();ptodoHold(${t.id})">Hold</button>` : ''}
            ${done ? `<button type="button" class="btn btn-secondary" onclick="event.stopPropagation();ptodoReopen(${t.id})">Undo</button>` : ''}
            <button type="button" class="btn btn-secondary" onclick="event.stopPropagation();ptodoDelete(${t.id})">Delete</button>
          </div>
        </article>
      `);
    });
    html.push('</div>');
  });
  host.innerHTML = html.join('');
}

function ptodoFind(id) {
  return ptodoState.lastTodos.find((t) => Number(t.id) === Number(id)) || null;
}

async function loadPersonalTodoWorkspace() {
  const status = ptodoState.bucket === 'completed' ? 'done'
    : ptodoState.bucket === 'hold' ? 'hold'
      : null;
  const params = new URLSearchParams();
  if (ptodoState.bucket && ptodoState.bucket !== 'all') {
    params.set('bucket', ptodoState.bucket);
  }
  if (status) params.set('status', status);
  const cat = document.getElementById('ptodo-category-filter')?.value || '';
  if (cat) params.set('category', cat);
  const q = document.getElementById('ptodo-search')?.value.trim() || '';
  if (q) params.set('q', q);

  ptodoSetStatus('Loading…');
  try {
    const response = await fetchWithAuth(`/api/v1/personal-todos?${params.toString()}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to load To-Dos');
    }
    ptodoRenderList(data.data?.todos || []);
    ptodoSetStatus(`${data.data?.count || 0} item(s)`);
  } catch (error) {
    ptodoSetStatus(error.message || 'Unable to load To-Dos', true);
    ptodoRenderList([]);
  }
}

async function ptodoToggleDone(id, checked) {
  try {
    const path = checked
      ? `/api/v1/personal-todos/${id}/done`
      : `/api/v1/personal-todos/${id}/reopen`;
    const response = await fetchWithAuth(path, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to update');
    }
    if (checked) {
      ptodoOfferUndo(id);
    }
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to update');
    await loadPersonalTodoWorkspace();
  }
}

function ptodoOfferUndo(id) {
  clearTimeout(ptodoState.undoTimer);
  ptodoState.undoId = id;
  ptodoSetStatus('Marked done. Undo? Click Undo on the Completed card, or press Undo here within 8s.');
  const host = document.getElementById('ptodo-status');
  if (host) {
    host.innerHTML = `Marked done. <button type="button" class="btn btn-secondary" onclick="ptodoReopen(${id})">Undo</button>`;
  }
  ptodoState.undoTimer = setTimeout(() => {
    ptodoState.undoId = null;
    loadPersonalTodoWorkspace();
  }, 8000);
}

async function ptodoHold(id) {
  try {
    const response = await fetchWithAuth(`/api/v1/personal-todos/${id}/hold`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to hold');
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to hold');
  }
}

async function ptodoReopen(id) {
  try {
    const response = await fetchWithAuth(`/api/v1/personal-todos/${id}/reopen`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to reopen');
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to reopen');
  }
}

async function ptodoDelete(id) {
  if (!confirm('Delete this To-Do?\n\nCancel / Delete')) return;
  try {
    const response = await fetchWithAuth(`/api/v1/personal-todos/${id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to delete');
    await loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to delete');
  }
}

function ptodoRenderWidget(countsElId, previewElId, summary) {
  const countsEl = document.getElementById(countsElId);
  const previewEl = document.getElementById(previewElId);
  if (!countsEl || !previewEl) return;
  const overdue = summary.overdue_count || 0;
  const today = summary.today_count || 0;
  const upcoming = summary.upcoming_count || 0;
  countsEl.innerHTML = `
    <span class="ptodo-count-overdue">${overdue} Overdue</span>
    · Today: ${today}
    · Upcoming: ${upcoming}
  `;
  const preview = summary.preview || [];
  if (!preview.length) {
    previewEl.innerHTML = '<p class="subtitle">No open To-Dos.</p>';
    return;
  }
  previewEl.innerHTML = preview
    .map(
      (t) => `
      <div class="ptodo-dash-item${t.bucket === 'overdue' ? ' is-overdue' : ''}">
        <span>☐ ${ptodoEsc(t.task_title)}</span>
      </div>
    `,
    )
    .join('');
}

async function loadPersonalTodoWidgets() {
  if (!authState?.accessToken) return;
  try {
    const response = await fetchWithAuth('/api/v1/personal-todos/summary');
    const data = await response.json();
    if (!response.ok || !data.success) return;
    const summary = data.data || {};
    ptodoRenderWidget('ptodo-dash-counts', 'ptodo-dash-preview', summary);
    ptodoRenderWidget('ptodo-myday-counts', 'ptodo-myday-preview', summary);
  } catch (_) {
    /* ignore widget errors */
  }
  ptodoStartDueReminderPolling();
}

/** Phase 2 — in-app due reminder alerts (desktop/web). */
const ptodoDueAlerted = new Set();
let ptodoDuePollTimer = null;
let ptodoDueQueue = [];

function ptodoStartDueReminderPolling() {
  if (ptodoDuePollTimer) return;
  ptodoPollDueReminders();
  ptodoDuePollTimer = setInterval(ptodoPollDueReminders, 60_000);
}

async function ptodoPollDueReminders() {
  if (!authState?.accessToken) return;
  try {
    const response = await fetchWithAuth('/api/v1/personal-todos/due-reminders');
    const data = await response.json();
    if (!response.ok || !data.success) return;
    const todos = data.data?.todos || [];
    todos.forEach((t) => {
      if (!t.id || ptodoDueAlerted.has(t.id)) return;
      ptodoDueAlerted.add(t.id);
      ptodoDueQueue.push(t);
    });
    ptodoShowNextDueAlert();
  } catch (_) {
    /* ignore */
  }
}

function ptodoShowNextDueAlert() {
  const modal = document.getElementById('ptodo-due-modal');
  if (!modal || !modal.classList.contains('hidden')) return;
  const todo = ptodoDueQueue.shift();
  if (!todo) return;
  document.getElementById('ptodo-due-title').textContent = todo.task_title || 'To-Do Reminder';
  document.getElementById('ptodo-due-meta').textContent = [
    todo.category,
    todo.person_party,
    todo.due_date,
  ]
    .filter(Boolean)
    .join(' · ');
  modal.dataset.todoId = String(todo.id);
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
}

function ptodoCloseDueModal() {
  const modal = document.getElementById('ptodo-due-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  delete modal.dataset.todoId;
  setTimeout(ptodoShowNextDueAlert, 200);
}

async function ptodoDueDone() {
  const id = document.getElementById('ptodo-due-modal')?.dataset?.todoId;
  if (!id) return ptodoCloseDueModal();
  try {
    await fetchWithAuth(`/api/v1/personal-todos/${id}/done`, { method: 'POST' });
    if (currentModuleKey === 'todo') loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (_) {
    /* ignore */
  }
  ptodoCloseDueModal();
}

async function ptodoDueSnooze(preset) {
  const id = document.getElementById('ptodo-due-modal')?.dataset?.todoId;
  if (!id) return ptodoCloseDueModal();
  try {
    await fetchWithAuth(`/api/v1/personal-todos/${id}/snooze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset }),
    });
    ptodoDueAlerted.delete(Number(id));
    if (currentModuleKey === 'todo') loadPersonalTodoWorkspace();
    loadPersonalTodoWidgets();
  } catch (error) {
    alert(error.message || 'Unable to snooze');
  }
  ptodoCloseDueModal();
}

function ptodoDueOpen() {
  ptodoCloseDueModal();
  openModule('ToDo');
}

document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (authState?.accessToken) ptodoStartDueReminderPolling();
  }, 2500);
});

