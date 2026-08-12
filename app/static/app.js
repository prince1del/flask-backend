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

/** Theme accent — follows Settings → Theme (--hop-c-accent / --nx-gold), never hardcode Nexora gold. */
function nxThemeAccentCss() {
  return 'var(--hop-c-accent, var(--nx-gold, #25e0ff))';
}

/** Surface + text tokens for JS-built modals so light themes don't keep the dark gold Nexora look. */
function nxThemeUi() {
  const theme = document.documentElement.getAttribute('data-hop-theme') || 'emerald';
  const isLight = theme === 'bright' || theme === 'emerald' || theme === 'custom' || !theme;
  const accent = nxThemeAccentCss();
  if (isLight) {
    return {
      isLight: true,
      accent,
      accentFg: '#ffffff',
      overlay: 'rgba(15, 23, 42, 0.48)',
      boxBg: 'var(--hop-c-card, #ffffff)',
      boxBorder: 'var(--hop-c-border, #DED3BE)',
      boxFg: 'var(--hop-c-text, #1F1F1F)',
      // Stronger than soft muted so modal copy stays readable on white
      muted: 'var(--hop-c-muted, #4A4338)',
      soft: 'var(--hop-c-bg-soft, #F5EFE0)',
      rowBorder: 'var(--hop-c-border, #DED3BE)',
      secondaryBorder: 'var(--hop-c-border, #C4B89A)',
      secondaryFg: 'var(--hop-c-text, #1F1F1F)',
      secondaryBg: 'var(--hop-c-bg, #F8F4EA)',
      // Fixed status inks — avoid neon/theme greens in review tables
      ok: '#166534',
      up: '#8A6D12',
      down: '#9F1239',
      warn: '#9A3412',
    };
  }
  return {
    isLight: false,
    accent,
    accentFg: '#0b1220',
    overlay: 'rgba(0, 0, 0, 0.65)',
    boxBg: '#14141a',
    boxBorder: '#2a2a33',
    boxFg: '#e6e6e6',
    muted: '#a3a3a3',
    soft: '#1a1a22',
    rowBorder: '#23232b',
    secondaryBorder: '#444444',
    secondaryFg: '#e6e6e6',
    secondaryBg: 'transparent',
    ok: 'var(--hop-c-accent, #C9A227)',
    up: 'var(--hop-c-accent, #C9A227)',
    down: '#fb7185',
    warn: '#fbbf24',
  };
}

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
  allDistributorRecords: [],
  allRetailerRecords: [],
  rawDistributorRecords: [],
  rawRetailerRecords: [],
  purgedContactKeys: null,
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
  distributorsLoadSeq: 0,
  retailersLoadSeq: 0,
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

function profileAvatarInitial(username) {
  const raw = String(username || '').trim();
  const match = raw.match(/[A-Za-z0-9]/);
  return (match ? match[0] : 'U').toUpperCase();
}

/** Gmail-style initial; circle color follows Ask / theme via CSS (--nx-gold). */
function setUserProfileButton(username) {
  const menu = document.getElementById('nx-profile-menu');
  const el = document.getElementById('user-info') || document.getElementById('user-name');
  if (!el) return;
  const label = String(username || authState.username || '').trim() || 'User';
  const initial = profileAvatarInitial(label);
  el.textContent = initial;
  el.title = label;
  el.setAttribute('aria-label', `${label} — account menu`);
  el.classList.add('profile-btn', 'profile-btn--avatar');
  el.classList.remove('hidden');
  el.style.removeProperty('background');
  el.style.removeProperty('color');
  el.style.removeProperty('display');
  if (menu) {
    menu.classList.remove('hidden');
    menu.style.removeProperty('display');
  }
  const nameEl = document.getElementById('nx-profile-username');
  if (nameEl) nameEl.textContent = label;
  const dropAvatar = document.getElementById('nx-profile-dropdown-avatar');
  if (dropAvatar) dropAvatar.textContent = initial;
  const roleEl = document.getElementById('nx-profile-role');
  if (roleEl) {
    const roleRaw = String(authState.role || '').trim();
    const workspace = String(authState.workspaceId || '').trim();
    const roleLabel = roleRaw
      ? roleRaw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
      : '';
    roleEl.textContent = [roleLabel, workspace].filter(Boolean).join(' · ');
  }
  // Hide any leftover standalone logout buttons across shells
  document.querySelectorAll('.btn-logout, .hop-nav-logout').forEach((btn) => {
    if (btn.closest?.('#nx-profile-dropdown')) return;
    btn.classList.add('hidden');
    btn.style.display = 'none';
  });
}

function closeUserProfileMenu() {
  const menu = document.getElementById('nx-profile-menu');
  const drop = document.getElementById('nx-profile-dropdown');
  const btn = document.getElementById('user-info');
  if (drop) drop.classList.add('hidden');
  if (menu) menu.classList.remove('is-open');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function toggleUserProfileMenu(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const drop = document.getElementById('nx-profile-dropdown');
  const btn = document.getElementById('user-info');
  const menu = document.getElementById('nx-profile-menu');
  if (!drop || !btn) return;
  const willOpen = drop.classList.contains('hidden');
  if (willOpen) {
    drop.classList.remove('hidden');
    menu?.classList.add('is-open');
    btn.setAttribute('aria-expanded', 'true');
  } else {
    closeUserProfileMenu();
  }
}

document.addEventListener('click', (e) => {
  const menu = document.getElementById('nx-profile-menu');
  if (!menu || menu.contains(e.target)) return;
  closeUserProfileMenu();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeUserProfileMenu();
});

// Sales Executives only see what's actually been built and tested so
// far in NEXORA — everything else (Purchase, Inventory, Finance,
// Reports, Analytics, Orders, Approvals, Banking, and the still-
// decorative Sales sub-tabs) is hidden from the UI only. Settings stays
// visible (theme is app-wide). Nothing on the backend is touched.
const SALES_EXECUTIVE_HIDDEN_NAV_IDS = [
  'nav-purchase', 'nav-inventory', 'nav-finance', 'nav-reports',
  'nav-analytics', 'nav-orders', 'nav-approvals',
  'nav-banking',
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
  setUserProfileButton(authState.username || 'User');
  if (authState.accessToken) {
    goToHomePage();
  }
}

function applyRoleBasedUI() {
  if (authState.role === 'hop_admin') {
    applyHopRoleUI();
    setUserProfileButton(authState.username || 'User');
    return;
  }
  if (authState.role !== 'sales_executive') {
    document.body.classList.remove('bd-hop-ui');
    setUserProfileButton(authState.username || 'User');
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
    syncBdBrandFromCompanyProfile();
    goToHomePage();
  }
  setUserProfileButton(authState.username || 'User');
  requestAnimationFrame(() => bdSyncSidebarScroll());
}

function syncLoginOpenBodyClass() {
  const loginModal = document.getElementById('loginModal');
  const open = !!(loginModal && !loginModal.classList.contains('hidden'));
  document.body.classList.toggle('nx-login-open', open);
}

function loadAuthState() {
  authState.accessToken = localStorage.getItem('authAccessToken');
  authState.refreshToken = localStorage.getItem('authRefreshToken');
  authState.username = localStorage.getItem('authUsername');
  authState.role = localStorage.getItem('authRole');
  authState.workspaceId = localStorage.getItem('authWorkspaceId');
  const storedUid = localStorage.getItem('authUserId');
  authState.userId = storedUid ? Number(storedUid) : null;
  const askNexoraButton = document.getElementById('ask-nexora-btn');

  if (authState.accessToken) {
    document.getElementById('loginModal')?.classList.add('hidden');
    document.getElementById('dashboard')?.classList.remove('hidden');
    askNexoraButton?.classList.remove('hidden');
    resetGlobalSearchUi();
    if (typeof hopSyncThemeForCurrentUser === 'function') {
      hopSyncThemeForCurrentUser();
    }
    applyRoleBasedUI();
    setUserProfileButton(authState.username || 'Admin User');
  } else {
    askNexoraButton?.classList.add('hidden');
    if (typeof hopResetThemeChromeToDefault === 'function') {
      hopResetThemeChromeToDefault();
    }
  }
  syncLoginOpenBodyClass();
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
  initStandardModalDismiss();
  setGlobalSearchBarVisible(true);
  // Keep minimized dock on <body> so Customers / other pages never hide it with #dashboard.
  ensureWidgetDock();
  bindNexoraChatOverlayDismiss();
  bindMobileNavDismissGestures();
  loadAuthState();
  const syncNav = () => {
    if (typeof bdSyncSidebarScroll === 'function') bdSyncSidebarScroll();
  };
  requestAnimationFrame(syncNav);
  window.setTimeout(syncNav, 50);
  window.setTimeout(syncNav, 300);
  window.addEventListener('resize', syncNav);
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
  if (typeof initNxYmdPickers === 'function') initNxYmdPickers(document);
  window.addEventListener('resize', scheduleCustomersLayout);
  window.addEventListener('resize', scheduleArticleMasterLayout);
}

const TA_WIDGET_POS_KEY = 'dashboardTaWidgetPosition';
let dashboardTaWidgetDragBound = false;

function dashboardWidgetPosScope() {
  const uid = authState?.userId || authState?.username || 'anon';
  return String(uid);
}

function taWidgetStorageKey() {
  return `${TA_WIDGET_POS_KEY}:${dashboardWidgetPosScope()}`;
}

function readWidgetPos(storageKey) {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
      return { x: saved.x, y: saved.y };
    }
  } catch (e) {
    /* ignore bad JSON */
  }
  return null;
}

function writeWidgetPos(storageKey, x, y) {
  localStorage.setItem(storageKey, JSON.stringify({ x, y }));
}

function whenDashboardBoardReady(callback, tries = 0) {
  const board = getDashboardWidgetBoard();
  if (board && board.clientWidth > 40 && board.clientHeight > 40) {
    callback();
    return;
  }
  if (tries >= 40) {
    callback();
    return;
  }
  requestAnimationFrame(() => whenDashboardBoardReady(callback, tries + 1));
}

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

  whenDashboardBoardReady(() => {
    const saved = readWidgetPos(taWidgetStorageKey()) || readWidgetPos(TA_WIDGET_POS_KEY);
    if (saved) {
      widget.style.left = `${saved.x}px`;
      widget.style.top = `${saved.y}px`;
      clampDashboardTaWidgetPosition(layer, widget);
    } else {
      centerDashboardTaWidget(layer, widget);
    }
  });
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
  let didMove = false;

  const onPointerDown = (event) => {
    if (event.button !== undefined && event.button !== 0) return;
    if (event.target.closest?.('.ta-widget-minimize-btn')) return;
    dragging = true;
    didMove = false;
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
    if (Math.abs(event.clientX - startX) > 2 || Math.abs(event.clientY - startY) > 2) {
      didMove = true;
    }
    widget.style.left = `${x}px`;
    widget.style.top = `${y}px`;
  };

  const onPointerUp = (event) => {
    if (!dragging || (pointerId !== null && event.pointerId !== pointerId)) return;
    dragging = false;
    pointerId = null;
    widget.classList.remove('is-dragging');
    if (!didMove) return;
    const x = parseFloat(widget.style.left) || 0;
    const y = parseFloat(widget.style.top) || 0;
    writeWidgetPos(taWidgetStorageKey(), x, y);
    // keep legacy key in sync for older sessions
    writeWidgetPos(TA_WIDGET_POS_KEY, x, y);
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
  return `${FO_WIDGET_POS_PREFIX}${dashboardWidgetPosScope()}:${season}`;
}

function foWidgetStorageKeyLegacy(season) {
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

function applyFoSeasonWidgetPosition(layer, widget, season, index) {
  if (!layer || !widget) return;
  whenDashboardBoardReady(() => {
    const saved =
      readWidgetPos(foWidgetStorageKey(season)) ||
      readWidgetPos(foWidgetStorageKeyLegacy(season));
    if (saved) {
      widget.style.left = `${saved.x}px`;
      widget.style.top = `${saved.y}px`;
      clampDashboardTaWidgetPosition(layer, widget);
    } else {
      const def = defaultFoWidgetPosition(Number.isFinite(index) ? index : 0);
      widget.style.left = `${def.x}px`;
      widget.style.top = `${def.y}px`;
    }
  });
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
    if (event.target.closest?.('.fo-season-cat-toggle')) return;
    if (event.target.closest?.('.fo-season-cat-body')) return;
    if (!onHandle && !onHeader) return;

    const season = widget.dataset.foSeason || '';
    const board = getDashboardWidgetBoard() || layer;
    const boardRect = board.getBoundingClientRect();
    const widgetRect = widget.getBoundingClientRect();
    foWidgetDragState.active = {
      layer,
      widget,
      season,
      storageKey: foWidgetStorageKey(season),
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originLeft: widgetRect.left - boardRect.left,
      originTop: widgetRect.top - boardRect.top,
      didMove: false,
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
    if (Math.abs(event.clientX - drag.startX) > 2 || Math.abs(event.clientY - drag.startY) > 2) {
      drag.didMove = true;
    }
    widget.style.left = `${x}px`;
    widget.style.top = `${y}px`;
  };

  const onPointerUp = (event) => {
    const drag = foWidgetDragState.active;
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.widget.classList.remove('is-dragging');
    if (drag.didMove) {
      const x = parseFloat(drag.widget.style.left) || 0;
      const y = parseFloat(drag.widget.style.top) || 0;
      writeWidgetPos(drag.storageKey, x, y);
      writeWidgetPos(foWidgetStorageKeyLegacy(drag.season), x, y);
    }
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

function toggleFoSeasonCat(btn) {
  const block = btn?.closest?.('.fo-season-cat-block');
  if (!block) return;
  const open = block.classList.toggle('is-open');
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function buildFoSeasonWidgetCard(seasonData, index) {
  const season = seasonData.season || '—';
  const title = seasonData.label || season;
  const safeId = `${season}_${index}`.replace(/[^a-zA-Z0-9_-]/g, '_');
  const categories = seasonData.categories || [];
  const flatRows = seasonData.rows || [];
  const catNames = categories.map((c) => c.category).filter(Boolean);
  const rowCount = flatRows.length || categories.reduce((n, c) => n + ((c.rows || []).length), 0);
  const nestByCategory = categories.length > 1 || catNames.some((n) => n && n !== '—');
  const bodyHtml = nestByCategory
    ? categories.map((block, catIndex) => {
        const catLabel = block.category || 'Other';
        const distN = (block.rows || []).length;
        return `
        <div class="fo-season-cat-block"${catIndex < categories.length - 1 ? ' style="margin-bottom:8px;"' : ''}>
          <button type="button" class="fo-season-cat-toggle" aria-expanded="false" onclick="toggleFoSeasonCat(this)">
            <span class="fo-season-cat-toggle-copy">
              <strong>${foEscapeText(catLabel)}</strong>
              <em>${distN} distributor${distN === 1 ? '' : 's'}</em>
            </span>
            <span class="fo-season-cat-toggle-meta">
              <span>${formatFilledOrderAmount(block.total_ex_mill_value)}</span>
              <span class="fo-season-cat-chevron" aria-hidden="true">▾</span>
            </span>
          </button>
          <div class="fo-season-cat-body">
            <table class="ta-fy-overview-table ta-excel-table ta-excel-table-widget">
              <thead>
                <tr><th>Distributor</th><th>Qty</th><th>Amount</th></tr>
              </thead>
              <tbody>${renderFoSeasonOverviewRows(block.rows || [])}</tbody>
            </table>
          </div>
        </div>`;
      }).join('')
    : `<table class="ta-fy-overview-table ta-excel-table ta-excel-table-widget">
        <thead><tr><th>Distributor</th><th>Qty</th><th>Amount</th></tr></thead>
        <tbody>${renderFoSeasonOverviewRows(flatRows)}</tbody>
      </table>`;
  return `
    <article
      id="dashboard-fo-widget-${safeId}"
      class="fo-season-widget ta-playing-card-compact card-highlight ta-draggable-widget fo-playing-card-compact"
      data-fo-season="${foEscapeText(season)}"
      data-fo-widget-index="${index}"
    >
      <button type="button" class="ta-widget-minimize-btn" data-fo-season="${foEscapeText(season)}" onclick="minimizeFoWidget(this.dataset.foSeason)" aria-label="Minimize widget" title="Minimize">─</button>
      <button type="button" class="ta-widget-drag-handle" aria-label="Drag to move" title="Drag">⠿</button>
      <div class="ta-playing-card-inner">
        <div class="ta-playing-card-header ta-widget-drag-surface">
          <h2>${foEscapeText(title)}</h2>
          <p>${catNames.length ? foEscapeText(catNames.join(' · ')) + ' · ' : ''}${rowCount} distributor${rowCount === 1 ? '' : 's'} · tap a category</p>
        </div>
        <div class="ta-playing-card-table-wrap ta-excel-sheet ta-widget-sheet fo-season-table-wrap">
          ${bodyHtml}
        </div>
        <div class="ta-playing-card-footer fo-season-widget-footer">
          <div class="fo-season-widget-totals">
            <span>${formatFilledOrderQty(seasonData.total_piece_qty)} pcs</span>
            <span>${formatFilledOrderAmount(seasonData.total_ex_mill_value)}</span>
          </div>
          <button type="button" class="ta-widget-details-btn fo-season-open-btn" onclick="openModule('OrderFulfillment')">Open details →</button>
        </div>
      </div>
    </article>
  `;
}

function buildFoSeasonOverviewFromOrders(orders) {
  const slotGroups = {};
  const seasonCats = {};
  (orders || []).forEach((order) => {
    const season = (order.season || '—').trim() || '—';
    const category = (order.category || '—').trim() || '—';
    if (!seasonCats[season]) seasonCats[season] = new Set();
    seasonCats[season].add(category);
    const slotKey = `${season}||${category}`;
    if (!slotGroups[slotKey]) slotGroups[slotKey] = {};
    const key = order.distributor_id || order.distributor_name_raw || order.id;
    const dist = (filledOrdersState.distributors || []).find((d) => d.id === order.distributor_id);
    const name = dist
      ? getFilledOrderDistributorLabel(dist)
      : (order.distributor_name_raw || `Distributor #${order.distributor_id || '?'}`);
    if (!slotGroups[slotKey][key]) {
      slotGroups[slotKey][key] = {
        distributor_name: name,
        total_piece_qty: 0,
        total_ex_mill_value: 0,
      };
    }
    const row = slotGroups[slotKey][key];
    row.total_piece_qty += Number(order.total_piece_qty) || 0;
    row.total_ex_mill_value += Number(order.total_ex_mill_value) || 0;
  });
  return Object.keys(seasonCats)
    .sort()
    .reverse()
    .map((season) => {
      const cats = [...seasonCats[season]].sort((a, b) => a.localeCompare(b));
      const seasonDist = {};
      const categories = cats.map((category) => {
        const rows = Object.values(slotGroups[`${season}||${category}`] || {}).sort((a, b) =>
          (a.distributor_name || '').localeCompare(b.distributor_name || ''),
        );
        rows.forEach((row) => {
          const dname = row.distributor_name || '';
          if (!seasonDist[dname]) {
            seasonDist[dname] = {
              distributor_name: row.distributor_name,
              total_piece_qty: 0,
              total_ex_mill_value: 0,
            };
          }
          seasonDist[dname].total_piece_qty += row.total_piece_qty;
          seasonDist[dname].total_ex_mill_value += row.total_ex_mill_value;
        });
        return {
          category,
          rows,
          total_piece_qty: rows.reduce((sum, row) => sum + row.total_piece_qty, 0),
          total_ex_mill_value: rows.reduce((sum, row) => sum + row.total_ex_mill_value, 0),
        };
      });
      const seasonRows = Object.values(seasonDist).sort((a, b) =>
        (a.distributor_name || '').localeCompare(b.distributor_name || ''),
      );
      return {
        season,
        label: season,
        categories,
        rows: seasonRows,
        total_piece_qty: seasonRows.reduce((sum, row) => sum + row.total_piece_qty, 0),
        total_ex_mill_value: seasonRows.reduce((sum, row) => sum + row.total_ex_mill_value, 0),
      };
    });
}

function renderFoSeasonWidgets(layer, seasons) {
  if (!layer) return;
  // Remount clears DOM — drop stale minimize chips so AW26 doesn't stay "gone"
  let dockDirty = false;
  for (const id of [...minimizedWidgets.keys()]) {
    if (String(id).startsWith('dashboard-fo-widget-')) {
      minimizedWidgets.delete(id);
      dockDirty = true;
    }
  }
  if (dockDirty) renderWidgetDock();

  if (!seasons.length) {
    layer.innerHTML = '';
    return;
  }
  layer.innerHTML = seasons.map((seasonData, index) => buildFoSeasonWidgetCard(seasonData, index)).join('');
  seasons.forEach((seasonData, index) => {
    const safeId = (seasonData.season || '').replace(/[^a-zA-Z0-9_-]/g, '_');
    const widget = document.getElementById(`dashboard-fo-widget-${safeId}`);
    if (!widget) return;
    applyFoSeasonWidgetPosition(layer, widget, seasonData.season, index);
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

  const onDashboard =
    currentModuleKey === 'dashboard' &&
    !!document.querySelector('#dashboard .content-inner.dashboard-ta-focus');

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
    // Never force-hide while off Dashboard — that made AW26 "vanish" after Order Desk
    // uploads. Visibility is restored in showDashboardWorkspace().
    if (!seasons.length) {
      layer.classList.add('hidden');
    } else if (onDashboard) {
      layer.classList.remove('hidden');
    }
  } catch (error) {
    if (!onDashboard) {
      return;
    }
    layer.classList.remove('hidden');
    layer.innerHTML = `
      <article class="fo-season-widget ta-playing-card-compact fo-playing-card-compact ta-draggable-widget" style="left:16px;top:16px;pointer-events:auto">
        <div class="ta-playing-card-inner">
          <p style="padding:1rem;color:#b91c1c;font-size:0.75rem;">${foEscapeText(error.message || 'Unable to load order widgets.')}</p>
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
    // Theme is per-login — load this account's palette from server (not previous user's)
    if (typeof hopSyncThemeForCurrentUser === 'function') {
      await hopSyncThemeForCurrentUser(data.data.user.ui_theme || null);
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
    if (typeof syncLoginOpenBodyClass === 'function') syncLoginOpenBodyClass();
    setUserProfileButton(authState.username);
    if (errorEl) {
      errorEl.textContent = '';
      errorEl.classList.remove('login-timeout-msg');
      errorEl.classList.add('error');
    }
    resetGlobalSearchUi();
    applyRoleBasedUI();
    setUserProfileButton(authState.username);
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
  // Manual logout (any UI / any id) — confirm first. Session timeout skips confirm.
  const skipConfirm = reason === 'timeout' || reason === 'session-expired' || reason === 'forced';
  if (!skipConfirm) {
    const ok = typeof nexoraConfirm === 'function'
      ? await nexoraConfirm('Log out of NEXORA? You will need to sign in again.', {
          title: 'Log out',
          okText: 'Log out',
          cancelText: 'Stay signed in',
          danger: true,
        })
      : window.confirm('Log out of NEXORA? You will need to sign in again.');
    if (!ok) return;
  }

  try {
    await fetch('/logout', {
      method: 'GET',
      credentials: 'same-origin',
    });
  } catch (e) {
    console.warn('Logout request failed:', e);
  }

  clearAuthLocalState();
  if (typeof hopResetThemeChromeToDefault === 'function') {
    hopResetThemeChromeToDefault();
  }
  document.body.classList.remove('bd-hop-ui', 'customers-page-active', 'nexora-ask-open', 'hop-active', 'hop-module-fullscreen');
  document.documentElement.classList.remove('hop-active', 'hop-module-fullscreen');
  document.getElementById('hop-executive-workspace')?.classList.remove('hop-ws--fullscreen');
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
  if (typeof syncLoginOpenBodyClass === 'function') syncLoginOpenBodyClass();
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
    .map((row) => {
      if (row.summary_error) {
        return `
        <tr title="Achievement summary failed for this year — check server logs">
          <td>${row.fy || '—'}</td>
          <td>${formatTaTableAmount(row.target)}</td>
          <td colspan="2" style="color:#b45309">Unavailable (calculation error)</td>
        </tr>`;
      }
      return `
        <tr>
          <td>${row.fy || '—'}</td>
          <td>${formatTaTableAmount(row.target)}</td>
          <td>${formatTaTableAmount(row.achievement)}</td>
          <td class="${taPercentClass(row.percentage)}">${formatTaPercent(row.percentage)}</td>
        </tr>`;
    })
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
        infoEl.textContent = 'Connect Google Drive. Nexora uses only the NEXORA folder (Downloads, Invoices, Reports, Backups).';
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
      statusEl.textContent = 'Google Drive connected · NEXORA folder';
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
  // Prefer mime from cache when available
  const cached = cloudHubFilesCache.find(
    (f) => String(f.file_id || f.id || '') === String(fileId),
  );
  const mime = String(cached?.mime_type || cached?.file_type || '').toLowerCase();
  if (mime.includes('folder')) {
    if (typeof nexoraToast === 'function') {
      nexoraToast('Folders open in Drive only — pick a file to download in NEXORA.', 'warn');
    }
    return;
  }

  // Download through NEXORA using the connected Drive token (not Chrome's Google account).
  (async () => {
    const statusEl = document.getElementById('cloud-hub-status');
    const label = fileName || fileId;
    if (statusEl) statusEl.textContent = `Downloading ${label}…`;
    try {
      const response = await fetchWithAuth(
        `/api/v1/storage/files/${encodeURIComponent(fileId)}/download`,
      );
      if (!response.ok) {
        let message = 'Download failed';
        try {
          const data = await response.json();
          message = data.error || data.message || message;
        } catch (_) { /* ignore */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      let downloadName = label;
      const disposition = response.headers.get('Content-Disposition') || '';
      const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      const plainMatch = disposition.match(/filename=\"?([^\";]+)\"?/i);
      if (utfMatch) {
        try { downloadName = decodeURIComponent(utfMatch[1]); } catch (_) { /* keep */ }
      } else if (plainMatch) {
        downloadName = plainMatch[1];
      }
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = downloadName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      if (statusEl) statusEl.textContent = 'Google Drive connected';
      if (typeof nexoraToast === 'function') {
        nexoraToast(`Downloaded: ${downloadName}`, 'success');
      }
    } catch (error) {
      if (statusEl) statusEl.textContent = 'Google Drive connected';
      if (typeof nexoraToast === 'function') {
        nexoraToast(error.message || 'Unable to download file from Drive', 'error');
      } else {
        alert(error.message || 'Unable to download file from Drive');
      }
    }
  })();
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
    countEl.textContent = `${files.length} file${files.length === 1 ? '' : 's'} · click to download in NEXORA`;
  }
  tbody.innerHTML = files
    .map((file) => {
      const name = foEscapeText(file.file_name || file.name || file.title || '—');
      const fileId = foEscapeText(file.file_id || file.id || '');
      const size = formatBytes(file.file_size_bytes ?? file.file_size ?? 0);
      const updated = foEscapeText(formatCloudHubDate(file.modified_at || file.updated_at || file.last_synced || file.created_at));
      const kind = cloudHubFileKind(file);
      return `<tr class="cloud-hub-file-row" tabindex="0" role="button" data-file-id="${fileId}" data-file-name="${name}" title="Download in NEXORA">
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
        '<tr class="cloud-hub-empty-row"><td colspan="4">NEXORA folder is empty. Put files in Drive → NEXORA, then Sync.</td></tr>';
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

function _dsrTodayIso() {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function _dsrEsc(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function loadMarketVisitWorkspace() {
  const statusEl = document.getElementById('dsr-market-status');
  const tbody = document.getElementById('dsr-visits-tbody');
  const countEl = document.getElementById('dsr-visit-count');
  const visitDateEl = document.getElementById('dsr-visit-date');
  const exportFrom = document.getElementById('dsr-export-from');
  const exportTo = document.getElementById('dsr-export-to');
  const today = _dsrTodayIso();
  if (typeof initNxYmdPickers === 'function') {
    initNxYmdPickers(document.getElementById('market-visit-workspace') || document);
    initNxYmdPickers(document.getElementById('dsr-export-modal') || document);
  }
  if (visitDateEl && !visitDateEl.value) visitDateEl.value = today;
  if (exportFrom && !exportFrom.value) exportFrom.value = today;
  if (exportTo && !exportTo.value) exportTo.value = today;
  if (typeof syncNxYmdPicker === 'function') {
    syncNxYmdPicker('dsr-visit-date');
    syncNxYmdPicker('dsr-export-from');
    syncNxYmdPicker('dsr-export-to');
  }
  if (!authState.accessToken) {
    if (statusEl) statusEl.textContent = 'Please login first.';
    return;
  }
  if (countEl) countEl.textContent = 'Loading…';
  try {
    const response = await fetchWithAuth('/api/v1/dsr-market/visits?limit=200');
    const data = await response.json();
    if (!response.ok || data.success === false) {
      throw new Error(data.error?.message || data.error || 'Failed to load visits');
    }
    const rows = Array.isArray(data.data) ? data.data : [];
    if (countEl) countEl.textContent = `${rows.length} visit${rows.length === 1 ? '' : 's'}`;
    if (tbody) {
      if (!rows.length) {
        tbody.innerHTML = '<tr class="dsr-empty-row"><td colspan="5">No visits yet. Save one above.</td></tr>';
      } else {
        tbody.innerHTML = rows.map((r) => `
          <tr>
            <td>${_dsrEsc(r.visit_date)}</td>
            <td>${_dsrEsc(r.customer_name)}</td>
            <td>${_dsrEsc(r.channel_type || '')}</td>
            <td>${_dsrEsc(r.city_area || '')}</td>
            <td>${r.order_lacs != null ? _dsrEsc(r.order_lacs) : ''}</td>
          </tr>`).join('');
      }
    }
    if (statusEl) statusEl.textContent = '';
    await loadDsrCompetitorBrands({ selectBrand: null });
  } catch (error) {
    if (statusEl) statusEl.textContent = error.message || 'Unable to load visits.';
    if (countEl) countEl.textContent = 'Error';
    if (tbody) {
      tbody.innerHTML = '<tr class="dsr-empty-row"><td colspan="5">Unable to load visits.</td></tr>';
    }
  }
}

function openDsrExportModal() {
  const modal = document.getElementById('dsr-export-modal');
  const today = _dsrTodayIso();
  if (typeof initNxYmdPickers === 'function') {
    initNxYmdPickers(modal || document);
  }
  const exportFrom = document.getElementById('dsr-export-from');
  const exportTo = document.getElementById('dsr-export-to');
  if (exportFrom && !exportFrom.value) exportFrom.value = today;
  if (exportTo && !exportTo.value) exportTo.value = today;
  if (typeof syncNxYmdPicker === 'function') {
    syncNxYmdPicker('dsr-export-from');
    syncNxYmdPicker('dsr-export-to');
  }
  const statusEl = document.getElementById('dsr-export-status');
  if (statusEl) statusEl.textContent = '';
  if (modal) {
    modal.classList.remove('hidden');
    modal.setAttribute('aria-hidden', 'false');
  }
}

function closeDsrExportModal() {
  const modal = document.getElementById('dsr-export-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
  }
}

function toggleDsrSelect(event, inputId) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const root = document.querySelector(`.dsr-select[data-dsr-select="${inputId}"]`);
  if (!root) return;
  const panel = root.querySelector('.dsr-select-panel');
  const wasOpen = panel && !panel.classList.contains('hidden');
  closeAllDsrSelects();
  closeDsrBrandDropdown();
  if (!wasOpen && panel) {
    panel.classList.remove('hidden');
    root.classList.add('is-open');
  }
}

function closeAllDsrSelects() {
  document.querySelectorAll('.dsr-select').forEach((root) => {
    root.classList.remove('is-open');
    root.querySelector('.dsr-select-panel')?.classList.add('hidden');
  });
}

function pickDsrSelect(inputId, value) {
  const input = document.getElementById(inputId);
  const root = document.querySelector(`.dsr-select[data-dsr-select="${inputId}"]`);
  if (input) {
    input.value = value;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const label = root?.querySelector('.dsr-select-label');
  if (label) label.textContent = value === '' ? 'Select' : value;
  root?.querySelectorAll('.dsr-select-option').forEach((btn) => {
    btn.classList.toggle('is-selected', (btn.getAttribute('data-value') || '') === value);
  });
  closeAllDsrSelects();
}

function syncDsrBrandDropdownLabel() {
  const label = document.getElementById('dsr-brand-dd-label');
  const selected = Array.from(
    document.querySelectorAll('input[name="dsr-competitor"]:checked')
  ).map((el) => (el.value || '').trim()).filter(Boolean);
  if (!label) return;
  if (!selected.length) {
    label.textContent = 'Select brands';
    return;
  }
  if (selected.length <= 2) {
    label.textContent = selected.join(', ');
    return;
  }
  label.textContent = `${selected.length} selected · ${selected.slice(0, 2).join(', ')}…`;
}

function toggleDsrBrandDropdown(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const wrap = document.getElementById('dsr-competitor-chips');
  const panel = document.getElementById('dsr-brand-dd-panel');
  const trigger = document.getElementById('dsr-brand-dd-trigger');
  if (!wrap || !panel || !trigger) return;
  const open = panel.classList.contains('hidden');
  closeDsrBrandDropdown();
  if (open) {
    panel.classList.remove('hidden');
    wrap.classList.add('is-open');
    trigger.setAttribute('aria-expanded', 'true');
  }
}

function closeDsrBrandDropdown() {
  const wrap = document.getElementById('dsr-competitor-chips');
  const panel = document.getElementById('dsr-brand-dd-panel');
  const trigger = document.getElementById('dsr-brand-dd-trigger');
  panel?.classList.add('hidden');
  wrap?.classList.remove('is-open');
  trigger?.setAttribute('aria-expanded', 'false');
}

function renderDsrCompetitorBrandChips(brands, selectedNames) {
  const list = document.getElementById('dsr-brand-dd-list');
  if (!list) return;
  const selected = new Set(
    (selectedNames || []).map((n) => String(n || '').trim().toLowerCase()).filter(Boolean)
  );
  const names = (brands || []).map((b) => String(b || '').trim()).filter(Boolean);
  const fallback = [
    'Bombay Dyeing', 'Ddecor', 'Portico', 'Raymonds', 'Sansar', 'Spaces', 'Swayam', 'Welspun',
  ];
  const finalNames = names.length ? names : fallback;
  list.innerHTML = finalNames.map((name) => {
    const checked = selected.has(name.toLowerCase()) ? ' checked' : '';
    return `<label class="dsr-brand-dd-option"><input type="checkbox" name="dsr-competitor" value="${_dsrEsc(name)}"${checked} onchange="syncDsrBrandDropdownLabel()"> ${_dsrEsc(name)}</label>`;
  }).join('');
  syncDsrBrandDropdownLabel();
}

document.addEventListener('click', (e) => {
  const brandWrap = document.getElementById('dsr-competitor-chips');
  if (brandWrap && !brandWrap.contains(e.target)) closeDsrBrandDropdown();
  if (!e.target.closest?.('.dsr-select')) closeAllDsrSelects();
});

async function loadDsrCompetitorBrands({ selectBrand } = {}) {
  try {
    const response = await fetchWithAuth('/api/v1/dsr-market/competitor-brands');
    const raw = await response.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : null;
    } catch (_) {
      throw new Error('Competitor brands API unavailable (not JSON). Try Refresh after deploy.');
    }
    if (!response.ok || data?.success === false) {
      throw new Error(data?.error?.message || data?.error || 'Failed to load brands');
    }
    const brands = Array.isArray(data?.data) ? data.data : [];
    const selected = selectBrand ? [selectBrand] : Array.from(
      document.querySelectorAll('input[name="dsr-competitor"]:checked')
    ).map((el) => el.value);
    renderDsrCompetitorBrandChips(brands, selected);
  } catch (error) {
    console.warn('loadDsrCompetitorBrands', error);
    syncDsrBrandDropdownLabel();
  }
}

async function promptAddDsrCompetitorBrand() {
  const name = (window.prompt('New competitor brand name:') || '').trim();
  if (!name) return;
  try {
    const response = await fetchWithAuth('/api/v1/dsr-market/competitor-brands', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    const raw = await response.text();
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : null;
    } catch (_) {
      throw new Error('Add brand failed — server returned HTML instead of JSON.');
    }
    if (!response.ok || data?.success === false) {
      throw new Error(data?.error?.message || data?.error || 'Failed to add brand');
    }
    const brands = Array.isArray(data?.data) ? data.data : [];
    const keep = Array.from(
      document.querySelectorAll('input[name="dsr-competitor"]:checked')
    ).map((el) => el.value);
    if (!keep.map((x) => x.toLowerCase()).includes(name.toLowerCase())) keep.push(name);
    renderDsrCompetitorBrandChips(brands, keep);
    if (typeof nexoraToast === 'function') nexoraToast(`Brand added: ${name}`, 'success');
  } catch (error) {
    if (typeof nexoraToast === 'function') nexoraToast(error.message || 'Unable to add brand', 'error');
  }
}

async function saveDsrMarketVisit(event) {
  if (event) event.preventDefault();
  const statusEl = document.getElementById('dsr-market-status');
  const btn = document.getElementById('dsr-save-btn');
  const customerName = (document.getElementById('dsr-customer-name')?.value || '').trim();
  const visitDate = (document.getElementById('dsr-visit-date')?.value || '').trim();
  if (!customerName || !visitDate) {
    if (statusEl) statusEl.textContent = 'Customer name and date are required.';
    return false;
  }
  const orderRaw = (document.getElementById('dsr-order-lacs')?.value || '').trim();
  const competitorBrands = Array.from(
    document.querySelectorAll('input[name="dsr-competitor"]:checked')
  )
    .map((el) => (el.value || '').trim())
    .filter(Boolean)
    .join(', ');
  const body = {
    visit_date: visitDate,
    customer_name: customerName,
    location: (document.getElementById('dsr-location')?.value || '').trim() || null,
    owner_name: (document.getElementById('dsr-owner-name')?.value || '').trim() || null,
    contact_nos: (document.getElementById('dsr-contact-nos')?.value || '').trim() || null,
    channel_type: (document.getElementById('dsr-channel-type')?.value || '').trim() || null,
    customer_type: (document.getElementById('dsr-customer-type')?.value || '').trim() || null,
    address: (document.getElementById('dsr-address')?.value || '').trim() || null,
    city_area: (document.getElementById('dsr-city-area')?.value || '').trim() || null,
    existing_or_new: (document.getElementById('dsr-existing-or-new')?.value || '').trim() || null,
    order_lacs: orderRaw === '' ? null : Number(orderRaw),
    bed: (document.getElementById('dsr-bed')?.value || '').trim() || null,
    bath: (document.getElementById('dsr-bath')?.value || '').trim() || null,
    tob: (document.getElementById('dsr-tob')?.value || '').trim() || null,
    others: (document.getElementById('dsr-others')?.value || '').trim() || null,
    competitor_brands: competitorBrands || null,
    branding_yn: (document.getElementById('dsr-branding-yn')?.value || '').trim() || null,
    retailer_feedback: (document.getElementById('dsr-feedback')?.value || '').trim() || null,
    sm_remarks: (document.getElementById('dsr-sm-remarks')?.value || '').trim() || null,
  };
  if (btn) btn.disabled = true;
  try {
    const response = await fetchWithAuth('/api/v1/dsr-market/visits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok || data.success === false) {
      throw new Error(data.error?.message || data.error || 'Save failed');
    }
    // Keep location for next visit; clear customer-specific fields only.
    ['dsr-customer-name', 'dsr-owner-name', 'dsr-contact-nos', 'dsr-customer-type', 'dsr-address', 'dsr-city-area',
      'dsr-order-lacs', 'dsr-bed', 'dsr-bath', 'dsr-tob', 'dsr-others',
      'dsr-feedback', 'dsr-sm-remarks'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    document.querySelectorAll('input[name="dsr-competitor"]').forEach((el) => {
      el.checked = false;
    });
    if (typeof syncDsrBrandDropdownLabel === 'function') syncDsrBrandDropdownLabel();
    if (typeof nexoraToast === 'function') nexoraToast('Visit saved', 'success');
    if (statusEl) statusEl.textContent = 'Visit saved.';
    await loadMarketVisitWorkspace();
  } catch (error) {
    if (statusEl) statusEl.textContent = error.message || 'Unable to save visit.';
    if (typeof nexoraToast === 'function') nexoraToast(error.message || 'Save failed', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
  return false;
}

function syncDsrOrderLacsTotal() {
  const part = (id) => {
    const raw = (document.getElementById(id)?.value || '').trim().replace(/,/g, '');
    if (!raw) return 0;
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  };
  const bedEl = document.getElementById('dsr-bed');
  const bathEl = document.getElementById('dsr-bath');
  const tobEl = document.getElementById('dsr-tob');
  const othersEl = document.getElementById('dsr-others');
  const totalEl = document.getElementById('dsr-order-lacs');
  if (!totalEl) return;
  const allBlank = [bedEl, bathEl, tobEl, othersEl].every((el) => !(el?.value || '').trim());
  if (allBlank) {
    totalEl.value = '';
    return;
  }
  const sum = part('dsr-bed') + part('dsr-bath') + part('dsr-tob') + part('dsr-others');
  totalEl.value = String(Math.round(sum * 100) / 100);
}

async function exportDsrMarketExcel() {
  const statusEl = document.getElementById('dsr-export-status') || document.getElementById('dsr-market-status');
  const from = (document.getElementById('dsr-export-from')?.value || '').trim();
  const to = (document.getElementById('dsr-export-to')?.value || '').trim();
  if (!from || !to) {
    if (statusEl) statusEl.textContent = 'Pick from and to dates for export.';
    return;
  }
  if (statusEl) statusEl.textContent = 'Preparing Excel…';
  const includeOwner = !!document.getElementById('dsr-export-include-owner')?.checked;
  try {
    const response = await fetchWithAuth(
      `/api/v1/dsr-market/export?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&include_owner=${includeOwner ? '1' : '0'}`,
    );
    if (!response.ok) {
      let message = 'Export failed';
      try {
        const data = await response.json();
        message = data.error?.message || data.error || message;
      } catch (_) { /* ignore */ }
      throw new Error(message);
    }
    const blob = await response.blob();
    let downloadName = `DSR_${from}_to_${to}.xlsx`;
    const disposition = response.headers.get('Content-Disposition') || '';
    const utfMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plainMatch = disposition.match(/filename=\"?([^\";]+)\"?/i);
    if (utfMatch) {
      try { downloadName = decodeURIComponent(utfMatch[1]); } catch (_) { /* keep */ }
    } else if (plainMatch) {
      downloadName = plainMatch[1];
    }
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = downloadName;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(link.href);
    if (statusEl) statusEl.textContent = `Downloaded ${downloadName}`;
    if (typeof nexoraToast === 'function') nexoraToast(`Downloaded: ${downloadName}`, 'success');
    closeDsrExportModal();
  } catch (error) {
    if (statusEl) statusEl.textContent = error.message || 'Unable to export.';
    if (typeof nexoraToast === 'function') nexoraToast(error.message || 'Export failed', 'error');
  }
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

/** Distributor / retailer forms — protect against accidental backdrop dismiss. */
const CUSTOMER_FORM_MODAL_IDS = new Set([
  'master-distributor-form-modal',
  'master-retailer-form-modal',
  'distributor-form-modal',
  'retailer-form-modal',
]);

let modalBackdropPointerDownEl = null;

function isCustomerFormModal(modal) {
  return Boolean(modal && CUSTOMER_FORM_MODAL_IDS.has(modal.id));
}

function isCustomerFormDirty(modal) {
  if (!modal) return false;
  const fields = modal.querySelectorAll(
    'input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), select, textarea',
  );
  for (const el of fields) {
    if (el.disabled || el.readOnly) continue;
    if (String(el.value || '').trim()) return true;
  }
  return false;
}

/** Never auto-dismiss these (login must stay until successful auth). */
const NON_DISMISSIBLE_MODAL_IDS = new Set([
  'loginModal',
]);

function canAutoDismissModal(modal) {
  if (!modal || modal.classList.contains('hidden')) return false;
  if (NON_DISMISSIBLE_MODAL_IDS.has(modal.id)) return false;
  return true;
}

/** Close a standard `.modal` without saving (Cancel / Esc / backdrop). */
function dismissStandardModal(modal) {
  if (!canAutoDismissModal(modal)) return;
  if (modal.id === 'global-search-modal') {
    closeGlobalSearchModal();
    return;
  }
  if (modal.id) closeModal(modal.id);
  else modal.classList.add('hidden');
}

async function requestDismissModal(modal) {
  if (!canAutoDismissModal(modal)) return;
  if (isCustomerFormModal(modal) && isCustomerFormDirty(modal)) {
    const ok = await nexoraConfirm(
      'This form has unsaved details. Closing will discard them. Close anyway?',
      {
        title: 'Discard form?',
        danger: true,
        okText: 'Close',
        cancelText: 'Keep editing',
      },
    );
    if (!ok) return;
  }
  dismissStandardModal(modal);
}

/** Use from Cancel / ✕ on distributor & retailer forms. */
function safeCloseModal(id) {
  const modal = document.getElementById(id);
  if (!modal) return;
  requestDismissModal(modal);
}

function getTopVisibleStandardModal() {
  const open = [...document.querySelectorAll('.modal:not(.hidden)')].filter(
    (modal) => !NON_DISMISSIBLE_MODAL_IDS.has(modal.id),
  );
  return open.length ? open[open.length - 1] : null;
}

function isBlockingOverlayOpen() {
  const confirm = document.getElementById('nx-confirm-modal');
  if (confirm && !confirm.classList.contains('hidden')) return true;
  // Dynamic prompts (confirm / download / conflict) sit above forms
  return !!document.querySelector('body > div[style*="position: fixed"][style*="z-index: 9"]');
}

/**
 * Esc + intentional backdrop click closes open `.modal` dialogs.
 * Accidental: drag from inside form → release outside does NOT close.
 * Dirty customer forms ask confirm before discard.
 */
function initStandardModalDismiss() {
  if (document.documentElement.dataset.modalDismissBound === '1') return;
  document.documentElement.dataset.modalDismissBound = '1';

  document.addEventListener('pointerdown', (event) => {
    const modal = event.target?.closest?.('.modal');
    modalBackdropPointerDownEl = (modal && event.target === modal) ? modal : null;
  }, true);

  document.addEventListener('click', (event) => {
    const modal = event.target?.closest?.('.modal');
    if (!modal || modal.classList.contains('hidden')) return;
    // Only the dimmed backdrop (the .modal itself), not .modal-content
    if (event.target !== modal) return;
    // Must press AND release on backdrop — stops accidental outside release.
    if (modalBackdropPointerDownEl !== modal) {
      modalBackdropPointerDownEl = null;
      return;
    }
    modalBackdropPointerDownEl = null;
    requestDismissModal(modal);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (event.defaultPrevented) return;
    if (isBlockingOverlayOpen()) return;
    const modal = getTopVisibleStandardModal();
    if (modal) {
      event.preventDefault();
      requestDismissModal(modal);
      return;
    }
    if (articleMasterState.selectionMode) {
      const amWs = document.getElementById('article-master-workspace');
      if (amWs && !amWs.classList.contains('hidden')) {
        event.preventDefault();
        exitArticleMasterSelectionMode();
      }
    }
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
  const panel = section.querySelector('.tab-panel:not(.hidden)');
  if (!panel) return;
  const wrap = panel.querySelector('.party-master-scroll-wrapper');
  if (!wrap) return;
  const pagination = panel.querySelector('[id$="-pagination"]');
  const apply = () => {
    const panelH = panel.getBoundingClientRect().height;
    const pagH = pagination ? pagination.getBoundingClientRect().height : 0;
    const fallback = Math.max(220, Math.floor(window.innerHeight - 220));
    const available = Math.max(180, Math.floor((panelH > 40 ? panelH : fallback) - pagH - 4));
    wrap.style.setProperty('height', `${available}px`, 'important');
    wrap.style.setProperty('max-height', `${available}px`, 'important');
    wrap.style.setProperty('min-height', '0', 'important');
    wrap.style.setProperty('flex', '1 1 0', 'important');
    wrap.style.setProperty('overflow', 'scroll', 'important');
    wrap.style.setProperty('overflow-x', 'scroll', 'important');
    wrap.style.setProperty('overflow-y', 'scroll', 'important');
  };
  apply();
  requestAnimationFrame(apply);
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
  const distributorFilter = document.getElementById('distributor-filter-wrap');
  const retailerFilter = document.getElementById('retailer-filter-wrap');

  if (tab === 'retailer') {
    distributorTab?.classList.remove('active');
    retailerTab?.classList.add('active');
    distributorPanel?.classList.add('hidden');
    retailerPanel?.classList.remove('hidden');
    distributorFilter?.classList.add('hidden');
    retailerFilter?.classList.remove('hidden');
    if (!partyMasterTableState.retailersLoaded && !partyMasterTableState.retailersLoading) {
      loadRetailers();
    }
  } else {
    distributorTab?.classList.add('active');
    retailerTab?.classList.remove('active');
    distributorPanel?.classList.remove('hidden');
    retailerPanel?.classList.add('hidden');
    distributorFilter?.classList.remove('hidden');
    retailerFilter?.classList.add('hidden');
  }
  scheduleCustomersLayout();
}

let partyDetailRecordsCache = [];

const PARTY_DETAIL_LABELS = {
  name: 'Name', firm_name: 'Firm Name', firmNickName: 'Firm Nickname',
  contactPerson: 'Contact Person', contact_person: 'Contact Person',
  contactPersonRole: 'Contact Person Role', contact_person_role: 'Contact Person Role',
  distributor: 'Distributor', distributor_name: 'Distributor',
  distributorCode: 'Distributor Code', distributor_code: 'Distributor Code',
  gst: 'GST Number', gst_no: 'GST Number', gst_number: 'GST Number',
  territory: 'Territory', zone: 'Zone',
  region: 'Region / State', state: 'State', city: 'City', location: 'City',
  pincode: 'Pincode', pin_code: 'Pincode', address: 'Address',
  storeType: 'Store Type', category: 'Category', phone: 'Phone',
  phone_number: 'Phone', phone_number_2: 'Phone 2', phone2: 'Phone 2',
  email: 'Email', paymentTerms: 'Payment Terms',
  creditLimit: 'Credit Limit', credit_limit: 'Credit Limit',
  birthday: 'Birthday', anniversary: 'Anniversary',
  secondaryName: 'Secondary Name', secondaryPhone: 'Secondary Phone',
  salesName: 'Sales Executive', salesPhone: 'Sales Phone', salesEmail: 'Sales Email',
};

function formatLakhs(value) {
  const num = Number(value || 0);
  return `${num.toLocaleString(undefined, { maximumFractionDigits: 2 })} L`;
}

/** Full INR → "3 Crore" / "15 Lakh" / "3 Crore 50 Lakh" */
function formatInrCrLakh(rupees) {
  const n = Math.round(Number(rupees || 0));
  if (n <= 0) return '0';
  const crore = Math.floor(n / 10000000);
  const rem = n % 10000000;
  const lakh = Math.floor(rem / 100000);
  const parts = [];
  if (crore) parts.push(`${crore} Crore`);
  if (lakh) parts.push(`${lakh} Lakh`);
  return parts.length ? parts.join(' ') : `₹${n.toLocaleString('en-IN')}`;
}

function lakhsToRupees(lakhs) {
  return Number(lakhs || 0) * 100000;
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

    if (fyTargetEl) {
      const fyRs = targetsData.data?.target_rupees ?? lakhsToRupees(fyTarget);
      const fyNarr = targetsData.data?.target_narration || formatInrCrLakh(fyRs);
      fyTargetEl.textContent = fyNarr;
      fyTargetEl.title = `₹${Number(fyRs || 0).toLocaleString('en-IN')}`;
    }
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
        .map((r) => {
          const rs = r.target_rupees ?? lakhsToRupees(r.target_lakhs);
          const narr = r.target_narration || formatInrCrLakh(rs);
          return `
          <tr>
            <td>${r.display_label || r.distributor_name || '—'}</td>
            <td title="₹${Number(rs || 0).toLocaleString('en-IN')}">${narr}</td>
          </tr>
        `;
        })
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
  const raw = document.getElementById('dist-target-amount')?.value || '0';
  const targetRupees = parseFloat(String(raw).replace(/,/g, ''));
  if (!yearId || !distributorName || Number.isNaN(targetRupees) || targetRupees <= 0) {
    setFormInlineStatus(
      'dist-target-status',
      'Fiscal year, distributor name, and target in ₹ (e.g. 30000000 = 3 Crore) are required.',
      'error',
    );
    return;
  }
  try {
    const response = await fetchWithAuth(`/api/v1/target-achievement/years/${yearId}/distributor-target`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        distributor_name: distributorName,
        target_rupees: targetRupees,
        nick: nick || undefined,
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error?.message || data.error || 'Unable to save distributor target');
    }
    const narr = data.data?.target_narration || formatInrCrLakh(targetRupees);
    const fyNarr = data.data?.fy_target_narration || '';
    closeModal('distributorTargetModal');
    setFormInlineStatus('dist-target-status', '');
    refreshActiveTargetUi();
    if (currentModuleKey === 'myday') loadExecutiveHome();
    if (fyNarr) {
      console.info(`Saved ${distributorName}: ${narr} · FY total ${fyNarr}`);
    }
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
  const safeLabel = String(label || '').trim() || 'customer';
  return `
    <a class="bd-party-action-btn bd-party-action-call" href="tel:${digits}">
      <span class="bd-party-action-ico" aria-hidden="true">☎</span>
      <span>Call ${safeLabel}</span>
    </a>
    <a class="bd-party-action-btn bd-party-action-wa" href="https://wa.me/${wa}" target="_blank" rel="noopener">
      <span class="bd-party-action-ico" aria-hidden="true">💬</span>
      <span>WhatsApp</span>
    </a>
  `;
}

function escapePartyDetailHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Excel/pandas often stores pincode as float (221002.0) — show as plain digits. */
function formatPartyFieldValue(key, value) {
  if (value === null || value === undefined || value === '') return value;
  const k = String(key || '').toLowerCase();
  const isPinLike = k === 'pincode' || k === 'pin_code' || k === 'pin' || k.endsWith('_pincode');
  const isCodeLike = isPinLike
    || k === 'buyercode'
    || k === 'buyer_code'
    || k === 'distributor_code'
    || k === 'distributorcode';
  if (!isCodeLike) return value;
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : String(Math.trunc(value));
  }
  const s = String(value).trim();
  if (/^\d+\.0+$/.test(s)) return s.replace(/\.0+$/, '');
  if (/^\d+\.0+$/i.test(s)) return s.split('.')[0];
  return s;
}

function showPartyDetail(record, editFn) {
  const title = document.getElementById('party-detail-title');
  const body = document.getElementById('party-detail-body');
  const editBtn = document.getElementById('party-detail-edit-btn');
  const actionsEl = document.getElementById('party-detail-actions');
  const extraEl = document.getElementById('party-detail-360');

  title.textContent = record.name || record.firm_name || 'Details';
  const preferredOrder = [
    'name', 'firm_name', 'firmNickName', 'contactPerson', 'contact_person',
    'distributorCode', 'distributor_code', 'distributor', 'distributor_name',
    'gst', 'gst_no', 'phone', 'phone_number', 'phone2', 'email',
    'city', 'state', 'pincode', 'territory', 'zone', 'address',
    'paymentTerms', 'storeType', 'store_type', 'creditLimit', 'credit_limit',
    'birthday', 'anniversary', 'secondaryName', 'secondaryPhone',
    'salesName', 'salesPhone', 'salesEmail',
  ];
  const entries = Object.entries(record).filter(([key, value]) => {
    if (['actions', 'distributorKey', 'partyId', 'partyType', 'source', 'buyerCode'].includes(key)) return false;
    return value !== null && value !== undefined && value !== '' && value !== '-';
  });
  entries.sort((a, b) => {
    const ai = preferredOrder.indexOf(a[0]);
    const bi = preferredOrder.indexOf(b[0]);
    if (ai === -1 && bi === -1) return 0;
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });
  body.innerHTML = entries.map(([key, value]) => {
    const label = PARTY_DETAIL_LABELS[key] || key;
    const display = formatPartyFieldValue(key, value);
    const spanClass = /address/i.test(key) ? ' bd-party-field--wide' : '';
    return `
      <div class="bd-party-field${spanClass}">
        <span class="bd-party-field-label">${escapePartyDetailHtml(label)}</span>
        <strong class="bd-party-field-value">${escapePartyDetailHtml(display)}</strong>
      </div>
    `;
  }).join('');

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
    extraEl.innerHTML = '<p class="bd-party-muted">Loading Party 360…</p>';
    extraEl.classList.remove('hidden');
  }

  if (editFn) {
    editBtn.style.display = 'inline-block';
    editBtn.textContent = 'Edit';
    editBtn.onclick = () => {
      closeModal('party-detail-modal');
      editFn();
    };
  } else if (record && record.partyId && record.partyType === 'distributor') {
    editBtn.style.display = 'inline-block';
    editBtn.textContent = 'Edit';
    editBtn.onclick = () => {
      closeModal('party-detail-modal');
      openDistributorEditFromRecord(record);
    };
  } else if (record && record.partyId && record.partyType === 'retailer') {
    editBtn.style.display = 'inline-block';
    editBtn.textContent = 'Edit';
    editBtn.onclick = () => {
      closeModal('party-detail-modal');
      if (record.source === 'master') editMasterRetailer(record.partyId);
      else editRetailer(record.partyId);
    };
  } else {
    editBtn.style.display = 'none';
  }

  toggleModal('party-detail-modal', true);

  if (record.partyId && record.partyType) {
    loadParty360Extension(record.partyType, record.partyId, extraEl);
  } else if (extraEl) {
    extraEl.innerHTML = '<p class="bd-party-muted">Party 360 needs a master record ID — upload or open from Customers master list.</p>';
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
      <div class="bd-party-360-head">
        <p class="bd-party-detail-eyebrow">Insights</p>
        <h3>Party 360</h3>
      </div>
      <div class="bd-party-360-grid">
        <article class="bd-party-360-card">
          <span class="bd-party-field-label">Outstanding</span>
          ${outstanding
            ? `<strong class="bd-party-360-metric">₹${Number(outstanding.outstanding || 0).toLocaleString()}</strong>
               ${outstanding.overdue ? `<span class="bd-party-muted">Overdue ₹${Number(outstanding.overdue).toLocaleString()}</span>` : ''}`
            : '<span class="bd-party-muted">Needs invoice data in Finance.</span>'}
        </article>
        <article class="bd-party-360-card">
          <span class="bd-party-field-label">Order tracking (${tracking.length})</span>
          ${tracking.length
            ? `<ul class="bd-party-360-list">${tracking.slice(0, 8).map((t) => `<li>${escapePartyDetailHtml(t.distributor_name || '—')} — Ref ${escapePartyDetailHtml(t.order_ref_no || '—')} · SO ${t.has_sales_order ? '✓' : '✗'} · CI ${t.has_commercial_invoice ? '✓' : '✗'}</li>`).join('')}</ul>`
            : '<span class="bd-party-muted">No lifecycle records yet.</span>'}
        </article>
        <article class="bd-party-360-card">
          <span class="bd-party-field-label">Filled orders (${filled.length})</span>
          ${filled.length
            ? `<ul class="bd-party-360-list">${filled.slice(0, 5).map((f) => `<li>#${f.id} ${escapePartyDetailHtml(f.category || 'Order')} — ${f.matched_lines || 0}/${f.total_lines || 0} lines</li>`).join('')}</ul>`
            : '<span class="bd-party-muted">No filled orders linked under your login.</span>'}
        </article>
        <article class="bd-party-360-card">
          <span class="bd-party-field-label">Visits (${visits.length})</span>
          ${visits.length
            ? `<ul class="bd-party-360-list">${visits.slice(0, 5).map((v) => `<li>${escapePartyDetailHtml(v.visit_date)}: ${escapePartyDetailHtml(v.notes || '—')}</li>`).join('')}</ul>`
            : '<span class="bd-party-muted">No visits logged yet.</span>'}
        </article>
      </div>
    `;
  } catch (error) {
    container.innerHTML = `<p class="bd-party-muted">${escapePartyDetailHtml(error.message || 'Party 360 unavailable.')}</p>`;
  }
}

function getVisiblePartyColumns(records, columnDefs) {
  // Always show every defined column so no party fields are hidden.
  return columnDefs;
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
        <button type="button" class="btn btn-secondary" ${safePage <= 1 ? 'disabled' : ''} onclick="changePartyMasterPage('${paginationId}', -1)">Prev</button>
        <span class="party-master-pagination-page">${safePage} / ${totalPages}</span>
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
          if (c.isAction) return `<td class="pm-actions">${r[c.key] || ''}</td>`;
          const v = formatPartyFieldValue(c.key, r[c.key]);
          const text = v !== null && v !== undefined && v !== '' ? String(v) : '—';
          const safe = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
          return `<td title="${safe}">${safe}</td>`;
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

const PARTY_MASTER_ICON_EDIT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25Zm2.92 2.33H5v-.92l9.1-9.1.92.92-9.1 9.1ZM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.3a1 1 0 0 0-1.41 0l-1.7 1.7L19 8.74l1.71-1.7Z" fill="currentColor"/></svg>';
const PARTY_MASTER_ICON_DELETE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7Zm3-3h6l1 2H8l1-2Z" fill="currentColor"/></svg>';
const ARTICLE_MASTER_ICON_HISTORY = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4a8 8 0 1 0 8 8h-2a6 6 0 1 1-6-6V4Zm1 4v4.6l3.2 1.9-.9 1.5L11 13V8h2Z" fill="currentColor"/></svg>';

function partyMasterActionButtons(editHandler, deleteHandler) {
  return `
    <div class="pm-action-btns">
      <button type="button" class="pm-text-btn pm-text-edit" title="Edit" aria-label="Edit" onclick="event.stopPropagation();${editHandler}">Edit</button>
      <button type="button" class="pm-icon-btn pm-icon-delete" title="Delete forever" aria-label="Delete" onclick="event.stopPropagation();${deleteHandler}">${PARTY_MASTER_ICON_DELETE}</button>
    </div>
  `;
}

/** Unified Customers delete — always purges party + master twins for one contact. */
async function deleteCustomerContact(kind, id, source) {
  const label = kind === 'distributor' ? 'distributor' : 'retailer';
  if (!(await nexoraConfirm(
    `Permanently delete this ${label}? Matching copies in party and master data are removed together.`,
    { title: `Delete ${label}`, danger: true, okText: 'Delete forever' },
  ))) {
    return;
  }
  try {
    const prefer = source === 'master' ? 'master' : 'party';
    const ok = await purgeCustomerAndTwins(kind, id, prefer);
    if (!ok) throw new Error(`Unable to delete ${label}`);
    rerenderCustomersTable(kind);
    if (kind === 'distributor') {
      await refreshCustomersAfterMasterChange('distributors');
      await refreshCustomersAfterMasterChange('retailers');
      loadDistributorSelect();
    } else {
      await refreshCustomersAfterMasterChange('retailers');
    }
    nexoraToast(`${label.charAt(0).toUpperCase()}${label.slice(1)} deleted.`, 'success');
  } catch (error) {
    alert(error.message || `Error deleting ${label}.`);
  }
}

async function deleteDistributor(id) {
  return deleteCustomerContact('distributor', id, 'party');
}
async function deleteMasterDistributor(id) {
  return deleteCustomerContact('distributor', id, 'master');
}
async function deleteRetailer(id) {
  return deleteCustomerContact('retailer', id, 'party');
}
async function deleteMasterRetailer(id) {
  return deleteCustomerContact('retailer', id, 'master');
}

function articleMasterActionButtons(articleId, hasHistory) {
  const historyBtn = hasHistory
    ? `<button type="button" class="pm-icon-btn pm-icon-history" title="Price history" aria-label="Price history" onclick="event.stopPropagation();openArticleMasterPriceHistory(${articleId})">${ARTICLE_MASTER_ICON_HISTORY}</button>`
    : '';
  return `
    <div class="pm-action-btns am-action-btns">
      <button type="button" class="pm-icon-btn pm-icon-edit" title="Edit" aria-label="Edit" onclick="event.stopPropagation();openArticleMasterFullEdit(${articleId})">${PARTY_MASTER_ICON_EDIT}</button>
      ${historyBtn}
      <button type="button" class="pm-icon-btn pm-icon-delete" title="Delete" aria-label="Delete" onclick="event.stopPropagation();deleteOneArticleMaster(${articleId})">${PARTY_MASTER_ICON_DELETE}</button>
    </div>
  `;
}

const DISTRIBUTOR_TABLE_COLUMNS = [
  { key: 'name', label: 'Firm / Name', alwaysShow: true },
  { key: 'distributorCode', label: 'Distributor Code', alwaysShow: true },
  { key: 'contactPerson', label: 'Contact Person' },
  { key: 'contactPersonRole', label: 'Contact Role' },
  { key: 'gst', label: 'GST Number' },
  { key: 'territory', label: 'Territory' },
  { key: 'zone', label: 'Zone' },
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

function partyContactNameCore(name) {
  let text = String(name == null ? '' : name).trim().toLowerCase().replace(/\s+/g, ' ');
  if (!text) return '';
  // "INDU HANDLOOMS, VISAKHAPATNAM" → "indu handlooms"
  text = text.replace(/\s*,\s*[^,]+$/, '').trim();
  text = text.replace(/[^\w\s&./-]/g, ' ').replace(/\s+/g, ' ').trim();
  return text;
}

function partyContactDedupeKey(record) {
  const gst = String(record?.gst || '').replace(/\s+/g, '').toUpperCase();
  if (gst && gst !== '—' && gst.toLowerCase() !== 'n/a') return `gst:${gst}`;
  const phone = String(record?.phone || '').replace(/\D+/g, '');
  if (phone.length >= 10) return `phone:${phone.slice(-10)}`;
  const core = partyContactNameCore(record?.name);
  if (core) return `firm:${core}`;
  const name = String(record?.name || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (name) return `name:${name}`;
  return '';
}

function partyContactMatchKeys(record) {
  const keys = new Set();
  const gst = String(record?.gst || '').replace(/\s+/g, '').toUpperCase();
  if (gst && gst !== '—' && gst.toLowerCase() !== 'n/a') keys.add(`gst:${gst}`);
  const phone = String(record?.phone || '').replace(/\D+/g, '');
  if (phone.length >= 10) keys.add(`phone:${phone.slice(-10)}`);
  const core = partyContactNameCore(record?.name);
  if (core) keys.add(`firm:${core}`);
  const full = String(record?.name || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (full) keys.add(`name:${full}`);
  const primary = partyContactDedupeKey(record);
  if (primary) keys.add(primary);
  return [...keys];
}

function rememberPurgedCustomerKeys(records) {
  if (!partyMasterState.purgedContactKeys) {
    partyMasterState.purgedContactKeys = new Set();
  }
  (records || []).forEach((rec) => {
    partyContactMatchKeys(rec).forEach((k) => partyMasterState.purgedContactKeys.add(k));
  });
  try {
    sessionStorage.setItem(
      'nexora_purged_customer_keys',
      JSON.stringify([...partyMasterState.purgedContactKeys].slice(-500)),
    );
  } catch (_) { /* ignore quota */ }
}

function loadPurgedCustomerKeys() {
  if (partyMasterState.purgedContactKeys instanceof Set) return partyMasterState.purgedContactKeys;
  let keys = [];
  try {
    keys = JSON.parse(sessionStorage.getItem('nexora_purged_customer_keys') || '[]');
  } catch (_) {
    keys = [];
  }
  partyMasterState.purgedContactKeys = new Set(Array.isArray(keys) ? keys : []);
  return partyMasterState.purgedContactKeys;
}

function isPurgedCustomerRecord(record) {
  const purged = loadPurgedCustomerKeys();
  if (!purged.size) return false;
  return partyContactMatchKeys(record).some((k) => purged.has(k));
}

function dedupePartyMasterPreferringMasters(records) {
  const out = [];
  const seen = new Map();
  const push = (rec, preferMaster) => {
    if (isPurgedCustomerRecord(rec)) return;
    const key = partyContactDedupeKey(rec);
    if (!key) {
      out.push(rec);
      return;
    }
    const existingIdx = seen.get(key);
    if (existingIdx == null) {
      seen.set(key, out.length);
      out.push(rec);
      return;
    }
    if (preferMaster) {
      out[existingIdx] = rec;
    }
  };
  (records || []).forEach((rec) => {
    const isMaster = rec?.source === 'master'
      || (typeof rec?.actions === 'string'
        && (rec.actions.includes('editMasterDistributor') || rec.actions.includes('editMasterRetailer')));
    push(rec, isMaster);
  });
  return out;
}

function removeCustomerRecordFromLocalState(kind, partyId) {
  const id = Number(partyId);
  if (!Number.isFinite(id)) return;
  const filterOut = (list) => (list || []).filter((r) => Number(r?.partyId) !== id);
  if (kind === 'distributor') {
    partyMasterState.allDistributorRecords = filterOut(partyMasterState.allDistributorRecords);
    partyMasterTableState.distributorRecords = filterOut(partyMasterTableState.distributorRecords);
    partyMasterState.distributors = (partyMasterState.distributors || []).filter((d) => Number(d.id) !== id);
  } else if (kind === 'retailer') {
    partyMasterState.allRetailerRecords = filterOut(partyMasterState.allRetailerRecords);
    partyMasterTableState.retailerRecords = filterOut(partyMasterTableState.retailerRecords);
    partyMasterState.retailers = (partyMasterState.retailers || []).filter((r) => Number(r.id) !== id);
  }
}

function isMasterCustomerRecord(record) {
  if (record?.source === 'master') return true;
  if (record?.source === 'party') return false;
  const actions = String(record?.actions || '');
  return actions.includes('editMasterDistributor') || actions.includes('editMasterRetailer');
}

function findCustomerRecordById(kind, partyId, preferSource) {
  const id = Number(partyId);
  const raw = kind === 'distributor'
    ? (partyMasterState.rawDistributorRecords || partyMasterState.allDistributorRecords)
    : (partyMasterState.rawRetailerRecords || partyMasterState.allRetailerRecords);
  const matches = (raw || []).filter((r) => Number(r?.partyId) === id);
  if (preferSource) {
    const preferred = matches.find((r) => r.source === preferSource || (preferSource === 'master' ? isMasterCustomerRecord(r) : !isMasterCustomerRecord(r)));
    if (preferred) return preferred;
  }
  return matches[0] || (kind === 'distributor'
    ? (partyMasterState.allDistributorRecords || []).find((r) => Number(r?.partyId) === id)
    : (partyMasterState.allRetailerRecords || []).find((r) => Number(r?.partyId) === id)) || null;
}

function findMatchingCustomerTwins(kind, seedRecord) {
  if (!seedRecord) return [];
  const raw = kind === 'distributor'
    ? (partyMasterState.rawDistributorRecords || partyMasterState.allDistributorRecords)
    : (partyMasterState.rawRetailerRecords || partyMasterState.allRetailerRecords);
  const seedKeys = new Set(partyContactMatchKeys(seedRecord));
  if (!seedKeys.size) return [seedRecord];
  const matches = (raw || []).filter((r) => partyContactMatchKeys(r).some((k) => seedKeys.has(k)));
  // Always include the clicked seed (correct source/id) even if raw list was stale
  const seedId = Number(seedRecord.partyId);
  const seedSource = seedRecord.source || (isMasterCustomerRecord(seedRecord) ? 'master' : 'party');
  if (!matches.some((r) => Number(r.partyId) === seedId && (r.source || '') === seedSource)) {
    matches.unshift(seedRecord);
  }
  return matches.length ? matches : [seedRecord];
}

function removeCustomerRecordsByMatchKeys(kind, keys) {
  const keySet = new Set(keys || []);
  if (!keySet.size) return;
  const filterOut = (list) => (list || []).filter((r) => !partyContactMatchKeys(r).some((k) => keySet.has(k)));
  if (kind === 'distributor') {
    partyMasterState.rawDistributorRecords = filterOut(partyMasterState.rawDistributorRecords);
    partyMasterState.allDistributorRecords = filterOut(partyMasterState.allDistributorRecords);
    partyMasterTableState.distributorRecords = filterOut(partyMasterTableState.distributorRecords);
  } else if (kind === 'retailer') {
    partyMasterState.rawRetailerRecords = filterOut(partyMasterState.rawRetailerRecords);
    partyMasterState.allRetailerRecords = filterOut(partyMasterState.allRetailerRecords);
    partyMasterTableState.retailerRecords = filterOut(partyMasterTableState.retailerRecords);
  }
}

function customerDeleteApiUrl(kind, record) {
  const id = Number(record?.partyId);
  const master = isMasterCustomerRecord(record);
  if (kind === 'distributor') {
    return master ? `/api/v1/masters/distributors/${id}` : `/api/v1/parties/distributors/${id}`;
  }
  return master ? `/api/v1/masters/retailers/${id}` : `/api/v1/parties/retailers/${id}`;
}

async function purgeCustomerAndTwins(kind, partyId, preferSource) {
  const seed = findCustomerRecordById(kind, partyId, preferSource) || {
    partyId,
    source: preferSource || 'party',
    name: '',
    actions: preferSource === 'master'
      ? (kind === 'distributor' ? 'editMasterDistributor' : 'editMasterRetailer')
      : (kind === 'distributor' ? 'editDistributor' : 'editRetailer'),
  };
  if (preferSource && !seed.source) seed.source = preferSource;

  const twins = findMatchingCustomerTwins(kind, seed);
  const matchKeys = new Set();
  twins.forEach((rec) => partyContactMatchKeys(rec).forEach((k) => matchKeys.add(k)));

  let primaryOk = false;
  const deletedRecs = [];
  await Promise.all(twins.map(async (rec) => {
    try {
      const response = await fetchWithAuth(customerDeleteApiUrl(kind, rec), { method: 'DELETE' });
      const data = await response.json().catch(() => ({}));
      const ok = response.status === 404 || Boolean(response.ok && data.success);
      const sameId = Number(rec.partyId) === Number(partyId);
      const sameSource = !preferSource
        || rec.source === preferSource
        || (preferSource === 'master' ? isMasterCustomerRecord(rec) : !isMasterCustomerRecord(rec));
      if (ok && sameId && sameSource) primaryOk = true;
      if (ok) deletedRecs.push(rec);
      return ok;
    } catch (_) {
      return false;
    }
  }));

  if (!primaryOk) return false;

  rememberPurgedCustomerKeys(deletedRecs.length ? deletedRecs : twins);
  removeCustomerRecordsByMatchKeys(kind, [...matchKeys]);
  twins.forEach((rec) => removeCustomerRecordFromLocalState(kind, rec.partyId));
  return true;
}

function rerenderCustomersTable(kind) {
  if (kind === 'distributor') {
    const filtered = filterDistributorRecords(partyMasterState.allDistributorRecords || []);
    partyMasterTableState.distributorRecords = filtered;
    populateDistributorCityFilterOptions(partyMasterState.allDistributorRecords || []);
    renderPartyMasterTable(
      'distributor',
      'distributor-thead',
      'distributor-tbody',
      'distributor-pagination',
      filtered,
      DISTRIBUTOR_TABLE_COLUMNS,
      partyMasterTableState.distributorPage || 1,
    );
  } else {
    const all = partyMasterState.allRetailerRecords || [];
    populateRetailerDistributorFilterOptions(all);
    populateRetailerCityFilterOptions(all);
    const filtered = filterRetailerRecords(all);
    partyMasterTableState.retailerRecords = filtered;
    renderPartyMasterTable(
      'retailer',
      'retailer-thead',
      'retailer-tbody',
      'retailer-pagination',
      filtered,
      RETAILER_TABLE_COLUMNS,
      partyMasterTableState.retailerPage || 1,
    );
  }
  scheduleCustomersLayout();
}

async function loadDistributors() {
  const loadSeq = ++partyMasterTableState.distributorsLoadSeq;
  partyMasterTableState.distributorsLoading = true;
  showPartyMasterTableLoading('distributor-tbody');

  partyMasterTableState.distributorsLoadPromise = (async () => {
    try {
      const response = await fetchWithAuth('/api/v1/parties/distributors?limit=5000');
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.message || 'Unable to load distributors');
      }
      if (loadSeq !== partyMasterTableState.distributorsLoadSeq) return;
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
      if (loadSeq !== partyMasterTableState.distributorsLoadSeq) return;

      const partyRecords = partyMasterState.distributors.map((d) => ({
        partyId: d.id,
        partyType: 'distributor',
        source: 'party',
        name: d.name,
        contactPerson: d.contact_person,
        distributorCode: '',
        gst: d.gst_number,
        territory: d.territory || '',
        zone: '',
        city: resolvePartyCity(d.city, d.address, d.name),
        state: d.state,
        pincode: formatPartyFieldValue('pincode', d.pin_code),
        address: d.address,
        phone: d.phone,
        creditLimit: d.credit_limit,
        actions: partyMasterActionButtons(`openDistributorEditFromRecord({partyId:${d.id},source:'party',partyType:'distributor'})`, `deleteDistributor(${d.id})`),
      }));
      const masterRecords = masterDistributors.map((d) => ({
        partyId: d.id,
        partyType: 'distributor',
        source: 'master',
        name: d.firm_name || d.name,
        firmNickName: d.firm_nick_name || '',
        distributorCode: formatPartyFieldValue('distributorCode', d.distributor_code || d.distributor_id || ''),
        contactPerson: d.name,
        contactPersonRole: d.contact_person_role || '',
        gst: d.gst_no,
        territory: d.territory || '',
        zone: d.zone || '',
        city: resolvePartyCity(d.location, d.address, d.firm_name || d.name),
        state: d.region,
        pincode: formatPartyFieldValue('pincode', d.pincode),
        address: d.address,
        phone: d.phone_number,
        phone2: d.phone_number_2 || '',
        email: d.email || '',
        paymentTerms: d.payment_terms || '',
        birthday: d.birthday || '',
        anniversary: d.anniversary || '',
        secondaryName: d.secondary_distributor_name || '',
        secondaryPhone: d.secondary_distributor_phone_number || '',
        salesName: d.sales_executive_name || '',
        salesPhone: d.sales_executive_phone_number || '',
        salesEmail: d.sales_executive_email || '',
        creditLimit: d.credit_limit,
        actions: partyMasterActionButtons(`openDistributorEditFromRecord({partyId:${d.id},source:'master',partyType:'distributor'})`, `deleteMasterDistributor(${d.id})`),
      }));
      partyMasterState.rawDistributorRecords = [...partyRecords, ...masterRecords];
      const records = dedupePartyMasterPreferringMasters(partyMasterState.rawDistributorRecords);

      partyMasterState.allDistributorRecords = records;
      populateDistributorCityFilterOptions(records);
      const filteredRecords = filterDistributorRecords(records);

      partyMasterTableState.distributorRecords = filteredRecords;
      partyMasterTableState.distributorPage = 1;
      partyMasterTableState.distributorsLoaded = true;
      renderPartyMasterTable(
        'distributor',
        'distributor-thead',
        'distributor-tbody',
        'distributor-pagination',
        filteredRecords,
        DISTRIBUTOR_TABLE_COLUMNS,
        1,
      );
      scheduleCustomersLayout();
    } catch (error) {
      if (loadSeq !== partyMasterTableState.distributorsLoadSeq) return;
      console.warn('Failed to load distributors:', error);
      const tbody = document.getElementById('distributor-tbody');
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="20">Unable to load distributors.</td></tr>';
      }
    } finally {
      if (loadSeq === partyMasterTableState.distributorsLoadSeq) {
        partyMasterTableState.distributorsLoading = false;
      }
    }
  })();

  return partyMasterTableState.distributorsLoadPromise;
}

function isBlankPartyValue(value) {
  const text = String(value == null ? '' : value).trim();
  if (!text) return true;
  const lower = text.toLowerCase();
  return text === '—' || text === '-' || text === '–' || lower === 'n/a' || lower === 'na' || lower === 'null';
}

const PARTY_CITY_STATE_PATTERN = /\b(andhra\s*pradesh|arunachal\s*pradesh|himachal\s*pradesh|madhya\s*pradesh|uttar\s*pradesh|west\s*bengal|tamil\s*nadu|telangana|karnataka|maharashtra|gujarat|rajasthan|kerala|punjab|haryana|odisha|orissa|bihar|jharkhand|chhattisgarh|chattisgarh|assam|goa|nct\s+of\s+delhi|uttarakhand|uttaranchal|meghalaya|manipur|mizoram|nagaland|sikkim|tripura|pondicherry|puducherry|chandigarh|ladakh|lakshadweep|andaman|nicobar)\b/gi;

const PARTY_CITY_STREET_WORDS = /\b(road|rd|street|st|lane|nagar|colony|area|market|mkt|bazaar|station|stastion|near|opp|opposite|main|cross|circle|chowk|plot|door|floor|block|sector|phase|apartment|complex|building|shop|chowk|vihar|enclave|extension|extn|marg|gali|bazar)\b/i;

const PARTY_CITY_ADDRESS_MARKERS = /\b(shop\s*no|mkt\.?|market|road|rd\.?|street|lane|colony|nagar|apartment|complex|building|floor|plot|near|opp\.?|vile\s*parle|sector|phase|mall|shyamkamal|flat|house\s*no|h\.?\s*no)\b/i;

/** Multi-word cities first so "Navi Mumbai" / "New Delhi" win over shorter names. */
const PARTY_KNOWN_CITIES = [
  ['navi mumbai', 'Navi Mumbai'],
  ['new delhi', 'New Delhi'],
  ['greater noida', 'Greater Noida'],
  ['gautam buddha nagar', 'Noida'],
  ['gautambuddha nagar', 'Noida'],
  ['gautam budh nagar', 'Noida'],
  ['thane west', 'Thane'],
  ['thane east', 'Thane'],
  ['mumbai', 'Mumbai'],
  ['bombay', 'Mumbai'],
  ['thane', 'Thane'],
  ['pune', 'Pune'],
  ['nagpur', 'Nagpur'],
  ['nashik', 'Nashik'],
  ['nasik', 'Nashik'],
  ['aurangabad', 'Aurangabad'],
  ['bengaluru', 'Bengaluru'],
  ['bangalore', 'Bengaluru'],
  ['mysore', 'Mysuru'],
  ['mysuru', 'Mysuru'],
  ['chennai', 'Chennai'],
  ['madras', 'Chennai'],
  ['coimbatore', 'Coimbatore'],
  ['madurai', 'Madurai'],
  ['hyderabad', 'Hyderabad'],
  ['secunderabad', 'Secunderabad'],
  ['vijayawada', 'Vijayawada'],
  ['visakhapatnam', 'Visakhapatnam'],
  ['vizag', 'Visakhapatnam'],
  ['kolkata', 'Kolkata'],
  ['calcutta', 'Kolkata'],
  ['delhi', 'Delhi'],
  ['noida', 'Noida'],
  ['gurugram', 'Gurugram'],
  ['gurgaon', 'Gurugram'],
  ['faridabad', 'Faridabad'],
  ['ghaziabad', 'Ghaziabad'],
  ['ahmedabad', 'Ahmedabad'],
  ['surat', 'Surat'],
  ['vadodara', 'Vadodara'],
  ['baroda', 'Vadodara'],
  ['rajkot', 'Rajkot'],
  ['jaipur', 'Jaipur'],
  ['jodhpur', 'Jodhpur'],
  ['udaipur', 'Udaipur'],
  ['indore', 'Indore'],
  ['bhopal', 'Bhopal'],
  ['lucknow', 'Lucknow'],
  ['kanpur', 'Kanpur'],
  ['varanasi', 'Varanasi'],
  ['patna', 'Patna'],
  ['ranchi', 'Ranchi'],
  ['bhubaneswar', 'Bhubaneswar'],
  ['cuttack', 'Cuttack'],
  ['kochi', 'Kochi'],
  ['cochin', 'Kochi'],
  ['thiruvananthapuram', 'Thiruvananthapuram'],
  ['trivandrum', 'Thiruvananthapuram'],
  ['chandigarh', 'Chandigarh'],
  ['ludhiana', 'Ludhiana'],
  ['amritsar', 'Amritsar'],
  ['jammu', 'Jammu'],
  ['srinagar', 'Srinagar'],
  ['guwahati', 'Guwahati'],
  ['raipur', 'Raipur'],
  ['jabalpur', 'Jabalpur'],
  ['solapur', 'Solapur'],
  ['kolhapur', 'Kolhapur'],
  ['panvel', 'Panvel'],
  ['kalyan', 'Kalyan'],
  ['vasai', 'Vasai'],
  ['virar', 'Virar'],
  ['dombivli', 'Dombivli'],
  ['dombivali', 'Dombivli'],
  ['meerut', 'Meerut'],
  ['agra', 'Agra'],
  ['bhatinda', 'Bathinda'],
  ['bathinda', 'Bathinda'],
  ['dehradun', 'Dehradun'],
  ['haridwar', 'Haridwar'],
  ['rohtak', 'Rohtak'],
  ['panipat', 'Panipat'],
  ['karnal', 'Karnal'],
  ['ambala', 'Ambala'],
  ['hisar', 'Hisar'],
  ['sonipat', 'Sonipat'],
  ['sonepat', 'Sonipat'],
];

function matchKnownPartyCity(text) {
  const raw = String(text || '').toLowerCase();
  const lower = raw
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!lower && !raw) return '';
  for (const [needle, canonical] of PARTY_KNOWN_CITIES) {
    const spaced = needle.replace(/\s+/g, '\\s+');
    const pattern = new RegExp(`(?:^|[^a-z])${spaced}(?:[^a-z]|$)`, 'i');
    if (lower && pattern.test(lower)) return canonical;
    // Glued street+city: "Commercial Streetbangalore", "MGroadmumbai"
    const compactNeedle = needle.replace(/\s+/g, '');
    if (compactNeedle.length >= 4) {
      const glued = new RegExp(`${compactNeedle}(?:[^a-z]|$)`, 'i');
      if (glued.test(raw.replace(/\s+/g, ''))) return canonical;
    }
  }
  return '';
}

function titleCaseCityName(name) {
  return String(name || '')
    .trim()
    .toLowerCase()
    .replace(/\b([a-z])/g, (ch) => ch.toUpperCase());
}

function isGarbagePartyCityCandidate(raw) {
  const text = String(raw || '').replace(/\s+/g, ' ').trim();
  if (!text) return true;
  const lower = text.toLowerCase();
  if (matchKnownPartyCity(text)) return false;
  if (text.length < 3 || text.length > 32) return true;
  if (/^\d+$/.test(text)) return true;
  if (/^[a-z]\s*-\s*\d/i.test(text)) return true; // F-3, A-12
  if (/\b(and|or|the|near|opp|plot|shop|floor|flat|door|no|nos?|vs|to)\b/i.test(lower) && text.split(/\s+/).length <= 3) return true;
  if (/^(and|or|the|near|opp|india|state|city|town|village|dist|district|east|west|north|south)$/i.test(lower)) return true;
  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  if (words.every((w) => w.length <= 2)) return true; // "And F", "F F"
  if (PARTY_CITY_STREET_WORDS.test(text) || PARTY_CITY_ADDRESS_MARKERS.test(text)) return true;
  if (/(road|street|lane|nagar|colony|market|sector|phase|complex|building)[a-z]{3,}/i.test(text)) return true;
  return false;
}

/** Clean messy location strings into one city name, e.g. "… Delhi-110092" → "Delhi" (never "And F"). */
function normalizePartyCityName(raw) {
  let text = String(raw == null ? '' : raw).trim();
  if (isBlankPartyValue(text)) return '';

  // ALWAYS prefer a known city from the original string first (incl. glued Streetbangalore).
  const knownRaw = matchKnownPartyCity(text);
  if (knownRaw) return knownRaw;

  text = text
    .replace(/[|/_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  // Strip glued street/address prefix before a known city: "CommercialStreetbangalore"
  const compact = text.replace(/\s+/g, '');
  for (const [needle] of PARTY_KNOWN_CITIES) {
    const compactNeedle = needle.replace(/\s+/g, '');
    if (compactNeedle.length < 4) continue;
    const idx = compact.toLowerCase().lastIndexOf(compactNeedle);
    if (idx > 0) {
      const knownGlued = matchKnownPartyCity(compact.slice(idx));
      if (knownGlued) return knownGlued;
    }
  }

  // Strip glued state suffix: "Bangalorekarnataka" (not city names like Delhi)
  text = text.replace(/(andhrapradesh|telangana|tamilnadu|karnataka|maharashtra|gujarat|rajasthan|uttarpradesh|westbengal|madhyapradesh|himachalpradesh|kerala|punjab|haryana|odisha|orissa|bihar|jharkhand|chhattisgarh|uttarakhand)$/i, '');

  // City-PIN glued: "Delhi-110092" / "Delhi 110092"
  const cityPin = text.match(/^([A-Za-z][A-Za-z. ]{1,30}?)[\s\-–—]*\d{6}\b/);
  if (cityPin) {
    const knownPin = matchKnownPartyCity(cityPin[1]);
    if (knownPin) return knownPin;
    const pinCity = cityPin[1].replace(/\s+/g, ' ').trim();
    if (!isGarbagePartyCityCandidate(pinCity)) return titleCaseCityName(pinCity);
  }

  // Remove pincodes / digit runs
  text = text.replace(/\d+/g, ' ');
  // Remove state names — never strip Delhi/Mumbai/etc. (those are cities)
  text = text.replace(PARTY_CITY_STATE_PATTERN, ' ');
  text = text.replace(/[()[\]{}]+/g, ' ').replace(/\s+/g, ' ').trim();

  const known = matchKnownPartyCity(text);
  if (known) return known;

  const segments = text
    .split(/[,–—\-]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  let candidate = '';
  for (let i = segments.length - 1; i >= 0; i -= 1) {
    const seg = segments[i].replace(/\s+/g, ' ').trim();
    if (!seg || isGarbagePartyCityCandidate(seg)) continue;
    const knownSeg = matchKnownPartyCity(seg);
    if (knownSeg) return knownSeg;
    const words = seg.split(/\s+/).filter(Boolean);
    if (!words.length || words.length > 3) continue;
    candidate = seg;
    break;
  }
  if (!candidate) return '';

  const knownCandidate = matchKnownPartyCity(candidate);
  if (knownCandidate) return knownCandidate;

  candidate = candidate
    .replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (isGarbagePartyCityCandidate(candidate)) return '';
  if (/^(Andheri|Bandra|Juhu|Powai|Borivali|Malad|Goregaon|Dadar|Worli|Kurla|Ghatkopar|Vile\s*Parle|Laxmi\s*Nagar|Vijay\s*Chowk)$/i.test(candidate)) return '';

  return titleCaseCityName(candidate);
}

function extractCityFromAddress(address) {
  const text = String(address || '').trim();
  if (!text) return '';

  // Full-address known-city win: "… Laxmi Nagar, Delhi-110092" → Delhi
  const knownFull = matchKnownPartyCity(text);
  if (knownFull) return knownFull;

  const parts = text.split(',').map((part) => part.trim()).filter(Boolean);
  if (parts.length) {
    for (let i = parts.length - 1; i >= 0; i -= 1) {
      const knownPart = matchKnownPartyCity(parts[i]);
      if (knownPart) return knownPart;
      const cleaned = normalizePartyCityName(parts[i]);
      if (cleaned && !isGarbagePartyCityCandidate(cleaned)) return cleaned;
    }
  }

  const endPinMatch = text.match(/([A-Za-z][A-Za-z.\s]{1,40}?)[\s\-–—]*\d{6}\b/);
  if (endPinMatch) {
    const knownPin = matchKnownPartyCity(endPinMatch[1]);
    if (knownPin) return knownPin;
    const cleaned = normalizePartyCityName(endPinMatch[1]);
    if (cleaned && !isGarbagePartyCityCandidate(cleaned)) return cleaned;
  }

  const fallback = normalizePartyCityName(text);
  return isGarbagePartyCityCandidate(fallback) ? '' : fallback;
}

function extractCityFromPartyName(name) {
  const text = String(name || '').trim();
  if (!text) return '';
  const known = matchKnownPartyCity(text);
  if (known) return known;
  const parts = text.split(/[,|]/).map((part) => part.trim()).filter(Boolean);
  if (parts.length < 2) return '';
  return normalizePartyCityName(parts[parts.length - 1]);
}

function resolvePartyCity(city, address, name) {
  // Address known city always wins over junk stored in location ("And F", "Andheri West").
  const knownFromAddr = matchKnownPartyCity(address || '');
  if (knownFromAddr) return knownFromAddr;

  const rawCity = String(city == null ? '' : city).trim();
  const cityLooksLikeAddress = PARTY_CITY_ADDRESS_MARKERS.test(rawCity)
    || rawCity.length > 28
    || rawCity.split(/[,\-]/).length > 2
    || isGarbagePartyCityCandidate(rawCity);
  const fromAddr = extractCityFromAddress(address);
  const fromCity = cityLooksLikeAddress ? '' : normalizePartyCityName(city);
  const fromName = extractCityFromPartyName(name);

  if (fromAddr && matchKnownPartyCity(fromAddr)) return fromAddr;
  if (fromCity && matchKnownPartyCity(fromCity)) return fromCity;
  if (fromName && matchKnownPartyCity(fromName)) return fromName;
  if (fromAddr && !isGarbagePartyCityCandidate(fromAddr)) return fromAddr;
  if (fromCity && !isGarbagePartyCityCandidate(fromCity)) return fromCity;
  if (fromName && !isGarbagePartyCityCandidate(fromName)) return fromName;
  return '';
}

function uniqueSortedCities(records) {
  const byKey = new Map();
  (records || []).forEach((r) => {
    let city = normalizePartyCityName(r.city);
    if (!city || isGarbagePartyCityCandidate(city)) {
      city = String(r.city || '').trim();
      city = normalizePartyCityName(city);
    }
    if (!city || isGarbagePartyCityCandidate(city) || isBlankPartyValue(city)) return;
    const key = city.toLowerCase();
    if (!byKey.has(key)) byKey.set(key, city);
  });
  return [...byKey.values()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
}

function populateDistributorCityFilterOptions(records) {
  const select = document.getElementById('distributor-city-filter');
  if (!select) return;
  const currentValue = select.value;
  const uniqueCities = uniqueSortedCities(records);
  select.innerHTML = ['<option value="">All</option>']
    .concat(uniqueCities.map((city) => `<option value="${city}">${city}</option>`))
    .join('');
  const stillValid = uniqueCities.some((city) => city.toLowerCase() === String(currentValue || '').toLowerCase());
  select.value = stillValid ? uniqueCities.find((city) => city.toLowerCase() === currentValue.toLowerCase()) : '';
}

function getDistributorFilterValues() {
  const city = document.getElementById('distributor-city-filter')?.value || '';
  const search = String(document.getElementById('distributor-search-input')?.value || '').trim().toLowerCase();
  return { city, search };
}

function filterDistributorRecords(records) {
  const { city, search } = getDistributorFilterValues();
  const cityKey = city.toLowerCase();
  return (records || []).filter((r) => {
    if (cityKey) {
      const recordCity = String(r.city || '').trim().toLowerCase();
      if (recordCity !== cityKey) return false;
    }
    if (search) {
      const haystack = [r.name, r.contactPerson, r.phone, r.gst, r.distributorCode, r.territory, r.zone, r.city, r.address]
        .map((v) => String(v || '').toLowerCase())
        .join(' ');
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}

function applyDistributorFilters() {
  const records = partyMasterState.allDistributorRecords || [];
  const filteredRecords = filterDistributorRecords(records);
  partyMasterTableState.distributorRecords = filteredRecords;
  partyMasterTableState.distributorPage = 1;
  renderPartyMasterTable(
    'distributor',
    'distributor-thead',
    'distributor-tbody',
    'distributor-pagination',
    filteredRecords,
    DISTRIBUTOR_TABLE_COLUMNS,
    1,
  );
  scheduleCustomersLayout();
}

function populateRetailerDistributorFilterOptions(records) {
  const select = document.getElementById('retailer-distributor-filter');
  if (!select) return;
  const currentValue = select.value;
  const uniqueDistributors = [...new Set(records.map((r) => r.distributorKey).filter(Boolean))].sort((a, b) =>
    String(a).localeCompare(String(b), undefined, { sensitivity: 'base' }),
  );
  select.innerHTML = ['<option value="">All</option>']
    .concat(uniqueDistributors.map((name) => `<option value="${name}">${name}</option>`))
    .join('');
  select.value = uniqueDistributors.includes(currentValue) ? currentValue : '';
}

function populateRetailerCityFilterOptions(records) {
  const select = document.getElementById('retailer-city-filter');
  if (!select) return;
  const currentValue = select.value;
  const uniqueCities = uniqueSortedCities(records);
  select.innerHTML = ['<option value="">All</option>']
    .concat(uniqueCities.map((city) => `<option value="${city}">${city}</option>`))
    .join('');
  const stillValid = uniqueCities.some((city) => city.toLowerCase() === String(currentValue || '').toLowerCase());
  select.value = stillValid ? uniqueCities.find((city) => city.toLowerCase() === currentValue.toLowerCase()) : '';
}

function getRetailerFilterValues() {
  const distributor = document.getElementById('retailer-distributor-filter')?.value || '';
  const city = document.getElementById('retailer-city-filter')?.value || '';
  const search = String(document.getElementById('retailer-search-input')?.value || '').trim().toLowerCase();
  return { distributor, city, search };
}

function filterRetailerRecords(records) {
  const { distributor, city, search } = getRetailerFilterValues();
  const cityKey = city.toLowerCase();
  return (records || []).filter((r) => {
    if (distributor && r.distributorKey !== distributor) return false;
    if (cityKey && String(r.city || '').trim().toLowerCase() !== cityKey) return false;
    if (search) {
      const haystack = [r.name, r.contactPerson, r.phone, r.gst, r.city, r.address]
        .map((v) => String(v || '').toLowerCase())
        .join(' ');
      if (!haystack.includes(search)) return false;
    }
    return true;
  });
}

function applyRetailerFilters() {
  const records = partyMasterState.allRetailerRecords || [];
  const filteredRecords = filterRetailerRecords(records);
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

function filterRetailersByDistributor() {
  applyRetailerFilters();
}

async function loadRetailers() {
  const loadSeq = ++partyMasterTableState.retailersLoadSeq;
  partyMasterTableState.retailersLoading = true;
  showPartyMasterTableLoading('retailer-tbody');

  partyMasterTableState.retailersLoadPromise = (async () => {
    try {
      if (!partyMasterTableState.distributorsLoaded && !partyMasterTableState.distributorsLoading) {
        await loadDistributors();
      } else if (partyMasterTableState.distributorsLoading) {
        await partyMasterTableState.distributorsLoadPromise;
      }
      if (loadSeq !== partyMasterTableState.retailersLoadSeq) return;

      const response = await fetchWithAuth('/api/v1/parties/retailers?limit=5000');
      const data = await response.json();
      if (!response.ok || !data.success) {
        throw new Error(data.message || 'Unable to load retailers');
      }
      if (loadSeq !== partyMasterTableState.retailersLoadSeq) return;
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
      if (loadSeq !== partyMasterTableState.retailersLoadSeq) return;

      const partyRecords = partyMasterState.retailers.map((r) => {
        const distributorLabel = distributorById.get(r.distributor_id)
          || (r.distributor_id == null ? 'Unassigned' : r.distributor_id);
        return {
          partyId: r.id,
          partyType: 'retailer',
          source: 'party',
          name: r.name,
          contactPerson: r.contact_person,
          distributor: distributorLabel,
          distributorKey: distributorLabel,
          gst: r.gst_number,
          territory: r.territory,
          city: resolvePartyCity(r.city, r.address, r.name),
          state: r.state,
          pincode: formatPartyFieldValue('pincode', r.pin_code),
          address: r.address,
          storeType: r.store_type,
          phone: r.phone,
          actions: partyMasterActionButtons(`editRetailer(${r.id})`, `deleteRetailer(${r.id})`),
        };
      });
      const masterRecords = masterRetailers.map((r) => ({
        partyId: r.id,
        partyType: 'retailer',
        source: 'master',
        name: r.name,
        contactPerson: r.contact_person,
        distributor: r.distributor_name || 'Unassigned',
        distributorKey: r.distributor_name || 'Unassigned',
        gst: r.gst_no,
        territory: r.location,
        city: resolvePartyCity(r.location, r.address, r.name),
        state: r.state,
        pincode: formatPartyFieldValue('pincode', r.pincode),
        address: r.address,
        storeType: r.category,
        phone: r.phone_number,
        actions: partyMasterActionButtons(`editMasterRetailer(${r.id})`, `deleteMasterRetailer(${r.id})`),
      }));
      partyMasterState.rawRetailerRecords = [...partyRecords, ...masterRecords];
      const records = dedupePartyMasterPreferringMasters(partyMasterState.rawRetailerRecords);

      partyMasterState.allRetailerRecords = records;
      populateRetailerDistributorFilterOptions(records);
      populateRetailerCityFilterOptions(records);

      const filteredRecords = filterRetailerRecords(records);

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
      if (loadSeq !== partyMasterTableState.retailersLoadSeq) return;
      console.warn('Failed to load retailers:', error);
      const tbody = document.getElementById('retailer-tbody');
      if (tbody) {
        tbody.innerHTML = '<tr><td colspan="20">Unable to load retailers.</td></tr>';
      }
    } finally {
      if (loadSeq === partyMasterTableState.retailersLoadSeq) {
        partyMasterTableState.retailersLoading = false;
      }
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
  // Customers uses master as the canonical write path (one contact model).
  openMasterDistributorForm();
}

function openRetailerForm() {
  openMasterRetailerForm();
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
    await refreshCustomersAfterMasterChange('distributors');
    loadDistributorSelect();
    nexoraToast('Distributor saved.', 'success');
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
    await refreshCustomersAfterMasterChange('retailers');
    nexoraToast('Retailer saved.', 'success');
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

function bdSyncSidebarScroll(opts = {}) {
  const preserveScroll = opts.preserveScroll !== false;
  const lists = document.querySelectorAll(
    '#bd-left-nav .hop-nav-list, #dashboard .bd-shell .hop-nav-list, #hop-executive-workspace .hop-nav-list'
  );
  const seen = new Set();
  lists.forEach((list) => {
    if (seen.has(list)) return;
    seen.add(list);

    const prevScroll = list.scrollTop;

    list.classList.remove('is-scrollable');
    list.style.setProperty('overflow-y', 'hidden', 'important');
    list.style.setProperty('scrollbar-width', 'none', 'important');

    // Measure real visible content — scrollHeight on flex:1 rails is unreliable
    const listCs = window.getComputedStyle(list);
    const gap = parseFloat(listCs.rowGap || listCs.gap) || 0;
    let contentH = (parseFloat(listCs.paddingTop) || 0) + (parseFloat(listCs.paddingBottom) || 0);
    let visible = 0;
    Array.from(list.children).forEach((child) => {
      const cs = window.getComputedStyle(child);
      if (cs.display === 'none' || cs.visibility === 'hidden') return;
      contentH += child.getBoundingClientRect().height;
      visible += 1;
      // Expanded fold items are nested — include their visible height
      if (child.classList?.contains('hop-nav-fold') && !child.classList.contains('is-collapsed')) {
        const items = child.querySelector('.hop-nav-fold-items');
        if (items) {
          const ics = window.getComputedStyle(items);
          if (ics.display !== 'none') {
            // fold height already includes items via getBoundingClientRect on the fold
          }
        }
      }
    });
    if (visible > 1) contentH += gap * (visible - 1);

    const avail = list.clientHeight || list.getBoundingClientRect().height;
    const needsScroll = contentH > avail + 2;

    if (needsScroll) {
      list.classList.add('is-scrollable');
      list.style.setProperty('overflow-y', 'auto', 'important');
      list.style.setProperty('scrollbar-width', 'thin', 'important');
      if (preserveScroll) {
        const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight);
        list.scrollTop = Math.min(prevScroll, maxScroll);
      }
    } else {
      list.scrollTop = 0;
      list.style.setProperty('overflow-y', 'hidden', 'important');
      list.style.setProperty('scrollbar-width', 'none', 'important');
    }
  });

  // Aside itself must never scroll (BD + HoP)
  document.querySelectorAll('#bd-left-nav, #hop-executive-workspace .hop-nav').forEach((aside) => {
    aside.style.setProperty('overflow', 'hidden', 'important');
    aside.style.setProperty('scrollbar-width', 'none', 'important');
  });
}

/** After Settings (or any fold) expands, bring its last sub-item into view. */
function bdScrollNavFoldIntoView(fold) {
  if (!fold || !fold.getBoundingClientRect) return;
  const list = fold.closest('.hop-nav-list, .nav-list');
  if (!list) return;

  bdSyncSidebarScroll({ preserveScroll: true });

  const run = () => {
    if (!list.classList.contains('is-scrollable')) return;
    const pad = 10;
    const items = fold.querySelector('.hop-nav-fold-items');
    const lastSub = items
      ? Array.from(items.querySelectorAll('.nav-item, .hop-nav-btn')).filter((el) => {
          const cs = window.getComputedStyle(el);
          return cs.display !== 'none' && cs.visibility !== 'hidden';
        }).pop()
      : null;
    const anchor = lastSub || fold;
    const listRect = list.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    if (anchorRect.bottom > listRect.bottom - pad) {
      list.scrollBy({ top: anchorRect.bottom - listRect.bottom + pad, behavior: 'smooth' });
    } else if (fold.getBoundingClientRect().top < listRect.top + pad) {
      list.scrollBy({ top: fold.getBoundingClientRect().top - listRect.top - pad, behavior: 'smooth' });
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(run));
}

function bdToggleSettingsFold() {
  const fold = document.querySelector('#bd-left-nav .hop-nav-fold[data-hop-fold="bd-settings"]');
  if (!fold) return;
  const willOpen = fold.classList.contains('is-collapsed');
  fold.classList.toggle('is-collapsed', !willOpen);
  fold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
  requestAnimationFrame(() => {
    bdSyncSidebarScroll({ preserveScroll: true });
    if (willOpen) bdScrollNavFoldIntoView(fold);
  });
}

function bdExpandSettingsFold() {
  const fold = document.querySelector('#bd-left-nav .hop-nav-fold[data-hop-fold="bd-settings"]');
  if (!fold) return;
  fold.classList.remove('is-collapsed');
  fold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => {
    bdSyncSidebarScroll({ preserveScroll: true });
    bdScrollNavFoldIntoView(fold);
  });
}

function openWorkspaceSettings(tab) {
  const el = document.getElementById('settings-workspace');
  if (!el) {
    window.location.href = '/admin/database';
    return;
  }
  pinBdShellForModule(el);
  document.getElementById('sales-workspace')?.classList.add('hidden');
  bdExpandSettingsFold();
  const mode = tab === 'company' ? 'company' : 'theme';
  if (mode === 'theme' && typeof hopCaptureThemeBaseline === 'function') {
    hopCaptureThemeBaseline();
  }
  showBdSettingsTab(mode);
}

function showBdSettingsTab(tab) {
  const mode = tab === 'company' ? 'company' : 'theme';
  document.getElementById('bd-settings-theme-panel')?.classList.toggle('hidden', mode !== 'theme');
  document.getElementById('bd-settings-company-panel')?.classList.toggle('hidden', mode !== 'company');

  const eyebrow = document.getElementById('bd-settings-eyebrow');
  const title = document.getElementById('bd-settings-title');
  if (eyebrow) eyebrow.textContent = 'Settings';
  if (title) title.textContent = mode === 'company' ? 'Company' : 'Theme';

  setActiveSidebarItem(mode === 'company' ? 'Company' : 'Theme');
  document.getElementById('nav-settings')?.classList.remove('active');
  document.getElementById('nav-settings-company')?.classList.toggle('active', mode === 'company');
  document.getElementById('nav-settings-theme')?.classList.toggle('active', mode === 'theme');

  if (mode === 'theme') {
    const mount = document.getElementById('bd-settings-theme-mount');
    if (!mount) return;
    if (typeof renderHopThemeModule === 'function') {
      renderHopThemeModule(mount);
    } else {
      mount.innerHTML = '<p class="nx-text-dim">Theme module is unavailable.</p>';
    }
    return;
  }

  if (mode === 'company') {
    loadCompanyProfileV2();
  }
}

function refreshBdSettingsThemeMount() {
  const ws = document.getElementById('settings-workspace');
  if (!ws || ws.classList.contains('hidden')) return;
  const mount = document.getElementById('bd-settings-theme-mount');
  if (mount && typeof renderHopThemeModule === 'function') {
    renderHopThemeModule(mount);
  }
}

/** If leaving Settings with a previewed theme, ask keep/discard like HoP. */
function guardBdThemeLeave(continueFn) {
  const ws = document.getElementById('settings-workspace');
  if (!ws || ws.classList.contains('hidden')) return false;
  if (typeof hopThemePageDirty !== 'function' || !hopThemePageDirty()) {
    if (typeof hopThemePageBaseline !== 'undefined') hopThemePageBaseline = null;
    return false;
  }
  if (typeof hopPromptBdThemeLeave === 'function') {
    hopPromptBdThemeLeave(continueFn);
    return true;
  }
  return false;
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

function isHopUserSession() {
  return authState.role === 'hop_admin' || authState.workspaceId === 'house_of_prizm';
}

/** Show only the HoP shell and open a HoP view (never BD modules). */
function showHopShell(viewName) {
  if (typeof closeMobileNav === 'function') closeMobileNav();
  if (typeof unmountBdModule === 'function') unmountBdModule();
  document.body.classList.remove('customers-page-active');
  document.body.classList.add('hop-active');
  document.documentElement.classList.add('hop-active');
  setGlobalSearchBarVisible(false);
  [
    'dashboard',
    'sales-workspace',
    'party-master-section',
    'order-fulfillment-workspace',
    'order-cycle-workspace',
    'order-desk-workspace',
    'article-master-workspace',
    'filled-orders-workspace',
    'executive-home-workspace',
    'target-vs-achievement-workspace',
    'cloud-hub-workspace',
    'market-visit-workspace',
    'pjp-workspace',
    'personal-todo-workspace',
  ].forEach((id) => document.getElementById(id)?.classList.add('hidden'));
  document.getElementById('hop-executive-workspace')?.classList.remove('hidden');
  currentModuleKey = 'hopexecutive';
  if (typeof bindHopNavClicks === 'function') bindHopNavClicks();
  if (typeof openHopView === 'function') openHopView(viewName || 'dashboard');
  else if (typeof loadHopExecutiveSnapshot === 'function') loadHopExecutiveSnapshot();
  requestAnimationFrame(() => {
    if (typeof bdSyncSidebarScroll === 'function') bdSyncSidebarScroll({ preserveScroll: true });
  });
}

function clearHopActiveScrollLock() {
  document.body.classList.remove('hop-active', 'hop-module-fullscreen');
  document.documentElement.classList.remove('hop-active', 'hop-module-fullscreen');
  document.getElementById('hop-executive-workspace')?.classList.remove('hop-ws--fullscreen');
}

function mobileNavHome() {
  if (isHopUserSession()) showHopShell('dashboard');
  else openModule('Dashboard');
}

function mobileNavCrm() {
  if (isHopUserSession()) showHopShell('customers');
  else openModule('Customers');
}

function mobileNavOrders() {
  if (isHopUserSession()) showHopShell('orders');
  else openModule('OrderDesk');
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
    dash.style.removeProperty('min-height');
    dash.style.removeProperty('top');
    dash.style.removeProperty('bottom');
    dash.style.removeProperty('height');
    dash.style.removeProperty('pointer-events');
    return;
  }
  dash.classList.remove('hidden');
  dash.classList.add('bd-module-mode');
  document.getElementById('bd-home-view')?.classList.add('hidden');
  dash.style.setProperty('right', 'auto', 'important');
  dash.style.setProperty('width', '232px', 'important');
  dash.style.setProperty('max-width', '232px', 'important');
  dash.style.setProperty('top', 'var(--nx-header-h, 64px)', 'important');
  dash.style.setProperty('bottom', '0', 'important');
  dash.style.setProperty('height', 'auto', 'important');
  dash.style.setProperty('min-height', 'calc(100dvh - var(--nx-header-h, 64px))', 'important');
  dash.style.setProperty('pointer-events', 'none', 'important');
  const nav = dash.querySelector('.hop-nav');
  if (nav) {
    nav.style.setProperty('pointer-events', 'auto', 'important');
    nav.style.setProperty('height', '100%', 'important');
    nav.style.setProperty('min-height', '100%', 'important');
  }
  const shell = dash.querySelector('.bd-shell');
  if (shell) {
    shell.style.setProperty('height', '100%', 'important');
    shell.style.setProperty('min-height', '100%', 'important');
  }
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
    document.getElementById('dashboard-widget-dock')?.classList.add('hidden');
    moduleEl.classList.remove('bd-mounted-module', 'bd-module-fullscreen', 'hidden');
    document.body.classList.remove('bd-module-open');
    document.body.appendChild(moduleEl);
    if (moduleEl.id === 'party-master-section') {
      requestAnimationFrame(() => scheduleCustomersLayout());
      setTimeout(() => scheduleCustomersLayout(), 80);
    }
    if (moduleEl.id === 'article-master-workspace') {
      requestAnimationFrame(() => scheduleArticleMasterLayout());
      setTimeout(() => scheduleArticleMasterLayout(), 80);
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
  document.body.classList.remove('customers-page-active', 'bd-module-open');
  document.querySelectorAll('.bd-module-fullscreen').forEach((el) => {
    el.classList.remove('bd-module-fullscreen');
  });
  unmountBdModule();
  const dash = document.getElementById('dashboard');
  dash?.classList.remove('hidden');
  dash?.classList.remove('bd-module-mode');
  if (dash) {
    dash.style.removeProperty('right');
    dash.style.removeProperty('width');
    dash.style.removeProperty('max-width');
    dash.style.removeProperty('min-height');
    dash.style.removeProperty('top');
    dash.style.removeProperty('bottom');
    dash.style.removeProperty('height');
    dash.style.removeProperty('pointer-events');
    const nav = dash.querySelector('.hop-nav');
    if (nav) {
      nav.style.removeProperty('pointer-events');
      nav.style.removeProperty('height');
      nav.style.removeProperty('min-height');
    }
    const shell = dash.querySelector('.bd-shell');
    if (shell) {
      shell.style.removeProperty('height');
      shell.style.removeProperty('min-height');
    }
  }
  document.getElementById('bd-home-view')?.classList.remove('hidden');
  document.getElementById('bd-module-mount')?.classList.add('hidden');
  document.getElementById('party-master-section')?.classList.add('hidden');
  ['sales-workspace', 'purchase-workspace', 'inventory-workspace', 'article-master-workspace', 'order-desk-workspace', 'order-fulfillment-workspace', 'order-cycle-workspace', 'executive-home-workspace', 'hop-executive-workspace', 'target-vs-achievement-workspace', 'cloud-hub-workspace', 'market-visit-workspace', 'pjp-workspace', 'personal-todo-workspace', 'filled-orders-workspace', 'settings-workspace'].forEach((id) => {
    document.getElementById(id)?.classList.add('hidden');
  });
  if (authState.role === 'sales_executive') {
    document.getElementById('dashboard-fo-widgets-layer')?.classList.remove('hidden');
    document.getElementById('dashboard-ta-playing-card')?.classList.remove('hidden');
    // Overlay modules hide the dock; bring it back when returning home.
    if (minimizedWidgets.size) {
      ensureWidgetDock().classList.remove('hidden');
      renderWidgetDock();
    }
    loadTaFyOverviewCard();
    loadFilledOrdersSeasonWidgets();
    if (typeof loadPersonalTodoWidgets === 'function') loadPersonalTodoWidgets();
    if (typeof loadPjpWeekWidgets === 'function') loadPjpWeekWidgets();
  }
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
  if (typeof loadPersonalTodoWidgets === 'function') loadPersonalTodoWidgets();
  if (typeof loadPjpWeekWidgets === 'function') loadPjpWeekWidgets();
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
  if (guardBdThemeLeave(() => goBack())) return;
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

  // Order Desk sidebar → Fulfillment directly (no intermediate landing page).
  if (normalized === 'orderdesk') {
    return openModule('OrderFulfillment');
  }

  // Leaving Order Fulfillment hard-deletes analyze + Excel workbook from memory.
  if (currentModuleKey === 'orderfulfillment' && normalized !== 'orderfulfillment'
      && typeof _soPackOnLeaveModule === 'function') {
    _soPackOnLeaveModule();
  }

  if (normalized !== 'settings' && guardBdThemeLeave(() => openModule(moduleName))) {
    return;
  }

  // HoP users must never land on BD shells (Customers → Distributors & Retailers, etc.).
  if (isHopUserSession()) {
    const hopViewByModule = {
      customers: 'customers',
      parties: 'customers',
      dashboard: 'dashboard',
      home: 'dashboard',
      myday: 'dashboard',
      orderdesk: 'orders',
      orders: 'orders',
      hopexecutive: 'dashboard',
      houseofprizm: 'dashboard',
    };
    if (Object.prototype.hasOwnProperty.call(hopViewByModule, normalized)) {
      if (!suppressModuleHistoryPush && currentModuleKey !== 'hopexecutive') {
        moduleHistoryStack.push(currentModuleKey);
      }
      suppressModuleHistoryPush = false;
      showHopShell(hopViewByModule[normalized]);
      return;
    }
  }

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
  if (typeof clearHopActiveScrollLock === 'function') clearHopActiveScrollLock();
  document.getElementById('target-vs-achievement-workspace')?.classList.add('hidden');
  document.getElementById('cloud-hub-workspace')?.classList.add('hidden');
  document.getElementById('market-visit-workspace')?.classList.add('hidden');
  document.getElementById('pjp-workspace')?.classList.add('hidden');
  document.getElementById('personal-todo-workspace')?.classList.add('hidden');
  if (normalized !== 'settings') {
    document.getElementById('settings-workspace')?.classList.add('hidden');
  }

  if (normalized === 'hopexecutive' || normalized === 'houseofprizm') {
    showHopShell('dashboard');
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

  if (normalized === 'marketvisit' || normalized === 'dsr' || normalized === 'dsrmarket') {
    pinBdShellForModule(document.getElementById('market-visit-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Market Visit');
    if (typeof initNxYmdPickers === 'function') initNxYmdPickers(document);
    loadMarketVisitWorkspace();
    return;
  }

  if (normalized === 'pjp' || normalized === 'journeyplan' || normalized === 'permanentjourneyplan') {
    pinBdShellForModule(document.getElementById('pjp-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('PJP');
    loadPjpWorkspace();
    return;
  }

  if (normalized === 'todo' || normalized === 'todos' || normalized === 'personaltodo') {
    pinBdShellForModule(document.getElementById('personal-todo-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('To-Do');
    loadPersonalTodoWorkspace();
    return;
  }

  if (normalized === 'ordercycle') {
    // Order Cycle UI removed for now — engine kept; reopen Order Desk.
    openModule('OrderDesk');
    return;
  }

  if (normalized === 'orderfulfillment') {
    pinBdShellForModule(document.getElementById('order-fulfillment-workspace'));
    document.getElementById('sales-workspace')?.classList.add('hidden');
    setActiveSidebarItem('Order Desk');
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
    loadArticleMasterList().then(() => scheduleArticleMasterLayout());
    requestAnimationFrame(() => scheduleArticleMasterLayout());
    return;
  }

  if (normalized === 'filledorders') {
    // Filled Orders list UI removed for now — engine kept; reopen Order Fulfillment.
    openModule('OrderFulfillment');
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
    setActiveSidebarItem('Know your Customer');
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
  document.getElementById('master-distributor-form-title').textContent = 'Add Distributor';
  ['master-distributor-firm-name','master-distributor-firm-nick-name','master-distributor-name','master-distributor-contact-role','master-distributor-code','master-distributor-phone','master-distributor-phone-2','master-distributor-email','master-distributor-address','master-distributor-location','master-distributor-region','master-distributor-pincode','master-distributor-gst','master-distributor-territory','master-distributor-payment-terms','master-distributor-credit-limit','master-distributor-birthday','master-distributor-anniversary','master-distributor-secondary-name','master-distributor-secondary-phone','master-distributor-secondary-birthday','master-distributor-secondary-anniversary','master-distributor-sales-name','master-distributor-sales-phone','master-distributor-sales-email','master-distributor-sales-birthday','master-distributor-sales-anniversary'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const body = document.querySelector('#master-distributor-form-modal .bd-master-form-body');
  if (body) body.scrollTop = 0;
}

function openDistributorEditFromRecord(record) {
  if (!record || !record.partyId) return;
  if (record.source === 'master') {
    editMasterDistributor(record.partyId);
    return;
  }
  editDistributor(record.partyId);
}

function resetMasterRetailerForm() {
  document.getElementById('master-retailer-id').value = '';
  document.getElementById('master-retailer-form-title').textContent = 'Add Retailer';
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
  const distCode = document.getElementById('master-distributor-code').value.trim() || undefined;
  const territoryVal = document.getElementById('master-distributor-territory')?.value.trim() || undefined;
  const body = {
    name: document.getElementById('master-distributor-name').value.trim(),
    contact_person_role: document.getElementById('master-distributor-contact-role')?.value.trim() || undefined,
    firm_name: document.getElementById('master-distributor-firm-name').value.trim() || undefined,
    firm_nick_name: document.getElementById('master-distributor-firm-nick-name').value.trim() || undefined,
    distributor_code: distCode,
    buyer_code: distCode,
    phone_number: document.getElementById('master-distributor-phone').value.trim() || undefined,
    phone_number_2: document.getElementById('master-distributor-phone-2').value.trim() || undefined,
    email: document.getElementById('master-distributor-email').value.trim() || undefined,
    address: document.getElementById('master-distributor-address').value.trim() || undefined,
    location: document.getElementById('master-distributor-location').value.trim() || undefined,
    region: document.getElementById('master-distributor-region').value.trim() || undefined,
    pincode: document.getElementById('master-distributor-pincode').value.trim() || undefined,
    gst_no: document.getElementById('master-distributor-gst').value.trim() || undefined,
    territory: territoryVal,
    zone: territoryVal,
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
    await refreshCustomersAfterMasterChange('distributors');
    nexoraToast('Distributor saved.', 'success');
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
    resetMasterDistributorForm();
    document.getElementById('master-distributor-id').value = record.id;
    document.getElementById('master-distributor-form-title').textContent = 'Edit Distributor';
    document.getElementById('master-distributor-firm-name').value = record.firm_name || '';
    document.getElementById('master-distributor-firm-nick-name').value = record.firm_nick_name || '';
    document.getElementById('master-distributor-name').value = record.name || '';
    const roleEl = document.getElementById('master-distributor-contact-role');
    if (roleEl) roleEl.value = record.contact_person_role || '';
    document.getElementById('master-distributor-code').value =
      record.buyer_code || record.distributor_code || record.distributor_id || '';
    document.getElementById('master-distributor-phone').value = record.phone_number || '';
    document.getElementById('master-distributor-phone-2').value = record.phone_number_2 || '';
    document.getElementById('master-distributor-email').value = record.email || '';
    document.getElementById('master-distributor-address').value = record.address || '';
    document.getElementById('master-distributor-location').value = record.location || '';
    document.getElementById('master-distributor-region').value = record.region || '';
    document.getElementById('master-distributor-pincode').value = record.pincode || '';
    document.getElementById('master-distributor-gst').value = record.gst_no || '';
    const territoryEl = document.getElementById('master-distributor-territory');
    if (territoryEl) territoryEl.value = record.territory || record.zone || '';
    document.getElementById('master-distributor-payment-terms').value = record.payment_terms || '';
    document.getElementById('master-distributor-credit-limit').value = record.credit_limit ?? '';
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
    const body = document.querySelector('#master-distributor-form-modal .bd-master-form-body');
    if (body) body.scrollTop = 0;
  } catch (error) {
    alert(error.message || 'Error loading distributor.');
  }
}

function isMastersGridOpen() {
  const modal = document.getElementById('masters-grid-modal');
  return Boolean(modal && !modal.classList.contains('hidden'));
}

async function refreshCustomersAfterMasterChange(kind) {
  if (kind === 'distributors') {
    partyMasterTableState.distributorsLoaded = false;
    partyMasterTableState.distributorsLoading = false;
    partyMasterState.allDistributorRecords = [];
    partyMasterState.rawDistributorRecords = [];
    if (!document.getElementById('party-master-section')?.classList.contains('hidden')) {
      await loadDistributors();
    }
    if (isMastersGridOpen()) await openMastersGrid('distributors');
    return;
  }
  partyMasterTableState.retailersLoaded = false;
  partyMasterTableState.retailersLoading = false;
  partyMasterState.allRetailerRecords = [];
  partyMasterState.rawRetailerRecords = [];
  if (!document.getElementById('party-master-section')?.classList.contains('hidden')) {
    await loadRetailers();
  }
  if (isMastersGridOpen()) await openMastersGrid('retailers');
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
    await refreshCustomersAfterMasterChange('retailers');
    nexoraToast('Retailer saved.', 'success');
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
    document.getElementById('master-retailer-form-title').textContent = 'Edit Retailer';
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
      ['contact_person_role', 'Contact Role'],
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
    const columns = [['actions', 'Actions'], ...visibleColumns];

    thead.innerHTML = `<tr>${columns.map(([, label]) => `<th>${label}</th>`).join('')}</tr>`;

    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="${columns.length}">No records yet.</td></tr>`;
    } else {
      tbody.innerHTML = rows
        .map((row) => {
          const actions = masterType === 'distributors'
            ? partyMasterActionButtons(`editMasterDistributor(${row.id})`, `deleteMasterDistributor(${row.id})`)
            : partyMasterActionButtons(`editMasterRetailer(${row.id})`, `deleteMasterRetailer(${row.id})`);
          const cells = columns
            .map(([key]) => {
              if (key === 'actions') return `<td class="pm-actions">${actions}</td>`;
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
    const response = await fetchWithAuth(`/api/v1/search?q=${encodeURIComponent(query)}`);
    const rawText = await response.text();
    let data;
    try {
      data = JSON.parse(rawText);
    } catch (parseErr) {
      throw new Error(
        response.ok
          ? 'Search returned invalid JSON'
          : `Search failed (HTTP ${response.status}). Try logout and login again.`
      );
    }
    if (!response.ok) {
      throw new Error(data?.error?.message || data?.message || `Search failed (HTTP ${response.status})`);
    }

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
    ['contact_person_role', 'Contact Role'],
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
    ['order_ref_no', 'SO / Order Ref'],
    ['invoice_no', 'CI No'],
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
      ? (index) => `openArticleMasterFromSearch(${index})`
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

const BD_BRAND_COMPANY_FALLBACK = 'NORTH HEAD OFFICE';

function setBdNavBrandCompanyName(name) {
  const el = document.getElementById('bd-nav-brand-company');
  if (!el) return;
  const label = String(name || '').trim() || BD_BRAND_COMPANY_FALLBACK;
  el.textContent = label;
  el.title = label === BD_BRAND_COMPANY_FALLBACK ? 'Set company name in Settings → Company' : label;
  const brand = el.closest('.hop-nav-brand');
  if (brand) {
    brand.setAttribute('aria-label', `Go to home — ${label}`);
  }
}

async function syncBdBrandFromCompanyProfile() {
  if (authState.role !== 'sales_executive' || !authState.accessToken) return;
  try {
    const response = await fetchWithAuth('/api/v1/company-profile');
    const data = await response.json();
    if (response.ok && data.success && data.data?.company_name) {
      setBdNavBrandCompanyName(data.data.company_name);
      return;
    }
  } catch (e) {
    /* keep current / fallback */
  }
  setBdNavBrandCompanyName('');
}

async function loadCompanyProfileV2() {
  const nameInput = document.getElementById('bd-company-name');
  const gstInput = document.getElementById('bd-company-gst');
  if (!nameInput || !gstInput) return;
  try {
    const response = await fetchWithAuth('/api/v1/company-profile');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load company profile');
    }
    if (data.data) {
      nameInput.value = data.data.company_name || '';
      gstInput.value = data.data.gst_number || '';
      setBdNavBrandCompanyName(data.data.company_name || '');
    } else {
      nameInput.value = '';
      gstInput.value = '';
      setBdNavBrandCompanyName('');
    }
  } catch (error) {
    if (typeof nexoraToast === 'function') {
      nexoraToast(error.message || 'Could not load company profile', 'error');
    }
  }
}

async function saveCompanyProfileV2() {
  const nameInput = document.getElementById('bd-company-name');
  const gstInput = document.getElementById('bd-company-gst');
  const saveBtn = document.getElementById('bd-company-save-btn');
  if (!nameInput || !gstInput) return;
  const name = nameInput.value.trim();
  const gst = gstInput.value.trim().toUpperCase();
  gstInput.value = gst;

  if (!name) {
    if (typeof nexoraToast === 'function') nexoraToast('Company name is required.', 'warn');
    nameInput.focus();
    return;
  }

  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
  }
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
    setBdNavBrandCompanyName(data.data.company_name || name);
    if (typeof nexoraToast === 'function') {
      nexoraToast('Company profile saved', 'success');
    }
  } catch (error) {
    if (typeof nexoraToast === 'function') {
      nexoraToast(error.message || 'Could not save company profile', 'error');
    }
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = 'Save profile';
    }
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
    const container = document.getElementById('oc-accordion');
    const emptyState = document.getElementById('oc-empty-state');
    // UI shell removed — keep engine callable for later rebuild.
    if (!container) return;

    const response = await fetchWithAuth('/api/v1/order-fulfillment/order-cycle');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load Order Cycle data');
    }
    orderCycleData = data.data;

    if (!orderCycleData.financial_years.length) {
      container.innerHTML = '';
      emptyState?.classList.remove('hidden');
      return;
    }
    emptyState?.classList.add('hidden');

    // First Financial Year open by default, so there's something to see immediately.
    if (!ocExpandedFy.size) {
      ocExpandedFy.add(orderCycleData.financial_years[0].fy);
    }
    renderOcAccordion();
  } catch (error) {
    const container = document.getElementById('oc-accordion');
    if (container) {
      container.innerHTML =
        `<div class="nx-oc-error">Error: ${foEscapeText(error.message)}</div>`;
    }
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
  if (!container) return;
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

let ofSoPackLastPayload = null;
let ofSoPackFoMatchResult = null;
let ofSoPackFilledOrdersCache = null;
let ofSoPackActiveTab = 'consolidated';
/** Fingerprint of the ZIP/RAR that currently backs ofSoPackLastPayload / on-screen table. */
let ofSoPackAnalyzedKey = null;
/** Cached Excel workbook for the current analyzed pack (cleared on new ZIP / re-analyze / leave module / refresh). */
let ofSoPackExcelBlob = null;
let ofSoPackExcelKey = null;
/** Multi-pack session: [{ key, filename, payload?, error? }, ...] matching current file selection. */
let ofSoPackBatch = [];
/** Fingerprint of the full multi-file selection that ofSoPackBatch was analyzed against. */
let ofSoPackBatchSelectionKey = null;
/** Cached ZIP of Excels for multi-pack download. */
let ofSoPackBatchExcelBlob = null;
let ofSoPackBatchExcelKey = null;
/** Bump when workbook layout changes so stale in-memory Excel is never re-served. */
const OF_SO_PACK_EXCEL_FORMAT = 'multi-pack-zip-v1';

function _soPackFileKey(file) {
  if (!file) return null;
  return `${file.name}|${file.size}|${file.lastModified}`;
}

function _soPackSelectedFiles() {
  const fileInput = document.getElementById('of-so-pack-file');
  if (!fileInput || !fileInput.files || !fileInput.files.length) return [];
  return Array.from(fileInput.files);
}

function _soPackSelectionKey(files) {
  const list = files || _soPackSelectedFiles();
  if (!list.length) return null;
  return list.map((f) => _soPackFileKey(f)).join('||');
}

function _soPackExcelCacheKey(packKey) {
  return packKey ? `${packKey}|${OF_SO_PACK_EXCEL_FORMAT}` : null;
}

function _soPackClearExcelCache() {
  ofSoPackExcelBlob = null;
  ofSoPackExcelKey = null;
  ofSoPackBatchExcelBlob = null;
  ofSoPackBatchExcelKey = null;
}

/** Drop analyze preview + Excel blob (new ZIP, leave module, hard reset). */
function _soPackHardClearMemory() {
  ofSoPackLastPayload = null;
  ofSoPackFoMatchResult = null;
  ofSoPackFilledOrdersCache = null;
  ofSoPackAnalyzedKey = null;
  ofSoPackBatch = [];
  ofSoPackBatchSelectionKey = null;
  _soPackClearExcelCache();
  const pick = document.getElementById('of-so-pack-pick');
  const bar = document.getElementById('of-so-pack-batch-bar');
  if (pick) pick.innerHTML = '';
  if (bar) {
    bar.classList.add('hidden');
    bar.style.display = 'none';
  }
  const foPick = document.getElementById('of-so-pack-fo-pick');
  if (foPick) foPick.innerHTML = '<option value="">— Analyze pack first —</option>';
  const matchBtn = document.getElementById('of-so-pack-fo-match-btn');
  if (matchBtn) matchBtn.disabled = true;
  _clearSoPackFoMatchUi();
}

function _soPackBuyerLabel(payload) {
  const buyers = [];
  const seen = new Set();
  const rows = [...(payload && payload.so_summary ? payload.so_summary : []),
    ...(payload && payload.consolidated ? payload.consolidated : [])];
  for (const row of rows) {
    const name = String((row && row.buyer_name) || '').trim();
    if (!name) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    buyers.push(name);
  }
  if (buyers.length === 1) return buyers[0];
  if (buyers.length > 1) {
    return buyers.length <= 3
      ? buyers.join('_')
      : `${buyers[0]}_and_${buyers.length - 1}_more`;
  }
  const src = String((payload && payload.meta && payload.meta.source_filename) || '');
  return src.replace(/\.(zip|rar|pdf)$/i, '').replace(/_\d+_PDFs$/i, '').trim() || 'SO_Pack';
}

function _soPackExcelDownloadName(payload) {
  const label = _soPackBuyerLabel(payload);
  const safe = label
    .replace(/[<>:"/\\|?*\u0000-\u001f]+/g, '')
    .replace(/\s+/g, ' ')
    .replace(/^[\s.]+|[\s.]+$/g, '')
    .slice(0, 100) || 'SO_Pack';
  return `${safe}_SO_Pack.xlsx`;
}

function _soPackTriggerExcelDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || _soPackExcelDownloadName(ofSoPackLastPayload);
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function _soPackOkPacks() {
  return (ofSoPackBatch || []).filter((p) => p && p.payload && !p.error);
}

function _soPackBatchReadyForSelection() {
  const files = _soPackSelectedFiles();
  const selKey = _soPackSelectionKey(files);
  if (!files.length || !selKey || !ofSoPackBatchSelectionKey) return false;
  if (selKey !== ofSoPackBatchSelectionKey) return false;
  const built = _soPackBuildJobs(files);
  const jobs = (built && built.jobs) || [];
  if (!jobs.length || ofSoPackBatch.length !== jobs.length) return false;
  return _soPackOkPacks().length > 0;
}

function _soPackBuildJobs(files) {
  const list = files || [];
  const archives = [];
  const pdfs = [];
  const other = [];
  for (const f of list) {
    const n = String(f.name || '').toLowerCase();
    if (n.endsWith('.zip') || n.endsWith('.rar')) archives.push(f);
    else if (n.endsWith('.pdf')) pdfs.push(f);
    else other.push(f);
  }
  const jobs = archives.map((f) => ({
    key: _soPackFileKey(f),
    filename: f.name,
    files: [f],
  }));
  if (pdfs.length) {
    jobs.push({
      key: pdfs.map((f) => _soPackFileKey(f)).join('++'),
      filename: pdfs.length === 1 ? pdfs[0].name : `${pdfs.length} PDFs`,
      files: pdfs,
    });
  }
  return { jobs, other };
}

/** White/disabled until Analyze succeeds for the selected pack(s); then match Analyze (primary) colour. */
function updateSoPackExcelButtonState() {
  const btn = document.getElementById('of-so-pack-excel-btn');
  if (!btn) return;
  const ready = _soPackBatchReadyForSelection();
  const okN = _soPackOkPacks().length;
  btn.disabled = !ready || _soPackBusyInFlight;
  btn.classList.toggle('nx-btn-primary', ready);
  if (!ready) {
    btn.title = 'First click Analyze, then Excel Download';
    btn.textContent = 'Excel Download';
  } else if (okN > 1) {
    btn.textContent = `Excel Download (${okN})`;
    const cacheHit = ofSoPackBatchExcelBlob && ofSoPackBatchExcelKey === _soPackExcelCacheKey(ofSoPackBatchSelectionKey);
    btn.title = cacheHit
      ? `Download ZIP of ${okN} Excels (cached — instant)`
      : `Download ZIP with ${okN} separate Excel files`;
  } else {
    btn.textContent = 'Excel Download';
    const only = _soPackOkPacks()[0];
    const cacheHit = ofSoPackExcelBlob && only && ofSoPackExcelKey === _soPackExcelCacheKey(only.key);
    btn.title = cacheHit ? 'Download Excel (cached — instant)' : 'Download Excel for this analyzed pack';
  }
}

function _soPackClearPreviewForNewFile() {
  _soPackHardClearMemory();
  const preview = document.getElementById('of-so-pack-preview');
  if (preview) preview.classList.add('hidden');
  const kpis = document.getElementById('of-so-pack-kpis');
  if (kpis) kpis.innerHTML = '';
  const statusEl = document.getElementById('of-so-pack-status');
  if (statusEl) statusEl.textContent = '';
  const thead = document.getElementById('of-so-pack-thead');
  const tbody = document.getElementById('of-so-pack-tbody');
  if (thead) thead.innerHTML = '';
  if (tbody) tbody.innerHTML = '';
  if (typeof showOfSection === 'function') {
    /* keep current section; just hide stale pack preview */
  }
  updateSoPackExcelButtonState();
}

/** Leave Order Fulfillment / hard reset: wipe memory + on-screen SO Pack state + file input. */
function _soPackOnLeaveModule() {
  _soPackClearPreviewForNewFile();
  const ofResult = document.getElementById('of-so-pack-result');
  if (ofResult) {
    ofResult.textContent = '';
    ofResult.classList.remove('so-pack-ok', 'so-pack-busy');
    ofResult.removeAttribute('title');
  }
  const ofFile = document.getElementById('of-so-pack-file');
  if (ofFile) ofFile.value = '';
}

function bindSoPackFileInput() {
  const fileInput = document.getElementById('of-so-pack-file');
  if (!fileInput || fileInput.dataset.soPackBound === '1') return;
  fileInput.dataset.soPackBound = '1';
  fileInput.addEventListener('change', () => {
    const files = _soPackSelectedFiles();
    const key = _soPackSelectionKey(files);
    if (!files.length) {
      _soPackClearPreviewForNewFile();
      _soPackShowMessage('', false);
      return;
    }
    // New selection must not keep showing a previous pack on screen.
    if (key !== ofSoPackBatchSelectionKey) {
      _soPackClearPreviewForNewFile();
      const n = files.length;
      _soPackShowMessage(
        n > 1
          ? `${n} files selected — click Analyze.`
          : 'New file selected — click Analyze first.',
        true,
      );
    }
  });
}

function _renderSoPackBatchPicker() {
  const bar = document.getElementById('of-so-pack-batch-bar');
  const pick = document.getElementById('of-so-pack-pick');
  if (!bar || !pick) return;
  const items = ofSoPackBatch || [];
  if (items.length <= 1) {
    bar.classList.add('hidden');
    bar.style.display = 'none';
    pick.innerHTML = '';
    return;
  }
  bar.classList.remove('hidden');
  bar.style.display = 'flex';
  pick.innerHTML = items.map((p, idx) => {
    const label = p.payload
      ? `${_soPackBuyerLabel(p.payload)} (${p.filename})`
      : `${p.filename} — failed`;
    const selected = p.key === ofSoPackAnalyzedKey ? ' selected' : '';
    const disabled = p.error || !p.payload ? ' disabled' : '';
    return `<option value="${foEscapeText(String(idx))}"${selected}${disabled}>${foEscapeText(label)}</option>`;
  }).join('');
}

function selectSoPackBatchItem(idxStr) {
  const idx = Number(idxStr);
  const item = ofSoPackBatch[idx];
  if (!item || !item.payload) return;
  _renderSoPackPreview(item.payload, item.key, { skipBatchPicker: true });
}

function _soPackMoney(n) {
  const v = Number(n || 0);
  if (!Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function showSoPackTab(tab) {
  ofSoPackActiveTab = tab || 'consolidated';
  if (ofSoPackActiveTab === 'fo_match') ofSoPackActiveTab = 'consolidated';
  document.querySelectorAll('[data-so-pack-tab]').forEach((btn) => {
    const on = btn.getAttribute('data-so-pack-tab') === ofSoPackActiveTab;
    btn.classList.toggle('nx-btn-primary', on);
    btn.classList.toggle('btn-primary', on);
    btn.classList.toggle('btn-secondary', !on);
  });
  if (!ofSoPackLastPayload) return;
  const thead = document.getElementById('of-so-pack-thead');
  const tbody = document.getElementById('of-so-pack-tbody');
  if (!thead || !tbody) return;

  let headers = [];
  let rows = [];
  if (ofSoPackActiveTab === 'so_summary') {
    headers = ['SO Number', 'Order Date', 'Buyer', 'PO', 'Products', 'SKU Lines', 'Qty', 'Net', 'GST', 'Total', 'PDF'];
    rows = (ofSoPackLastPayload.so_summary || []).map((r) => [
      r.so_number, r.order_date, r.buyer_name, r.po_number, r.product_types, r.sku_lines,
      r.total_qty, _soPackMoney(r.net_amount), _soPackMoney(r.gst_amount), _soPackMoney(r.total_amount), r.source_pdf,
    ]);
  } else if (ofSoPackActiveTab === 'line_detail') {
    headers = ['SO', 'Material', 'Product', 'Detail', 'Qty', 'Rate', 'Net', 'GST', 'Total', 'PDF'];
    rows = (ofSoPackLastPayload.line_detail || []).slice(0, 500).map((r) => [
      r.so_number, r.material_code, r.product_name, r.product_detail, r.qty, r.rate,
      _soPackMoney(r.net_amount), _soPackMoney(r.gst_amount), _soPackMoney(r.total_amount), r.source_pdf,
    ]);
  } else {
    headers = ['SO Number', 'Order Date', 'Buyer Name', 'PO Number', 'Product Name', 'SKU Lines', 'Total Qty', 'Net', 'GST', 'Total'];
    rows = (ofSoPackLastPayload.consolidated || []).map((r) => [
      r.so_number, r.order_date, r.buyer_name, r.po_number, r.product_name, r.sku_lines, r.total_qty,
      _soPackMoney(r.net_amount), _soPackMoney(r.gst_amount), _soPackMoney(r.total_amount),
    ]);
  }

  thead.innerHTML = `<tr>${headers.map((h) => `<th>${foEscapeText(String(h))}</th>`).join('')}</tr>`;
  tbody.innerHTML = rows.length
    ? rows.map((cols) => `<tr>${cols.map((c) => `<td>${foEscapeText(c == null ? '—' : String(c))}</td>`).join('')}</tr>`).join('')
    : `<tr><td colspan="${headers.length}">No rows</td></tr>`;
}

function _clearSoPackFoMatchUi() {
  // Match results now live on the Order Match page (Saved Orders layout).
}

function _soPackSoftKey(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim().replace(/\s+/g, ' ');
}

function _scoreFoForBuyer(order, buyerLabel) {
  const buyer = _soPackSoftKey(buyerLabel);
  if (!buyer) return 0;
  const names = [
    order.distributor_name_raw,
    order.distributor_name,
    order.firm_name,
    order.firm_nick_name,
  ];
  let best = 0;
  for (const name of names) {
    const key = _soPackSoftKey(name);
    if (!key) continue;
    if (key.includes(buyer) || buyer.includes(key)) best = Math.max(best, 0.95);
    // crude token overlap
    const a = new Set(key.split(' '));
    const b = new Set(buyer.split(' '));
    let hit = 0;
    a.forEach((t) => { if (b.has(t)) hit += 1; });
    const ratio = hit / Math.max(a.size, b.size, 1);
    best = Math.max(best, ratio);
  }
  return best;
}

async function _loadSoPackFilledOrderOptions() {
  const sel = document.getElementById('of-so-pack-fo-pick');
  const btn = document.getElementById('of-so-pack-fo-match-btn');
  if (!sel) return;
  if (!ofSoPackLastPayload) {
    sel.innerHTML = '<option value="">— Analyze pack first —</option>';
    if (btn) btn.disabled = true;
    return;
  }
  const buyer = _soPackBuyerLabel(ofSoPackLastPayload);
  try {
    if (!ofSoPackFilledOrdersCache) {
      const response = await fetchWithAuth('/api/v1/filled-orders/list');
      const data = await parseApiJson(response);
      if (!response.ok) throw new Error((data.error && data.error.message) || data.error || 'Unable to load filled orders');
      ofSoPackFilledOrdersCache = data.filled_orders || [];
    }
    const orders = [...(ofSoPackFilledOrdersCache || [])].map((o) => ({
      ...o,
      _score: _scoreFoForBuyer(o, buyer),
    }));
    orders.sort((a, b) => (b._score - a._score)
      || String(b.created_at || '').localeCompare(String(a.created_at || ''))
      || (Number(b.id) - Number(a.id)));

    if (!orders.length) {
      sel.innerHTML = '<option value="">— No saved Filled Orders — upload in step 2 —</option>';
      if (btn) btn.disabled = true;
      return;
    }

    const suggested = orders.find((o) => o._score >= 0.45) || (orders.length === 1 ? orders[0] : null);
    sel.innerHTML = [
      '<option value="">— Choose saved Filled Order —</option>',
      ...orders.map((o) => {
        const label = [
          o.distributor_name_raw || o.distributor_name || `Dist #${o.distributor_id || '?'}`,
          o.category,
          o.season,
          o.source_filename,
          o.total_piece_qty != null ? `${o.total_piece_qty} pcs` : null,
          o._score >= 0.45 ? '★ suggested' : null,
        ].filter(Boolean).join(' · ');
        const selected = suggested && Number(suggested.id) === Number(o.id) ? ' selected' : '';
        return `<option value="${Number(o.id)}"${selected}>${foEscapeText(label)}</option>`;
      }),
    ].join('');
    if (btn) btn.disabled = !sel.value;
    sel.onchange = () => {
      if (btn) btn.disabled = !sel.value;
      ofSoPackFoMatchResult = null;
      _clearSoPackFoMatchUi();
    };
  } catch (err) {
    sel.innerHTML = `<option value="">— ${foEscapeText(err.message || 'Load failed')} —</option>`;
    if (btn) btn.disabled = true;
  }
}

function _matchLabStatusClass(status) {
  if (status === 'MATCH' || status === 'MATCH_FUZZY_BRAND') return 'color:#3dd68c;font-weight:600;';
  if (status === 'QTY_MISMATCH' || status === 'VALUE_MISMATCH') return 'color:#ffb020;font-weight:600;';
  return 'color:#ff6b6b;font-weight:600;';
}

async function runSoPackFoMatch() {
  const sel = document.getElementById('of-so-pack-fo-pick');
  const btn = document.getElementById('of-so-pack-fo-match-btn');
  const resultBox = document.getElementById('of-so-pack-result');
  const filledOrderId = sel && sel.value ? Number(sel.value) : null;
  if (!ofSoPackLastPayload) {
    if (resultBox) {
      resultBox.textContent = 'Analyze a SO Pack first.';
      resultBox.classList.remove('so-pack-ok');
    }
    return;
  }
  if (!filledOrderId) {
    if (resultBox) {
      resultBox.textContent = 'Choose a saved Filled Order.';
      resultBox.classList.remove('so-pack-ok');
    }
    return;
  }
  if (resultBox) resultBox.textContent = 'Matching Filled Order vs SO Pack…';
  if (btn) btn.disabled = true;
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/match-filled-order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filled_order_id: filledOrderId,
        so_pack: ofSoPackLastPayload,
        so_buyer_label: _soPackBuyerLabel(ofSoPackLastPayload),
        so_source_filename: (ofSoPackLastPayload.meta || {}).source_filename || null,
      }),
    });
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Match failed');
    }
    ofSoPackFoMatchResult = data.data || {};
    const runId = ofSoPackFoMatchResult.run_id || (ofSoPackFoMatchResult.run && ofSoPackFoMatchResult.run.id);
    if (resultBox) {
      resultBox.textContent = 'Match saved — opening Order Match…';
      resultBox.classList.add('so-pack-ok');
    }
    if (typeof nexoraToast === 'function') nexoraToast('FO vs SO match saved', 'success');
    await openOrderMatchWorkspace(runId);
  } catch (err) {
    ofSoPackFoMatchResult = null;
    if (resultBox) {
      resultBox.textContent = err.message || 'Match failed';
      resultBox.classList.remove('so-pack-ok');
    }
  } finally {
    if (btn) btn.disabled = !(sel && sel.value);
  }
}

const orderMatchState = {
  runs: [],
  grouped: [],
  selectedDistributorKey: '',
  selectedRunId: null,
  detail: null,
  expandedDistributorKeys: {},
  expandedSeasonKeys: {},
};

function _orderMatchGroupKey(run) {
  if (run.distributor_id) return `id:${run.distributor_id}`;
  return `name:${String(run.distributor_name || run.so_buyer_label || 'Unknown').trim().toLowerCase()}`;
}

function _buildOrderMatchGroups() {
  const map = new Map();
  for (const run of orderMatchState.runs || []) {
    const key = _orderMatchGroupKey(run);
    const name = run.distributor_name || run.so_buyer_label || `Distributor #${run.distributor_id || '?'}`;
    if (!map.has(key)) {
      map.set(key, { key, distributorName: name, runs: [] });
    }
    map.get(key).runs.push(run);
  }
  return [...map.values()].map((group) => {
    const runs = group.runs
      .slice()
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')) || Number(b.id || 0) - Number(a.id || 0));
    const seasonMap = new Map();
    runs.forEach((run) => {
      const label = orderDeskSeasonLabel(run.season);
      if (!seasonMap.has(label)) {
        seasonMap.set(label, { label, runs: [], totalQty: 0, totalValue: 0 });
      }
      const bucket = seasonMap.get(label);
      bucket.runs.push(run);
      bucket.totalQty += Number(run.so_qty || run.fo_qty || 0);
      bucket.totalValue += Number(run.so_net_amount || run.fo_exmill_value || 0);
    });
    const seasons = Array.from(seasonMap.values())
      .map((s) => ({
        ...s,
        runs: s.runs.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))),
      }))
      .sort((a, b) => {
        const aOther = a.label === 'Others' ? 1 : 0;
        const bOther = b.label === 'Others' ? 1 : 0;
        if (aOther !== bOther) return aOther - bOther;
        const aDate = a.runs[0]?.created_at || '';
        const bDate = b.runs[0]?.created_at || '';
        return String(bDate).localeCompare(String(aDate)) || a.label.localeCompare(b.label);
      });
    return {
      ...group,
      runs,
      seasons,
      latest: runs[0] || null,
      totalQty: runs.reduce((sum, r) => sum + Number(r.so_qty || r.fo_qty || 0), 0),
      totalValue: runs.reduce((sum, r) => sum + Number(r.so_net_amount || r.fo_exmill_value || 0), 0),
    };
  }).sort((a, b) => a.distributorName.localeCompare(b.distributorName));
}

function renderOrderMatchDistributorRail() {
  const host = document.getElementById('of-match-distributor-list');
  if (!host) return;
  const groups = orderMatchState.grouped || [];
  if (!groups.length) {
    host.innerHTML = '<p class="nx-text-dim" style="padding:0.75rem;font-size:0.85rem;">No matches yet. Run Match FO on SO Pack.</p>';
    return;
  }
  host.innerHTML = groups.map((group) => {
    const active = group.key === orderMatchState.selectedDistributorKey;
    const expanded = !!orderMatchState.expandedDistributorKeys[group.key];
    const latest = group.latest || {};
    const latestHint = [orderDeskSeasonLabel(latest.season), latest.category, String(latest.created_at || '').slice(0, 10)]
      .filter(Boolean)
      .join(' · ');
    const metrics = formatOrderDeskQtyValue(group.totalQty, group.totalValue);
    const seasonHtml = expanded
      ? `<div class="of-tree-seasons">${(group.seasons || []).map((season) => {
          const seasonKey = `${group.key}||${season.label}`;
          const seasonOpen = !!orderMatchState.expandedSeasonKeys[seasonKey];
          const seasonMetrics = formatOrderDeskQtyValue(season.totalQty, season.totalValue);
          const runsHtml = seasonOpen
            ? `<div class="of-tree-orders">${season.runs.map((r) => {
                const selected = Number(orderMatchState.selectedRunId) === Number(r.id);
                const title = [r.category, r.so_source_filename || r.fo_source_filename].filter(Boolean).join(' · ') || `Match #${r.id}`;
                const hint = [
                  orderDeskSeasonLabel(r.season),
                  `${Number(r.match_count || 0)} matched`,
                  formatOrderDeskQtyValue(r.so_qty || r.fo_qty, r.so_net_amount || r.fo_exmill_value),
                ].filter(Boolean).join(' · ');
                return `
                  <button type="button"
                    class="of-tree-order-btn ${selected ? 'is-active' : ''}"
                    data-match-run-id="${Number(r.id)}">
                    <span class="of-rail-label">${foEscapeText(title)}</span>
                    <span class="of-rail-hint">${foEscapeText(hint)}</span>
                  </button>`;
              }).join('')}</div>`
            : '';
          const seasonHint = [season.runs.length > 1 ? `${season.runs.length} matches` : '', seasonMetrics]
            .filter(Boolean)
            .join(' · ');
          return `
            <div class="of-tree-season ${seasonOpen ? 'is-open' : ''}">
              <button type="button" class="of-tree-season-btn" data-match-season-key="${encodeURIComponent(seasonKey)}">
                <span class="of-tree-folder-ico">📁</span>
                <span class="of-rail-text">
                  <span class="of-rail-label">${foEscapeText(season.label)}</span>
                  <span class="of-rail-hint">${foEscapeText(seasonHint)}</span>
                </span>
              </button>
              ${runsHtml}
            </div>`;
        }).join('')}</div>`
      : '';
    const distHint = [latestHint, group.runs.length > 1 ? `${group.runs.length} matches` : '', metrics]
      .filter(Boolean)
      .join(' · ');
    return `
      <div class="of-tree-dist ${expanded ? 'is-open' : ''} ${active ? 'is-active' : ''}">
        <button type="button" class="of-rail-item of-match-distributor-btn ${active ? 'is-active' : ''}" data-match-distributor-key="${encodeURIComponent(group.key)}">
          <span class="of-rail-text">
            <span class="of-rail-label">${foEscapeText(group.distributorName)}</span>
            <span class="of-rail-hint">${foEscapeText(distHint)}</span>
          </span>
        </button>
        ${seasonHtml}
      </div>`;
  }).join('');
  host.querySelectorAll('.of-match-distributor-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      toggleOrderMatchDistributor(decodeURIComponent(btn.getAttribute('data-match-distributor-key') || ''));
    });
  });
  host.querySelectorAll('.of-tree-season-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = decodeURIComponent(btn.getAttribute('data-match-season-key') || '');
      if (!key) return;
      orderMatchState.expandedSeasonKeys[key] = !orderMatchState.expandedSeasonKeys[key];
      renderOrderMatchDistributorRail();
    });
  });
  host.querySelectorAll('.of-tree-order-btn[data-match-run-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const runId = Number(btn.getAttribute('data-match-run-id') || 0);
      if (runId) selectOrderMatchRun(runId);
    });
  });
}

function toggleOrderMatchDistributor(key) {
  if (!key) return;
  const wasOpen = !!orderMatchState.expandedDistributorKeys[key];
  orderMatchState.expandedDistributorKeys[key] = !wasOpen;
  if (!wasOpen) selectOrderMatchDistributor(key);
  else renderOrderMatchDistributorRail();
}


function renderOrderMatchRunPicker() {
  const picker = document.getElementById('of-match-run-pick');
  const title = document.getElementById('of-match-distributor-title');
  const group = (orderMatchState.grouped || []).find((g) => g.key === orderMatchState.selectedDistributorKey);
  if (title) {
    title.textContent = group ? group.distributorName : 'Select a distributor';
    title.title = title.textContent;
  }
  if (!picker) return;
  if (!group || !group.runs.length) {
    picker.innerHTML = '<option value="">No matches</option>';
    return;
  }
  picker.innerHTML = group.runs.map((r) => {
    const label = [
      r.category,
      r.season,
      r.fo_source_filename,
      `${Number(r.match_count || 0) + Number(r.fuzzy_count || 0)} ok`,
      (r.mismatch_count || r.missing_count || r.extra_count)
        ? `${Number(r.mismatch_count || 0) + Number(r.missing_count || 0) + Number(r.extra_count || 0)} issues`
        : null,
      String(r.created_at || '').slice(0, 16),
    ].filter(Boolean).join(' · ');
    const selected = Number(orderMatchState.selectedRunId) === Number(r.id) ? ' selected' : '';
    return `<option value="${Number(r.id)}"${selected}>${foEscapeText(label)}</option>`;
  }).join('');
}

function renderOrderMatchStats(run) {
  const meta = document.getElementById('of-match-detail-meta');
  if (!meta) return;
  if (!run) {
    meta.textContent = 'Select a distributor from the left.';
    return;
  }
  const chips = [
    ['of-saved-chip', run.category || '—'],
    ['of-saved-chip', run.season || '—'],
    ['of-saved-chip', `${Number(run.match_count || 0) + Number(run.fuzzy_count || 0)} match`],
    ['of-saved-chip', `${run.mismatch_count || 0} mismatch`],
    ['of-saved-chip', `${run.missing_count || 0} missing`],
    ['of-saved-chip', `${run.extra_count || 0} extra`],
    ['of-saved-chip', `FO ${formatFilledOrderQty(run.fo_qty)} pcs`],
    ['of-saved-chip', `SO ${formatFilledOrderQty(run.so_qty)} pcs`],
    ['of-saved-chip of-saved-chip--accent', `Δ qty ${run.delta_qty ?? 0}`],
    ['of-saved-chip of-saved-chip--accent', `FO ExMill ${formatFilledOrderAmount(run.fo_exmill_value)}`],
    ['of-saved-chip of-saved-chip--accent', `SO Net ${formatFilledOrderAmount(run.so_net_amount)}`],
  ];
  meta.innerHTML = chips.map(([cls, text]) => `<span class="${cls}">${foEscapeText(String(text))}</span>`).join('');
}

function renderOrderMatchDetailRows(rows) {
  const tbody = document.getElementById('of-match-detail-tbody');
  if (!tbody) return;
  const list = rows || [];
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="10">No Brand×Size lines in this match.</td></tr>';
    return;
  }
  const ordered = [...list].sort((a, b) => {
    const rank = {
      MISSING_ON_SO: 0, EXTRA_ON_SO: 1, QTY_MISMATCH: 2, VALUE_MISMATCH: 3,
      MATCH_FUZZY_BRAND: 4, MATCH: 5,
    };
    return (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
      || String(a.brand || '').localeCompare(String(b.brand || ''))
      || String(a.size || '').localeCompare(String(b.size || ''));
  });
  tbody.innerHTML = ordered.map((r) => {
    const status = r.status || '';
    const ok = status === 'MATCH' || status === 'MATCH_FUZZY_BRAND';
    const statusWord = status === 'MATCH_FUZZY_BRAND' ? 'Fuzzy' : (ok ? 'Match' : status);
    const soNums = Array.isArray(r.so_numbers)
      ? r.so_numbers.map((n) => String(n || '').trim()).filter(Boolean)
      : [];
    const soLabel = soNums.length
      ? soNums.map((n) => (/^so\b/i.test(n) ? n : `SO ${n}`)).join(', ')
      : '—';
    const statusHtml = ok
      ? `<span class="of-match-status of-match-status--ok" title="${foEscapeText(status)}">✓ ${foEscapeText(statusWord)}</span>`
      : `<span class="of-match-status of-match-status--flag" title="${foEscapeText(status)}">${foEscapeText(statusWord || '—')}</span>`;
    return `<tr>
      <td>${foEscapeText(r.brand || '')}</td>
      <td>${foEscapeText(r.size || '')}</td>
      <td>${r.fo_qty ?? ''}</td>
      <td>${r.so_qty ?? ''}</td>
      <td>${r.delta_qty ?? ''}</td>
      <td>${formatFilledOrderAmount(r.fo_exmill_value)}</td>
      <td>${formatFilledOrderAmount(r.so_net_amount)}</td>
      <td>${formatFilledOrderAmount(r.delta_value)}</td>
      <td class="of-match-col-status">${statusHtml}</td>
      <td class="of-match-col-so">${foEscapeText(soLabel)}</td>
    </tr>`;
  }).join('');
}

async function selectOrderMatchDistributor(key) {
  orderMatchState.selectedDistributorKey = key;
  orderMatchState.expandedDistributorKeys[key] = true;
  const _groupForExpand = (orderMatchState.grouped || []).find((g) => g.key === key);
  const _latestRun = _groupForExpand?.latest || _groupForExpand?.runs?.[0];
  if (_latestRun) {
    orderMatchState.expandedSeasonKeys[`${key}||${orderDeskSeasonLabel(_latestRun.season)}`] = true;
  }
  renderOrderMatchDistributorRail();
  const group = (orderMatchState.grouped || []).find((g) => g.key === key);
  const firstId = group && group.runs[0] ? group.runs[0].id : null;
  renderOrderMatchRunPicker();
  if (firstId) await loadOrderMatchRunDetail(firstId);
  else {
    orderMatchState.selectedRunId = null;
    orderMatchState.detail = null;
    renderOrderMatchStats(null);
    renderOrderMatchDetailRows([]);
  }
}

async function onOrderMatchRunPickChanged(value) {
  const id = Number(value);
  if (!id) return;
  await selectOrderMatchRun(id);
}

async function selectOrderMatchRun(runId) {
  const id = Number(runId);
  if (!id) return;
  const group = (orderMatchState.grouped || []).find((g) =>
    (g.runs || []).some((r) => Number(r.id) === id)
  );
  if (group) {
    orderMatchState.selectedDistributorKey = group.key;
    orderMatchState.expandedDistributorKeys[group.key] = true;
    const run = (group.runs || []).find((r) => Number(r.id) === id);
    orderMatchState.expandedSeasonKeys[`${group.key}||${orderDeskSeasonLabel(run && run.season)}`] = true;
  }
  renderOrderMatchDistributorRail();
  await loadOrderMatchRunDetail(id);
}

async function loadOrderMatchRunDetail(runId) {
  const tbody = document.getElementById('of-match-detail-tbody');
  const meta = document.getElementById('of-match-detail-meta');
  if (meta) meta.textContent = 'Loading match…';
  if (tbody) tbody.innerHTML = '<tr><td colspan="10">Loading…</td></tr>';
  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/order-match/${runId}`);
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Unable to load match');
    }
    const run = (data.data && data.data.run) || null;
    orderMatchState.selectedRunId = runId;
    orderMatchState.detail = run;
    renderOrderMatchRunPicker();
    renderOrderMatchStats(run);
    renderOrderMatchDetailRows((run && run.rows) || []);
  } catch (err) {
    if (meta) meta.textContent = err.message || 'Load failed';
    if (tbody) tbody.innerHTML = `<tr><td colspan="10">${foEscapeText(err.message || 'Load failed')}</td></tr>`;
  }
}

async function openOrderMatchWorkspace(focusRunId) {
  const tbody = document.getElementById('of-match-detail-tbody');
  const meta = document.getElementById('of-match-detail-meta');
  if (meta) meta.textContent = 'Loading matches…';
  if (tbody) tbody.innerHTML = '<tr><td colspan="10">Loading matches…</td></tr>';

  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/order-match/list');
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Unable to load matches');
    }
    orderMatchState.runs = (data.data && data.data.runs) || [];
  } catch (err) {
    orderMatchState.runs = [];
    if (meta) meta.textContent = err.message || 'Unable to load matches';
  }

  orderMatchState.grouped = _buildOrderMatchGroups();
  setOfRailMode('match');
  showOfSection('order-match');
  renderOrderMatchDistributorRail();

  let focusGroupKey = '';
  if (focusRunId) {
    const run = orderMatchState.runs.find((r) => Number(r.id) === Number(focusRunId));
    if (run) focusGroupKey = _orderMatchGroupKey(run);
  }
  const firstKey = focusGroupKey || (orderMatchState.grouped[0] && orderMatchState.grouped[0].key) || '';
  if (firstKey) {
    orderMatchState.selectedDistributorKey = firstKey;
    renderOrderMatchDistributorRail();
    renderOrderMatchRunPicker();
    const group = orderMatchState.grouped.find((g) => g.key === firstKey);
    const runId = focusRunId
      || (group && group.runs[0] && group.runs[0].id)
      || null;
    if (runId) await loadOrderMatchRunDetail(runId);
  } else {
    orderMatchState.selectedDistributorKey = '';
    orderMatchState.selectedRunId = null;
    renderOrderMatchRunPicker();
    if (meta) meta.textContent = 'No matches yet. On SO Pack, pick a Filled Order and click Match FO.';
    if (tbody) tbody.innerHTML = '<tr><td colspan="10">No matches yet.</td></tr>';
  }
}

function exitOrderMatchWorkspace() {
  setOfRailMode('default');
  showOfSection('so-pack');
}

async function deleteOrderMatchSelectedRun() {
  const runId = orderMatchState.selectedRunId;
  if (!runId) {
    await showSimpleConfirmModal('No match selected', 'Select a match first.', 'OK', 'Close');
    return;
  }
  const ok = await showSimpleConfirmModal(
    'Delete match result?',
    'This removes the saved FO vs SO match snapshot. You can run Match FO again anytime.',
    'Delete',
    'Cancel',
  );
  if (!ok) return;
  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/order-match/${runId}`, { method: 'DELETE' });
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Delete failed');
    }
    if (typeof nexoraToast === 'function') nexoraToast('Match deleted', 'success');
    await openOrderMatchWorkspace();
  } catch (err) {
    await showSimpleConfirmModal('Delete failed', err.message || 'Could not delete.', 'OK', 'Close');
  }
}

const SO_PACK_ANALYZE_TIPS = [
  'Unpacking ZIP / RAR archive…',
  'Reading Bombay Dyeing SO PDFs…',
  'Matching product names & sizes…',
  'Rolling up qty, net & GST…',
  'Building Consolidated view…',
];

const SO_PACK_EXCEL_TIPS = [
  'Preparing workbook sheets…',
  'Writing Consolidated rows…',
  'Adding SO Summary…',
  'Filling Line Item Detail…',
  'Almost ready to download…',
];

let _soPackBusyTimer = null;
let _soPackBusyTipIdx = 0;
let _soPackBusyInFlight = false;
/** When true, tip line is driven by live server progress (no fake rotation). */
let _soPackBusyLiveTips = false;

function _soPackEnsureBusyModal() {
  let modal = document.getElementById('of-so-pack-busy-modal');
  if (modal) return modal;
  const host = document.getElementById('order-fulfillment-workspace') || document.body;
  modal = document.createElement('div');
  modal.id = 'of-so-pack-busy-modal';
  modal.className = 'so-pack-modal hidden';
  modal.setAttribute('aria-hidden', 'true');
  modal.innerHTML = `
    <div class="so-pack-modal__backdrop"></div>
    <div class="so-pack-modal__card" role="dialog" aria-modal="true" aria-labelledby="of-so-pack-busy-title">
      <div class="so-pack-modal__glow" aria-hidden="true"></div>
      <div class="so-pack-modal__orbit" aria-hidden="true">
        <span class="so-pack-modal__ring"></span>
        <span class="so-pack-modal__ring so-pack-modal__ring--2"></span>
        <span class="so-pack-modal__core"></span>
      </div>
      <p class="so-pack-modal__eyebrow" id="of-so-pack-busy-eyebrow">SO Pack</p>
      <h3 class="so-pack-modal__title" id="of-so-pack-busy-title">Analyzing SO pack</h3>
      <p class="so-pack-modal__tip" id="of-so-pack-busy-tip">Unpacking ZIP / RAR archive…</p>
      <div class="so-pack-modal__bar" aria-hidden="true"><i></i></div>
      <p class="so-pack-modal__hint">Please wait — this may take a moment for large packs</p>
    </div>`;
  host.appendChild(modal);
  return modal;
}

function _soPackSetBusyButtons(disabled) {
  const root = document.getElementById('order-fulfillment-workspace');
  if (!root) return;
  root.querySelectorAll('#of-so-pack-file, button[onclick="analyzeSoPack()"]')
    .forEach((el) => {
      el.disabled = !!disabled;
    });
  updateSoPackExcelButtonState();
  if (disabled) {
    const excelBtn = document.getElementById('of-so-pack-excel-btn');
    if (excelBtn) excelBtn.disabled = true;
  }
}

function _soPackClearBusy() {
  if (_soPackBusyTimer) {
    clearInterval(_soPackBusyTimer);
    _soPackBusyTimer = null;
  }
  _soPackBusyInFlight = false;
  _soPackBusyLiveTips = false;
  _soPackSetBusyButtons(false);
  const modal = document.getElementById('of-so-pack-busy-modal');
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('so-pack-modal--excel', 'is-open');
    modal.setAttribute('aria-hidden', 'true');
  }
}

function _soPackSetBusyTip(text) {
  const tip = document.getElementById('of-so-pack-busy-tip');
  if (!tip || text == null || text === '') return;
  tip.classList.remove('is-fading');
  tip.textContent = String(text);
}

function _soPackShowBusy(mode, progressText) {
  const modal = _soPackEnsureBusyModal();
  const resultBox = document.getElementById('of-so-pack-result');
  _soPackClearBusy();
  _soPackBusyInFlight = true;
  _soPackSetBusyButtons(true);
  _soPackBusyTipIdx = 0;
  const isExcel = mode === 'excel';
  // Analyze tip = live server messages; Excel still uses rotating tips.
  _soPackBusyLiveTips = !isExcel;
  const tips = isExcel ? SO_PACK_EXCEL_TIPS : SO_PACK_ANALYZE_TIPS;
  const titleEl = document.getElementById('of-so-pack-busy-title');
  const tipEl = document.getElementById('of-so-pack-busy-tip');
  const eyeEl = document.getElementById('of-so-pack-busy-eyebrow');
  if (titleEl) {
    titleEl.textContent = isExcel
      ? (progressText || 'Building Excel workbook')
      : (progressText || 'Analyzing SO pack');
  }
  if (eyeEl) eyeEl.textContent = isExcel ? 'Download Excel' : 'SO Pack Consolidate';
  if (tipEl) {
    tipEl.textContent = isExcel ? tips[0] : 'Starting analysis…';
    tipEl.classList.remove('is-fading');
  }
  if (resultBox) {
    resultBox.classList.remove('so-pack-busy', 'so-pack-ok');
    resultBox.textContent = '';
  }
  // Mount on body so fixed overlay centers on full viewport
  if (modal.parentElement !== document.body) {
    document.body.appendChild(modal);
  }
  modal.classList.toggle('so-pack-modal--excel', isExcel);
  modal.classList.remove('hidden');
  modal.classList.add('is-open');
  modal.setAttribute('aria-hidden', 'false');

  if (isExcel) {
    _soPackBusyTimer = setInterval(() => {
      const tip = document.getElementById('of-so-pack-busy-tip');
      if (!tip || !_soPackBusyInFlight || _soPackBusyLiveTips) return;
      tip.classList.add('is-fading');
      setTimeout(() => {
        if (!_soPackBusyInFlight || _soPackBusyLiveTips) return;
        _soPackBusyTipIdx = (_soPackBusyTipIdx + 1) % tips.length;
        tip.textContent = tips[_soPackBusyTipIdx];
        tip.classList.remove('is-fading');
      }, 220);
    }, 2200);
  }
}

function _soPackSetBusyProgress(titleText) {
  const titleEl = document.getElementById('of-so-pack-busy-title');
  if (titleEl && titleText) titleEl.textContent = titleText;
}

function _soPackShowMessage(text, ok) {
  const resultBox = document.getElementById('of-so-pack-result');
  _soPackClearBusy();
  if (!resultBox) return;
  resultBox.classList.toggle('so-pack-ok', !!ok);
  resultBox.classList.remove('so-pack-busy');
  resultBox.textContent = text || '';
}

function showOfSection(section) {
  const meta = {
    'article-master': {
      title: 'Article Master',
      sub: 'Catalog of brands, sizes, rates and bale packs — source of truth for matching filled orders.',
    },
    'filled-order': {
      title: 'Filled Order',
      sub: 'Upload distributor Excel. Qty is the source of truth; bale mismatches are highlighted for correction.',
    },
    'so-pack': {
      title: 'SO Pack',
      sub: 'Analyze ZIP/RAR/PDF packs, pick a saved Filled Order, Match FO — results open on Order Match.',
    },
    'order-match': {
      title: 'Order Match',
      sub: 'FO vs SO Pack Brand × Size results — same layout as Saved Orders.',
    },
    'invoice-ci': {
      title: 'Invoice (CI)',
      sub: 'Upload commercial invoices and link them in the SO & CI tracking board.',
    },
    'saved-orders': {
      title: 'Saved Orders',
      sub: '',
    },
  };
  // Legacy single-SO PDF step removed — SO Pack is the SO engine.
  let key = section === 'sales-order' || section === 'match-lab' ? 'so-pack' : section;
  key = meta[key] ? key : 'filled-order';
  const info = meta[key];
  const isSaved = key === 'saved-orders';
  const isMatch = key === 'order-match';

  if (!isSaved && !isMatch) {
    setOfRailMode('default');
  }

  document.querySelectorAll('#order-fulfillment-workspace .of-rail-item').forEach((btn) => {
    btn.classList.toggle('is-active', btn.getAttribute('data-of-section') === key);
  });

  // Hide regular panes while Saved Orders / Order Match workspace is open.
  document.querySelectorAll('#order-fulfillment-workspace .of-pane').forEach((pane) => {
    const on = !isSaved && !isMatch && pane.getAttribute('data-of-pane') === key;
    pane.classList.toggle('is-active', on);
    if (on) {
      pane.removeAttribute('hidden');
      pane.style.display = 'flex';
    } else {
      pane.setAttribute('hidden', '');
      pane.style.display = 'none';
    }
  });

  const savedWs = document.getElementById('of-saved-workspace');
  const matchWs = document.getElementById('of-match-workspace');
  const ofScreen = document.querySelector('#order-fulfillment-workspace .nx-of-vyapar');
  if (ofScreen) ofScreen.classList.toggle('is-saved-orders', isSaved || isMatch);
  if (savedWs) {
    if (isSaved) {
      savedWs.removeAttribute('hidden');
      savedWs.classList.add('is-open');
      savedWs.style.display = 'flex';
    } else {
      savedWs.setAttribute('hidden', '');
      savedWs.classList.remove('is-open');
      savedWs.style.display = 'none';
    }
  }
  if (matchWs) {
    if (isMatch) {
      matchWs.removeAttribute('hidden');
      matchWs.classList.add('is-open');
      matchWs.style.display = 'flex';
    } else {
      matchWs.setAttribute('hidden', '');
      matchWs.classList.remove('is-open');
      matchWs.style.display = 'none';
    }
  }

  const titleEl = document.getElementById('of-stage-title');
  const subEl = document.getElementById('of-stage-sub');
  if (titleEl) titleEl.textContent = info.title;
  if (subEl) {
    subEl.textContent = info.sub || '';
    subEl.style.display = info.sub ? '' : 'none';
  }

  if (key === 'invoice-ci') {
    loadOrderFulfillmentUploads();
  }
  if (key === 'so-pack' && ofSoPackLastPayload) {
    const preview = document.getElementById('of-so-pack-preview');
    if (preview) preview.classList.remove('hidden');
  }
}

function setOfRailMode(mode) {
  const defaultNav = document.getElementById('of-rail-default-nav');
  const savedNav = document.getElementById('of-rail-saved-nav');
  const matchNav = document.getElementById('of-rail-match-nav');
  const titleEl = document.getElementById('of-rail-title');
  const kickerEl = document.getElementById('of-rail-kicker');
  const isSaved = mode === 'saved';
  const isMatch = mode === 'match';
  if (defaultNav) defaultNav.classList.toggle('hidden', isSaved || isMatch);
  if (savedNav) savedNav.classList.toggle('hidden', !isSaved);
  if (matchNav) matchNav.classList.toggle('hidden', !isMatch);
  if (titleEl) {
    titleEl.textContent = isSaved ? 'Saved Orders' : (isMatch ? 'Order Match' : 'Fulfillment');
  }
  if (kickerEl) kickerEl.textContent = 'Order Desk';
}

function onOfRailBack() {
  const savedWs = document.getElementById('of-saved-workspace');
  if (savedWs && savedWs.classList.contains('is-open')) {
    exitOfSavedOrdersWorkspace();
    return;
  }
  const matchWs = document.getElementById('of-match-workspace');
  if (matchWs && matchWs.classList.contains('is-open')) {
    exitOrderMatchWorkspace();
    return;
  }
  goBack();
}

function showOfBottomPanel(panel) {
  // Legacy bottom tabs removed — map to Vyapar-style sections.
  if (panel === 'pack') {
    if (!ofSoPackLastPayload) {
      if (typeof nexoraToast === 'function') nexoraToast('Analyze a SO pack first', 'error');
      return;
    }
    showOfSection('so-pack');
    const preview = document.getElementById('of-so-pack-preview');
    if (preview) preview.classList.remove('hidden');
    return;
  }
  // Old single-SO panel → SO Pack (current SO engine).
  showOfSection('so-pack');
}

function _renderSoPackPreview(data, fileKey, opts) {
  // Re-analyze invalidates any previous workbook cache for this session.
  if (!(opts && opts.skipBatchPicker)) {
    _soPackClearExcelCache();
  }
  ofSoPackLastPayload = data;
  ofSoPackFoMatchResult = null;
  _clearSoPackFoMatchUi();
  if (fileKey) ofSoPackAnalyzedKey = fileKey;
  const preview = document.getElementById('of-so-pack-preview');
  const kpis = document.getElementById('of-so-pack-kpis');
  const statusEl = document.getElementById('of-so-pack-status');
  const meta = data.meta || {};
  if (preview) preview.classList.remove('hidden');
  if (kpis) {
    kpis.innerHTML = [
      ['PDFs', meta.pdf_count],
      ['SOs', meta.so_count],
      ['Products', meta.consolidated_rows],
      ['Qty', meta.total_qty],
      ['Net', _soPackMoney(meta.net_amount)],
      ['GST', _soPackMoney(meta.gst_amount)],
      ['Total', _soPackMoney(meta.total_amount)],
    ].map(([label, val]) => {
      const text = String(val ?? '—');
      return `<div class="of-kpi-cell" title="${foEscapeText(text)}">
        <span>${foEscapeText(String(label))}</span>
        <strong>${foEscapeText(text)}</strong>
      </div>`;
    }).join('');
  }
  const errs = meta.errors || [];
  const buyer = _soPackBuyerLabel(data);
  const fullMsg = errs.length
    ? `${buyer}: Parsed ${meta.so_count || 0} SO(s). ${errs.length} PDF(s) had issues.`
    : `${buyer}: ${meta.pdf_count || 0} PDF(s) → ${meta.so_count || 0} SO(s) → ${meta.consolidated_rows || 0} product rows · Qty ${meta.total_qty ?? '—'} · Total ₹ ${_soPackMoney(meta.total_amount)}`;
  if (statusEl) statusEl.textContent = fullMsg;
  if (!(opts && opts.skipMessage)) {
    const okN = _soPackOkPacks().length;
    const failN = (ofSoPackBatch || []).filter((p) => p && p.error).length;
    if (okN > 1 || failN) {
      _soPackShowMessage(
        `Ready · ${okN} pack(s)${failN ? ` · ${failN} failed` : ''} · viewing ${buyer}`,
        failN === 0,
      );
    } else {
      _soPackShowMessage(
        `Ready · ${meta.so_count || 0} SO · ${meta.consolidated_rows || 0} products`,
        !errs.length,
      );
    }
  }
  const resultBox = document.getElementById('of-so-pack-result');
  if (resultBox) resultBox.title = fullMsg;
  if (!(opts && opts.skipBatchPicker)) {
    _renderSoPackBatchPicker();
  }
  showOfBottomPanel('pack');
  showSoPackTab(ofSoPackActiveTab || 'consolidated');
  updateSoPackExcelButtonState();
  _loadSoPackFilledOrderOptions();
}

async function _soPackRunAnalyze(fileOrFiles) {
  const files = Array.isArray(fileOrFiles) ? fileOrFiles : [fileOrFiles];
  const formData = new FormData();
  for (const f of files) formData.append('file', f);

  // Prefer live progress stream (real PDF/SO status on the tip line).
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/analyze-stream', {
      method: 'POST',
      body: formData,
      headers: { Accept: 'application/x-ndjson, application/json' },
    });
    if (response.ok && response.body && (response.headers.get('Content-Type') || '').includes('ndjson')) {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let payload = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n');
        buffer = parts.pop() || '';
        for (const line of parts) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          let evt;
          try {
            evt = JSON.parse(trimmed);
          } catch (_) {
            continue;
          }
          if (evt.type === 'progress' && evt.message) {
            _soPackSetBusyTip(evt.message);
          } else if (evt.type === 'done') {
            payload = evt.data || null;
          } else if (evt.type === 'error') {
            throw new Error(evt.message || 'Analyze failed');
          }
        }
      }
      if (buffer.trim()) {
        try {
          const evt = JSON.parse(buffer.trim());
          if (evt.type === 'done') payload = evt.data || payload;
          if (evt.type === 'error') throw new Error(evt.message || 'Analyze failed');
          if (evt.type === 'progress' && evt.message) _soPackSetBusyTip(evt.message);
        } catch (e) {
          if (e && e.message && !String(e.message).includes('JSON')) throw e;
        }
      }
      if (!payload) throw new Error('Analyze stream ended without result');
      return payload;
    }
    // Non-stream JSON error from stream route
    if (!response.ok) {
      const data = await parseApiJson(response);
      throw new Error((data.error && data.error.message) || 'Analyze failed');
    }
  } catch (streamErr) {
    // Fall back to classic analyze if stream unsupported / network oddity
    if (streamErr && streamErr.message && /stream ended|Analyze failed|No PDF|Empty file|Upload a|Upload ZIP|Multiple files/i.test(streamErr.message)) {
      throw streamErr;
    }
  }

  const formData2 = new FormData();
  for (const f of files) formData2.append('file', f);
  _soPackSetBusyTip('Analyzing pack (fallback)…');
  const response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/analyze', {
    method: 'POST',
    body: formData2,
  });
  const data = await parseApiJson(response);
  if (!response.ok || !data.success) {
    throw new Error((data.error && data.error.message) || 'Analyze failed');
  }
  return data.data || {};
}

async function analyzeSoPack() {
  bindSoPackFileInput();
  if (_soPackBusyInFlight) return;
  const files = _soPackSelectedFiles();
  if (!files.length) {
    _soPackShowMessage('Please choose ZIP, RAR, or PDF file(s) first.', false);
    return;
  }
  const built = _soPackBuildJobs(files);
  if (built.other.length) {
    _soPackShowMessage('Only ZIP, RAR, or PDF files are allowed.', false);
    return;
  }
  const jobs = built.jobs;
  if (!jobs.length) {
    _soPackShowMessage('Please choose ZIP, RAR, or PDF file(s) first.', false);
    return;
  }
  const total = jobs.length;
  _soPackShowBusy('analyze', total > 1 ? `Analyzing pack 1 of ${total}` : 'Analyzing SO pack');
  ofSoPackBatch = [];
  ofSoPackBatchSelectionKey = null;
  _soPackClearExcelCache();
  let firstOk = null;
  try {
    for (let i = 0; i < jobs.length; i++) {
      const job = jobs[i];
      _soPackSetBusyProgress(
        total > 1
          ? `Analyzing ${i + 1} of ${total}: ${job.filename}`
          : 'Analyzing SO pack',
      );
      try {
        const payload = await _soPackRunAnalyze(job.files);
        const item = { key: job.key, filename: job.filename, payload };
        ofSoPackBatch.push(item);
        if (!firstOk) firstOk = item;
      } catch (err) {
        ofSoPackBatch.push({
          key: job.key,
          filename: job.filename,
          error: err.message || 'Analyze failed',
        });
      }
    }
    ofSoPackBatchSelectionKey = _soPackSelectionKey(files);
    if (!firstOk) {
      const msgs = ofSoPackBatch.map((p) => `${p.filename}: ${p.error || 'failed'}`).join(' · ');
      throw new Error(msgs || 'Analyze failed');
    }
    _renderSoPackPreview(firstOk.payload, firstOk.key);
    const okN = _soPackOkPacks().length;
    const failN = ofSoPackBatch.filter((p) => p.error).length;
    if (typeof nexoraToast === 'function') {
      nexoraToast(
        failN
          ? `Analyzed ${okN}/${total} packs (${failN} failed)`
          : (total > 1 ? `Analyzed ${okN} packs` : 'SO pack analyzed'),
        failN ? 'warn' : 'ok',
      );
    }
  } catch (e) {
    _soPackShowMessage(e.message || 'Analyze failed', false);
    if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Analyze failed', 'error');
  }
}

async function downloadSoPackExcel() {
  bindSoPackFileInput();
  if (_soPackBusyInFlight) return;
  if (!_soPackBatchReadyForSelection()) {
    const msg = 'First click Analyze, then Excel Download.';
    _soPackShowMessage(msg, false);
    if (typeof nexoraToast === 'function') nexoraToast(msg, 'warn');
    return;
  }
  const okPacks = _soPackOkPacks();
  const selKey = ofSoPackBatchSelectionKey;

  // Multi-pack → one ZIP of separate Excels
  if (okPacks.length > 1) {
    const excelCacheKey = _soPackExcelCacheKey(selKey);
    if (ofSoPackBatchExcelBlob && ofSoPackBatchExcelKey === excelCacheKey) {
      _soPackTriggerExcelDownload(ofSoPackBatchExcelBlob, `SO_Pack_Batch_${okPacks.length}.zip`);
      _soPackShowMessage(`ZIP of ${okPacks.length} Excels downloaded (from memory).`, true);
      if (typeof nexoraToast === 'function') nexoraToast('Batch Excel ZIP downloaded', 'ok');
      return;
    }
    _soPackClearExcelCache();
    _soPackShowBusy('excel', `Building ${okPacks.length} Excel files…`);
    try {
      const response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/excel-batch', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/zip, application/json, */*',
        },
        body: JSON.stringify({ packs: okPacks.map((p) => p.payload) }),
      });
      if (!response.ok) {
        let msg = 'Batch Excel download failed';
        try {
          const data = await parseApiJson(response);
          msg = (data.error && data.error.message) || msg;
        } catch (_) {}
        throw new Error(msg);
      }
      const blob = await response.blob();
      if (blob.type && blob.type.indexOf('json') !== -1 && blob.size < 4096) {
        const text = await blob.text();
        let msg = 'Batch Excel download failed';
        try {
          const data = JSON.parse(text);
          msg = (data.error && data.error.message) || msg;
        } catch (_) {
          msg = text || msg;
        }
        throw new Error(msg);
      }
      let filename = `SO_Pack_Batch_${okPacks.length}.zip`;
      const cd = response.headers.get('Content-Disposition') || '';
      const m = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(cd);
      if (m) {
        try {
          filename = decodeURIComponent(m[1].replace(/"/g, '').trim());
        } catch (_) {
          filename = m[1].replace(/"/g, '').trim() || filename;
        }
      }
      ofSoPackBatchExcelBlob = blob;
      ofSoPackBatchExcelKey = excelCacheKey;
      _soPackTriggerExcelDownload(blob, filename);
      _soPackShowMessage(`ZIP of ${okPacks.length} Excels downloaded.`, true);
      updateSoPackExcelButtonState();
      if (typeof nexoraToast === 'function') nexoraToast(`Downloaded ${okPacks.length} Excels in ZIP`, 'ok');
    } catch (e) {
      _soPackShowMessage(e.message || 'Batch Excel download failed', false);
      if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Batch Excel download failed', 'error');
    }
    return;
  }

  // Single pack — existing path
  const only = okPacks[0];
  const packKey = only.key;
  const excelCacheKey = _soPackExcelCacheKey(packKey);
  if (ofSoPackExcelBlob && ofSoPackExcelKey === excelCacheKey) {
    _soPackTriggerExcelDownload(ofSoPackExcelBlob, _soPackExcelDownloadName(only.payload));
    _soPackShowMessage('Excel downloaded (from memory).', true);
    if (typeof nexoraToast === 'function') nexoraToast('Excel downloaded', 'ok');
    return;
  }
  _soPackClearExcelCache();
  _soPackShowBusy('excel');
  try {
    let response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/excel', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/json, */*',
      },
      body: JSON.stringify(only.payload),
    });
    if (!response || !response.ok) {
      const file = _soPackSelectedFiles().find((f) => _soPackFileKey(f) === packKey);
      if (file) {
        const formData = new FormData();
        formData.append('file', file);
        response = await fetchWithAuth('/api/v1/order-fulfillment/so-pack/excel', {
          method: 'POST',
          body: formData,
        });
      }
    }
    if (!response || !response.ok) {
      let msg = 'Excel download failed';
      if (response) {
        try {
          const data = await parseApiJson(response);
          msg = (data.error && data.error.message) || msg;
        } catch (_) {}
      }
      throw new Error(msg);
    }
    const blob = await response.blob();
    if (blob.type && blob.type.indexOf('json') !== -1 && blob.size < 2048) {
      const text = await blob.text();
      let msg = 'Excel download failed';
      try {
        const data = JSON.parse(text);
        msg = (data.error && data.error.message) || msg;
      } catch (_) {
        msg = text || msg;
      }
      throw new Error(msg);
    }
    ofSoPackExcelBlob = blob;
    ofSoPackExcelKey = excelCacheKey;
    _soPackTriggerExcelDownload(blob, _soPackExcelDownloadName(only.payload));
    _soPackShowMessage('Excel downloaded.', true);
    updateSoPackExcelButtonState();
    if (typeof nexoraToast === 'function') nexoraToast('Excel downloaded', 'ok');
  } catch (e) {
    _soPackShowMessage(e.message || 'Excel download failed', false);
    if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Excel download failed', 'error');
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

function _ofMoney(v) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—';
  return `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function _ofQty(v) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

async function uploadInvoiceV2() {
  const fileInput = document.getElementById('of-invoice-file');

  if (!fileInput.files.length) {
    _showOfInvoiceResult('Please choose a file first.');
    return;
  }

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  _showOfInvoiceResult('Uploading and parsing...');
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/upload/invoice', {
      method: 'POST',
      body: formData,
    });
    const rawText = await response.text();
    let data;
    try {
      data = JSON.parse(rawText);
    } catch (parseErr) {
      const snippet = (rawText || '').replace(/\s+/g, ' ').slice(0, 160);
      throw new Error(
        `Server returned HTML/non-JSON (HTTP ${response.status}). `
        + (snippet ? `Start: ${snippet}` : 'Empty body — deploy may still be restarting; retry in 1–2 min.')
      );
    }
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Upload failed');
    }
    const d = data.data;

    if (d.is_duplicate) {
      _showOfInvoiceResult(`<div class="of-ci-title is-bad">${escapeHtml(d.message || d.link_error || 'Duplicate CI — already processed.')}</div>`);
      ofInvoicePendingLink = null;
      return;
    }

    ofInvoicePendingLink = {
      order_ref_no: d.order_ref_no,
      invoice_no: d.invoice_no,
      buyer_name: d.buyer_name,
      buyer_gst: d.buyer_gst || null,
      commercial_invoice_file_reference: d.commercial_invoice_file_reference,
      commercial_invoice_parsed: d.commercial_invoice_parsed,
      suggested_distributor: d.suggested_distributor || null,
      party_match: d.party_match || null,
      mode: d.no_match_found ? 'ci_only' : 'linked',
    };

    const amountValue = d.extracted_amount != null ? d.extracted_amount : '';
    const amountNote = d.extracted_amount != null
      ? '(auto-extracted from the invoice — adjust if needed)'
      : '(could not auto-read the amount — please enter it)';
    const partyHtml = _ofPartyMatchHtml(d.party_match, d.buyer_name, d.buyer_gst);

    // Lane B: SO exists → show light compare + confirm link
    if (!d.no_match_found && d.compare) {
      const c = d.compare;
      const qtyWarn = c.qty_mismatch
        ? `<div class="of-ci-title is-warn" style="margin-top:6px;">Qty mismatch: SO ${_ofQty(c.so_total_qty)} vs CI ${_ofQty(c.ci_total_qty)}</div>`
        : '';
      const partyName = d.distributor_name || c.so_distributor || 'matched party';
      const mismatch = (d.party_match && d.party_match.status === 'mismatch');
      const mismatchGate = mismatch
        ? `<label class="of-ci-ack">
            <input type="checkbox" id="of-ci-party-mismatch-ack" />
            <span>I confirm CI buyer and SO / Customers distributor are the same party despite the mismatch warning.</span>
          </label>`
        : '';
      _showOfInvoiceResult(`
        <div class="of-ci-title is-ok">SO found in Nexora — review compare, then confirm</div>
        ${partyHtml}
        <table class="data-table" style="margin:10px 0;max-width:36rem;">
          <tbody>
            <tr><td>SO / Order Ref</td><td><strong>${escapeHtml(c.order_ref_no || '—')}</strong></td></tr>
            <tr><td>CI Invoice No</td><td>${escapeHtml(c.invoice_no || '—')}</td></tr>
            <tr><td>SO party (Customers)</td><td>${escapeHtml(c.so_distributor || '—')}</td></tr>
            <tr><td>CI buyer</td><td>${escapeHtml(c.ci_buyer_name || '—')}</td></tr>
            <tr><td>CI buyer GST</td><td>${escapeHtml(c.ci_buyer_gst || '—')}</td></tr>
            <tr><td>SO qty / value</td><td>${_ofQty(c.so_total_qty)} · ${_ofMoney(c.so_total_value)}</td></tr>
            <tr><td>CI qty / value</td><td>${c.ci_total_qty == null ? 'on confirm' : _ofQty(c.ci_total_qty)} · ${_ofMoney(c.ci_total_value || c.ci_amount)}</td></tr>
            <tr><td>SO lines / CI lines</td><td>${c.so_item_count ?? '—'} / ${c.ci_line_count == null ? 'on confirm' : (c.ci_line_count ?? '—')}</td></tr>
          </tbody>
        </table>
        ${c.detail_note ? `<div class="of-ci-note">${escapeHtml(c.detail_note)}</div>` : ''}
        ${qtyWarn}
        ${mismatchGate}
        <div class="form-group">
          <label>Invoice Amount (₹) <span>${amountNote}</span></label>
          <input type="number" id="of-invoice-amount" step="0.01" value="${amountValue}" />
        </div>
        <button class="btn btn-primary" onclick="confirmCiLinkV2()">Confirm — link CI to ${escapeHtml(partyName)}</button>
      `);
      return;
    }

    // Lane A: no SO → CI-only confirm with distributor pick
    const suggestedId = (d.suggested_distributor && d.suggested_distributor.id) || '';
    const suggestedName = (d.suggested_distributor && d.suggested_distributor.name) || '';
    const buyerLabel = d.buyer_name || suggestedName || 'unknown party on PDF';
    _showOfInvoiceResult(`
      <div class="of-ci-title is-warn">No matching Sales Order in Nexora</div>
      ${partyHtml}
      <p style="margin:8px 0;">
        SO / Order Ref on CI: <strong>${escapeHtml(d.order_ref_no || 'not found')}</strong><br/>
        Invoice No: <strong>${escapeHtml(d.invoice_no || '—')}</strong><br/>
        Buyer on CI: <strong>${escapeHtml(buyerLabel)}</strong><br/>
        Buyer GST: <strong>${escapeHtml(d.buyer_gst || '—')}</strong>
      </p>
      <p class="of-ci-note">
        Real sale is CI. Pick the <strong>Customers</strong> distributor that matches this CI buyer, then save as CI-only.
        If the same SO number is uploaded later, it can merge into this tracking.
      </p>
      <div class="form-group">
        <label>Distributor (Customers / Party Master) *</label>
        <select id="of-ci-only-distributor" style="max-width:min(100%,28rem);"></select>
      </div>
      <div class="form-group">
        <label>Invoice Amount (₹) <span>${amountNote}</span></label>
        <input type="number" id="of-invoice-amount" step="0.01" value="${amountValue}" />
      </div>
      <button class="btn btn-primary" onclick="confirmCiOnlyV2()">Confirm — save CI-only (no SO)</button>
    `);
    await _populateCiOnlyDistributorSelect(suggestedId, d.party_match);
  } catch (error) {
    _showOfInvoiceResult(`<div class="of-ci-title is-bad">Error: ${escapeHtml(error.message)}</div>`);
  }
}

function _showOfInvoiceResult(html) {
  const resultBox = document.getElementById('of-invoice-result');
  if (!resultBox) return;
  resultBox.hidden = false;
  resultBox.innerHTML = html || '';
  if (!html) resultBox.hidden = true;
}

function _ofPartyMatchHtml(partyMatch, buyerName, buyerGst) {
  if (!partyMatch) {
    return `<div class="of-ci-party-match is-warn">
      <div class="of-ci-party-match__status">Customers ↔ CI: NOT EVALUATED</div>
    </div>`;
  }
  const status = partyMatch.status || 'unmatched';
  const statusClass = status === 'matched' ? 'is-matched'
    : status === 'mismatch' ? 'is-mismatch'
    : (status === 'ambiguous' || status === 'unmatched') ? 'is-warn'
    : 'is-warn';
  const ciName = (partyMatch.ci_distributor && partyMatch.ci_distributor.name) || '—';
  const soName = (partyMatch.so_distributor && partyMatch.so_distributor.name) || '—';
  let candidates = '';
  if (Array.isArray(partyMatch.candidates) && partyMatch.candidates.length) {
    candidates = `<div class="of-ci-party-match__candidates">Candidates: ${
      partyMatch.candidates.map((c) => escapeHtml(c.name || `#${c.id}`)).join(', ')
    }</div>`;
  }
  return `
    <div class="of-ci-party-match ${statusClass}">
      <div class="of-ci-party-match__status">Customers ↔ CI: ${escapeHtml(status.toUpperCase())}</div>
      <p class="of-ci-party-match__msg">${escapeHtml(partyMatch.message || '')}</p>
      <p class="of-ci-party-match__meta">
        CI buyer: <strong>${escapeHtml(buyerName || '—')}</strong>
        · GST: <strong>${escapeHtml(buyerGst || '—')}</strong><br/>
        Customers match: <strong>${escapeHtml(ciName)}</strong>
        · SO party: <strong>${escapeHtml(soName)}</strong>
      </p>
      ${candidates}
    </div>
  `;
}

async function _populateCiOnlyDistributorSelect(preferredId, partyMatch) {
  const sel = document.getElementById('of-ci-only-distributor');
  if (!sel) return;
  sel.innerHTML = '<option value="">Loading distributors…</option>';
  try {
    const response = await fetchWithAuth('/api/v1/masters/distributors?limit=5000');
    const data = await response.json();
    const list = (data && data.data) || data.distributors || data || [];
    const rows = Array.isArray(list) ? list : (list.items || list.results || []);
    sel.innerHTML = '<option value="">— Select distributor —</option>';
    const preferIds = new Set();
    if (preferredId) preferIds.add(String(preferredId));
    if (partyMatch && Array.isArray(partyMatch.candidates)) {
      partyMatch.candidates.forEach((c) => {
        if (c && c.id != null) preferIds.add(String(c.id));
      });
    }
    // Put suggested / candidate rows first
    const ranked = [...rows].sort((a, b) => {
      const aPref = preferIds.has(String(a.id)) ? 0 : 1;
      const bPref = preferIds.has(String(b.id)) ? 0 : 1;
      return aPref - bPref;
    });
    ranked.forEach((d) => {
      const id = d.id;
      const name = d.firm_name || d.name || `Distributor #${id}`;
      const gst = d.gst_no ? ` · ${d.gst_no}` : '';
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = preferIds.has(String(id)) ? `★ ${name}${gst}` : `${name}${gst}`;
      if (preferredId && String(id) === String(preferredId)) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (e) {
    sel.innerHTML = `<option value="">Failed to load distributors</option>`;
  }
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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

async function confirmCiOnlyV2() {
  if (ofCiConfirmRequestInFlight) return;
  ofCiConfirmRequestInFlight = true;
  try {
    await _confirmCiOnlyV2Impl();
  } finally {
    ofCiConfirmRequestInFlight = false;
  }
}

function _parseFetchJson(response, rawText) {
  try {
    return JSON.parse(rawText);
  } catch (parseErr) {
    const snippet = (rawText || '').replace(/\s+/g, ' ').slice(0, 160);
    throw new Error(
      `Server returned HTML/non-JSON (HTTP ${response.status}). `
      + (snippet ? `Start: ${snippet}` : 'Empty body — service may be restarting (OOM/deploy); retry in 1–2 min.')
    );
  }
}

async function _confirmCiOnlyV2Impl() {
  if (!ofInvoicePendingLink) {
    _showOfInvoiceResult('<div class="of-ci-title is-warn">Nothing pending to confirm.</div>');
    return;
  }
  const distSel = document.getElementById('of-ci-only-distributor');
  const distributorId = distSel ? distSel.value : '';
  if (!distributorId) {
    const box = document.getElementById('of-invoice-result');
    if (box) {
      box.insertAdjacentHTML('beforeend', '<div class="of-ci-title is-bad" style="margin-top:8px;">Select a distributor first.</div>');
    }
    return;
  }
  const suggestedId = ofInvoicePendingLink.suggested_distributor
    && ofInvoicePendingLink.suggested_distributor.id;
  let acknowledgePartyMismatch = false;
  if (suggestedId && String(suggestedId) !== String(distributorId)) {
    acknowledgePartyMismatch = window.confirm(
      'Selected Customers distributor differs from the CI auto-match. Save anyway?'
    );
    if (!acknowledgePartyMismatch) return;
  }
  const amountInput = document.getElementById('of-invoice-amount');
  const amount = amountInput ? parseFloat(amountInput.value) : null;

  try {
    const box = document.getElementById('of-invoice-result');
    if (box) box.insertAdjacentHTML('beforeend', '<div class="of-ci-note" style="margin-top:8px;">Saving…</div>');
    const response = await fetchWithAuth('/api/v1/order-fulfillment/confirm-ci-only', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        order_ref_no: ofInvoicePendingLink.order_ref_no,
        invoice_no: ofInvoicePendingLink.invoice_no,
        distributor_id: Number(distributorId),
        commercial_invoice_file_reference: ofInvoicePendingLink.commercial_invoice_file_reference,
        commercial_invoice_parsed: ofInvoicePendingLink.commercial_invoice_parsed,
        amount: isNaN(amount) ? null : amount,
        acknowledge_party_mismatch: acknowledgePartyMismatch,
      }),
    });
    const rawText = await response.text();
    let data = _parseFetchJson(response, rawText);
    if ((!response.ok || !data.success) && data.error && data.error.code === 'ci_customers_mismatch' && !acknowledgePartyMismatch) {
      const ok = window.confirm(`${data.error.message}\n\nSave anyway with the selected distributor?`);
      if (!ok) return;
      const retry = await fetchWithAuth('/api/v1/order-fulfillment/confirm-ci-only', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          order_ref_no: ofInvoicePendingLink.order_ref_no,
          invoice_no: ofInvoicePendingLink.invoice_no,
          distributor_id: Number(distributorId),
          commercial_invoice_file_reference: ofInvoicePendingLink.commercial_invoice_file_reference,
          commercial_invoice_parsed: ofInvoicePendingLink.commercial_invoice_parsed,
          amount: isNaN(amount) ? null : amount,
          acknowledge_party_mismatch: true,
        }),
      });
      const retryText = await retry.text();
      const retryData = _parseFetchJson(retry, retryText);
      if (!retry.ok || !retryData.success) {
        throw new Error((retryData.error && retryData.error.message) || 'CI-only save failed');
      }
      data = retryData;
    }
    if (!response.ok && !(data && data.success)) {
      throw new Error((data.error && data.error.message) || 'CI-only save failed');
    }
    if (!data.success) {
      throw new Error((data.error && data.error.message) || 'CI-only save failed');
    }
    const d = data.data;
    if (d.is_duplicate || d.link_error) {
      _showOfInvoiceResult(`<div class="of-ci-title is-bad">${escapeHtml(d.link_error || 'Could not save CI.')}</div>`);
      ofInvoicePendingLink = null;
      return;
    }
    const detailNote = d.detail_level === 'text_only_save'
      ? ' Header/amount saved (line tables deferred on small RAM).'
      : ' Full CI details stored.';
    _showOfInvoiceResult(
      `<div class="of-ci-title is-ok">Saved CI-only! Tracking #${escapeHtml(d.tracking_id)}` +
      (d.achievement_id ? `, Achievement #${escapeHtml(d.achievement_id)} (sale from CI).` : '.') +
      `</div>` +
      `<div class="of-ci-note" style="margin-top:6px;">Order ref <strong>${escapeHtml(d.order_ref_no || '')}</strong> · ${escapeHtml(d.distributor_name || '')}.${detailNote} SO can merge later if uploaded.</div>` +
      `<div style="margin-top:8px;"><button type="button" class="nx-btn nx-btn-primary" onclick="openCiTrackingDetail(${Number(d.tracking_id)})">View CI detail</button></div>`
    );
    ofInvoicePendingLink = null;
    document.getElementById('of-invoice-file').value = '';
    loadOrderFulfillmentUploads();
    if (d.tracking_id) openCiTrackingDetail(d.tracking_id);
  } catch (error) {
    _showOfInvoiceResult(`<div class="of-ci-title is-bad">Error: ${escapeHtml(error.message)}</div>`);
  }
}

async function _confirmCiLinkV2Impl() {
  if (!ofInvoicePendingLink) {
    _showOfInvoiceResult('<div class="of-ci-title is-warn">Nothing pending to confirm.</div>');
    return;
  }
  const mismatchAck = document.getElementById('of-ci-party-mismatch-ack');
  if (
    ofInvoicePendingLink.party_match
    && ofInvoicePendingLink.party_match.status === 'mismatch'
    && mismatchAck
    && !mismatchAck.checked
  ) {
    const box = document.getElementById('of-invoice-result');
    if (box) {
      box.insertAdjacentHTML(
        'beforeend',
        '<div class="of-ci-title is-bad" style="margin-top:8px;">CI buyer and SO / Customers distributor do not match. Tick the confirmation box to proceed, or fix Customers master GST/name.</div>'
      );
    }
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
    const rawText = await response.text();
    const data = _parseFetchJson(response, rawText);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Confirm failed');
    }
    const d = data.data;

    if (d.is_duplicate || d.link_error) {
      _showOfInvoiceResult(`<div class="of-ci-title is-bad">${escapeHtml(d.link_error || 'This Commercial Invoice could not be linked.')}</div>`);
      ofInvoicePendingLink = null;
      return;
    }

    const discrepancyAlert = d.has_discrepancy
      ? `<div class="of-ci-title is-bad" style="margin-top:8px;">⚠ DISCREPANCY DETECTED — one or more items don't match across Ordered/SO/CI quantities or values. Check the reconciliation sheet for this distributor.</div>`
      : '';
    const ramNote = d.detail_level === 'text_only_save'
      ? `<div class="of-ci-note" style="margin-top:6px;">Saved with header/amount (line tables skipped on small RAM).</div>`
      : '';
    _showOfInvoiceResult(
      `<div class="of-ci-title is-ok">Linked! Tracking #${escapeHtml(d.tracking_id)}` +
      (d.achievement_id ? `, Achievement #${escapeHtml(d.achievement_id)} recorded.` : '.') +
      `</div>` +
      discrepancyAlert + ramNote +
      `<div style="margin-top:8px;"><button type="button" class="nx-btn nx-btn-primary" onclick="openCiTrackingDetail(${Number(d.tracking_id)})">View CI detail</button></div>`
    );
    ofInvoicePendingLink = null;
    document.getElementById('of-invoice-file').value = '';
    loadOrderFulfillmentUploads();
    if (d.tracking_id) openCiTrackingDetail(d.tracking_id);
  } catch (error) {
    _showOfInvoiceResult(`<div class="of-ci-title is-bad">Error: ${escapeHtml(error.message)}</div>`);
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
        ? `${orders.length} earlier filled order(s) on file` +
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
  bindSoPackFileInput();
  updateSoPackExcelButtonState();
  showOfSection(ofSoPackLastPayload ? 'so-pack' : 'filled-order');
}

function closeCiTrackingDetail() {
  const panel = document.getElementById('of-ci-detail-panel');
  if (panel) panel.hidden = true;
  const body = document.getElementById('of-ci-detail-body');
  if (body) body.innerHTML = '';
}

async function openCiTrackingDetail(trackingId) {
  const panel = document.getElementById('of-ci-detail-panel');
  const body = document.getElementById('of-ci-detail-body');
  const titleEl = document.getElementById('of-ci-detail-title');
  const subEl = document.getElementById('of-ci-detail-sub');
  if (!panel || !body) return;

  panel.hidden = false;
  body.innerHTML = '<p class="nx-text-dim">Loading CI detail…</p>';
  if (titleEl) titleEl.textContent = `CI Detail #${trackingId}`;
  if (subEl) subEl.textContent = 'Fetching saved commercial invoice…';

  try {
    const response = await fetchWithAuth(`/api/v1/order-fulfillment/tracking/${trackingId}`);
    const rawText = await response.text();
    const data = typeof _parseFetchJson === 'function'
      ? _parseFetchJson(response, rawText)
      : JSON.parse(rawText);
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load CI detail');
    }
    const d = data.data || {};
    body.innerHTML = _renderCiTrackingDetailHtml(d);
    if (titleEl) {
      titleEl.textContent = `CI ${d.invoice_no || d.order_ref_no || `#${trackingId}`}`;
    }
    if (subEl) {
      const level = d.ci_detail_level || 'saved';
      subEl.textContent = `${d.distributor_name || '—'} · ${level}${d.has_sales_order ? ' · SO linked' : ' · CI-only'}`;
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (error) {
    body.innerHTML = `<p style="color:#FF6B6B;">${escapeHtml(error.message)}</p>`;
  }
}

function _renderCiTrackingDetailHtml(d) {
  const header = d.ci_header || {};
  const totals = d.ci_totals || {};
  const ciLines = Array.isArray(d.ci_line_items) ? d.ci_line_items : [];
  const reconItems = Array.isArray(d.items) ? d.items : [];

  const headerRows = [
    ['Invoice No', d.invoice_no || header.invoice_no],
    ['Invoice Date', d.commercial_invoice_date || header.invoice_date],
    ['Order Ref / SO', d.order_ref_no || header.order_ref_no],
    ['Distributor', d.distributor_name],
    ['Buyer (CI)', header.buyer_name],
    ['Buyer GST', header.buyer_gst],
    ['Consignee', header.consignee_name],
    ['Place of supply', header.place_of_supply],
    ['Cust PO', header.cust_po],
    ['Delivery No', header.delivery_no],
    ['Transporter / LR', [header.transporter, header.lr_no].filter(Boolean).join(' · ') || null],
    ['Payment / Transit', [d.payment_status, d.transit_status].filter(Boolean).join(' · ') || null],
  ].filter(([, v]) => v != null && String(v).trim() !== '');

  const amountRows = [
    ['Taxable', header.taxable_amount ?? totals.taxable_amount ?? totals.taxable],
    ['IGST', header.total_igst ?? totals.total_igst ?? totals.igst],
    ['Invoice total', header.invoice_total ?? totals.invoice_total ?? totals.line_total],
    ['Pieces', header.total_pieces ?? totals.qty],
  ].filter(([, v]) => v != null && v !== '');

  const headerTable = headerRows.length
    ? `<table class="data-table" style="max-width:40rem;margin:0 0 0.75rem;"><tbody>${
        headerRows.map(([k, v]) => `<tr><td style="width:11rem;">${escapeHtml(k)}</td><td><strong>${escapeHtml(v)}</strong></td></tr>`).join('')
      }</tbody></table>`
    : '<p class="nx-text-dim">No CI header fields saved.</p>';

  const amountTable = amountRows.length
    ? `<table class="data-table" style="max-width:28rem;margin:0 0 0.75rem;"><tbody>${
        amountRows.map(([k, v]) => {
          const shown = typeof v === 'number' || (v !== '' && !Number.isNaN(Number(v)))
            ? _ofMoney(v)
            : escapeHtml(v);
          return `<tr><td style="width:11rem;">${escapeHtml(k)}</td><td><strong>${shown}</strong></td></tr>`;
        }).join('')
      }</tbody></table>`
    : '';

  let linesHtml = '';
  if (ciLines.length) {
    linesHtml = `
      <h4 style="margin:0.75rem 0 0.35rem;">CI line items (${ciLines.length})</h4>
      <div class="of-tracking-wrap">
        <table class="data-table">
          <thead><tr><th>#</th><th>Item</th><th>Qty</th><th>Rate</th><th>Value</th><th>HSN</th></tr></thead>
          <tbody>
            ${ciLines.map((it, i) => `<tr>
              <td>${i + 1}</td>
              <td>${escapeHtml(it.item_name || it.material_code || '—')}</td>
              <td>${_ofQty(it.qty)}</td>
              <td>${it.rate != null ? _ofMoney(it.rate) : '—'}</td>
              <td>${_ofMoney(it.value ?? it.taxable ?? it.line_total)}</td>
              <td>${escapeHtml(it.hsn || '—')}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } else if (reconItems.some((it) => Number(it.ci_qty) > 0)) {
    linesHtml = `
      <h4 style="margin:0.75rem 0 0.35rem;">Reconciliation items (CI qty)</h4>
      <div class="of-tracking-wrap">
        <table class="data-table">
          <thead><tr><th>Item</th><th>Ordered</th><th>SO</th><th>CI</th><th>CI value</th></tr></thead>
          <tbody>
            ${reconItems.map((it) => `<tr>
              <td>${escapeHtml(it.item_name || it.item_key || '—')}</td>
              <td>${_ofQty(it.ordered_qty)}</td>
              <td>${_ofQty(it.so_qty)}</td>
              <td>${_ofQty(it.ci_qty)}</td>
              <td>${_ofMoney(it.ci_value)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>`;
  } else {
    const note = d.ci_parse_note
      || (d.ci_detail_level === 'text_only_save' || d.ci_detail_level === 'upload_preview'
        ? 'Line tables were skipped on small RAM (Starter 512MB). Header/amount are saved. Upgrade to Standard 2GB for full line parse.'
        : 'No CI line items stored yet.');
    linesHtml = `<p class="nx-text-dim" style="margin-top:0.75rem;">${escapeHtml(note)}</p>`;
  }

  const pdfBtn = d.has_commercial_invoice
    ? `<button type="button" class="nx-btn nx-btn-primary" onclick="openOrderFulfillmentTrackingPdf(${Number(d.tracking_id)}, 'ci')">Open CI PDF</button>`
    : '';
  const soPdfBtn = d.has_sales_order
    ? `<button type="button" class="nx-btn" onclick="openOrderFulfillmentTrackingPdf(${Number(d.tracking_id)}, 'so')">Open SO PDF</button>`
    : '';

  return `
    <div style="display:flex;flex-wrap:wrap;gap:0.5rem;margin-bottom:0.75rem;">
      ${pdfBtn}${soPdfBtn}
    </div>
    <h4 style="margin:0 0 0.35rem;">Header</h4>
    ${headerTable}
    ${amountTable ? `<h4 style="margin:0.5rem 0 0.35rem;">Amounts</h4>${amountTable}` : ''}
    ${linesHtml}
  `;
}

async function openOrderFulfillmentTrackingPdf(trackingId, kind) {
  try {
    const response = await fetchWithAuth(
      `/api/v1/order-fulfillment/tracking/${trackingId}/file?kind=${encodeURIComponent(kind || 'ci')}`
    );
    if (!response.ok) {
      const raw = await response.text();
      let msg = `Could not open ${kind || 'ci'} PDF (HTTP ${response.status})`;
      try {
        const data = JSON.parse(raw);
        if (data.error && data.error.message) msg = data.error.message;
      } catch (_) { /* ignore */ }
      throw new Error(msg);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank', 'noopener');
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    alert(error.message || 'PDF open failed');
  }
}

async function loadOrderFulfillmentUploads() {
  const trackingBody = document.getElementById('of-tracking-tbody');
  const trackingBodyCi = document.getElementById('of-tracking-tbody-ci');
  try {
    const response = await fetchWithAuth('/api/v1/order-fulfillment/uploads');
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error((data.error && data.error.message) || 'Failed to load uploads');
    }

    const tracking = (data.data.tracking_records || []).slice();
    const ciRows = tracking.filter((t) => t.has_commercial_invoice);
    const byDist = new Map();
    ciRows.forEach((t) => {
      const key = t.distributor_id != null
        ? `id:${t.distributor_id}`
        : `name:${String(t.distributor_name || t.order_ref_no || 'Unknown').trim().toLowerCase()}`;
      if (!byDist.has(key)) {
        byDist.set(key, {
          key,
          name: t.distributor_name || t.buyer_name || t.order_ref_no || 'Unknown distributor',
          rows: [],
        });
      }
      byDist.get(key).rows.push(t);
    });
    const distGroups = [...byDist.values()]
      .map((g) => ({
        ...g,
        rows: g.rows.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))),
      }))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));

    const formatCiDate = (v) => {
      if (!v) return '—';
      const s = String(v);
      return s.length >= 10 ? s.slice(0, 10) : s;
    };

    const rowsHtml = distGroups.length
      ? distGroups.map((g) => {
          const header = `<tr class="of-tree-ci-dist"><td colspan="8"><strong>${foEscapeText(g.name)}</strong> · ${g.rows.length} CI saved</td></tr>`;
          const body = g.rows.map((t) => {
            const inv = t.invoice_no || '—';
            const amt = t.ci_amount != null && t.ci_amount !== '' ? _ofMoney(t.ci_amount) : '—';
            return `<tr class="of-ci-row" style="cursor:pointer;" onclick="if(!event.target.closest('button')) openCiTrackingDetail(${t.tracking_id})">
              <td><strong>${escapeHtml(inv)}</strong></td>
              <td>${escapeHtml(t.order_ref_no || '—')}</td>
              <td>${escapeHtml(t.distributor_name || '—')}</td>
              <td>${escapeHtml(t.buyer_name || '—')}</td>
              <td>${amt}</td>
              <td>${escapeHtml(formatCiDate(t.commercial_invoice_date || t.created_at))}</td>
              <td>${t.has_sales_order ? 'Yes' : 'No'}</td>
              <td style="white-space:nowrap;" onclick="event.stopPropagation();">
                <button type="button" class="btn btn-secondary" style="padding:2px 10px;font-size:0.85rem;" onclick="openCiTrackingDetail(${t.tracking_id})">View</button>
                <button type="button" onclick="deleteOrderFulfillmentTracking(${t.tracking_id}, '${(t.order_ref_no || '').replace(/'/g, "\\'")}')" class="btn btn-danger" style="padding: 2px 10px; font-size: 0.85rem;">Delete</button>
              </td>
            </tr>`;
          }).join('');
          return header + body;
        }).join('')
      : '<tr><td colspan="8">No commercial invoices saved yet. Upload a CI above, then Confirm.</td></tr>';
    // Keep SO tracking table alphabetical flat for uploads pane.
    const soRowsHtml = tracking.length
      ? tracking
          .slice()
          .sort((a, b) =>
            String(a.distributor_name || a.order_ref_no || '').localeCompare(
              String(b.distributor_name || b.order_ref_no || ''),
              undefined,
              { sensitivity: 'base' }
            )
          )
          .map(
            (t) => `<tr>
              <td>${escapeHtml(t.order_ref_no || '-')}</td>
              <td>${escapeHtml(t.distributor_name || '-')}</td>
              <td>${t.has_sales_order ? 'Yes' : 'No'}</td>
              <td>${t.has_commercial_invoice ? 'Yes' : 'No'}</td>
              <td>${escapeHtml(t.payment_status || '-')}</td>
              <td>${escapeHtml(t.transit_status || '-')}</td>
              <td style="white-space:nowrap;">
                ${t.has_commercial_invoice ? `<button type="button" class="btn btn-secondary" style="padding:2px 10px;font-size:0.85rem;" onclick="openCiTrackingDetail(${t.tracking_id})">View CI</button>` : ''}
                <button type="button" onclick="deleteOrderFulfillmentTracking(${t.tracking_id}, '${(t.order_ref_no || '').replace(/'/g, "\\'")}')" class="btn btn-danger" style="padding: 2px 10px; font-size: 0.85rem;">Delete</button>
              </td>
            </tr>`
          )
          .join('')
      : '<tr><td colspan="7">No Sales Orders/Invoices tracked yet.</td></tr>';
    if (trackingBody) trackingBody.innerHTML = soRowsHtml;
    if (trackingBodyCi) trackingBodyCi.innerHTML = rowsHtml;
  } catch (error) {
    const errHtmlCi = `<tr><td colspan="8">Error: ${escapeHtml(error.message)}</td></tr>`;
    const errHtml = `<tr><td colspan="7">Error: ${escapeHtml(error.message)}</td></tr>`;
    if (trackingBody) trackingBody.innerHTML = errHtml;
    if (trackingBodyCi) trackingBodyCi.innerHTML = errHtmlCi;
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
  detailRowsCache: [],
  selectionMode: false,
  selectedIds: new Set(),
};

const ARTICLE_DETAIL_FIELDS = [
  ['category', 'Category'],
  ['product_type', 'Product'],
  ['brand', 'Brand'],
  ['size', 'Size'],
  ['tc', 'TC'],
  ['units', 'Units'],
  ['bs_size', 'Size'],
  ['pillow_size', 'Pillow Size'],
  ['color', 'Color'],
  ['pillow_stitching_style', 'Pillow Stitching Style'],
  ['print_style', 'Print Style'],
  ['blend', 'Blend'],
  ['packing', 'Packing'],
  ['bale_pack_size', 'Bale Pack Size'],
  ['mrp', 'MRP (₹)'],
  ['ex_mill_price', 'Ex-Mill (₹)'],
  ['awd_markup_on_exmill', 'Distributor Margin'],
  ['retailer_margin', 'Retailer Margin'],
  ['perceived', 'Proposed Customer Discount'],
  ['ptr', 'PTR (₹)'],
];

/** Extra Excel columns stored under original header names (varies by category). */
const ARTICLE_EXTRA_FIELD_ALIASES = {
  tc: ['TC'],
  units: ['Units'],
  bs_size: ['BS Size', 'Size', 'Bedset Size (Cms)', 'Bedset Size'],
  pillow_size: ['Pillow Size', 'Pillow Size (Cms)'],
  color: ['Color', 'Colour', 'Shade'],
  pillow_stitching_style: ['Pillow Stitching Style'],
  print_style: ['Print Style', 'Print/Dyed/Weave'],
  blend: ['Blend', 'BLEND'],
  packing: ['Packing'],
  retailer_margin: [
    'Retailer Margin', 'Retail Mark down', 'Retailer MD', 'Retailer Markdown',
  ],
  awd_markup_on_exmill: [
    'AWD Mark up on Exmill', 'AWD MD', 'AWD MU', 'AWD Markup on Exmill',
    'Distributor Mark up', 'Distributor Margin', 'Mark up on Exmill', 'Markup on Exmill',
  ],
  // Perceived ≡ Proposed Customer Discount (same field)
  perceived: [
    'Proposed Customer Discount', 'Perceived', 'Perceive', 'Perceived Margin',
  ],
};

const ARTICLE_SIZE_DISPLAY_NAMES = {
  'SB BS': 'Single Bedsheet',
  'DB BS': 'Double Bedsheet',
  'KS BS': 'King Bedsheet',
  'DB FS': 'Double Fitted Sheet',
  'KB FS': 'King Fitted Sheet',
  'DB COMF': 'Double Comforter',
  'DB REVERSIBLE COMF': 'Double Reversible Comforter',
  'DB DUVET COVER': 'Double Duvet Cover',
};

function formatArticleSizeDisplay(size) {
  if (size === null || size === undefined || size === '') return '';
  const key = String(size).trim().replace(/\s+/g, ' ').toUpperCase();
  return ARTICLE_SIZE_DISPLAY_NAMES[key] || String(size).trim();
}

/** Product label — Size wins over generic Bedsheet (DB Reversible Comf → Comforter). */
function getArticleProductDisplay(article) {
  const product = String(article?.product_type || '').trim();
  const sizeKey = String(article?.size || '').trim().replace(/\s+/g, ' ').toUpperCase();
  let inferred = null;
  if (/DUVET/.test(sizeKey)) inferred = 'Duvet Cover';
  else if (/COMF|COMFORTER/.test(sizeKey)) inferred = 'Comforter';
  else if (/(?:^|\s)FS(?:\s|$)|FITTED/.test(sizeKey)) inferred = 'Fitted Sheet';
  else if (/(?:^|\s)BS(?:\s|$)|BEDSHEET/.test(sizeKey)) inferred = 'Bedsheet';
  const generic = !product || ['bedsheet', 'sheet set', 'sheet sets'].includes(product.toLowerCase());
  if (inferred && generic && inferred !== 'Bedsheet') return inferred;
  return product || '—';
}

/** Physical size from booking (e.g. 75x150 / 274x274) — BS Size column (UI label: Size). */
function getArticlePhysicalSizeDisplay(article) {
  const bs = getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.bs_size);
  if (bs != null && String(bs).trim() !== '') return String(bs).trim();
  const pillow = getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.pillow_size);
  if (pillow != null && String(pillow).trim() !== '') return String(pillow).trim();
  // Bath gap-fill: Hand Towel → 40x60, Bath Towel → 75x150, …
  if (String(article?.category || '').trim() === 'Bath') {
    const key = String(article?.size || '').trim().replace(/\s+/g, ' ').toUpperCase();
    const map = {
      'HAND TOWEL': '40x60',
      'HAND TOWEL SET OF 2': '40x60(2pc)',
      'FACE TOWEL': '30x30',
      'FACE TOWEL SET OF 3': '30x30(3pc)',
      'LADIES TOWEL': '60x120',
      'BATH TOWEL': '75x150',
      'BATH MAT': '50x70',
      'POOL TOWEL': '90x180',
      'TOWEL SET': 'R4',
      'GYM TOWEL': '50x100',
      '91X100': '91x100',
      LARGE: 'L',
      'EXTRA LARGE': 'XL',
      'DOUBLE EXTRA LARGE': 'XXL',
    };
    if (map[key]) return map[key];
  }
  return '';
}

/** Brand · Size full name — skip Product when Size already says Bedsheet/Fitted/etc. */
function formatArticleMasterHeading(article) {
  const brand = String(article?.brand || '').trim();
  const sizeDisp = String(formatArticleSizeDisplay(article?.size) || article?.size || '').trim();
  const product = String(article?.product_type || '').trim();
  const parts = [brand, sizeDisp].filter(Boolean);
  if (product) {
    const sizeL = sizeDisp.toLowerCase();
    const prodL = product.toLowerCase();
    const redundant =
      !sizeDisp ||
      sizeL.includes(prodL) ||
      (prodL === 'bedsheet' && /(bedsheet|fitted\s*sheet|comforter|duvet)/i.test(sizeDisp)) ||
      (prodL === 'sheet sets' && /bedsheet/i.test(sizeDisp));
    if (!redundant) parts.push(product);
  }
  return parts.join(' · ') || 'Article';
}

function getArticleExtraValue(article, aliases) {
  const extra = (article && article.extra_attributes) || {};
  if (!extra || typeof extra !== 'object') return null;
  for (const alias of aliases) {
    if (Object.prototype.hasOwnProperty.call(extra, alias) && extra[alias] !== null && extra[alias] !== undefined && extra[alias] !== '') {
      return extra[alias];
    }
  }
  const lowerMap = Object.fromEntries(
    Object.entries(extra).map(([k, v]) => [String(k).trim().toLowerCase(), v])
  );
  for (const alias of aliases) {
    const hit = lowerMap[String(alias).trim().toLowerCase()];
    if (hit !== null && hit !== undefined && hit !== '') return hit;
  }
  return null;
}

function formatArticleMarginPercent(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'string' && value.trim().endsWith('%')) {
    const inner = Number(value.trim().replace(/%/g, '').replace(/,/g, ''));
    if (Number.isFinite(inner)) return `${Math.round(inner)}%`;
    return value.trim();
  }
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  // Booking sheets store fractions (0.28 → 28%). Float noise like 28.000000004 → 28%.
  const pct = Math.abs(num) <= 1 ? num * 100 : num;
  return `${Math.round(pct)}%`;
}

/** Retailer Margin + Perceived (when present), e.g. "35% + 40%". */
function formatArticleRetailerMarginDisplay(article, retailerMarginRaw) {
  const base = formatArticleMarginPercent(retailerMarginRaw);
  const perceived = getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.perceived);
  if (perceived === null || perceived === undefined || perceived === '') return base;
  return `${base} + ${formatArticleMarginPercent(perceived)}`;
}

/** Contact-style profile card for an Article Master row. */
async function showArticleDetail(article) {
  if (!article || typeof article !== 'object') return;
  const title = document.getElementById('article-detail-title');
  const body = document.getElementById('article-detail-body');
  const editBtn = document.getElementById('article-detail-edit-btn');
  const historyBtn = document.getElementById('article-detail-history-btn');
  if (!title || !body) return;

  const heading = formatArticleMasterHeading(article)
    || article.item_key
    || 'Article';
  title.textContent = heading;

  const PRICE_KEYS = new Set(['mrp', 'ptr', 'ex_mill_price']);
  const PRICE_COMPARE_FIELDS = [
    ['mrp', 'MRP (₹)'],
    ['ptr', 'PTR (₹)'],
    ['ex_mill_price', 'Ex-Mill (₹)'],
  ];

  let seasonPayload = null;
  if (article.id) {
    try {
      const response = await fetchWithAuth(`/api/v1/article-master/${article.id}/price-seasons`);
      const data = await response.json();
      if (response.ok && Array.isArray(data.seasons) && data.seasons.length) {
        seasonPayload = data;
      }
    } catch (_) {
      seasonPayload = null;
    }
  }

  // Fallback: previous vs current from audit history when no season snapshots yet
  let latestByField = {};
  let lastChangeMeta = null;
  if (!seasonPayload && article.has_price_history && article.id) {
    try {
      const response = await fetchWithAuth(`/api/v1/article-master/${article.id}/price-history`);
      const data = await response.json();
      if (response.ok) {
        const history = data.history || [];
        for (const h of history) {
          const f = h.field_changed;
          if (!f || latestByField[f]) continue;
          latestByField[f] = h;
        }
        if (history.length) {
          lastChangeMeta = history[0];
        }
      }
    } catch (_) {
      latestByField = {};
    }
  }

  const fieldsHtml = ARTICLE_DETAIL_FIELDS.map(([key, label]) => {
    if (PRICE_KEYS.has(key) && (seasonPayload || latestByField[key])) return '';

    let raw = article[key];
    if (ARTICLE_EXTRA_FIELD_ALIASES[key]) {
      raw = getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES[key]);
    }
    if (raw === null || raw === undefined || raw === '') return '';
    let display = raw;
    if (key === 'size') {
      display = formatArticleSizeDisplay(raw);
    } else if (PRICE_KEYS.has(key)) {
      display = formatArticleMasterValue(raw, key);
    } else if (key === 'retailer_margin' || key === 'perceived' || key === 'awd_markup_on_exmill') {
      display = formatArticleMarginPercent(raw);
    }
    const wide = key === 'item_key' ? ' bd-party-field--wide' : '';
    return `
      <div class="bd-party-field${wide}">
        <span class="bd-party-field-label">${escapePartyDetailHtml(label)}</span>
        <strong class="bd-party-field-value">${escapePartyDetailHtml(display)}</strong>
      </div>
    `;
  }).filter(Boolean).join('');

  let ratesHtml = '';
  if (seasonPayload) {
    const seasons = seasonPayload.seasons || [];
    const rowMap = seasonPayload.rows || {};
    const headCells = seasons.map((s) => `<th>${escapePartyDetailHtml(s)}</th>`).join('');
    const bodyRows = PRICE_COMPARE_FIELDS.map(([key, label]) => {
      const bySeason = rowMap[key] || {};
      const cells = seasons.map((s, idx) => {
        const val = bySeason[s];
        const disp = (val === null || val === undefined || val === '')
          ? '—'
          : formatArticleMasterValue(val, key);
        let deltaHtml = '';
        if (idx > 0) {
          const prev = bySeason[seasons[idx - 1]];
          const oldN = Number(prev);
          const newN = Number(val);
          if (Number.isFinite(oldN) && Number.isFinite(newN) && oldN !== 0) {
            const pct = ((newN - oldN) / Math.abs(oldN)) * 100;
            const up = pct > 0.05;
            const down = pct < -0.05;
            const cls = up ? 'am-rate-up' : (down ? 'am-rate-down' : 'am-rate-flat');
            const sign = pct > 0 ? '+' : '';
            deltaHtml = ` <span class="am-rate-delta ${cls}">${sign}${pct.toFixed(1)}%</span>`;
          }
        }
        const cls = idx === seasons.length - 1 ? 'am-rate-new' : 'am-rate-old';
        return `<td class="${cls}">${escapePartyDetailHtml(disp)}${deltaHtml}</td>`;
      }).join('');
      return `<tr><td>${escapePartyDetailHtml(label)}</td>${cells}</tr>`;
    }).join('');
    ratesHtml = `
      <div class="bd-party-field bd-party-field--wide am-rates-block">
        <span class="bd-party-field-label">Rates — by season (last ${seasons.length})</span>
        <div class="am-rates-table-wrap">
          <table class="am-rates-table">
            <thead>
              <tr><th>Field</th>${headCells}</tr>
            </thead>
            <tbody>${bodyRows}</tbody>
          </table>
        </div>
      </div>
    `;
  } else {
    const compareRows = PRICE_COMPARE_FIELDS.map(([key, label]) => {
      const hist = latestByField[key];
      if (!hist) return '';
      const oldDisp = formatPriceHistoryValue(hist.old_value, key);
      const newDisp = formatArticleMasterValue(article[key], key);
      const oldN = Number(hist.old_value);
      const newN = Number(article[key]);
      let deltaHtml = '';
      if (Number.isFinite(oldN) && Number.isFinite(newN) && oldN !== 0) {
        const pct = ((newN - oldN) / Math.abs(oldN)) * 100;
        const up = pct > 0.05;
        const down = pct < -0.05;
        const cls = up ? 'am-rate-up' : (down ? 'am-rate-down' : 'am-rate-flat');
        const sign = pct > 0 ? '+' : '';
        deltaHtml = `<span class="am-rate-delta ${cls}">${sign}${pct.toFixed(1)}%</span>`;
      }
      return `
        <tr>
          <td>${escapePartyDetailHtml(label)}</td>
          <td class="am-rate-old">${escapePartyDetailHtml(oldDisp)}</td>
          <td class="am-rate-new">${escapePartyDetailHtml(newDisp)} ${deltaHtml}</td>
        </tr>
      `;
    }).filter(Boolean).join('');

    if (compareRows) {
      let metaLine = '';
      if (lastChangeMeta) {
        const when = lastChangeMeta.changed_at
          ? new Date(lastChangeMeta.changed_at).toLocaleString()
          : '';
        const who = lastChangeMeta.changed_by || '';
        metaLine = `<p class="am-rates-meta">${escapePartyDetailHtml([who, when].filter(Boolean).join(' · '))}</p>`;
      }
      ratesHtml = `
        <div class="bd-party-field bd-party-field--wide am-rates-block">
          <span class="bd-party-field-label">Rates — previous vs current</span>
          <div class="am-rates-table-wrap">
            <table class="am-rates-table">
              <thead>
                <tr><th>Field</th><th>Previous</th><th>Current</th></tr>
              </thead>
              <tbody>${compareRows}</tbody>
            </table>
          </div>
          ${metaLine}
        </div>
      `;
    }
  }

  body.innerHTML = (fieldsHtml + ratesHtml)
    || '<p class="bd-party-muted">No details available.</p>';

  const articleId = Number(article.id);
  if (editBtn) {
    editBtn.onclick = () => {
      closeModal('article-detail-modal');
      openArticleMasterFullEdit(articleId, article);
    };
  }
  if (historyBtn) {
    if (article.has_price_history) {
      historyBtn.classList.remove('hidden');
      historyBtn.onclick = () => {
        closeModal('article-detail-modal');
        openArticleMasterPriceHistory(articleId);
      };
    } else {
      historyBtn.classList.add('hidden');
      historyBtn.onclick = null;
    }
  }

  toggleModal('article-detail-modal', true);
}

function formatArticleMasterValue(value, field = null) {
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  if (['mrp', 'ptr', 'ex_mill_price'].includes(field)) {
    const num = Number(value);
    if (!Number.isFinite(num)) return String(value);
    // MRP: whole rupees; PTR / Ex-Mill: always 2 decimals (00.00)
    if (field === 'mrp') return String(Math.round(num));
    return num.toFixed(2);
  }
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return value;
}

function formatArticleMasterMoneyInput(value, field) {
  if (value === null || value === undefined || value === '') return '';
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (field === 'mrp') return String(Math.round(num));
  return num.toFixed(2);
}

function updateArticleMasterStats() {
  const articles = articleMasterState.articles;
  const counts = { Bed: 0, Bath: 0, TOB: 0, Pillow: 0 };
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
  if (pillowEl) pillowEl.textContent = String(counts.Pillow);
}

function populateArticleMasterCategoryFilter() {
  const select = document.getElementById('am-category-filter');
  if (!select) return;
  const current = select.value || 'All';
  const categories = [...new Set(articleMasterState.articles.map((a) => a.category).filter(Boolean))].sort();
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

function articleMasterSizeSearchTokens(size) {
  const s = String(size || '').trim().toUpperCase().replace(/\s+/g, ' ');
  const tokens = [s, formatArticleSizeDisplay(size) || ''];
  if (s.startsWith('KS') || s.startsWith('KB') || s.startsWith('KDB') || s.includes('KING')) {
    tokens.push('king', 'king size', 'ks', 'kb');
  } else if (s.startsWith('DB') || s.startsWith('DBL') || s.includes('DOUBLE')) {
    tokens.push('double', 'db', 'dbl');
  } else if (s.startsWith('SB') || s.includes('SINGLE')) {
    tokens.push('single', 'sb');
  }
  return tokens.join(' ');
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
      a.category,
      a.brand,
      a.size,
      getArticleProductDisplay(a),
      a.item_key,
      articleMasterSizeSearchTokens(a.size),
      getArticlePhysicalSizeDisplay(a),
    ].join(' ').toLowerCase();
    return haystack.includes(query);
  });
  return filtered.sort((a, b) => {
    const brandCmp = String(a.brand || '').localeCompare(String(b.brand || ''), undefined, { sensitivity: 'base' });
    if (brandCmp !== 0) return brandCmp;
    const sizeRank = (size) => {
      const s = String(size || '').trim().toUpperCase().replace(/\s+/g, ' ');
      if (!s) return 4;
      if (s.startsWith('SB') || s.includes('SINGLE')) return 1;
      if (s.startsWith('KDB') || s.startsWith('KS') || s.startsWith('KB') || s.includes('KING')) return 3;
      if (s.startsWith('DBL') || s.startsWith('DB') || s.includes('DOUBLE')) return 2;
      return 4;
    };
    const rankCmp = sizeRank(a.size) - sizeRank(b.size);
    if (rankCmp !== 0) return rankCmp;
    const sizeCmp = String(a.size || '').localeCompare(String(b.size || ''), undefined, { sensitivity: 'base' });
    if (sizeCmp !== 0) return sizeCmp;
    return String(a.item_key || '').localeCompare(String(b.item_key || ''), undefined, { sensitivity: 'base' });
  });
}

function scheduleArticleMasterLayout() {
  const section = document.getElementById('article-master-workspace');
  if (!section || section.classList.contains('hidden')) return;
  const wrap = document.getElementById('am-scroll-wrapper');
  const shell = section.querySelector('.bd-am-shell') || section;
  if (!wrap || !shell) return;
  const apply = () => {
    const shellH = shell.getBoundingClientRect().height;
    const topbar = section.querySelector('.bd-am-topbar');
    const result = document.getElementById('am-upload-result');
    const chromeH = (topbar?.getBoundingClientRect().height || 0)
      + ((result && result.offsetParent !== null && result.textContent.trim()) ? result.getBoundingClientRect().height : 0)
      + 4;
    const fallback = Math.max(220, Math.floor(window.innerHeight - 120));
    const available = Math.max(180, Math.floor((shellH > 40 ? shellH : fallback) - chromeH));
    wrap.style.setProperty('height', `${available}px`, 'important');
    wrap.style.setProperty('max-height', `${available}px`, 'important');
    wrap.style.setProperty('min-height', '0', 'important');
    wrap.style.setProperty('flex', '1 1 0', 'important');
    wrap.style.setProperty('overflow', 'scroll', 'important');
    wrap.style.setProperty('overflow-x', 'scroll', 'important');
    wrap.style.setProperty('overflow-y', 'scroll', 'important');
  };
  apply();
  requestAnimationFrame(apply);
}

function getArticleMarginFields(article) {
  return {
    distributor: getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.awd_markup_on_exmill),
    retailer: getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.retailer_margin),
    perceived: getArticleExtraValue(article, ARTICLE_EXTRA_FIELD_ALIASES.perceived),
  };
}

function articleMasterMarginColumnFlags(_rows) {
  // Always show all three margin columns for a uniform table layout.
  return { showDist: true, showRetailer: true, showPerceived: true };
}

function renderArticleMasterTable() {
  const tbody = document.getElementById('am-articles-tbody');
  const countEl = document.getElementById('am-list-count');
  const table = tbody?.closest('table');
  if (!tbody) return;

  const rows = getFilteredArticleMasterRows();
  articleMasterState.detailRowsCache = rows;
  updateArticleMasterSelectionChrome(rows);

  const selecting = !!articleMasterState.selectionMode;
  const { showDist, showRetailer, showPerceived } = articleMasterMarginColumnFlags(rows);
  const colCount =
    (selecting ? 1 : 0) +
    9 +
    (showDist ? 1 : 0) +
    (showRetailer ? 1 : 0) +
    (showPerceived ? 1 : 0) +
    1; // Category…Bale + BS Size + actions

  if (table) {
    table.classList.toggle('am-selecting', selecting);
    table.querySelectorAll('.am-col-select').forEach((el) => {
      el.style.display = selecting ? '' : 'none';
    });
    table.querySelectorAll('.am-col-dist-margin').forEach((el) => {
      el.style.display = showDist ? '' : 'none';
    });
    table.querySelectorAll('.am-col-retailer-margin').forEach((el) => {
      el.style.display = showRetailer ? '' : 'none';
    });
    table.querySelectorAll('.am-col-perceived-margin').forEach((el) => {
      el.style.display = showPerceived ? '' : 'none';
    });
    const selectAll = document.getElementById('am-select-all');
    if (selectAll) {
      const visibleIds = rows.map((a) => Number(a.id)).filter((id) => Number.isFinite(id));
      const selectedVisible = visibleIds.filter((id) => articleMasterState.selectedIds.has(id));
      selectAll.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
      selectAll.indeterminate =
        selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
    }
  }

  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${colCount}">No articles found. Upload a booking form Excel to get started.</td></tr>`;
    scheduleArticleMasterLayout();
    return;
  }

  tbody.innerHTML = rows.map((a, index) => {
    const margins = getArticleMarginFields(a);
    const distTd = showDist
      ? `<td class="am-col-dist-margin">${margins.distributor != null && margins.distributor !== '' ? escapePartyDetailHtml(formatArticleMarginPercent(margins.distributor)) : '—'}</td>`
      : '';
    const retTd = showRetailer
      ? `<td class="am-col-retailer-margin">${margins.retailer != null && margins.retailer !== '' ? escapePartyDetailHtml(formatArticleMarginPercent(margins.retailer)) : '—'}</td>`
      : '';
    const percTd = showPerceived
      ? `<td class="am-col-perceived-margin">${margins.perceived != null && margins.perceived !== '' ? escapePartyDetailHtml(formatArticleMarginPercent(margins.perceived)) : '—'}</td>`
      : '';
    const idNum = Number(a.id);
    const checked = articleMasterState.selectedIds.has(idNum) ? ' checked' : '';
    const selectTd = selecting
      ? `<td class="am-col-select" onclick="event.stopPropagation()">
          <input type="checkbox" class="am-row-select" data-am-id="${idNum}"${checked}
            aria-label="Select article"
            onchange="toggleArticleMasterSelection(${idNum}, this.checked)" />
        </td>`
      : '';
    const rowClick = selecting
      ? `toggleArticleMasterSelection(${idNum}, !articleMasterState.selectedIds.has(${idNum}))`
      : `if(!event.target.closest('button,input,label')){showArticleDetail(articleMasterState.detailRowsCache[${index}])}`;
    return `
    <tr onclick="${rowClick}" style="cursor:pointer;">
      ${selectTd}
      <td>${a.category || '—'}</td>
      <td>${formatArticleMasterValue(a.brand)}</td>
      <td>${escapePartyDetailHtml(formatArticleSizeDisplay(a.size) || '—')}</td>
      <td>${escapePartyDetailHtml(getArticlePhysicalSizeDisplay(a) || '—')}</td>
      <td>${escapePartyDetailHtml(getArticleProductDisplay(a))}</td>
      <td>${formatArticleMasterValue(a.mrp, 'mrp')}</td>
      <td>${formatArticleMasterValue(a.ptr, 'ptr')}</td>
      <td>${formatArticleMasterValue(a.ex_mill_price, 'ex_mill_price')}</td>
      ${distTd}${retTd}${percTd}
      <td>${formatArticleMasterValue(a.bale_pack_size)}</td>
      <td class="am-actions">${articleMasterActionButtons(a.id, !!a.has_price_history)}</td>
    </tr>`;
  }).join('');
  scheduleArticleMasterLayout();
}

async function loadArticleMasterList() {
  const tbody = document.getElementById('am-articles-tbody');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="12">Loading...</td></tr>';
  }

  try {
    const response = await fetchWithAuth('/api/v1/article-master/list');
    const data = await response.json();
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load articles'));
    }
    articleMasterState.articles = data.articles || [];
    if (articleMasterState.selectionMode) {
      const valid = new Set(
        (articleMasterState.articles || []).map((a) => Number(a.id)).filter((id) => Number.isFinite(id)),
      );
      articleMasterState.selectedIds = new Set(
        [...articleMasterState.selectedIds].filter((id) => valid.has(id)),
      );
    }
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
    const knownCategories = ['Bed', 'Bath', 'TOB', 'Pillow'];
    const seasonOptions = ['SS-25', 'AW-25', 'SS-26', 'AW-26', 'SS-27', 'AW-27'];
    const suggestedSeason = (data.suggested_season_tag || data.season_tag || '').trim();
    const ui = nxThemeUi();

    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 99999; padding: 12px;';

    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 10px; ` +
      `padding: 14px 16px; max-width: 340px; width: 100%; ` +
      `box-shadow: 0 10px 28px rgba(0,0,0,0.28); font-family: inherit; color: ${ui.boxFg};`;

    const mixText = Object.entries(breakdown)
      .map(([cat, count]) => `${cat} ${count}`)
      .join(' · ') || '—';

    const seasonInList = seasonOptions.includes(suggestedSeason);
    const seasonOptsHtml = ['']
      .concat(seasonOptions)
      .map((s) => {
        const selected = s && s === suggestedSeason ? ' selected' : (!s && !suggestedSeason ? ' selected' : '');
        const label = s || '— none —';
        return `<option value="${s}"${selected}>${label}</option>`;
      })
      .join('');

    const fieldCss =
      `width:100%; box-sizing:border-box; padding:7px 8px; border-radius:7px; ` +
      `border:1px solid ${ui.secondaryBorder}; background:${ui.boxBg}; color:${ui.boxFg}; font-size:13px;`;

    box.innerHTML =
      `<div style="font-size:14px; font-weight:600; margin-bottom:4px; color:${ui.accent};">Confirm category & season</div>` +
      `<div style="font-size:11px; color:${ui.muted}; margin-bottom:8px; line-height:1.35;">` +
        `Suggested: <strong style="color:${ui.boxFg};">${detectedCategory}</strong>` +
        ` · ${totalRows} rows · ${mixText}` +
      `</div>` +
      `<div style="font-size:11px; color:${ui.muted}; margin-bottom:3px;">Season</div>` +
      `<select id="am-modal-season" style="${fieldCss} margin-bottom:6px;">${seasonOptsHtml}</select>` +
      `<input id="am-modal-season-manual" type="text" placeholder="Or type season (e.g. AW-26, SS-28)" ` +
        `value="${seasonInList ? '' : suggestedSeason.replace(/"/g, '&quot;')}" ` +
        `style="${fieldCss} margin-bottom:10px;" autocomplete="off" />` +
      `<div id="am-modal-auto-btn" style="background:${ui.accent}; color:${ui.accentFg}; text-align:center; ` +
        `padding:8px; border-radius:7px; cursor:pointer; font-weight:600; font-size:13px; margin-bottom:8px;">` +
        `AUTO — keep per-row categories</div>` +
      `<div style="font-size:10px; color:${ui.muted}; margin-bottom:4px;">Or force all rows:</div>` +
      '<div id="am-modal-force-btns" style="display:flex; gap:5px; flex-wrap:wrap; margin-bottom:8px;"></div>' +
      `<div id="am-modal-cancel-btn" style="text-align:center; padding:6px; border-radius:7px; cursor:pointer; ` +
        `font-size:12px; color:${ui.secondaryFg}; border:1px solid ${ui.secondaryBorder}; background:${ui.secondaryBg};">Cancel</div>`;

    overlay.appendChild(box);
    document.body.appendChild(overlay);

    const seasonSelect = box.querySelector('#am-modal-season');
    const seasonManual = box.querySelector('#am-modal-season-manual');

    // If suggested season isn't in the preset list, select none and show it in manual.
    if (suggestedSeason && !seasonInList && seasonManual) {
      seasonManual.value = suggestedSeason;
      if (seasonSelect) seasonSelect.value = '';
    }

    seasonSelect?.addEventListener('change', () => {
      if (seasonSelect.value && seasonManual) seasonManual.value = '';
    });
    seasonManual?.addEventListener('input', () => {
      if (seasonManual.value.trim() && seasonSelect) seasonSelect.value = '';
    });

    function pickedSeason() {
      const typed = (seasonManual?.value || '').trim();
      if (typed) return typed.toUpperCase().replace(/\s+/g, '-').replace(/--+/g, '-');
      return (seasonSelect?.value || '').trim() || null;
    }
    function finish(category) {
      cleanup();
      if (!category) {
        resolve(null);
        return;
      }
      resolve({ category, season_tag: pickedSeason() });
    }

    const forceContainer = box.querySelector('#am-modal-force-btns');
    knownCategories.forEach((cat) => {
      const btn = document.createElement('div');
      btn.textContent = cat;
      btn.style.cssText =
        'flex: 1 1 auto; text-align:center; padding:6px 8px; border-radius:7px; ' +
        `cursor:pointer; font-size:12px; border:1px solid ${ui.secondaryBorder}; color:${ui.secondaryFg}; background:${ui.secondaryBg}; min-width: 58px;`;
      btn.addEventListener('mouseenter', () => { btn.style.borderColor = ui.accent; btn.style.color = ui.accent; });
      btn.addEventListener('mouseleave', () => { btn.style.borderColor = ui.secondaryBorder; btn.style.color = ui.secondaryFg; });
      btn.addEventListener('click', () => { finish(cat); });
      forceContainer.appendChild(btn);
    });

    function cleanup() {
      document.body.removeChild(overlay);
      document.removeEventListener('keydown', onKeydown);
    }
    function onKeydown(e) {
      if (e.key === 'Escape') { finish(null); }
    }
    document.addEventListener('keydown', onKeydown);

    box.querySelector('#am-modal-auto-btn').addEventListener('click', () => {
      finish('AUTO');
    });
    box.querySelector('#am-modal-cancel-btn').addEventListener('click', () => {
      finish(null);
    });
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) { finish(null); }
    });
  });
}

const articleMasterUploadState = {
  pendingFile: null,
  pendingCategory: null,
  pendingSeasonTag: null,
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

function formatArticleMasterComparisonStatus(status) {
  const ui = nxThemeUi();
  const map = {
    match: `<span style="color:${ui.ok}; font-weight:600;">Match</span>`,
    mismatch: `<span style="color:${ui.down}; font-weight:600;">Mismatch</span>`,
    missing_in_file: `<span style="color:${ui.warn}; font-weight:600;">Missing in file</span>`,
    missing_in_master: `<span style="color:${ui.warn}; font-weight:600;">Missing in Article Master</span>`,
    both_empty: `<span style="color:${ui.muted};">Empty</span>`,
  };
  return map[status] || status;
}

function formatArticleMasterPriceChange(change) {
  const ui = nxThemeUi();
  if (!change || change.direction === 'same') {
    return `<span style="color:${ui.muted};">—</span>`;
  }
  const pct = change.pct != null ? ` (${change.pct > 0 ? '+' : ''}${change.pct}%)` : '';
  if (change.direction === 'increase') {
    return `<span style="color:${ui.up}; font-weight:600;">↑ ${formatArticleMasterValue(change.delta)}${pct}</span>`;
  }
  if (change.direction === 'decrease') {
    return `<span style="color:${ui.down}; font-weight:600;">↓ ${formatArticleMasterValue(Math.abs(change.delta))}${pct}</span>`;
  }
  return `<span style="color:${ui.warn}; font-weight:600;">Changed</span>`;
}

function renderArticleMasterConflictCard(conflict, arrayIdx = 0) {
  const ui = nxThemeUi();
  const key = getArticleMasterConflictKey(conflict, arrayIdx);
  const resolved = articleMasterUploadState.resolutions[key];
  const label = formatArticleMasterHeading(conflict);

  const colgroup =
    '<colgroup>' +
    '<col style="width:18%">' +
    '<col style="width:26%">' +
    '<col style="width:26%">' +
    '<col style="width:30%">' +
    '</colgroup>';

  const thPad = 'padding:8px 10px; font-weight:600; text-align:left; white-space:nowrap;';
  const tdPad = 'padding:7px 10px; vertical-align:middle;';

  const sectionRow = (title) => (
    `<tr>` +
    `<td colspan="4" style="${tdPad} padding-top:12px; padding-bottom:6px; color:${ui.boxFg}; font-weight:600; font-size:12px; border-bottom:1px solid ${ui.rowBorder}; background:${ui.soft};">` +
    `${title}</td></tr>`
  );

  const keyRows = (conflict.field_comparisons || []).map((c) => (
    '<tr style="border-bottom:1px solid ' + ui.rowBorder + ';">' +
    `<td style="${tdPad} color:${ui.muted}; font-weight:500;">${c.field}</td>` +
    // Article Master = existing, New file = upload (same order as price rows)
    `<td style="${tdPad} color:${ui.boxFg};">${formatArticleMasterValue(c.existing_value)}</td>` +
    `<td style="${tdPad} color:${ui.boxFg};">${formatArticleMasterValue(c.upload_value)}</td>` +
    `<td style="${tdPad}">${formatArticleMasterComparisonStatus(c.status)}</td>` +
    '</tr>'
  )).join('');

  const priceRows = (conflict.price_comparisons || []).map((c) => (
    '<tr style="border-bottom:1px solid ' + ui.rowBorder + ';">' +
    `<td style="${tdPad} color:${ui.muted}; font-weight:500;">${c.field}</td>` +
    `<td style="${tdPad} color:${ui.boxFg};">${formatArticleMasterValue(c.existing_value, c.field)}</td>` +
    `<td style="${tdPad} color:${ui.boxFg};">${formatArticleMasterValue(c.upload_value, c.field)}</td>` +
    `<td style="${tdPad}">${formatArticleMasterPriceChange(c.change)}</td>` +
    '</tr>'
  )).join('');

  const compareTable = (keyRows || priceRows)
    ? (
      `<table class="am-conflict-compare-table" style="width:100%; border-collapse:collapse; table-layout:fixed; font-size:12.5px; margin-top:10px;">` +
      colgroup +
      `<thead><tr style="background:${ui.soft}; border-bottom:1px solid ${ui.rowBorder}; color:${ui.boxFg};">` +
      `<th style="${thPad}">Field</th>` +
      `<th style="${thPad}">Article Master</th>` +
      `<th style="${thPad}">New file</th>` +
      `<th style="${thPad}">Change</th>` +
      '</tr></thead><tbody>' +
      (keyRows ? sectionRow('Identity fields') + keyRows : '') +
      (priceRows ? sectionRow('Price revision (season update)') + priceRows : '') +
      '</tbody></table>'
    )
    : '';

  const dupNote = (conflict.duplicate_ids && conflict.duplicate_ids.length)
    ? `<div style="color:${ui.accent}; font-size:11px; margin-top:6px;">${conflict.duplicate_ids.length} extra duplicate row(s) in Article Master — Replace will merge into one.</div>`
    : '';
  const recommend = conflict.recommended_action === 'replace'
    ? `<div style="color:${ui.muted}; font-size:11px; margin-top:8px;">Recommended: <strong style="color:${ui.boxFg};">Replace with new prices</strong> — applies season update and removes duplicate rows.</div>`
    : '';

  const resolvedBadge = resolved
    ? `<div style="margin-top:10px; font-size:12px; color:${ui.accent};">Resolved: ${resolved === 'replace' ? 'Replace existing' : resolved === 'create_new' ? 'Create new entry' : 'Skip'}</div>`
    : '';

  const createBtn = conflict.can_create_new
    ? `<button type="button" class="nx-btn am-conflict-create" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px; color:${ui.secondaryFg}; border-color:${ui.secondaryBorder}; background:${ui.secondaryBg};">Create new entry</button>`
    : '';

  const cardBorder = resolved ? ui.accent : ui.boxBorder;
  const cardBg = resolved ? ui.soft : ui.boxBg;

  return (
    `<div class="am-conflict-card" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="padding:14px; margin-bottom:12px; border:1px solid ${cardBorder}; border-radius:8px; background:${cardBg}; color:${ui.boxFg}; ${resolved ? 'opacity:0.92;' : ''}">` +
    `<div style="font-size:13px; font-weight:600; color:${ui.boxFg};">${label}</div>` +
    `<div style="font-size:11px; color:${ui.muted}; margin-top:4px;">Category: ${conflict.category || '—'} · File row: ${conflict.upload_index != null ? Number(conflict.upload_index) + 1 : arrayIdx + 1}</div>` +
    `<div style="color:${ui.muted}; font-size:12px; margin-top:8px;">${conflict.issue_summary || 'Seasonal price revision'}</div>` +
    dupNote + recommend +
    compareTable + resolvedBadge +
    `<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:12px;">` +
    (resolved ? '' :
      `<button type="button" class="nx-btn nx-btn-primary am-conflict-replace" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px;">Replace with new prices</button>` +
      createBtn +
      `<button type="button" class="nx-btn am-conflict-skip" data-conflict-key="${key}" data-array-idx="${arrayIdx}" style="font-size:12px; color:${ui.secondaryFg}; border-color:${ui.secondaryBorder}; background:${ui.secondaryBg};">Skip this row</button>`
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
    const ui = nxThemeUi();

    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 100001;';

    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 1100px; width: 96%; max-height: 94vh; display:flex; flex-direction:column; ` +
      `box-shadow: 0 12px 40px rgba(0,0,0,0.28); font-family: inherit; color: ${ui.boxFg};`;

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
        `<h2 style="margin:0 0 8px; color:${ui.accent};">Seasonal price revision — review</h2>` +
        `<p style="margin:0; color:${ui.muted}; font-size:13px;">${data.message || 'Uploaded prices differ from Article Master (increase or decrease). Replace applies the new season prices.'}</p>` +
        `<p style="margin:8px 0 0; color:${ui.muted}; font-size:12px;">${resolved} resolved · ${remaining} remaining` +
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
      `<button type="button" id="am-conflict-replace-all-btn" class="nx-btn nx-btn-primary">Replace all with new prices</button>` +
      `<button type="button" id="am-conflict-skip-all-btn" class="nx-btn" style="color:${ui.secondaryFg}; border-color:${ui.secondaryBorder}; background:${ui.secondaryBg};">Skip all remaining</button>` +
      `<button type="button" id="am-conflict-cancel-btn" class="nx-btn" style="color:${ui.secondaryFg}; border-color:${ui.secondaryBorder}; background:${ui.secondaryBg};">Cancel upload</button>` +
      '</div>' +
      `<button type="button" id="am-conflict-apply-btn" class="nx-btn nx-btn-primary" disabled style="opacity:0.55;">Resolve all items to continue</button>`;

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

async function uploadArticleMasterSheet(confirmedCategory = null, conflictResolutions = null, uiPrefix = null, seasonTag = null) {
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
  const effectiveSeason = seasonTag || articleMasterUploadState.pendingSeasonTag;
  if (effectiveSeason) {
    formData.append('season_tag', effectiveSeason);
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
      const category = typeof selected === 'string' ? selected : selected.category;
      const season = typeof selected === 'string' ? null : (selected.season_tag || null);
      articleMasterUploadState.pendingFile = file;
      articleMasterUploadState.pendingCategory = category;
      articleMasterUploadState.pendingSeasonTag = season;
      articleMasterUploadState.resolutions = {};
      await uploadArticleMasterSheet(category, null, prefix, season);
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
            ? `${partial.join(' · ')} · mismatch review cancelled — Refresh to see new rows (e.g. new sizes).`
            : 'Upload cancelled — price mismatches not resolved.';
        }
        // Creates may already be committed before the mismatch modal — refresh list.
        await loadArticleMasterList();
        return;
      }
      await uploadArticleMasterSheet(
        articleMasterUploadState.pendingCategory || confirmedCategory,
        resolutions,
        prefix,
        articleMasterUploadState.pendingSeasonTag || seasonTag
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
    articleMasterUploadState.pendingSeasonTag = null;
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
    'This will permanently hard-delete this article from Article Master. This cannot be undone.',
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
    articleMasterState.selectedIds.delete(Number(articleId));
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Delete failed');
  }
}

function updateArticleMasterSelectionChrome(rows) {
  const countEl = document.getElementById('am-list-count');
  const deleteBtn = document.getElementById('am-delete-btn');
  const cancelBtn = document.getElementById('am-cancel-selection-btn');
  const rowCount = Array.isArray(rows) ? rows.length : (getFilteredArticleMasterRows()?.length || 0);
  const selected = articleMasterState.selectedIds.size;
  const selecting = !!articleMasterState.selectionMode;

  if (countEl) {
    if (selecting) {
      countEl.textContent =
        `${selected} selected · ${rowCount} article${rowCount === 1 ? '' : 's'}`;
      countEl.classList.toggle('am-count-has-selection', selected > 0);
    } else {
      countEl.textContent = `${rowCount} article${rowCount === 1 ? '' : 's'}`;
      countEl.classList.remove('am-count-has-selection');
    }
  }

  if (cancelBtn) {
    cancelBtn.classList.toggle('hidden', !selecting);
  }

  if (deleteBtn) {
    if (selecting) {
      deleteBtn.textContent = selected > 0 ? `Delete (${selected})` : 'Delete selected';
      deleteBtn.title = selected > 0
        ? `Delete ${selected} selected article${selected === 1 ? '' : 's'}`
        : 'Select articles, then delete — or Cancel';
      deleteBtn.classList.toggle('nx-btn-primary', selected > 0);
    } else {
      deleteBtn.textContent = 'Delete';
      deleteBtn.title = 'Delete selected articles or delete all';
      deleteBtn.classList.remove('nx-btn-primary');
    }
  }
}

function enterArticleMasterSelectionMode() {
  articleMasterState.selectionMode = true;
  articleMasterState.selectedIds = new Set();
  renderArticleMasterTable();
  nexoraToast('Select articles to delete. Cancel or Esc clears selection.', 'info');
}

function exitArticleMasterSelectionMode({ silent = false } = {}) {
  if (!articleMasterState.selectionMode) return;
  articleMasterState.selectionMode = false;
  articleMasterState.selectedIds = new Set();
  renderArticleMasterTable();
  if (!silent) nexoraToast('Selection cancelled', 'info');
}

function toggleArticleMasterSelection(articleId, checked) {
  const id = Number(articleId);
  if (!Number.isFinite(id)) return;
  if (checked) articleMasterState.selectedIds.add(id);
  else articleMasterState.selectedIds.delete(id);
  updateArticleMasterSelectionChrome(articleMasterState.detailRowsCache);
  const selectAll = document.getElementById('am-select-all');
  if (selectAll) {
    const rows = articleMasterState.detailRowsCache || [];
    const visibleIds = rows.map((a) => Number(a.id)).filter((n) => Number.isFinite(n));
    const selectedVisible = visibleIds.filter((vid) => articleMasterState.selectedIds.has(vid));
    selectAll.checked = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;
    selectAll.indeterminate =
      selectedVisible.length > 0 && selectedVisible.length < visibleIds.length;
  }
  const cb = document.querySelector(`.am-row-select[data-am-id="${id}"]`);
  if (cb) cb.checked = articleMasterState.selectedIds.has(id);
}

function toggleArticleMasterSelectAll(checked) {
  const rows = articleMasterState.detailRowsCache || getFilteredArticleMasterRows();
  rows.forEach((a) => {
    const id = Number(a.id);
    if (!Number.isFinite(id)) return;
    if (checked) articleMasterState.selectedIds.add(id);
    else articleMasterState.selectedIds.delete(id);
  });
  renderArticleMasterTable();
}

/** Choice modal: returns 'selected' | 'all' | null */
function showArticleMasterDeleteChoiceModal() {
  return new Promise((resolve) => {
    const ui = nxThemeUi();
    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 100002;';
    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 420px; width: 90%; color: ${ui.boxFg};`;
    box.innerHTML =
      `<div style="font-size:16px; font-weight:600; margin-bottom:10px; color:${ui.accent};">Delete articles</div>` +
      `<div style="font-size:13px; color:${ui.muted}; margin-bottom:18px; line-height:1.5;">` +
      `Choose whether to pick specific articles or remove everything in the current filter.</div>` +
      `<div style="display:flex; flex-direction:column; gap:10px;">` +
      `<button type="button" id="am-del-choice-selected" class="btn btn-primary" style="width:100%; background:${ui.accent}; border-color:${ui.accent}; color:${ui.accentFg};">Delete selected</button>` +
      `<button type="button" id="am-del-choice-all" class="btn btn-secondary" style="width:100%; background:${ui.secondaryBg}; border-color:${ui.secondaryBorder}; color:${ui.boxFg};">Delete all</button>` +
      `<button type="button" id="am-del-choice-cancel" class="btn btn-secondary" style="width:100%; background:transparent; border-color:${ui.secondaryBorder}; color:${ui.muted};">Cancel</button>` +
      `</div>`;
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    const cleanup = (val) => {
      document.removeEventListener('keydown', onKey);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') cleanup(null);
    };
    box.querySelector('#am-del-choice-selected').addEventListener('click', () => cleanup('selected'));
    box.querySelector('#am-del-choice-all').addEventListener('click', () => cleanup('all'));
    box.querySelector('#am-del-choice-cancel').addEventListener('click', () => cleanup(null));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(null); });
    document.addEventListener('keydown', onKey);
  });
}

async function onArticleMasterDeleteClick() {
  if (articleMasterState.selectionMode) {
    await deleteSelectedArticleMaster();
    return;
  }
  const choice = await showArticleMasterDeleteChoiceModal();
  if (choice === 'selected') {
    enterArticleMasterSelectionMode();
  } else if (choice === 'all') {
    await deleteAllArticleMaster();
  }
}

async function deleteSelectedArticleMaster() {
  const ids = [...articleMasterState.selectedIds];
  const resultEl = document.getElementById('am-upload-result');
  if (!ids.length) {
    nexoraToast('Select at least one article to delete.', 'warn');
    return;
  }
  const ok = await showSimpleConfirmModal(
    'Delete selected articles?',
    `<strong style="color:#f87171;">Warning:</strong> This permanently hard-deletes ` +
      `${ids.length} selected article${ids.length === 1 ? '' : 's'}. This cannot be undone.`,
    'Delete selected',
    'Cancel'
  );
  if (!ok) return;

  try {
    const response = await fetchWithAuth('/api/v1/article-master/delete-selected', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Delete selected failed'));
    }
    if (resultEl) {
      resultEl.textContent = `Permanently deleted ${data.deleted || 0} selected article${(data.deleted || 0) === 1 ? '' : 's'}.`;
    }
    exitArticleMasterSelectionMode({ silent: true });
    await loadArticleMasterList();
  } catch (error) {
    alert(error.message || 'Delete selected failed');
  }
}

async function deleteAllArticleMaster() {
  const category = document.getElementById('am-category-filter')?.value || 'All';
  const matching = (articleMasterState.articles || []).filter((a) =>
    category === 'All' ? true : a.category === category,
  );
  const resultEl = document.getElementById('am-upload-result');

  if (!matching.length) {
    const emptyMsg =
      category === 'All'
        ? 'Nothing to delete. There are no articles in Article Master.'
        : `Nothing to delete. There are no articles in the "${category}" category.`;
    if (resultEl) resultEl.textContent = emptyMsg;
    await showSimpleConfirmModal('Nothing to delete', emptyMsg, 'OK', 'Close');
    return;
  }

  const scope = category === 'All'
    ? `all ${matching.length} articles (every category)`
    : `all ${matching.length} articles in the "${category}" category`;
  const ok = await showSimpleConfirmModal(
    'Delete all articles?',
    `<strong style="color:#f87171;">Warning:</strong> This permanently hard-deletes ${scope}. This cannot be undone.`,
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
    if (resultEl) {
      resultEl.textContent = `Permanently deleted ${data.deleted || 0} articles (${data.category}).`;
    }
    exitArticleMasterSelectionMode({ silent: true });
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
      `<p style="margin-top:10px;">Keeps the oldest row and applies the <strong>latest prices</strong>. Brand aliases (Bluemen/Bluman→Blumen) are applied.</p></div>`,
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

function openArticleMasterFullEdit(articleId, fallbackArticle) {
  const idNum = Number(articleId);
  let article = articleMasterState.articles.find((a) => Number(a.id) === idNum);
  if (!article && fallbackArticle && typeof fallbackArticle === 'object') {
    article = { ...fallbackArticle, id: fallbackArticle.id ?? idNum };
    if (!articleMasterState.articles.some((a) => Number(a.id) === Number(article.id))) {
      articleMasterState.articles.push(article);
    }
  }
  if (!article) return;
  articleMasterState.editArticleId = Number(article.id);

  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val ?? '';
  };
  const labelEl = document.getElementById('am-full-edit-label');
  if (labelEl) {
    labelEl.textContent = article.item_key ? `Item: ${article.item_key}` : `Article ID ${article.id}`;
  }
  setVal('am-full-edit-brand', article.brand);
  setVal('am-full-edit-size', formatArticleSizeDisplay(article.size) || article.size);
  setVal('am-full-edit-product', article.product_type);
  setVal('am-full-edit-mrp', formatArticleMasterMoneyInput(article.mrp, 'mrp'));
  setVal('am-full-edit-ptr', formatArticleMasterMoneyInput(article.ptr, 'ptr'));
  setVal('am-full-edit-exmill', formatArticleMasterMoneyInput(article.ex_mill_price, 'ex_mill_price'));
  setVal('am-full-edit-bale', article.bale_pack_size);
  toggleModal('am-full-edit-modal', true);
}

/** Global search → Article Master item card (same as contact card). */
async function openArticleMasterFromSearch(index) {
  const row = partyDetailRecordsCache?.[index];
  if (!row || row.id == null) return;
  try {
    closeModal('global-search-modal');
  } catch (e) { /* ignore */ }
  openModule('ArticleMaster');
  const idNum = Number(row.id);
  try {
    const found = articleMasterState.articles.some((a) => Number(a.id) === idNum);
    if (!found) {
      await loadArticleMasterList();
    }
  } catch (e) {
    /* still try opening with search row */
  }
  const article = articleMasterState.articles.find((a) => Number(a.id) === idNum) || row;
  showArticleDetail(article);
}

function collectArticleMasterFullEditUpdates(article) {
  const numOrNull = (raw) => {
    const s = String(raw ?? '').trim();
    if (!s) return null;
    const n = Number(s);
    return Number.isFinite(n) ? n : s;
  };
  const money2 = (raw) => {
    const n = numOrNull(raw);
    if (typeof n !== 'number') return n;
    return Math.round(n * 100) / 100;
  };
  const moneyMrp = (raw) => {
    const n = numOrNull(raw);
    if (typeof n !== 'number') return n;
    return Math.round(n);
  };
  const updates = {
    brand: document.getElementById('am-full-edit-brand')?.value?.trim() || null,
    size: document.getElementById('am-full-edit-size')?.value?.trim() || null,
    product_type: document.getElementById('am-full-edit-product')?.value?.trim() || null,
    mrp: moneyMrp(document.getElementById('am-full-edit-mrp')?.value),
    ptr: money2(document.getElementById('am-full-edit-ptr')?.value),
    ex_mill_price: money2(document.getElementById('am-full-edit-exmill')?.value),
    bale_pack_size: numOrNull(document.getElementById('am-full-edit-bale')?.value),
  };
  const changed = {};
  Object.entries(updates).forEach(([key, val]) => {
    const oldVal = article[key];
    let oldCmp = oldVal;
    let newCmp = val;
    if (['ptr', 'ex_mill_price'].includes(key)) {
      const o = Number(oldVal);
      const n = Number(val);
      if (Number.isFinite(o) && Number.isFinite(n) && Math.abs(o - n) < 0.005) return;
      oldCmp = Number.isFinite(o) ? o.toFixed(2) : oldVal;
      newCmp = Number.isFinite(n) ? n.toFixed(2) : val;
    } else if (key === 'mrp') {
      const o = Number(oldVal);
      const n = Number(val);
      if (Number.isFinite(o) && Number.isFinite(n) && Math.round(o) === Math.round(n)) return;
    }
    const oldStr = oldCmp == null ? '' : String(oldCmp);
    const newStr = newCmp == null ? '' : String(newCmp);
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

function formatPriceHistoryValue(value, field) {
  if (value === null || value === undefined || value === '') return '—';
  const text = String(value).trim();
  if (!text) return '—';
  const num = Number(text);
  if (!Number.isFinite(num)) return text;
  const key = String(field || '').toLowerCase();
  if (key === 'mrp') return String(Math.round(num));
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function openArticleMasterPriceHistory(articleId) {
  const article = articleMasterState.articles.find((a) => a.id === articleId);
  const labelEl = document.getElementById('am-price-history-label');
  const tbody = document.getElementById('am-price-history-tbody');
  if (labelEl) {
    labelEl.textContent = article
      ? formatArticleMasterHeading(article)
      : `Article #${articleId}`;
  }
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
        <td>${formatPriceHistoryValue(h.old_value, h.field_changed)}</td>
        <td>${formatPriceHistoryValue(h.new_value, h.field_changed)}</td>
        <td>${h.changed_by ?? '—'}</td>
        <td>${h.changed_at ? new Date(h.changed_at).toLocaleString() : '—'}</td>
      </tr>
    `).join('');
  } catch (error) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="5">${error.message || 'Failed to load history'}</td></tr>`;
  }
}

function showArticleMasterDownloadModal() {
  return new Promise((resolve) => {
    const categories = ['All', 'Bed', 'Bath', 'TOB', 'Pillow'];
    const ui = nxThemeUi();

    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';

    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 380px; width: 90%; ` +
      `box-shadow: 0 12px 40px rgba(0,0,0,0.28); font-family: inherit; color: ${ui.boxFg};`;

    box.innerHTML =
      `<div style="font-size:16px; font-weight:600; margin-bottom:6px; color:${ui.accent};">What would you like to download?</div>` +
      `<div style="font-size:13px; color:${ui.muted}; margin-bottom:16px; line-height:1.5;">Choose a category, or All for the full Article Master.</div>` +
      '<div id="am-download-cat-btns" style="display:flex; flex-direction:column; gap:8px; margin-bottom:14px;"></div>' +
      `<div id="am-download-cancel-btn" style="text-align:center; padding:8px; border-radius:8px; cursor:pointer; color:${ui.secondaryFg}; border:1px solid ${ui.secondaryBorder}; background:${ui.secondaryBg};">Cancel</div>`;

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
        `background:${isAll ? ui.accent : ui.secondaryBg}; ` +
        `color:${isAll ? ui.accentFg : ui.secondaryFg}; ` +
        `border:1px solid ${isAll ? 'transparent' : ui.secondaryBorder};`;
      if (!isAll) {
        btn.addEventListener('mouseenter', () => { btn.style.borderColor = ui.accent; btn.style.color = ui.accent; });
        btn.addEventListener('mouseleave', () => { btn.style.borderColor = ui.secondaryBorder; btn.style.color = ui.secondaryFg; });
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

const ofSavedOrdersState = {
  grouped: [],
  selectedDistributorKey: '',
  selectedOrderId: null,
  expandedDistributorKeys: {},
  expandedSeasonKeys: {},
};

function orderDeskSeasonLabel(season) {
  const s = String(season || '').trim();
  return s || 'Others';
}

function formatOrderDeskQtyValue(qty, value) {
  const parts = [];
  const q = Number(qty || 0);
  const v = Number(value || 0);
  if (q > 0) parts.push(`${Math.round(q)} pcs`);
  if (v > 0) parts.push(formatFilledOrderAmount(v));
  return parts.join(' · ');
}

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
    return { total: 0, matched: 0, unmatched: 0, rejected: 0, added: 0, flagged: 0, baleMismatch: 0 };
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
  const baleMismatch = preview.allItems.filter((it) => it.bale_qty_mismatch).length;
  return { total, matched, unmatched, rejected, flagged, added, baleMismatch };
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
  // UI shell removed — keep engine callable for later rebuild.
  if (!document.getElementById('fo-orders-tbody') && !document.getElementById('fo-upload-file')) {
    return;
  }
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
    if (data.warning && !parts.length) {
      if (resultEl) {
        resultEl.textContent =
          `"${file.name}" — ${data.warning} Select category/distributor manually only if this is a real distributor Excel (Brand + Size), not an SO Pack export.`;
      }
      return;
    }
    if (!parts.length) {
      if (resultEl) {
        resultEl.textContent = `"${file.name}" ready — could not detect distributor/category. Please select manually.`;
      }
      return;
    }
    if (resultEl) {
      if (data.warning) {
        resultEl.textContent =
          `${parts.join(' | ')} — but file did not parse as a filled order: ${data.warning}`;
      } else {
        resultEl.textContent = `${parts.join(' | ')} — click Upload to continue.`;
      }
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

async function openOfFilledOrdersListModal() {
  const modal = document.getElementById('of-fo-list-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  await loadFilledOrdersList();
}

function _getFilledOrderDistributorName(order) {
  const byId = filledOrdersState.distributors.find((d) => String(d.id) === String(order.distributor_id));
  return byId ? getFilledOrderDistributorLabel(byId) : (order.distributor_name_raw || 'Unknown distributor');
}

function _buildOfSavedDistributorGroups() {
  const map = new Map();
  (filledOrdersState.orders || []).forEach((order) => {
    const idPart = order.distributor_id != null ? `id:${order.distributor_id}` : 'id:none';
    const rawName = (order.distributor_name_raw || '').trim().toLowerCase();
    const key = `${idPart}|name:${rawName}`;
    if (!map.has(key)) {
      map.set(key, {
        key,
        distributorName: _getFilledOrderDistributorName(order),
        orders: [],
      });
    }
    map.get(key).orders.push(order);
  });
  return Array.from(map.values()).map((group) => {
    const orders = group.orders
      .slice()
      .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    const seasonMap = new Map();
    orders.forEach((order) => {
      const label = orderDeskSeasonLabel(order.season);
      if (!seasonMap.has(label)) {
        seasonMap.set(label, { label, orders: [], totalQty: 0, totalValue: 0 });
      }
      const bucket = seasonMap.get(label);
      bucket.orders.push(order);
      bucket.totalQty += Number(order.total_piece_qty || 0);
      bucket.totalValue += Number(order.total_ex_mill_value || 0);
    });
    const seasons = Array.from(seasonMap.values())
      .map((s) => ({
        ...s,
        orders: s.orders.sort((a, b) =>
          String(b.created_at || '').localeCompare(String(a.created_at || ''))
        ),
      }))
      .sort((a, b) => {
        const aOther = a.label === 'Others' ? 1 : 0;
        const bOther = b.label === 'Others' ? 1 : 0;
        if (aOther !== bOther) return aOther - bOther;
        const aDate = a.orders[0]?.created_at || '';
        const bDate = b.orders[0]?.created_at || '';
        return String(bDate).localeCompare(String(aDate)) || a.label.localeCompare(b.label);
      });
    const totalQty = orders.reduce((sum, o) => sum + Number(o.total_piece_qty || 0), 0);
    const totalValue = orders.reduce((sum, o) => sum + Number(o.total_ex_mill_value || 0), 0);
    return {
      ...group,
      orders,
      seasons,
      latest: orders[0] || null,
      totalQty,
      totalValue,
    };
  }).sort((a, b) => a.distributorName.localeCompare(b.distributorName));
}

function renderOfSavedDistributorRail() {
  const host = document.getElementById('of-saved-distributor-list');
  if (!host) return;
  const groups = ofSavedOrdersState.grouped;
  if (!groups.length) {
    host.innerHTML = '<p class="nx-text-faint" style="margin:8px 4px;">No saved orders yet.</p>';
    return;
  }
  host.innerHTML = groups.map((group) => {
    const active = group.key === ofSavedOrdersState.selectedDistributorKey;
    const expanded = !!ofSavedOrdersState.expandedDistributorKeys[group.key];
    const latest = group.latest || {};
    const latestHint = [orderDeskSeasonLabel(latest.season), latest.category, (latest.created_at || '').slice(0, 10)]
      .filter(Boolean)
      .join(' · ');
    const metrics = formatOrderDeskQtyValue(group.totalQty, group.totalValue);
    const seasonHtml = expanded
      ? `<div class="of-tree-seasons">${(group.seasons || []).map((season) => {
          const seasonKey = `${group.key}||${season.label}`;
          const seasonOpen = !!ofSavedOrdersState.expandedSeasonKeys[seasonKey];
          const seasonMetrics = formatOrderDeskQtyValue(season.totalQty, season.totalValue);
          const ordersHtml = seasonOpen
            ? `<div class="of-tree-orders">${season.orders.map((o) => {
                const selected = Number(ofSavedOrdersState.selectedOrderId) === Number(o.id);
                const orderMetrics = formatOrderDeskQtyValue(o.total_piece_qty, o.total_ex_mill_value);
                const title = [o.category, o.source_filename].filter(Boolean).join(' · ') || `Order #${o.id}`;
                const hint = [orderDeskSeasonLabel(o.season), (o.created_at || '').slice(0, 10), orderMetrics]
                  .filter(Boolean)
                  .join(' · ');
                return `
                  <button type="button"
                    class="of-tree-order-btn ${selected ? 'is-active' : ''}"
                    data-saved-order-id="${Number(o.id)}">
                    <span class="of-rail-label">${foEscapeText(title)}</span>
                    <span class="of-rail-hint">${foEscapeText(hint)}</span>
                  </button>`;
              }).join('')}</div>`
            : '';
          const seasonHint = [season.orders.length > 1 ? `${season.orders.length} orders` : '', seasonMetrics]
            .filter(Boolean)
            .join(' · ');
          return `
            <div class="of-tree-season ${seasonOpen ? 'is-open' : ''}">
              <button type="button" class="of-tree-season-btn" data-saved-season-key="${encodeURIComponent(seasonKey)}">
                <span class="of-tree-folder-ico">📁</span>
                <span class="of-rail-text">
                  <span class="of-rail-label">${foEscapeText(season.label)}</span>
                  <span class="of-rail-hint">${foEscapeText(seasonHint)}</span>
                </span>
              </button>
              ${ordersHtml}
            </div>`;
        }).join('')}</div>`
      : '';
    const distHint = [latestHint, group.orders.length > 1 ? `${group.orders.length} orders` : '', metrics]
      .filter(Boolean)
      .join(' · ');
    return `
      <div class="of-tree-dist ${expanded ? 'is-open' : ''} ${active ? 'is-active' : ''}">
        <button type="button" class="of-rail-item of-saved-distributor-btn ${active ? 'is-active' : ''}" data-saved-distributor-key="${encodeURIComponent(group.key)}">
          <span class="of-rail-text">
            <span class="of-rail-label">${foEscapeText(group.distributorName)}</span>
            <span class="of-rail-hint">${foEscapeText(distHint)}</span>
          </span>
        </button>
        ${seasonHtml}
      </div>
    `;
  }).join('');
  host.querySelectorAll('.of-saved-distributor-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = decodeURIComponent(btn.getAttribute('data-saved-distributor-key') || '');
      if (key) toggleOfSavedDistributor(key);
    });
  });
  host.querySelectorAll('.of-tree-season-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = decodeURIComponent(btn.getAttribute('data-saved-season-key') || '');
      if (!key) return;
      ofSavedOrdersState.expandedSeasonKeys[key] = !ofSavedOrdersState.expandedSeasonKeys[key];
      renderOfSavedDistributorRail();
    });
  });
  host.querySelectorAll('.of-tree-order-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const orderId = Number(btn.getAttribute('data-saved-order-id') || 0);
      if (orderId) selectOfSavedOrder(orderId);
    });
  });
}

async function toggleOfSavedDistributor(distributorKey) {
  const wasOpen = !!ofSavedOrdersState.expandedDistributorKeys[distributorKey];
  ofSavedOrdersState.expandedDistributorKeys[distributorKey] = !wasOpen;
  if (!wasOpen) {
    await selectOfSavedDistributor(distributorKey);
  } else {
    renderOfSavedDistributorRail();
  }
}

async function selectOfSavedOrder(orderId) {
  if (!_isOfSavedOrdersOpen()) {
    setOfRailMode('saved');
    showOfSection('saved-orders');
  }
  const group = ofSavedOrdersState.grouped.find((g) =>
    (g.orders || []).some((o) => Number(o.id) === Number(orderId))
  );
  if (group) {
    ofSavedOrdersState.selectedDistributorKey = group.key;
    ofSavedOrdersState.expandedDistributorKeys[group.key] = true;
    const order = (group.orders || []).find((o) => Number(o.id) === Number(orderId));
    const seasonKey = `${group.key}||${orderDeskSeasonLabel(order && order.season)}`;
    ofSavedOrdersState.expandedSeasonKeys[seasonKey] = true;
  }
  ofSavedOrdersState.selectedOrderId = Number(orderId);
  renderOfSavedDistributorRail();
  renderOfSavedOrderPicker();
  await loadOfSavedOrderDetail(orderId);
}

function markOfSavedDistributorActive(distributorKey) {
  document.querySelectorAll('#of-saved-distributor-list .of-saved-distributor-btn').forEach((btn) => {
    const key = decodeURIComponent(btn.getAttribute('data-saved-distributor-key') || '');
    btn.classList.toggle('is-active', key === distributorKey);
  });
}

function renderOfSavedOrderPicker() {
  const picker = document.getElementById('of-saved-order-pick');
  const title = document.getElementById('of-saved-distributor-title');
  if (!picker || !title) return;
  const group = ofSavedOrdersState.grouped.find((g) => g.key === ofSavedOrdersState.selectedDistributorKey);
  if (!group) {
    title.textContent = 'Select a distributor';
    title.title = '';
    picker.innerHTML = '<option value="">Select saved order</option>';
    return;
  }
  title.textContent = group.distributorName;
  title.title = `${group.distributorName} · ${group.orders.length} saved order${group.orders.length === 1 ? '' : 's'}`;
  picker.innerHTML = group.orders.map((o) => (
    `<option value="${o.id}" ${String(o.id) === String(ofSavedOrdersState.selectedOrderId) ? 'selected' : ''}>` +
    `${o.category || '—'} · ${o.season || '—'} · ${(o.created_at || '').slice(0, 10)}` +
    '</option>'
  )).join('');
}

function renderOfSavedStats(fo, itemCount) {
  const meta = document.getElementById('of-saved-detail-meta');
  if (!meta) return;
  if (!fo) {
    meta.textContent = 'Select a distributor from the left.';
    return;
  }
  const chips = [
    ['of-saved-chip', fo.category || '—'],
    ['of-saved-chip', fo.season || '—'],
    ['of-saved-chip', `${itemCount ?? fo.total_lines ?? 0} lines`],
    ['of-saved-chip', `${formatFilledOrderQty(fo.total_bales)} bales`],
    ['of-saved-chip', `${formatFilledOrderQty(fo.total_piece_qty)} pcs`],
    ['of-saved-chip of-saved-chip--accent', `Ex-mill ${formatFilledOrderAmount(fo.total_ex_mill_value)}`],
  ];
  meta.innerHTML = chips.map(([cls, text]) => `<span class="${cls}">${text}</span>`).join('');
}

function renderOfSavedOrderDetailRows(items) {
  const tbody = document.getElementById('of-saved-detail-tbody');
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="12">No line items for this order.</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((it) => {
    const flag = it.bale_qty_mismatch
      ? '<span class="of-saved-flag">⚠ bales</span>'
      : (it.is_clean_bale_multiple ? '' : '<span class="of-saved-flag">🚩</span>');
    const matched = it.matched
      ? '<span class="of-saved-ok" title="Matched">✓</span>'
      : '<span class="of-saved-no" title="Unmatched">✗</span>';
    return `<tr>
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
      <td>${matched}</td>
      <td>${flag}</td>
    </tr>`;
  }).join('');
}

async function loadOfSavedOrderDetail(orderId) {
  const tbody = document.getElementById('of-saved-detail-tbody');
  const meta = document.getElementById('of-saved-detail-meta');
  if (tbody) tbody.innerHTML = '<tr><td colspan="12">Loading order details...</td></tr>';
  if (meta) meta.textContent = 'Loading…';
  try {
    const response = await fetchWithAuth(`/api/v1/filled-orders/${orderId}`);
    const data = await parseApiResponse(response);
    if (!response.ok) {
      throw new Error(getApiErrorMessage(data, 'Failed to load order detail'));
    }
    const fo = data.filled_order || {};
    const items = data.items || [];
    renderOfSavedStats(fo, items.length);
    renderOfSavedOrderDetailRows(items);
  } catch (error) {
    if (meta) meta.textContent = error.message || 'Could not load order detail.';
    if (tbody) tbody.innerHTML = `<tr><td colspan="12">${error.message || 'Failed to load detail.'}</td></tr>`;
  }
}

function onOfSavedOrderPickChanged(orderId) {
  if (!orderId) {
    const tbody = document.getElementById('of-saved-detail-tbody');
    renderOfSavedStats(null);
    if (tbody) tbody.innerHTML = '<tr><td colspan="12">Select saved order.</td></tr>';
    return;
  }
  ofSavedOrdersState.selectedOrderId = Number(orderId);
  loadOfSavedOrderDetail(ofSavedOrdersState.selectedOrderId);
}

function _isOfSavedOrdersOpen() {
  const ofScreen = document.querySelector('#order-fulfillment-workspace .nx-of-vyapar');
  return !!(ofScreen && ofScreen.classList.contains('is-saved-orders'));
}

async function selectOfSavedDistributor(distributorKey) {
  // Do not re-enter workspace on every click — that was causing layout jumps.
  if (!_isOfSavedOrdersOpen()) {
    setOfRailMode('saved');
    showOfSection('saved-orders');
  }
  ofSavedOrdersState.selectedDistributorKey = distributorKey;
  ofSavedOrdersState.expandedDistributorKeys[distributorKey] = true;
  const group = ofSavedOrdersState.grouped.find((g) => g.key === distributorKey);
  const latest = group?.latest || group?.orders?.[0] || null;
  ofSavedOrdersState.selectedOrderId = latest?.id || null;
  if (latest) {
    const seasonKey = `${distributorKey}||${orderDeskSeasonLabel(latest.season)}`;
    ofSavedOrdersState.expandedSeasonKeys[seasonKey] = true;
  }
  renderOfSavedDistributorRail();
  renderOfSavedOrderPicker();
  if (ofSavedOrdersState.selectedOrderId) {
    await loadOfSavedOrderDetail(ofSavedOrdersState.selectedOrderId);
  } else {
    const tbody = document.getElementById('of-saved-detail-tbody');
    renderOfSavedStats(null);
    if (tbody) tbody.innerHTML = '<tr><td colspan="12">No saved order under this distributor.</td></tr>';
  }
}

async function openOfSavedOrdersWorkspace() {
  const tbody = document.getElementById('of-saved-detail-tbody');
  const meta = document.getElementById('of-saved-detail-meta');
  if (meta) meta.textContent = 'Loading saved orders...';
  if (tbody) tbody.innerHTML = '<tr><td colspan="12">Loading saved orders...</td></tr>';

  await loadFilledOrdersList();
  ofSavedOrdersState.grouped = _buildOfSavedDistributorGroups();
  const firstKey = ofSavedOrdersState.grouped[0]?.key || '';
  setOfRailMode('saved');
  showOfSection('saved-orders');
  renderOfSavedDistributorRail();

  if (firstKey) {
    await selectOfSavedDistributor(firstKey);
  } else {
    ofSavedOrdersState.selectedDistributorKey = '';
    ofSavedOrdersState.selectedOrderId = null;
    renderOfSavedOrderPicker();
    if (meta) meta.textContent = 'No saved filled orders yet.';
    if (tbody) tbody.innerHTML = '<tr><td colspan="12">No saved filled orders yet.</td></tr>';
  }
}

function exitOfSavedOrdersWorkspace() {
  setOfRailMode('default');
  showOfSection('filled-order');
}

async function deleteOfSavedSelectedOrder() {
  const orderId = ofSavedOrdersState.selectedOrderId;
  if (!orderId) {
    await showSimpleConfirmModal('No order selected', 'Select a saved order first.', 'OK', 'Close');
    return;
  }
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
    const prevKey = ofSavedOrdersState.selectedDistributorKey;
    await loadFilledOrdersList();
    if (document.getElementById('of-fo-summary')) {
      await loadOrderFulfillmentCatalogSummary();
    }
    ofSavedOrdersState.grouped = _buildOfSavedDistributorGroups();
    // Always rebuild the left rail — otherwise deleted distributors stay visible.
    renderOfSavedDistributorRail();

    if (!ofSavedOrdersState.grouped.length) {
      ofSavedOrdersState.selectedDistributorKey = '';
      ofSavedOrdersState.selectedOrderId = null;
      renderOfSavedOrderPicker();
      const title = document.getElementById('of-saved-distributor-title');
      const tbody = document.getElementById('of-saved-detail-tbody');
      if (title) {
        title.textContent = 'No saved orders';
        title.title = '';
      }
      renderOfSavedStats(null);
      if (tbody) tbody.innerHTML = '<tr><td colspan="12">No saved filled orders yet.</td></tr>';
      if (typeof nexoraToast === 'function') nexoraToast('Saved order deleted', 'success');
      return;
    }

    const sameGroup = ofSavedOrdersState.grouped.find((g) => g.key === prevKey);
    const nextKey = sameGroup ? prevKey : ofSavedOrdersState.grouped[0].key;
    ofSavedOrdersState.selectedDistributorKey = '';
    // Force a full select (rail already rendered) so detail + picker refresh.
    await selectOfSavedDistributor(nextKey);
    if (typeof nexoraToast === 'function') nexoraToast('Saved order deleted', 'success');
  } catch (error) {
    await showSimpleConfirmModal('Delete failed', error.message || 'Could not delete this order.', 'OK', 'Close');
  }
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
    if (authState.role === 'sales_executive') {
      loadFilledOrdersSeasonWidgets();
    }
  } catch (error) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="14">${error.message || 'Failed to load'}</td></tr>`;
  }
}

function formatFilledOrderComparisonStatus(status) {
  const ui = nxThemeUi();
  const map = {
    match: `<span style="color:${ui.ok}; font-weight:600;">Match</span>`,
    mismatch: `<span style="color:${ui.down}; font-weight:600;">Mismatch</span>`,
    missing_in_file: `<span style="color:${ui.warn}; font-weight:600;">Missing in file</span>`,
    missing_in_master: `<span style="color:${ui.warn}; font-weight:600;">Missing in Article Master</span>`,
    both_empty: `<span style="color:${ui.muted};">Empty</span>`,
  };
  return map[status] || status;
}

function renderFilledOrderMismatchIssueCard(it) {
  const ui = nxThemeUi();
  const previewKey = getFilledOrderPreviewItemKey(it);
  const label = [
    formatFilledOrderValue(it.brand),
    formatFilledOrderValue(it.size),
    formatFilledOrderValue(it.product_type),
  ].filter((v) => v && v !== '—').join(' · ') || 'Unknown line';
  const lineNo = it.line_number != null ? `Line ${it.line_number}` : 'Line ?';

  const comparisons = (it.field_comparisons || []).map((c) => (
    '<tr>' +
    `<td style="padding:6px 8px; color:${ui.muted};">${c.field}</td>` +
    `<td style="padding:6px 8px; color:${ui.boxFg};">${formatFilledOrderValue(c.file_value)}</td>` +
    `<td style="padding:6px 8px; color:${ui.boxFg};">${formatFilledOrderValue(c.master_value)}</td>` +
    `<td style="padding:6px 8px;">${formatFilledOrderComparisonStatus(c.status)}</td>` +
    '</tr>'
  )).join('');

  const comparisonTable = comparisons
    ? (
      `<table style="width:100%; border-collapse:collapse; font-size:12px; margin-top:10px; color:${ui.boxFg};">` +
      `<thead><tr style="color:${ui.muted}; border-bottom:1px solid ${ui.rowBorder};">` +
      '<th style="text-align:left; padding:6px 8px;">Field</th>' +
      '<th style="text-align:left; padding:6px 8px;">In file</th>' +
      '<th style="text-align:left; padding:6px 8px;">Article Master</th>' +
      '<th style="text-align:left; padding:6px 8px;">Status</th>' +
      '</tr></thead><tbody>' + comparisons + '</tbody></table>'
    )
    : `<div style="color:${ui.down}; font-size:12px; margin-top:8px;">${it.issue_summary || 'Not found in Article Master'}</div>`;

  const hint = it.suggestion
    ? `<div style="color:${ui.muted}; font-size:12px; margin-top:8px; line-height:1.45;">Tip: ${it.suggestion}</div>`
    : '';
  const qtyRow = it.final_piece_qty != null
    ? `<div style="font-size:12px; color:${ui.muted}; margin-top:8px;">Qty: ${formatFilledOrderQty(it.raw_qty_value)} ${it.detected_unit || ''} → ${formatFilledOrderQty(it.final_piece_qty)} pcs</div>`
    : '';

  return (
    `<div class="fo-mismatch-card" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" ` +
    `style="border:1px solid ${ui.boxBorder}; border-radius:10px; padding:14px; margin-bottom:12px; background:${ui.secondaryBg}; color:${ui.boxFg};">` +
    `<div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between;">` +
    `<div><span style="color:${ui.muted}; font-size:12px;">${lineNo}</span> <strong style="font-size:14px; color:${ui.boxFg};">${label}</strong></div>` +
    `<div style="display:flex; gap:8px; flex-wrap:wrap;">` +
    `<button type="button" class="btn btn-primary fo-mismatch-add-btn" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" style="padding:6px 12px; font-size:12px; background:${ui.accent}; border-color:${ui.accent}; color:${ui.accentFg};">Add to Article Master</button>` +
    `<button type="button" class="btn btn-danger fo-mismatch-reject-btn" data-preview-key="${previewKey.replace(/"/g, '&quot;')}" style="padding:6px 12px; font-size:12px; background:#b42318; border:1px solid #7f1d1d; color:#fff; opacity:1; pointer-events:auto;">Exclude line</button>` +
    `</div></div>` +
    comparisonTable + qtyRow + hint +
    `</div>`
  );
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
    const ui = nxThemeUi();
    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 100002;';
    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 440px; width: 90%; color: ${ui.boxFg};`;
    box.innerHTML =
      `<div style="font-size:16px; font-weight:600; margin-bottom:10px; color:${ui.accent};">${title}</div>` +
      `<div style="font-size:13px; color:${ui.muted}; margin-bottom:18px; line-height:1.5;">${message}</div>` +
      `<div style="display:flex; gap:10px;">` +
      `<button id="scm-yes" class="btn btn-primary" style="flex:1; background:${ui.accent}; border-color:${ui.accent}; color:${ui.accentFg};">${yesText}</button>` +
      `<button id="scm-no" class="btn btn-secondary" style="flex:1; background:${ui.secondaryBg}; border-color:${ui.secondaryBorder}; color:${ui.boxFg};">${noText}</button>` +
      `</div>`;
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    const cleanup = (val) => {
      document.removeEventListener('keydown', onKey);
      if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') cleanup(false);
    };
    box.querySelector('#scm-yes').addEventListener('click', () => cleanup(true));
    box.querySelector('#scm-no').addEventListener('click', () => cleanup(false));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(false); });
    document.addEventListener('keydown', onKey);
  });
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
    'Exclude this line?',
    `Is line ko is filled order se <strong>hata denge</strong> jab aap Save karoge.<br><br>` +
      `<strong>${label}</strong><br><br>` +
      `Abhi order save nahi hoga — sirf yeh line exclude list mein jayegi.`,
    'Yes, exclude line',
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
    const ui = nxThemeUi();
    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 100001;';

    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 1100px; width: 96%; max-height: 94vh; display:flex; flex-direction:column; ` +
      `box-shadow: 0 12px 40px rgba(0,0,0,0.28); font-family: inherit; color: ${ui.boxFg};`;

    const listEl = document.createElement('div');
    listEl.style.cssText = 'flex:1 1 auto; min-height:0; overflow:auto; margin:14px 0; padding-right:4px;';

    function renderList() {
      const items = getFilledOrderPendingUnmatchedItems();
      const preview = filledOrdersState.pendingPreview;
      const keyFields = (preview?.keyFields || []).join(', ');
      box.querySelector('.fo-mismatch-header').innerHTML =
        `<h2 style="margin:0 0 8px; color:${ui.accent};">Unmatched lines — review</h2>` +
        `<p style="margin:0; color:${ui.muted}; font-size:13px;">${items.length} line(s) remaining. ` +
        `Match keys: ${keyFields || 'brand, size'}. Scroll to review each line.</p>` +
        (preview?.addedKeys.size
          ? `<p style="margin:8px 0 0; color:#7fdc7f; font-size:12px;">${preview.addedKeys.size} added to Article Master.</p>`
          : '') +
        (preview?.rejectedKeys.size
          ? `<p style="margin:4px 0 0; color:#f87171; font-size:12px;">${preview.rejectedKeys.size} line(s) excluded — order abhi save nahi hua; Save pe yeh lines skip hongi.</p>`
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

function downloadFilledOrderBaleMismatchCsv(data) {
  const items = (data.bale_mismatch_items && data.bale_mismatch_items.length)
    ? data.bale_mismatch_items
    : (data.all_items || []).filter((it) => it.bale_qty_mismatch);
  if (!items.length) {
    nxNotify('No bale mismatches to download.', 'info');
    return;
  }
  const headers = [
    'Brand', 'Size', 'Qty (used)', 'Sheet bales', 'Expected bales (Qty/Bale Size)',
    'Bale size', 'Difference', 'Issue',
  ];
  const lines = [headers.join(',')];
  for (const it of items) {
    const expected = it.expected_bales;
    const sheet = it.sheet_bales;
    const diff = (sheet != null && expected != null) ? (sheet - expected) : '';
    const row = [
      it.brand || '',
      it.size || '',
      it.final_piece_qty ?? '',
      sheet ?? '',
      expected ?? '',
      it.bale_size_used ?? '',
      diff,
      (it.bale_mismatch_detail || '').replace(/"/g, '""'),
    ].map((v) => `"${v}"`);
    lines.push(row.join(','));
  }
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `bale_mismatch_${(data.distributor_name || 'order').replace(/\s+/g, '_')}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function showFilledOrderSaveConfirmModal(data) {
  return new Promise((resolve) => {
    initFilledOrderPendingPreview(data);
    const existing = data.existing_order || null;
    const ui = nxThemeUi();
    const mismatchItems = data.bale_mismatch_items
      || (data.all_items || []).filter((it) => it.bale_qty_mismatch)
      || [];

    const overlay = document.createElement('div');
    overlay.style.cssText =
      `position: fixed; inset: 0; background: ${ui.overlay}; ` +
      'display: flex; align-items: center; justify-content: center; z-index: 99999;';

    const box = document.createElement('div');
    box.style.cssText =
      `background: ${ui.boxBg}; border: 1px solid ${ui.boxBorder}; border-radius: 12px; ` +
      `padding: 24px; max-width: 720px; width: 94%; max-height: 88vh; overflow: auto; ` +
      `box-shadow: 0 12px 40px rgba(0,0,0,0.28); font-family: inherit; color: ${ui.boxFg};`;

    const statsHost = document.createElement('div');
    statsHost.id = 'fo-save-stats-host';

    function renderStats() {
      const s = getFilledOrderPendingSaveStats();
      const checkBtn = s.unmatched > 0
        ? `<button type="button" id="fo-check-unmatched-btn" class="btn btn-primary" style="padding:4px 10px; font-size:12px; margin-left:10px; background:${ui.accent}; border-color:${ui.accent}; color:${ui.accentFg};">Check</button>`
        : '';
      const rows = [
        ['Total lines', s.total],
        ['Matched', s.matched],
        ['Unmatched', `${s.unmatched}${checkBtn}`],
        ['Excluded', s.rejected],
        ['Added to AM', s.added],
        ['Flagged (pack)', s.flagged],
        ['Bale mismatches', s.baleMismatch],
        ['Qty column', data.quantity_column_used || '—'],
        ['Bales column', data.bales_column_used || '—'],
        ['Unit', data.quantity_unit_used || '—'],
        ['Category', data.category || '—'],
        ['Season', data.season || '—'],
      ];
      statsHost.innerHTML = rows.map(([label, value]) => (
        `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid ${ui.rowBorder};">` +
        `<span style="color:${ui.muted};">${label}</span>` +
        `<span style="color:${ui.boxFg}; font-weight:600;">${value}</span>` +
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
      `<div style="font-size:16px; font-weight:600; margin-bottom:6px; color:${ui.accent};">` +
      (existing ? 'Replace existing filled order?' : 'Save filled order?') +
      '</div>' +
      (existing
        ? `<div style="font-size:13px; color:#b45309; margin-bottom:10px; line-height:1.5; padding:10px; border:1px solid color-mix(in srgb, ${ui.accent} 45%, transparent); border-radius:8px; background:color-mix(in srgb, ${ui.accent} 10%, transparent);">
            <strong>Duplicate detected.</strong> ${data.distributor_name || 'This distributor'} already has a
            <strong>${existing.category}</strong> order for season <strong>${existing.season}</strong>
            (${existing.total_lines ?? 0} lines, uploaded ${(existing.created_at || '').slice(0, 10)}).
            Saving will replace that order.
          </div>`
        : '') +
      (mismatchItems.length
        ? `<div style="font-size:13px; color:#991b1b; margin-bottom:12px; line-height:1.5; padding:10px 12px; border:1px solid #fecaca; border-radius:8px; background:#fef2f2;">
            <strong>Bale mismatch: ${mismatchItems.length} line(s).</strong>
            Order will use <strong>Qty</strong> (not silently fix bales).
            Ask the distributor to correct sheet bales, or continue with Qty.
          </div>`
        : '') +
      `<div style="font-size:13px; color:${ui.muted}; margin-bottom:14px; line-height:1.5;">Review summary below. ` +
      'If unmatched &gt; 0, click <strong>Check</strong> to review details before saving.</div>';

    const placeholder = document.createElement('div');
    placeholder.id = 'fo-save-stats-host';
    box.appendChild(placeholder);
    box.appendChild(statsHost);
    placeholder.replaceWith(statsHost);

    if (mismatchItems.length) {
      const tableWrap = document.createElement('div');
      tableWrap.style.cssText = 'margin:12px 0 8px; overflow:auto; max-height:220px; border:1px solid #fecaca; border-radius:8px;';
      const rowsHtml = mismatchItems.slice(0, 50).map((it) => {
        const expected = it.expected_bales != null ? Number(it.expected_bales).toFixed(2) : '—';
        const sheet = it.sheet_bales != null ? it.sheet_bales : '—';
        const diff = (it.sheet_bales != null && it.expected_bales != null)
          ? (it.sheet_bales - it.expected_bales)
          : '';
        return `<tr style="background:rgba(220,50,50,0.12);">
          <td style="padding:6px 8px;">${it.brand || ''}</td>
          <td style="padding:6px 8px;">${it.size || ''}</td>
          <td style="padding:6px 8px; text-align:right;">${formatFilledOrderQty(it.final_piece_qty)}</td>
          <td style="padding:6px 8px; text-align:right;">${sheet}</td>
          <td style="padding:6px 8px; text-align:right;">${expected}</td>
          <td style="padding:6px 8px; text-align:right;">${diff !== '' ? Number(diff).toFixed(2) : '—'}</td>
        </tr>`;
      }).join('');
      tableWrap.innerHTML =
        `<table style="width:100%; border-collapse:collapse; font-size:12px;">
          <thead><tr style="background:#450a0a; color:#fecaca; text-align:left;">
            <th style="padding:6px 8px;">Brand</th>
            <th style="padding:6px 8px;">Size</th>
            <th style="padding:6px 8px; text-align:right;">Qty</th>
            <th style="padding:6px 8px; text-align:right;">Sheet bales</th>
            <th style="padding:6px 8px; text-align:right;">Expected</th>
            <th style="padding:6px 8px; text-align:right;">Diff</th>
          </tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>`;
      box.appendChild(tableWrap);

      const dlBtn = document.createElement('div');
      dlBtn.style.cssText =
        `text-align:center; padding:8px; border-radius:8px; cursor:pointer; margin:8px 0 4px; ` +
        `color:${ui.boxFg}; border:1px solid ${ui.boxBorder}; background:${ui.secondaryBg}; font-size:13px; font-weight:600;`;
      dlBtn.textContent = 'Download mismatch list (CSV)';
      dlBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        downloadFilledOrderBaleMismatchCsv(data);
      });
      box.appendChild(dlBtn);
    }

    const confirmBtn = document.createElement('div');
    confirmBtn.id = 'fo-save-confirm-btn';
    confirmBtn.style.cssText = `background:${ui.accent}; color:${ui.accentFg}; text-align:center; padding:10px; border-radius:8px; cursor:pointer; font-weight:600; margin:16px 0 10px;`;
    confirmBtn.textContent = mismatchItems.length
      ? (existing ? 'Continue with Qty — replace order' : 'Continue with Qty — save order')
      : (existing ? 'Replace existing order' : 'Save filled order');
    const cancelBtn = document.createElement('div');
    cancelBtn.id = 'fo-save-cancel-btn';
    cancelBtn.style.cssText = `text-align:center; padding:8px; border-radius:8px; cursor:pointer; color:#f87171; border:1px solid #5c2b2b; background:${ui.secondaryBg};`;
    cancelBtn.textContent = 'Cancel — do not save';
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

    const candidates = data.candidates || [];
    const recommended = candidates.find((c) => c.kind === 'pieces')
      || candidates.find((c) => /^(qty|qnty|quantity)$/i.test(String(c.column_label || '').trim()))
      || candidates[0];

    const rows = candidates.map((c) => {
      const rel = relationships.find((r) => r.sum_column_index === c.column_index);
      const note = rel ? `<div style="color:#7fdc7f;font-size:0.8rem;margin-top:4px;">✓ ${rel.note}</div>` : '';
      const isBales = c.kind === 'bales' || /bale/i.test(String(c.column_label || ''));
      const badge = isBales
        ? '<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;background:#5b3a1a;color:#fbbf24;font-size:0.72rem;font-weight:600;">BALES — packing count</span>'
        : '<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:999px;background:#14532d;color:#86efac;font-size:0.72rem;font-weight:600;">PIECES — order Qty</span>';
      const hint = c.hint
        || (isBales
          ? 'Yeh bale count hai (kitne packs). Order quantity ke liye yeh mat chunein.'
          : 'Yeh piece / order quantity hai — normally yeh select karein.');
      const sampleLabel = isBales ? 'Sample bales' : 'Sample pieces';
      const checked = recommended && c.column_label === recommended.column_label ? ' checked' : '';
      const border = isBales ? '#5b3a1a' : '#3a3a44';
      return `
        <label style="display:block; border:1px solid ${border}; border-radius:8px; padding:12px; margin-bottom:8px; cursor:pointer;">
          <input type="radio" name="fo-qty-col-choice" value="${String(c.column_label).replace(/"/g, '&quot;')}" style="margin-right:8px;"${checked} />
          <strong>${c.column_label}</strong>${badge}
          <div style="font-size:0.82rem; color:#cbd5e1; margin-top:6px;">${hint}</div>
          <div style="font-size:0.78rem; color:#94a3b8; margin-top:4px;">${sampleLabel}: ${(c.sample_values || []).join(', ')} · ${c.populated_count} rows</div>
          ${note}
        </label>
      `;
    }).join('');

    const guidance = data.guidance
      || 'Excel mein Quantity ke multiple columns mil sake. <strong>Pieces / Qty</strong> chunein — '
        + '<strong>No of Bales</strong> nahi. Bales system alag se Qty ke against check karta hai.';

    box.innerHTML = `
      <h2 style="margin-top:0;">Which column is Order Quantity?</h2>
      <p style="color:#cbd5e1; line-height:1.45; margin:0 0 14px;">${guidance}</p>
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
      if (!checked) {
        if (typeof nexoraToast === 'function') nexoraToast('Please select a column.', 'warn');
        else alert('Please select a column.');
        return;
      }
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
        if (resultEl) {
          resultEl.textContent =
            'Upload cancelled — nothing new was saved. Earlier filled orders (list below) are unchanged.';
        }
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
      // Always refresh dashboard season widgets (even if upload was from Order Desk).
      if (authState.role === 'sales_executive') {
        loadFilledOrdersSeasonWidgets();
      }
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
    const rowStyle = (it.is_clean_bale_multiple && !it.bale_qty_mismatch)
      ? ''
      : 'background: rgba(220,50,50,0.18);';
    const flag = it.bale_qty_mismatch
      ? '⚠️ bales'
      : (it.is_clean_bale_multiple ? '' : '🚩');
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
        <td>${flag}</td>
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
      <td>${flag}</td>
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
  const scroller = document.querySelector('#fo-detail-modal .fo-detail-table-scroll');
  if (tbody) tbody.innerHTML = '<tr><td colspan="13">Loading...</td></tr>';
  if (scroller) {
    scroller.scrollLeft = 0;
    scroller.scrollTop = 0;
  }
  closeModal('of-fo-list-modal');
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
    const savedWs = document.getElementById('of-saved-workspace');
    if (savedWs && savedWs.classList.contains('is-open')) {
      ofSavedOrdersState.grouped = _buildOfSavedDistributorGroups();
      renderOfSavedDistributorRail();
      if (!ofSavedOrdersState.grouped.length) {
        ofSavedOrdersState.selectedDistributorKey = '';
        ofSavedOrdersState.selectedOrderId = null;
        renderOfSavedOrderPicker();
        const title = document.getElementById('of-saved-distributor-title');
        const tbody = document.getElementById('of-saved-detail-tbody');
        if (title) {
          title.textContent = 'No saved orders';
          title.title = '';
        }
        renderOfSavedStats(null);
        if (tbody) tbody.innerHTML = '<tr><td colspan="12">No saved filled orders yet.</td></tr>';
      } else {
        const key = ofSavedOrdersState.selectedDistributorKey || ofSavedOrdersState.grouped[0].key;
        const stillThere = ofSavedOrdersState.grouped.find((g) => g.key === key);
        ofSavedOrdersState.selectedDistributorKey = '';
        await selectOfSavedDistributor(stillThere ? key : ofSavedOrdersState.grouped[0].key);
      }
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
  if (backdrop) {
    backdrop.hidden = true;
    backdrop.setAttribute('aria-hidden', 'true');
  }
}

function openMobileNav() {
  document.body.classList.add('mobile-nav-open');
  const toggle = document.getElementById('mobile-nav-toggle');
  const backdrop = document.getElementById('mobile-nav-backdrop');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');
  if (backdrop) {
    backdrop.hidden = false;
    backdrop.setAttribute('aria-hidden', 'false');
  }
  bindMobileNavDismissGestures();
}

function toggleMobileNav() {
  if (document.body.classList.contains('mobile-nav-open')) closeMobileNav();
  else openMobileNav();
}

/** Outside tap (backdrop) + left swipe closes the drawer. */
function bindMobileNavDismissGestures() {
  if (window.__mobileNavGesturesBound) return;
  window.__mobileNavGesturesBound = true;

  let startX = 0;
  let startY = 0;
  let tracking = false;

  document.addEventListener('touchstart', (e) => {
    if (!document.body.classList.contains('mobile-nav-open')) return;
    const t = e.changedTouches && e.changedTouches[0];
    if (!t) return;
    startX = t.clientX;
    startY = t.clientY;
    tracking = true;
  }, { passive: true });

  document.addEventListener('touchend', (e) => {
    if (!tracking || !document.body.classList.contains('mobile-nav-open')) {
      tracking = false;
      return;
    }
    tracking = false;
    const t = e.changedTouches && e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - startX;
    const dy = t.clientY - startY;
    // Left swipe (ignore mostly-vertical scrolls in the menu list)
    if (dx < -56 && Math.abs(dx) > Math.abs(dy) * 1.15) {
      closeMobileNav();
    }
  }, { passive: true });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.classList.contains('mobile-nav-open')) {
      closeMobileNav();
    }
  });
}

document.addEventListener('DOMContentLoaded', initApp);

