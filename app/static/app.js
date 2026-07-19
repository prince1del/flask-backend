const authState = {
  accessToken: null,
  refreshToken: null,
  username: null,
  role: null,
  workspaceId: null,
  userId: null,
};

/** True while redirecting to login after idle timeout — suppresses red API errors. */
let sessionTimeoutInProgress = false;
let refreshTokenPromise = null;

function isSessionTimeoutError(error) {
  return Boolean(error && (error.isSessionTimeout || error.message === 'SESSION_TIMEOUT'));
}

function makeSessionTimeoutError() {
  const err = new Error('SESSION_TIMEOUT');
  err.isSessionTimeout = true;
  err.name = 'SessionTimeoutError';
  return err;
}

/* ===== NEXORA themed notifications (replaces white browser alert/confirm) ===== */
const NX_NOTIFY = {
  toastTimer: null,
  confirmResolver: null,
};
const _nativeAlert = typeof window.alert === 'function' ? window.alert.bind(window) : null;
const _nativeConfirm = typeof window.confirm === 'function' ? window.confirm.bind(window) : null;

function nxEnsureNotifyDom() {
  if (!document.getElementById('nx-toast-stack')) {
    const stack = document.createElement('div');
    stack.id = 'nx-toast-stack';
    stack.className = 'nx-toast-stack';
    stack.setAttribute('aria-live', 'polite');
    document.body.appendChild(stack);
  }
  if (!document.getElementById('nx-confirm-modal')) {
    const wrap = document.createElement('div');
    wrap.id = 'nx-confirm-modal';
    wrap.className = 'nx-confirm-modal hidden';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.innerHTML = `
      <div class="nx-confirm-backdrop" data-nx-confirm-cancel></div>
      <div class="nx-confirm-card">
        <div class="nx-confirm-icon" id="nx-confirm-icon" aria-hidden="true">?</div>
        <h3 id="nx-confirm-title">Confirm</h3>
        <p id="nx-confirm-message"></p>
        <div class="nx-confirm-actions">
          <button type="button" class="btn btn-secondary" id="nx-confirm-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="nx-confirm-ok">OK</button>
        </div>
      </div>`;
    document.body.appendChild(wrap);
  }
}

function nxInferToastType(message) {
  const lower = String(message || '').toLowerCase();
  if (/error|fail|unable|invalid|required|could not|denied|blocked/.test(lower)) return 'error';
  if (/success|saved|linked|synced|added|created|deleted|connected|installed|updated/.test(lower)) {
    return 'success';
  }
  if (/warning|already|postpone|select|choose|please|confirm/.test(lower)) return 'warn';
  return 'info';
}

function nexoraToast(message, type, options = {}) {
  nxEnsureNotifyDom();
  const stack = document.getElementById('nx-toast-stack');
  if (!stack) return;
  const kind = type || nxInferToastType(message);
  const toast = document.createElement('div');
  toast.className = `nx-toast nx-toast-${kind}`;
  toast.setAttribute('role', 'status');
  const icons = { success: '✓', error: '!', warn: '⚠', info: 'i' };
  toast.innerHTML = `
    <span class="nx-toast-icon" aria-hidden="true">${icons[kind] || 'i'}</span>
    <div class="nx-toast-body">${String(message ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>')}</div>
    <button type="button" class="nx-toast-close" aria-label="Dismiss">×</button>`;
  const remove = () => {
    toast.classList.add('is-leaving');
    setTimeout(() => toast.remove(), 220);
  };
  toast.querySelector('.nx-toast-close')?.addEventListener('click', remove);
  stack.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('is-visible'));
  const ttl = options.duration ?? (kind === 'error' ? 6500 : 4200);
  setTimeout(remove, ttl);
}

function nexoraAlert(message, type) {
  nexoraToast(message, type || nxInferToastType(message));
}

function nexoraConfirm(message, options = {}) {
  nxEnsureNotifyDom();
  const modal = document.getElementById('nx-confirm-modal');
  const msgEl = document.getElementById('nx-confirm-message');
  const titleEl = document.getElementById('nx-confirm-title');
  const iconEl = document.getElementById('nx-confirm-icon');
  const okBtn = document.getElementById('nx-confirm-ok');
  const cancelBtn = document.getElementById('nx-confirm-cancel');
  if (!modal || !msgEl || !okBtn || !cancelBtn) {
    return Promise.resolve(_nativeConfirm ? _nativeConfirm(message) : false);
  }

  if (typeof NX_NOTIFY.confirmResolver === 'function') {
    NX_NOTIFY.confirmResolver(false);
    NX_NOTIFY.confirmResolver = null;
  }

  titleEl.textContent = options.title || 'Please confirm';
  msgEl.textContent = String(message ?? '');
  if (iconEl) {
    iconEl.textContent = options.danger ? '!' : '?';
    iconEl.classList.toggle('is-danger', Boolean(options.danger));
  }
  okBtn.textContent = options.okText || 'OK';
  cancelBtn.textContent = options.cancelText || 'Cancel';
  okBtn.classList.toggle('btn-danger', Boolean(options.danger));
  okBtn.classList.toggle('btn-primary', !options.danger);

  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  setTimeout(() => okBtn.focus(), 30);

  return new Promise((resolve) => {
    const finish = (value) => {
      modal.classList.add('hidden');
      modal.setAttribute('aria-hidden', 'true');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      modal.querySelectorAll('[data-nx-confirm-cancel]').forEach((el) => {
        el.removeEventListener('click', onCancel);
      });
      document.removeEventListener('keydown', onKey);
      NX_NOTIFY.confirmResolver = null;
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onKey = (event) => {
      if (event.key === 'Escape') finish(false);
      if (event.key === 'Enter') finish(true);
    };
    NX_NOTIFY.confirmResolver = finish;
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    modal.querySelectorAll('[data-nx-confirm-cancel]').forEach((el) => {
      el.addEventListener('click', onCancel);
    });
    document.addEventListener('keydown', onKey);
  });
}

// Replace native white browser dialogs app-wide.
window.alert = function nxAlertOverride(message) {
  nexoraAlert(message);
};
window.confirm = function nxConfirmOverride(message) {
  // Never show the white browser dialog. Sync callers must migrate to await nexoraConfirm().
  console.warn('Blocked native confirm(); use await nexoraConfirm(...).', message);
  nexoraToast(String(message || 'Please confirm this action from the updated dialog.'), 'warn');
  return false;
};

const partyMasterState = {
  distributors: [],
  retailers: [],
};

const PARTY_MASTER_PAGE_SIZE = 100;

const partyMasterTableState = {
  distributorRecords: [],
  retailerRecords: [],
  distributorPage: 1,
  retailerPage: 1,
  distributorsLoaded: false,
  retailersLoaded: false,
  distributorsLoading: false,
  retailersLoading: false,
};

let pendingUpdateMetadata = null;

const financeState = {
  accounts: [],
  gstReturns: [],
  vatReturns: [],
};

const appIconDefinitions = [
  { key: 'dashboard', icon: '📊', title: 'Dashboard', description: 'Open the analytics dashboard', action: 'openAnalyticsDashboard' },
  { key: 'fileLibrary', icon: '📁', title: 'File Library', description: 'Browse storage and documents', action: 'openFileLibrary' },
  { key: 'partyMaster', icon: '👥', title: 'Party Master', description: 'Manage distributors & retailers', action: 'openPartyMasterSection' },
  { key: 'orders', icon: '📦', title: 'Orders', description: 'Manage order workflows', action: 'openOrderManagement' },
  { key: 'invoices', icon: '🧾', title: 'Invoices', description: 'Create and review invoices', action: 'loadInvoices' },
  { key: 'settings', icon: '⚙️', title: 'Settings', description: 'Open app settings', action: 'openWorkspaceSettings' },
];

function saveAuthToken(token) {
  if (token) {
    localStorage.setItem('authAccessToken', token);
  } else {
    localStorage.removeItem('authAccessToken');
  }
}

function saveRefreshToken(token) {
  if (token) {
    localStorage.setItem('authRefreshToken', token);
  } else {
    localStorage.removeItem('authRefreshToken');
  }
}

function saveUsername(username) {
  if (username) {
    localStorage.setItem('authUsername', username);
  } else {
    localStorage.removeItem('authUsername');
  }
}

function saveRole(role) {
  if (role) {
    localStorage.setItem('authRole', role);
  } else {
    localStorage.removeItem('authRole');
  }
}

function saveWorkspaceId(workspaceId) {
  if (workspaceId) {
    localStorage.setItem('authWorkspaceId', workspaceId);
  } else {
    localStorage.removeItem('authWorkspaceId');
  }
}

function saveUserId(userId) {
  if (userId != null && userId !== '') {
    localStorage.setItem('authUserId', String(userId));
  } else {
    localStorage.removeItem('authUserId');
  }
}

// Sales Executives only see what's actually been built and tested so
// far in NEXORA — everything else (Purchase, Inventory, Finance,
// Reports, Analytics, Orders, Approvals, Cloud Hub, Banking, Settings,
// and the still-decorative Sales sub-tabs) is hidden from the UI
// only. Nothing on the backend is touched, so re-enabling any of
// these later is just uncommenting/removing an id from this list —
// no rebuilding required.
const SALES_EXECUTIVE_HIDDEN_NAV_IDS = [
  'nav-purchase', 'nav-inventory', 'nav-finance', 'nav-reports',
  'nav-analytics', 'nav-orders', 'nav-approvals',
  'nav-banking', 'nav-settings',
];
const SALES_EXECUTIVE_HIDDEN_SALES_SUBTAB_IDS = [
  'sales-tab-overview', 'sales-tab-invoices', 'sales-tab-orders', 'sales-tab-reports',
];
const SALES_EXECUTIVE_HIDDEN_ELEMENT_IDS = [
  'sales-fake-metrics',
  'dashboard-fake-overview-cards',
  'dashboard-fake-ai-summary',
  'dashboard-fake-health',
  'dashboard-fake-activities',
  'dashboard-fake-priorities',
  'dashboard-fake-rightbar',
];

function goToHomePage() {
  moduleHistoryStack.length = 0;
  suppressModuleHistoryPush = true;
  // House of Prizm — isolated home (never opens NEXORA executive My Day)
  if (authState.role === 'hop_admin') {
    openModule('HopExecutive');
    suppressModuleHistoryPush = false;
    return;
  }
  openModule('Dashboard');
  suppressModuleHistoryPush = false;
  if (authState.role === 'sales_executive') {
    loadTaFyOverviewCard();
    initDashboardTaWidgetDrag();
    initDashboardFoWidgetsDrag();
    loadFilledOrdersSeasonWidgets();
  }
}

function applyHopRoleUI() {
  // Ask NEXORA stays branded the same — answers are workspace-isolated server-side
  document.body.classList.remove('bd-hop-ui');
  document.getElementById('ask-nexora-btn')?.classList.remove('hidden');
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('executive-home-workspace')?.classList.add('hidden');
  document.getElementById('global-search-shell')?.classList.add('hidden');
  document.getElementById('global-search-trigger')?.classList.add('hidden');
  if (authState.accessToken) {
    goToHomePage();
  }
}

function applyRoleBasedUI() {
  if (authState.role === 'hop_admin') {
    applyHopRoleUI();
    return;
  }
  if (authState.role !== 'sales_executive') {
    document.body.classList.remove('bd-hop-ui');
    return;
  }
  document.body.classList.add('bd-hop-ui');
  SALES_EXECUTIVE_HIDDEN_NAV_IDS.forEach((id) => {
    document.getElementById(id)?.classList.add('hidden');
  });
  document.querySelectorAll('#dashboard .hop-nav-group').forEach((group) => {
    let el = group.nextElementSibling;
    let anyVisible = false;
    while (el && !el.classList.contains('hop-nav-group')) {
      if (el.matches?.('.nav-item') && !el.classList.contains('hidden')) {
        anyVisible = true;
        break;
      }
      el = el.nextElementSibling;
    }
    group.classList.toggle('hidden', !anyVisible);
  });
  SALES_EXECUTIVE_HIDDEN_SALES_SUBTAB_IDS.forEach((id) => {
    document.getElementById(id)?.classList.add('hidden');
  });
  SALES_EXECUTIVE_HIDDEN_ELEMENT_IDS.forEach((id) => {
    document.getElementById(id)?.classList.add('hidden');
  });
  document.getElementById('dashboard-honest-placeholder')?.classList.add('hidden');
  document.getElementById('nav-my-day')?.classList.add('hidden');
  document.querySelector('#dashboard .content-inner')?.classList.add('dashboard-ta-focus');
  document.getElementById('dashboard-ta-playing-card')?.classList.remove('hidden');
  document.getElementById('dashboard-fo-widgets-layer')?.classList.remove('hidden');
  if (authState.accessToken) {
    goToHomePage();
  }
}

function loadAuthState() {
  authState.accessToken = localStorage.getItem('authAccessToken');
  authState.refreshToken = localStorage.getItem('authRefreshToken');
  authState.username = localStorage.getItem('authUsername');
  authState.role = localStorage.getItem('authRole');
  authState.workspaceId = localStorage.getItem('authWorkspaceId');
  const storedUid = localStorage.getItem('authUserId');
  authState.userId = storedUid ? Number(storedUid) : null;
  const userInfoEl = document.getElementById('user-info') || document.getElementById('user-name');
  const askNexoraButton = document.getElementById('ask-nexora-btn');

  if (authState.accessToken) {
    document.getElementById('loginModal')?.classList.add('hidden');
    document.getElementById('dashboard')?.classList.remove('hidden');
    askNexoraButton?.classList.remove('hidden');
    resetGlobalSearchUi();
    applyRoleBasedUI();
    if (userInfoEl) {
      userInfoEl.textContent = authState.username || 'Admin User';
    }
  } else {
    askNexoraButton?.classList.add('hidden');
  }
}

function getStoredUpdateVersion(key) {
  return localStorage.getItem(key) || null;
}

function shouldShowUpdate(version, manual) {
  const deferredVersion = getStoredUpdateVersion('deferredUpdateVersion');
  const pendingVersion = getStoredUpdateVersion('pendingUpdateVersion');
  if (manual) {
    return true;
  }
  if (deferredVersion === version || pendingVersion === version) {
    return false;
  }
  return true;
}

function initApp() {
  resetGlobalSearchUi();
  initGlobalSearchUi();
  setGlobalSearchBarVisible(true);
  // Keep minimized dock on <body> so Customers / other pages never hide it with #dashboard.
  ensureWidgetDock();
  bindNexoraChatOverlayDismiss();
  loadAuthState();
  if (authState.accessToken && authState.role === 'hop_admin') {
    goToHomePage();
    return;
  }
  if (authState.accessToken && authState.role !== 'sales_executive') {
    goToHomePage();
  }
  updateGreeting();
  loadRecentActivities();
  loadDashboard();
  loadYears();
  loadAppIconTray();
  checkForUpdates();
  initDashboardTaWidgetDrag();
  initDashboardFoWidgetsDrag();
  window.addEventListener('resize', scheduleCustomersLayout);
}

const TA_WIDGET_POS_KEY = 'dashboardTaWidgetPosition';
let dashboardTaWidgetDragBound = false;

function getDashboardWidgetBoard() {
  // Prefer the full main panel — that is the true empty area for widgets
  return (
    document.querySelector('#dashboard .main-content:has(.content-inner.dashboard-ta-focus)') ||
    document.querySelector('#dashboard .content-inner.dashboard-ta-focus') ||
    null
  );
}

function getDashboardWidgetDragBounds(layer, widget) {
  const board = getDashboardWidgetBoard() || layer;
  const boardW = board?.clientWidth || layer?.clientWidth || 0;
  const boardH = board?.clientHeight || layer?.clientHeight || 0;
  const widgetW = widget?.offsetWidth || 0;
  const widgetH = widget?.offsetHeight || 0;
  // Allow widgets to reach every edge; keep a thin strip visible if oversized
  const minVisible = 40;
  return {
    maxX: Math.max(0, boardW - Math.min(widgetW, minVisible)),
    maxY: Math.max(0, boardH - Math.min(widgetH, minVisible)),
    boardW,
    boardH,
  };
}

function clampDashboardTaWidgetPosition(layer, widget) {
  if (!layer || !widget) return;
  const { maxX, maxY } = getDashboardWidgetDragBounds(layer, widget);
  let x = parseFloat(widget.style.left) || 0;
  let y = parseFloat(widget.style.top) || 0;
  x = Math.max(0, Math.min(x, maxX));
  y = Math.max(0, Math.min(y, maxY));
  widget.style.left = `${x}px`;
  widget.style.top = `${y}px`;
}

function centerDashboardTaWidget(layer, widget) {
  if (!layer || !widget) return;
  requestAnimationFrame(() => {
    const { boardW, boardH } = getDashboardWidgetDragBounds(layer, widget);
    const x = Math.max(0, (boardW - widget.offsetWidth) / 2);
    const y = Math.max(0, (boardH - widget.offsetHeight) / 2);
    widget.style.left = `${x}px`;
    widget.style.top = `${y}px`;
  });
}

function applyDashboardTaWidgetPosition() {
  const layer = document.getElementById('dashboard-ta-playing-card');
  const widget = document.getElementById('dashboard-ta-widget');
  if (!layer || !widget || layer.classList.contains('hidden')) return;

  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(TA_WIDGET_POS_KEY) || 'null');
  } catch (e) {
    saved = null;
  }

  if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
    widget.style.left = `${saved.x}px`;
    widget.style.top = `${saved.y}px`;
    clampDashboardTaWidgetPosition(layer, widget);
  } else {
    centerDashboardTaWidget(layer, widget);
  }
}

function initDashboardTaWidgetDrag() {
  if (dashboardTaWidgetDragBound) {
    applyDashboardTaWidgetPosition();
    return;
  }

  const layer = document.getElementById('dashboard-ta-playing-card');
  const widget = document.getElementById('dashboard-ta-widget');
  const handle = widget?.querySelector('.ta-widget-drag-handle');
  const header = widget?.querySelector('.ta-widget-drag-surface');
  if (!layer || !widget) return;

  dashboardTaWidgetDragBound = true;
  applyDashboardTaWidgetPosition();

  let dragging = false;
  let pointerId = null;
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  const onPointerDown = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    if (event.target.closest?.('.ta-widget-minimize-btn')) return;
    dragging = true;
    pointerId = event.pointerId;
    widget.classList.add('is-dragging');
    const board = getDashboardWidgetBoard() || layer;
    const boardRect = board.getBoundingClientRect();
    const widgetRect = widget.getBoundingClientRect();
    startX = event.clientX;
    startY = event.clientY;
    originLeft = widgetRect.left - boardRect.left;
    originTop = widgetRect.top - boardRect.top;
    widget.style.left = `${originLeft}px`;
    widget.style.top = `${originTop}px`;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };

  const onPointerMove = (event) => {
    if (!dragging || (pointerId !== null && event.pointerId !== pointerId)) return;
    const { maxX, maxY } = getDashboardWidgetDragBounds(layer, widget);
    let x = originLeft + (event.clientX - startX);
    let y = originTop + (event.clientY - startY);
    x = Math.max(0, Math.min(x, maxX));
    y = Math.max(0, Math.min(y, maxY));
    widget.style.left = `${x}px`;
    widget.style.top = `${y}px`;
  };

  const onPointerUp = (event) => {
    if (!dragging || (pointerId !== null && event.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
    widget.classList.remove('is-dragging');
    const x = parseFloat(widget.style.left) || 0;
    const y = parseFloat(widget.style.top) || 0;
    localStorage.setItem(TA_WIDGET_POS_KEY, JSON.stringify({ x, y }));
  };

  handle?.addEventListener('pointerdown', onPointerDown);
  header?.addEventListener('pointerdown', onPointerDown);
  // Listen on document so drag continues smoothly even if pointer leaves the widget
  document.addEventListener('pointermove', onPointerMove);
  document.addEventListener('pointerup', onPointerUp);
  document.addEventListener('pointercancel', onPointerUp);
  window.addEventListener('resize', () => clampDashboardTaWidgetPosition(layer, widget));
}

const FO_WIDGET_POS_PREFIX = 'dashboardFoWidgetPosition:';
let dashboardFoWidgetDragBound = false;
const foWidgetDragState = { active: null };
let foSeasonWidgetsLoadSeq = 0;

function foWidgetStorageKey(season) {
  return `${FO_WIDGET_POS_PREFIX}${season}`;
}

function foEscapeText(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function defaultFoWidgetPosition(index) {
  return { x: 16, y: 16 + index * 420 };
}

function applyFoSeasonWidgetPosition(layer, widget, storageKey, index) {
  if (!layer || !widget) return;
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
  } catch (e) {
    saved = null;
  }
  if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
    widget.style.left = `${saved.x}px`;
    widget.style.top = `${saved.y}px`;
    clampDashboardTaWidgetPosition(layer, widget);
  } else {
    const def = defaultFoWidgetPosition(Number.isFinite(index) ? index : 0);
    widget.style.left = `${def.x}px`;
    widget.style.top = `${def.y}px`;
  }
}

function initDashboardFoWidgetsDrag() {
  if (dashboardFoWidgetDragBound) return;
  dashboardFoWidgetDragBound = true;

  const onPointerDown = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    const layer = document.getElementById('dashboard-fo-widgets-layer');
    if (!layer || layer.classList.contains('hidden')) return;
    const widget = event.target.closest?.('.fo-season-widget');
    if (!widget || !layer.contains(widget)) return;
    const onHandle = event.target.closest?.('.ta-widget-drag-handle');
    const onHeader = event.target.closest?.('.ta-widget-drag-surface');
    if (event.target.closest?.('.ta-widget-minimize-btn')) return;
    if (!onHandle && !onHeader) return;

    const season = widget.dataset.foSeason || '';
    const board = getDashboardWidgetBoard() || layer;
    const boardRect = board.getBoundingClientRect();
    const widgetRect = widget.getBoundingClientRect();
    foWidgetDragState.active = {
      layer,
      widget,
      storageKey: foWidgetStorageKey(season),
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originLeft: widgetRect.left - boardRect.left,
      originTop: widgetRect.top - boardRect.top,
    };
    widget.classList.add('is-dragging');
    widget.style.left = `${foWidgetDragState.active.originLeft}px`;
    widget.style.top = `${foWidgetDragState.active.originTop}px`;
    widget.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  };

  const onPointerMove = (event) => {
    const drag = foWidgetDragState.active;
    if (!drag || event.pointerId !== drag.pointerId) return;
    const { layer, widget } = drag;
    const { maxX, maxY } = getDashboardWidgetDragBounds(layer, widget);
    let x = drag.originLeft + (event.clientX - drag.startX);
    let y = drag.originTop + (event.clientY - drag.startY);
    x = Math.max(0, Math.min(x, maxX));
    y = Math.max(0, Math.min(y, maxY));
    widget.style.left = `${x}px`;
    widget.style.top = `${y}px`;
  };

  const onPointerUp = (event) => {
    const drag = foWidgetDragState.active;
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.widget.classList.remove('is-dragging');
    const x = parseFloat(drag.widget.style.left) || 0;
    const y = parseFloat(drag.widget.style.top) || 0;
    localStorage.setItem(drag.storageKey, JSON.stringify({ x, y }));
    foWidgetDragState.active = null;
  };

  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('pointermove', onPointerMove);
  document.addEventListener('pointerup', onPointerUp);
  document.addEventListener('pointercancel', onPointerUp);
  window.addEventListener('resize', () => {
    const layer = document.getElementById('dashboard-fo-widgets-layer');
    if (!layer) return;
    layer.querySelectorAll('.fo-season-widget').forEach((widget) => {
      clampDashboardTaWidgetPosition(layer, widget);
    });
  });
}

function renderFoSeasonOverviewRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="3">No orders for this season yet.</td></tr>';
  }
  return rows
    .map(
      (row) => `
        <tr>
          <td title="${foEscapeText(row.distributor_name)}">${foEscapeText(row.distributor_name)}</td>
          <td>${formatFilledOrderQty(row.total_piece_qty)}</td>
          <td>${formatFilledOrderAmount(row.total_ex_mill_value)}</td>
        </tr>
      `,
    )
    .join('');
}

function buildFoSeasonWidgetCard(seasonData, index) {
  const season = seasonData.season || '—';
  const safeId = season.replace(/[^a-zA-Z0-9_-]/g, '_');
  const seasonBadge = (season.split(/\s+/)[0] || season).slice(0, 4);
  return `
    <article
      id="dashboard-fo-widget-${safeId}"
      class="fo-season-widget ta-playing-card-compact card-highlight ta-draggable-widget fo-playing-card-compact"
      data-fo-season="${foEscapeText(season)}"
      data-fo-widget-index="${index}"
    >
      <button type="button" class="ta-widget-minimize-btn" data-fo-season="${foEscapeText(season)}" onclick="minimizeFoWidget(this.dataset.foSeason)" aria-label="Minimize widget" title="Minimize to dock bar (bottom-right)">─</button>
      <button type="button" class="ta-widget-drag-handle" aria-label="Hold and drag to move widget" title="Hold and drag to move">⠿</button>
      <div class="ta-playing-card-inner">
        <div class="ta-playing-card-corner" aria-hidden="true">
          <span class="ta-playing-card-suit">📋</span>
          <span class="ta-playing-card-rank">${foEscapeText(seasonBadge)}</span>
        </div>
        <div class="ta-playing-card-header ta-widget-drag-surface">
          <h2>Orders · ${foEscapeText(season)}</h2>
          <p>Qty in pieces · Ex-mill ₹</p>
        </div>
        <div class="ta-playing-card-table-wrap ta-excel-sheet ta-widget-sheet">
          <table class="ta-fy-overview-table ta-excel-table ta-excel-table-widget">
            <thead>
              <tr>
                <th>Distributor</th>
                <th>Total Qty</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>${renderFoSeasonOverviewRows(seasonData.rows || [])}</tbody>
          </table>
        </div>
        <div class="ta-playing-card-footer fo-season-widget-footer">
          <div class="fo-season-widget-totals">
            <span>Total: ${formatFilledOrderQty(seasonData.total_piece_qty)} pcs</span>
            <span>${formatFilledOrderAmount(seasonData.total_ex_mill_value)}</span>
          </div>
          <button type="button" class="ta-widget-details-btn" onclick="openModule('FilledOrders')">Open details →</button>
        </div>
      </div>
    </article>
  `;
}

function buildFoSeasonOverviewFromOrders(orders) {
  const bySeason = {};
  (orders || []).forEach((order) => {
    const season = (order.season || '—').trim() || '—';
    if (!bySeason[season]) bySeason[season] = {};
    const key = order.distributor_id || order.distributor_name_raw || order.id;
    const dist = (filledOrdersState.distributors || []).find((d) => d.id === order.distributor_id);
    const name = dist
      ? getFilledOrderDistributorLabel(dist)
      : (order.distributor_name_raw || `Distributor #${order.distributor_id || '?'}`);
    if (!bySeason[season][key]) {
      bySeason[season][key] = {
        distributor_name: name,
        total_piece_qty: 0,
        total_ex_mill_value: 0,
      };
    }
    const row = bySeason[season][key];
    row.total_piece_qty += Number(order.total_piece_qty) || 0;
    row.total_ex_mill_value += Number(order.total_ex_mill_value) || 0;
  });
  return Object.keys(bySeason)
    .sort()
    .reverse()
    .map((season) => {
      const rows = Object.values(bySeason[season]).sort((a, b) =>
        (a.distributor_name || '').localeCompare(b.distributor_name || ''),
      );
      return {
        season,
        rows,
        total_piece_qty: rows.reduce((sum, row) => sum + row.total_piece_qty, 0),
        total_ex_mill_value: rows.reduce((sum, row) => sum + row.total_ex_mill_value, 0),
      };
    });
}

function renderFoSeasonWidgets(layer, seasons) {
  if (!seasons.length) {
    layer.innerHTML = '';
    return;
  }
  layer.innerHTML = seasons.map((seasonData, index) => buildFoSeasonWidgetCard(seasonData, index)).join('');
  seasons.forEach((seasonData, index) => {
    const safeId = (seasonData.season || '').replace(/[^a-zA-Z0-9_-]/g, '_');
    const widget = document.getElementById(`dashboard-fo-widget-${safeId}`);
    if (!widget) return;
    applyFoSeasonWidgetPosition(layer, widget, foWidgetStorageKey(seasonData.season), index);
  });
}

async function loadFilledOrdersSeasonWidgets() {
  const layer = document.getElementById('dashboard-fo-widgets-layer');
  if (!layer) return;
  const loadSeq = ++foSeasonWidgetsLoadSeq;

  if (!authState.accessToken || authState.role !== 'sales_executive') {
    layer.innerHTML = '';
    layer.classList.add('hidden');
    return;
  }

  if (!document.querySelector('#dashboard .content-inner.dashboard-ta-focus')) {
    layer.classList.add('hidden');
    return;
  }

  layer.classList.remove('hidden');

  try {
    let seasons = [];
    const overviewResp = await fetchWithAuth('/api/v1/filled-orders/season-overview');
    if (overviewResp.ok) {
      const data = await overviewResp.json();
      seasons = data.seasons || [];
    } else if (overviewResp.status === 404) {
      await loadFilledOrdersDistributors();
      const listResp = await fetchWithAuth('/api/v1/filled-orders/list');
      const listData = await parseApiResponse(listResp);
      if (!listResp.ok) {
        throw new Error(getApiErrorMessage(listData, 'Unable to load order widgets'));
      }
      seasons = buildFoSeasonOverviewFromOrders(listData.filled_orders || []);
    } else {
      const data = await parseApiResponse(overviewResp);
      throw new Error(getApiErrorMessage(data, 'Unable to load order widgets'));
    }
    if (loadSeq !== foSeasonWidgetsLoadSeq) return;
    renderFoSeasonWidgets(layer, seasons);
    initDashboardFoWidgetsDrag();
  } catch (error) {
    layer.innerHTML = `
      <article class="fo-season-widget ta-playing-card-compact fo-playing-card-compact ta-draggable-widget" style="left:16px;top:16px;pointer-events:auto">
        <div class="ta-playing-card-inner">
          <p style="padding:1rem;color:#f87171;font-size:0.75rem;">${foEscapeText(error.message || 'Unable to load order widgets.')}</p>
        </div>
      </article>
    `;
  }
}

async function login() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value.trim();
  const errorEl = document.getElementById('loginError');

  if (!username || !password) {
    errorEl.textContent = 'Username and password are required.';
    errorEl.classList.remove('login-timeout-msg');
    errorEl.classList.add('error');
    return;
  }

  try {
    const response = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ username, password }),
      credentials: 'same-origin',
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Login failed');
    }

    authState.accessToken = data.data.access_token;
    authState.refreshToken = data.data.refresh_token || null;
    authState.username = data.data.user.username || username;
    authState.role = data.data.user.role || null;
    authState.workspaceId = data.data.user.workspace_id || null;
    authState.userId = data.data.user.id != null ? Number(data.data.user.id) : null;
    sessionTimeoutInProgress = false;
    saveAuthToken(authState.accessToken);
    saveRefreshToken(authState.refreshToken);
    saveUsername(authState.username);
    saveRole(authState.role);
    saveWorkspaceId(authState.workspaceId);
    saveUserId(authState.userId);
    // Every login gets a clean Ask NEXORA session for this id only
    if (typeof resetNexoraChatForCurrentUser === 'function') {
      resetNexoraChatForCurrentUser(true);
    }
    applyRoleBasedUI();

    try {
      await fetch('/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({ username, password }).toString(),
        credentials: 'same-origin',
      });
    } catch (e) {
      console.warn('Session login failed:', e);
    }

    document.getElementById('loginModal')?.classList.add('hidden');
    document.getElementById('dashboard')?.classList.remove('hidden');
    document.getElementById('ask-nexora-btn')?.classList.remove('hidden');
    const userInfoEl = document.getElementById('user-info') || document.getElementById('user-name');
    if (userInfoEl) {
      userInfoEl.textContent = authState.username;
    }
    if (errorEl) {
      errorEl.textContent = '';
      errorEl.classList.remove('login-timeout-msg');
      errorEl.classList.add('error');
    }
    resetGlobalSearchUi();
    applyRoleBasedUI();
    if (authState.role === 'hop_admin') {
      goToHomePage();
      return;
    }
    if (authState.role !== 'sales_executive') {
      goToHomePage();
    }
    loadDashboard();
    loadYears();
  } catch (error) {
    errorEl.textContent = error.message || 'Invalid username or password.';
    errorEl.classList.remove('login-timeout-msg');
    errorEl.classList.add('error');
  }
}

async function tryRefreshAccessToken() {
  const refreshToken = authState.refreshToken || localStorage.getItem('authRefreshToken');
  if (!refreshToken) return false;
  if (refreshTokenPromise) return refreshTokenPromise;

  refreshTokenPromise = (async () => {
    try {
      const response = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
        credentials: 'same-origin',
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success || !data.data?.access_token) {
        return false;
      }
      authState.accessToken = data.data.access_token;
      saveAuthToken(authState.accessToken);
      if (data.data.refresh_token) {
        authState.refreshToken = data.data.refresh_token;
        saveRefreshToken(authState.refreshToken);
      }
      return true;
    } catch (e) {
      console.warn('Token refresh failed:', e);
      return false;
    } finally {
      refreshTokenPromise = null;
    }
  })();

  return refreshTokenPromise;
}

function clearAuthLocalState() {
  authState.accessToken = null;
  authState.refreshToken = null;
  authState.username = null;
  authState.role = null;
  authState.workspaceId = null;
  authState.userId = null;
  saveAuthToken(null);
  saveRefreshToken(null);
  saveUsername(null);
  saveRole(null);
  saveWorkspaceId(null);
  saveUserId(null);
}

async function logout(reason) {
  try {
    await fetch('/logout', {
      method: 'GET',
      credentials: 'same-origin',
    });
  } catch (e) {
    console.warn('Logout request failed:', e);
  }

  clearAuthLocalState();
  document.body.classList.remove('bd-hop-ui', 'customers-page-active', 'nexora-ask-open');
  if (typeof resetNexoraChatForCurrentUser === 'function') {
    resetNexoraChatForCurrentUser(true);
  }
  resetGlobalSearchUi();
  closeAllModals();
  const loginModal = document.getElementById('loginModal');
  const dashboard = document.getElementById('dashboard');
  const loginError = document.getElementById('loginError');
  const askNexoraButton = document.getElementById('ask-nexora-btn');
  if (loginModal) loginModal.classList.remove('hidden');
  if (dashboard) dashboard.classList.add('hidden');
  document.getElementById('hop-executive-workspace')?.classList.add('hidden');
  document.getElementById('global-search-shell')?.classList.remove('hidden');
  document.getElementById('global-search-trigger')?.classList.remove('hidden');
  if (askNexoraButton) askNexoraButton.classList.add('hidden');
  if (loginError) {
    if (reason === 'session-expired' || reason === 'timeout') {
      loginError.textContent = 'Timeout — kindly re-login';
      loginError.classList.remove('error');
      loginError.classList.add('login-timeout-msg');
    } else {
      loginError.textContent = '';
      loginError.classList.remove('login-timeout-msg');
      loginError.classList.add('error');
    }
  }
}

/** Idle / expired session — calm login prompt, no red “Token expired” flash. */
async function handleSessionTimeout() {
  if (sessionTimeoutInProgress) return;
  sessionTimeoutInProgress = true;
  try {
    await logout('timeout');
    if (typeof nexoraToast === 'function') {
      nexoraToast('Timeout — kindly re-login', 'warn', { duration: 4500 });
    }
  } finally {
    // keep flag until next successful login
  }
}

async function fetchWithAuth(url, options = {}, alreadyRetried = false) {
  const headers = options.headers ? { ...options.headers } : {};
  if (authState.accessToken) {
    headers.Authorization = `Bearer ${authState.accessToken}`;
  }
  const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });

  if (response.status === 401 && !alreadyRetried) {
    const refreshed = await tryRefreshAccessToken();
    if (refreshed && authState.accessToken) {
      return fetchWithAuth(url, options, true);
    }
  }

  if (response.status === 401) {
    await handleSessionTimeout();
    throw makeSessionTimeoutError();
  }
  return response;
}

function loadDashboard() {
  updateStorageStatus();
  loadTaFyOverviewCard();
  if (authState.role === 'sales_executive') {
    loadFilledOrdersSeasonWidgets();
  }
}

function formatTaTableAmount(value) {
  const num = Number(value || 0);
  return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatTaPercent(value) {
  const num = Number(value || 0);
  return `${num.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
}

function taPercentClass(pct) {
  const num = Number(pct || 0);
  if (num >= 100) return '';
  if (num >= 70) return 'ta-pct-mid';
  return 'ta-pct-low';
}

function renderTaFyOverviewRows(rows) {
  if (!rows.length) {
    return '<tr><td colspan="4">No fiscal years yet — add targets under Target vs Achievement.</td></tr>';
  }
  return rows
    .map(
      (row) => `
        <tr>
          <td>${row.fy || '—'}</td>
          <td>${formatTaTableAmount(row.target)}</td>
          <td>${formatTaTableAmount(row.achievement)}</td>
          <td class="${taPercentClass(row.percentage)}">${formatTaPercent(row.percentage)}</td>
        </tr>
      `,
    )
    .join('');
}

async function loadTaFyOverviewCard() {
  const overviewTbodies = [
    document.getElementById('dashboard-ta-overview-tbody'),
    document.getElementById('ta-ws-overview-tbody'),
  ].filter(Boolean);
  const widgetLayer = document.getElementById('dashboard-ta-playing-card');
  if (!overviewTbodies.length) return;

  if (!authState.accessToken) {
    const empty = '<tr><td colspan="4">Log in to view target vs achievement.</td></tr>';
    overviewTbodies.forEach((tbody) => {
      tbody.innerHTML = empty;
    });
    return;
  }

  overviewTbodies.forEach((tbody) => {
    tbody.innerHTML = '<tr><td colspan="4">Loading…</td></tr>';
  });

  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/fy-overview');
    const data = await parseApiJson(response, 'Unable to load overview');
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to load overview');
    }
    const rows = sortFiscalYearsAscending(
      (data.data?.rows || []).map((row) => ({ ...row, year: row.fy, display_year: row.fy })),
    );
    const html = renderTaFyOverviewRows(rows);
    overviewTbodies.forEach((tbody) => {
      tbody.innerHTML = html;
    });
    if (widgetLayer && document.querySelector('#dashboard .content-inner.dashboard-ta-focus')) {
      widgetLayer.classList.remove('hidden');
      requestAnimationFrame(() => applyDashboardTaWidgetPosition());
    }
  } catch (error) {
    if (isSessionTimeoutError(error)) return;
    const errHtml = `<tr><td colspan="4">${error.message || 'Unable to load overview.'}</td></tr>`;
    overviewTbodies.forEach((tbody) => {
      tbody.innerHTML = errHtml;
    });
  }
}

async function parseApiJson(response, defaultMessage = 'Request failed') {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    if (response.status === 404) {
      throw new Error(
        'Server API not found. Restart Flask (.venv\\Scripts\\python.exe _run_server_5000.py) then hard-refresh (Ctrl+Shift+R).',
      );
    }
    throw new Error(
      `Server returned an error page (${response.status}). Restart Flask and hard-refresh (Ctrl+Shift+R).`,
    );
  }
  return response.json();
}

function getApiErrorMessage(data, defaultMessage = 'An error occurred') {
  if (!data) {
    return defaultMessage;
  }
  if (typeof data === 'string') {
    return data;
  }
  if (data.error) {
    if (typeof data.error === 'string') {
      return data.error;
    }
    if (data.error.message) {
      return data.error.message;
    }
    if (data.error.code) {
      return `${data.error.code}: ${data.error.message || defaultMessage}`;
    }
  }
  if (data.message) {
    return data.message;
  }
  return defaultMessage;
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) {
    return 'N/A';
  }
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let value = Number(bytes);
  while (value >= 1024 && index < sizes.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(1)} ${sizes[index]}`;
}

async function updateStorageStatus() {
  const statusEl = document.getElementById('cloud-hub-status') || document.getElementById('storage-status');
  const infoEl = document.getElementById('cloud-hub-info') || document.getElementById('storage-info');
  const connectBtn = document.getElementById('cloud-hub-connect-btn');
  const syncBtn = document.getElementById('cloud-hub-sync-btn');
  const disconnectBtn = document.getElementById('cloud-hub-disconnect-btn');
  const meterFill = document.getElementById('cloud-hub-meter-fill');

  if (statusEl) {
    statusEl.textContent = 'Checking storage status...';
  }
  if (infoEl) {
    infoEl.textContent = 'Loading storage details...';
  }
  if (meterFill) meterFill.style.width = '0%';

  if (!authState.accessToken) {
    if (statusEl) {
      statusEl.textContent = 'Storage features require login.';
    }
    if (infoEl) {
      infoEl.textContent = 'Connect Google Drive after login to see storage information.';
    }
    if (connectBtn) connectBtn.disabled = true;
    if (syncBtn) syncBtn.disabled = true;
    if (disconnectBtn) disconnectBtn.disabled = true;
    return;
  }

  try {
    const accountResponse = await fetchWithAuth('/api/v1/storage/account');
    const accountData = await accountResponse.json();
    if (!accountResponse.ok || !accountData.success) {
      throw new Error(accountData.error || 'Unable to load storage account');
    }

    if (!accountData.data.connected) {
      if (statusEl) {
        statusEl.textContent = 'Google Drive not connected';
      }
      if (infoEl) {
        infoEl.textContent = 'Connect your Google Drive to upload and browse files from Cloud Hub.';
      }
      if (connectBtn) {
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect Google Drive';
      }
      if (syncBtn) syncBtn.disabled = true;
      if (disconnectBtn) disconnectBtn.disabled = true;
      return false;
    }

    const dashboardResponse = await fetchWithAuth('/api/v1/storage/dashboard');
    const dashboardData = await dashboardResponse.json();
    if (!dashboardResponse.ok || !dashboardData.success) {
      throw new Error(dashboardData.error || 'Unable to load storage dashboard');
    }

    if (statusEl) {
      statusEl.textContent = 'Google Drive connected';
    }
    if (infoEl) {
      const stats = dashboardData.data.storage_info || {};
      const fileCount = stats.file_count ?? 0;
      const totalSize = stats.total_size ?? 0;
      const quota = stats.quota ?? 0;
      infoEl.textContent = `Files: ${fileCount} • Used: ${formatBytes(totalSize)} / ${formatBytes(quota)}`;
      if (meterFill && quota > 0) {
        const pct = Math.max(2, Math.min(100, Math.round((Number(totalSize) / Number(quota)) * 100)));
        meterFill.style.width = `${pct}%`;
      } else if (meterFill) {
        meterFill.style.width = fileCount > 0 ? '8%' : '0%';
      }
    }
    if (connectBtn) {
      connectBtn.disabled = true;
      connectBtn.textContent = 'Connected';
    }
    if (syncBtn) syncBtn.disabled = false;
    if (disconnectBtn) disconnectBtn.disabled = false;
    return true;
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = 'Storage status unavailable';
    }
    if (infoEl) {
      infoEl.textContent = error.message || 'Unable to fetch storage information.';
    }
    if (connectBtn) connectBtn.disabled = false;
    if (syncBtn) syncBtn.disabled = true;
    if (disconnectBtn) disconnectBtn.disabled = true;
    return false;
  }
}

function cloudHubFileKind(file) {
  const mime = String(file.mime_type || file.file_type || '').toLowerCase();
  const name = String(file.file_name || file.name || '').toLowerCase();
  if (mime.includes('folder') || name.endsWith('/')) return { label: 'Folder', icon: '📁', cls: 'is-folder' };
  if (mime.includes('spreadsheet') || mime.includes('excel') || /\.(xlsx?|csv)$/.test(name)) {
    return { label: 'Sheet', icon: '📊', cls: 'is-sheet' };
  }
  if (mime.includes('document') || mime.includes('word') || /\.(docx?|txt|rtf)$/.test(name)) {
    return { label: 'Doc', icon: '📄', cls: 'is-doc' };
  }
  if (mime.includes('presentation') || mime.includes('powerpoint') || /\.pptx?$/.test(name)) {
    return { label: 'Slides', icon: '📑', cls: 'is-slides' };
  }
  if (mime.startsWith('image/') || /\.(jpe?g|png|gif|webp|heic)$/.test(name)) {
    return { label: 'Image', icon: '🖼', cls: 'is-image' };
  }
  if (mime.startsWith('audio/') || /\.(mp3|m4a|wav|aac)$/.test(name)) {
    return { label: 'Audio', icon: '🎵', cls: 'is-audio' };
  }
  if (mime.startsWith('video/') || /\.(mp4|mov|mkv)$/.test(name)) {
    return { label: 'Video', icon: '🎬', cls: 'is-video' };
  }
  if (mime.includes('pdf') || name.endsWith('.pdf')) return { label: 'PDF', icon: '📕', cls: 'is-pdf' };
  if (name.endsWith('.apk')) return { label: 'App', icon: '📦', cls: 'is-app' };
  return { label: 'File', icon: '📎', cls: 'is-file' };
}

function formatCloudHubDate(value) {
  if (!value) return '—';
  const raw = String(value);
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw.slice(0, 16);
  return d.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function openCloudHubFile(fileId, fileName) {
  if (!fileId) return;
  const url = `https://drive.google.com/open?id=${encodeURIComponent(fileId)}`;
  const win = window.open(url, '_blank', 'noopener,noreferrer');
  if (!win) {
    const statusEl = document.getElementById('cloud-hub-status');
    if (statusEl) {
      statusEl.textContent = `Popup blocked — open Drive for: ${fileName || fileId}`;
    }
  }
}

let cloudHubFilesCache = [];

function filterCloudHubFiles() {
  const input = document.getElementById('cloud-hub-search-input');
  const q = String(input?.value || '').trim().toLowerCase();
  renderCloudHubFiles(
    q
      ? cloudHubFilesCache.filter((f) =>
          String(f.file_name || f.name || '')
            .toLowerCase()
            .includes(q)
        )
      : cloudHubFilesCache
  );
}

function renderCloudHubFiles(files) {
  const tbody = document.getElementById('cloud-hub-files-tbody');
  const countEl = document.getElementById('cloud-hub-file-count');
  if (!tbody) return;
  if (!files.length) {
    tbody.innerHTML =
      '<tr class="cloud-hub-empty-row"><td colspan="4">No matching files. Try Sync or clear search.</td></tr>';
    if (countEl) countEl.textContent = '0 files';
    return;
  }
  if (countEl) {
    countEl.textContent = `${files.length} file${files.length === 1 ? '' : 's'} · click to open in Drive`;
  }
  tbody.innerHTML = files
    .map((file) => {
      const name = foEscapeText(file.file_name || file.name || file.title || '—');
      const fileId = foEscapeText(file.file_id || file.id || '');
      const size = formatBytes(file.file_size_bytes ?? file.file_size ?? 0);
      const updated = foEscapeText(formatCloudHubDate(file.modified_at || file.updated_at || file.last_synced || file.created_at));
      const kind = cloudHubFileKind(file);
      return `<tr class="cloud-hub-file-row" tabindex="0" role="link" data-file-id="${fileId}" data-file-name="${name}" title="Open in Google Drive">
        <td><span class="cloud-hub-file-name"><span class="cloud-hub-file-icon ${kind.cls}" aria-hidden="true">${kind.icon}</span><span>${name}</span></span></td>
        <td><span class="cloud-hub-type-pill ${kind.cls}">${kind.label}</span></td>
        <td>${size}</td>
        <td>${updated}</td>
      </tr>`;
    })
    .join('');
}

function bindCloudHubFileClicks() {
  const tbody = document.getElementById('cloud-hub-files-tbody');
  if (!tbody || tbody.dataset.bound === '1') return;
  tbody.dataset.bound = '1';
  tbody.addEventListener('click', (event) => {
    const row = event.target.closest('tr.cloud-hub-file-row');
    if (!row) return;
    openCloudHubFile(row.dataset.fileId, row.dataset.fileName);
  });
  tbody.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const row = event.target.closest('tr.cloud-hub-file-row');
    if (!row) return;
    event.preventDefault();
    openCloudHubFile(row.dataset.fileId, row.dataset.fileName);
  });
}

async function loadCloudHubFiles() {
  const tbody = document.getElementById('cloud-hub-files-tbody');
  if (!tbody) return;
  bindCloudHubFileClicks();
  try {
    const response = await fetchWithAuth('/api/v1/storage/files');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Unable to load files');
    }
    cloudHubFilesCache = data.data?.files || [];
    const search = document.getElementById('cloud-hub-search-input');
    if (search) search.value = '';
    if (!cloudHubFilesCache.length) {
      tbody.innerHTML =
        '<tr class="cloud-hub-empty-row"><td colspan="4">No indexed files yet. Click Sync after connecting Drive.</td></tr>';
      const countEl = document.getElementById('cloud-hub-file-count');
      if (countEl) countEl.textContent = '0 files indexed';
      return;
    }
    renderCloudHubFiles(cloudHubFilesCache);
  } catch (error) {
    tbody.innerHTML = `<tr class="cloud-hub-empty-row"><td colspan="4">${foEscapeText(error.message || 'Unable to load files')}</td></tr>`;
  }
}

async function loadCloudHubWorkspace() {
  const connected = await updateStorageStatus();
  const tbody = document.getElementById('cloud-hub-files-tbody');
  if (!connected) {
    cloudHubFilesCache = [];
    if (tbody) {
      tbody.innerHTML =
        '<tr class="cloud-hub-empty-row"><td colspan="4">Connect Google Drive to see files.</td></tr>';
    }
    const countEl = document.getElementById('cloud-hub-file-count');
    if (countEl) countEl.textContent = 'Not connected';
    return;
  }
  await loadCloudHubFiles();
}

async function disconnectGoogleDrive() {
  const statusEl = document.getElementById('cloud-hub-status') || document.getElementById('storage-status');
  if (!authState.accessToken) {
    if (statusEl) statusEl.textContent = 'Please login first.';
    return;
  }
  if (!(await nexoraConfirm('Disconnect Google Drive from Cloud Hub?', { title: 'Disconnect Drive', danger: true, okText: 'Disconnect' }))) return;
  try {
    const response = await fetchWithAuth('/api/v1/storage/disconnect', { method: 'POST' });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Disconnect failed');
    }
    await loadCloudHubWorkspace();
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = error.message || 'Unable to disconnect Google Drive.';
    }
  }
}

function openFileLibrary() {
  openModule('CloudHub');
}

function showUploadModal() {
  toggleModal('uploadModal', true);
}

function showAddYearModal() {
  toggleModal('addYearModal', true);
}

async function showManualEntryModal() {
  await loadYears();
  toggleModal('manualEntryModal', true);
}

async function showReportUploadModal() {
  await loadYears();
  toggleModal('reportUploadModal', true);
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.add('hidden');
  }
}

function closeAllModals() {
  document.querySelectorAll('.modal').forEach((modal) => {
    modal.classList.add('hidden');
  });
}

function closeScan() {
  closeModal('scanModal');
}

function toggleModal(id, show) {
  const modal = document.getElementById(id);
  if (!modal) return;
  modal.classList.toggle('hidden', !show);
}

function populateYearSelects(years = []) {
  const allYears = years;
  const yearSelect = document.getElementById('yearSelect');
  const reportYear = document.getElementById('reportYear');

  if (!yearSelect || !reportYear) return;

  yearSelect.innerHTML = '';
  reportYear.innerHTML = '';

  if (!allYears.length) {
    allYears.push({ id: 1, year: '2024-2025' }, { id: 2, year: '2025-2026' });
  }

  allYears.forEach((year) => {
    const label = getFiscalYearDisplayLabel(year);
    const option1 = document.createElement('option');
    option1.value = year.id;
    option1.textContent = label;
    yearSelect.appendChild(option1);

    const option2 = document.createElement('option');
    option2.value = year.id;
    option2.textContent = label;
    reportYear.appendChild(option2);
  });
}

async function loadYears() {
  const summary = document.getElementById('target-summary');
  const yearsList = document.getElementById('years-list');

  if (summary) {
    summary.innerHTML = '<p>Loading fiscal year summary...</p>';
  }
  if (yearsList) {
    yearsList.innerHTML = '<p>Loading configured years...</p>';
  }

  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years');
    const data = await response.json();
    if (response.ok && data.success) {
      const years = dedupeFiscalYearsForSelect(data.data.years || []);
      if (summary) {
        summary.innerHTML = years.length
          ? years
              .map(
                (year) =>
                  `<p><strong>${getFiscalYearDisplayLabel(year)}</strong>: target ${formatLakhs(year.target)}</p>`
              )
              .join('')
          : '<p>No fiscal years configured yet. Add one to begin tracking performance.</p>';
      }
      if (yearsList) {
        yearsList.innerHTML = years.length
          ? years
              .map(
                (year) =>
                  `<div class="list-item"><strong>${year.year || year.financial_year}</strong> — target ${formatLakhs(year.target)}</div>`
              )
              .join('')
          : '<div class="list-item">No configured years found.</div>';
      }
      populateYearSelects(years);
      populateYearSelectsForTa(years);
      return;
    }

    throw new Error(data.error || 'Unable to load fiscal years');
  } catch (error) {
    if (summary) {
      summary.innerHTML = '<p>Unable to load fiscal year information.</p>';
    }
    if (yearsList) {
      yearsList.innerHTML = '<div class="list-item">Unable to load configurations.</div>';
    }
    console.warn('Failed to load years:', error);
  }
}

async function connectGoogleDrive() {
  try {
    if (!authState.accessToken) {
      alert('Please login to connect Google Drive.');
      return;
    }

    const popup = window.open('about:blank', '_blank');
    if (!popup) {
      alert('Please disable your popup blocker and try again.');
      return;
    }

    const response = await fetchWithAuth('/api/v1/storage/connect', {
      method: 'POST',
    });
    const data = await response.json();

    if (!response.ok || !data.success) {
      popup.close();
      throw new Error(getApiErrorMessage(data, 'Failed to initiate Google Drive connection'));
    }

    if (data.data && data.data.oauth_url) {
      popup.location = data.data.oauth_url;
    } else {
      popup.close();
      throw new Error('Google Drive connection could not be started.');
    }
  } catch (error) {
    const statusEl = document.getElementById('cloud-hub-status') || document.getElementById('storage-status');
    if (statusEl) {
      statusEl.textContent = error.message || 'Unable to connect Google Drive.';
    } else {
      console.error(error);
    }
  }
}

window.addEventListener('message', (event) => {
  if (!event.data || typeof event.data !== 'object') {
    return;
  }
  if (event.data.type === 'google_drive_connected') {
    updateStorageStatus();
    loadCloudHubWorkspace();
  }
  if (event.data.type === 'google_drive_connection_failed') {
    const statusEl = document.getElementById('cloud-hub-status') || document.getElementById('storage-status');
    if (statusEl) {
      statusEl.textContent = 'Connect failed: ' + (event.data.message || 'Unknown error');
    }
  }
});

async function syncGoogleDrive() {
  const statusEl = document.getElementById('cloud-hub-status') || document.getElementById('storage-status');
  const infoEl = document.getElementById('cloud-hub-info') || document.getElementById('storage-info');
  const syncBtn = document.getElementById('cloud-hub-sync-btn');
  try {
    if (!authState.accessToken) {
      if (statusEl) statusEl.textContent = 'Please login to sync Google Drive.';
      return;
    }

    if (syncBtn) {
      syncBtn.disabled = true;
      syncBtn.textContent = 'Syncing…';
    }
    if (statusEl) statusEl.textContent = 'Syncing Google Drive…';

    const response = await fetchWithAuth('/api/v1/storage/sync', {
      method: 'POST',
    });
    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Sync failed');
    }

    const synced = data.data?.files_synced ?? 0;
    if (statusEl) {
      statusEl.textContent = `Google Drive connected · synced ${synced} file(s)`;
    }
    if (infoEl && data.data?.message) {
      infoEl.textContent = data.data.message;
    }
    await loadCloudHubWorkspace();
  } catch (error) {
    if (statusEl) {
      statusEl.textContent = error.message || 'Google Drive sync failed.';
    }
  } finally {
    if (syncBtn) {
      syncBtn.disabled = false;
      syncBtn.textContent = 'Sync files';
    }
  }
}

async function openJsonPage(title, url) {
  if (!authState.accessToken) {
    alert('Please login to access this resource.');
    return;
  }

  try {
    const response = await fetchWithAuth(url);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Unable to load content');
    }

    const win = window.open('', '_blank');
    if (!win) {
      alert('Unable to open new window. Please disable your popup blocker.');
      return;
    }
    win.document.write(`<!doctype html><html><head><title>${title}</title><style>body{font-family:system-ui,Arial;margin:1rem;}pre{white-space:pre-wrap;word-break:break-word;background:#f5f5f5;padding:1rem;border-radius:12px;}</style></head><body><h1>${title}</h1><pre>${JSON.stringify(data, null, 2)}</pre></body></html>`);
    win.document.close();
  } catch (error) {
    alert(error.message || 'Unable to load data.');
  }
}

function openPartyMasterSection() {
  openModule('Customers');
}

function scheduleCustomersLayout() {
  const section = document.getElementById('party-master-section');
  if (!section || section.classList.contains('hidden')) return;
  const wraps = section.querySelectorAll('.tab-panel:not(.hidden) .party-master-scroll-wrapper');
  const available = Math.max(480, window.innerHeight - 200);
  wraps.forEach((wrap) => {
    wrap.style.height = `${available}px`;
    wrap.style.maxHeight = `${available}px`;
    wrap.style.minHeight = '480px';
    wrap.style.overflowX = 'auto';
    wrap.style.overflowY = 'auto';
  });
}

function resizeCustomersTableArea() {
  scheduleCustomersLayout();
}

async function loadCustomersWorkspace() {
  openPartyMasterTab('distributor');
  await loadDistributors();
  await loadDistributorSelect();
  scheduleCustomersLayout();
}

function closePartyMasterSection() {
  openModule('Dashboard');
}

function openPartyMasterTab(tab) {
  const distributorTab = document.getElementById('distributor-tab-button');
  const retailerTab = document.getElementById('retailer-tab-button');
  const distributorPanel = document.getElementById('distributor-panel');
  const retailerPanel = document.getElementById('retailer-panel');

  if (tab === 'retailer') {
    distributorTab?.classList.remove('active');
    retailerTab?.classList.add('active');
    distributorPanel?.classList.add('hidden');
    retailerPanel?.classList.remove('hidden');
    if (!partyMasterTableState.retailersLoaded && !partyMasterTableState.retailersLoading) {
      loadRetailers();
    }
  } else {
    distributorTab?.classList.add('active');
    retailerTab?.classList.remove('active');
    distributorPanel?.classList.remove('hidden');
    retailerPanel?.classList.add('hidden');
  }
  scheduleCustomersLayout();
}

let partyDetailRecordsCache = [];

const PARTY_DETAIL_LABELS = {
  name: 'Name', firm_name: 'Firm Name', contactPerson: 'Contact Person',
  contact_person: 'Contact Person', distributor: 'Distributor',
  distributor_name: 'Distributor', gst: 'GST Number', gst_no: 'GST Number',
  gst_number: 'GST Number', territory: 'Territory', zone: 'Zone',
  region: 'Region / State', state: 'State', city: 'City', location: 'City',
  pincode: 'Pincode', pin_code: 'Pincode', address: 'Address',
  storeType: 'Store Type', category: 'Category', phone: 'Phone',
  phone_number: 'Phone', phone_number_2: 'Phone 2', email: 'Email',
  creditLimit: 'Credit Limit', credit_limit: 'Credit Limit',
};

function formatLakhs(value) {
  const num = Number(value || 0);
  return `${num.toLocaleString(undefined, { maximumFractionDigits: 2 })} L`;
}

function normalizeFiscalYearLabel(raw) {
  const text = String(raw || '').trim();
  if (!text) return '';
  const tokens = text.match(/\d{2,4}/g);
  if (!tokens || tokens.length < 2) return text;
  const expand = (token) => {
    const n = parseInt(token, 10);
    return n < 100 ? 2000 + n : n;
  };
  let start = expand(tokens[0]);
  let end = expand(tokens[1]);
  if (start > end) [start, end] = [end, start];
  return `${start}-${end}`;
}

function fiscalYearSortKey(label) {
  const normalized = normalizeFiscalYearLabel(label);
  const match = normalized.match(/^(\d{4})-(\d{4})$/);
  if (!match) return [0, 0];
  return [parseInt(match[1], 10), parseInt(match[2], 10)];
}

function sortFiscalYearsAscending(years) {
  return [...years].sort((a, b) => {
    const aKey = fiscalYearSortKey(a.display_year || a.year || a.financial_year);
    const bKey = fiscalYearSortKey(b.display_year || b.year || b.financial_year);
    if (aKey[0] !== bKey[0]) return aKey[0] - bKey[0];
    return aKey[1] - bKey[1];
  });
}

function getFiscalYearDisplayLabel(year) {
  return normalizeFiscalYearLabel(year.display_year || year.year || year.financial_year) || `FY ${year.id}`;
}

function dedupeFiscalYearsForSelect(years) {
  const byLabel = new Map();
  const rank = (year) => {
    const yid = Number(year.id) || 0;
    const raw = year.financial_year || year.year || year.display_year || '';
    const canonical = normalizeFiscalYearLabel(raw) === raw;
    const target = Number(year.target ?? year.target_amount ?? 0);
    const children = Number(year.breakup_count ?? 0);
    return [children, target, canonical ? 1 : 0, -yid];
  };
  const better = (a, b) => {
    const ra = rank(a);
    const rb = rank(b);
    for (let i = 0; i < ra.length; i += 1) {
      if (ra[i] !== rb[i]) return ra[i] > rb[i];
    }
    return false;
  };
  for (const year of years || []) {
    const label = getFiscalYearDisplayLabel(year);
    if (!label) continue;
    const prev = byLabel.get(label);
    if (!prev || better(year, prev)) {
      byLabel.set(label, { ...year, display_year: label });
    }
  }
  return sortFiscalYearsAscending([...byLabel.values()]);
}

function populateYearSelectsForTa(years) {
  const selects = [
    document.getElementById('ta-ws-year-select'),
    document.getElementById('dist-target-year-select'),
    document.getElementById('reportYear'),
    document.getElementById('yearSelect'),
    document.getElementById('fy-achievement-year-select'),
  ].filter(Boolean);
  if (!years) return;
  const sorted = dedupeFiscalYearsForSelect(years);
  selects.forEach((select) => {
    const current = select.value;
    select.innerHTML = sorted
      .map((year) => {
        const label = getFiscalYearDisplayLabel(year);
        return `<option value="${year.id}">${label}</option>`;
      })
      .join('');
    if (current) select.value = current;
    else if (sorted.length) select.value = String(sorted[sorted.length - 1].id);
  });
}

let taCategoryDetailAvailable = false;

function formatCategoryCell(value) {
  const num = Number(value || 0);
  if (!num) return '<span class="ta-cat-dash">—</span>';
  return num.toFixed(2);
}

function renderCategoryMatrixTable(matrix, fyLabel) {
  const thead = document.getElementById('ta-category-matrix-thead');
  const tbody = document.getElementById('ta-category-matrix-tbody');
  const title = document.getElementById('ta-category-modal-title');
  const subtitle = document.getElementById('ta-category-modal-subtitle');
  if (!thead || !tbody) return;

  const label = normalizeFiscalYearLabel(fyLabel) || fyLabel || 'Fiscal year';
  if (title) title.textContent = `Category achievement — ${label}`;
  if (subtitle) {
    subtitle.textContent = `Distributor × category breakdown in lakhs. Grand total: ${formatLakhs(matrix.grand_total)}.`;
  }

  const categories = matrix.categories || [];
  thead.innerHTML = `
    <tr>
      <th>Distributor</th>
      ${categories.map((cat) => `<th>${cat}</th>`).join('')}
      <th>Total</th>
    </tr>
  `;

  const rows = matrix.rows || [];
  tbody.innerHTML = rows
    .map(
      (row) => `
        <tr>
          <td>${row.label || row.distributor || '—'}</td>
          ${categories
            .map((cat) => `<td>${formatCategoryCell(row.values?.[cat])}</td>`)
            .join('')}
          <td class="ta-cat-total">${Number(row.total || 0).toFixed(2)}</td>
        </tr>
      `,
    )
    .join('');

  if (categories.length) {
    const totals = matrix.totals_by_category || {};
    tbody.innerHTML += `
      <tr>
        <td class="ta-cat-total">Category total</td>
        ${categories.map((cat) => `<td class="ta-cat-total">${formatCategoryCell(totals[cat])}</td>`).join('')}
        <td class="ta-cat-total">${Number(matrix.grand_total || 0).toFixed(2)}</td>
      </tr>
    `;
  }
}

async function openTaCategoryDetailModal(yearId) {
  const select = document.getElementById('ta-ws-year-select');
  const resolvedYearId = yearId || select?.value;
  if (!resolvedYearId) return;

  setFormInlineStatus('ta-category-modal-status', '');
  toggleModal('taCategoryDetailModal', true);
  const thead = document.getElementById('ta-category-matrix-thead');
  const tbody = document.getElementById('ta-category-matrix-tbody');
  if (thead) thead.innerHTML = '<tr><th>Loading…</th></tr>';
  if (tbody) tbody.innerHTML = '';

  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${resolvedYearId}/category-breakup`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'No category data for this year');
    }
    renderCategoryMatrixTable(data.data, data.data.fy_label);
  } catch (error) {
    setFormInlineStatus('ta-category-modal-status', error.message || 'Unable to load category detail.', 'error');
    if (thead) thead.innerHTML = '';
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="6">${error.message || 'No category data.'}</td></tr>`;
    }
  }
}

function updateTaWsCategoryUi(hasCategoryDetail, fyLabel, categoryMatrix) {
  taCategoryDetailAvailable = Boolean(hasCategoryDetail);
  const btn = document.getElementById('ta-ws-category-btn');
  const fyLabelEl = document.getElementById('ta-ws-fy-label');
  const totalsEl = document.getElementById('ta-ws-category-totals');
  const label = normalizeFiscalYearLabel(fyLabel) || fyLabel || 'Fiscal year';

  if (btn) {
    btn.classList.toggle('hidden', !taCategoryDetailAvailable);
  }
  if (fyLabelEl) {
    fyLabelEl.textContent = label;
    fyLabelEl.classList.toggle('executive-target-label-clickable', taCategoryDetailAvailable);
    fyLabelEl.title = taCategoryDetailAvailable
      ? 'Click for category-wise achievement (Bed Sheet, Towels, …)'
      : '';
    fyLabelEl.onclick = taCategoryDetailAvailable
      ? () => openTaCategoryDetailModal()
      : null;
  }
  if (totalsEl) {
    const cats = categoryMatrix?.categories || [];
    const catTotals = categoryMatrix?.totals_by_category || {};
    if (taCategoryDetailAvailable && cats.length) {
      totalsEl.classList.remove('hidden');
      totalsEl.innerHTML = cats
        .map((cat) => `<span class="ta-ws-cat-pill"><strong>${cat}</strong> ${formatLakhs(catTotals[cat] || 0)}</span>`)
        .join('');
    } else {
      totalsEl.classList.add('hidden');
      totalsEl.innerHTML = '';
    }
  }
}

function renderTaWsAchievementSummary(summary, hasCategoryDetail) {
  const summaryEl = document.getElementById('ta-ws-ach-summary');
  if (!summaryEl || !summary) return;
  const pct = Number(summary.percentage || 0);
  const categoryHint = hasCategoryDetail ? ' Category breakdown available.' : '';
  if (Number(summary.active_achievement || 0) > 0) {
    summaryEl.textContent = `Excel total ${formatLakhs(summary.achievement_excel_total)} · Active ${formatLakhs(summary.active_achievement)} of ${formatLakhs(summary.target_lakhs)} (${pct}%).${categoryHint}`;
  } else {
    summaryEl.textContent = `No achievement yet — upload sales Excel (category-wise pivot).${categoryHint}`;
  }
}

function updateTaCategoryDetailUi(hasCategoryDetail, fyLabel) {
  taCategoryDetailAvailable = Boolean(hasCategoryDetail);
  const btn = document.getElementById('executive-ta-category-btn');
  const targetLabel = document.getElementById('executive-target-label');
  const yearSelect = document.getElementById('executive-ta-year-select');

  if (btn) {
    btn.classList.toggle('hidden', !taCategoryDetailAvailable);
  }
  if (targetLabel) {
    const label = normalizeFiscalYearLabel(fyLabel) || fyLabel || 'Fiscal year';
    targetLabel.textContent = label;
    targetLabel.classList.toggle('executive-target-label-clickable', taCategoryDetailAvailable);
    targetLabel.title = taCategoryDetailAvailable
      ? 'Click to view category-wise achievement detail'
      : '';
    targetLabel.onclick = taCategoryDetailAvailable
      ? () => openTaCategoryDetailModal()
      : null;
  }
  if (yearSelect && !yearSelect.dataset.categoryDblBound) {
    yearSelect.dataset.categoryDblBound = '1';
    yearSelect.addEventListener('dblclick', () => {
      if (taCategoryDetailAvailable) openTaCategoryDetailModal();
    });
  }
}

function renderExecutiveAchievementSummary(summary, fyLabel, hasCategoryDetail) {
  const targetEl = document.getElementById('executive-ta-fy-target');
  const manualFy = document.getElementById('executive-ach-manual-fy');
  const excelEl = document.getElementById('executive-ach-excel');
  const ciEl = document.getElementById('executive-ach-ci');
  const noteEl = document.getElementById('executive-ach-active-note');
  const targetLabel = document.getElementById('executive-target-label');
  const targetBar = document.getElementById('executive-target-bar');
  const targetSummary = document.getElementById('executive-target-summary');

  if (!summary) return;
  const label = normalizeFiscalYearLabel(fyLabel) || fyLabel || 'Fiscal year';
  updateTaCategoryDetailUi(hasCategoryDetail, label);
  if (targetEl) targetEl.textContent = formatLakhs(summary.target_lakhs);
  if (manualFy) manualFy.textContent = formatLakhs(summary.achievement_manual_fy);
  if (excelEl) excelEl.textContent = formatLakhs(summary.achievement_excel_total);
  if (ciEl) ciEl.textContent = formatLakhs(summary.achievement_ci_total);

  const sourceLabels = {
    ci: 'CI totals (Order Fulfillment)',
    excel: 'Distributor Excel totals',
    manual_fy: 'FY manual entry',
    manual_distributor: 'Distributor manual entries',
    none: 'No achievement recorded yet',
  };
  const active = summary.active_source || 'none';
  const pct = Number(summary.percentage || 0);
  if (targetBar) targetBar.style.width = `${Math.min(100, pct)}%`;
  if (targetSummary) {
    targetSummary.textContent = summary.target_lakhs
      ? `${formatLakhs(summary.active_achievement)} of ${formatLakhs(summary.target_lakhs)} (${pct}%)`
      : 'Set an FY target first (lakhs), then add achievement via any channel below.';
  }
  if (noteEl) {
    const categoryHint = hasCategoryDetail
      ? ' Click the year label or Category detail for Bed Sheet / Towels breakdown.'
      : '';
    noteEl.textContent =
      active === 'none'
        ? `Achievement can be entered three ways: FY manual, distributor Excel upload, or CI uploads in Order Fulfillment.${categoryHint}`
        : `Progress bar uses: ${sourceLabels[active] || active}. All three channels are shown above for comparison.${categoryHint}`;
  }
  if (targetLabel && !hasCategoryDetail) {
    targetLabel.textContent = label;
  }
}

function refreshActiveTargetUi() {
  if (currentModuleKey === 'targetvsachievement') {
    loadTaTargetWorkspace();
  }
}

async function loadTaTargetWorkspace() {
  const tbody = document.getElementById('ta-ws-target-tbody');
  const achTbody = document.getElementById('ta-ws-ach-tbody');
  const yearSelect = document.getElementById('ta-ws-year-select');
  const fyTargetEl = document.getElementById('ta-ws-fy-target');
  const fyLabelEl = document.getElementById('ta-ws-fy-label');
  if (!tbody || !yearSelect) return;

  const yearId = yearSelect.value;
  if (!yearId) {
    tbody.innerHTML = '<tr><td colspan="2">Add a fiscal year first.</td></tr>';
    if (achTbody) achTbody.innerHTML = '<tr><td colspan="7">Select a fiscal year first.</td></tr>';
    if (fyTargetEl) fyTargetEl.textContent = '—';
    if (fyLabelEl) fyLabelEl.textContent = '—';
    updateTaWsCategoryUi(false, '', null);
    return;
  }

  tbody.innerHTML = '<tr><td colspan="2">Loading…</td></tr>';
  if (achTbody) achTbody.innerHTML = '<tr><td colspan="7">Loading…</td></tr>';
  try {
    const [yearsRes, targetsRes, breakupRes] = await Promise.all([
      fetchWithAuth('/api/v1/target-achievement/years'),
      fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/distributor-targets`),
      fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/breakup`),
    ]);
    const yearsData = await parseApiJson(yearsRes, 'Unable to load fiscal years');
    const targetsData = await parseApiJson(targetsRes, 'Unable to load distributor targets');
    const breakupData = await parseApiJson(breakupRes, 'Unable to load achievement');

    const years = dedupeFiscalYearsForSelect(yearsData.data?.years || []);
    const year = years.find((y) => String(y.id) === String(yearId));
    const label = year ? getFiscalYearDisplayLabel(year) : 'Fiscal year';
    const fyTarget = year ? (year.target ?? year.target_amount ?? 0) : 0;

    if (fyTargetEl) fyTargetEl.textContent = formatLakhs(fyTarget);
    if (fyLabelEl) fyLabelEl.textContent = label;

    if (!targetsRes.ok || !targetsData.success) {
      throw new Error(targetsData.error?.message || targetsData.error || 'Unable to load targets');
    }

    const rows = targetsData.data?.rows || [];
    if (!rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="2">No distributor targets yet — use Distributor target to add them.</td></tr>';
    } else {
      tbody.innerHTML = rows
        .map(
          (r) => `
          <tr>
            <td>${r.display_label || r.distributor_name || '—'}</td>
            <td>${formatLakhs(r.target_lakhs)}</td>
          </tr>
        `,
        )
        .join('');
    }

    if (breakupRes.ok && breakupData.success) {
      const summary = breakupData.data.summary || {};
      const categoryMatrix = breakupData.data.category_matrix || {};
      const hasCategoryDetail = Boolean(breakupData.data.has_category_detail);
      const achRows = breakupData.data.breakup || [];
      updateTaWsCategoryUi(hasCategoryDetail, label, categoryMatrix);
      renderTaWsAchievementSummary(summary, hasCategoryDetail);
      if (achTbody) {
        if (!achRows.length) {
          achTbody.innerHTML =
            '<tr><td colspan="7">No achievement rows — upload sales Excel (category pivot file).</td></tr>';
        } else {
          achTbody.innerHTML = achRows
            .map(
              (r) => `
              <tr>
                <td>${r.display_label || r.distributor_name || '—'}</td>
                <td>${formatLakhs(r.target_lakhs)}</td>
                <td>${formatLakhs(r.achievement_excel)}</td>
                <td>${formatLakhs(r.achievement_ci)}</td>
                <td>${formatLakhs(r.achievement_manual)}</td>
                <td>${formatLakhs(r.achievement_lakhs)}</td>
                <td>${Number(r.percentage || 0).toFixed(1)}%</td>
              </tr>
            `,
            )
            .join('');
        }
      }
    } else if (achTbody) {
      updateTaWsCategoryUi(false, label, null);
      achTbody.innerHTML = '<tr><td colspan="7">Achievement will appear after Excel upload.</td></tr>';
      if (fyLabelEl) fyLabelEl.textContent = label;
    }
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="2">${error.message || 'Unable to load targets.'}</td></tr>`;
    if (achTbody) achTbody.innerHTML = `<tr><td colspan="7">${error.message || 'Unable to load achievement.'}</td></tr>`;
  }
}

async function loadExecutiveTargetBreakup() {
  const tbody = document.getElementById('executive-ta-breakup-tbody');
  const yearSelect = document.getElementById('executive-ta-year-select');
  if (!tbody || !yearSelect) return;
  const yearId = yearSelect.value;
  if (!yearId) {
    tbody.innerHTML = '<tr><td colspan="7">Add a fiscal year first.</td></tr>';
    return;
  }
  tbody.innerHTML = '<tr><td colspan="7">Loading…</td></tr>';
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/breakup`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to load breakup');
    }
    const summary = data.data.summary || {};
    const fyLabel = data.data.fy_label || '';
    const hasCategoryDetail = Boolean(data.data.has_category_detail);
    renderExecutiveAchievementSummary(summary, fyLabel, hasCategoryDetail);
    const rows = data.data.breakup || [];
    if (!rows.length) {
      tbody.innerHTML =
        '<tr><td colspan="7">No distributor rows yet — upload sales Excel, enter CI in Order Fulfillment, or add distributor manual achievement.</td></tr>';
      return;
    }
    tbody.innerHTML = rows
      .map(
        (r) => `
          <tr>
            <td>${r.display_label || r.distributor_name || '—'}</td>
            <td>${formatLakhs(r.target_lakhs)}</td>
            <td>${formatLakhs(r.achievement_excel)}</td>
            <td>${formatLakhs(r.achievement_ci)}</td>
            <td>${formatLakhs(r.achievement_manual)}</td>
            <td>${formatLakhs(r.achievement_lakhs)}</td>
            <td>${Number(r.percentage || 0).toFixed(1)}%</td>
          </tr>
        `,
      )
      .join('');
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="7">${error.message || 'Unable to load breakup.'}</td></tr>`;
  }
}

function openSalesExcelUploadModal() {
  setFormInlineStatus('report-upload-status', '');
  toggleModal('reportUploadModal', true);
  refreshTaYearSelects().then(() => {
    const src = document.getElementById('ta-ws-year-select');
    const dst = document.getElementById('reportYear');
    if (src && dst && src.value) dst.value = src.value;
  });
}

function openDistributorAchievementModal() {
  setFormInlineStatus('manual-entry-status', '');
  toggleModal('manualEntryModal', true);
  loadYears().then(() => {
    const src = document.getElementById('ta-ws-year-select');
    const dst = document.getElementById('yearSelect');
    if (src && dst && src.value) dst.value = src.value;
  });
}

function openFyAchievementModal() {
  setFormInlineStatus('fy-achievement-status', '');
  toggleModal('fyAchievementModal', true);
  refreshTaYearSelects().then(() => {
    const src = document.getElementById('ta-ws-year-select');
    const dst = document.getElementById('fy-achievement-year-select');
    if (src && dst && src.value) dst.value = src.value;
  });
}

async function submitFyAchievement() {
  const yearId = document.getElementById('fy-achievement-year-select')?.value;
  const amount = parseFloat(document.getElementById('fy-achievement-amount')?.value || '0');
  if (!yearId || Number.isNaN(amount) || amount < 0) {
    setFormInlineStatus('fy-achievement-status', 'Select a fiscal year and enter achievement (lakhs).', 'error');
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/manual-fy-achievement`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ achievement_lakhs: amount }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to save FY achievement');
    }
    setFormInlineStatus('fy-achievement-status', 'FY manual achievement saved.', 'success');
    closeModal('fyAchievementModal');
    document.getElementById('fy-achievement-amount').value = '';
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    setFormInlineStatus('fy-achievement-status', error.message || 'Failed to save FY achievement.', 'error');
  }
}

function openDistributorTargetModal() {
  setFormInlineStatus('dist-target-status', '');
  toggleModal('distributorTargetModal', true);
  loadYears().then(() => {
    const src = document.getElementById('ta-ws-year-select');
    const dst = document.getElementById('dist-target-year-select');
    if (src && dst && src.value) dst.value = src.value;
  });
}

async function submitDistributorTarget() {
  const yearId = document.getElementById('dist-target-year-select')?.value;
  const distributorName = document.getElementById('dist-target-name')?.value.trim();
  const nick = document.getElementById('dist-target-nick')?.value.trim();
  const target = parseFloat(document.getElementById('dist-target-amount')?.value || '0');
  if (!yearId || !distributorName || Number.isNaN(target) || target <= 0) {
    setFormInlineStatus('dist-target-status', 'Fiscal year, distributor name, and target (lakhs) are required.', 'error');
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/distributor-target`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        distributor_name: distributorName,
        target_lakhs: target,
        nick: nick || undefined,
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to save distributor target');
    }
    closeModal('distributorTargetModal');
    setFormInlineStatus('dist-target-status', '');
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    setFormInlineStatus('dist-target-status', error.message || 'Failed to save distributor target.', 'error');
  }
}

async function refreshTaYearSelects() {
  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years');
    const data = await response.json();
    if (response.ok && data.success) {
      populateYearSelectsForTa(sortFiscalYearsAscending(data.data.years || []));
    }
    loadTaFyOverviewCard();
  } catch (error) {
    console.warn('Failed to load years for TA:', error);
  }
}

function formatExecutivePhoneLinks(phone, label) {
  const digits = String(phone || '').replace(/\D/g, '');
  if (!digits) return '';
  const wa = digits.length === 10 ? `91${digits}` : digits;
  return `
    <a class="btn btn-secondary" href="tel:${digits}">📞 Call ${label || ''}</a>
    <a class="btn btn-secondary" href="https://wa.me/${wa}" target="_blank" rel="noopener">💬 WhatsApp</a>
  `;
}

function showPartyDetail(record, editFn) {
  const title = document.getElementById('party-detail-title');
  const body = document.getElementById('party-detail-body');
  const editBtn = document.getElementById('party-detail-edit-btn');
  const actionsEl = document.getElementById('party-detail-actions');
  const extraEl = document.getElementById('party-detail-360');

  title.textContent = record.name || record.firm_name || 'Details';
  body.innerHTML = Object.entries(record)
    .filter(([key, value]) => {
      if (['actions', 'distributorKey', 'partyId', 'partyType'].includes(key)) return false;
      return value !== null && value !== undefined && value !== '' && value !== '-';
    })
    .map(([key, value]) => {
      const label = PARTY_DETAIL_LABELS[key] || key;
      return `<div><strong>${label}:</strong><br>${value}</div>`;
    })
    .join('');

  const phone = record.phone || record.phone_number;
  if (actionsEl) {
    if (phone) {
      actionsEl.innerHTML = formatExecutivePhoneLinks(phone, record.name || '');
      actionsEl.classList.remove('hidden');
    } else {
      actionsEl.innerHTML = '';
      actionsEl.classList.add('hidden');
    }
  }
  if (extraEl) {
    extraEl.innerHTML = '<p class="subtitle">Loading Party 360…</p>';
    extraEl.classList.remove('hidden');
  }

  if (editFn) {
    editBtn.style.display = 'inline-block';
    editBtn.onclick = () => {
      closeModal('party-detail-modal');
      editFn();
    };
  } else {
    editBtn.style.display = 'none';
  }

  toggleModal('party-detail-modal', true);

  if (record.partyId && record.partyType) {
    loadParty360Extension(record.partyType, record.partyId, extraEl);
  } else if (extraEl) {
    extraEl.innerHTML = '<p class="subtitle">Party 360 needs a master record ID — upload or open from Customers master list.</p>';
  }
}

async function loadParty360Extension(partyType, partyId, container) {
  if (!container) return;
  try {
    const response = await fetchWithAuth(`/api/v1/executive/party/${partyType}/${partyId}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to load party details');
    }
    const payload = data.data;
    const phone = payload.phone;
    if (phone) {
      const actionsEl = document.getElementById('party-detail-actions');
      if (actionsEl) {
        actionsEl.innerHTML = formatExecutivePhoneLinks(phone, payload.party?.name || payload.party?.firm_name || '');
        actionsEl.classList.remove('hidden');
      }
    }

    const outstanding = payload.outstanding;
    const tracking = payload.tracking || [];
    const visits = payload.visits || [];
    const filled = payload.filled_orders || [];

    container.innerHTML = `
      <h3>Party 360</h3>
      ${outstanding ? `
        <div class="party-detail-360-section">
          <strong>Outstanding:</strong> ₹${Number(outstanding.outstanding || 0).toLocaleString()}
          ${outstanding.overdue ? ` · Overdue ₹${Number(outstanding.overdue).toLocaleString()}` : ''}
        </div>` : '<div class="party-detail-360-section"><span class="subtitle">Outstanding — needs invoice data in Finance module.</span></div>'}
      <div class="party-detail-360-section">
        <strong>Order tracking (${tracking.length})</strong>
        ${tracking.length ? `<ul>${tracking.slice(0, 8).map((t) => `<li>${t.distributor_name || '—'} — Ref ${t.order_ref_no || '—'} · SO ${t.has_sales_order ? '✓' : '✗'} · CI ${t.has_commercial_invoice ? '✓' : '✗'}</li>`).join('')}</ul>` : '<p class="subtitle">No lifecycle records for this party yet.</p>'}
      </div>
      <div class="party-detail-360-section">
        <strong>Your filled orders (${filled.length})</strong>
        ${filled.length ? `<ul>${filled.slice(0, 5).map((f) => `<li>#${f.id} ${f.category || 'Order'} — ${f.matched_lines || 0}/${f.total_lines || 0} lines</li>`).join('')}</ul>` : '<p class="subtitle">No filled orders linked to this distributor under your login.</p>'}
      </div>
      <div class="party-detail-360-section">
        <strong>Visits (${visits.length})</strong>
        ${visits.length ? `<ul>${visits.slice(0, 5).map((v) => `<li>${v.visit_date}: ${v.notes || '—'}</li>`).join('')}</ul>` : '<p class="subtitle">No visits logged yet.</p>'}
      </div>
    `;
  } catch (error) {
    container.innerHTML = `<p class="subtitle">${error.message || 'Party 360 unavailable.'}</p>`;
  }
}

function getVisiblePartyColumns(records, columnDefs) {
  return columnDefs.filter((col) => {
    if (col.alwaysShow) return true;
    return records.some((r) => {
      const v = r[col.key];
      return v !== null && v !== undefined && v !== '' && v !== '-';
    });
  });
}

function showPartyMasterTableLoading(tbodyId) {
  const tbody = document.getElementById(tbodyId);
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="20" style="padding:24px;text-align:center;color:var(--nx-muted);">Loading customers…</td></tr>';
  }
}

function renderPartyMasterPagination(paginationId, page, totalRecords) {
  const el = document.getElementById(paginationId);
  if (!el) return;
  const totalPages = Math.max(1, Math.ceil(totalRecords / PARTY_MASTER_PAGE_SIZE));
  const safePage = Math.min(Math.max(page, 1), totalPages);
  const start = totalRecords ? (safePage - 1) * PARTY_MASTER_PAGE_SIZE + 1 : 0;
  const end = totalRecords ? Math.min(safePage * PARTY_MASTER_PAGE_SIZE, totalRecords) : 0;
  el.innerHTML = `
    <div class="party-master-pagination">
      <span class="party-master-pagination-meta">${totalRecords ? `Showing ${start}–${end} of ${totalRecords}` : 'No records'}</span>
      <div class="party-master-pagination-actions">
        <button type="button" class="btn btn-secondary" ${safePage <= 1 ? 'disabled' : ''} onclick="changePartyMasterPage('${paginationId}', -1)">Previous</button>
        <span class="party-master-pagination-page">Page ${safePage} / ${totalPages}</span>
        <button type="button" class="btn btn-secondary" ${safePage >= totalPages ? 'disabled' : ''} onclick="changePartyMasterPage('${paginationId}', 1)">Next</button>
      </div>
    </div>
  `;
}

function renderPartyMasterTable(kind, theadId, tbodyId, paginationId, records, columnDefs, page) {
  const thead = document.getElementById(theadId);
  const tbody = document.getElementById(tbodyId);
  if (!thead || !tbody) return;

  const totalRecords = records.length;
  const totalPages = Math.max(1, Math.ceil(totalRecords / PARTY_MASTER_PAGE_SIZE));
  const safePage = Math.min(Math.max(page, 1), totalPages);
  if (kind === 'distributor') {
    partyMasterTableState.distributorPage = safePage;
  } else {
    partyMasterTableState.retailerPage = safePage;
  }

  const visibleColumns = getVisiblePartyColumns(records, columnDefs);
  renderPartyMasterPagination(paginationId, safePage, totalRecords);

  if (!visibleColumns.length || !totalRecords) {
    thead.innerHTML = '';
    tbody.innerHTML = '<tr><td colspan="20">No data yet.</td></tr>';
    return;
  }

  const start = (safePage - 1) * PARTY_MASTER_PAGE_SIZE;
  const pageRecords = records.slice(start, start + PARTY_MASTER_PAGE_SIZE);
  thead.innerHTML = `<tr>${visibleColumns.map((c) => `<th>${c.label}</th>`).join('')}</tr>`;
  partyDetailRecordsCache = pageRecords;
  tbody.innerHTML = pageRecords
    .map((r, index) => {
      const cells = visibleColumns
        .map((c) => {
          if (c.isAction) return `<td>${r[c.key] || ''}</td>`;
          const v = r[c.key];
          return `<td>${v !== null && v !== undefined && v !== '' ? v : '-'}</td>`;
        })
        .join('');
      return `<tr onclick="if(!event.target.closest('button')){showPartyDetail(partyDetailRecordsCache[${index}])}">${cells}</tr>`;
    })
    .join('');
}

function changePartyMasterPage(paginationId, delta) {
  if (paginationId === 'distributor-pagination') {
    renderPartyMasterTable(
      'distributor',
      'distributor-thead',
      'distributor-tbody',
      'distributor-pagination',
      partyMasterTableState.distributorRecords,
      DISTRIBUTOR_TABLE_COLUMNS,
      partyMasterTableState.distributorPage + delta,
    );
    scheduleCustomersLayout();
    return;
  }
  renderPartyMasterTable(
    'retailer',
    'retailer-thead',
    'retailer-tbody',
    'retailer-pagination',
    partyMasterTableState.retailerRecords,
    RETAILER_TABLE_COLUMNS,
    partyMasterTableState.retailerPage + delta,
  );
  scheduleCustomersLayout();
}

const DISTRIBUTOR_TABLE_COLUMNS = [
  { key: 'name', label: 'Firm / Name', alwaysShow: true },
  { key: 'buyerCode', label: 'Distributor Code', alwaysShow: true },
  { key: 'contactPerson', label: 'Contact Person' },
  { key: 'gst', label: 'GST Number' },
  { key: 'territory', label: 'Territory / Zone' },
  { key: 'city', label: 'City' },
  { key: 'state', label: 'State / Region' },
  { key: 'pincode', label: 'Pincode' },
  { key: 'address', label: 'Address' },
  { key: 'phone', label: 'Phone' },
  { key: 'creditLimit', label: 'Credit Limit' },
  { key: 'actions', label: 'Actions', isAction: true, alwaysShow: true },
];

const RETAILER_TABLE_COLUMNS = [
  { key: 'name', label: 'Name', alwaysShow: true },
  { key: 'contactPerson', label: 'Contact Person' },
  { key: 'distributor', label: 'Distributor' },
  { key: 'gst', label: 'GST Number' },
  { key: 'territory', label: 'Territory' },
  { key: 'city', label: 'City' },
  { key: 'state', label: 'State' },
  { key: 'pincode', label: 'Pincode' },
  { key: 'address', label: 'Address' },
  { key: 'storeType', label: 'Store Type' },
  { key: 'phone', label: 'Phone' },
  { key: 'actions', label: 'Actions', isAction: true, alwaysShow: true },
];

async function loadDistributors() {
  if (partyMasterTableState.distributorsLoading) {
    return partyMasterTableState.distributorsLoadPromise;
  }
  partyMasterTableState.distributorsLoading = true;
  showPartyMasterTableLoading('distributor-tbody');

  partyMasterTableState.distributorsLoadPromise = (async () => {
    try {
      const response = await fetchWithAuth('/api/v1/parties/distributors?limit=200');
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.message || 'Unable to load distributors');
      }
      partyMasterState.distributors = data.data.results || [];

      let masterDistributors = [];
      try {
        const masterResponse = await fetchWithAuth('/api/v1/masters/distributors?limit=5000');
        const masterData = await masterResponse.json();
        if (masterResponse.ok && masterData.success) {
          masterDistributors = masterData.data;
        }
      } catch (e) {
        console.warn('Failed to load master distributors for combined view:', e);
      }

      const records = [
        ...partyMasterState.distributors.map((d) => ({
          partyId: d.id,
          partyType: 'distributor',
          name: d.name,
          contactPerson: d.contact_person,
          gst: d.gst_number,
          territory: d.territory,
          city: d.city,
          state: d.state,
          pincode: d.pin_code,
          address: d.address,
          phone: d.phone,
          creditLimit: d.credit_limit,
          actions: `<button onclick="editDistributor(${d.id})" class="btn btn-secondary">Edit</button> <button onclick="deleteDistributor(${d.id})" class="btn btn-danger">Delete</button>`,
        })),
        ...masterDistributors.map((d) => ({
          partyId: d.id,
          partyType: 'distributor',
          name: d.firm_name || d.name,
          buyerCode: d.distributor_code || d.distributor_id || d.buyer_code || '',
          contactPerson: d.name,
          gst: d.gst_no,
          territory: d.zone,
          city: d.location,
          state: d.region,
          pincode: d.pincode,
          address: d.address,
          phone: d.phone_number,
          creditLimit: d.credit_limit,
          actions: `<button onclick="event.stopPropagation(); editMasterDistributor(${d.id})" class="btn btn-secondary">Edit</button> <button onclick="event.stopPropagation(); deleteMasterDistributor(${d.id})" class="btn btn-danger">Delete</button>`,
        })),
      ];

      partyMasterTableState.distributorRecords = records;
      partyMasterTableState.distributorPage = 1;
      partyMasterTableState.distributorsLoaded = true;
      renderPartyMasterTable(
        'distributor',
        'distributor-thead',
        'distributor-tbody',
        'distributor-pagination',
        records,
        DISTRIBUTOR_TABLE_COLUMNS,
        1,
      );
      scheduleCustomersLayout();
    } catch (error) {
      console.warn('Failed to load distributors:', error);
      const tbody = document.getElementById('distributor-tbody');
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="20">Unable to load distributors.</td></tr>';
      }
    } finally {
      partyMasterTableState.distributorsLoading = false;
    }
  })();

  return partyMasterTableState.distributorsLoadPromise;
}

function populateRetailerDistributorFilterOptions(records) {
  const select = document.getElementById('retailer-distributor-filter');
  if (!select) return;
  const currentValue = select.value;
  const uniqueDistributors = [...new Set(records.map((r) => r.distributorKey).filter(Boolean))].sort();
  select.innerHTML = ['<option value="">-- All Distributors --</option>']
    .concat(uniqueDistributors.map((name) => `<option value="${name}">${name}</option>`))
    .join('');
  select.value = currentValue;
}

function filterRetailersByDistributor() {
  const filterValue = document.getElementById('retailer-distributor-filter').value;
  const records = partyMasterState.allRetailerRecords || [];
  const filteredRecords = filterValue
    ? records.filter((r) => r.distributorKey === filterValue)
    : records;

  partyMasterTableState.retailerRecords = filteredRecords;
  partyMasterTableState.retailerPage = 1;
  renderPartyMasterTable(
    'retailer',
    'retailer-thead',
    'retailer-tbody',
    'retailer-pagination',
    filteredRecords,
    RETAILER_TABLE_COLUMNS,
    1,
  );
  scheduleCustomersLayout();
}

async function loadRetailers() {
  if (partyMasterTableState.retailersLoading) {
    return partyMasterTableState.retailersLoadPromise;
  }
  partyMasterTableState.retailersLoading = true;
  showPartyMasterTableLoading('retailer-tbody');

  partyMasterTableState.retailersLoadPromise = (async () => {
    try {
      if (!partyMasterTableState.distributorsLoaded && !partyMasterTableState.distributorsLoading) {
        await loadDistributors();
      } else if (partyMasterTableState.distributorsLoading) {
        await partyMasterTableState.distributorsLoadPromise;
      }

      const response = await fetchWithAuth('/api/v1/parties/retailers?limit=200');
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.message || 'Unable to load retailers');
      }
      partyMasterState.retailers = data.data.results || [];

      const distributorById = new Map(
        partyMasterState.distributors.map((d) => [d.id, d.name]),
      );

      let masterRetailers = [];
      try {
        const masterResponse = await fetchWithAuth('/api/v1/masters/retailers?limit=5000');
        const masterData = await masterResponse.json();
        if (masterResponse.ok && masterData.success) {
          masterRetailers = masterData.data;
        }
      } catch (e) {
        console.warn('Failed to load master retailers for combined view:', e);
      }

      const records = [
        ...partyMasterState.retailers.map((r) => {
          const distributorLabel = distributorById.get(r.distributor_id)
            || (r.distributor_id == null ? 'Unassigned' : r.distributor_id);
          return {
            partyId: r.id,
            partyType: 'retailer',
            name: r.name,
            contactPerson: r.contact_person,
            distributor: distributorLabel,
            distributorKey: distributorLabel,
            gst: r.gst_number,
            territory: r.territory,
            city: r.city,
            state: r.state,
            pincode: r.pin_code,
            address: r.address,
            storeType: r.store_type,
            phone: r.phone,
            actions: `<button onclick="editRetailer(${r.id})" class="btn btn-secondary">Edit</button> <button onclick="deleteRetailer(${r.id})" class="btn btn-danger">Delete</button>`,
          };
        }),
        ...masterRetailers.map((r) => ({
          partyId: r.id,
          partyType: 'retailer',
          name: r.name,
          contactPerson: r.contact_person,
          distributor: r.distributor_name || 'Unassigned',
          distributorKey: r.distributor_name || 'Unassigned',
          gst: r.gst_no,
          territory: r.location,
          city: r.location,
          state: r.state,
          pincode: r.pincode,
          address: r.address,
          storeType: r.category,
          phone: r.phone_number,
          actions: `<button onclick="event.stopPropagation(); editMasterRetailer(${r.id})" class="btn btn-secondary">Edit</button> <button onclick="event.stopPropagation(); deleteMasterRetailer(${r.id})" class="btn btn-danger">Delete</button>`,
        })),
      ];

      partyMasterState.allRetailerRecords = records;
      populateRetailerDistributorFilterOptions(records);

      const filterValue = document.getElementById('retailer-distributor-filter')
        ? document.getElementById('retailer-distributor-filter').value
        : '';
      const filteredRecords = filterValue
        ? records.filter((r) => r.distributorKey === filterValue)
        : records;

      partyMasterTableState.retailerRecords = filteredRecords;
      partyMasterTableState.retailerPage = 1;
      partyMasterTableState.retailersLoaded = true;
      renderPartyMasterTable(
        'retailer',
        'retailer-thead',
        'retailer-tbody',
        'retailer-pagination',
        filteredRecords,
        RETAILER_TABLE_COLUMNS,
        1,
      );
      scheduleCustomersLayout();
    } catch (error) {
      console.warn('Failed to load retailers:', error);
      const tbody = document.getElementById('retailer-tbody');
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="20">Unable to load retailers.</td></tr>';
      }
    } finally {
      partyMasterTableState.retailersLoading = false;
    }
  })();

  return partyMasterTableState.retailersLoadPromise;
}

async function loadDistributorSelect() {
  try {
    if (!partyMasterTableState.distributorsLoaded) {
      await loadDistributors();
    }
    const select = document.getElementById('retailer-distributor');
    if (!select) return;
    const options = ['<option value="">-- Unassigned --</option>']
      .concat(partyMasterState.distributors.map((d) => `<option value="${d.id}">${d.name}</option>`));
    select.innerHTML = options.join('');
  } catch (error) {
    console.warn('Failed to populate distributor select:', error);
  }
}

function openDistributorForm() {
  document.getElementById('distributor-id').value = '';
  document.getElementById('distributor-form-title').textContent = 'Add Distributor';
  document.getElementById('dist-name').value = '';
  document.getElementById('dist-contact-person').value = '';
  document.getElementById('dist-gst').value = '';
  document.getElementById('dist-territory').value = '';
  document.getElementById('dist-city').value = '';
  document.getElementById('dist-state').value = '';
  document.getElementById('dist-pincode').value = '';
  document.getElementById('dist-phone').value = '';
  document.getElementById('dist-email').value = '';
  document.getElementById('dist-address').value = '';
  document.getElementById('dist-credit-limit').value = '';
  toggleModal('distributor-form-modal', true);
}

function openRetailerForm() {
  document.getElementById('retailer-id').value = '';
  document.getElementById('retailer-form-title').textContent = 'Add Retailer';
  document.getElementById('retailer-name').value = '';
  document.getElementById('retailer-contact-person').value = '';
  document.getElementById('retailer-gst').value = '';
  document.getElementById('retailer-store-type').value = '';
  document.getElementById('retailer-territory').value = '';
  document.getElementById('retailer-city').value = '';
  document.getElementById('retailer-state').value = '';
  document.getElementById('retailer-pincode').value = '';
  document.getElementById('retailer-phone').value = '';
  document.getElementById('retailer-email').value = '';
  document.getElementById('retailer-address').value = '';
  loadDistributorSelect();
  toggleModal('retailer-form-modal', true);
}

async function saveDistributor(event) {
  event.preventDefault();
  const id = document.getElementById('distributor-id').value;
  const body = {
    name: document.getElementById('dist-name').value.trim(),
    contact_person: document.getElementById('dist-contact-person').value.trim() || undefined,
    gst_number: document.getElementById('dist-gst').value.trim() || undefined,
    territory: document.getElementById('dist-territory').value.trim() || undefined,
    city: document.getElementById('dist-city').value.trim() || undefined,
    state: document.getElementById('dist-state').value.trim() || undefined,
    pin_code: document.getElementById('dist-pincode').value.trim() || undefined,
    phone: document.getElementById('dist-phone').value.trim() || undefined,
    email: document.getElementById('dist-email').value.trim() || undefined,
    address: document.getElementById('dist-address').value.trim() || undefined,
    credit_limit: document.getElementById('dist-credit-limit').value.trim()
      ? parseFloat(document.getElementById('dist-credit-limit').value)
      : undefined,
  };

  if (!body.name) {
    alert('Firm name is required.');
    return;
  }

  try {
    const url = id ? `/api/v1/parties/distributors/${id}` : '/api/v1/parties/distributors';
    const method = id ? 'PUT' : 'POST';
    let response = await fetchWithAuth(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = await response.json();

    if (response.status === 409 && data.requires_confirmation) {
      const proceed = await nexoraConfirm(data.message || 'A similar distributor already exists. Save anyway?', {
        title: 'Possible duplicate',
        okText: 'Save anyway',
      });
      if (!proceed) {
        return;
      }
      response = await fetchWithAuth(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, force_save: true }),
      });
      data = await response.json();
    }

    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to save distributor');
    }
    closeModal('distributor-form-modal');
    loadDistributors();
    loadDistributorSelect();
    alert('Distributor saved successfully.');
  } catch (error) {
    alert(error.message || 'Error saving distributor.');
  }
}

async function editDistributor(id) {
  try {
    const response = await fetchWithAuth(`/api/v1/parties/distributors/${id}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to load distributor');
    }
    const distributor = data.data;
    document.getElementById('distributor-id').value = distributor.id;
    document.getElementById('distributor-form-title').textContent = 'Edit Distributor';
    document.getElementById('dist-name').value = distributor.name || '';
    document.getElementById('dist-contact-person').value = distributor.contact_person || '';
    document.getElementById('dist-gst').value = distributor.gst_number || '';
    document.getElementById('dist-territory').value = distributor.territory || '';
    document.getElementById('dist-city').value = distributor.city || '';
    document.getElementById('dist-state').value = distributor.state || '';
    document.getElementById('dist-pincode').value = distributor.pin_code || '';
    document.getElementById('dist-phone').value = distributor.phone || '';
    document.getElementById('dist-email').value = distributor.email || '';
    document.getElementById('dist-address').value = distributor.address || '';
    document.getElementById('dist-credit-limit').value = distributor.credit_limit || '';
    toggleModal('distributor-form-modal', true);
  } catch (error) {
    alert(error.message || 'Error loading distributor.');
  }
}

async function deleteDistributor(id) {
  if (!(await nexoraConfirm('Delete this distributor? This will mark it inactive.', { title: 'Delete distributor', danger: true, okText: 'Delete' }))) {
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/parties/distributors/${id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to delete distributor');
    }
    loadDistributors();
    loadRetailers();
    loadDistributorSelect();
  } catch (error) {
    alert(error.message || 'Error deleting distributor.');
  }
}

async function saveRetailer(event) {
  event.preventDefault();
  const id = document.getElementById('retailer-id').value;
  const distributorRaw = document.getElementById('retailer-distributor').value;
  const body = {
    name: document.getElementById('retailer-name').value.trim(),
    distributor_id: distributorRaw ? parseInt(distributorRaw, 10) : null,
    contact_person: document.getElementById('retailer-contact-person').value.trim() || undefined,
    gst_number: document.getElementById('retailer-gst').value.trim() || undefined,
    store_type: document.getElementById('retailer-store-type').value.trim() || undefined,
    territory: document.getElementById('retailer-territory').value.trim() || undefined,
    city: document.getElementById('retailer-city').value.trim() || undefined,
    state: document.getElementById('retailer-state').value.trim() || undefined,
    pin_code: document.getElementById('retailer-pincode').value.trim() || undefined,
    phone: document.getElementById('retailer-phone').value.trim() || undefined,
    email: document.getElementById('retailer-email').value.trim() || undefined,
    address: document.getElementById('retailer-address').value.trim() || undefined,
  };

  if (!body.name) {
    alert('Firm name is required.');
    return;
  }

  try {
    const url = id ? `/api/v1/parties/retailers/${id}` : '/api/v1/parties/retailers';
    const method = id ? 'PUT' : 'POST';
    let response = await fetchWithAuth(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = await response.json();

    if (response.status === 409 && data.requires_confirmation) {
      const proceed = await nexoraConfirm(data.message || 'A similar retailer already exists. Save anyway?', {
        title: 'Possible duplicate',
        okText: 'Save anyway',
      });
      if (!proceed) {
        return;
      }
      response = await fetchWithAuth(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, force_save: true }),
      });
      data = await response.json();
    }

    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to save retailer');
    }
    closeModal('retailer-form-modal');
    loadRetailers();
    alert('Retailer saved successfully.');
  } catch (error) {
    alert(error.message || 'Error saving retailer.');
  }
}

async function editRetailer(id) {
  try {
    await loadDistributorSelect();
    const response = await fetchWithAuth(`/api/v1/parties/retailers/${id}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to load retailer');
    }
    const retailer = data.data;
    document.getElementById('retailer-id').value = retailer.id;
    document.getElementById('retailer-form-title').textContent = 'Edit Retailer';
    document.getElementById('retailer-name').value = retailer.name || '';
    document.getElementById('retailer-contact-person').value = retailer.contact_person || '';
    document.getElementById('retailer-gst').value = retailer.gst_number || '';
    document.getElementById('retailer-store-type').value = retailer.store_type || '';
    document.getElementById('retailer-territory').value = retailer.territory || '';
    document.getElementById('retailer-city').value = retailer.city || '';
    document.getElementById('retailer-state').value = retailer.state || '';
    document.getElementById('retailer-pincode').value = retailer.pin_code || '';
    document.getElementById('retailer-phone').value = retailer.phone || '';
    document.getElementById('retailer-email').value = retailer.email || '';
    document.getElementById('retailer-address').value = retailer.address || '';
    document.getElementById('retailer-distributor').value = retailer.distributor_id != null ? retailer.distributor_id : '';
    toggleModal('retailer-form-modal', true);
  } catch (error) {
    alert(error.message || 'Error loading retailer.');
  }
}

async function deleteRetailer(id) {
  if (!(await nexoraConfirm('Delete this retailer? This will mark it inactive.', { title: 'Delete retailer', danger: true, okText: 'Delete' }))) {
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/parties/retailers/${id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.message || 'Unable to delete retailer');
    }
    loadRetailers();
  } catch (error) {
    alert(error.message || 'Error deleting retailer.');
  }
}

function openTargetSummary() {
  window.location.href = '/reports';
}

function openReports() {
  window.location.href = '/reports';
}

function openAnalyticsDashboard() {
  if (!authState.accessToken) {
    const loginModal = document.getElementById('loginModal');
    if (loginModal) {
      loginModal.classList.remove('hidden');
    }
    alert('Please login to access Ask NEXORA.');
    return;
  }
  window.location.href = '/analytics';
}

const NEXORA_CHAT_EXAMPLES_HOP = [
  'Show quotations pending follow-up',
  'Which hotel projects have the highest probability?',
  'Show payments overdue by more than 30 days',
  "Prepare today's sales report",
  'Lead pipeline summary',
  "Which customers haven't ordered in 6 months?",
  'Which vendor has the best price?',
  'Sales funnel summary',
];
const NEXORA_CHAT_EXAMPLES_BD = [
  'Bernina ne Florentine King bedsheet mein kitna qty order kiya?',
  'Bernina ka GST number aur address?',
  'FY 2024-2025 target achievement kitna hai?',
  'Kalra Agencies AW26 Towel total kitna hai?',
  'Aster ka ex mill kitna hai?',
  'AW26 Towel season mein sab distributors ka total?',
];

function isHopAskWorkspace() {
  return authState.role === 'hop_admin' || authState.workspaceId === 'house_of_prizm';
}

function nexoraTimeGreeting() {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  return 'Good evening';
}

function nexoraChatWelcomeHtml() {
  return `${nexoraTimeGreeting()} — how may I help you?`;
}

function applyNexoraChatChrome() {}

function renderNexoraExampleChips(examples) {
  const wrap = document.getElementById('nexora-chat-examples');
  if (!wrap) return;
  const list = Array.isArray(examples) && examples.length
    ? examples
    : (isHopAskWorkspace() ? NEXORA_CHAT_EXAMPLES_HOP : NEXORA_CHAT_EXAMPLES_BD);
  const safe = isHopAskWorkspace()
    ? list.filter((ex) => !/bernina|kalra|aster|aw26 towel/i.test(String(ex)))
    : list;
  const finalList = safe.length ? safe : (isHopAskWorkspace() ? NEXORA_CHAT_EXAMPLES_HOP : NEXORA_CHAT_EXAMPLES_BD);
  wrap.innerHTML = finalList
    .map(
      (ex) =>
        `<button type="button" class="nexora-chat-example-chip" data-nexora-example="${foEscapeText(ex)}">${foEscapeText(ex)}</button>`,
    )
    .join('');
  if (!wrap.dataset.chipBound) {
    wrap.dataset.chipBound = '1';
    wrap.addEventListener('click', (event) => {
      const btn = event.target.closest?.('.nexora-chat-example-chip');
      if (!btn) return;
      event.preventDefault();
      event.stopPropagation();
      const text = btn.getAttribute('data-nexora-example') || '';
      if (text) useNexoraExample(text, { autoSend: true });
    });
  }
}

const minimizedWidgets = new Map();

function ensureWidgetDock() {
  let dock = document.getElementById('dashboard-widget-dock');
  if (!dock) {
    dock = document.createElement('section');
    dock.id = 'dashboard-widget-dock';
    dock.className = 'dashboard-widget-dock hidden';
    dock.setAttribute('aria-label', 'Minimized widgets — click a chip to restore');
    document.body.appendChild(dock);
  } else if (dock.parentElement !== document.body) {
    document.body.appendChild(dock);
  }
  if (!dock.dataset.bound) {
    dock.dataset.bound = '1';
    dock.addEventListener('click', (event) => {
      const chip = event.target.closest?.('.dashboard-widget-dock-chip');
      if (!chip?.dataset?.widgetId) return;
      event.preventDefault();
      event.stopPropagation();
      restoreMinimizedWidget(chip.dataset.widgetId);
    });
  }
  return dock;
}

function renderWidgetDock() {
  const dock = ensureWidgetDock();
  if (minimizedWidgets.size === 0) {
    dock.classList.add('hidden');
    dock.innerHTML = '';
    return;
  }
  dock.classList.remove('hidden');
  const chips = Array.from(minimizedWidgets.entries())
    .map(
      ([id, meta]) => `
        <button type="button" class="dashboard-widget-dock-chip" data-widget-id="${foEscapeText(id)}" title="Restore">
          <span>${meta.icon || '▢'}</span>
          <span>${foEscapeText(meta.label)}</span>
          <span class="dock-restore-hint">▢</span>
        </button>
      `,
    )
    .join('');
  dock.innerHTML = `<span class="dashboard-widget-dock-label">Minimized</span>${chips}`;
}

function minimizeDashboardWidget(widgetId, label, icon) {
  const widget = document.getElementById(widgetId);
  if (!widget) return;
  widget.classList.add('widget-minimized');
  minimizedWidgets.set(widgetId, { label: label || widgetId, icon: icon || '▢' });
  renderWidgetDock();
}

function minimizeTaWidget() {
  minimizeDashboardWidget('dashboard-ta-widget', 'Target vs Achievement', '🎯');
}

function minimizeFoWidget(season) {
  const safeId = String(season || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  minimizeDashboardWidget(`dashboard-fo-widget-${safeId}`, `Orders · ${season}`, '📋');
}

function restoreMinimizedWidget(widgetId) {
  if (widgetId === 'nexora-chat') {
    restoreNexoraChatFromDock();
    return;
  }
  if (widgetId === 'dashboard-ta-widget') {
    document.getElementById('dashboard-ta-playing-card')?.classList.remove('hidden');
  }
  if (widgetId.startsWith('dashboard-fo-widget-')) {
    document.getElementById('dashboard-fo-widgets-layer')?.classList.remove('hidden');
  }
  const widget = document.getElementById(widgetId);
  if (widget) {
    widget.classList.remove('widget-minimized');
    widget.style.removeProperty('display');
  }
  minimizedWidgets.delete(widgetId);
  renderWidgetDock();
}

const nexoraChatState = {
  examplesLoadedFor: null, // user|workspace key
  boundUserKey: null,
  busy: false,
  teachPhrase: '',
};

function nexoraUserScopeKey() {
  return `${authState.userId || ''}|${authState.role || ''}|${authState.workspaceId || ''}`;
}

function resetNexoraChatForCurrentUser(forceClearHistory = false) {
  const key = nexoraUserScopeKey();
  const switched = nexoraChatState.boundUserKey !== key;
  if (forceClearHistory || switched) {
    resetNexoraChat();
    minimizedWidgets.delete('nexora-chat');
    renderWidgetDock();
    nexoraChatState.boundUserKey = key;
  }
}

function closeNexoraTeachPanel() {
  const panel = document.getElementById('nexora-chat-teach-panel');
  if (panel) {
    panel.classList.add('hidden');
    panel.setAttribute('aria-hidden', 'true');
  }
  nexoraChatState.teachPhrase = '';
  const status = document.getElementById('nexora-teach-status');
  if (status) {
    status.classList.add('hidden');
    status.textContent = '';
  }
}

function setNexoraTeachStatus(message, isError = false) {
  const status = document.getElementById('nexora-teach-status');
  if (!status) return;
  status.textContent = message;
  status.classList.remove('hidden', 'is-error');
  if (isError) status.classList.add('is-error');
}

function openNexoraTeachPanel(userPhrase, _botAnswer) {
  const panel = document.getElementById('nexora-chat-teach-panel');
  const phraseInput = document.getElementById('nexora-teach-user-phrase');
  const canonicalInput = document.getElementById('nexora-teach-canonical');
  if (!panel || !phraseInput || !canonicalInput) return;
  nexoraChatState.teachPhrase = (userPhrase || '').trim();
  phraseInput.value = nexoraChatState.teachPhrase;
  canonicalInput.value = '';
  panel.classList.remove('hidden');
  panel.setAttribute('aria-hidden', 'false');
  setNexoraTeachStatus('');
  canonicalInput.focus();
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function suggestNexoraTeachCanonical() {
  const userPhrase = (document.getElementById('nexora-teach-user-phrase')?.value || nexoraChatState.teachPhrase || '').trim();
  if (!userPhrase) return;
  const suggestBtn = document.getElementById('nexora-teach-suggest-btn');
  if (suggestBtn) suggestBtn.disabled = true;
  setNexoraTeachStatus('Gemini suggest kar raha hai…');
  try {
    const response = await fetchWithAuth('/api/v1/nexora/ask/teach', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_phrase: userPhrase, suggest_with_llm: true, dry_run: true }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Suggest failed'));
    }
    const canonical = data.data?.canonical_question;
    const llmError = data.data?.error;
    if (!canonical) {
      if (llmError === 'missing_api_key' || data.data?.gemini_configured === false) {
        setNexoraTeachStatus(
          'GEMINI_API_KEY .env mein set karein: https://aistudio.google.com/apikey phir server restart.',
          true,
        );
        return;
      }
      if (llmError === 'quota_exceeded') {
        setNexoraTeachStatus('Gemini quota khatam — thodi der baad try karein ya manually likhein.', true);
        return;
      }
      if (llmError === 'model_busy') {
        setNexoraTeachStatus('Gemini busy hai — 1-2 min baad try karein.', true);
        return;
      }
      if (llmError === 'invalid_api_key') {
        setNexoraTeachStatus('GEMINI_API_KEY galat hai — nayi key .env mein daalein.', true);
        return;
      }
      setNexoraTeachStatus('Gemini suggest nahi kar paya — baad mein try karein ya manually likhein.', true);
      return;
    }
    const canonicalInput = document.getElementById('nexora-teach-canonical');
    if (canonicalInput) canonicalInput.value = canonical;
    setNexoraTeachStatus('Suggest ready — edit karke Save karein.');
  } catch (error) {
    setNexoraTeachStatus(error.message || 'Suggest failed', true);
  } finally {
    if (suggestBtn) suggestBtn.disabled = false;
  }
}

async function submitNexoraTeach() {
  const userPhrase = (document.getElementById('nexora-teach-user-phrase')?.value || nexoraChatState.teachPhrase || '').trim();
  const canonical = (document.getElementById('nexora-teach-canonical')?.value || '').trim();
  if (!userPhrase || !canonical) {
    setNexoraTeachStatus('Sahi sawal likhna zaroori hai.', true);
    return;
  }
  const saveBtn = document.getElementById('nexora-teach-save-btn');
  if (saveBtn) saveBtn.disabled = true;
  setNexoraTeachStatus('Save ho raha hai…');
  try {
    const response = await fetchWithAuth('/api/v1/nexora/ask/teach', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_phrase: userPhrase, canonical_question: canonical }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Teach failed'));
    }
    closeNexoraTeachPanel();
    appendNexoraChatBubble(
      'bot',
      `✓ **Seekh liya!** Ab jab koi puche *"${userPhrase}"* to main *"${canonical}"* samajh kar jawab dunga.`,
    );
  } catch (error) {
    setNexoraTeachStatus(error.message || 'Save failed', true);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

function resetNexoraChat() {
  closeNexoraTeachPanel();
  applyNexoraChatChrome();
  const container = document.getElementById('nexora-chat-messages');
  if (container) {
    container.innerHTML = `<div class="nexora-chat-bubble nexora-chat-bubble-bot">${nexoraChatWelcomeHtml()}</div>`;
  }
  const input = document.getElementById('nexora-chat-input');
  if (input) input.value = '';
  nexoraChatState.examplesLoadedFor = null;
  const wrap = document.getElementById('nexora-chat-examples');
  if (wrap) wrap.innerHTML = '';
}

function bindNexoraChatOverlayDismiss() {
  const overlay = document.getElementById('nexora-chat-overlay');
  if (!overlay || overlay.dataset.dismissBound === '1') return;
  overlay.dataset.dismissBound = '1';
  // Backdrop / outside-panel tap → minimize to dock (keep conversation).
  // Only the ✕ button fully closes + clears chat.
  overlay.addEventListener('click', (event) => {
    if (overlay.classList.contains('hidden')) return;
    if (event.target === overlay) {
      event.preventDefault();
      minimizeNexoraChat();
    }
  });
  const panel = overlay.querySelector('.nexora-chat-panel');
  panel?.addEventListener('click', (event) => event.stopPropagation());
}

function setNexoraAskBackgroundBlur(active) {
  document.body.classList.toggle('nexora-ask-open', Boolean(active));
}

function openNexoraChat() {
  if (!authState.accessToken) {
    document.getElementById('loginModal')?.classList.remove('hidden');
    alert('Please login to use Ask NEXORA.');
    return;
  }
  // If another id's chat was open, wipe it before showing this user's Ask NEXORA
  resetNexoraChatForCurrentUser(false);
  minimizedWidgets.delete('nexora-chat');
  renderWidgetDock();
  const overlay = document.getElementById('nexora-chat-overlay');
  if (!overlay) return;
  bindNexoraChatOverlayDismiss();
  applyNexoraChatChrome();
  const messages = document.getElementById('nexora-chat-messages');
  if (messages && messages.children.length <= 1) {
    const onlyWelcome = messages.querySelector('.nexora-chat-bubble-bot');
    if (onlyWelcome) onlyWelcome.innerHTML = nexoraChatWelcomeHtml();
  }
  overlay.classList.remove('hidden');
  overlay.setAttribute('aria-hidden', 'false');
  setNexoraAskBackgroundBlur(true);
  loadNexoraChatExamples();
  setTimeout(() => document.getElementById('nexora-chat-input')?.focus(), 50);
}

function minimizeNexoraChat() {
  const overlay = document.getElementById('nexora-chat-overlay');
  if (!overlay) return;
  overlay.classList.add('hidden');
  overlay.setAttribute('aria-hidden', 'true');
  setNexoraAskBackgroundBlur(false);
  minimizedWidgets.set('nexora-chat', { label: 'Ask NEXORA', icon: '💬' });
  // Always pin dock to body so it stays visible on Customers / non-dashboard pages.
  ensureWidgetDock();
  renderWidgetDock();
}

function restoreNexoraChatFromDock() {
  const overlay = document.getElementById('nexora-chat-overlay');
  if (!overlay) return;
  bindNexoraChatOverlayDismiss();
  overlay.classList.remove('hidden');
  overlay.setAttribute('aria-hidden', 'false');
  setNexoraAskBackgroundBlur(true);
  minimizedWidgets.delete('nexora-chat');
  renderWidgetDock();
  setTimeout(() => document.getElementById('nexora-chat-input')?.focus(), 50);
}

function closeNexoraChat() {
  resetNexoraChat();
  const overlay = document.getElementById('nexora-chat-overlay');
  if (!overlay) return;
  overlay.classList.add('hidden');
  overlay.setAttribute('aria-hidden', 'true');
  setNexoraAskBackgroundBlur(false);
  minimizedWidgets.delete('nexora-chat');
  renderWidgetDock();
}

window.minimizeTaWidget = minimizeTaWidget;
window.minimizeFoWidget = minimizeFoWidget;
window.restoreMinimizedWidget = restoreMinimizedWidget;
window.minimizeNexoraChat = minimizeNexoraChat;
window.openNexoraChat = openNexoraChat;
window.closeNexoraChat = closeNexoraChat;
window.useNexoraExample = useNexoraExample;
window.submitNexoraQuestion = submitNexoraQuestion;
window.openNexoraTeachPanel = openNexoraTeachPanel;
window.closeNexoraTeachPanel = closeNexoraTeachPanel;
window.submitNexoraTeach = submitNexoraTeach;
window.suggestNexoraTeachCanonical = suggestNexoraTeachCanonical;

function renderNexoraChatHtml(text) {
  const escaped = foEscapeText(text || '')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\n/g, '<br>');
  return escaped;
}

function appendNexoraChatBubble(role, text, teachContext = null) {
  const container = document.getElementById('nexora-chat-messages');
  if (!container) return;
  const bubble = document.createElement('div');
  bubble.className = `nexora-chat-bubble nexora-chat-bubble-${role}`;
  bubble.innerHTML = renderNexoraChatHtml(text);
  if (role === 'bot' && teachContext?.question) {
    const footer = document.createElement('div');
    footer.className = 'nexora-chat-bubble-actions';
    const teachBtn = document.createElement('button');
    teachBtn.type = 'button';
    teachBtn.className = 'nexora-chat-teach-btn';
    teachBtn.textContent = '✎ Sikhhao';
    teachBtn.title = 'Galat jawab? Sahi sawal sikhao';
    teachBtn.addEventListener('click', () => {
      openNexoraTeachPanel(teachContext.question, text);
    });
    footer.appendChild(teachBtn);
    bubble.appendChild(footer);
  }
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

async function loadNexoraChatExamples() {
  const scopeKey = nexoraUserScopeKey();
  const wrap = document.getElementById('nexora-chat-examples');
  if (!wrap) return;
  if (isHopAskWorkspace()) {
    renderNexoraExampleChips(NEXORA_CHAT_EXAMPLES_HOP);
  }
  if (nexoraChatState.examplesLoadedFor === scopeKey && wrap.childElementCount) return;
  try {
    const response = await fetchWithAuth('/api/v1/nexora/ask/examples');
    const data = await parseApiResponse(response);
    if (!response.ok) {
      renderNexoraExampleChips(null);
      return;
    }
    // Trust server isolation tag when present
    const iso = data.isolation || {};
    if (iso.workspace_id && authState.workspaceId && iso.workspace_id !== authState.workspaceId) {
      renderNexoraExampleChips(null);
      return;
    }
    renderNexoraExampleChips(data.examples || []);
    nexoraChatState.examplesLoadedFor = scopeKey;
  } catch (e) {
    renderNexoraExampleChips(null);
  }
}

function useNexoraExample(text, opts = {}) {
  const question = String(text || '').trim();
  if (!question) return;
  const input = document.getElementById('nexora-chat-input');
  if (input) {
    input.value = question;
    input.focus();
  }
  if (opts.autoSend !== false) {
    // Chip click should actually ask — works for every logged-in id
    submitNexoraQuestion(new Event('submit'));
  }
}

async function submitNexoraQuestion(event) {
  event?.preventDefault();
  if (nexoraChatState.busy) return;
  const input = document.getElementById('nexora-chat-input');
  const question = (input?.value || '').trim();
  if (!question) return;

  appendNexoraChatBubble('user', question);
  if (input) input.value = '';
  nexoraChatState.busy = true;
  const sendBtn = document.getElementById('nexora-chat-send-btn');
  if (sendBtn) sendBtn.disabled = true;

  try {
    const response = await fetchWithAuth('/api/v1/nexora/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    if (response.status === 404) {
      throw new Error(
        'Ask NEXORA API not found — restart server: .venv\\Scripts\\python.exe _run_server_5000.py then Ctrl+Shift+R',
      );
    }
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Unable to get an answer'));
    }
    appendNexoraChatBubble('bot', data.answer || 'No answer returned.', { question });
  } catch (error) {
    const msg = (error.message || '').includes('reload')
      ? 'Server restart karein (.venv\\Scripts\\python.exe _run_server_5000.py) phir Ctrl+Shift+R.'
      : (error.message || 'Something went wrong.');
    appendNexoraChatBubble('bot', msg, { question });
  } finally {
    nexoraChatState.busy = false;
    if (sendBtn) sendBtn.disabled = false;
    input?.focus();
  }
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    const overlay = document.getElementById('nexora-chat-overlay');
    if (overlay && !overlay.classList.contains('hidden')) {
      // Escape = tuck away to dock; ✕ is the only full close/clear.
      minimizeNexoraChat();
    }
  }
});

function openProfileSettings() {
  window.location.href = '/settings/schema?entity=distributor';
}

function openWorkspaceSettings() {
  window.location.href = '/admin/database';
}

function openOrderManagement() {
  openModule('Orders');
}

function openApprovalsPage() {
  openModule('Approvals');
}

function openBankingSection() {
  openModule('Banking');
}

function openFinanceWorkspace() {
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('sales-workspace')?.classList.add('hidden');
  document.getElementById('purchase-workspace')?.classList.add('hidden');
  document.getElementById('inventory-workspace')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  document.getElementById('orders-workspace')?.classList.add('hidden');
  document.getElementById('approvals-workspace')?.classList.add('hidden');
  document.getElementById('banking-workspace')?.classList.add('hidden');
  document.getElementById('finance-workspace')?.classList.remove('hidden');
  setActiveSidebarItem('Finance');
  loadFinanceSummary();
}

function openAccountModal() {
  document.getElementById('finance-account-name').value = '';
  document.getElementById('finance-account-type').value = 'asset';
  document.getElementById('finance-account-opening-balance').value = '0';
  document.getElementById('finance-account-notes').value = '';
  toggleModal('finance-account-modal', true);
}

function openNewOrderModal() {
  document.getElementById('order-date').value = new Date().toISOString().slice(0, 10);
  document.getElementById('order-amount').value = '0';
  document.getElementById('order-tax-rate').value = '0';
  loadOrderDistributorsAndRetailers();
  toggleModal('new-order-modal', true);
}

async function loadOrderDistributorsAndRetailers() {
  try {
    await Promise.all([loadDistributors(), loadRetailers()]);
    const distributorSelect = document.getElementById('order-distributor');
    const retailerSelect = document.getElementById('order-retailer');
    if (distributorSelect) {
      distributorSelect.innerHTML = partyMasterState.distributors
        .map((d) => `<option value="${d.id}">${d.name}</option>`)
        .join('');
    }
    if (retailerSelect) {
      retailerSelect.innerHTML = partyMasterState.retailers
        .map((r) => `<option value="${r.id}">${r.name}</option>`)
        .join('');
    }
  } catch (error) {
    console.warn('Unable to load distributors/retailers for order creation:', error);
  }
}

async function saveNewOrder(event) {
  event.preventDefault();
  const distributorId = parseInt(document.getElementById('order-distributor')?.value || '0', 10);
  const retailerId = parseInt(document.getElementById('order-retailer')?.value || '0', 10);
  const orderDate = document.getElementById('order-date')?.value;
  const amount = parseFloat(document.getElementById('order-amount')?.value || '0');
  const taxRate = parseFloat(document.getElementById('order-tax-rate')?.value || '0');

  if (!distributorId || !retailerId || !orderDate || Number.isNaN(amount) || amount <= 0) {
    alert('Please provide distributor, retailer, date and an amount greater than zero.');
    return;
  }

  try {
    const response = await fetchWithAuth('/api/v1/sales-orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        distributor_id: distributorId,
        retailer_id: retailerId,
        order_date: orderDate,
        so_date: orderDate,
        tax_rate: taxRate,
        items: [
          {
            product_code: 'STD-001',
            product_name: 'Standard order item',
            quantity: 1,
            unit_price: amount,
          },
        ],
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.message || 'Unable to create order');
    }
    closeModal('new-order-modal');
    await loadOrders();
    alert('Order created successfully.');
  } catch (error) {
    alert(error.message || 'Error creating order.');
  }
}

async function openOrdersWorkspace() {
  if (!authState.accessToken) {
    document.getElementById('loginModal')?.classList.remove('hidden');
    alert('Please login to access Orders.');
    return;
  }
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('sales-workspace')?.classList.add('hidden');
  document.getElementById('purchase-workspace')?.classList.add('hidden');
  document.getElementById('inventory-workspace')?.classList.add('hidden');
  document.getElementById('finance-workspace')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  document.getElementById('approvals-workspace')?.classList.add('hidden');
  document.getElementById('banking-workspace')?.classList.add('hidden');
  document.getElementById('orders-workspace')?.classList.remove('hidden');
  setActiveSidebarItem('Orders');
  await loadOrders();
}

async function openApprovalsWorkspace() {
  if (!authState.accessToken) {
    document.getElementById('loginModal')?.classList.remove('hidden');
    alert('Please login to access Approvals.');
    return;
  }
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('sales-workspace')?.classList.add('hidden');
  document.getElementById('purchase-workspace')?.classList.add('hidden');
  document.getElementById('inventory-workspace')?.classList.add('hidden');
  document.getElementById('finance-workspace')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  document.getElementById('orders-workspace')?.classList.add('hidden');
  document.getElementById('banking-workspace')?.classList.add('hidden');
  document.getElementById('approvals-workspace')?.classList.remove('hidden');
  setActiveSidebarItem('Approvals');
  await loadApprovals();
}

async function openBankingWorkspace() {
  if (!authState.accessToken) {
    document.getElementById('loginModal')?.classList.remove('hidden');
    alert('Please login to access Banking.');
    return;
  }
  document.getElementById('dashboard')?.classList.add('hidden');
  document.getElementById('sales-workspace')?.classList.add('hidden');
  document.getElementById('purchase-workspace')?.classList.add('hidden');
  document.getElementById('inventory-workspace')?.classList.add('hidden');
  document.getElementById('finance-workspace')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  document.getElementById('orders-workspace')?.classList.add('hidden');
  document.getElementById('approvals-workspace')?.classList.add('hidden');
  document.getElementById('banking-workspace')?.classList.remove('hidden');
  setActiveSidebarItem('Banking');
  await loadBankAccounts();
}

async function loadOrders() {
  const list = document.getElementById('orders-list');
  if (!list) return;
  list.textContent = 'Loading order list...';
  try {
    await loadOrderDistributorsAndRetailers();
    const distributorMap = Object.fromEntries(partyMasterState.distributors.map((d) => [d.id, d.name]));
    const retailerMap = Object.fromEntries(partyMasterState.retailers.map((r) => [r.id, r.name]));
    const response = await fetchWithAuth('/api/v1/sales-orders?limit=100');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.message || 'Unable to load orders');
    }
    const orders = data.data.results || [];
    if (!orders.length) {
      list.innerHTML = '<div class="list-empty">No orders found.</div>';
      return;
    }
    list.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Order #</th>
            <th>Distributor</th>
            <th>Retailer</th>
            <th>Date</th>
            <th>Amount</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${orders
            .map(
              (order) => `
                <tr>
                  <td>${order.so_number || order.id}</td>
                  <td>${distributorMap[order.distributor_id] || order.distributor_id || '-'}</td>
                  <td>${retailerMap[order.retailer_id] || order.retailer_id || '-'}</td>
                  <td>${order.order_date || '-'}</td>
                  <td>₹ ${Number(order.net_amount || 0).toFixed(2)}</td>
                  <td>${order.status || '-'}</td>
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    `;
  } catch (error) {
    list.innerHTML = `<div class="error">${error.message || 'Unable to load orders.'}</div>`;
  }
}

async function loadApprovals() {
  const list = document.getElementById('approvals-list');
  if (!list) return;
  list.textContent = 'Loading approvals...';
  try {
    await loadOrderDistributorsAndRetailers();
    const distributorMap = Object.fromEntries(partyMasterState.distributors.map((d) => [d.id, d.name]));
    const retailerMap = Object.fromEntries(partyMasterState.retailers.map((r) => [r.id, r.name]));
    const response = await fetchWithAuth('/api/v1/sales-orders?status=draft&limit=100');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.message || 'Unable to load approvals');
    }
    const orders = data.data.results || [];
    if (!orders.length) {
      list.innerHTML = '<div class="list-empty">No pending approvals.</div>';
      return;
    }
    list.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Order #</th>
            <th>Distributor</th>
            <th>Retailer</th>
            <th>Date</th>
            <th>Amount</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${orders
            .map(
              (order) => `
                <tr>
                  <td>${order.so_number || order.id}</td>
                  <td>${distributorMap[order.distributor_id] || order.distributor_id || '-'}</td>
                  <td>${retailerMap[order.retailer_id] || order.retailer_id || '-'}</td>
                  <td>${order.order_date || '-'}</td>
                  <td>₹ ${Number(order.net_amount || 0).toFixed(2)}</td>
                  <td>
                    <button onclick="updateOrderStatus(${order.id}, 'approved')" class="btn btn-primary">Approve</button>
                    <button onclick="updateOrderStatus(${order.id}, 'cancelled')" class="btn btn-secondary">Reject</button>
                  </td>
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    `;
  } catch (error) {
    list.innerHTML = `<div class="error">${error.message || 'Unable to load approvals.'}</div>`;
  }
}

async function updateOrderStatus(orderId, status) {
  try {
    const response = await fetchWithAuth(`/api/v1/sales-orders/${orderId}/status`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.message || 'Unable to update order status');
    }
    await loadApprovals();
    await loadOrders();
  } catch (error) {
    alert(error.message || 'Unable to update order status.');
  }
}

async function loadBankAccounts() {
  const list = document.getElementById('bank-accounts-list');
  if (!list) return;
  list.textContent = 'Loading bank accounts...';
  try {
    const response = await fetchWithAuth('/api/v1/finance/accounts');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.message || 'Unable to load bank accounts');
    }
    const accounts = data.data || [];
    if (!accounts.length) {
      list.innerHTML = '<div class="list-empty">No bank accounts found.</div>';
      return;
    }
    list.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>Account</th>
            <th>Type</th>
            <th>Balance</th>
          </tr>
        </thead>
        <tbody>
          ${accounts
            .map(
              (account) => `
                <tr>
                  <td>${account.name}</td>
                  <td>${account.account_type}</td>
                  <td>₹ ${Number(account.opening_balance || 0).toFixed(2)}</td>
                </tr>
              `
            )
            .join('')}
        </tbody>
      </table>
    `;
  } catch (error) {
    list.innerHTML = `<div class="error">${error.message || 'Unable to load bank accounts.'}</div>`;
  }
}

function openGSTModal() {
  document.getElementById('finance-gst-period').value = '';
  document.getElementById('finance-gst-sales').value = '0';
  document.getElementById('finance-gst-purchase').value = '0';
  document.getElementById('finance-gst-rate').value = '18';
  document.getElementById('finance-gst-status').value = 'draft';
  document.getElementById('finance-gst-notes').value = '';
  toggleModal('finance-gst-modal', true);
}

function openVATModal() {
  document.getElementById('finance-vat-period').value = '';
  document.getElementById('finance-vat-sales').value = '0';
  document.getElementById('finance-vat-purchase').value = '0';
  document.getElementById('finance-vat-rate').value = '12';
  document.getElementById('finance-vat-status').value = 'draft';
  document.getElementById('finance-vat-notes').value = '';
  toggleModal('finance-vat-modal', true);
}

async function fetchFinanceOverview() {
  const response = await fetchWithAuth('/api/v1/finance/summary');
  return response.json();
}

async function loadFinanceSummary() {
  try {
    const data = await fetchFinanceOverview();
    if (data.success) {
      document.getElementById('finance-accounts-count').textContent = data.data.accounts || 0;
      document.getElementById('finance-gst-due').textContent = `₹ ${Number(data.data.gst_tax_due || 0).toFixed(2)}`;
      document.getElementById('finance-vat-due').textContent = `₹ ${Number(data.data.vat_tax_due || 0).toFixed(2)}`;
    }
    await loadFinanceAccounts();
    await loadFinanceGSTReturns();
    await loadFinanceVATReturns();
  } catch (error) {
    console.warn('Unable to load finance summary:', error);
  }
}

async function loadFinanceAccounts() {
  try {
    const response = await fetchWithAuth('/api/v1/finance/accounts');
    const data = await response.json();
    financeState.accounts = data.success ? data.data : [];
    const list = document.getElementById('finance-accounts-list');
    if (!list) return;
    list.innerHTML = financeState.accounts.map((account) => `<li>${account.name} (${account.account_type}) — ${Number(account.opening_balance || 0).toFixed(2)}</li>`).join('');
  } catch (error) {
    console.warn('Unable to load finance accounts:', error);
  }
}

async function loadFinanceGSTReturns() {
  try {
    const response = await fetchWithAuth('/api/v1/finance/gst');
    const data = await response.json();
    financeState.gstReturns = data.success ? data.data : [];
    const list = document.getElementById('finance-gst-list');
    if (!list) return;
    list.innerHTML = financeState.gstReturns.map((item) => `<li>${item.period} — ${item.filed_status} — ₹ ${Number(item.tax_amount || 0).toFixed(2)}</li>`).join('');
  } catch (error) {
    console.warn('Unable to load GST returns:', error);
  }
}

async function loadFinanceVATReturns() {
  try {
    const response = await fetchWithAuth('/api/v1/finance/vat');
    const data = await response.json();
    financeState.vatReturns = data.success ? data.data : [];
    const list = document.getElementById('finance-vat-list');
    if (!list) return;
    list.innerHTML = financeState.vatReturns.map((item) => `<li>${item.period} — ${item.filed_status} — ₹ ${Number(item.tax_amount || 0).toFixed(2)}</li>`).join('');
  } catch (error) {
    console.warn('Unable to load VAT returns:', error);
  }
}

async function saveAccount(event) {
  event.preventDefault();
  try {
    const response = await fetchWithAuth('/api/v1/finance/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('finance-account-name').value.trim(),
        account_type: document.getElementById('finance-account-type').value,
        opening_balance: parseFloat(document.getElementById('finance-account-opening-balance').value || '0'),
        notes: document.getElementById('finance-account-notes').value.trim(),
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to save account');
    closeModal('finance-account-modal');
    await loadFinanceSummary();
    alert('Account saved successfully.');
  } catch (error) {
    alert(error.message);
  }
}

async function saveGSTReturn(event) {
  event.preventDefault();
  try {
    const response = await fetchWithAuth('/api/v1/finance/gst', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        period: document.getElementById('finance-gst-period').value.trim(),
        sales_amount: parseFloat(document.getElementById('finance-gst-sales').value || '0'),
        purchase_amount: parseFloat(document.getElementById('finance-gst-purchase').value || '0'),
        tax_rate: parseFloat(document.getElementById('finance-gst-rate').value || '0'),
        filed_status: document.getElementById('finance-gst-status').value,
        notes: document.getElementById('finance-gst-notes').value.trim(),
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to save GST return');
    closeModal('finance-gst-modal');
    await loadFinanceSummary();
    alert('GST return saved successfully.');
  } catch (error) {
    alert(error.message);
  }
}

async function saveVATReturn(event) {
  event.preventDefault();
  try {
    const response = await fetchWithAuth('/api/v1/finance/vat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        period: document.getElementById('finance-vat-period').value.trim(),
        sales_amount: parseFloat(document.getElementById('finance-vat-sales').value || '0'),
        purchase_amount: parseFloat(document.getElementById('finance-vat-purchase').value || '0'),
        tax_rate: parseFloat(document.getElementById('finance-vat-rate').value || '0'),
        filed_status: document.getElementById('finance-vat-status').value,
        notes: document.getElementById('finance-vat-notes').value.trim(),
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to save VAT return');
    closeModal('finance-vat-modal');
    await loadFinanceSummary();
    alert('VAT return saved successfully.');
  } catch (error) {
    alert(error.message);
  }
}

async function checkForUpdates(manual = false) {
  try {
    const versionResponse = await fetchWithAuth('/api/v1/app/version');
    const versionData = await versionResponse.json();
    if (!versionResponse.ok || !versionData.success) {
      return;
    }

    const currentVersion = versionData.data.app_version;
    const metadataResponse = await fetchWithAuth('/api/v1/app/update-metadata');
    const metadataData = await metadataResponse.json();
    if (!metadataResponse.ok || !metadataData.success) {
      return;
    }

    const updateMetadata = metadataData.data;
    if (compareVersions(updateMetadata.version, currentVersion) > 0) {
      if (!shouldShowUpdate(updateMetadata.version, manual)) {
        return;
      }
      pendingUpdateMetadata = updateMetadata;
      showUpdateModal(updateMetadata, manual);
    } else if (manual) {
      alert('You are already running the latest version.');
    }
  } catch (error) {
    console.warn('Update check failed:', error);
    if (manual) {
      alert('Unable to check for updates right now. Please try again later.');
    }
  }
}

function showUpdateModal(updateMetadata, manual = false) {
  const promptText = document.getElementById('updatePromptText');
  const releaseNotes = document.getElementById('updateReleaseNotes');

  if (promptText) {
    promptText.textContent =
      `A new version ${updateMetadata.version} is available. Restart now to install it, or choose Later to continue using the current version until the next restart.`;
  }

  if (releaseNotes) {
    releaseNotes.textContent = updateMetadata.release_notes || '';
  }

  const modal = document.getElementById('updateModal');
  if (modal) {
    modal.classList.remove('hidden');
  }
}

function restartToInstallUpdate() {
  if (!pendingUpdateMetadata) {
    return;
  }

  const downloadUrl = pendingUpdateMetadata.download_url;
  closeModal('updateModal');
  localStorage.removeItem('deferredUpdateVersion');
  localStorage.setItem('pendingUpdateVersion', pendingUpdateMetadata.version);

  if (downloadUrl) {
    window.open(downloadUrl, '_blank');
  }

  if (window.pywebview && window.pywebview.api && typeof window.pywebview.api.restartApp === 'function') {
    window.pywebview.api.restartApp();
  } else {
    alert('Update will be installed after restart. The app will now reload.');
    window.location.reload();
  }
}

function deferUpdate() {
  if (pendingUpdateMetadata) {
    localStorage.setItem('deferredUpdateVersion', pendingUpdateMetadata.version);
    localStorage.removeItem('pendingUpdateVersion');
  }

  closeModal('updateModal');
  alert('Update postponed. The app will continue running on the current version until the next restart.');
}

function compareVersions(left, right) {
  const a = String(left).split('.').map(Number);
  const b = String(right).split('.').map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i += 1) {
    const ai = a[i] || 0;
    const bi = b[i] || 0;
    if (ai > bi) return 1;
    if (ai < bi) return -1;
  }
  return 0;
}

function showAppInfo() {
  alert('Centralized DB System\nVersion check will run automatically on startup.');
}

async function scanDuplicates() {
  if (!authState.accessToken) {
    alert('Please login to scan duplicates.');
    return;
  }
  toggleModal('scanModal', true);
  const status = document.getElementById('scanStatus');
  const progress = document.getElementById('scanProgress');
  if (status) status.textContent = 'Scanning party records...';
  if (progress) progress.style.width = '40%';

  try {
    const response = await fetchWithAuth('/api/v1/party-matching/review-queue');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Duplicate scan failed');
    }
    if (progress) progress.style.width = '100%';
    if (status) status.textContent = `Scan complete. ${data.data.pending_reviews.length} pending review items found.`;
  } catch (error) {
    if (status) status.textContent = error.message || 'Scan failed.';
  }
}

function openReviewQueue() {
  openJsonPage('Party Matching Review Queue', '/api/v1/party-matching/review-queue');
}

function openApprovalQueue() {
  openJsonPage('Party Matching Approval Queue', '/api/v1/party-matching/review-queue');
}

async function openAliasLibrary() {
  const query = prompt('Search aliases by name or GST number', '');
  if (!query) return;
  openJsonPage(
    `Alias Search: ${query}`,
    `/api/v1/party-matching/search?query=${encodeURIComponent(query)}`
  );
}

function openConnectedServices() {
  openJsonPage('Connected Storage Services', '/api/v1/storage/account');
}

let fyManualTargetYearsCache = [];

function setFormInlineStatus(elementId, message, type = 'success') {
  const el = document.getElementById(elementId);
  if (!el) return;
  if (!message) {
    el.textContent = '';
    el.classList.add('hidden');
    el.classList.remove('success', 'error');
    return;
  }
  el.textContent = message;
  el.classList.remove('hidden');
  el.classList.toggle('success', type === 'success');
  el.classList.toggle('error', type === 'error');
}

function resetFyManualTargetFields(clearTarget = true) {
  if (clearTarget) {
    const targetInput = document.getElementById('fy-manual-target-amount');
    if (targetInput) targetInput.value = '';
  }
  const newYearInput = document.getElementById('fy-manual-new-year');
  if (newYearInput) newYearInput.value = '';
}

function toggleFyTargetMode() {
  const isNew = document.getElementById('fy-target-mode-new')?.checked;
  document.getElementById('fy-target-existing-fields')?.classList.toggle('hidden', !!isNew);
  document.getElementById('fy-target-new-fields')?.classList.toggle('hidden', !isNew);
  setFormInlineStatus('fy-manual-target-status', '');
  if (isNew) {
    resetFyManualTargetFields(true);
  } else {
    prefillFyManualTarget();
  }
}

async function openFyManualTargetModal() {
  setFormInlineStatus('fy-manual-target-status', '');
  resetFyManualTargetFields(true);
  toggleModal('fyManualTargetModal', true);
  const select = document.getElementById('fy-manual-year-select');
  if (select) select.innerHTML = '<option>Loading…</option>';
  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years');
    const data = await parseApiJson(response, 'Unable to load fiscal years');
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Unable to load fiscal years');
    }
    fyManualTargetYearsCache = data.data.years || [];
    if (!fyManualTargetYearsCache.length) {
      document.getElementById('fy-target-mode-new').checked = true;
      toggleFyTargetMode();
      if (select) select.innerHTML = '';
      return;
    }
    document.getElementById('fy-target-mode-existing').checked = true;
    toggleFyTargetMode();
    if (select) {
      const sorted = dedupeFiscalYearsForSelect(fyManualTargetYearsCache);
      select.innerHTML = sorted
        .map((year) => {
          const label = getFiscalYearDisplayLabel(year);
          return `<option value="${year.id}">${label}</option>`;
        })
        .join('');
      const active = document.getElementById('ta-ws-year-select')?.value;
      if (active) select.value = active;
    }
    prefillFyManualTarget();
  } catch (error) {
    setFormInlineStatus('fy-manual-target-status', error.message || 'Unable to load fiscal years.', 'error');
  }
}

function prefillFyManualTarget() {
  if (document.getElementById('fy-target-mode-new')?.checked) {
    return;
  }
  const yearId = document.getElementById('fy-manual-year-select')?.value;
  const input = document.getElementById('fy-manual-target-amount');
  if (!input || !yearId) {
    if (input) input.value = '';
    return;
  }
  const year = fyManualTargetYearsCache.find((y) => String(y.id) === String(yearId));
  if (year) {
    input.value = year.target ?? year.target_amount ?? '';
  } else {
    input.value = '';
  }
}

async function submitFyManualTarget() {
  const isNew = document.getElementById('fy-target-mode-new')?.checked;
  const target = parseFloat(document.getElementById('fy-manual-target-amount')?.value || '0');
  if (Number.isNaN(target) || target <= 0) {
    setFormInlineStatus('fy-manual-target-status', 'Enter a target greater than zero (lakhs).', 'error');
    return;
  }
  try {
    if (isNew) {
      const year = document.getElementById('fy-manual-new-year')?.value.trim();
      if (!year) {
        setFormInlineStatus('fy-manual-target-status', 'Enter a fiscal year, e.g. 2025-26.', 'error');
        return;
      }
      const response = await fetchWithAuth('/api/v1/target-achievement/years', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ year, target, unit: 'lakhs' }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error?.message || data.error || 'Unable to add fiscal year');
      }
      if (data.data?.updated_existing) {
        setFormInlineStatus(
          'fy-manual-target-status',
          `Fiscal year ${normalizeFiscalYearLabel(data.data.year)} already existed — target updated.`,
          'success',
        );
      }
    } else {
      const yearId = document.getElementById('fy-manual-year-select')?.value;
      if (!yearId) {
        setFormInlineStatus('fy-manual-target-status', 'Select a fiscal year, or choose Create new FY.', 'error');
        return;
      }
      const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target, unit: 'lakhs' }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.error?.message || data.error || 'Unable to update target');
      }
    }
    closeModal('fyManualTargetModal');
    resetFyManualTargetFields(true);
    loadYears();
    refreshTaYearSelects();
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    setFormInlineStatus('fy-manual-target-status', error.message || 'Could not save FY target.', 'error');
  }
}

async function submitAddYear() {
  const year = document.getElementById('fiscalYear')?.value.trim();
  const target = parseFloat(document.getElementById('targetAmount')?.value || '0');
  if (!year || Number.isNaN(target) || target <= 0) {
    alert('Please enter a valid fiscal year and target amount.');
    return;
  }
  try {
    const response = await fetchWithAuth('/api/v1/target-achievement/years', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ year, target, unit: 'lakhs' }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || 'Unable to add year');
    }
    alert(`Fiscal year ${data.data.year} added successfully.`);
    closeModal('addYearModal');
    loadYears();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    alert(error.message || 'Failed to add year.');
  }
}

async function submitManualEntry() {
  const yearId = document.getElementById('yearSelect')?.value;
  const distributorName = document.getElementById('distributorName')?.value.trim();
  const amount = parseFloat(document.getElementById('achievementAmount')?.value || '0');
  if (!yearId || !distributorName || Number.isNaN(amount) || amount <= 0) {
    setFormInlineStatus('manual-entry-status', 'Complete fiscal year, distributor name, and achievement (lakhs).', 'error');
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/upload`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ distributor_name: distributorName, amount, file_name: 'manual-entry' }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to save manual entry');
    }
    closeModal('manualEntryModal');
    setFormInlineStatus('manual-entry-status', '');
    document.getElementById('distributorName').value = '';
    document.getElementById('achievementAmount').value = '';
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    setFormInlineStatus('manual-entry-status', error.message || 'Failed to save manual entry.', 'error');
  }
}

async function deleteTaFyAchievement() {
  const yearId = document.getElementById('ta-ws-year-select')?.value;
  if (!yearId) {
    alert('Select a fiscal year first.');
    return;
  }
  const fyLabel = document.getElementById('ta-ws-fy-label')?.textContent?.trim() || 'this fiscal year';
  const ok = await showSimpleConfirmModal(
    'Delete FY achievement?',
    `Remove all achievement for ${fyLabel}: Excel upload, CI, manual entries, and category breakdown. Targets are kept.`,
    'Delete achievement',
    'Cancel',
  );
  if (!ok) return;
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/achievement`, {
      method: 'DELETE',
    });
    const data = await parseApiJson(response, 'Delete failed');
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Delete failed');
    }
    await loadTaTargetWorkspace();
    loadTaFyOverviewCard();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    await showSimpleConfirmModal('Could not delete', error.message || 'Delete failed', 'OK', 'Close');
  }
}

async function deleteTaFyTargets() {
  const yearId = document.getElementById('ta-ws-year-select')?.value;
  if (!yearId) {
    alert('Select a fiscal year first.');
    return;
  }
  const fyLabel = document.getElementById('ta-ws-fy-label')?.textContent?.trim() || 'this fiscal year';
  const ok = await showSimpleConfirmModal(
    'Delete FY targets?',
    `Remove FY target and all distributor targets for ${fyLabel}. Achievement data is kept.`,
    'Delete targets',
    'Cancel',
  );
  if (!ok) return;
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/targets`, {
      method: 'DELETE',
    });
    const data = await parseApiJson(response, 'Delete failed');
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Delete failed');
    }
    await loadTaTargetWorkspace();
    loadTaFyOverviewCard();
    refreshTaYearSelects();
  } catch (error) {
    await showSimpleConfirmModal('Could not delete', error.message || 'Delete failed', 'OK', 'Close');
  }
}

async function uploadSalesReport() {
  const yearId = document.getElementById('reportYear')?.value;
  const reportFile = document.getElementById('reportFile')?.files?.[0];
  if (!yearId || !reportFile) {
    setFormInlineStatus('report-upload-status', 'Select a fiscal year and Excel file.', 'error');
    return;
  }
  setFormInlineStatus('report-upload-status', 'Uploading…', 'success');
  const formData = new FormData();
  formData.append('file', reportFile);
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/upload-sales-excel`, {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Upload failed');
    }
    const fileKind = data.data?.file_kind || 'achievement';
    const count = data.data?.distributor_count;
    if (fileKind === 'budget') {
      const total = data.data?.total_target_lakhs;
      setFormInlineStatus(
        'report-upload-status',
        `Imported ${count} distributor targets — total ${formatLakhs(total)} (budget file).`,
        'success',
      );
    } else {
      const total = data.data?.total_achievement_lakhs;
      const catCount = data.data?.category_row_count || 0;
      const catMsg = catCount ? ` ${catCount} category rows (Bed Sheet / Towels / …).` : '';
      setFormInlineStatus(
        'report-upload-status',
        `Imported ${count} distributors — Excel achievement ${formatLakhs(total)}.${catMsg}`,
        'success',
      );
    }
    closeModal('reportUploadModal');
    document.getElementById('reportFile').value = '';
    refreshTaYearSelects();
    loadTaFyOverviewCard();
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
  } catch (error) {
    setFormInlineStatus('report-upload-status', error.message || 'Failed to upload sales report.', 'error');
  }
}

async function uploadFile() {
  const uploadFileInput = document.getElementById('fileInput');
  const uploadFile = uploadFileInput?.files?.[0];
  const uploadStatus = document.getElementById('upload-status');
  const masterType = document.getElementById('uploadMasterType')?.value || 'distributors';

  if (!uploadFile) {
    alert('Please select a file to upload.');
    return;
  }

  if (!authState.accessToken) {
    alert('Please login to upload files.');
    return;
  }

  const formData = new FormData();
  formData.append('file', uploadFile);
  formData.append('master_type', masterType);

  if (uploadStatus) {
    uploadStatus.textContent = 'Uploading file...';
    uploadStatus.classList.remove('error', 'success');
  }

  try {
    const response = await fetch('/api/v1/masters/bulk-upload', {
      method: 'POST',
      body: formData,
      credentials: 'same-origin',
      headers: authState.accessToken ? { Authorization: `Bearer ${authState.accessToken}` } : {},
    });
    const responseBody = await response.json().catch(() => null);

    if (!response.ok) {
      const message = responseBody?.error?.message || responseBody?.message || 'Upload failed';
      if (uploadStatus) {
        uploadStatus.textContent = message;
        uploadStatus.classList.add('error');
      }
      return;
    }

    if (uploadStatus) {
      uploadStatus.textContent = responseBody?.message || 'Upload completed successfully.';
      uploadStatus.classList.add('success');
    }
    if (uploadFileInput) {
      uploadFileInput.value = '';
    }
  } catch (error) {
    if (uploadStatus) {
      uploadStatus.textContent = error.message || 'Unable to upload file. Check your connection.';
      uploadStatus.classList.add('error');
    }
  }
}

// Remove legacy upload handler if not used.

function loadAppIconTray() {
  const tray = document.getElementById('appIconTray');
  if (!tray) {
    return;
  }

  const order = getSavedIconOrder();
  tray.innerHTML = order
    .map((key) => {
      const icon = appIconDefinitions.find((item) => item.key === key) || appIconDefinitions[0];
      return `
        <button type="button" class="app-icon" data-key="${icon.key}" aria-label="${icon.title}" title="${icon.title}">
          <div class="app-icon-icon">${icon.icon}</div>
          <div class="app-icon-title">${icon.title}</div>
          <div class="app-icon-description">${icon.description}</div>
        </button>
      `;
    })
    .join('');

  setupIconDragHandlers();
}

function getSavedIconOrder() {
  try {
    const raw = localStorage.getItem('appIconOrder');
    const parsed = raw ? JSON.parse(raw) : null;
    if (Array.isArray(parsed) && parsed.length === appIconDefinitions.length) {
      return parsed;
    }
  } catch (error) {
    console.warn('Unable to load icon order:', error);
  }
  return appIconDefinitions.map((item) => item.key);
}

function saveIconOrder(order) {
  localStorage.setItem('appIconOrder', JSON.stringify(order));
}

let iconPressTimer = null;
let dragSourceKey = null;

function setupIconDragHandlers() {
  const items = document.querySelectorAll('.app-icon');
  items.forEach((item) => {
    item.draggable = false;
    item.classList.remove('dragging', 'drag-ready', 'drag-over');
    item.addEventListener('pointerdown', handleIconPointerDown);
    item.addEventListener('pointerup', handleIconPointerUp);
    item.addEventListener('pointerleave', handleIconPointerCancel);
    item.addEventListener('dragstart', handleIconDragStart);
    item.addEventListener('dragend', handleIconDragEnd);
    item.addEventListener('dragover', handleIconDragOver);
    item.addEventListener('dragleave', handleIconDragLeave);
    item.addEventListener('drop', handleIconDrop);
    item.addEventListener('click', handleAppIconClick);
  });
}

function handleIconPointerDown(event) {
  const item = event.currentTarget;
  iconPressTimer = window.setTimeout(() => {
    item.draggable = true;
    item.classList.add('drag-ready');
    item.setPointerCapture(event.pointerId);
  }, 500);
}

function handleIconPointerUp() {
  if (iconPressTimer) {
    clearTimeout(iconPressTimer);
    iconPressTimer = null;
  }
}

function handleIconPointerCancel() {
  if (iconPressTimer) {
    clearTimeout(iconPressTimer);
    iconPressTimer = null;
  }
}

function handleIconDragStart(event) {
  const item = event.currentTarget;
  dragSourceKey = item.dataset.key;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', dragSourceKey);
  window.requestAnimationFrame(() => item.classList.add('dragging'));
}

function handleIconDragEnd(event) {
  const item = event.currentTarget;
  item.classList.remove('dragging', 'drag-ready');
  item.draggable = false;
}

function handleIconDragOver(event) {
  event.preventDefault();
  event.currentTarget.classList.add('drag-over');
}

function handleIconDragLeave(event) {
  event.currentTarget.classList.remove('drag-over');
}

function handleIconDrop(event) {
  event.preventDefault();
  const target = event.currentTarget;
  target.classList.remove('drag-over');
  const sourceKey = event.dataTransfer.getData('text/plain') || dragSourceKey;
  const targetKey = target.dataset.key;
  if (!sourceKey || sourceKey === targetKey) {
    return;
  }

  const order = getSavedIconOrder();
  const sourceIndex = order.indexOf(sourceKey);
  const targetIndex = order.indexOf(targetKey);
  if (sourceIndex === -1 || targetIndex === -1) {
    return;
  }

  order.splice(sourceIndex, 1);
  order.splice(targetIndex, 0, sourceKey);
  saveIconOrder(order);
  loadAppIconTray();
}

function handleAppIconClick(event) {
  const item = event.currentTarget;
  const key = item.dataset.key;
  const icon = appIconDefinitions.find((entry) => entry.key === key);
  if (!icon) {
    return;
  }
  const action = window[icon.action];
  if (typeof action === 'function') {
    action();
  }
}

function setActiveSidebarItem(label) {
  const normalized = (label || '').toLowerCase();
  document.querySelectorAll('.sidebar-item, .nav-item').forEach((button) => {
    const text = button.textContent.trim().toLowerCase();
    const isActive = text === normalized || text.endsWith(normalized);
    button.classList.toggle('active', isActive);
  });
}

function isMobileNavLayout() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function pinBdNavRail() {
  /** Slim HoP left rail only — does not cover Customers/module content. */
  const dash = document.getElementById('dashboard');
  if (!dash) return;
  // Phones use bottom Menu bar — do not reserve a blank 232px desktop rail.
  if (isMobileNavLayout()) {
    dash.classList.add('hidden');
    dash.classList.remove('bd-module-mode');
    dash.style.removeProperty('right');
    dash.style.removeProperty('width');
    dash.style.removeProperty('max-width');
    dash.style.removeProperty('pointer-events');
    return;
  }
  dash.classList.remove('hidden');
  dash.classList.add('bd-module-mode');
  document.getElementById('bd-home-view')?.classList.add('hidden');
  dash.style.setProperty('right', 'auto', 'important');
  dash.style.setProperty('width', '232px', 'important');
  dash.style.setProperty('max-width', '232px', 'important');
  dash.style.setProperty('pointer-events', 'none', 'important');
  const nav = dash.querySelector('.hop-nav');
  if (nav) nav.style.setProperty('pointer-events', 'auto', 'important');
}

function pinBdShellForModule(moduleEl) {
  /** Keep HoP-style left nav visible while a BD module is open. */
  if (authState.role !== 'sales_executive') {
    document.getElementById('dashboard')?.classList.add('hidden');
    if (moduleEl) moduleEl.classList.remove('hidden');
    return;
  }
  // Overlay beside nav (Customers + workspaces) — mounting crushed scroll/layout
  const useOverlay = moduleEl && (
    moduleEl.id === 'party-master-section'
    || moduleEl.classList.contains('workspace-shell')
  );
  if (useOverlay) {
    unmountBdModule();
    pinBdNavRail();
    document.getElementById('bd-module-mount')?.classList.add('hidden');
    moduleEl.classList.remove('bd-mounted-module', 'hidden');
    document.body.appendChild(moduleEl);
    if (moduleEl.id === 'party-master-section') {
      requestAnimationFrame(() => scheduleCustomersLayout());
      setTimeout(() => scheduleCustomersLayout(), 80);
    }
    return;
  }
  if (moduleEl) {
    mountBdModule(moduleEl);
    return;
  }
  pinBdNavRail();
}

let _bdMountedEl = null;

function unmountBdModule() {
  const mount = document.getElementById('bd-module-mount');
  if (_bdMountedEl) {
    _bdMountedEl.classList.remove('bd-mounted-module');
    document.body.appendChild(_bdMountedEl);
    _bdMountedEl.classList.add('hidden');
  }
  _bdMountedEl = null;
  mount?.classList.add('hidden');
}

function mountBdModule(el) {
  if (!el || authState.role !== 'sales_executive') return false;
  const mount = document.getElementById('bd-module-mount');
  const dash = document.getElementById('dashboard');
  if (!mount || !dash) return false;

  unmountBdModule();
  _bdMountedEl = el;
  // Full shell (not slim rail) so mount has real height inside hop-main
  dash.classList.remove('hidden');
  dash.classList.remove('bd-module-mode');
  dash.style.removeProperty('right');
  dash.style.removeProperty('width');
  dash.style.removeProperty('max-width');
  dash.style.removeProperty('pointer-events');
  document.getElementById('bd-home-view')?.classList.add('hidden');
  el.classList.add('bd-mounted-module');
  el.classList.remove('hidden');
  mount.classList.remove('hidden');
  mount.appendChild(el);
  return true;
}

function showDashboardWorkspace() {
  document.body.classList.remove('customers-page-active');
  unmountBdModule();
  const dash = document.getElementById('dashboard');
  dash?.classList.remove('hidden');
  dash?.classList.remove('bd-module-mode');
  if (dash) {
    dash.style.removeProperty('right');
    dash.style.removeProperty('width');
    dash.style.removeProperty('max-width');
    dash.style.removeProperty('pointer-events');
  }
  document.getElementById('bd-home-view')?.classList.remove('hidden');
  document.getElementById('bd-module-mount')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  ['sales-workspace', 'purchase-workspace', 'inventory-workspace', 'article-master-workspace', 'order-desk-workspace', 'order-fulfillment-workspace', 'order-cycle-workspace', 'executive-home-workspace', 'hop-executive-workspace', 'target-vs-achievement-workspace', 'cloud-hub-workspace', 'filled-orders-workspace'].forEach((id) => {
    document.getElementById(id)?.classList.add('hidden');
  });
}

function renderExecutivePendingActions(actions) {
  const list = document.getElementById('executive-pending-list');
  const countEl = document.getElementById('executive-pending-count');
  if (!list) return;
  if (countEl) countEl.textContent = `${actions.length} item(s)`;
  if (!actions.length) {
    list.innerHTML = '<p class="subtitle">No pending actions — upload orders or log visits to see items here.</p>';
    return;
  }
  list.innerHTML = actions
    .slice(0, 25)
    .map(
      (a) => `
        <div class="executive-action-item severity-${a.severity || 'medium'}">
          <strong>${a.title || 'Action'}</strong>
          <p>${a.detail || ''}</p>
        </div>
      `,
    )
    .join('');
}

function renderExecutiveOrderStatus(rows) {
  const tbody = document.getElementById('executive-order-status-tbody');
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="5">No order lifecycle data yet — use Order Fulfillment uploads.</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .slice(0, 40)
    .map(
      (r) => `
        <tr>
          <td>${r.distributor_name || '—'}</td>
          <td>${r.order_ref_no || '—'}</td>
          <td>${r.has_sales_order ? '✓' : '—'}</td>
          <td>${r.has_commercial_invoice ? '✓' : '—'}</td>
          <td>${r.payment_status || '—'}</td>
        </tr>
      `,
    )
    .join('');
}

function renderExecutiveVisits(visits) {
  const list = document.getElementById('executive-visits-list');
  if (!list) return;
  if (!visits.length) {
    list.innerHTML = '<p class="subtitle">No visits logged yet.</p>';
    return;
  }
  list.innerHTML = visits
    .map(
      (v) => `
        <div class="executive-action-item severity-low">
          <strong>${v.party_name || 'Party'} (${v.party_type || ''})</strong>
          <p>${v.visit_date}${v.follow_up_date ? ` · Follow-up ${v.follow_up_date}` : ''}${v.notes ? ` — ${v.notes}` : ''}</p>
        </div>
      `,
    )
    .join('');
}

function renderExecutiveHome(data) {
  const greeting = document.getElementById('executive-home-greeting');
  const statsGrid = document.getElementById('executive-stats-grid');
  const targetLabel = document.getElementById('executive-target-label');
  const targetBar = document.getElementById('executive-target-bar');
  const targetSummary = document.getElementById('executive-target-summary');

  if (greeting && data.user) {
    greeting.textContent = `Good day, ${data.user.username || 'Executive'} — live data from your workspace.`;
  }

  const c = data.counts || {};
  if (statsGrid) {
    statsGrid.innerHTML = [
      ['Distributors', c.distributors],
      ['Retailers', c.retailers],
      ['Pending actions', c.pending_actions],
      ['Order tracking', c.tracking_records],
      ['Your filled orders', c.filled_orders],
      ['Articles', c.articles],
    ]
      .map(
        ([label, value]) => `
          <div class="executive-stat-card">
            <span>${label}</span>
            <strong>${Number(value || 0).toLocaleString()}</strong>
          </div>
        `,
      )
      .join('');
  }

  const target = data.target_achievement || {};
  if (targetLabel) {
    targetLabel.textContent = target.has_target
      ? normalizeFiscalYearLabel(target.label) || target.label || 'Fiscal year'
      : 'No fiscal year configured';
  }
  if (targetBar) {
    const pct = Math.min(100, Number(target.percentage || 0));
    targetBar.style.width = `${pct}%`;
  }
  if (targetSummary) {
    targetSummary.textContent = target.has_target
      ? `Achievement ${formatLakhs(target.achievement)} of ${formatLakhs(target.target)} (${Number(target.percentage || 0)}%)`
      : 'Add a fiscal year and target (lakhs), then upload sales Excel or enter achievement.';
  }

  renderExecutivePendingActions(data.pending_actions || []);
  renderExecutiveOrderStatus(data.order_status || []);
  renderExecutiveVisits(data.recent_visits || []);
  refreshTaYearSelects().then(() => {
    if (currentModuleKey === 'targetvsachievement') loadTaTargetWorkspace();
  });
}

async function loadExecutiveHome() {
  const loading = document.getElementById('executive-home-loading');
  const content = document.getElementById('executive-home-content');
  loading?.classList.remove('hidden');
  content?.classList.add('hidden');
  try {
    const response = await fetchWithAuth('/api/v1/executive/home');
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('application/json')) {
      throw new Error(
        response.status === 404
          ? 'Executive API not found — restart the Flask server (.venv\\Scripts\\python.exe _run_server_5000.py) and hard-refresh.'
          : `Server error (${response.status}) — hard-refresh the page. If it persists, restart Flask.`,
      );
    }
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to load executive home');
    }
    renderExecutiveHome(data.data);
    loading?.classList.add('hidden');
    content?.classList.remove('hidden');
  } catch (error) {
    if (loading) {
      loading.textContent = error.message || 'Unable to load My Day.';
    }
    console.warn('Executive home load failed:', error);
  }
}

function openExecutiveVisitModal() {
  const today = new Date().toISOString().slice(0, 10);
  const dateInput = document.getElementById('executive-visit-date');
  if (dateInput && !dateInput.value) dateInput.value = today;
  toggleModal('executive-visit-modal', true);
}

async function submitExecutiveVisit() {
  const partyType = document.getElementById('executive-visit-party-type')?.value;
  const partyName = document.getElementById('executive-visit-party-name')?.value.trim();
  const partyIdRaw = document.getElementById('executive-visit-party-id')?.value;
  const visitDate = document.getElementById('executive-visit-date')?.value;
  const followUp = document.getElementById('executive-visit-follow-up')?.value;
  const notes = document.getElementById('executive-visit-notes')?.value.trim();
  if (!partyType || !partyName || !visitDate) {
    alert('Party type, name, and visit date are required.');
    return;
  }
  const body = {
    party_type: partyType,
    party_name: partyName,
    visit_date: visitDate,
    notes: notes || undefined,
    follow_up_date: followUp || undefined,
  };
  if (partyIdRaw) body.party_id = parseInt(partyIdRaw, 10);
  try {
    const response = await fetchWithAuth('/api/v1/executive/visits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || 'Unable to save visit');
    }
    closeModal('executive-visit-modal');
    document.getElementById('executive-visit-party-name').value = '';
    document.getElementById('executive-visit-party-id').value = '';
    document.getElementById('executive-visit-notes').value = '';
    document.getElementById('executive-visit-follow-up').value = '';
    loadExecutiveHome();
  } catch (error) {
    alert(error.message || 'Failed to save visit.');
  }
}

let currentModuleKey = 'dashboard';
const moduleHistoryStack = [];
let suppressModuleHistoryPush = false;

/* House of Prizm UI lives in hop_app.js (loaded after this file). */

function goBack() {
  const previousModule = moduleHistoryStack.pop();
  if (previousModule) {
    suppressModuleHistoryPush = true;
    openModule(previousModule);
    return;
  }
  openModule('Dashboard');
}

function openModule(moduleName) {
  if (typeof closeMobileNav === 'function') closeMobileNav();
  const normalized = (moduleName || '').toLowerCase();
  if (!suppressModuleHistoryPush && normalized !== currentModuleKey) {
    moduleHistoryStack.push(currentModuleKey);
  }
  suppressModuleHistoryPush = false;
  currentModuleKey = normalized;
  const isCustomersModule = normalized === 'customers' || normalized === 'parties';
  if (!isCustomersModule) {
    document.body.classList.remove('customers-page-active');
  }
  setGlobalSearchBarVisible(normalized === 'dashboard');
  if (!isCustomersModule) {
    document.getElementById('party-master-section')?.classList.add('hidden');
  }
  document.getElementById('order-fulfillment-workspace')?.classList.add('hidden');
  document.getElementById('order-cycle-workspace')?.classList.add('hidden');
  document.getElementById('order-desk-workspace')?.classList.add('hidden');
  document.getElementById('article-master-workspace')?.classList.add('hidden');
  document.getElementById('filled-orders-workspace')?.classList.add('hidden');
  document.getElementById('executive-home-workspace')?.classList.add('hidden');
  document.getElementById('hop-executive-workspace')?.classList.add('hidden');
  document.getElementById('target-vs-achievement-workspace')?.classList.add('hidden');
  document.getElementById('cloud-hub-workspace')?.classList.add('hidden');

  if (normalized === 'hopexecutive' || normalized === 'houseofprizm') {
    unmountBdModule();
    document.getElementById('dashboard')?.classList.add('hidden');
    document.getElementById('sales-workspace')?.classList.add('hidden');
    document.getElementById('executive-home-workspace')?.classList.add('hidden');
    document.getElementById('hop-executive-workspace')?.classList.remove('hidden');
    if (typeof openHopView === 'function') openHopView('dashboard');
    else if (typeof loadHopExecutiveSnapshot === 'function') loadHopExecutiveSnapshot();
    return;
  }

  if (normalized === 'dashboard') {
    showDashboardWorkspace();
    setActiveSidebarItem('Dashboard');
    return;
  }

  if (normalized === 'myday') {
    pinBdShellForModule(document.getElementById('executive-home-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('My Day');
    loadExecutiveHome();
    return;
  }

  if (normalized === 'orderdesk') {
    pinBdShellForModule(document.getElementById('order-desk-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Order Desk');
    return;
  }

  if (normalized === 'targetvsachievement') {
    pinBdShellForModule(document.getElementById('target-vs-achievement-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Target vs Achievement');
    refreshTaYearSelects().then(() => loadTaTargetWorkspace());
    return;
  }

  if (normalized === 'cloudhub' || normalized === 'google drive' || normalized === 'filelibrary') {
    pinBdShellForModule(document.getElementById('cloud-hub-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Cloud Hub');
    loadCloudHubWorkspace();
    return;
  }

  if (normalized === 'ordercycle') {
    pinBdShellForModule(document.getElementById('order-cycle-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Order Desk');
    loadOrderCycleHierarchy();
    return;
  }

  if (normalized === 'orderfulfillment') {
    pinBdShellForModule(document.getElementById('order-fulfillment-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Order Desk');
    loadCompanyProfileV2();
    initOrderFulfillmentEmbeddedPanels();
    loadOrderFulfillmentUploads();
    return;
  }

  if (normalized === 'sales') {
    pinBdShellForModule(document.getElementById('sales-workspace'));
    document.getElementById('purchase-workspace')?.classList.add('hidden');
    document.getElementById('inventory-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Sales');
    return;
  }

  if (normalized === 'purchase') {
    pinBdShellForModule(document.getElementById('purchase-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    document.getElementById('inventory-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Purchase');
    return;
  }

  if (normalized === 'inventory') {
    pinBdShellForModule(document.getElementById('inventory-workspace'));
    document.getElementById('article-master-workspace')?.classList.add('hidden');
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Inventory');
    return;
  }

  if (normalized === 'articlemaster') {
    pinBdShellForModule(document.getElementById('article-master-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    document.getElementById('inventory-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Article Master');
    loadArticleMasterList();
    return;
  }

  if (normalized === 'filledorders') {
    pinBdShellForModule(document.getElementById('filled-orders-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    document.getElementById('inventory-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Filled Orders');
    initFilledOrdersWorkspace();
    return;
  }

  if (isCustomersModule) {
    document.getElementById('sales-workspace')?.classList.add('hidden');
    document.getElementById('purchase-workspace')?.classList.add('hidden');
    document.getElementById('inventory-workspace')?.classList.add('hidden');
    document.getElementById('finance-workspace')?.classList.add('hidden');
    document.getElementById('orders-workspace')?.classList.add('hidden');
    document.getElementById('approvals-workspace')?.classList.add('hidden');
    document.getElementById('banking-workspace')?.classList.add('hidden');
    pinBdShellForModule(document.getElementById('party-master-section'));
    document.body.classList.add('customers-page-active');
    setActiveSidebarItem('Customers');
    loadCustomersWorkspace();
    return;
  }

  if (normalized === 'reports') {
    openReports();
    return;
  }

  if (normalized === 'analytics') {
    openAnalyticsDashboard();
    return;
  }

  if (normalized === 'google drive') {
    openModule('CloudHub');
    return;
  }

  if (normalized === 'finance') {
    openFinanceWorkspace();
    return;
  }

  if (normalized === 'orders') {
    openOrdersWorkspace();
    return;
  }

  if (normalized === 'approvals') {
    openApprovalsWorkspace();
    return;
  }

  if (normalized === 'banking') {
    openBankingWorkspace();
    return;
  }

  if (normalized === 'settings') {
    openWorkspaceSettings();
    return;
  }

  console.log('Opening module:', moduleName);
  const moduleAlert = document.getElementById('module-alert');
  if (moduleAlert) {
    moduleAlert.textContent = `Opening ${moduleName} module...`;
  }
  alert(`Opening ${moduleName}`);
}

function updateGreeting() {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good Morning' : hour < 17 ? 'Good Afternoon' : 'Good Evening';
  const greetingText = document.getElementById('greeting-text');
  if (greetingText) {
    greetingText.textContent = `${greeting}, ${authState.username || 'Admin'}!`;
  }
}

function loadRecentActivities() {
  const activities = [
    { icon: '📈', title: 'Target updated for FY 2026-27', meta: 'Updated by system', time: '12m ago' },
    { icon: '📤', title: 'Sales report synced', meta: 'Uploaded via Drive', time: '42m ago' },
    { icon: '💼', title: 'New retailer onboarded', meta: 'Retailer management', time: '1h ago' },
    { icon: '🔍', title: 'Duplicate party scan completed', meta: 'Party matching', time: '2h ago' },
  ];

  const feed = document.getElementById('activity-feed');
  if (!feed) return;

  feed.innerHTML = activities
    .map(
      (activity) => `
        <div class="activity-item">
          <div class="activity-icon">${activity.icon}</div>
          <div class="activity-content">
            <div class="activity-title">${activity.title}</div>
            <div class="activity-meta">${activity.meta}</div>
          </div>
          <div class="activity-time">${activity.time}</div>
        </div>
      `
    )
    .join('');
}

let mastersDistributorCache = [];

async function fetchMasterDistributorsForDropdown() {
  try {
    const response = await fetchWithAuth('/api/v1/masters/distributors?limit=5000');
    const data = await response.json();
    if (response.ok && data.success) {
      mastersDistributorCache = data.data;
    }
  } catch (error) {
    console.warn('Failed to fetch master distributors:', error);
  }
}

function openMastersBulkModal() {
  fetchMasterDistributorsForDropdown().then(() => {
    populateMastersRetailDistributorFilter();
  });
  toggleModal('masters-bulk-modal', true);
}

function populateMasterRetailerDistributorOptions(records) {
  const select = document.getElementById('master-retailer-distributor');
  if (!select) return;
  const options = ['<option value="">-- Unassigned --</option>'];
  const items = Array.isArray(records) ? records : [];
  const markup = options.concat(items.map((d) => `<option value="${d.id}">${d.firm_name || d.name || d.distributor_id || 'Distributor'}</option>`)).join('');
  select.innerHTML = markup;
}

function resetMasterDistributorForm() {
  document.getElementById('master-distributor-id').value = '';
  document.getElementById('master-distributor-form-title').textContent = 'Add Master Distributor';
  ['master-distributor-firm-name','master-distributor-firm-nick-name','master-distributor-name','master-distributor-code','master-distributor-buyer-code','master-distributor-phone','master-distributor-phone-2','master-distributor-email','master-distributor-address','master-distributor-location','master-distributor-region','master-distributor-pincode','master-distributor-gst','master-distributor-zone','master-distributor-payment-terms','master-distributor-credit-limit','master-distributor-birthday','master-distributor-anniversary','master-distributor-secondary-name','master-distributor-secondary-phone','master-distributor-secondary-birthday','master-distributor-secondary-anniversary','master-distributor-sales-name','master-distributor-sales-phone','master-distributor-sales-email','master-distributor-sales-birthday','master-distributor-sales-anniversary'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
}

function resetMasterRetailerForm() {
  document.getElementById('master-retailer-id').value = '';
  document.getElementById('master-retailer-form-title').textContent = 'Add Master Retailer';
  ['master-retailer-name','master-retailer-contact-person','master-retailer-phone','master-retailer-phone-2','master-retailer-email','master-retailer-address','master-retailer-location','master-retailer-state','master-retailer-pincode','master-retailer-gst','master-retailer-category','master-retailer-birthday','master-retailer-anniversary','master-retailer-secondary-name','master-retailer-secondary-phone','master-retailer-secondary-birthday','master-retailer-secondary-anniversary','master-retailer-sales-name','master-retailer-sales-phone','master-retailer-sales-email','master-retailer-sales-birthday','master-retailer-sales-anniversary'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const select = document.getElementById('master-retailer-distributor');
  if (select) select.value = '';
}

function openMasterDistributorForm() {
  resetMasterDistributorForm();
  toggleModal('master-distributor-form-modal', true);
}

function openMasterRetailerForm() {
  resetMasterRetailerForm();
  fetchWithAuth('/api/v1/masters/distributors?limit=5000').then(async (response) => {
    const data = await response.json();
    if (response.ok && data.success) {
      populateMasterRetailerDistributorOptions(data.data || []);
    }
  });
  toggleModal('master-retailer-form-modal', true);
}

async function saveMasterDistributor(event) {
  event.preventDefault();
  const id = document.getElementById('master-distributor-id').value;
  const body = {
    name: document.getElementById('master-distributor-name').value.trim(),
    firm_name: document.getElementById('master-distributor-firm-name').value.trim() || undefined,
    firm_nick_name: document.getElementById('master-distributor-firm-nick-name').value.trim() || undefined,
    distributor_code: document.getElementById('master-distributor-code').value.trim() || undefined,
    buyer_code: document.getElementById('master-distributor-buyer-code').value.trim() || undefined,
    phone_number: document.getElementById('master-distributor-phone').value.trim() || undefined,
    phone_number_2: document.getElementById('master-distributor-phone-2').value.trim() || undefined,
    email: document.getElementById('master-distributor-email').value.trim() || undefined,
    address: document.getElementById('master-distributor-address').value.trim() || undefined,
    location: document.getElementById('master-distributor-location').value.trim() || undefined,
    region: document.getElementById('master-distributor-region').value.trim() || undefined,
    pincode: document.getElementById('master-distributor-pincode').value.trim() || undefined,
    gst_no: document.getElementById('master-distributor-gst').value.trim() || undefined,
    zone: document.getElementById('master-distributor-zone').value.trim() || undefined,
    payment_terms: document.getElementById('master-distributor-payment-terms').value.trim() || undefined,
    credit_limit: document.getElementById('master-distributor-credit-limit').value.trim() ? parseFloat(document.getElementById('master-distributor-credit-limit').value) : undefined,
    birthday: document.getElementById('master-distributor-birthday').value.trim() || undefined,
    anniversary: document.getElementById('master-distributor-anniversary').value.trim() || undefined,
    secondary_distributor_name: document.getElementById('master-distributor-secondary-name').value.trim() || undefined,
    secondary_distributor_phone_number: document.getElementById('master-distributor-secondary-phone').value.trim() || undefined,
    secondary_distributor_birthday: document.getElementById('master-distributor-secondary-birthday').value.trim() || undefined,
    secondary_distributor_anniversary: document.getElementById('master-distributor-secondary-anniversary').value.trim() || undefined,
    sales_executive_name: document.getElementById('master-distributor-sales-name').value.trim() || undefined,
    sales_executive_phone_number: document.getElementById('master-distributor-sales-phone').value.trim() || undefined,
    sales_executive_email: document.getElementById('master-distributor-sales-email').value.trim() || undefined,
    sales_executive_birthday: document.getElementById('master-distributor-sales-birthday').value.trim() || undefined,
    sales_executive_anniversary: document.getElementById('master-distributor-sales-anniversary').value.trim() || undefined,
  };
  if (!body.name) {
    alert('Contact person / name is required.');
    return;
  }
  try {
    const url = id ? `/api/v1/masters/distributors/${id}` : '/api/v1/masters/distributors';
    const method = id ? 'PUT' : 'POST';
    const response = await fetchWithAuth(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to save distributor');
    closeModal('master-distributor-form-modal');
    openMastersGrid('distributors');
    alert('Master distributor saved successfully.');
  } catch (error) {
    alert(error.message || 'Error saving distributor.');
  }
}

async function editMasterDistributor(id) {
  try {
    const response = await fetchWithAuth(`/api/v1/masters/distributors/${id}`);
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to load distributor');
    const record = data.data;
    document.getElementById('master-distributor-id').value = record.id;
    document.getElementById('master-distributor-form-title').textContent = 'Edit Master Distributor';
    document.getElementById('master-distributor-firm-name').value = record.firm_name || '';
    document.getElementById('master-distributor-firm-nick-name').value = record.firm_nick_name || '';
    document.getElementById('master-distributor-name').value = record.name || '';
    document.getElementById('master-distributor-code').value = record.distributor_code || '';
    document.getElementById('master-distributor-buyer-code').value = record.buyer_code || '';
    document.getElementById('master-distributor-phone').value = record.phone_number || '';
    document.getElementById('master-distributor-phone-2').value = record.phone_number_2 || '';
    document.getElementById('master-distributor-email').value = record.email || '';
    document.getElementById('master-distributor-address').value = record.address || '';
    document.getElementById('master-distributor-location').value = record.location || '';
    document.getElementById('master-distributor-region').value = record.region || '';
    document.getElementById('master-distributor-pincode').value = record.pincode || '';
    document.getElementById('master-distributor-gst').value = record.gst_no || '';
    document.getElementById('master-distributor-zone').value = record.zone || '';
    document.getElementById('master-distributor-payment-terms').value = record.payment_terms || '';
    document.getElementById('master-distributor-credit-limit').value = record.credit_limit || '';
    document.getElementById('master-distributor-birthday').value = record.birthday || '';
    document.getElementById('master-distributor-anniversary').value = record.anniversary || '';
    document.getElementById('master-distributor-secondary-name').value = record.secondary_distributor_name || '';
    document.getElementById('master-distributor-secondary-phone').value = record.secondary_distributor_phone_number || '';
    document.getElementById('master-distributor-secondary-birthday').value = record.secondary_distributor_birthday || '';
    document.getElementById('master-distributor-secondary-anniversary').value = record.secondary_distributor_anniversary || '';
    document.getElementById('master-distributor-sales-name').value = record.sales_executive_name || '';
    document.getElementById('master-distributor-sales-phone').value = record.sales_executive_phone_number || '';
    document.getElementById('master-distributor-sales-email').value = record.sales_executive_email || '';
    document.getElementById('master-distributor-sales-birthday').value = record.sales_executive_birthday || '';
    document.getElementById('master-distributor-sales-anniversary').value = record.sales_executive_anniversary || '';
    toggleModal('master-distributor-form-modal', true);
  } catch (error) {
    alert(error.message || 'Error loading distributor.');
  }
}

async function deleteMasterDistributor(id) {
  if (!(await nexoraConfirm('Delete this master distributor? This will mark it inactive.', { title: 'Delete distributor', danger: true, okText: 'Delete' }))) return;
  try {
    const response = await fetchWithAuth(`/api/v1/masters/distributors/${id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to delete distributor');
    openMastersGrid('distributors');
  } catch (error) {
    alert(error.message || 'Error deleting distributor.');
  }
}

async function saveMasterRetailer(event) {
  event.preventDefault();
  const id = document.getElementById('master-retailer-id').value;
  const body = {
    name: document.getElementById('master-retailer-name').value.trim(),
    distributor_id: document.getElementById('master-retailer-distributor').value ? parseInt(document.getElementById('master-retailer-distributor').value, 10) : null,
    contact_person: document.getElementById('master-retailer-contact-person').value.trim() || undefined,
    phone_number: document.getElementById('master-retailer-phone').value.trim() || undefined,
    phone_number_2: document.getElementById('master-retailer-phone-2').value.trim() || undefined,
    email: document.getElementById('master-retailer-email').value.trim() || undefined,
    address: document.getElementById('master-retailer-address').value.trim() || undefined,
    location: document.getElementById('master-retailer-location').value.trim() || undefined,
    state: document.getElementById('master-retailer-state').value.trim() || undefined,
    pincode: document.getElementById('master-retailer-pincode').value.trim() || undefined,
    gst_no: document.getElementById('master-retailer-gst').value.trim() || undefined,
    category: document.getElementById('master-retailer-category').value.trim() || undefined,
    birthday: document.getElementById('master-retailer-birthday').value.trim() || undefined,
    anniversary: document.getElementById('master-retailer-anniversary').value.trim() || undefined,
    secondary_retailer_name: document.getElementById('master-retailer-secondary-name').value.trim() || undefined,
    secondary_retailer_phone_number: document.getElementById('master-retailer-secondary-phone').value.trim() || undefined,
    secondary_retailer_birthday: document.getElementById('master-retailer-secondary-birthday').value.trim() || undefined,
    secondary_retailer_anniversary: document.getElementById('master-retailer-secondary-anniversary').value.trim() || undefined,
    sales_executive_name: document.getElementById('master-retailer-sales-name').value.trim() || undefined,
    sales_executive_phone_number: document.getElementById('master-retailer-sales-phone').value.trim() || undefined,
    sales_executive_email: document.getElementById('master-retailer-sales-email').value.trim() || undefined,
    sales_executive_birthday: document.getElementById('master-retailer-sales-birthday').value.trim() || undefined,
    sales_executive_anniversary: document.getElementById('master-retailer-sales-anniversary').value.trim() || undefined,
  };
  if (!body.name) { alert('Shop name is required.'); return; }
  try {
    const url = id ? `/api/v1/masters/retailers/${id}` : '/api/v1/masters/retailers';
    const method = id ? 'PUT' : 'POST';
    const response = await fetchWithAuth(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to save retailer');
    closeModal('master-retailer-form-modal');
    openMastersGrid('retailers');
    alert('Master retailer saved successfully.');
  } catch (error) {
    alert(error.message || 'Error saving retailer.');
  }
}

async function editMasterRetailer(id) {
  try {
    await fetchWithAuth('/api/v1/masters/distributors?limit=5000').then(async (response) => {
      const data = await response.json();
      if (response.ok && data.success) {
        populateMasterRetailerDistributorOptions(data.data || []);
      }
    });
    const response = await fetchWithAuth(`/api/v1/masters/retailers/${id}`);
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to load retailer');
    const record = data.data;
    document.getElementById('master-retailer-id').value = record.id;
    document.getElementById('master-retailer-form-title').textContent = 'Edit Master Retailer';
    document.getElementById('master-retailer-name').value = record.name || '';
    document.getElementById('master-retailer-contact-person').value = record.contact_person || '';
    document.getElementById('master-retailer-phone').value = record.phone_number || '';
    document.getElementById('master-retailer-phone-2').value = record.phone_number_2 || '';
    document.getElementById('master-retailer-email').value = record.email || '';
    document.getElementById('master-retailer-address').value = record.address || '';
    document.getElementById('master-retailer-location').value = record.location || '';
    document.getElementById('master-retailer-state').value = record.state || '';
    document.getElementById('master-retailer-pincode').value = record.pincode || '';
    document.getElementById('master-retailer-gst').value = record.gst_no || '';
    document.getElementById('master-retailer-category').value = record.category || '';
    document.getElementById('master-retailer-birthday').value = record.birthday || '';
    document.getElementById('master-retailer-anniversary').value = record.anniversary || '';
    document.getElementById('master-retailer-secondary-name').value = record.secondary_retailer_name || '';
    document.getElementById('master-retailer-secondary-phone').value = record.secondary_retailer_phone_number || '';
    document.getElementById('master-retailer-secondary-birthday').value = record.secondary_retailer_birthday || '';
    document.getElementById('master-retailer-secondary-anniversary').value = record.secondary_retailer_anniversary || '';
    document.getElementById('master-retailer-sales-name').value = record.sales_executive_name || '';
    document.getElementById('master-retailer-sales-phone').value = record.sales_executive_phone_number || '';
    document.getElementById('master-retailer-sales-email').value = record.sales_executive_email || '';
    document.getElementById('master-retailer-sales-birthday').value = record.sales_executive_birthday || '';
    document.getElementById('master-retailer-sales-anniversary').value = record.sales_executive_anniversary || '';
    document.getElementById('master-retailer-distributor').value = record.distributor_id != null ? String(record.distributor_id) : '';
    toggleModal('master-retailer-form-modal', true);
  } catch (error) {
    alert(error.message || 'Error loading retailer.');
  }
}

async function deleteMasterRetailer(id) {
  if (!(await nexoraConfirm('Delete this master retailer? This will mark it inactive.', { title: 'Delete retailer', danger: true, okText: 'Delete' }))) return;
  try {
    const response = await fetchWithAuth(`/api/v1/masters/retailers/${id}`, { method: 'DELETE' });
    const data = await response.json();
    if (!response.ok || !data.success) throw new Error(data.error?.message || 'Unable to delete retailer');
    openMastersGrid('retailers');
  } catch (error) {
    alert(error.message || 'Error deleting retailer.');
  }
}

function openMastersBulkTab(which) {
  const distTab = document.getElementById('masters-bulk-dist-tab');
  const retailTab = document.getElementById('masters-bulk-retail-tab');
  const distPanel = document.getElementById('masters-bulk-distributors-panel');
  const retailPanel = document.getElementById('masters-bulk-retailers-panel');
  if (which === 'retailers') {
    distTab.classList.remove('active');
    retailTab.classList.add('active');
    distPanel.classList.add('hidden');
    retailPanel.classList.remove('hidden');
  } else {
    retailTab.classList.remove('active');
    distTab.classList.add('active');
    retailPanel.classList.add('hidden');
    distPanel.classList.remove('hidden');
  }
}

function toggleMastersRetailDistributorFilter() {
  const scope = document.getElementById('masters-retail-export-scope').value;
  const group = document.getElementById('masters-retail-distributor-filter-group');
  if (scope === 'distributor') {
    group.classList.remove('hidden');
  } else {
    group.classList.add('hidden');
  }
}

function populateMastersRetailDistributorFilter() {
  const select = document.getElementById('masters-retail-export-distributor');
  if (!select) return;
  select.innerHTML = mastersDistributorCache
    .map((d) => `<option value="${d.id}">${d.firm_name || d.name}</option>`)
    .join('');
}

async function openMastersGrid(masterType) {
  const title = document.getElementById('masters-grid-title');
  const loading = document.getElementById('masters-grid-loading');
  const thead = document.getElementById('masters-grid-thead');
  const tbody = document.getElementById('masters-grid-tbody');

  title.textContent = masterType === 'distributors' ? 'All Distributors (Master Data)' : 'All Retailers (Master Data)';
  loading.classList.remove('hidden');
  thead.innerHTML = '';
  tbody.innerHTML = '';
  toggleModal('masters-grid-modal', true);

  // Curated, sensible column order — a raw dump of every DB column
  // (including internal ids and workspace_id) would be clutter, not
  // a usable "Excel-like" view for a real person to read.
  const columnsByType = {
    distributors: [
      ['distributor_id', 'Distributor Code'],
      ['firm_name', 'Firm Name'],
      ['firm_nick_name', 'Nickname'],
      ['name', 'Contact Person'],
      ['phone_number', 'Phone'],
      ['phone_number_2', 'Phone 2'],
      ['email', 'Email'],
      ['location', 'City'],
      ['zone', 'Zone'],
      ['region', 'Region'],
      ['address', 'Address'],
      ['pincode', 'Pincode'],
      ['gst_no', 'GST No'],
      ['payment_terms', 'Payment Terms'],
      ['credit_limit', 'Credit Limit'],
      ['birthday', 'Birthday'],
      ['anniversary', 'Anniversary'],
      ['secondary_distributor_name', 'Secondary Partner'],
      ['secondary_distributor_phone_number', 'Secondary Partner Phone'],
      ['sales_executive_name', 'Sales Executive'],
      ['sales_executive_phone_number', 'Sales Executive Phone'],
      ['status', 'Status'],
    ],
    retailers: [
      ['name', 'Shop Name'],
      ['contact_person', 'Contact Person'],
      ['distributor_name', 'Distributor'],
      ['phone_number', 'Phone'],
      ['phone_number_2', 'Phone 2'],
      ['email', 'Email'],
      ['location', 'City'],
      ['state', 'State'],
      ['pincode', 'Pincode'],
      ['address', 'Address'],
      ['gst_no', 'GST No'],
      ['category', 'Category'],
      ['birthday', 'Birthday'],
      ['anniversary', 'Anniversary'],
      ['secondary_retailer_name', 'Secondary Retailer'],
      ['secondary_retailer_phone_number', 'Secondary Retailer Phone'],
      ['sales_executive_name', 'Sales Executive'],
      ['status', 'Status'],
    ],
  };
  // Always keep identity + status columns even if sparse; hide purely empty optional fields.
  const alwaysShowKeys = masterType === 'distributors'
    ? new Set(['distributor_id', 'firm_name', 'name', 'status'])
    : new Set(['name', 'contact_person', 'distributor_name', 'status']);

  const isFilledMasterValue = (value) => {
    if (value == null) return false;
    const text = String(value).trim();
    return text !== '' && text !== '-';
  };

  try {
    const response = await fetchWithAuth(`/api/v1/masters/${masterType}?limit=5000`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load data');
    }
    const rows = data.data || [];
    const candidateColumns = columnsByType[masterType] || [];

    // Show a column only when at least one row has a real value (or it's a core identity/status field).
    const visibleColumns = candidateColumns.filter(([key]) => {
      if (alwaysShowKeys.has(key)) return true;
      return rows.some((row) => isFilledMasterValue(row[key]));
    });
    const columns = [...visibleColumns, ['actions', 'Actions']];

    thead.innerHTML = `<tr>${columns.map(([, label]) => `<th>${label}</th>`).join('')}</tr>`;

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="${columns.length}">No records yet.</td></tr>`;
    } else {
      tbody.innerHTML = rows
        .map((row) => {
          const actions = masterType === 'distributors'
            ? `<button class="btn btn-secondary" onclick="event.stopPropagation(); editMasterDistributor(${row.id})">Edit</button> <button class="btn btn-danger" onclick="event.stopPropagation(); deleteMasterDistributor(${row.id})">Delete</button>`
            : `<button class="btn btn-secondary" onclick="event.stopPropagation(); editMasterRetailer(${row.id})">Edit</button> <button class="btn btn-danger" onclick="event.stopPropagation(); deleteMasterRetailer(${row.id})">Delete</button>`;
          const cells = columns
            .map(([key]) => {
              if (key === 'actions') return `<td>${actions}</td>`;
              return `<td>${isFilledMasterValue(row[key]) ? foEscapeText(row[key]) : '—'}</td>`;
            })
            .join('');
          return `<tr>${cells}</tr>`;
        })
        .join('');
    }
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="4">Error: ${foEscapeText(error.message)}</td></tr>`;
  } finally {
    loading.classList.add('hidden');
  }
}

async function bulkUploadMasters(masterType) {
  const fileInputId = masterType === 'distributors' ? 'masters-dist-upload-file' : 'masters-retail-upload-file';
  const resultId = masterType === 'distributors' ? 'masters-dist-upload-result' : 'masters-retail-upload-result';
  const fileInput = document.getElementById(fileInputId);
  const resultBox = document.getElementById(resultId);
  const file = fileInput.files[0];

  if (!file) {
    alert('Please choose a file first.');
    return;
  }

  resultBox.textContent = 'Uploading and processing — this may take a few seconds for large files...';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const response = await fetchWithAuth(`/api/v1/masters/${masterType}/bulk-upload`, {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Bulk upload failed');
    }
    const r = data.data;
    let summary = `Processed ${r.rows_processed} rows — Inserted: ${r.inserted}, Updated: ${r.updated}, Skipped: ${r.skipped}`;
    if (masterType === 'retailers') {
      summary += `, Unassigned (no distributor match): ${r.unassigned}`;
      if (r.ambiguous_distributor_matches && r.ambiguous_distributor_matches.length) {
        summary += `\n${r.ambiguous_distributor_matches.length} row(s) had an ambiguous distributor match — please review and link manually.`;
      }
    }
    if (r.errors && r.errors.length) {
      summary += `\nErrors: ${r.errors.length} (see console for details)`;
      console.warn('Bulk upload errors:', r.errors);
    }
    resultBox.textContent = summary;
    fileInput.value = '';
    fetchMasterDistributorsForDropdown();
  } catch (error) {
    resultBox.textContent = `Error: ${error.message || 'Bulk upload failed'}`;
  }
}

function sanitizeFilenamePart(text) {
  return (text || '').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'unknown';
}

async function downloadMastersDistributors() {
  const format = document.getElementById('masters-dist-export-format').value;
  await triggerMastersDownload(`/api/v1/masters/distributors/export?format=${format}`, `all_distributors.${format}`);
}

async function downloadMastersRetailers() {
  const format = document.getElementById('masters-retail-export-format').value;
  const scope = document.getElementById('masters-retail-export-scope').value;
  let url = `/api/v1/masters/retailers/export?format=${format}`;
  let filename = `all_retailers.${format}`;
  if (scope === 'distributor') {
    const distributorSelect = document.getElementById('masters-retail-export-distributor');
    const distributorId = distributorSelect.value;
    if (!distributorId) {
      alert('Please choose a distributor first.');
      return;
    }
    const distributorName = distributorSelect.options[distributorSelect.selectedIndex].text;
    url += `&distributor_id=${distributorId}`;
    filename = `retailers-${sanitizeFilenamePart(distributorName)}.${format}`;
  }
  await triggerMastersDownload(url, filename);
}

async function triggerMastersDownload(url, filename) {
  try {
    const response = await fetchWithAuth(url);
    if (!response.ok) {
      throw new Error('Download failed');
    }
    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(downloadUrl);
  } catch (error) {
    alert(error.message || 'Download failed.');
  }
}

let globalSearchState = { results: {}, activeCategory: null };

const GLOBAL_SEARCH_CATEGORY_LABELS = {
  distributors: 'Distributors',
  retailers: 'Retailers',
  orders: 'Sales Orders / CI',
  stock: 'Stock',
  article_master: 'Article Master',
  verifications: 'Verifications',
  visit_logs: 'Visit Logs',
  analytics: 'Analytics',
};

const GLOBAL_SEARCH_MIN_CHARS = 2;

function resetGlobalSearchUi() {
  const input = document.getElementById('global-search-input');
  if (input) {
    input.value = '';
    input.setAttribute('autocomplete', 'off');
  }
  clearTimeout(globalSearchDebounceTimer);
  globalSearchRequestToken += 1;
  closeGlobalSearchModal();
}

function setGlobalSearchBarVisible(show) {
  const shell = document.getElementById('global-search-shell');
  const trigger = document.getElementById('global-search-trigger');
  if (shell) shell.classList.toggle('hidden', !show);
  if (trigger) trigger.classList.toggle('hidden', !show);
  if (!show) {
    resetGlobalSearchUi();
  }
}

function closeGlobalSearchModal() {
  const modal = document.getElementById('global-search-modal');
  if (modal) {
    modal.classList.add('hidden');
  }
}

function initGlobalSearchUi() {
  const modal = document.getElementById('global-search-modal');
  if (!modal || modal.dataset.dismissBound === '1') {
    return;
  }
  modal.dataset.dismissBound = '1';
  modal.addEventListener('click', (event) => {
    if (event.target === modal) {
      closeGlobalSearchModal();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modal && !modal.classList.contains('hidden')) {
      closeGlobalSearchModal();
    }
  });
  document.addEventListener('click', (event) => {
    if (!modal || modal.classList.contains('hidden')) {
      return;
    }
    const input = document.getElementById('global-search-input');
    const content = modal.querySelector('.modal-content');
    if (content?.contains(event.target)) return;
    if (input?.contains(event.target)) return;
    if (event.target.closest('#global-search-trigger')) return;
    closeGlobalSearchModal();
  });
}

function isFilledOrderDuplicateMessage(message) {
  const text = String(message || '').toLowerCase();
  return text.includes('unique constraint')
    || text.includes('already exists')
    || text.includes('duplicate');
}

async function handleFilledOrderDuplicateResponse(data, prefix, baseParams, resultEl) {
  const replace = await showFilledOrderDuplicateModal(data);
  if (!replace) {
    if (resultEl) {
      resultEl.textContent = 'Upload cancelled — an order for this distributor and season already exists.';
    }
    return true;
  }
  await uploadFilledOrder({
    ...baseParams,
    confirm_commit: 'true',
    confirm_replace: 'true',
    skip_item_keys: JSON.stringify(
      filledOrdersState.pendingPreview
        ? [...filledOrdersState.pendingPreview.rejectedKeys]
        : [],
    ),
  }, prefix);
  filledOrdersState.pendingPreview = null;
  return true;
}

let globalSearchDebounceTimer = null;

function scheduleGlobalSearch() {
  const input = document.getElementById('global-search-input');
  clearTimeout(globalSearchDebounceTimer);
  const query = (input?.value || '').trim();
  if (!query) {
    const modal = document.getElementById('global-search-modal');
    if (modal && !modal.classList.contains('hidden')) {
      closeModal('global-search-modal');
    }
    return;
  }
  if (query.length < GLOBAL_SEARCH_MIN_CHARS) {
    return;
  }
  globalSearchDebounceTimer = setTimeout(runGlobalSearch, 300);
}

let globalSearchRequestToken = 0;

async function runGlobalSearch() {
  const input = document.getElementById('global-search-input');
  const query = (input?.value || '').trim();
  if (!query || query.length < GLOBAL_SEARCH_MIN_CHARS) {
    return;
  }

  const thisRequestToken = ++globalSearchRequestToken;

  document.getElementById('global-search-query-label').textContent = query;
  document.getElementById('global-search-loading').classList.remove('hidden');
  document.getElementById('global-search-tabs').innerHTML = '';
  document.getElementById('global-search-thead').innerHTML = '';
  document.getElementById('global-search-tbody').innerHTML = '';
  toggleModal('global-search-modal', true);

  try {
    const response = await fetchWithAuth(`/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();

    // An OLDER, slower keystroke's response can arrive AFTER a newer
    // one — without this check, it would overwrite the newer,
    // correct results with stale ones (this is what made live search
    // feel "very slow"/wrong while typing quickly).
    if (thisRequestToken !== globalSearchRequestToken) {
      return;
    }

    globalSearchState.results = data.results || {};

    const tabsContainer = document.getElementById('global-search-tabs');
    const categoriesWithResults = Object.keys(globalSearchState.results)
      .filter((cat) => globalSearchState.results[cat].length > 0);

    if (!categoriesWithResults.length) {
      tabsContainer.innerHTML = '';
      document.getElementById('global-search-tbody').innerHTML = '<tr><td>No results found.</td></tr>';
    } else {
      tabsContainer.innerHTML = categoriesWithResults
        .map((cat, index) => {
          const label = GLOBAL_SEARCH_CATEGORY_LABELS[cat] || cat;
          const count = globalSearchState.results[cat].length;
          const activeClass = index === 0 ? 'active' : '';
          return `<button class="tab-button ${activeClass}" onclick="renderGlobalSearchTab('${cat}')">${label} (${count})</button>`;
        })
        .join('');
      renderGlobalSearchTab(categoriesWithResults[0]);
    }
  } catch (error) {
    if (thisRequestToken === globalSearchRequestToken) {
      document.getElementById('global-search-tbody').innerHTML = `<tr><td>Error: ${error.message}</td></tr>`;
    }
  } finally {
    if (thisRequestToken === globalSearchRequestToken) {
      document.getElementById('global-search-loading').classList.add('hidden');
    }
  }
}

const GLOBAL_SEARCH_COLUMNS = {
  distributors: [
    ['firm_name', 'Firm Name'],
    ['buyer_code', 'Distributor Code'],
    ['contact_person', 'Contact Person'],
    ['phone_number', 'Phone'],
    ['city', 'City'],
    ['gst_no', 'GST No'],
    ['zone', 'Zone'],
    ['region', 'Region'],
    ['address', 'Address'],
  ],
  retailers: [
    ['name', 'Shop Name'],
    ['contact_person', 'Contact Person'],
    ['distributor_name', 'Distributor'],
    ['phone_number', 'Phone'],
    ['city', 'City'],
    ['gst_no', 'GST No'],
    ['address', 'Address'],
  ],
  orders: [
    ['order_ref_no', 'Order Ref No'],
    ['distributor_name', 'Distributor'],
    ['transit_status', 'Transit Status'],
    ['payment_status', 'Payment Status'],
  ],
  stock: [
    ['brand', 'Brand'],
    ['product', 'Product'],
    ['colors', 'Colors'],
    ['size', 'Size'],
  ],
  article_master: [
    ['brand', 'Brand'],
    ['size', 'Size'],
    ['category', 'Category'],
    ['product_type', 'Product'],
    ['mrp', 'MRP'],
    ['ptr', 'PTR'],
    ['ex_mill_price', 'Ex-Mill'],
    ['item_key', 'Item Key'],
  ],
};

function renderGlobalSearchTab(category) {
  globalSearchState.activeCategory = category;
  document.querySelectorAll('#global-search-tabs .tab-button').forEach((btn) => {
    btn.classList.toggle('active', btn.textContent.startsWith(GLOBAL_SEARCH_CATEGORY_LABELS[category] || category));
  });

  const rows = globalSearchState.results[category] || [];
  const thead = document.getElementById('global-search-thead');
  const tbody = document.getElementById('global-search-tbody');
  const columns = GLOBAL_SEARCH_COLUMNS[category];

  if (columns) {
    thead.innerHTML = `<tr>${columns.map(([, label]) => `<th>${label}</th>`).join('')}</tr>`;
    partyDetailRecordsCache = rows;
    const clickHandler = category === 'article_master'
      ? (index) => `openModule('ArticleMaster')`
      : (index) => `showPartyDetail(partyDetailRecordsCache[${index}])`;
    tbody.innerHTML = rows.length
      ? rows
          .map(
            (r, index) =>
              `<tr onclick="${clickHandler(index)}" style="cursor:pointer;">${columns
                .map(([key]) => {
                  let val = r[key];
                  if (['mrp', 'ptr', 'ex_mill_price'].includes(key) && val != null && val !== '') {
                    const num = Number(val);
                    val = Number.isFinite(num) ? num.toFixed(2) : val;
                  }
                  return `<td>${val != null && val !== '' ? val : '-'}</td>`;
                })
                .join('')}</tr>`
          )
          .join('')
      : `<tr><td colspan="${columns.length}">No results in this category.</td></tr>`;
  } else {
    // Fallback for categories that still use the simpler content-string shape
    thead.innerHTML = '<tr><th>Result</th></tr>';
    tbody.innerHTML = rows.length
      ? rows.map((r) => `<tr><td>${r.content}</td></tr>`).join('')
      : '<tr><td>No results in this category.</td></tr>';
  }
}

// ===================== Order Fulfillment =====================

async function loadCompanyProfileV2() {
  const nameInput = document.getElementById('of-company-name');
  const gstInput = document.getElementById('of-company-gst');
  const resultBox = document.getElementById('of-company-profile-result');
  try {
    const response = await fetchWithAuth('/api/v1/company-profile');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load company profile');
    }
    if (data.data) {
      nameInput.value = data.data.company_name || '';
      gstInput.value = data.data.gst_number || '';
      resultBox.textContent = 'Loaded saved company profile.';
    } else {
      resultBox.textContent = 'No company profile set yet — please fill this in and Save before uploading Sales Orders/Invoices.';
    }
  } catch (error) {
    resultBox.textContent = `Error loading company profile: ${error.message}`;
  }
}

async function saveCompanyProfileV2() {
  const name = document.getElementById('of-company-name').value.trim();
  const gst = document.getElementById('of-company-gst').value.trim();
  const resultBox = document.getElementById('of-company-profile-result');

  if (!name) {
    resultBox.textContent = 'Company Name is required.';
    return;
  }

  resultBox.textContent = 'Saving...';
  try {
    const response = await fetchWithAuth('/api/v1/company-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_name: name, gst_number: gst }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Save failed');
    }
    resultBox.textContent = `Saved: ${data.data.company_name} (GST: ${data.data.gst_number || 'not set'})`;
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

// ===================== Order Cycle (expandable/collapsible accordion) =====================
// Order Cycle -> Financial Year -> Distributor -> Order Sheet Name -> Filled Order / SO / CI
// Visual layer: .nx-theme (.nx-card / .nx-nav-item). No status pills in Step 1.

let orderCycleData = { financial_years: [] };
let ocExpandedFy = new Set();
let ocExpandedDistributor = new Set();
let ocExpandedSheet = new Set();

const OC_ICON_FULFILLMENT =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="9" width="12" height="9" rx="1.5"/><path d="M15 12h3.5L21 15v3h-6"/><circle cx="7.5" cy="20" r="1.6"/><circle cx="17" cy="20" r="1.6"/></svg>';
const OC_ICON_DISTRIBUTORS =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="6" r="2.3"/><circle cx="18" cy="6" r="2.3"/><circle cx="12" cy="17" r="2.3"/><path d="M7.6 7.6L11 15.2M16.4 7.6L13 15.2M8.3 6h7.4"/></svg>';
const OC_ICON_ANALYTICS =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 20V10M11 20V4M18 20v-7"/><path d="M2 20h20"/></svg>';
const OC_ICON_CHEVRON =
  '<svg class="nx-oc-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';

function ocJsString(value) {
  return String(value ?? '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'");
}

async function loadOrderCycleHierarchy() {
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/order-cycle');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load Order Cycle data');
    }
    orderCycleData = data.data;
    const container = document.getElementById('oc-accordion');
    const emptyState = document.getElementById('oc-empty-state');

    if (!orderCycleData.financial_years.length) {
      container.innerHTML = '';
      emptyState.classList.remove('hidden');
      return;
    }
    emptyState.classList.add('hidden');

    // First Financial Year open by default, so there's something to see immediately.
    if (!ocExpandedFy.size) {
      ocExpandedFy.add(orderCycleData.financial_years[0].fy);
    }
    renderOcAccordion();
  } catch (error) {
    document.getElementById('oc-accordion').innerHTML =
      `<div class="nx-oc-error">Error: ${foEscapeText(error.message)}</div>`;
  }
}

function ocToggle(setObj, key) {
  if (setObj.has(key)) {
    setObj.delete(key);
  } else {
    setObj.add(key);
  }
  renderOcAccordion();
}

function renderOcAccordion() {
  const container = document.getElementById('oc-accordion');
  container.innerHTML = orderCycleData.financial_years.map(renderOcFyFolder).join('');
}

function renderOcNavHeader(opts) {
  const { isOpen, iconSvg, label, meta, toggleExpr } = opts;
  const activeClass = isOpen ? ' active' : '';
  return `
    <button type="button" class="nx-nav-item${activeClass}" onclick="${toggleExpr}">
      ${iconSvg}
      <span class="nx-oc-label">${foEscapeText(label)}</span>
      <span class="nx-oc-meta">${foEscapeText(meta)}</span>
      ${OC_ICON_CHEVRON}
    </button>
  `;
}

function renderOcFyFolder(fyEntry) {
  const isOpen = ocExpandedFy.has(fyEntry.fy);
  const distributorCount = fyEntry.distributors.length;
  const meta = `${distributorCount} distributor${distributorCount === 1 ? '' : 's'}`;
  const body = isOpen
    ? `<div class="nx-oc-children">${
        fyEntry.distributors.map((d) => renderOcDistributorFolder(fyEntry.fy, d)).join('') ||
        '<div class="nx-text-dim nx-oc-file-empty">No distributors yet for this Financial Year.</div>'
      }</div>`
    : '';
  return `
    <div class="nx-card nx-oc-node">
      ${renderOcNavHeader({
        isOpen,
        iconSvg: OC_ICON_ANALYTICS,
        label: fyEntry.fy,
        meta,
        toggleExpr: `ocToggle(ocExpandedFy, '${ocJsString(fyEntry.fy)}')`,
      })}
      ${body}
    </div>
  `;
}

function renderOcDistributorFolder(fy, distributorEntry) {
  const key = `${fy}::${distributorEntry.name}`;
  const isOpen = ocExpandedDistributor.has(key);
  const sheetCount = distributorEntry.order_sheets.length;
  const meta = `${sheetCount} order sheet${sheetCount === 1 ? '' : 's'}`;
  const body = isOpen
    ? `<div class="nx-oc-children">${
        distributorEntry.order_sheets.map((s) => renderOcOrderSheetFolder(key, s)).join('') ||
        '<div class="nx-text-dim nx-oc-file-empty">No order sheets yet for this distributor.</div>'
      }</div>`
    : '';
  return `
    <div class="nx-card nx-oc-node">
      ${renderOcNavHeader({
        isOpen,
        iconSvg: OC_ICON_DISTRIBUTORS,
        label: distributorEntry.name,
        meta,
        toggleExpr: `ocToggle(ocExpandedDistributor, '${ocJsString(key)}')`,
      })}
      ${body}
    </div>
  `;
}

function renderOcFileGroup(title, files, emptyLabel) {
  const list = files?.length
    ? `<div class="nx-oc-file-list">${files.map((f) => renderOcFileLink(f)).join('')}</div>`
    : `<div class="nx-text-dim nx-oc-file-empty">${foEscapeText(emptyLabel)}</div>`;
  return `
    <div class="nx-oc-group">
      <div class="nx-oc-group-title">${OC_ICON_FULFILLMENT}<span>${foEscapeText(title)}</span></div>
      ${list}
    </div>
  `;
}

function renderOcOrderSheetFolder(distributorKey, sheet) {
  const key = `${distributorKey}::${sheet.name}`;
  const isOpen = ocExpandedSheet.has(key);
  const fileCount =
    (sheet.filled_order_files?.length || 0) +
    (sheet.so_files?.length || 0) +
    (sheet.ci_files?.length || 0) +
    (sheet.reconciliation_file ? 1 : 0);
  const meta = `${fileCount} file${fileCount === 1 ? '' : 's'}`;

  let body = '';
  if (isOpen) {
    const reconciliationList = sheet.reconciliation_file ? [sheet.reconciliation_file] : [];
    body = `
      <div class="nx-oc-children">
        <div class="nx-oc-leaf-block">
          ${renderOcFileGroup(
            'Given Order by Distributor (Filled Order)',
            sheet.filled_order_files,
            'None uploaded',
          )}
          ${renderOcFileGroup(
            `SO — creates against that order (${sheet.so_files.length})`,
            sheet.so_files,
            'None uploaded',
          )}
          ${renderOcFileGroup(
            `CI — creates against that order (${sheet.ci_files.length})`,
            sheet.ci_files,
            'None uploaded',
          )}
          ${renderOcFileGroup('Reconciliation Sheet', reconciliationList, 'Not generated yet')}
        </div>
      </div>
    `;
  }

  return `
    <div class="nx-card nx-oc-node">
      ${renderOcNavHeader({
        isOpen,
        iconSvg: OC_ICON_FULFILLMENT,
        label: sheet.name,
        meta,
        toggleExpr: `ocToggle(ocExpandedSheet, '${ocJsString(key)}')`,
      })}
      ${body}
    </div>
  `;
}

function renderOcFileLink(file) {
  const path = ocJsString(file.relative_path);
  return `
    <div class="nx-oc-file-row">
      <a class="nx-oc-file-link" href="#" onclick="openOrderFulfillmentFile('${path}'); return false;">${foEscapeText(file.name)}</a>
      <button type="button" class="nx-oc-btn-delete" onclick="deleteOrderFulfillmentFile('${path}')">Delete</button>
    </div>
  `;
}

async function uploadOrderSheetV2() {
  const fileInput = document.getElementById('of-order-sheet-file');
  const name = document.getElementById('of-order-sheet-name').value.trim();
  const category = document.getElementById('of-order-sheet-category').value.trim();
  const resultBox = document.getElementById('of-order-sheet-result');

  if (!fileInput.files.length) {
    resultBox.textContent = 'Please choose a file first.';
    return;
  }
  if (!name || !category) {
    resultBox.textContent = 'Name and Category are required.';
    return;
  }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('name', name);
  formData.append('category', category);

  resultBox.textContent = 'Uploading...';
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/upload/order-sheet', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Upload failed');
    }
    resultBox.textContent = `Saved: "${data.data.name}" (${data.data.category})`;
    fileInput.value = '';
    loadOrderFulfillmentUploads();
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

let ofFilledOrderPendingFile = null;

async function uploadFilledOrderV2(confirmedDistributorId) {
  const fileInput = document.getElementById('of-filled-order-file');
  const resultBox = document.getElementById('of-filled-order-result');

  const file = confirmedDistributorId ? ofFilledOrderPendingFile : fileInput.files[0];
  if (!file) {
    resultBox.textContent = 'Please choose a file first.';
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  if (confirmedDistributorId) {
    formData.append('distributor_id', confirmedDistributorId);
  }

  resultBox.textContent = 'Uploading...';
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/upload/filled-order', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Upload failed');
    }

    if (data.data.requires_confirmation) {
      ofFilledOrderPendingFile = file;
      const suggestion = data.data.suggested_distributor;
      if (suggestion) {
        resultBox.innerHTML = `
          <div style="color: #FFD97D;">Is this order for <strong>${suggestion.firm_name || suggestion.name}</strong>?</div>
          <button class="btn btn-primary" onclick="uploadFilledOrderV2(${suggestion.id})">Confirm</button>
          <button class="btn btn-secondary" onclick="document.getElementById('of-filled-order-result').textContent='Cancelled.'">No</button>
        `;
      } else {
        resultBox.textContent = 'No distributor could be suggested from the filename. Please link this manually.';
      }
    } else {
      resultBox.textContent = `Linked to ${data.data.confirmed_distributor.firm_name || data.data.confirmed_distributor.name}.`;
      ofFilledOrderPendingFile = null;
      fileInput.value = '';
    }
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

let ofSalesOrderPendingFile = null;
let ofSalesOrderPendingFilledOrderId = null;
let ofSalesOrderRequestInFlight = false;

async function uploadSalesOrderV2(confirmedDistributorId, confirmedFilledOrderId) {
  if (ofSalesOrderRequestInFlight) {
    return; // A request is already in progress — ignore extra clicks
             // instead of firing a second, near-simultaneous request
             // (which the server correctly rejects as a duplicate,
             // but was previously mislabeled as a false "Linked").
  }
  ofSalesOrderRequestInFlight = true;
  try {
    await _uploadSalesOrderV2Impl(confirmedDistributorId, confirmedFilledOrderId);
  } finally {
    ofSalesOrderRequestInFlight = false;
  }
}

async function _uploadSalesOrderV2Impl(confirmedDistributorId, confirmedFilledOrderId) {
  const fileInput = document.getElementById('of-sales-order-file');
  const resultBox = document.getElementById('of-sales-order-result');

  const file = confirmedDistributorId ? ofSalesOrderPendingFile : fileInput.files[0];
  if (!file) {
    resultBox.textContent = 'Please choose a file first.';
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  if (confirmedDistributorId) {
    formData.append('distributor_id', confirmedDistributorId);
  }
  const filledOrderId = confirmedFilledOrderId || ofSalesOrderPendingFilledOrderId;
  if (filledOrderId) {
    formData.append('filled_order_id', filledOrderId);
  }

  resultBox.textContent = 'Uploading and parsing...';
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/upload/sales-order', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Upload failed');
    }
    const d = data.data;

    // Duplicate rejection and any other link_error both come back with
    // requires_confirmation: false (same as a genuine successful link),
    // so tracking_id is the only reliable signal that linking actually
    // happened. Without this check, a duplicate-rejected SO showed the
    // same "Linked" message as a real one, just with tracking_id
    // missing — hiding the real "already processed" reason from the user.
    if (d.is_duplicate || d.link_error) {
      resultBox.innerHTML = `<div style="color: #FF6B6B; font-weight: bold;">${d.link_error || 'This Sales Order could not be linked.'}</div>`;
      ofSalesOrderPendingFile = null;
      fileInput.value = '';
      return;
    }

    if (!d.requires_confirmation && d.tracking_id) {
      const discrepancyAlert = d.has_discrepancy
        ? `<div style="color: #FF6B6B; font-weight: bold; margin-top: 8px;">⚠ DISCREPANCY DETECTED — one or more items don't match across Ordered/SO/CI quantities or values. Check the reconciliation sheet for this distributor.</div>`
        : '';
      let filledOrderPrompt = '';
      if (d.requires_filled_order_confirmation && d.suggested_filled_order) {
        const fo = d.suggested_filled_order;
        ofSalesOrderPendingFilledOrderId = fo.id;
        filledOrderPrompt = `
          <div style="color:#FFD97D; margin-top:8px;">
            Link Filled Order <strong>${fo.category} / ${fo.season}</strong> (${fo.source_filename || 'uploaded file'}) as Ordered qty?
            <br><button class="btn btn-primary" style="margin-top:6px;" onclick="uploadSalesOrderV2(${confirmedDistributorId || 'null'}, ${fo.id})">Use this Filled Order</button>
            <button class="btn btn-secondary" style="margin-top:6px;" onclick="ofSalesOrderPendingFilledOrderId=null">Skip</button>
          </div>`;
      } else {
        ofSalesOrderPendingFilledOrderId = null;
      }
      resultBox.innerHTML = `Linked. Tracking #${d.tracking_id} for Order Ref "${d.order_ref_no}".${discrepancyAlert}${filledOrderPrompt}`;
      if (!d.requires_filled_order_confirmation) {
        ofSalesOrderPendingFile = null;
        fileInput.value = '';
      }
      loadOrderFulfillmentUploads();
      return;
    }

    ofSalesOrderPendingFile = file;
    const byCode = d.matched_by_buyer_code;
    const byGst = d.matched_by_gst;

    if (d.signals_agree === false) {
      resultBox.innerHTML = `
        <strong>Warning:</strong> Buyer Code suggests "${byCode.firm_name || byCode.name}",
        but GST suggests "${byGst.firm_name || byGst.name}". These disagree — please pick the correct one.
        <br><button class="btn btn-primary" onclick="uploadSalesOrderV2(${byCode.id})">Use ${byCode.firm_name || byCode.name}</button>
        <button class="btn btn-primary" onclick="uploadSalesOrderV2(${byGst.id})">Use ${byGst.firm_name || byGst.name}</button>
      `;
    } else if (byCode || byGst) {
      const match = byCode || byGst;
      const agreeNote = d.signals_agree ? ' (Buyer Code and GST both matched)' : '';
      resultBox.innerHTML = `
        <div style="color: #FFD97D;">Is this SO for <strong>${match.firm_name || match.name}</strong>?${agreeNote}</div>
        <button class="btn btn-primary" onclick="uploadSalesOrderV2(${match.id})">Confirm</button>
      `;
    } else {
      resultBox.textContent = `No distributor could be matched (Order Ref: "${d.order_ref_no || 'not found'}"). Please link this manually.`;
    }
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

let ofInvoicePendingLink = null;

async function uploadInvoiceV2() {
  const fileInput = document.getElementById('of-invoice-file');
  const resultBox = document.getElementById('of-invoice-result');

  if (!fileInput.files.length) {
    resultBox.textContent = 'Please choose a file first.';
    return;
  }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  resultBox.textContent = 'Uploading and parsing...';
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/upload/invoice', {
      method: 'POST',
      body: formData,
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Upload failed');
    }
    const d = data.data;

    if (d.no_match_found) {
      resultBox.textContent = `No matching Sales Order found for Order Ref "${d.order_ref_no || 'not found'}". Please make sure the Sales Order was uploaded first.`;
      return;
    }

    ofInvoicePendingLink = {
      order_ref_no: d.order_ref_no,
      commercial_invoice_file_reference: d.commercial_invoice_file_reference,
      commercial_invoice_parsed: d.commercial_invoice_parsed,
    };

    const partyName = d.distributor_name || 'an unknown party';
    const amountValue = d.extracted_amount != null ? d.extracted_amount : '';
    const amountNote = d.extracted_amount != null
      ? '(auto-extracted from the invoice — adjust if needed)'
      : '(could not auto-read the amount — please enter it)';

    resultBox.innerHTML = `
      <div style="color: #7CFC7C;">
        Is this Commercial Invoice for <strong>${partyName}</strong>?
      </div>
      <div class="form-group">
        <label>Invoice Amount (₹) <span style="color: #888; font-weight: normal;">${amountNote}</span></label>
        <input type="number" id="of-invoice-amount" step="0.01" value="${amountValue}" />
      </div>
      <button class="btn btn-primary" onclick="confirmCiLinkV2()">Confirm — Yes, ${partyName}</button>
    `;
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

let ofCiConfirmRequestInFlight = false;

async function confirmCiLinkV2() {
  if (ofCiConfirmRequestInFlight) {
    return; // Ignore extra clicks while a request is already in flight
             // — see the matching guard on uploadSalesOrderV2 for why.
  }
  ofCiConfirmRequestInFlight = true;
  try {
    await _confirmCiLinkV2Impl();
  } finally {
    ofCiConfirmRequestInFlight = false;
  }
}

async function _confirmCiLinkV2Impl() {
  const resultBox = document.getElementById('of-invoice-result');
  if (!ofInvoicePendingLink) {
    resultBox.textContent = 'Nothing pending to confirm.';
    return;
  }
  const amountInput = document.getElementById('of-invoice-amount');
  const amount = amountInput ? parseFloat(amountInput.value) : null;

  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/confirm-ci-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        order_ref_no: ofInvoicePendingLink.order_ref_no,
        commercial_invoice_file_reference: ofInvoicePendingLink.commercial_invoice_file_reference,
        commercial_invoice_parsed: ofInvoicePendingLink.commercial_invoice_parsed,
        amount: isNaN(amount) ? null : amount,
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Confirm failed');
    }
    const d = data.data;

    if (d.is_duplicate || d.link_error) {
      resultBox.innerHTML = `<div style="color: #FF6B6B; font-weight: bold;">${d.link_error || 'This Commercial Invoice could not be linked.'}</div>`;
      ofInvoicePendingLink = null;
      return;
    }

    const discrepancyAlert = d.has_discrepancy
      ? `<div style="color: #FF6B6B; font-weight: bold; margin-top: 8px;">⚠ DISCREPANCY DETECTED — one or more items don't match across Ordered/SO/CI quantities or values. Check the reconciliation sheet for this distributor.</div>`
      : '';
    resultBox.innerHTML = `Linked! Tracking #${d.tracking_id}` +
      (d.achievement_id ? `, Achievement #${d.achievement_id} recorded.` : '.') +
      discrepancyAlert;
    ofInvoicePendingLink = null;
    document.getElementById('of-invoice-file').value = '';
    loadOrderFulfillmentUploads();
  } catch (error) {
    resultBox.textContent = `Error: ${error.message}`;
  }
}

async function deleteOrderFulfillmentTracking(trackingId, orderRefNo) {
  if (!(await nexoraConfirm(`Delete tracking for Order Ref "${orderRefNo}"? This removes the SO/CI link, item reconciliation, and their files. This cannot be undone.`, {
    title: 'Delete tracking',
    danger: true,
    okText: 'Delete',
  }))) {
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/tracking/${trackingId}`, {
      method: 'DELETE',
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Delete failed');
    }
    loadOrderFulfillmentUploads();
    loadOrderFulfillmentFileBrowser();
  } catch (error) {
    alert(`Error deleting: ${error.message}`);
  }
}

async function loadOrderFulfillmentCatalogSummary() {
  const amSummaryEl = document.getElementById('of-am-summary');
  const foSummaryEl = document.getElementById('of-fo-summary');
  if (!amSummaryEl && !foSummaryEl) return;

  try {
    const [amResp, foResp] = await Promise.all([
      fetchWithAuth('/api/v1/article-master/list'),
      fetchWithAuth('/api/v1/filled-orders/list'),
    ]);
    const amData = await parseApiResponse(amResp);
    const foData = await parseApiResponse(foResp);
    if (!amResp.ok) {
      throw new Error(getApiErrorMessage(amData, 'Article Master load failed'));
    }
    if (!foResp.ok) {
      throw new Error(getApiErrorMessage(foData, 'Filled orders load failed'));
    }

    const articles = amData.articles || [];
    const orders = foData.filled_orders || [];
    const catCounts = {};
    articles.forEach((a) => {
      catCounts[a.category] = (catCounts[a.category] || 0) + 1;
    });
    const catParts = Object.entries(catCounts)
      .map(([category, count]) => `${category}: ${count}`)
      .join(' · ');

    if (amSummaryEl) {
      amSummaryEl.textContent = articles.length
        ? `${articles.length} article(s) in catalog${catParts ? ` (${catParts})` : ''}`
        : 'No articles yet — upload a booking form above.';
    }

    if (foSummaryEl) {
      const latest = orders[0];
      foSummaryEl.textContent = orders.length
        ? `${orders.length} filled order(s) saved` +
          (latest
            ? ` · Latest: ${latest.category || '—'} · ${(latest.created_at || '').slice(0, 10)}`
            : '')
        : 'No filled orders yet — upload distributor Excel above.';
    }
  } catch (error) {
    const msg = error.message || 'Failed to load';
    if (amSummaryEl) amSummaryEl.textContent = msg;
    if (foSummaryEl) foSummaryEl.textContent = msg;
  }
}

async function initOrderFulfillmentEmbeddedPanels() {
  setFilledOrderUploadFieldsEnabled(false, 'of-fo');
  await loadFilledOrdersDistributors(['fo', 'of-fo']);
  await loadOrderFulfillmentCatalogSummary();
}

async function loadOrderFulfillmentUploads() {
  const trackingBody = document.getElementById('of-tracking-tbody');
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/uploads');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load uploads');
    }

    const tracking = data.data.tracking_records || [];
    if (trackingBody) {
      trackingBody.innerHTML = tracking.length
        ? tracking
            .map(
              (t) => `<tr>
              <td>${t.order_ref_no || '-'}</td>
              <td>${t.distributor_name || '-'}</td>
              <td>${t.has_sales_order ? 'Yes' : 'No'}</td>
              <td>${t.has_commercial_invoice ? 'Yes' : 'No'}</td>
              <td>${t.payment_status || '-'}</td>
              <td>${t.transit_status || '-'}</td>
              <td><button onclick="deleteOrderFulfillmentTracking(${t.tracking_id}, '${(t.order_ref_no || '').replace(/'/g, "\\'")}')" class="btn btn-danger" style="padding: 2px 10px; font-size: 0.85rem;">Delete</button></td>
            </tr>`
            )
            .join('')
        : '<tr><td colspan="7">No Sales Orders/Invoices tracked yet.</td></tr>';
    }
  } catch (error) {
    if (trackingBody) {
      trackingBody.innerHTML = `<tr><td colspan="7">Error: ${error.message}</td></tr>`;
    }
  }
}

function renderFileBrowserNode(node, depth) {
  const indent = '&nbsp;&nbsp;'.repeat(depth * 2);
  if (node.type === 'file') {
    const sizeKb = node.size_bytes ? (node.size_bytes / 1024).toFixed(1) + ' KB' : '';
    return `<div>${indent}📄 <a href="#" onclick="openOrderFulfillmentFile('${node.relative_path}'); return false;" style="color: #6FB6FF; text-decoration: underline; cursor: pointer;">${node.name}</a> <span style="color: #888;">${sizeKb}</span> <button onclick="deleteOrderFulfillmentFile('${node.relative_path}')" class="btn btn-danger" style="padding: 1px 8px; font-size: 0.8rem; margin-left: 8px;">Delete</button></div>`;
  }
  const childrenHtml = (node.children || []).map((child) => renderFileBrowserNode(child, depth + 1)).join('');
  const isEmpty = !node.children || node.children.length === 0;
  return `<div>${indent}📁 <strong>${node.name}</strong>${isEmpty ? ' <span style="color: #888;">(empty)</span>' : ''}</div>${childrenHtml}`;
}

async function deleteOrderFulfillmentFile(relativePath) {
  if (!(await nexoraConfirm(`Delete "${relativePath.split('/').pop()}"? This cannot be undone.`, {
    title: 'Delete file',
    danger: true,
    okText: 'Delete',
  }))) {
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/file?path=${encodeURIComponent(relativePath)}`, {
      method: 'DELETE',
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Delete failed');
    }
    // This delete button is shared by two different views (the Order
    // Cycle accordion and the flat file browser) — refresh whichever
    // one(s) are actually present on the page instead of only ever
    // refreshing the file browser. Without this, deleting a file from
    // the Order Cycle accordion succeeded on the backend but the
    // accordion kept showing the old (deleted) file, looking exactly
    // like the delete had silently failed.
    if (typeof loadOrderCycleHierarchy === 'function') {
      loadOrderCycleHierarchy();
    }
    if (typeof loadOrderFulfillmentFileBrowser === 'function') {
      loadOrderFulfillmentFileBrowser();
    }
  } catch (error) {
    alert(`Error deleting file: ${error.message}`);
  }
}

async function openOrderFulfillmentFile(relativePath) {
  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/file?path=${encodeURIComponent(relativePath)}`);
    if (!response.ok) {
      alert('Could not open file — it may have been moved or deleted.');
      return;
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    window.open(objectUrl, '_blank');
  } catch (error) {
    alert(`Error opening file: ${error.message}`);
  }
}

async function loadOrderFulfillmentFileBrowser() {
  const container = document.getElementById('of-file-browser');
  container.textContent = 'Loading...';
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/file-browser');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load folders');
    }
    const tree = data.data;
    if (!tree.children || !tree.children.length) {
      container.textContent = 'No files uploaded yet — folders will appear here once you upload something above.';
      return;
    }
    container.innerHTML = tree.children.map((child) => renderFileBrowserNode(child, 0)).join('');
  } catch (error) {
    container.textContent = `Error: ${error.message}`;
  }
}

// --- Article Master (per-user catalog) ---

const articleMasterState = {
  articles: [],
  pendingUploadFile: null,
  editArticleId: null,
  editField: null,
};

function formatArticleMasterValue(value, field = null) {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (['mrp', 'ptr', 'ex_mill_price'].includes(field)) {
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(2) : value;
  }
  return value;
}

function updateArticleMasterStats() {
  const articles = articleMasterState.articles;
  const counts = { Bed: 0, Bath: 0, TOB: 0, 'TOB Pillow': 0 };
  articles.forEach((a) => {
    if (counts[a.category] !== undefined) {
      counts[a.category] += 1;
    }
  });
  const totalEl = document.getElementById('am-stat-total');
  if (totalEl) totalEl.textContent = String(articles.length);
  const bedEl = document.getElementById('am-stat-bed');
  if (bedEl) bedEl.textContent = String(counts.Bed);
  const bathEl = document.getElementById('am-stat-bath');
  if (bathEl) bathEl.textContent = String(counts.Bath);
  const tobEl = document.getElementById('am-stat-tob');
  if (tobEl) tobEl.textContent = String(counts.TOB);
  const pillowEl = document.getElementById('am-stat-pillow');
  if (pillowEl) pillowEl.textContent = String(counts['TOB Pillow']);
}

function populateArticleMasterCategoryFilter() {
  const select = document.getElementById('am-category-filter');
  if (!select) return;
  const current = select.value || 'All';
  const categories = [...new Set(articleMasterState.articles.map((a) => a.category))].sort();
  select.innerHTML = '<option value="All">All</option>';
  categories.forEach((cat) => {
    const opt = document.createElement('option');
    opt.value = cat;
    opt.textContent = cat;
    select.appendChild(opt);
  });
  if ([...select.options].some((o) => o.value === current)) {
    select.value = current;
  }
}

function getFilteredArticleMasterRows() {
  const category = document.getElementById('am-category-filter')?.value || 'All';
  const query = (document.getElementById('am-search-filter')?.value || '').trim().toLowerCase();
  const filtered = articleMasterState.articles.filter((a) => {
    if (category !== 'All' && a.category !== category) {
      return false;
    }
    if (!query) {
      return true;
    }
    const haystack = [
      a.category, a.brand, a.size, a.product_type, a.item_key,
    ].join(' ').toLowerCase();
    return haystack.includes(query);
  });
  return filtered.sort((a, b) => {
    const brandCmp = String(a.brand || '').localeCompare(String(b.brand || ''), undefined, { sensitivity: 'base' });
    if (brandCmp !== 0) return brandCmp;
    const sizeCmp = String(a.size || '').localeCompare(String(b.size || ''), undefined, { sensitivity: 'base' });
    if (sizeCmp !== 0) return sizeCmp;
    return String(a.item_key || '').localeCompare(String(b.item_key || ''), undefined, { sensitivity: 'base' });
  });
}

function renderArticleMasterTable() {
  const tbody = document.getElementById('am-articles-tbody');
  const countEl = document.getElementById('am-list-count');
  if (!tbody) return;

  const rows = getFilteredArticleMasterRows();
  if (countEl) {
    countEl.textContent = `${rows.length} article${rows.length === 1 ? '' : 's'}`;
  }

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10">No articles found. Upload a booking form Excel to get started.</td></tr>';
    return;
  }

  tbody.innerHTML = rows.map((a) => {
    const historyBtn = a.has_price_history
      ? `<button class="btn btn-secondary" style="padding:4px 8px;font-size:0.75rem;" onclick="openArticleMasterPriceHistory(${a.id})">History</button>`
      : '';
    return `
    <tr>
      <td>${a.category || '—'}</td>
      <td>${formatArticleMasterValue(a.brand)}</td>
      <td>${formatArticleMasterValue(a.size)}</td>
      <td>${formatArticleMasterValue(a.product_type)}</td>
      <td>${formatArticleMasterValue(a.mrp, 'mrp')}</td>
      <td>${formatArticleMasterValue(a.ptr, 'ptr')}</td>
      <td>${formatArticleMasterValue(a.ex_mill_price, 'ex_mill_price')}</td>
      <td>${formatArticleMasterValue(a.bale_pack_size)}</td>
      <td style="font-size:0.75rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;" title="${(a.item_key || '').replace(/"/g, '&quot;')}">${a.item_key || '—'}</td>
      <td>
          <button class="btn btn-primary" style="padding:4px 8px;font-size:0.75rem;" onclick="openArticleMasterFullEdit(${a.id})">Edit</button>
          ${historyBtn}
          <button class="btn btn-danger" style="padding:4px 8px;font-size:0.75rem;margin-left:4px;" onclick="deleteOneArticleMaster(${a.id})">Delete</button>
      </td>
    </tr>
  `;
  }).join('');
}

async function loadArticleMasterList() {
  const tbody = document.getElementById('am-articles-tbody');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="10">Loading...</td></tr>';
  }

  try {
    const response = await fetchWithAuth('/api/v1/article-master/list');
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load articles'));
    }
    articleMasterState.articles = data.articles || [];
    updateArticleMasterStats();
    populateArticleMasterCategoryFilter();
    renderArticleMasterTable();
  } catch (error) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="10">${error.message || 'Failed to load'}</td></tr>`;
    }
  }
}

async function parseApiResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }
  const text = await response.text();
  if (response.status === 405) {
    throw new Error('Save is not available on this server session — restart Flask and hard-refresh (Ctrl+Shift+R).');
  }
  throw new Error(
    text.startsWith('<!') ? 'Server error — please reload the page and try again.' : (text.slice(0, 200) || 'Invalid server response')
  );
}

function showArticleMasterCategoryModal(data) {
  return new Promise((resolve) => {
    const detectedCategory = data.detected_category || 'Bed';
    const breakdown = data.category_breakdown || {};
    const totalRows = data.article_count || 0;
    const knownCategories = ['Bed', 'Bath', 'TOB', 'TOB Pillow'];

    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';

    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 440px; width: 90%; ' +
      'box-shadow: 0 12px 40px rgba(0,0,0,0.5); font-family: inherit; color: #e6e6e6;';

    const breakdownRows = Object.entries(breakdown)
      .map(([cat, count]) => (
        '<div style="display:flex; justify-content:space-between; padding:4px 0; border-bottom:1px solid #23232b;">' +
        `<span style="color:#aaa;">${cat}</span>` +
        `<span style="color:#e6e6e6; font-weight:600;">${count}</span>` +
        '</div>'
      )).join('');

    box.innerHTML =
      '<div style="font-size:16px; font-weight:600; margin-bottom:6px; color:#e0b84a;">Confirm category</div>' +
      `<div style="font-size:13px; color:#999; margin-bottom:14px; line-height:1.5;">${data.message || 'Each row will be saved under its own category.'}</div>` +
      `<div style="font-size:12px; color:#999; margin-bottom:4px;">Suggested (majority): <strong style="color:#e6e6e6;">${detectedCategory}</strong></div>` +
      `<div style="font-size:12px; color:#999; margin:8px 0 4px;">Per-row mix (${totalRows} rows total):</div>` +
      `<div style="margin-bottom:16px;">${breakdownRows || '<div style="color:#666;font-size:12px;">-</div>'}</div>` +
      '<div id="am-modal-auto-btn" style="background:#2563eb; color:#fff; text-align:center; padding:10px; border-radius:8px; cursor:pointer; font-weight:600; margin-bottom:10px;">AUTO — save each row under its own category (recommended)</div>' +
      '<div style="font-size:11px; color:#777; margin-bottom:6px;">Or force one category (all rows will use it):</div>' +
      '<div id="am-modal-force-btns" style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px;"></div>' +
      '<div id="am-modal-cancel-btn" style="text-align:center; padding:8px; border-radius:8px; cursor:pointer; color:#999; border:1px solid #333;">Cancel</div>';

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const forceContainer = box.querySelector('#am-modal-force-btns');
    knownCategories.forEach((cat) => {
      const btn = document.createElement('div');
      btn.textContent = cat;
      btn.style.cssText =
        'flex: 1 1 auto; text-align:center; padding:8px 10px; border-radius:8px; ' +
        'cursor:pointer; font-size:13px; border:1px solid #333; color:#ccc; min-width: 70px;';
      btn.addEventListener('mouseenter', () => { btn.style.borderColor = '#e0b84a'; btn.style.color = '#e0b84a'; });
      btn.addEventListener('mouseleave', () => { btn.style.borderColor = '#333'; btn.style.color = '#ccc'; });
      btn.addEventListener('click', () => { cleanup(); resolve(cat); });
      forceContainer.appendChild(btn);
    });

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve(null); }
    }
    document.addEventListener('keydown', onKeydown);

    box.querySelector('#am-modal-auto-btn').addEventListener('click', () => {
      cleanup(); resolve('AUTO');
    });
    box.querySelector('#am-modal-cancel-btn').addEventListener('click', () => {
      cleanup(); resolve(null);
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { cleanup(); resolve(null); }
    });
  });
}

const articleMasterUploadState = {
  pendingFile: null,
  pendingCategory: null,
  pendingConflicts: [],
  resolutions: {},
  uiPrefix: null,
};

function getArticleMasterConflictKey(conflict, arrayIdx = 0) {
  const idx = conflict?.upload_index;
  if (idx !== undefined && idx !== null && idx !== '') {
    return String(idx);
  }
  return `row-${arrayIdx}`;
}

function getArticleMasterConflictFromCard(btn) {
  const card = btn?.closest?.('.am-conflict-card');
  if (!card) return null;
  const arrayIdx = Number(card.dataset.arrayIdx);
  if (!Number.isFinite(arrayIdx) || arrayIdx < 0) return null;
  const conflict = articleMasterUploadState.pendingConflicts[arrayIdx];
  if (!conflict) return null;
  return { conflict, arrayIdx };
}

function isArticleMasterConflictResolved(conflict, arrayIdx = 0) {
  const action = articleMasterUploadState.resolutions[getArticleMasterConflictKey(conflict, arrayIdx)];
  return action === 'replace' || action === 'create_new' || action === 'skip';
}

function remainingArticleMasterConflictsCount() {
  const seenKeys = new Set();
  let remaining = 0;
  articleMasterUploadState.pendingConflicts.forEach((conflict, arrayIdx) => {
    const key = getArticleMasterConflictKey(conflict, arrayIdx);
    if (seenKeys.has(key)) return;
    seenKeys.add(key);
    if (!isArticleMasterConflictResolved(conflict, arrayIdx)) {
      remaining += 1;
    }
  });
  return remaining;
}

function setArticleMasterConflictResolution(conflict, arrayIdx, action) {
  articleMasterUploadState.resolutions[getArticleMasterConflictKey(conflict, arrayIdx)] = action;
}

function applyArticleMasterConflictActionToAllRemaining(action) {
  const seenKeys = new Set();
  articleMasterUploadState.pendingConflicts.forEach((conflict, arrayIdx) => {
    const key = getArticleMasterConflictKey(conflict, arrayIdx);
    if (seenKeys.has(key)) return;
    seenKeys.add(key);
    if (!isArticleMasterConflictResolved(conflict, arrayIdx)) {
      setArticleMasterConflictResolution(conflict, arrayIdx, action);
    }
  });
}

function formatArticleMasterValue(val) {
  if (val === null || val === undefined || val === '') return '—';
  if (typeof val === 'number') return Number.isInteger(val) ? String(val) : val.toFixed(2);
  return String(val);
}

function formatArticleMasterComparisonStatus(status) {
  const map = {
    match: '<span style="color:#7fdc7f;">Match</span>',
    mismatch: '<span style="color:#f87171;">Mismatch</span>',
    missing_in_file: '<span style="color:#ffb648;">Missing in file</span>',
    missing_in_master: '<span style="color:#ffb648;">Missing in Article Master</span>',
    both_empty: '<span style="color:#888;">Empty</span>',
  };
  return map[status] || status;
}

function formatArticleMasterPriceChange(change) {
  if (!change || change.direction === 'same') {
    return '<span style="color:#888;">—</span>';
  }
  const pct = change.pct != null ? ` (${change.pct > 0 ? '+' : ''}${change.pct}%)` : '';
  if (change.direction === 'increase') {
    return `<span style="color:#7fdc7f;">↑ ${formatArticleMasterValue(change.delta)}${pct}</span>`;
  }
  if (change.direction === 'decrease') {
    return `<span style="color:#f87171;">↓ ${formatArticleMasterValue(Math.abs(change.delta))}${pct}</span>`;
  }
  return '<span style="color:#ffb648;">Changed</span>';
}

function renderArticleMasterConflictCard(conflict, arrayIdx = 0) {
  const key = getArticleMasterConflictKey(conflict, arrayIdx);
  const resolved = articleMasterUploadState.resolutions[key];
  const label = [
    formatArticleMasterValue(conflict.brand),
    formatArticleMasterValue(conflict.size),
    formatArticleMasterValue(conflict.product_type),
  ].filter((v) => v && v !== '—').join(' · ') || 'Unknown item';

  const keyRows = (conflict.field_comparisons || []).map((c) => (
    '<tr>' +
    `<td style="padding:6px 8px; color:#ccc;">${c.field}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterValue(c.upload_value)}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterValue(c.existing_value)}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterComparisonStatus(c.status)}</td>` +
    '</tr>'
  )).join('');

  const priceRows = (conflict.price_comparisons || []).map((c) => (
    '<tr>' +
    `<td style="padding:6px 8px; color:#ccc;">${c.field}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterValue(c.existing_value)}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterValue(c.upload_value)}</td>` +
    `<td style="padding:6px 8px;">${formatArticleMasterPriceChange(c.change)}</td>` +
    '</tr>'
  )).join('');

  const tableHead =
    '<thead><tr style="color:#888; border-bottom:1px solid #333;">' +
    '<th style="text-align:left; padding:6px 8px;">Field</th>' +
    '<th style="text-align:left; padding:6px 8px;">Article Master</th>' +
    '<th style="text-align:left; padding:6px 8px;">New file</th>' +
    '<th style="text-align:left; padding:6px 8px;">Change</th>' +
    '</tr></thead>';

  const keyTable = keyRows
    ? `<div style="font-size:11px; color:#888; margin-top:10px;">Identity fields</div>` +
      `<table style="width:100%; border-collapse:collapse; font-size:12px; margin-top:4px;">${tableHead}<tbody>${keyRows}</tbody></table>`
    : '';

  const priceTable = priceRows
    ? `<div style="font-size:11px; color:#888; margin-top:10px;">Price revision (season update)</div>` +
      `<table style="width:100%; border-collapse:collapse; font-size:12px; margin-top:4px;">${tableHead}<tbody>${priceRows}</tbody></table>`
    : '';

  const dupNote = (conflict.duplicate_ids && conflict.duplicate_ids.length)
    ? `<div style="color:#ffb648; font-size:11px; margin-top:6px;">${conflict.duplicate_ids.length} extra duplicate row(s) in Article Master — Replace will merge into one.</div>`
    : '';
  const recommend = conflict.recommended_action === 'replace'
    ? '<div style="color:#93c5fd; font-size:11px; margin-top:8px;">Recommended: <strong>Replace with new prices</strong> — applies season update and removes duplicate rows.</div>'
    : '';

  const resolvedBadge = resolved
    ? `<div style="margin-top:10px; font-size:12px; color:#7fdc7f;">Resolved: ${resolved === 'replace' ? 'Replace existing' : resolved === 'create_new' ? 'Create new entry' : 'Skip'}</div>`
    : '';

  const createBtn = conflict.can_create_new
    ? `<button type="button" class="btn btn-secondary am-conflict-create" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px;">Create new entry</button>`
    : '';

  return (
    `<div class="am-conflict-card" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="padding:14px; margin-bottom:12px; border:1px solid ${resolved ? '#1f3d2a' : '#2a2a33'}; border-radius:8px; background:${resolved ? '#0d1510' : '#101015'}; ${resolved ? 'opacity:0.82;' : ''}">` +
    `<div style="font-size:13px; font-weight:600; color:#e6e6e6;">${label}</div>` +
    `<div style="font-size:11px; color:#888; margin-top:4px;">Category: ${conflict.category || '—'} · File row: ${conflict.upload_index != null ? Number(conflict.upload_index) + 1 : arrayIdx + 1} · File key: ${conflict.upload_item_key || '—'}</div>` +
    `<div style="font-size:11px; color:#888;">Existing key: ${conflict.existing_item_key || '—'}</div>` +
    `<div style="color:#fca5a5; font-size:12px; margin-top:8px;">${conflict.issue_summary || 'Seasonal price revision'}</div>` +
    dupNote + recommend +
    keyTable + priceTable + resolvedBadge +
    `<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px;">` +
    (resolved ? '' :
      `<button type="button" class="btn btn-primary am-conflict-replace" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px;">Replace with new prices</button>` +
      createBtn +
      `<button type="button" class="btn btn-secondary am-conflict-skip" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px; color:#999;">Skip this row</button>`
    ) +
    `</div></div>`
  );
}

function bindArticleMasterConflictActions(container, rerender) {
  if (container.dataset.actionsBound === '1') {
    return;
  }
  container.dataset.actionsBound = '1';

  container.addEventListener('click', async (event) => {
    const replaceBtn = event.target.closest('.am-conflict-replace');
    const createBtn = event.target.closest('.am-conflict-create');
    const skipBtn = event.target.closest('.am-conflict-skip');
    const btn = replaceBtn || createBtn || skipBtn;
    if (!btn) return;

    const target = getArticleMasterConflictFromCard(btn);
    if (!target) return;
    const { conflict, arrayIdx } = target;

    if (isArticleMasterConflictResolved(conflict, arrayIdx)) {
      return;
    }

    if (replaceBtn) {
      const ok = await showSimpleConfirmModal(
        'Replace with new season prices?',
        'This will update Article Master with the uploaded file prices (MRP / PTR / Ex-Mill).',
        'Replace',
        'Cancel'
      );
      if (!ok) return;
      setArticleMasterConflictResolution(conflict, arrayIdx, 'replace');
      rerender();
      container.scrollTop = 0;
      return;
    }

    if (createBtn) {
      const ok = await showSimpleConfirmModal(
        'Create new entry?',
        'This will keep the existing row and add a separate article with the uploaded prices.',
        'Create new',
        'Cancel'
      );
      if (!ok) return;
      setArticleMasterConflictResolution(conflict, arrayIdx, 'create_new');
      rerender();
      container.scrollTop = 0;
      return;
    }

    if (skipBtn) {
      const ok = await showSimpleConfirmModal(
        'Skip this row?',
        'This uploaded row will not be saved.',
        'Skip',
        'Cancel'
      );
      if (!ok) return;
      setArticleMasterConflictResolution(conflict, arrayIdx, 'skip');
      rerender();
      container.scrollTop = 0;
    }
  });
}

function showArticleMasterPriceMismatchModal(data, onApply) {
  return new Promise((resolve) => {
    articleMasterUploadState.pendingConflicts = data.conflicts || [];
    articleMasterUploadState.resolutions = {};

    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.78); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 100001;';

    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 1100px; width: 96%; max-height: 94vh; display:flex; flex-direction:column; ' +
      'box-shadow: 0 12px 40px rgba(0,0,0,0.5); font-family: inherit; color: #e6e6e6;';

    const headerEl = document.createElement('div');
    const listEl = document.createElement('div');
    listEl.style.cssText = 'flex:1 1 auto; min-height:0; overflow:auto; margin:14px 0; padding-right:4px;';

    const footerEl = document.createElement('div');
    footerEl.style.cssText = 'display:flex; justify-content:space-between; align-items:center; margin-top:12px; flex-shrink:0; gap:10px; flex-wrap:wrap;';

    function renderModal() {
      const remaining = remainingArticleMasterConflictsCount();
      const total = articleMasterUploadState.pendingConflicts.length;
      const resolved = total - remaining;
      headerEl.innerHTML =
        '<h2 style="margin:0 0 8px; color:#e0b84a;">Seasonal price revision — review</h2>' +
        `<p style="margin:0; color:#aaa; font-size:13px;">${data.message || 'Uploaded prices differ from Article Master (increase or decrease). Replace applies the new season prices.'}</p>` +
        `<p style="margin:8px 0 0; color:#bbb; font-size:12px;">${resolved} resolved · ${remaining} remaining` +
        (data.created ? ` · ${data.created} already added` : '') +
        '</p>';

      const indexed = articleMasterUploadState.pendingConflicts.map((conflict, arrayIdx) => ({
        conflict,
        arrayIdx,
        resolved: isArticleMasterConflictResolved(conflict, arrayIdx),
        key: getArticleMasterConflictKey(conflict, arrayIdx),
      }));
      const seenKeys = new Set();
      const uniqueIndexed = indexed.filter((entry) => {
        if (seenKeys.has(entry.key)) return false;
        seenKeys.add(entry.key);
        return true;
      });
      uniqueIndexed.sort((a, b) => Number(a.resolved) - Number(b.resolved));

      listEl.innerHTML = uniqueIndexed
        .map(({ conflict, arrayIdx }) => renderArticleMasterConflictCard(conflict, arrayIdx))
        .join('');
      bindArticleMasterConflictActions(listEl, renderModal);

      const applyBtn = footerEl.querySelector('#am-conflict-apply-btn');
      if (applyBtn) {
        const canApply = remaining === 0;
        applyBtn.disabled = !canApply;
        applyBtn.style.opacity = canApply ? '1' : '0.5';
        applyBtn.style.cursor = canApply ? 'pointer' : 'not-allowed';
        applyBtn.textContent = canApply
          ? 'Apply decisions & finish upload'
          : `Resolve ${remaining} more item(s) to continue`;
      }
    }

    footerEl.innerHTML =
      '<div style="display:flex; gap:8px; flex-wrap:wrap;">' +
      '<button type="button" id="am-conflict-replace-all-btn" class="btn btn-primary">Replace all with new prices</button>' +
      '<button type="button" id="am-conflict-skip-all-btn" class="btn btn-secondary">Skip all remaining</button>' +
      '<button type="button" id="am-conflict-cancel-btn" class="btn btn-secondary">Cancel upload</button>' +
      '</div>' +
      '<button type="button" id="am-conflict-apply-btn" class="btn btn-primary" disabled style="opacity:0.5;">Resolve all items to continue</button>';

    box.appendChild(headerEl);
    box.appendChild(listEl);
    box.appendChild(footerEl);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    renderModal();

    function cleanup(result) {
      document.body.removeChild(overlay);
      resolve(result);
    }

    footerEl.querySelector('#am-conflict-cancel-btn').addEventListener('click', () => cleanup(null));
    footerEl.querySelector('#am-conflict-replace-all-btn').addEventListener('click', async () => {
      const remaining = remainingArticleMasterConflictsCount();
      if (!remaining) return;
      const ok = await showSimpleConfirmModal(
        'Replace all with new season prices?',
        `Apply uploaded prices to all <strong>${remaining}</strong> matching item(s)? This is the recommended action for AW26 / season updates.`,
        'Replace all',
        'Cancel'
      );
      if (!ok) return;
      applyArticleMasterConflictActionToAllRemaining('replace');
      renderModal();
    });
    footerEl.querySelector('#am-conflict-skip-all-btn').addEventListener('click', async () => {
      const remaining = remainingArticleMasterConflictsCount();
      if (!remaining) return;
      const ok = await showSimpleConfirmModal(
        'Skip all remaining rows?',
        `Skip <strong>${remaining}</strong> remaining item(s) — they will not be saved to Article Master.`,
        'Skip all',
        'Cancel'
      );
      if (!ok) return;
      applyArticleMasterConflictActionToAllRemaining('skip');
      renderModal();
    });
    footerEl.querySelector('#am-conflict-apply-btn').addEventListener('click', async () => {
      if (remainingArticleMasterConflictsCount() > 0) return;
      const ok = await showSimpleConfirmModal(
        'Apply your decisions?',
        'Article Master will be updated according to your Replace / Create new / Skip choices.',
        'Apply',
        'Back'
      );
      if (!ok) return;
      cleanup(articleMasterUploadState.resolutions);
      if (typeof onApply === 'function') onApply(articleMasterUploadState.resolutions);
    });
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(null); });
  });
}

function formatArticleMasterUploadResult(data) {
  if (data.message) return data.message;
  if ((data.created || 0) > 0) {
    return `Successfully added ${data.created} item(s).`;
  }
  if ((data.updated || 0) > 0) {
    return `Successfully updated ${data.updated} item(s).`;
  }
  if ((data.skipped || 0) > 0) {
    return '0 items updated — items already available in Article Master.';
  }
  return 'Upload complete.';
}

async function uploadArticleMasterSheet(confirmedCategory = null, conflictResolutions = null, uiPrefix = null) {
  const prefix = uiPrefix || articleMasterUploadState.uiPrefix || 'am';
  articleMasterUploadState.uiPrefix = prefix;
  const fileInput = document.getElementById(`${prefix}-upload-file`);
  const resultEl = document.getElementById(`${prefix}-upload-result`);
  const file = fileInput?.files?.[0];
  if (!file) {
    if (resultEl) resultEl.textContent = 'Please select an Excel file first.';
    return;
  }

  if (resultEl) resultEl.textContent = 'Uploading...';
  const formData = new FormData();
  formData.append('file', file);
  if (confirmedCategory) {
    formData.append('confirmed_category', confirmedCategory);
  }
  if (conflictResolutions && Object.keys(conflictResolutions).length) {
    formData.append('conflict_resolutions', JSON.stringify(conflictResolutions));
  }

  try {
    const response = await fetchWithAuth('/api/v1/article-master/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Upload failed'));
    }

    if (data.status === 'confirmation_required') {
      const selected = await showArticleMasterCategoryModal(data);
      if (!selected) {
        if (resultEl) {
          resultEl.textContent = 'Upload cancelled — not confirmed.';
        }
        return;
      }
      articleMasterUploadState.pendingFile = file;
      articleMasterUploadState.pendingCategory = selected;
      articleMasterUploadState.resolutions = {};
      await uploadArticleMasterSheet(selected, null, prefix);
      return;
    }

    if (data.status === 'price_mismatch_confirmation_required') {
      articleMasterUploadState.pendingFile = file;
      articleMasterUploadState.pendingCategory = confirmedCategory || articleMasterUploadState.pendingCategory;
      const resolutions = await showArticleMasterPriceMismatchModal(data, null);
      if (!resolutions) {
        const partial = [];
        if (data.created) partial.push(`${data.created} item(s) already added`);
        if (resultEl) {
          resultEl.textContent = partial.length
            ? `${partial.join(' · ')} · mismatch review cancelled.`
            : 'Upload cancelled — price mismatches not resolved.';
        }
        return;
      }
      await uploadArticleMasterSheet(
        articleMasterUploadState.pendingCategory || confirmedCategory,
        resolutions,
        prefix
      );
      return;
    }

    if (resultEl) {
      resultEl.textContent = formatArticleMasterUploadResult(data);
      if (data.needs_manual_review?.length) {
        resultEl.textContent += ` | Needs review: ${data.needs_manual_review.length}`;
      }
    }
    if (fileInput) fileInput.value = '';
    articleMasterUploadState.pendingFile = null;
    articleMasterUploadState.pendingCategory = null;
    articleMasterUploadState.resolutions = {};
    articleMasterUploadState.uiPrefix = null;

    const categorySelect = document.getElementById('am-category-filter');
    const searchInput = document.getElementById('am-search-filter');
    if (searchInput) searchInput.value = '';

    await loadArticleMasterList();

    if ((data.duplicate_groups_remaining || 0) > 0) {
      const mergeNow = await showSimpleConfirmModal(
        'Duplicate articles found',
        `${data.duplicate_groups_remaining} duplicate group(s) still in Article Master (e.g. Blumen + Bluman for same product). Merge them now?`,
        'Merge duplicates',
        'Later'
      );
      if (mergeNow) {
        await scanArticleMasterDuplicates();
      }
    }

    if (categorySelect) {
      // Mixed sheets → show All so user sees every new row
      const breakdown = data.category_breakdown || {};
      const cats = Object.keys(breakdown);
      if (cats.length === 1 && [...categorySelect.options].some((o) => o.value === cats[0])) {
        categorySelect.value = cats[0];
      } else {
        categorySelect.value = 'All';
      }
      renderArticleMasterTable();
    }

    if (prefix === 'of-am') {
      await loadOrderFulfillmentCatalogSummary();
    }
  } catch (error) {
    if (resultEl) resultEl.textContent = error.message || 'Upload failed';
  }
}

async function deleteOneArticleMaster(articleId) {
  const ok = await showSimpleConfirmModal(
    'Delete article?',
    'This article will be permanently removed from Article Master.',
    'Delete',
    'Cancel'
  );
  if (!ok) {
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/article-master/${articleId}`, {
      method: 'DELETE',
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Delete failed'));
    }
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Delete failed');
  }
}

async function deleteAllArticleMaster() {
  const category = document.getElementById('am-category-filter')?.value || 'All';
  const scope = category === 'All'
    ? 'all articles (every category)'
    : `all articles in the "${category}" category`;
  const ok = await showSimpleConfirmModal(
    'Delete all articles?',
    `<strong style="color:#f87171;">Warning:</strong> This permanently deletes ${scope}. This cannot be undone.`,
    'Delete all',
    'Cancel'
  );
  if (!ok) {
    return;
  }
  try {
    const response = await fetchWithAuth('/api/v1/article-master/delete-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Delete all failed'));
    }
    const resultEl = document.getElementById('am-upload-result');
    if (resultEl) {
      resultEl.textContent = `Deleted ${data.deleted || 0} articles (${data.category}).`;
    }
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Delete all failed');
  }
}

async function scanArticleMasterDuplicates() {
  const resultEl = document.getElementById('am-upload-result');
  try {
    if (resultEl) resultEl.textContent = 'Scanning for duplicate articles...';
    const response = await fetchWithAuth('/api/v1/article-master/duplicates');
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Duplicate scan failed'));
    }
    const groups = data.groups || [];
    if (!groups.length) {
      if (resultEl) resultEl.textContent = 'No duplicate articles found.';
      return;
    }

    const summary = groups.map((g, i) => {
      const names = (g.articles || []).map((a) => `${a.brand} (${a.item_key}) MRP ${a.mrp ?? '—'}`).join('\n  · ');
      return `${i + 1}. ${g.identity_label || 'Group'} — ${g.articles.length} rows:\n  · ${names}`;
    }).join('\n\n');

    const ok = await showSimpleConfirmModal(
      `Merge ${groups.length} duplicate group(s)?`,
      `<div style="text-align:left; font-size:12px; max-height:240px; overflow:auto;">` +
      `<p>Same product found under different brand spellings or item keys (e.g. Blumen / Bluman).</p>` +
      `<pre style="white-space:pre-wrap; color:#ccc; font-family:inherit;">${summary}</pre>` +
      `<p style="margin-top:10px;">Keeps the oldest row and applies the <strong>latest prices</strong>. Brand aliases (Blumen/Bluemen→Bluman) are applied.</p></div>`,
      'Merge all',
      'Cancel'
    );
    if (!ok) {
      if (resultEl) resultEl.textContent = `${groups.length} duplicate group(s) found — merge cancelled.`;
      return;
    }

    const mergeResp = await fetchWithAuth('/api/v1/article-master/merge-duplicates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ auto: true }),
    });
    const mergeData = await parseApiResponse(mergeResp);
    if (!mergeResp.ok) {
      throw new Error(getApiErrorMessage(mergeData, 'Merge failed'));
    }
    if (resultEl) resultEl.textContent = mergeData.message || 'Duplicates merged.';
    await loadArticleMasterList();
  } catch (error) {
    if (resultEl) resultEl.textContent = error.message || 'Duplicate scan failed';
    else alert(error.message || 'Duplicate scan failed');
  }
}

async function confirmArticleMasterNewCategory() {
  const name = document.getElementById('am-new-category-name')?.value?.trim();
  const keysRaw = document.getElementById('am-new-category-keys')?.value || 'brand, size';
  const keyFields = keysRaw.split(',').map((k) => k.trim()).filter(Boolean);
  const resultEl = document.getElementById('am-upload-result');

  if (!name) {
    alert('Category name required');
    return;
  }

  try {
    const response = await fetchWithAuth('/api/v1/article-master/confirm-new-category', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category_name: name, key_fields: keyFields }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Could not confirm category'));
    }
    closeModal('am-new-category-modal');
    if (resultEl) resultEl.textContent = `Category "${name}" confirmed. Please upload again.`;
    if (articleMasterState.pendingUploadFile) {
      const fileInput = document.getElementById('am-upload-file');
      if (fileInput) {
        const dt = new DataTransfer();
        dt.items.add(articleMasterState.pendingUploadFile);
        fileInput.files = dt.files;
      }
      articleMasterState.pendingUploadFile = null;
      await uploadArticleMasterSheet();
    }
  } catch (error) {
    alert(error.message || 'Confirm failed');
  }
}

function openArticleMasterFullEdit(articleId) {
  const article = articleMasterState.articles.find((a) => a.id === articleId);
  if (!article) return;
  articleMasterState.editArticleId = articleId;

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? '';
  };
  const labelEl = document.getElementById('am-full-edit-label');
  if (labelEl) {
    labelEl.textContent = article.item_key ? `Item: ${article.item_key}` : `Article ID ${articleId}`;
  }
  setVal('am-full-edit-brand', article.brand);
  setVal('am-full-edit-size', article.size);
  setVal('am-full-edit-product', article.product_type);
  setVal('am-full-edit-mrp', article.mrp);
  setVal('am-full-edit-ptr', article.ptr);
  setVal('am-full-edit-exmill', article.ex_mill_price);
  setVal('am-full-edit-bale', article.bale_pack_size);
  toggleModal('am-full-edit-modal', true);
}

function collectArticleMasterFullEditUpdates(article) {
  const numOrNull = (raw) => {
    const s = String(raw ?? '').trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : s;
  };
  const updates = {
    brand: document.getElementById('am-full-edit-brand')?.value?.trim() || null,
    size: document.getElementById('am-full-edit-size')?.value?.trim() || null,
    product_type: document.getElementById('am-full-edit-product')?.value?.trim() || null,
    mrp: numOrNull(document.getElementById('am-full-edit-mrp')?.value),
    ptr: numOrNull(document.getElementById('am-full-edit-ptr')?.value),
    ex_mill_price: numOrNull(document.getElementById('am-full-edit-exmill')?.value),
    bale_pack_size: numOrNull(document.getElementById('am-full-edit-bale')?.value),
  };
  const changed = {};
  Object.entries(updates).forEach(([key, val]) => {
    const oldVal = article[key];
    const oldStr = oldVal == null ? '' : String(oldVal);
    const newStr = val == null ? '' : String(val);
    if (oldStr !== newStr) {
      changed[key] = val;
    }
  });
  return changed;
}

async function saveArticleMasterFullEdit() {
  const articleId = articleMasterState.editArticleId;
  const article = articleMasterState.articles.find((a) => a.id === articleId);
  if (!article) return;

  const updates = collectArticleMasterFullEditUpdates(article);
  if (!Object.keys(updates).length) {
    alert('No changes to save.');
    return;
  }

  const ok = await showSimpleConfirmModal(
    'Save changes?',
    'Do you really want to change this article? Your edits will be saved to Article Master.',
    'Yes, save',
    'No'
  );
  if (!ok) return;

  try {
    const response = await fetchWithAuth(`/api/v1/article-master/${articleId}/edit-full`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Edit failed'));
    }
    closeModal('am-full-edit-modal');
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Edit failed');
  }
}

function openArticleMasterEdit(articleId, field) {
  const article = articleMasterState.articles.find((a) => a.id === articleId);
  articleMasterState.editArticleId = articleId;
  articleMasterState.editField = field;
  const labels = {
    mrp: 'MRP (₹)',
    ptr: 'PTR (₹)',
    ex_mill_price: 'Ex-Mill Price (₹)',
    bale_pack_size: 'Bale Pack Size',
  };
  const labelEl = document.getElementById('am-edit-label');
  const fieldLabel = document.getElementById('am-edit-field-label');
  const valueInput = document.getElementById('am-edit-value');
  if (labelEl) labelEl.textContent = article?.item_key ? `Item: ${article.item_key}` : `Article ID ${articleId}`;
  if (fieldLabel) fieldLabel.textContent = labels[field] || field;
  if (valueInput) valueInput.value = article?.[field] ?? '';
  toggleModal('am-edit-modal', true);
}

async function saveArticleMasterEdit() {
  const articleId = articleMasterState.editArticleId;
  const field = articleMasterState.editField;
  const value = document.getElementById('am-edit-value')?.value;
  if (!articleId || !field || value === undefined) {
    return;
  }

  const ok = await showSimpleConfirmModal(
    'Save changes?',
    'Do you really want to change this value?',
    'Yes, save',
    'No'
  );
  if (!ok) return;

  try {
    const response = await fetchWithAuth(`/api/v1/article-master/${articleId}/edit`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ field, value }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Edit failed'));
    }
    closeModal('am-edit-modal');
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Edit failed');
  }
}

function formatPriceHistoryValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  const text = String(value).trim();
  if (!text) return '—';
  const num = Number(text);
  if (!Number.isFinite(num)) return text;
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function openArticleMasterPriceHistory(articleId) {
  const article = articleMasterState.articles.find((a) => a.id === articleId);
  const labelEl = document.getElementById('am-price-history-label');
  const tbody = document.getElementById('am-price-history-tbody');
  if (labelEl) labelEl.textContent = article?.item_key || `Article #${articleId}`;
  if (tbody) tbody.innerHTML = '<tr><td colspan="5">Loading...</td></tr>';
  toggleModal('am-price-history-modal', true);

  try {
    const response = await fetchWithAuth(`/api/v1/article-master/${articleId}/price-history`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load history'));
    }
    const history = data.history || [];
    if (!history.length) {
      tbody.innerHTML = '<tr><td colspan="5">No changes recorded yet.</td></tr>';
      return;
    }
    tbody.innerHTML = history.map((h) => `
      <tr>
        <td>${h.field_changed}</td>
        <td>${formatPriceHistoryValue(h.old_value)}</td>
        <td>${formatPriceHistoryValue(h.new_value)}</td>
        <td>${h.changed_by ?? '—'}</td>
        <td>${h.changed_at ? new Date(h.changed_at).toLocaleString() : '—'}</td>
      </tr>
    `).join('');
  } catch (error) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="5">${error.message}</td></tr>`;
  }
}

function showArticleMasterDownloadModal() {
  return new Promise((resolve) => {
    const categories = ['All', 'Bed', 'Bath', 'TOB', 'TOB Pillow'];

    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';

    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 380px; width: 90%; ' +
      'box-shadow: 0 12px 40px rgba(0,0,0,0.5); font-family: inherit; color: #e6e6e6;';

    box.innerHTML =
      '<div style="font-size:16px; font-weight:600; margin-bottom:6px; color:#e0b84a;">What would you like to download?</div>' +
      '<div style="font-size:13px; color:#999; margin-bottom:16px; line-height:1.5;">Category chuno, ya "All" se poora Article Master.</div>' +
      '<div id="am-download-cat-btns" style="display:flex; flex-direction:column; gap:8px; margin-bottom:14px;"></div>' +
      '<div id="am-download-cancel-btn" style="text-align:center; padding:8px; border-radius:8px; cursor:pointer; color:#999; border:1px solid #333;">Cancel</div>';

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const catContainer = box.querySelector('#am-download-cat-btns');
    categories.forEach((cat) => {
      const btn = document.createElement('div');
      btn.textContent = cat === 'All' ? 'All Categories' : cat;
      const isAll = cat === 'All';
      btn.style.cssText =
        `text-align:center; padding:10px; border-radius:8px; cursor:pointer; font-size:14px; ` +
        `font-weight:${isAll ? '600' : '400'}; ` +
        `background:${isAll ? '#2563eb' : 'transparent'}; ` +
        `color:${isAll ? '#fff' : '#ccc'}; ` +
        `border:1px solid ${isAll ? '#2563eb' : '#333'};`;
      if (!isAll) {
        btn.addEventListener('mouseenter', () => { btn.style.borderColor = '#e0b84a'; btn.style.color = '#e0b84a'; });
        btn.addEventListener('mouseleave', () => { btn.style.borderColor = '#333'; btn.style.color = '#ccc'; });
      }
      btn.addEventListener('click', () => { cleanup(); resolve(cat); });
      catContainer.appendChild(btn);
    });

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve(null); }
    }
    document.addEventListener('keydown', onKeydown);

    box.querySelector('#am-download-cancel-btn').addEventListener('click', () => {
      cleanup(); resolve(null);
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { cleanup(); resolve(null); }
    });
  });
}

async function downloadArticleMasterExcel() {
  const category = await showArticleMasterDownloadModal();
  if (!category) return;
  const url = `/api/v1/article-master/download?category=${encodeURIComponent(category)}`;
  const filename = `Article_Master_${category.replace(/ /g, '_')}.xlsx`;
  try {
    const response = await fetchWithAuth(url);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getApiErrorMessage(data, 'Download failed'));
    }
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    alert(error.message || 'Download failed');
  }
}

const filledOrdersState = {
  orders: [],
  distributors: [],
  activeOrderId: null,
  activeOrderItems: [],
  editingItemId: null,
  pendingQtyConfirm: null,
  confirmedDistributorId: null,
  pendingPreview: null,
  uploadUiPrefix: 'fo',
};

function getFilledOrderUploadPrefix(prefix) {
  return prefix || filledOrdersState.uploadUiPrefix || 'fo';
}

function getFilledOrderPreviewItemKey(it) {
  return it.item_key || `line:${it.line_number}|${it.brand}|${it.size}`;
}

function initFilledOrderPendingPreview(data) {
  const unmatched = (data.unmatched_items && data.unmatched_items.length)
    ? [...data.unmatched_items]
    : (data.all_items || data.issue_items || data.sample_items || []).filter((it) => !it.matched);
  filledOrdersState.pendingPreview = {
    category: data.category,
    season: data.season,
    keyFields: data.key_fields || [],
    totalLines: data.total_lines || 0,
    allItems: (data.all_items && data.all_items.length) ? [...data.all_items] : [],
    unmatchedItems: unmatched,
    rejectedKeys: new Set(),
    addedKeys: new Set(),
  };
}

function getFilledOrderPendingUnmatchedItems() {
  const preview = filledOrdersState.pendingPreview;
  if (!preview) return [];
  return preview.unmatchedItems.filter((it) => {
    const key = getFilledOrderPreviewItemKey(it);
    return !preview.rejectedKeys.has(key) && !preview.addedKeys.has(key);
  });
}

function getFilledOrderPendingSaveStats() {
  const preview = filledOrdersState.pendingPreview;
  if (!preview) {
    return { total: 0, matched: 0, unmatched: 0, rejected: 0, added: 0, flagged: 0 };
  }
  const rejected = preview.rejectedKeys.size;
  const added = preview.addedKeys.size;
  const pendingUnmatched = getFilledOrderPendingUnmatchedItems().length;
  const total = preview.totalLines || preview.allItems.length || (
    pendingUnmatched + rejected + added
  );
  const unmatched = pendingUnmatched;
  const matched = Math.max(0, total - unmatched - rejected);
  const flagged = preview.allItems.filter((it) => !it.is_clean_bale_multiple).length;
  return { total, matched, unmatched, rejected, flagged, added };
}

function setFilledOrderUploadFieldsEnabled(enabled, prefix = 'fo') {
  const uiPrefix = getFilledOrderUploadPrefix(prefix);
  const categorySelect = document.getElementById(`${uiPrefix}-category-select`);
  const distributorSelect = document.getElementById(`${uiPrefix}-distributor-select`);
  const waitingLabel = '— Choose a file first —';
  if (categorySelect) {
    categorySelect.disabled = !enabled;
    if (categorySelect.options[0]) {
      categorySelect.options[0].textContent = enabled ? 'Auto-detect' : waitingLabel;
    }
    if (!enabled) categorySelect.value = '';
  }
  if (distributorSelect) {
    distributorSelect.disabled = !enabled;
    if (distributorSelect.options[0]) {
      distributorSelect.options[0].textContent = enabled ? 'Auto from filename…' : waitingLabel;
    }
    if (!enabled) distributorSelect.value = '';
  }
}

function resetFilledOrderUploadDropdowns(prefix = 'fo') {
  const uiPrefix = getFilledOrderUploadPrefix(prefix);
  const categorySelect = document.getElementById(`${uiPrefix}-category-select`);
  const distributorSelect = document.getElementById(`${uiPrefix}-distributor-select`);
  if (categorySelect) categorySelect.value = '';
  if (distributorSelect) distributorSelect.value = '';
  filledOrdersState.confirmedDistributorId = null;
}

function resetFilledOrderUploadForm(prefix = 'fo') {
  const uiPrefix = getFilledOrderUploadPrefix(prefix);
  const fileInput = document.getElementById(`${uiPrefix}-upload-file`);
  const resultEl = document.getElementById(`${uiPrefix}-upload-result`);
  if (fileInput) fileInput.value = '';
  if (resultEl) resultEl.textContent = '';
  resetFilledOrderUploadDropdowns(uiPrefix);
  setFilledOrderUploadFieldsEnabled(false, uiPrefix);
}

async function initFilledOrdersWorkspace() {
  resetFilledOrderUploadForm();
  await loadFilledOrdersDistributors();
  setFilledOrderUploadFieldsEnabled(false);
  await loadFilledOrdersList();
}

function setFilledOrderCategorySelect(category, prefix = 'fo') {
  const select = document.getElementById(`${getFilledOrderUploadPrefix(prefix)}-category-select`);
  if (!select || !category) return;
  const hasOption = Array.from(select.options).some((opt) => opt.value === category);
  if (hasOption) select.value = category;
}

function setFilledOrderDistributorSelect(distributorId, prefix = 'fo') {
  const select = document.getElementById(`${getFilledOrderUploadPrefix(prefix)}-distributor-select`);
  if (!select || distributorId == null) return;
  select.value = String(distributorId);
}

function applyFilledOrderUploadPreview(data, prefix = 'fo') {
  const uiPrefix = getFilledOrderUploadPrefix(prefix);
  const parts = [];
  if (data.suggested_distributor?.id) {
    setFilledOrderDistributorSelect(data.suggested_distributor.id, uiPrefix);
    filledOrdersState.confirmedDistributorId = data.suggested_distributor.id;
    parts.push(`Distributor: ${data.suggested_distributor.display_name || getFilledOrderDistributorLabel(data.suggested_distributor)}`);
  }
  if (data.detected_category) {
    setFilledOrderCategorySelect(data.detected_category, uiPrefix);
    parts.push(`Category: ${data.detected_category}`);
  }
  return parts;
}

async function onFilledOrderFileSelected(prefix = 'fo') {
  const uiPrefix = getFilledOrderUploadPrefix(prefix);
  filledOrdersState.uploadUiPrefix = uiPrefix;
  const resultEl = document.getElementById(`${uiPrefix}-upload-result`);
  const file = document.getElementById(`${uiPrefix}-upload-file`)?.files?.[0];
  resetFilledOrderUploadDropdowns(uiPrefix);
  if (!file) {
    setFilledOrderUploadFieldsEnabled(false, uiPrefix);
    if (resultEl) resultEl.textContent = '';
    return;
  }

  setFilledOrderUploadFieldsEnabled(true, uiPrefix);
  if (resultEl) resultEl.textContent = `"${file.name}" — detecting distributor and category...`;
  try {
    if (!filledOrdersState.distributors.length) {
      await loadFilledOrdersDistributors(['fo', 'of-fo']);
    }
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetchWithAuth('/api/v1/filled-orders/preview', {
      method: 'POST',
      body: formData,
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Preview failed'));
    }

    const parts = applyFilledOrderUploadPreview(data, uiPrefix);
    if (!parts.length) {
      if (resultEl) {
        resultEl.textContent = `"${file.name}" ready — could not detect distributor/category. Please select manually.`;
      }
      return;
    }
    if (resultEl) {
      resultEl.textContent = `${parts.join(' | ')} — click Upload to continue.`;
    }
  } catch (error) {
    if (resultEl) {
      resultEl.textContent = error.message || 'Preview failed — you can still try Upload.';
    }
  }
}

async function loadFilledOrdersDistributors(prefixes = ['fo']) {
  const prefixList = Array.isArray(prefixes) ? prefixes : [prefixes];
  const selects = prefixList
    .map((prefix) => document.getElementById(`${prefix}-distributor-select`))
    .filter(Boolean);
  if (!selects.length) return;
  try {
    const response = await fetchWithAuth('/api/v1/masters/distributors?limit=5000');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Failed to load distributors'));
    }
    filledOrdersState.distributors = data.data || [];
    selects.forEach((select) => {
      const selectedId = select.value;
      select.innerHTML =
        '<option value="">Auto from filename…</option>' +
        filledOrdersState.distributors
          .map((d) => `<option value="${d.id}">${getFilledOrderDistributorLabel(d)}</option>`)
          .join('');
      if (selectedId) select.value = selectedId;
    });
  } catch (error) {
    selects.forEach((select) => {
      select.innerHTML = '<option value="">Failed to load</option>';
    });
  }
}

function getFilledOrderDistributorLabel(d) {
  if (!d) return '—';
  const firm = (d.firm_name || '').trim();
  if (firm) return firm;
  const contact = (d.name || '').trim();
  return contact || (d.id != null ? `Distributor #${d.id}` : '—');
}

function formatFilledOrderValue(value) {
  if (value === null || value === undefined || value === '') return '—';
  return value;
}

function formatFilledOrderAmount(value) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  return num.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatFilledOrderQty(value) {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (!Number.isFinite(num)) return value;
  if (Number.isInteger(num) || Math.abs(num - Math.round(num)) < 1e-9) {
    return String(Math.round(num));
  }
  return num.toFixed(2);
}

function renderFilledOrdersTable() {
  const tbody = document.getElementById('fo-orders-tbody');
  const countEl = document.getElementById('fo-list-count');
  if (!tbody) return;
  const orders = filledOrdersState.orders;
  if (countEl) countEl.textContent = `${orders.length} order${orders.length === 1 ? '' : 's'}`;
  if (!orders.length) {
    tbody.innerHTML = '<tr><td colspan="14">No filled orders yet. Upload one above.</td></tr>';
    return;
  }
  const distributorNameById = {};
  filledOrdersState.distributors.forEach((d) => {
    distributorNameById[d.id] = getFilledOrderDistributorLabel(d);
  });
  tbody.innerHTML = orders.map((o) => `
    <tr>
      <td>${distributorNameById[o.distributor_id] || o.distributor_name_raw || '—'}</td>
      <td>${o.category}</td>
      <td>${o.season}</td>
      <td style="font-size:0.75rem;">${formatFilledOrderValue(o.quantity_column_used)}</td>
      <td>${o.quantity_unit_used || '—'}</td>
      <td>${o.total_lines ?? 0}</td>
      <td>${formatFilledOrderQty(o.total_bales)}</td>
      <td>${formatFilledOrderQty(o.total_piece_qty)}</td>
      <td>${formatFilledOrderAmount(o.total_ex_mill_value)}</td>
      <td>${o.matched_lines ?? 0}</td>
      <td>${o.unmatched_lines ?? 0}</td>
      <td>${o.flagged_lines ? `🚩 ${o.flagged_lines}` : '0'}</td>
      <td style="font-size:0.75rem;">${(o.created_at || '').slice(0, 10)}</td>
      <td>
        <button class="btn btn-secondary" style="padding:4px 8px;font-size:0.75rem;" onclick="openFilledOrderDetail(${o.id})">View</button>
        <button class="btn btn-primary" style="padding:4px 8px;font-size:0.75rem;margin-left:4px;" onclick="linkFilledOrderToSalesOrder(${o.id})">Link SO</button>
        <button class="btn btn-danger" style="padding:4px 8px;font-size:0.75rem;margin-left:4px;" onclick="deleteFilledOrder(${o.id})">Delete</button>
      </td>
    </tr>
  `).join('');
}

async function loadFilledOrdersList() {
  const tbody = document.getElementById('fo-orders-tbody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="14">Loading...</td></tr>';
  try {
    const response = await fetchWithAuth('/api/v1/filled-orders/list');
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load filled orders'));
    }
    filledOrdersState.orders = data.filled_orders || [];
    renderFilledOrdersTable();
    if (currentModuleKey === 'dashboard' && authState.role === 'sales_executive') {
      loadFilledOrdersSeasonWidgets();
    }
  } catch (error) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="14">${error.message || 'Failed to load'}</td></tr>`;
  }
}

function formatFilledOrderComparisonStatus(status) {
  const map = {
    match: '<span style="color:#7fdc7f;">Match</span>',
    mismatch: '<span style="color:#f87171;">Mismatch</span>',
    missing_in_file: '<span style="color:#ffb648;">Missing in file</span>',
    missing_in_master: '<span style="color:#ffb648;">Missing in Article Master</span>',
    both_empty: '<span style="color:#888;">Empty</span>',
  };
  return map[status] || status;
}

function formatFilledOrderIssueLine(it) {
  const label = [
    formatFilledOrderValue(it.brand),
    formatFilledOrderValue(it.size),
    formatFilledOrderValue(it.product_type),
  ].filter((v) => v && v !== '—').join(' · ') || 'Unknown line';
  const lineNo = it.line_number != null ? `Line ${it.line_number}` : 'Line ?';
  const badges = [];
  if (!it.matched) badges.push('<span style="color:#f87171;">Unmatched</span>');
  if (!it.is_clean_bale_multiple) badges.push('<span style="color:#ffb648;">Flagged qty</span>');
  const badgeHtml = badges.length ? ` <span style="font-size:11px;">(${badges.join(', ')})</span>` : '';

  const comparisons = (it.field_comparisons || []).map((c) => (
    '<tr>' +
    `<td style="padding:4px 6px; color:#ccc;">${c.field}</td>` +
    `<td style="padding:4px 6px;">${formatFilledOrderValue(c.file_value)}</td>` +
    `<td style="padding:4px 6px;">${formatFilledOrderValue(c.master_value)}</td>` +
    `<td style="padding:4px 6px;">${formatFilledOrderComparisonStatus(c.status)}</td>` +
    '</tr>'
  )).join('');

  const comparisonTable = comparisons
    ? (
      '<table style="width:100%; border-collapse:collapse; font-size:11px; margin-top:8px;">' +
      '<thead><tr style="color:#888;">' +
      '<th style="text-align:left; padding:4px 6px;">Field</th>' +
      '<th style="text-align:left; padding:4px 6px;">In file</th>' +
      '<th style="text-align:left; padding:4px 6px;">Article Master</th>' +
      '<th style="text-align:left; padding:4px 6px;">Status</th>' +
      '</tr></thead><tbody>' + comparisons + '</tbody></table>'
    )
    : '';

  const qtyRow = it.final_piece_qty != null
    ? `<div style="font-size:11px; color:#bbb; margin-top:6px;">Qty: ${formatFilledOrderQty(it.raw_qty_value)} ${it.detected_unit || ''} → ${formatFilledOrderQty(it.final_piece_qty)} pcs (bale ${formatFilledOrderQty(it.bale_size_used)})</div>`
    : '';

  const issue = it.issue_summary
    ? `<div style="color:#fca5a5; font-size:11px; margin-top:6px;">${it.issue_summary}</div>`
    : '';
  const hint = it.suggestion
    ? `<div style="color:#9ca3af; font-size:11px; margin-top:4px;">Tip: ${it.suggestion}</div>`
    : '';
  const action = it.recommended_action
    ? `<div style="color:#93c5fd; font-size:11px; margin-top:6px;"><strong>Suggested:</strong> ${it.recommended_action.label} — ${it.recommended_action.detail}</div>`
    : '';
  const closest = it.closest_master_item_key
    ? `<div style="font-size:11px; color:#888; margin-top:4px;">Closest in Article Master: ${it.closest_master_brand || '—'} (${it.closest_master_item_key})</div>`
    : '';

  return (
    `<div style="font-size:12px; color:#ddd; padding:12px 0; border-bottom:1px solid #2a2a33;">` +
    `<div><span style="color:#888;">${lineNo}</span> <strong>${label}</strong>${badgeHtml}</div>` +
    comparisonTable + qtyRow + closest + issue + hint + action +
    `</div>`
  );
}

function collectFilledOrderPreviewIssues(data) {
  if (Array.isArray(data.issue_items) && data.issue_items.length) {
    return data.issue_items;
  }

  const byKey = new Map();
  const addItem = (it) => {
    if (!it) return;
    const key = it.item_key || [it.brand, it.size, it.product_type].join('|');
    if (!byKey.has(key)) byKey.set(key, it);
  };
  (data.unmatched_items || []).forEach(addItem);
  (data.flagged_items || []).forEach(addItem);
  if (byKey.size) return [...byKey.values()];

  return (data.sample_items || [])
    .filter((it) => !it.matched || !it.is_clean_bale_multiple)
    .map((it) => ({
      ...it,
      issue_summary: it.issue_summary || (
        !it.matched
          ? 'Not found in Article Master — check brand/size/product or add to Article Master.'
          : `Qty ${it.final_piece_qty} pcs is not a clean bale multiple (bale size ${it.bale_size_used ?? '—'}).`
      ),
      suggestion: it.suggestion || null,
    }));
}

function showSimpleConfirmModal(title, message, yesText = 'Yes', noText = 'No') {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.72); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 100002;';
    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 440px; width: 90%; color: #e6e6e6;';
    box.innerHTML =
      `<div style="font-size:16px; font-weight:600; margin-bottom:10px; color:#e0b84a;">${title}</div>` +
      `<div style="font-size:13px; color:#bbb; margin-bottom:18px; line-height:1.5;">${message}</div>` +
      `<div style="display:flex; gap:10px;">` +
      `<button id="scm-yes" class="btn btn-primary" style="flex:1;">${yesText}</button>` +
      `<button id="scm-no" class="btn btn-secondary" style="flex:1;">${noText}</button>` +
      `</div>`;
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    const cleanup = (val) => {
      document.body.removeChild(overlay);
      resolve(val);
    };
    box.querySelector('#scm-yes').addEventListener('click', () => cleanup(true));
    box.querySelector('#scm-no').addEventListener('click', () => cleanup(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(false); });
  });
}

function renderFilledOrderMismatchIssueCard(it) {
  const previewKey = getFilledOrderPreviewItemKey(it);
  const label = [
    formatFilledOrderValue(it.brand),
    formatFilledOrderValue(it.size),
    formatFilledOrderValue(it.product_type),
  ].filter((v) => v && v !== '—').join(' · ') || 'Unknown line';
  const lineNo = it.line_number != null ? `Line ${it.line_number}` : 'Line ?';

  const comparisons = (it.field_comparisons || []).map((c) => (
    '<tr>' +
    `<td style="padding:6px 8px; color:#ccc;">${c.field}</td>` +
    `<td style="padding:6px 8px;">${formatFilledOrderValue(c.file_value)}</td>` +
    `<td style="padding:6px 8px;">${formatFilledOrderValue(c.master_value)}</td>` +
    `<td style="padding:6px 8px;">${formatFilledOrderComparisonStatus(c.status)}</td>` +
    '</tr>'
  )).join('');

  const comparisonTable = comparisons
    ? (
      '<table style="width:100%; border-collapse:collapse; font-size:12px; margin-top:10px;">' +
      '<thead><tr style="color:#888; border-bottom:1px solid #333;">' +
      '<th style="text-align:left; padding:6px 8px;">Field</th>' +
      '<th style="text-align:left; padding:6px 8px;">In file</th>' +
      '<th style="text-align:left; padding:6px 8px;">Article Master</th>' +
      '<th style="text-align:left; padding:6px 8px;">Status</th>' +
      '</tr></thead><tbody>' + comparisons + '</tbody></table>'
    )
    : `<div style="color:#fca5a5; font-size:12px; margin-top:8px;">${it.issue_summary || 'Not found in Article Master'}</div>`;

  const hint = it.suggestion
    ? `<div style="color:#9ca3af; font-size:12px; margin-top:8px;">Tip: ${it.suggestion}</div>`
    : '';
  const qtyRow = it.final_piece_qty != null
    ? `<div style="font-size:12px; color:#bbb; margin-top:8px;">Qty: ${formatFilledOrderQty(it.raw_qty_value)} ${it.detected_unit || ''} → ${formatFilledOrderQty(it.final_piece_qty)} pcs</div>`
    : '';

  return (
    `<div class="fo-mismatch-card" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" ` +
    `style="border:1px solid #2a2a33; border-radius:10px; padding:14px; margin-bottom:12px; background:#101015;">` +
    `<div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between;">` +
    `<div><span style="color:#888; font-size:12px;">${lineNo}</span> <strong style="font-size:14px;">${label}</strong></div>` +
    `<div style="display:flex; gap:8px; flex-wrap:wrap;">` +
    `<button type="button" class="btn btn-primary fo-mismatch-add-btn" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" style="padding:6px 12px; font-size:12px;">Add to Article Master</button>` +
    `<button type="button" class="btn btn-danger fo-mismatch-reject-btn" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" style="padding:6px 12px; font-size:12px;">Reject</button>` +
    `</div></div>` +
    comparisonTable + qtyRow + hint +
    `</div>`
  );
}

async function filledOrderPreviewAddToArticleMaster(previewKey, rerender) {
  const preview = filledOrdersState.pendingPreview;
  if (!preview) return;
  const item = preview.unmatchedItems.find((it) => getFilledOrderPreviewItemKey(it) === previewKey);
  if (!item) return;

  const label = [
    formatFilledOrderValue(item.brand),
    formatFilledOrderValue(item.size),
    formatFilledOrderValue(item.product_type),
  ].filter((v) => v && v !== '—').join(' · ');

  const confirmed = await showSimpleConfirmModal(
    'Add to Article Master?',
    `Save <strong>${label}</strong> in Article Master (category: ${preview.category})?`,
  );
  if (!confirmed) return;

  try {
    const response = await fetchWithAuth('/api/v1/filled-orders/preview/add-to-article-master', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: preview.category, item }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to add to Article Master'));
    }
    preview.addedKeys.add(previewKey);
    if (typeof rerender === 'function') rerender();
  } catch (error) {
    alert(error.message || 'Failed to add to Article Master');
  }
}

async function filledOrderPreviewRejectLine(previewKey, rerender) {
  const preview = filledOrdersState.pendingPreview;
  if (!preview) return;
  const item = preview.unmatchedItems.find((it) => getFilledOrderPreviewItemKey(it) === previewKey);
  if (!item) return;

  const label = [
    formatFilledOrderValue(item.brand),
    formatFilledOrderValue(item.size),
    formatFilledOrderValue(item.product_type),
  ].filter((v) => v && v !== '—').join(' · ');

  const confirmed = await showSimpleConfirmModal(
    'Reject this line?',
    `Exclude <strong>${label}</strong> from this filled order when you save?`,
    'Yes, reject',
    'No, go back',
  );
  if (!confirmed) return;

  preview.rejectedKeys.add(previewKey);
  if (typeof rerender === 'function') rerender();
}

function bindFilledOrderMismatchCardActions(container, rerender) {
  container.querySelectorAll('.fo-mismatch-add-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      filledOrderPreviewAddToArticleMaster(btn.dataset.previewKey, rerender);
    });
  });
  container.querySelectorAll('.fo-mismatch-reject-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      filledOrderPreviewRejectLine(btn.dataset.previewKey, rerender);
    });
  });
}

function showFilledOrderMismatchReviewModal(onUpdate) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.78); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 100001;';

    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 1100px; width: 96%; max-height: 94vh; display:flex; flex-direction:column; ' +
      'box-shadow: 0 12px 40px rgba(0,0,0,0.5); font-family: inherit; color: #e6e6e6;';

    const listEl = document.createElement('div');
    listEl.style.cssText = 'flex:1 1 auto; min-height:0; overflow:auto; margin:14px 0; padding-right:4px;';

    function renderList() {
      const items = getFilledOrderPendingUnmatchedItems();
      const preview = filledOrdersState.pendingPreview;
      const keyFields = (preview?.keyFields || []).join(', ');
      box.querySelector('.fo-mismatch-header').innerHTML =
        '<h2 style="margin:0 0 8px; color:#e0b84a;">Unmatched lines — review</h2>' +
        `<p style="margin:0; color:#aaa; font-size:13px;">${items.length} line(s) remaining. ` +
        `Match keys: ${keyFields || 'brand, size'}. Scroll to review each line.</p>` +
        (preview?.addedKeys.size
          ? `<p style="margin:8px 0 0; color:#7fdc7f; font-size:12px;">${preview.addedKeys.size} added to Article Master.</p>`
          : '') +
        (preview?.rejectedKeys.size
          ? `<p style="margin:4px 0 0; color:#f87171; font-size:12px;">${preview.rejectedKeys.size} rejected (will be excluded on save).</p>`
          : '');

      if (!items.length) {
        listEl.innerHTML = '<div style="color:#7fdc7f; padding:20px; text-align:center;">No unmatched lines left to review.</div>';
      } else {
        listEl.innerHTML = items.map(renderFilledOrderMismatchIssueCard).join('');
        bindFilledOrderMismatchCardActions(listEl, () => {
          renderList();
          if (typeof onUpdate === 'function') onUpdate();
        });
      }
      if (typeof onUpdate === 'function') onUpdate();
    }

    const headerEl = document.createElement('div');
    headerEl.className = 'fo-mismatch-header';
    const footerEl = document.createElement('div');
    footerEl.style.cssText = 'display:flex; justify-content:flex-end; margin-top:12px; flex-shrink:0;';
    footerEl.innerHTML = '<button type="button" id="fo-mismatch-close-btn" class="btn btn-secondary">Back to save</button>';
    box.appendChild(headerEl);
    box.appendChild(listEl);
    box.appendChild(footerEl);
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    renderList();

    function cleanup() {
      document.body.removeChild(overlay);
      resolve();
    }
    box.querySelector('#fo-mismatch-close-btn').addEventListener('click', cleanup);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(); });
  });
}

function showFilledOrderSaveConfirmModal(data) {
  return new Promise((resolve) => {
    initFilledOrderPendingPreview(data);
    const existing = data.existing_order || null;

    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';

    const box = document.createElement('div');
    box.style.cssText =
      'background: #14141a; border: 1px solid #2a2a33; border-radius: 12px; ' +
      'padding: 24px; max-width: 480px; width: 92%; ' +
      'box-shadow: 0 12px 40px rgba(0,0,0,0.5); font-family: inherit; color: #e6e6e6;';

    const statsHost = document.createElement('div');
    statsHost.id = 'fo-save-stats-host';

    function renderStats() {
      const s = getFilledOrderPendingSaveStats();
      const checkBtn = s.unmatched > 0
        ? `<button type="button" id="fo-check-unmatched-btn" class="btn btn-primary" style="padding:4px 10px; font-size:12px; margin-left:10px;">Check</button>`
        : '';
      const rows = [
        ['Total lines', s.total],
        ['Matched', s.matched],
        ['Unmatched', `${s.unmatched}${checkBtn}`],
        ['Rejected', s.rejected],
        ['Added to AM', s.added],
        ['Flagged', s.flagged],
        ['Qty column', data.quantity_column_used || '—'],
        ['Unit', data.quantity_unit_used || '—'],
        ['Category', data.category || '—'],
        ['Season', data.season || '—'],
      ];
      statsHost.innerHTML = rows.map(([label, value]) => (
        '<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid #23232b;">' +
        `<span style="color:#aaa;">${label}</span>` +
        `<span style="color:#e6e6e6; font-weight:600;">${value}</span>` +
        '</div>'
      )).join('');

      const checkEl = statsHost.querySelector('#fo-check-unmatched-btn');
      if (checkEl) {
        checkEl.addEventListener('click', async (e) => {
          e.stopPropagation();
          await showFilledOrderMismatchReviewModal(renderStats);
        });
      }
    }

    box.innerHTML =
      '<div style="font-size:16px; font-weight:600; margin-bottom:6px; color:#e0b84a;">' +
      (existing ? 'Replace existing filled order?' : 'Save filled order?') +
      '</div>' +
      (existing
        ? `<div style="font-size:13px; color:#ffb648; margin-bottom:10px; line-height:1.5; padding:10px; border:1px solid rgba(255,182,72,0.45); border-radius:8px; background:rgba(255,182,72,0.08);">
            <strong>Duplicate detected.</strong> ${data.distributor_name || 'This distributor'} already has a
            <strong>${existing.category}</strong> order for season <strong>${existing.season}</strong>
            (${existing.total_lines ?? 0} lines, uploaded ${(existing.created_at || '').slice(0, 10)}).
            Saving will replace that order.
          </div>`
        : '') +
      '<div style="font-size:13px; color:#999; margin-bottom:14px; line-height:1.5;">Review summary below. ' +
      'If unmatched &gt; 0, click <strong>Check</strong> to review details before saving.</div>';

    const placeholder = document.createElement('div');
    placeholder.id = 'fo-save-stats-host';
    box.appendChild(placeholder);
    box.appendChild(statsHost);
    placeholder.replaceWith(statsHost);

    const confirmBtn = document.createElement('div');
    confirmBtn.id = 'fo-save-confirm-btn';
    confirmBtn.style.cssText = 'background:#2563eb; color:#fff; text-align:center; padding:10px; border-radius:8px; cursor:pointer; font-weight:600; margin:16px 0 10px;';
    confirmBtn.textContent = existing ? 'Replace existing order' : 'Save filled order';
    const cancelBtn = document.createElement('div');
    cancelBtn.id = 'fo-save-cancel-btn';
    cancelBtn.style.cssText = 'text-align:center; padding:8px; border-radius:8px; cursor:pointer; color:#f87171; border:1px solid #5c2b2b;';
    cancelBtn.textContent = 'Cancel — reject upload';
    box.appendChild(confirmBtn);
    box.appendChild(cancelBtn);

    renderStats();

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve({ confirmed: false, replace: false }); }
    }
    document.addEventListener('keydown', onKeydown);

    box.querySelector('#fo-save-confirm-btn').addEventListener('click', () => {
      cleanup();
      resolve({ confirmed: true, replace: Boolean(existing) });
    });
    box.querySelector('#fo-save-cancel-btn').addEventListener('click', () => {
      cleanup();
      filledOrdersState.pendingPreview = null;
      resolve({ confirmed: false, replace: false });
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        cleanup();
        filledOrdersState.pendingPreview = null;
        resolve({ confirmed: false, replace: false });
      }
    });
  });
}

function showFilledOrderDuplicateModal(data) {
  const existing = data.existing_order || {};
  const uploadedOn = (existing.created_at || '').slice(0, 10);
  const name = data.distributor_name || existing.distributor_name_raw || 'This distributor';
  const category = data.category || existing.category || 'this category';
  const season = data.season || existing.season || 'this season';
  const lines = existing.total_lines;

  let message =
    `${name} already has a <strong>${category}</strong> filled order for season <strong>${season}</strong>.`;
  if (uploadedOn) {
    message += ` It was uploaded on ${uploadedOn}.`;
  }
  if (lines != null) {
    message += ` (${lines} lines)`;
  }
  message += '<br><br>Replace it with this upload?';

  return showSimpleConfirmModal(
    'Order already exists',
    message,
    'Replace existing',
    'Cancel'
  );
}

function showFilledOrderQtyColumnModal(data) {
  return new Promise((resolve) => {
    const relationships = data.relationships || [];
    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';
    const box = document.createElement('div');
    box.style.cssText =
      'background: #1c1c22; color: #eee; padding: 20px; border-radius: 10px; ' +
      'max-width: 640px; width: 92%; max-height: 82vh; overflow-y: auto;';

    const rows = (data.candidates || []).map((c) => {
      const rel = relationships.find((r) => r.sum_column_index === c.column_index);
      const note = rel ? `<div style="color:#7fdc7f;font-size:0.8rem;margin-top:4px;">✓ ${rel.note}</div>` : '';
      return `
        <label style="display:block; border:1px solid #3a3a44; border-radius:8px; padding:10px; margin-bottom:8px; cursor:pointer;">
          <input type="radio" name="fo-qty-col-choice" value="${c.column_label.replace(/"/g, '&quot;')}" style="margin-right:8px;" />
          <strong>${c.column_label}</strong>
          <div style="font-size:0.8rem; color:#aaa;">Samples: ${(c.sample_values || []).join(', ')} (${c.populated_count} rows populated)</div>
          ${note}
        </label>
      `;
    }).join('');

    box.innerHTML = `
      <h2 style="margin-top:0;">Confirm Quantity Column</h2>
      <p class="subtitle">${data.message || 'Multiple possible quantity columns found.'}</p>
      ${rows}
      <div style="display:flex; gap:10px; margin-top:14px;">
        <button id="fo-qty-confirm-btn" class="btn btn-primary">Confirm</button>
        <button id="fo-qty-cancel-btn" class="btn btn-secondary">Cancel</button>
      </div>
    `;
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve(null); }
    }
    document.addEventListener('keydown', onKeydown);

    box.querySelector('#fo-qty-confirm-btn').addEventListener('click', () => {
      const checked = box.querySelector('input[name="fo-qty-col-choice"]:checked');
      if (!checked) { alert('Please select a column.'); return; }
      cleanup();
      resolve(checked.value);
    });
    box.querySelector('#fo-qty-cancel-btn').addEventListener('click', () => {
      cleanup(); resolve(null);
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { cleanup(); resolve(null); }
    });
  });
}

function showFilledOrderDistributorModal(data) {
  return new Promise((resolve) => {
    const suggestion = data.suggested_distributor;
    const filenameHint = data.filename_hint || 'file';

    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';
    const box = document.createElement('div');
    box.style.cssText =
      'background: #1c1c22; color: #eee; padding: 20px; border-radius: 10px; ' +
      'max-width: 480px; width: 92%;';

    if (suggestion) {
      box.innerHTML = `
        <h2 style="margin-top:0;">Distributor Confirm</h2>
        <p>File: <strong>${filenameHint}</strong></p>
        <p>Is this order for <strong>${suggestion.display_name}</strong>?</p>
        <p style="font-size:0.85rem;color:#aaa;">${suggestion.match_reason || ''}</p>
        <div style="display:flex; flex-wrap:wrap; gap:10px; margin-top:14px;">
          <button id="fo-dist-yes-btn" class="btn btn-primary">Yes — correct distributor</button>
          <button id="fo-dist-no-btn" class="btn btn-secondary">No — select manually</button>
          <button id="fo-dist-cancel-btn" class="btn btn-secondary">Cancel</button>
        </div>
      `;
    } else {
      box.innerHTML = `
        <h2 style="margin-top:0;">Distributor Not Found</h2>
        <p>Could not detect distributor from filename <strong>"${filenameHint}"</strong>.</p>
        <p class="subtitle">Select from the dropdown below, then click Upload again.</p>
        <button id="fo-dist-manual-btn" class="btn btn-primary">OK</button>
      `;
    }

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve(null); }
    }
    document.addEventListener('keydown', onKeydown);

    if (suggestion) {
      box.querySelector('#fo-dist-yes-btn').addEventListener('click', () => {
        cleanup(); resolve('yes');
      });
      box.querySelector('#fo-dist-no-btn').addEventListener('click', () => {
        cleanup(); resolve('manual');
      });
      box.querySelector('#fo-dist-cancel-btn').addEventListener('click', () => {
        cleanup(); resolve(null);
      });
    } else {
      box.querySelector('#fo-dist-manual-btn').addEventListener('click', () => {
        cleanup(); resolve('manual');
      });
    }
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { cleanup(); resolve(null); }
    });
  });
}

function showFilledOrderSeasonModal(lastSeason) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.style.cssText =
      'position: fixed; inset: 0; background: rgba(0,0,0,0.65); ' +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';
    const box = document.createElement('div');
    box.style.cssText =
      'background: #1c1c22; color: #eee; padding: 20px; border-radius: 10px; ' +
      'max-width: 420px; width: 92%;';

    if (lastSeason) {
      box.innerHTML = `
        <h2 style="margin-top:0;">Season Confirm</h2>
        <p>Is order also for <strong>${lastSeason}</strong>?</p>
        <div style="display:flex; gap:10px; margin-top:14px;">
          <button id="fo-season-yes-btn" class="btn btn-primary">Yes</button>
          <button id="fo-season-no-btn" class="btn btn-secondary">No, different season</button>
        </div>
      `;
    } else {
      box.innerHTML = `
        <h2 style="margin-top:0;">Season</h2>
        <p class="subtitle">This is your first filled order — enter the season (e.g. AW26, SS27).</p>
        <div class="form-group">
          <input type="text" id="fo-season-manual-input" placeholder="e.g. AW26" />
        </div>
        <div style="display:flex; gap:10px; margin-top:14px;">
          <button id="fo-season-manual-confirm-btn" class="btn btn-primary">Confirm</button>
          <button id="fo-season-cancel-btn" class="btn btn-secondary">Cancel</button>
        </div>
      `;
    }
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { cleanup(); resolve(null); }
    }
    document.addEventListener('keydown', onKeydown);

    if (lastSeason) {
      box.querySelector('#fo-season-yes-btn').addEventListener('click', () => {
        cleanup(); resolve({ useLastSeason: true, season: lastSeason });
      });
      box.querySelector('#fo-season-no-btn').addEventListener('click', () => {
        cleanup();
        showFilledOrderSeasonModal(null).then(resolve);
      });
    } else {
      box.querySelector('#fo-season-manual-confirm-btn').addEventListener('click', () => {
        const val = box.querySelector('#fo-season-manual-input')?.value?.trim();
        if (!val) { alert('Season likho.'); return; }
        cleanup(); resolve({ useLastSeason: false, season: val });
      });
      box.querySelector('#fo-season-cancel-btn').addEventListener('click', () => {
        cleanup(); resolve(null);
      });
    }
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { cleanup(); resolve(null); }
    });
  });
}

async function uploadFilledOrder(extraParams = {}, uiPrefix = null) {
  const prefix = getFilledOrderUploadPrefix(uiPrefix);
  filledOrdersState.uploadUiPrefix = prefix;
  const fileInput = document.getElementById(`${prefix}-upload-file`);
  const resultEl = document.getElementById(`${prefix}-upload-result`);
  const manualDistributorId = document.getElementById(`${prefix}-distributor-select`)?.value;
  const category = document.getElementById(`${prefix}-category-select`)?.value;
  const file = fileInput?.files?.[0];

  const distributorId =
    extraParams.distributor_id ||
    filledOrdersState.confirmedDistributorId ||
    manualDistributorId ||
    null;

  if (!file) {
    if (resultEl) resultEl.textContent = 'Please select an Excel file first.';
    return;
  }

  if (resultEl) resultEl.textContent = 'Uploading...';
  const formData = new FormData();
  formData.append('file', file);
  if (distributorId) {
    formData.append('distributor_id', distributorId);
  }
  if (category) formData.append('category', extraParams.category || category);
  Object.entries(extraParams).forEach(([key, value]) => {
    if (value !== undefined && value !== null && key !== 'distributor_id') {
      formData.append(key, value);
    }
  });

  const carryParams = (more = {}) => ({
    distributor_id: distributorId,
    category: extraParams.category || category || more.category,
    season: extraParams.season || more.season,
    use_last_season: extraParams.use_last_season || more.use_last_season,
    confirm_commit: more.confirm_commit,
    confirm_replace: more.confirm_replace,
    ...more,
  });

  try {
    const response = await fetchWithAuth('/api/v1/filled-orders/upload', {
      method: 'POST',
      body: formData,
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Upload failed'));
    }

    if (data.status === 'distributor_confirmation_required') {
      applyFilledOrderUploadPreview(data, prefix);
      const choice = await showFilledOrderDistributorModal(data);
      if (choice === 'yes' && data.suggested_distributor?.id) {
        filledOrdersState.confirmedDistributorId = data.suggested_distributor.id;
        setFilledOrderDistributorSelect(data.suggested_distributor.id, prefix);
        await uploadFilledOrder({ distributor_id: data.suggested_distributor.id }, prefix);
        return;
      }
      if (choice === 'manual') {
        filledOrdersState.confirmedDistributorId = null;
        if (resultEl) {
          resultEl.textContent = 'Select the correct distributor from the dropdown, then click Upload again.';
        }
        document.getElementById(`${prefix}-distributor-select`)?.focus();
        return;
      }
      if (resultEl) resultEl.textContent = 'Upload cancelled.';
      return;
    }

    if (data.status === 'qty_column_confirmation_required') {
      if (data.category) setFilledOrderCategorySelect(data.category, prefix);
      const chosen = await showFilledOrderQtyColumnModal(data);
      if (!chosen) {
        if (resultEl) resultEl.textContent = 'Upload cancelled — quantity column not confirmed.';
        return;
      }
      await fetchWithAuth('/api/v1/filled-orders/confirm-qty-column', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          distributor_id: distributorId,
          category: data.category,
          confirmed_column_name: chosen,
        }),
      });
      await uploadFilledOrder(carryParams({ category: data.category }), prefix);
      return;
    }

    if (data.status === 'season_confirmation_required') {
      if (data.category) setFilledOrderCategorySelect(data.category, prefix);
      const seasonChoice = await showFilledOrderSeasonModal(data.last_season);
      if (!seasonChoice) {
        if (resultEl) resultEl.textContent = 'Upload cancelled — season not confirmed.';
        return;
      }
      await uploadFilledOrder(carryParams({
        category: data.category,
        season: seasonChoice.useLastSeason ? undefined : seasonChoice.season,
        use_last_season: seasonChoice.useLastSeason ? 'true' : undefined,
      }), prefix);
      return;
    }

    if (data.status === 'confirmation_required') {
      if (data.category) setFilledOrderCategorySelect(data.category, prefix);
      if (data.distributor_id) setFilledOrderDistributorSelect(data.distributor_id, prefix);
      const saveChoice = await showFilledOrderSaveConfirmModal(data);
      if (!saveChoice?.confirmed) {
        if (resultEl) resultEl.textContent = 'Upload cancelled.';
        return;
      }
      await uploadFilledOrder(carryParams({
        category: data.category,
        season: data.season,
        confirm_commit: 'true',
        confirm_replace: saveChoice.replace ? 'true' : undefined,
        skip_item_keys: JSON.stringify(
          filledOrdersState.pendingPreview
            ? [...filledOrdersState.pendingPreview.rejectedKeys]
            : [],
        ),
      }), prefix);
      filledOrdersState.pendingPreview = null;
      return;
    }

    if (data.status === 'duplicate_order_confirmation_required') {
      const handled = await handleFilledOrderDuplicateResponse(
        data,
        prefix,
        carryParams({
          category: data.category,
          season: data.season,
          distributor_id: data.distributor_id,
        }),
        resultEl,
      );
      if (handled) return;
    }

    if (data.status === 'success') {
      if (resultEl) {
        const replaced = data.replaced_existing ? ' (replaced previous order)' : '';
        resultEl.textContent = `Saved: ${data.filled_order.total_lines} lines ` +
          `(${data.filled_order.matched_lines} matched, ${data.filled_order.unmatched_lines} unmatched, ` +
          `${data.filled_order.flagged_lines} flagged).${replaced}`;
      }
      filledOrdersState.confirmedDistributorId = null;
      resetFilledOrderUploadForm(prefix);
      await loadFilledOrdersList();
      if (prefix === 'of-fo') {
        await loadOrderFulfillmentCatalogSummary();
      }
    }
  } catch (error) {
    if (isFilledOrderDuplicateMessage(error.message)) {
      await handleFilledOrderDuplicateResponse(
        {
          category: extraParams.category || category,
          season: extraParams.season,
          distributor_id: distributorId,
        },
        prefix,
        carryParams({
          category: extraParams.category || category,
          season: extraParams.season,
          distributor_id: distributorId,
        }),
        resultEl,
      );
      return;
    }
    if (resultEl) resultEl.textContent = error.message || 'Upload failed';
  }
}

function foInputAttrValue(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;');
}

function foRowFieldInput(field, value, type = 'text') {
  if (type === 'unit') {
    const unit = value === 'bales' ? 'bales' : 'pieces';
    return `<select class="fo-row-input" data-fo-field="${field}">
      <option value="bales"${unit === 'bales' ? ' selected' : ''}>bales</option>
      <option value="pieces"${unit === 'pieces' ? ' selected' : ''}>pieces</option>
    </select>`;
  }
  if (type === 'matched') {
    return `<select class="fo-row-input" data-fo-field="${field}">
      <option value="1"${value ? ' selected' : ''}>Matched</option>
      <option value="0"${!value ? ' selected' : ''}>Unmatched</option>
    </select>`;
  }
  const inputType = type === 'number' ? 'number' : 'text';
  const step = type === 'number' ? ' step="any"' : '';
  return `<input class="fo-row-input" data-fo-field="${field}" type="${inputType}"${step} value="${foInputAttrValue(value)}" />`;
}

function startFilledOrderRowEdit(itemId) {
  filledOrdersState.editingItemId = itemId;
  renderFilledOrderDetailTable();
}

function cancelFilledOrderRowEdit() {
  filledOrdersState.editingItemId = null;
  renderFilledOrderDetailTable();
}

function collectFilledOrderRowPayload(row) {
  const payload = {};
  const numberFields = new Set([
    'mrp', 'ptr', 'ex_mill_price', 'bale_size_used', 'raw_qty_value', 'final_piece_qty',
  ]);
  row.querySelectorAll('[data-fo-field]').forEach((el) => {
    const field = el.dataset.foField;
    let value = el.value;
    if (field === 'matched') {
      value = value === '1';
    } else if (numberFields.has(field)) {
      value = value === '' ? null : Number(value);
    } else {
      value = value.trim();
    }
    payload[field] = value;
  });
  return payload;
}

async function saveFilledOrderRowEdit(itemId) {
  const orderId = filledOrdersState.activeOrderId;
  if (!orderId) return;
  const row = document.querySelector(`#fo-detail-tbody tr[data-item-id="${itemId}"]`);
  if (!row) return;
  const payload = collectFilledOrderRowPayload(row);

  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/items/${itemId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Update failed'));
    }
    const idx = filledOrdersState.activeOrderItems.findIndex((i) => i.id === itemId);
    if (idx >= 0) filledOrdersState.activeOrderItems[idx] = data.item;
    filledOrdersState.editingItemId = null;
    const labelEl = document.getElementById('fo-detail-label');
    if (labelEl && data.filled_order) {
      const fo = data.filled_order;
      labelEl.textContent = `${fo.category} | ${fo.season} | ${fo.total_lines} lines`;
    }
    renderFilledOrderDetailTable();
    await loadFilledOrdersList();
  } catch (error) {
    await showSimpleConfirmModal('Could not save', error.message || 'Update failed', 'OK', 'Close');
  }
}

function renderFilledOrderDetailTable() {
  const tbody = document.getElementById('fo-detail-tbody');
  if (!tbody) return;
  const items = filledOrdersState.activeOrderItems;
  const editingId = filledOrdersState.editingItemId;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="13">No line items.</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((it) => {
    const rowStyle = it.is_clean_bale_multiple ? '' : 'background: rgba(220,50,50,0.18);';
    const editing = editingId === it.id;
    if (editing) {
      return `<tr data-item-id="${it.id}" class="fo-row-editing" style="${rowStyle}">
        <td>${foRowFieldInput('brand', it.brand)}</td>
        <td>${foRowFieldInput('size', it.size)}</td>
        <td>${foRowFieldInput('product_type', it.product_type)}</td>
        <td>${foRowFieldInput('mrp', it.mrp, 'number')}</td>
        <td>${foRowFieldInput('ptr', it.ptr, 'number')}</td>
        <td>${foRowFieldInput('ex_mill_price', it.ex_mill_price, 'number')}</td>
        <td>${foRowFieldInput('bale_size_used', it.bale_size_used, 'number')}</td>
        <td>${foRowFieldInput('raw_qty_value', it.raw_qty_value, 'number')}</td>
        <td>${foRowFieldInput('detected_unit', it.detected_unit, 'unit')}</td>
        <td>${foRowFieldInput('final_piece_qty', it.final_piece_qty, 'number')}</td>
        <td>${foRowFieldInput('matched', it.matched, 'matched')}</td>
        <td>${it.is_clean_bale_multiple ? '' : '🚩'}</td>
        <td class="fo-row-actions">
          <button class="btn btn-primary" style="padding:2px 8px;font-size:0.7rem;" onclick="saveFilledOrderRowEdit(${it.id})">Save</button>
          <button class="btn btn-secondary" style="padding:2px 8px;font-size:0.7rem;margin-left:4px;" onclick="cancelFilledOrderRowEdit()">Cancel</button>
        </td>
      </tr>`;
    }
    return `<tr data-item-id="${it.id}" style="${rowStyle}">
      <td>${formatFilledOrderValue(it.brand)}</td>
      <td>${formatFilledOrderValue(it.size)}</td>
      <td>${formatFilledOrderValue(it.product_type)}</td>
      <td>${formatFilledOrderAmount(it.mrp)}</td>
      <td>${formatFilledOrderAmount(it.ptr)}</td>
      <td>${formatFilledOrderAmount(it.ex_mill_price)}</td>
      <td>${formatFilledOrderQty(it.bale_size_used)}</td>
      <td>${formatFilledOrderQty(it.raw_qty_value)}</td>
      <td>${formatFilledOrderValue(it.detected_unit)}</td>
      <td>${formatFilledOrderQty(it.final_piece_qty)}</td>
      <td>${it.matched ? '✅' : '❌'}</td>
      <td>${it.is_clean_bale_multiple ? '' : '🚩'}</td>
      <td class="fo-row-actions">
        <button class="btn btn-secondary" style="padding:2px 8px;font-size:0.7rem;" onclick="startFilledOrderRowEdit(${it.id})">Edit</button>
        ${!it.matched ? `
          <button class="btn btn-secondary" style="padding:2px 6px;font-size:0.7rem;margin-left:4px;" onclick="resolveFilledOrderUnmatched(${it.id}, 'add_to_article_master')" title="Add to Article Master">+AM</button>
          <button class="btn btn-secondary" style="padding:2px 6px;font-size:0.7rem;margin-left:4px;" onclick="resolveFilledOrderUnmatched(${it.id}, 'skip')" title="Leave unresolved">Skip</button>
        ` : ''}
        <button class="btn btn-danger" style="padding:2px 6px;font-size:0.7rem;margin-left:4px;" onclick="deleteFilledOrderItem(${it.id})">Delete</button>
      </td>
    </tr>`;
  }).join('');
}

async function openFilledOrderDetail(orderId) {
  filledOrdersState.activeOrderId = orderId;
  filledOrdersState.editingItemId = null;
  const labelEl = document.getElementById('fo-detail-label');
  const tbody = document.getElementById('fo-detail-tbody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="13">Loading...</td></tr>';
  toggleModal('fo-detail-modal', true);
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}`);
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load order'));
    }
    if (labelEl) {
      const fo = data.filled_order;
      labelEl.textContent =
        `${fo.category} | ${fo.season} | ${fo.total_lines} lines | ` +
        `${formatFilledOrderQty(fo.total_bales)} bales | ` +
        `${formatFilledOrderQty(fo.total_piece_qty)} pcs | ` +
        `Ex-mill ${formatFilledOrderAmount(fo.total_ex_mill_value)}`;
    }
    filledOrdersState.activeOrderItems = data.items || [];
    renderFilledOrderDetailTable();
  } catch (error) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="13">${error.message || 'Failed to load'}</td></tr>`;
  }
}

async function resolveFilledOrderUnmatched(itemId, action) {
  const orderId = filledOrdersState.activeOrderId;
  if (!orderId) return;
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/resolve-unmatched`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_id: itemId, action }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Resolve failed'));
    }
    await openFilledOrderDetail(orderId);
    await loadFilledOrdersList();
  } catch (error) {
    alert(error.message || 'Resolve failed');
  }
}

async function deleteFilledOrderItem(itemId) {
  const orderId = filledOrdersState.activeOrderId;
  if (!orderId) return;
  const ok = await showSimpleConfirmModal(
    'Delete line item?',
    'This line will be permanently removed from the filled order.',
    'Delete',
    'Cancel'
  );
  if (!ok) return;
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/items/${itemId}`, {
      method: 'DELETE',
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Delete failed'));
    }
    await openFilledOrderDetail(orderId);
    await loadFilledOrdersList();
  } catch (error) {
    alert(error.message || 'Delete failed');
  }
}

async function linkFilledOrderToSalesOrder(orderId) {
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/so-candidates`);
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load Sales Order candidates'));
    }
    const candidates = (data.candidates || []).filter((c) => !c.already_linked);
    if (!candidates.length) {
      alert('No Sales Orders found for this distributor yet. Upload the SO first, then link it here.');
      return;
    }
    const suggested = candidates.find((c) => c.suggested) || candidates[0];
    const summary = candidates.map((c, i) => (
      `${i + 1}. Tracking #${c.tracking_id} — Order Ref ${c.order_ref_no || '—'}`
    )).join('\n');
    const choice = window.prompt(
      `Link this Filled Order to a Sales Order:\n\n${summary}\n\nEnter number (1-${candidates.length}) or cancel:`,
      '1',
    );
    if (!choice) return;
    const index = Number(choice) - 1;
    if (!Number.isInteger(index) || index < 0 || index >= candidates.length) {
      alert('Invalid selection');
      return;
    }
    const trackingId = candidates[index].tracking_id;
    const linkResp = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/link-so`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tracking_id: trackingId }),
    });
    const linkData = await parseApiResponse(linkResp);
    if (!linkResp.ok) {
      throw new Error(getApiErrorMessage(linkData, 'Link failed'));
    }
    alert(`Linked to Sales Order tracking #${trackingId}. Ordered qty is now populated in reconciliation.`);
    await loadFilledOrdersList();
  } catch (error) {
    alert(error.message || 'Link failed');
  }
}

async function deleteFilledOrder(orderId) {
  const ok = await showSimpleConfirmModal(
    'Delete filled order?',
    '<strong style="color:#f87171;">Warning:</strong> This permanently deletes the entire filled order and all line items. This cannot be undone.',
    'Delete order',
    'Cancel'
  );
  if (!ok) return;
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}`, { method: 'DELETE' });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Delete failed'));
    }
    await loadFilledOrdersList();
    if (document.getElementById('of-fo-summary')) {
      await loadOrderFulfillmentCatalogSummary();
    }
  } catch (error) {
    await showSimpleConfirmModal('Delete failed', error.message || 'Could not delete this order.', 'OK', 'Close');
  }
}

async function downloadFilledOrder(orderId) {
  if (!orderId) return;
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}/download`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getApiErrorMessage(data, 'Download failed'));
    }
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/);
    const filename = match ? match[1] : `filled_order_${orderId}.xlsx`;
    const blob = await response.blob();
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    alert(error.message || 'Download failed');
  }
}

function closeMobileNav() {
  document.body.classList.remove('mobile-nav-open');
  const toggle = document.getElementById('mobile-nav-toggle');
  const backdrop = document.getElementById('mobile-nav-backdrop');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
  if (backdrop) backdrop.hidden = true;
}

function openMobileNav() {
  document.body.classList.add('mobile-nav-open');
  const toggle = document.getElementById('mobile-nav-toggle');
  const backdrop = document.getElementById('mobile-nav-backdrop');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');
  if (backdrop) backdrop.hidden = false;
}

function toggleMobileNav() {
  if (document.body.classList.contains('mobile-nav-open')) closeMobileNav();
  else openMobileNav();
}

document.addEventListener('DOMContentLoaded', initApp);

