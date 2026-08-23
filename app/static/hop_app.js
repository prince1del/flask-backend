/* House of Prizm UI — project-centric ERP shell (hop_admin only). */

const hopState = {
  view: 'dashboard',
  viewHistory: [],
  customers: [],
  projects: [],
  leads: [],
  deals: [],
  dealDetail: null,
  meetings: [],
  vendors: [],
  quotations: [],
  orders: [],
  invoices: [],
  hub: null,
  hubTab: 'overview',
  reloadTimers: {},
  search: {},
  rateMatrix: null,
  rateCart: [],
  rateFilters: { q: '' },
  vyaparBackupFile: null,
  vyaparImportPreview: null,
  contactSelect: {
    customers: { mode: false, ids: [] },
    vendors: { mode: false, ids: [] },
  },
  contactEdit: null,
  invoiceUi: {
    period: 'this_month',
    from: '',
    to: '',
    status: 'all',
    q: '',
    party: '',
  },
};

const HOP_RATE_CART_KEY = 'hop_rate_cart_v1';
const HOP_THEME_KEY = 'hop_theme_v1';
const HOP_CUSTOM_COLORS_KEY = 'hop_theme_custom_v1';

/** Per-login theme silo — never share North Head / HoP / other users on same browser. */
function hopThemeStorageScope() {
  try {
    if (typeof authState !== 'undefined' && authState) {
      if (authState.userId != null && Number.isFinite(Number(authState.userId))) {
        return `id_${Number(authState.userId)}`;
      }
      const uname = String(authState.username || '').trim().toLowerCase();
      if (uname) return `name_${uname.replace(/[^a-z0-9_-]/g, '_').slice(0, 48)}`;
    }
  } catch (e) { /* ignore */ }
  return 'anon';
}

function hopThemeStorageKey() {
  return `${HOP_THEME_KEY}__${hopThemeStorageScope()}`;
}

function hopCustomColorsStorageKey() {
  return `${HOP_CUSTOM_COLORS_KEY}__${hopThemeStorageScope()}`;
}

function hopMigrateLegacyThemeOnce() {
  const scope = hopThemeStorageScope();
  if (scope === 'anon') return;
  try {
    if (localStorage.getItem(hopThemeStorageKey())) return;
    const legacyTheme = localStorage.getItem(HOP_THEME_KEY);
    if (!legacyTheme) return;
    localStorage.setItem(hopThemeStorageKey(), legacyTheme);
    const legacyColors = localStorage.getItem(HOP_CUSTOM_COLORS_KEY);
    if (legacyColors && !localStorage.getItem(hopCustomColorsStorageKey())) {
      localStorage.setItem(hopCustomColorsStorageKey(), legacyColors);
    }
    // Drop shared keys so the next login cannot inherit this theme.
    localStorage.removeItem(HOP_THEME_KEY);
    localStorage.removeItem(HOP_CUSTOM_COLORS_KEY);
  } catch (e) { /* ignore */ }
}

const HOP_THEMES = {
  bright: { id: 'bright', label: 'Bright theme on', color: '#f4f7fb' },
  emerald: { id: 'emerald', label: 'Emerald Gold theme on', color: '#F8F4EA' },
  custom: { id: 'custom', label: 'Custom theme on', color: '#F8F4EA' },
};

const HOP_DEFAULT_THEME = 'emerald';

function hopNormalizeThemeId(t) {
  return t;
}

const HOP_CUSTOM_DEFAULTS = {
  sidebar: '#123C32',
  bg: '#F8F4EA',
  text: '#1F1F1F',
  accent: '#C9A227',
  border: '#DED3BE',
  card: '#FFFFFF',
  muted: '#6B6254',
};

/** Luxury palettes — reuse custom CSS vars (data-hop-theme="custom"). */
const HOP_LUXURY_THEMES = {
  royal_navy: {
    id: 'royal_navy',
    title: 'Royal Navy & Champagne',
    label: 'Royal Navy theme on',
    desc: 'Royal, premium, professional — hospitality & large project clients.',
    chip: 'Top pick',
    colors: {
      sidebar: '#0B1F3A', bg: '#F8F4EA', text: '#0B1F3A',
      accent: '#C6A15B', border: '#B8B1A7', card: '#FFFFFF', muted: '#6E675F',
    },
  },
  burgundy_antique: {
    id: 'burgundy_antique',
    title: 'Burgundy & Antique Gold',
    label: 'Burgundy theme on',
    desc: 'Rich, warm luxury — curtains, fabrics & premium interiors.',
    colors: {
      sidebar: '#5A1828', bg: '#FFF8ED', text: '#262324',
      accent: '#B9975B', border: '#E2D2C0', card: '#FFFFFF', muted: '#7A6458',
    },
  },
  black_soft_gold: {
    id: 'black_soft_gold',
    title: 'Black & Soft Gold',
    label: 'Black & Soft Gold on',
    desc: 'Modern, bold, high-end — minimal luxury.',
    colors: {
      sidebar: '#171717', bg: '#F5F2EB', text: '#171717',
      accent: '#C5A059', border: '#A69B8D', card: '#FFFFFF', muted: '#6F675C',
    },
  },
  deep_teal_brass: {
    id: 'deep_teal_brass',
    title: 'Deep Teal & Brass',
    label: 'Deep Teal theme on',
    desc: 'Contemporary & sophisticated — furnishing & wallcovering.',
    colors: {
      sidebar: '#0D3B3E', bg: '#FAF7F0', text: '#1A2A2B',
      accent: '#B68D40', border: '#E8DDCB', card: '#FFFFFF', muted: '#6B6558',
    },
  },
  chocolate_gold: {
    id: 'chocolate_gold',
    title: 'Chocolate & Caramel Gold',
    label: 'Chocolate Gold theme on',
    desc: 'Warm, earthy & expensive — upholstery, leatherette, hotels.',
    chip: 'Top pick',
    colors: {
      sidebar: '#3A251C', bg: '#FFF9F0', text: '#3A251C',
      accent: '#C49346', border: '#E5D4BE', card: '#FFFFFF', muted: '#7A6550',
    },
  },
  plum_rose: {
    id: 'plum_rose',
    title: 'Plum & Rose Gold',
    label: 'Plum & Rose Gold on',
    desc: 'Soft luxury with a fashionable feel — residential & designer.',
    colors: {
      sidebar: '#48263B', bg: '#FAF6F1', text: '#2C1A24',
      accent: '#B98276', border: '#EBDCD4', card: '#FFFFFF', muted: '#7A645C',
    },
  },
  olive_brass: {
    id: 'olive_brass',
    title: 'Olive & Antique Brass',
    label: 'Olive Brass theme on',
    desc: 'Natural, elegant, timeless — fabrics, wallpapers, sustainable.',
    colors: {
      sidebar: '#3F4934', bg: '#E8E0D1', text: '#302A24',
      accent: '#B09150', border: '#D4CBB8', card: '#F7F3EA', muted: '#6A6356',
    },
  },
  midnight_copper: {
    id: 'midnight_copper',
    title: 'Midnight Blue & Copper',
    label: 'Midnight Copper theme on',
    desc: 'Distinctive & modern — premium look beyond typical gold.',
    colors: {
      sidebar: '#101B2D', bg: '#F7F5F0', text: '#101B2D',
      accent: '#B8734C', border: '#C5C8CE', card: '#FFFFFF', muted: '#626872',
    },
  },
};

const HOP_CUSTOM_PRESETS = {
  emerald: {
    label: 'Emerald + Gold',
    colors: { ...HOP_CUSTOM_DEFAULTS },
  },
  ...Object.fromEntries(
    Object.entries(HOP_LUXURY_THEMES).map(([key, t]) => [key, { label: t.title, colors: { ...t.colors } }]),
  ),
};

function hopIsKnownTheme(t) {
  const core = HOP_THEMES[t];
  if (core && core.retired) return false;
  return Boolean(core || HOP_LUXURY_THEMES[t]);
}

function hopThemeUsesCustomCss(t) {
  return t === 'custom' || Boolean(HOP_LUXURY_THEMES[t]);
}

function hopThemeMeta(t) {
  if (HOP_LUXURY_THEMES[t]) return HOP_LUXURY_THEMES[t];
  return HOP_THEMES[t] || HOP_THEMES.emerald;
}

function hopHexToRgb(hex) {
  const h = String(hex || '').replace('#', '').trim();
  if (h.length === 3) {
    const r = parseInt(h[0] + h[0], 16);
    const g = parseInt(h[1] + h[1], 16);
    const b = parseInt(h[2] + h[2], 16);
    return { r, g, b };
  }
  if (h.length !== 6) return { r: 0, g: 0, b: 0 };
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
  };
}

function hopRgbToHex(r, g, b) {
  const clamp = (n) => Math.max(0, Math.min(255, Math.round(n)));
  return '#' + [clamp(r), clamp(g), clamp(b)].map((n) => n.toString(16).padStart(2, '0')).join('').toUpperCase();
}

function hopMixHex(a, b, t) {
  const A = hopHexToRgb(a);
  const B = hopHexToRgb(b);
  return hopRgbToHex(
    A.r + (B.r - A.r) * t,
    A.g + (B.g - A.g) * t,
    A.b + (B.b - A.b) * t,
  );
}

function hopLuminance(hex) {
  const { r, g, b } = hopHexToRgb(hex);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

function hopNormalizeHex(hex, fallback) {
  const s = String(hex || '').trim();
  if (/^#[0-9A-Fa-f]{6}$/.test(s)) return s.toUpperCase();
  if (/^#[0-9A-Fa-f]{3}$/.test(s)) {
    return ('#' + s[1] + s[1] + s[2] + s[2] + s[3] + s[3]).toUpperCase();
  }
  return fallback;
}

function hopGetCustomColors() {
  hopMigrateLegacyThemeOnce();
  try {
    const raw = JSON.parse(localStorage.getItem(hopCustomColorsStorageKey()) || '{}');
    return {
      sidebar: hopNormalizeHex(raw.sidebar, HOP_CUSTOM_DEFAULTS.sidebar),
      bg: hopNormalizeHex(raw.bg, HOP_CUSTOM_DEFAULTS.bg),
      text: hopNormalizeHex(raw.text, HOP_CUSTOM_DEFAULTS.text),
      accent: hopNormalizeHex(raw.accent, HOP_CUSTOM_DEFAULTS.accent),
      border: hopNormalizeHex(raw.border, HOP_CUSTOM_DEFAULTS.border),
      card: hopNormalizeHex(raw.card, HOP_CUSTOM_DEFAULTS.card),
      muted: hopNormalizeHex(raw.muted, HOP_CUSTOM_DEFAULTS.muted),
    };
  } catch (e) {
    return { ...HOP_CUSTOM_DEFAULTS };
  }
}

function hopSaveCustomColors(colors) {
  const c = {
    sidebar: hopNormalizeHex(colors.sidebar, HOP_CUSTOM_DEFAULTS.sidebar),
    bg: hopNormalizeHex(colors.bg, HOP_CUSTOM_DEFAULTS.bg),
    text: hopNormalizeHex(colors.text, HOP_CUSTOM_DEFAULTS.text),
    accent: hopNormalizeHex(colors.accent, HOP_CUSTOM_DEFAULTS.accent),
    border: hopNormalizeHex(colors.border, HOP_CUSTOM_DEFAULTS.border),
    card: hopNormalizeHex(colors.card, HOP_CUSTOM_DEFAULTS.card),
    muted: hopNormalizeHex(colors.muted, HOP_CUSTOM_DEFAULTS.muted),
  };
  try {
    if (hopThemeStorageScope() !== 'anon') {
      localStorage.setItem(hopCustomColorsStorageKey(), JSON.stringify(c));
    }
  } catch (e) { /* ignore */ }
  return c;
}

function hopApplyCustomVars(colors) {
  const c = colors || hopGetCustomColors();
  const root = document.documentElement;
  const accentDark = hopMixHex(c.accent, '#000000', 0.22);
  const accentLight = hopMixHex(c.accent, '#FFFFFF', 0.45);
  // Near-white backgrounds must stay exact (no border mix → cream cast).
  const bgLum = hopLuminance(c.bg);
  const bgSoft = bgLum >= 0.88
    ? c.bg
    : hopMixHex(c.bg, c.border, 0.22);
  const { r, g, b } = hopHexToRgb(c.accent);
  const sideLum = hopLuminance(c.sidebar);
  const navText = sideLum < 0.45 ? hopMixHex('#FFFFFF', c.bg, 0.08) : hopMixHex(c.text, '#000000', 0.15);
  const navMuted = hopMixHex(navText, c.sidebar, 0.45);

  const vars = {
    '--hop-c-sidebar': c.sidebar,
    '--hop-c-bg': c.bg,
    '--hop-c-bg-soft': bgSoft,
    '--hop-c-text': c.text,
    '--hop-c-muted': c.muted,
    '--hop-c-border': c.border,
    '--hop-c-accent': c.accent,
    '--hop-c-accent-dark': accentDark,
    '--hop-c-accent-light': accentLight,
    '--hop-c-accent-soft': `rgba(${r}, ${g}, ${b}, 0.22)`,
    '--hop-c-accent-glow': `rgba(${r}, ${g}, ${b}, 0.45)`,
    '--hop-c-card': c.card,
    '--hop-c-nav-text': navText,
    '--hop-c-nav-muted': navMuted,
  };
  Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
  // Keep legacy NEXORA accent tokens in sync so buttons/logos follow custom/luxury colors
  root.style.setProperty('--nx-gold', c.accent);
  root.style.setProperty('--nx-gold-2', accentDark);
  root.style.setProperty('--nxt-cyan', c.accent);
  root.style.setProperty('--nxt-cyan-dim', `rgba(${r}, ${g}, ${b}, 0.16)`);
  HOP_THEMES.custom.color = c.bg;
  return c;
}

function hopClearCustomVars() {
  [
    '--hop-c-sidebar', '--hop-c-bg', '--hop-c-bg-soft', '--hop-c-text', '--hop-c-muted',
    '--hop-c-border', '--hop-c-accent', '--hop-c-accent-dark', '--hop-c-accent-light',
    '--hop-c-accent-soft', '--hop-c-accent-glow', '--hop-c-card', '--hop-c-nav-text', '--hop-c-nav-muted',
    '--nx-gold', '--nx-gold-2', '--nxt-cyan', '--nxt-cyan-dim',
  ].forEach((k) => document.documentElement.style.removeProperty(k));
}

function hopGetTheme() {
  hopMigrateLegacyThemeOnce();
  try {
    const raw = localStorage.getItem(hopThemeStorageKey()) || HOP_DEFAULT_THEME;
    const t = hopNormalizeThemeId(raw);
    if (!hopIsKnownTheme(t)) {
      try { localStorage.setItem(hopThemeStorageKey(), HOP_DEFAULT_THEME); } catch (e2) { /* ignore */ }
      return HOP_DEFAULT_THEME;
    }
    return t;
  } catch (e) {
    return HOP_DEFAULT_THEME;
  }
}

function hopApplyTheme(theme, opts) {
  const normalized = hopNormalizeThemeId(theme);
  const t = hopIsKnownTheme(normalized) ? normalized : HOP_DEFAULT_THEME;
  const previewOnly = Boolean(opts && opts.previewOnly);

  if (previewOnly) {
    hopThemeLivePreview = {
      theme: t,
      colors: HOP_LUXURY_THEMES[t]
        ? { ...HOP_LUXURY_THEMES[t].colors }
        : (t === 'custom' ? { ...hopGetCustomColors() } : null),
    };
  } else {
    hopThemeLivePreview = null;
    // Never persist theme against anon — wait until a real login is in authState.
    if (hopThemeStorageScope() !== 'anon') {
      try { localStorage.setItem(hopThemeStorageKey(), t); } catch (e) { /* ignore */ }
    }
    if (HOP_LUXURY_THEMES[t]) {
      hopSaveCustomColors(HOP_LUXURY_THEMES[t].colors);
    }
  }

  if (previewOnly && HOP_LUXURY_THEMES[t]) {
    hopApplyCustomVars(HOP_LUXURY_THEMES[t].colors);
  }

  const cssTheme = hopThemeUsesCustomCss(t) ? 'custom' : t;
  document.documentElement.setAttribute('data-hop-theme', cssTheme);
  document.body.setAttribute('data-hop-theme', cssTheme);
  const dash = document.getElementById('dashboard');
  if (dash) dash.setAttribute('data-hop-theme', cssTheme);
  const ws = document.getElementById('hop-executive-workspace');
  if (ws) ws.setAttribute('data-hop-theme', cssTheme);
  document.querySelectorAll('.nx-theme').forEach((el) => el.setAttribute('data-hop-theme', cssTheme));

  if (!previewOnly) {
    if (hopThemeUsesCustomCss(t)) hopApplyCustomVars();
    else hopClearCustomVars();
  } else if (!HOP_LUXURY_THEMES[t] && !hopThemeUsesCustomCss(t)) {
    hopClearCustomVars();
  } else if (t === 'custom') {
    hopApplyCustomVars(hopThemeLivePreview?.colors || hopGetCustomColors());
  }

  const meta = document.getElementById('hop-theme-color-meta')
    || document.querySelector('meta[name="theme-color"]');
  const metaInfo = hopThemeMeta(t);
  const themeColor = hopThemeUsesCustomCss(t)
    ? (hopThemeLivePreview?.colors?.bg || hopGetCustomColors().bg)
    : (metaInfo.color || '#F8F4EA');
  if (meta) meta.setAttribute('content', themeColor);
  if (!(opts && opts.silent) && typeof nexoraToast === 'function') {
    nexoraToast(metaInfo.label || 'Theme on', 'ok');
  }
  if (!(opts && opts.skipRerender) && hopState.view === 'theme') {
    const mount = hopMount();
    if (mount) renderHopThemeModule(mount);
  }
  if (!(opts && opts.skipRerender) && typeof refreshBdSettingsThemeMount === 'function') {
    refreshBdSettingsThemeMount();
  }
  // Cloud-backed per-user theme — only when committing (Apply), never on live preview
  if (!previewOnly && !(opts && opts.skipPersistRemote) && hopThemeStorageScope() !== 'anon') {
    hopPersistThemeToServer(t);
  }
  hopUpdateThemeApplyBar();
}

let hopThemeRemoteTimer = null;

function hopPersistThemeToServer(themeId) {
  if (typeof fetchWithAuth !== 'function') return;
  const payload = {
    theme: themeId,
    custom_colors: hopThemeUsesCustomCss(themeId) ? hopGetCustomColors() : null,
  };
  clearTimeout(hopThemeRemoteTimer);
  hopThemeRemoteTimer = setTimeout(() => {
    fetchWithAuth('/api/v1/me/ui-theme', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => { /* offline / auth — local cache still holds this login's theme */ });
  }, 400);
}

async function hopPullThemeFromServer() {
  if (typeof fetchWithAuth !== 'function') return null;
  if (hopThemeStorageScope() === 'anon') return null;
  try {
    const res = await fetchWithAuth('/api/v1/me/ui-theme');
    const data = await res.json();
    if (!res.ok || !data.success || !data.data) return null;
    return data.data;
  } catch (e) {
    return null;
  }
}

function hopApplyServerThemePayload(prefs, opts) {
  if (!prefs || !prefs.theme) return false;
  if (prefs.custom_colors && typeof prefs.custom_colors === 'object') {
    hopSaveCustomColors(prefs.custom_colors);
  }
  const themeId = hopNormalizeThemeId(prefs.theme);
  hopApplyTheme(themeId, Object.assign({ silent: true, skipRerender: true, skipPersistRemote: true }, opts || {}));
  return true;
}

function hopThemeDisplayName(themeId) {
  const id = hopNormalizeThemeId(themeId);
  const m = hopThemeMeta(id);
  if (id === 'bright') return 'Bright';
  if (id === 'emerald') return 'Emerald Gold';
  if (id === 'custom') return 'Custom';
  if (HOP_LUXURY_THEMES[id]) return m.title || id;
  return m.label || id;
}

/** Baseline when user opened Theme page — used to confirm on leave. */
let hopThemePageBaseline = null;
let hopThemeLeavePending = null;
/** Live browse preview — not saved until Apply. */
let hopThemeLivePreview = null;

function hopGetDisplayedTheme() {
  if (hopThemeLivePreview && hopIsKnownTheme(hopThemeLivePreview.theme)) {
    return hopThemeLivePreview.theme;
  }
  return hopGetTheme();
}

function hopCaptureThemeBaseline() {
  hopThemeLivePreview = null;
  hopThemePageBaseline = {
    theme: hopGetTheme(),
    colors: { ...hopGetCustomColors() },
  };
  hopUpdateThemeApplyBar();
}

function hopThemePageDirty() {
  if (!hopThemePageBaseline) {
    return Boolean(hopThemeLivePreview);
  }
  const cur = hopGetDisplayedTheme();
  if (cur !== hopThemePageBaseline.theme) return true;
  if (!hopThemeUsesCustomCss(cur)) return false;
  const c = hopThemeLivePreview?.colors || hopGetCustomColors();
  const b = hopThemePageBaseline.colors || {};
  return ['sidebar', 'bg', 'text', 'accent', 'border', 'card', 'muted']
    .some((k) => String(c[k] || '').toUpperCase() !== String(b[k] || '').toUpperCase());
}

function hopUpdateThemeApplyBar() {
  const btn = document.getElementById('hop-theme-apply-btn');
  const hint = document.getElementById('hop-theme-apply-hint');
  const dirty = hopThemePageDirty();
  if (btn) {
    btn.disabled = !dirty;
    btn.classList.toggle('is-ready', dirty);
  }
  if (hint) {
    if (!dirty) {
      hint.textContent = 'Pick a theme to preview — Apply saves it for this login only.';
    } else {
      hint.textContent = `${hopThemeDisplayName(hopGetDisplayedTheme())} is previewing. Click Apply to save for this login.`;
    }
  }
}

/** Commit the live preview (or current theme) to this login — local + server. */
function hopCommitThemeApply() {
  const themeId = hopGetDisplayedTheme();
  const previewColors = hopThemeLivePreview?.colors;
  if (previewColors && hopThemeUsesCustomCss(themeId)) {
    hopSaveCustomColors(previewColors);
  }
  hopThemeLivePreview = null;
  hopApplyTheme(themeId, { silent: true, skipRerender: false });
  hopThemePageBaseline = {
    theme: themeId,
    colors: { ...hopGetCustomColors() },
  };
  hopPlayThemeSetAnimation(themeId, () => {
    if (typeof nexoraToast === 'function') {
      nexoraToast(`${hopThemeDisplayName(themeId)} applied`, 'ok');
    }
  });
  hopUpdateThemeApplyBar();
}

function hopCloseThemeConfirm() {
  const el = document.getElementById('hop-theme-confirm-overlay');
  if (el) el.remove();
  hopThemeLeavePending = null;
}

/** Live-preview a theme while browsing — save only via Apply. */
function hopRequestTheme(themeId) {
  if (!hopIsKnownTheme(themeId) || themeId === 'custom') return;
  hopApplyTheme(themeId, { silent: true, previewOnly: true });
}

function hopShowThemeLeaveOverlay(pending) {
  const themeId = hopGetDisplayedTheme();
  const name = hopThemeDisplayName(themeId);
  const prevName = hopThemeDisplayName(hopThemePageBaseline?.theme || hopGetTheme() || HOP_DEFAULT_THEME);
  hopThemeLeavePending = pending || null;
  const existing = document.getElementById('hop-theme-confirm-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'hop-theme-confirm-overlay';
  overlay.className = 'hop-theme-confirm-overlay is-open';
  overlay.innerHTML = `
    <div class="hop-theme-confirm-backdrop" onclick="hopStayOnThemePage()"></div>
    <div class="hop-theme-confirm-dialog" role="dialog" aria-modal="true" aria-label="Unapplied theme">
      <p class="hop-theme-confirm-kicker">Leaving Theme</p>
      <h3>Apply <em>${foEscapeText(name)}</em>?</h3>
      <p class="hop-theme-confirm-copy">
        You previewed a new look but have not applied it yet. Apply to save, or discard and return to
        <strong>${foEscapeText(prevName)}</strong>.
      </p>
      <div class="hop-theme-confirm-actions">
        <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" onclick="hopDiscardThemeAndLeave()">Discard</button>
        <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" onclick="hopStayOnThemePage()">Stay</button>
        <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--primary" onclick="hopKeepThemeAndLeave()">Apply &amp; leave</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

function hopPromptThemeLeave(nextView, opts) {
  hopShowThemeLeaveOverlay({ nextView, opts });
}

/** North Head Settings — same keep/discard flow as HoP Theme page. */
function hopPromptBdThemeLeave(continueFn) {
  hopShowThemeLeaveOverlay({ bdContinue: continueFn });
}

function hopRunThemeLeaveContinue(pending) {
  if (!pending) return;
  if (typeof pending.bdContinue === 'function') {
    pending.bdContinue();
    return;
  }
  if (pending.nextView != null) {
    openHopView(pending.nextView, { ...(pending.opts || {}), skipThemeConfirm: true });
  }
}

function hopStayOnThemePage() {
  const el = document.getElementById('hop-theme-confirm-overlay');
  if (el) el.remove();
  hopThemeLeavePending = null;
}

function hopKeepThemeAndLeave() {
  const pending = hopThemeLeavePending;
  const themeId = hopGetDisplayedTheme();
  const el = document.getElementById('hop-theme-confirm-overlay');
  if (el) el.remove();
  hopThemeLeavePending = null;
  const previewColors = hopThemeLivePreview?.colors;
  if (previewColors && hopThemeUsesCustomCss(themeId)) {
    hopSaveCustomColors(previewColors);
  }
  hopThemeLivePreview = null;
  hopApplyTheme(themeId, { silent: true, skipRerender: true });
  hopThemePageBaseline = null;
  hopPlayThemeSetAnimation(themeId, () => {
    if (typeof nexoraToast === 'function') {
      nexoraToast(`${hopThemeDisplayName(themeId)} applied`, 'ok');
    }
    hopRunThemeLeaveContinue(pending);
  });
}

function hopDiscardThemeAndLeave() {
  const pending = hopThemeLeavePending;
  const el = document.getElementById('hop-theme-confirm-overlay');
  if (el) el.remove();
  hopThemeLeavePending = null;
  hopThemeLivePreview = null;
  if (hopThemePageBaseline) {
    hopSaveCustomColors(hopThemePageBaseline.colors);
    hopApplyTheme(hopThemePageBaseline.theme, { silent: true, skipRerender: true, skipPersistRemote: true });
  }
  hopThemePageBaseline = null;
  hopRunThemeLeaveContinue(pending);
}

function hopPlayThemeSetAnimation(themeId, done) {
  const existing = document.getElementById('hop-theme-set-flash');
  if (existing) existing.remove();
  const name = hopThemeDisplayName(themeId);
  const flash = document.createElement('div');
  flash.id = 'hop-theme-set-flash';
  flash.className = 'hop-theme-set-flash';
  flash.innerHTML = `
    <div class="hop-theme-set-flash-card">
      <span class="hop-theme-set-check" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
      </span>
      <strong>Theme set</strong>
      <span>${foEscapeText(name)}</span>
    </div>`;
  document.body.appendChild(flash);
  requestAnimationFrame(() => flash.classList.add('is-on'));
  window.setTimeout(() => {
    flash.classList.remove('is-on');
    flash.classList.add('is-out');
    window.setTimeout(() => {
      flash.remove();
      if (typeof done === 'function') done();
    }, 220);
  }, 720);
}

function hopInitTheme() {
  hopApplyTheme(hopGetTheme(), { silent: true, skipPersistRemote: true });
}

/** Call after login / auth restore so this account's theme loads (not previous user's). */
async function hopSyncThemeForCurrentUser(loginThemePrefs) {
  // Only trust server when the user actually saved a theme (saved:true).
  // Unsaved default must NOT force retired blue chrome every login.
  if (loginThemePrefs && loginThemePrefs.saved && hopApplyServerThemePayload(loginThemePrefs)) {
    return;
  }
  const remote = await hopPullThemeFromServer();
  if (remote && remote.saved && hopApplyServerThemePayload(remote)) {
    return;
  }
  // No server theme yet — Emerald Gold default, then push so login stays green.
  hopApplyTheme(HOP_DEFAULT_THEME, { silent: true, skipRerender: true });
}

function hopResetThemeChromeToDefault() {
  hopClearCustomVars();
  document.documentElement.setAttribute('data-hop-theme', HOP_DEFAULT_THEME);
  document.body.setAttribute('data-hop-theme', HOP_DEFAULT_THEME);
  const dash = document.getElementById('dashboard');
  if (dash) dash.setAttribute('data-hop-theme', HOP_DEFAULT_THEME);
  const ws = document.getElementById('hop-executive-workspace');
  if (ws) ws.setAttribute('data-hop-theme', HOP_DEFAULT_THEME);
  document.querySelectorAll('.nx-theme').forEach((el) => el.setAttribute('data-hop-theme', HOP_DEFAULT_THEME));
}

function hopReadCustomFormColors() {
  const pick = (id, fallback) => {
    const el = document.getElementById(id);
    return hopNormalizeHex(el && el.value, fallback);
  };
  return {
    sidebar: pick('hop-c-sidebar', HOP_CUSTOM_DEFAULTS.sidebar),
    bg: pick('hop-c-bg', HOP_CUSTOM_DEFAULTS.bg),
    text: pick('hop-c-text', HOP_CUSTOM_DEFAULTS.text),
    accent: pick('hop-c-accent', HOP_CUSTOM_DEFAULTS.accent),
    border: pick('hop-c-border', HOP_CUSTOM_DEFAULTS.border),
    card: pick('hop-c-card', HOP_CUSTOM_DEFAULTS.card),
    muted: pick('hop-c-muted', HOP_CUSTOM_DEFAULTS.muted),
  };
}

let hopThemeStudioSnapshot = null;

function hopPreviewCustomTheme() {
  // Live preview only — colours persist on Apply, not while editing
  const colors = hopReadCustomFormColors();
  hopApplyCustomVars(colors);
  document.documentElement.setAttribute('data-hop-theme', 'custom');
  document.body.setAttribute('data-hop-theme', 'custom');
  const dash = document.getElementById('dashboard');
  if (dash) dash.setAttribute('data-hop-theme', 'custom');
  const ws = document.getElementById('hop-executive-workspace');
  if (ws) ws.setAttribute('data-hop-theme', 'custom');
  document.querySelectorAll('.nx-theme').forEach((el) => el.setAttribute('data-hop-theme', 'custom'));
  const meta = document.getElementById('hop-theme-color-meta');
  if (meta) meta.setAttribute('content', colors.bg);
  const swSide = document.querySelector('.hop-theme-swatch--custom .hop-theme-swatch-side');
  const swMain = document.querySelector('.hop-theme-swatch--custom .hop-theme-swatch-main');
  if (swSide) swSide.style.background = colors.sidebar;
  if (swMain) swMain.style.background = colors.bg;
  const live = document.getElementById('hop-custom-live-frame');
  if (live) {
    live.style.setProperty('--hop-preview-sidebar', colors.sidebar);
    live.style.setProperty('--hop-preview-bg', colors.bg);
    live.style.setProperty('--hop-preview-text', colors.text);
    live.style.setProperty('--hop-preview-accent', colors.accent);
    live.style.setProperty('--hop-preview-border', colors.border);
    live.style.setProperty('--hop-preview-card', colors.card);
    live.style.setProperty('--hop-preview-muted', colors.muted);
  }
}

function hopFillCustomForm(colors) {
  const c = colors || hopGetCustomColors();
  const map = {
    sidebar: 'hop-c-sidebar',
    bg: 'hop-c-bg',
    text: 'hop-c-text',
    accent: 'hop-c-accent',
    border: 'hop-c-border',
    card: 'hop-c-card',
    muted: 'hop-c-muted',
  };
  Object.entries(map).forEach(([key, id]) => {
    const picker = document.getElementById(id);
    const hex = document.getElementById(id + '-hex');
    if (picker) picker.value = c[key];
    if (hex) hex.value = c[key];
  });
}

function hopBindCustomColorSync() {
  ['sidebar', 'bg', 'text', 'accent', 'border', 'card', 'muted'].forEach((k) => {
    const picker = document.getElementById('hop-c-' + k);
    const hex = document.getElementById('hop-c-' + k + '-hex');
    if (!picker || !hex) return;
    picker.addEventListener('input', () => { hex.value = picker.value.toUpperCase(); });
  });
}

function hopRestoreThemeStudioSnapshot() {
  if (!hopThemeStudioSnapshot) return;
  const snap = hopThemeStudioSnapshot;
  hopThemeStudioSnapshot = null;
  hopSaveCustomColors(snap.colors);
  hopApplyTheme(snap.theme || HOP_DEFAULT_THEME, { silent: true, skipRerender: true });
}

function hopCustomStudioMarkup(c) {
  const presetBtns = Object.entries(HOP_CUSTOM_PRESETS).map(([key, p]) => {
    const col = p.colors;
    return `<button type="button" class="hop-custom-preset-btn" onclick="hopLoadCustomPreset('${key}')">
      <span class="hop-custom-preset-swatches" aria-hidden="true">
        <i style="background:${col.sidebar}"></i>
        <i style="background:${col.bg}"></i>
        <i style="background:${col.accent}"></i>
      </span>
      <span class="hop-custom-preset-label">${p.label}</span>
    </button>`;
  }).join('');
  const colorField = (id, label, value) => `
    <label class="hop-custom-color-field">
      <span>${label}</span>
      <span class="hop-custom-color-row">
        <input type="color" id="${id}" value="${value}" oninput="hopPreviewCustomTheme()" />
        <input type="text" id="${id}-hex" value="${value}" maxlength="7" spellcheck="false"
          onchange="document.getElementById('${id}').value=this.value; hopPreviewCustomTheme();"
          oninput="if(/^#[0-9A-Fa-f]{6}$/.test(this.value)){document.getElementById('${id}').value=this.value; hopPreviewCustomTheme();}" />
      </span>
    </label>`;
  return `
    <div class="hop-custom-theme-panel hop-custom-theme-panel--modal">
      <div class="hop-custom-panel-head">
        <div>
          <p class="hop-theme-studio-kicker" style="margin:0 0 4px">Custom</p>
          <h3 class="nx-display">Theme studio</h3>
          <p>Try colours freely — Apply saves for <strong>this login only</strong>. Cancel keeps your current theme. Other users are not affected.</p>
        </div>
        <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" onclick="hopCancelCustomThemeStudio()" aria-label="Cancel">Cancel</button>
      </div>
      <div class="hop-custom-panel-layout">
        <aside class="hop-custom-live" aria-hidden="true">
          <p class="hop-custom-live-label">Live preview</p>
          <div class="hop-custom-live-frame" id="hop-custom-live-frame"
            style="--hop-preview-sidebar:${c.sidebar};--hop-preview-bg:${c.bg};--hop-preview-text:${c.text};--hop-preview-accent:${c.accent};--hop-preview-border:${c.border};--hop-preview-card:${c.card};--hop-preview-muted:${c.muted}">
            <div class="hop-custom-live-nav">
              <span></span><span class="is-active"></span><span></span>
            </div>
            <div class="hop-custom-live-main">
              <div class="hop-custom-live-bar"></div>
              <div class="hop-custom-live-card">
                <i></i><i></i>
                <div class="hop-custom-live-btn"></div>
              </div>
            </div>
          </div>
        </aside>
        <div class="hop-custom-panel-controls">
          <p class="hop-custom-section-label">Start from a preset</p>
          <div class="hop-custom-presets">${presetBtns}</div>
          <p class="hop-custom-section-label">Fine-tune colours</p>
          <div class="hop-custom-color-grid">
            ${colorField('hop-c-sidebar', 'Sidebar / header', c.sidebar)}
            ${colorField('hop-c-bg', 'Background', c.bg)}
            ${colorField('hop-c-text', 'Text', c.text)}
            ${colorField('hop-c-accent', 'Accent', c.accent)}
            ${colorField('hop-c-border', 'Borders', c.border)}
            ${colorField('hop-c-card', 'Cards', c.card)}
            ${colorField('hop-c-muted', 'Secondary text', c.muted)}
          </div>
          <div class="hop-custom-actions">
            <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--primary" onclick="hopApplyCustomThemeFromForm()">Apply</button>
            <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" onclick="hopResetCustomTheme()">Reset</button>
            <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" onclick="hopCancelCustomThemeStudio()">Cancel</button>
          </div>
        </div>
      </div>
    </div>`;
}

function hopOpenCustomThemeStudio() {
  hopThemeStudioSnapshot = {
    theme: hopGetTheme(),
    colors: { ...hopGetCustomColors() },
  };
  const c = hopGetCustomColors();
  hopApplyCustomVars(c);
  document.documentElement.setAttribute('data-hop-theme', 'custom');
  const ws = document.getElementById('hop-executive-workspace');
  if (ws) ws.setAttribute('data-hop-theme', 'custom');
  document.querySelectorAll('.nx-theme.hop-shell').forEach((el) => el.setAttribute('data-hop-theme', 'custom'));
  let overlay = document.getElementById('hop-custom-studio-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'hop-custom-studio-overlay';
    overlay.className = 'hop-custom-studio-overlay';
    document.body.appendChild(overlay);
  }
  overlay.innerHTML = `
    <div class="hop-custom-studio-backdrop" onclick="hopCancelCustomThemeStudio()"></div>
    <div class="hop-custom-studio-dialog" role="dialog" aria-modal="true" aria-label="Theme studio">
      ${hopCustomStudioMarkup(c)}
    </div>`;
  overlay.classList.add('is-open');
  document.body.classList.add('hop-custom-studio-open');
  hopBindCustomColorSync();
  if (!window._hopCustomStudioEsc) {
    window._hopCustomStudioEsc = (e) => {
      if (e.key === 'Escape') hopCancelCustomThemeStudio();
    };
    document.addEventListener('keydown', window._hopCustomStudioEsc);
  }
}

function hopCloseCustomThemeStudio() {
  const overlay = document.getElementById('hop-custom-studio-overlay');
  if (overlay) {
    overlay.classList.remove('is-open');
    overlay.innerHTML = '';
  }
  document.body.classList.remove('hop-custom-studio-open');
  if (window._hopCustomStudioEsc) {
    document.removeEventListener('keydown', window._hopCustomStudioEsc);
    window._hopCustomStudioEsc = null;
  }
  if (hopState.view === 'theme') {
    const mount = hopMount();
    if (mount) renderHopThemeModule(mount);
  }
  if (typeof refreshBdSettingsThemeMount === 'function') {
    refreshBdSettingsThemeMount();
  }
}

function hopCancelCustomThemeStudio() {
  hopRestoreThemeStudioSnapshot();
  hopCloseCustomThemeStudio();
}

function hopApplyCustomThemeFromForm() {
  const colors = hopReadCustomFormColors();
  hopThemeStudioSnapshot = null;
  // Studio "Apply" means save (modal copy: Apply to save) — not another preview step.
  hopSaveCustomColors(colors);
  hopThemeLivePreview = null;
  hopApplyTheme('custom', { silent: true, skipRerender: false });
  hopThemePageBaseline = {
    theme: 'custom',
    colors: { ...hopGetCustomColors() },
  };
  hopCloseCustomThemeStudio();
  hopUpdateThemeApplyBar();
  if (typeof refreshBdSettingsThemeMount === 'function') refreshBdSettingsThemeMount();
  const finish = () => {
    if (typeof nexoraToast === 'function') nexoraToast('Custom look applied for this login', 'ok');
  };
  if (typeof hopPlayThemeSetAnimation === 'function') {
    hopPlayThemeSetAnimation('custom', finish);
  } else {
    finish();
  }
}

function hopLoadCustomPreset(key) {
  const preset = HOP_CUSTOM_PRESETS[key];
  if (!preset) return;
  hopFillCustomForm(preset.colors);
  hopPreviewCustomTheme();
}

function hopResetCustomTheme() {
  hopFillCustomForm(HOP_CUSTOM_DEFAULTS);
  hopPreviewCustomTheme();
}

function hopLoadRateCart() {
  try {
    const raw = localStorage.getItem(HOP_RATE_CART_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    hopState.rateCart = Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    hopState.rateCart = [];
  }
  return hopState.rateCart;
}

function hopSaveRateCart() {
  localStorage.setItem(HOP_RATE_CART_KEY, JSON.stringify(hopState.rateCart || []));
}

function hopRefreshRateMatrixCartButtons() {
  const count = (hopState.rateCart || []).length;
  const summaryEl = document.querySelector('.hop-rate-summary span:last-child');
  if (summaryEl) summaryEl.textContent = `${count} in quote cart`;
  document.querySelectorAll('.hop-rate-add-btn.is-in-cart').forEach((btn) => {
    btn.classList.remove('is-in-cart');
    if (btn.textContent.trim() === 'Added +') btn.textContent = 'Add to cart';
  });
}

async function hopClearRateQuote() {
  hopLoadRateCart();
  if (!(hopState.rateCart || []).length) {
    alert('Quote cart already empty.');
    return;
  }
  if (!(await nexoraConfirm('Clear quote cart? Selected rates will be removed.', {
    title: 'Clear quote cart',
    danger: true,
    okText: 'Clear cart',
  }))) return;
  hopState.rateCart = [];
  hopState.rateCartExpanded = false;
  hopSaveRateCart();
  hopRenderRateCartPanel();
  hopRefreshRateMatrixCartButtons();
}

function hopRateCartKey(item) {
  return `${item.product_key}::${item.sheet_id}`;
}

function hopAddToRateCart(productKeyEnc, sheetId) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  const matrix = hopState.rateMatrix;
  if (!matrix) return;
  const product = (matrix.products || []).find((p) => p.product_key === productKey);
  if (!product) return;
  const offer = (product.offers || {})[String(sheetId)];
  if (!offer || offer.missing || !(Number(offer.rate) > 0)) {
    alert('Is seller pe is product ka rate nahi hai.');
    return;
  }
  hopLoadRateCart();
  let qty = 1;
  const next = {
    product_key: productKey,
    label: product.label,
    size: product.size || '',
    quality: offer.quality || product.quality_hint || '',
    sheet_id: Number(sheetId),
    supplier_name: offer.supplier_name,
    rate: Number(offer.rate),
    gst_pct: Number(offer.gst_pct || 0),
    landed_rate: Number(offer.landed_rate || offer.rate),
    qty,
    line_id: offer.line_id || null,
  };
  const idx = hopState.rateCart.findIndex((c) => hopRateCartKey(c) === hopRateCartKey(next));
  if (idx >= 0) {
    hopState.rateCart[idx].qty = Number(hopState.rateCart[idx].qty || 0) + qty;
  } else {
    hopState.rateCart.push(next);
  }
  hopSaveRateCart();
  // Keep cart compact so matrix stays visible
  hopState.rateCartExpanded = false;
  hopRenderRateCartPanel();
  // Light feedback on the button without full page re-render
  const btn = document.querySelector(`button[onclick="hopAddToRateCart('${productKeyEnc}', ${sheetId})"]`);
  if (btn) {
    btn.textContent = 'Added +';
    btn.classList.add('is-in-cart');
  }
}

function hopAddBestToRateCart(productKeyEnc) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  const product = (hopState.rateMatrix?.products || []).find((p) => p.product_key === productKey);
  if (!product?.best || product.best.sheet_id == null) {
    alert('Is product pe best rate nahi mila.');
    return;
  }
  hopAddToRateCart(productKeyEnc, product.best.sheet_id);
}

function hopRemoveRateCartItem(productKeyEnc, sheetId) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  hopLoadRateCart();
  hopState.rateCart = hopState.rateCart.filter(
    (c) => !(c.product_key === productKey && String(c.sheet_id) === String(sheetId)),
  );
  hopSaveRateCart();
  hopRenderRateCartPanel();
}

function hopUpdateRateCartQty(productKeyEnc, sheetId, value) {
  const productKey = decodeURIComponent(productKeyEnc || '');
  hopLoadRateCart();
  const item = hopState.rateCart.find(
    (c) => c.product_key === productKey && String(c.sheet_id) === String(sheetId),
  );
  if (!item) return;
  let qty = Number(value);
  if (!Number.isFinite(qty) || qty <= 0) qty = 1;
  item.qty = qty;
  hopSaveRateCart();
  hopRenderRateCartPanel();
}

function hopRateCartGroups() {
  hopLoadRateCart();
  const groups = new Map();
  for (const item of hopState.rateCart) {
    const key = `${item.sheet_id}::${item.supplier_name}`;
    if (!groups.has(key)) {
      groups.set(key, {
        sheet_id: item.sheet_id,
        supplier_name: item.supplier_name,
        items: [],
        subtotal: 0,
        landed_total: 0,
      });
    }
    const g = groups.get(key);
    const qty = Number(item.qty || 1);
    g.items.push(item);
    g.subtotal += Number(item.rate || 0) * qty;
    g.landed_total += Number(item.landed_rate || item.rate || 0) * qty;
  }
  return [...groups.values()];
}

function hopToggleRateCartExpand() {
  hopState.rateCartExpanded = !hopState.rateCartExpanded;
  hopRenderRateCartPanel();
}

function hopRenderRateCartPanel() {
  const el = document.getElementById('hop-rate-cart-panel');
  if (!el) return;
  const groups = hopRateCartGroups();
  const count = (hopState.rateCart || []).length;
  const open = Boolean(hopState.rateCartExpanded);
  el.classList.toggle('is-empty', !count);
  el.classList.toggle('is-collapsed', count > 0 && !open);

  if (!count) {
    el.innerHTML = `
      <div class="hop-rate-cart-bar">
        <div class="hop-rate-cart-bar-main">
          <strong>Quote cart</strong>
          <span class="nx-text-dim">empty — matrix me Add to cart dabao</span>
        </div>
      </div>`;
    return;
  }

  const landedAll = groups.reduce((s, g) => s + Number(g.landed_total || 0), 0);
  const supplierBits = groups.map((g) => `${hopCell(g.supplier_name)} (${g.items.length})`).join(' · ');

  const blocks = groups.map((g) => `
    <div class="hop-rate-cart-group">
      <div class="hop-rate-cart-group-head">
        <strong>${hopCell(g.supplier_name)}</strong>
        <span class="hop-rate-sub">${g.items.length} · ${hopMoney(g.landed_total)}</span>
      </div>
      <table class="hop-table hop-rate-cart-table"><thead><tr>
        <th>Product</th><th>Rate</th><th>Qty</th><th>Line</th><th></th>
      </tr></thead><tbody>
        ${g.items.map((it) => {
          const enc = encodeURIComponent(it.product_key);
          const line = Number(it.landed_rate || it.rate || 0) * Number(it.qty || 1);
          return `<tr>
            <td>${hopCell(it.label)}${it.size ? `<div class="hop-rate-sub">${hopCell(it.size)}</div>` : ''}</td>
            <td>${hopMoney(it.rate)}<div class="hop-rate-sub">+${hopCell(it.gst_pct)}%</div></td>
            <td><input type="number" min="1" step="1" value="${hopCell(it.qty)}" class="hop-rate-cart-qty"
              onchange="hopUpdateRateCartQty('${enc}', ${it.sheet_id}, this.value)" /></td>
            <td>${hopMoney(line)}</td>
            <td><button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopRemoveRateCartItem('${enc}', ${it.sheet_id})">✕</button></td>
          </tr>`;
        }).join('')}
      </tbody></table>
    </div>
  `).join('');

  el.innerHTML = `
    <div class="hop-rate-cart-bar">
      <div class="hop-rate-cart-bar-main">
        <strong>Quote cart</strong>
        <span class="hop-rate-cart-pill">${count}</span>
        <span class="nx-text-dim">${groups.length} supplier · ${hopMoney(landedAll)}</span>
        <span class="hop-rate-cart-suppliers nx-text-dim">${supplierBits}</span>
      </div>
      <div class="hop-rate-cart-bar-actions">
        <button type="button" class="nx-btn" onclick="hopToggleRateCartExpand()">${open ? 'Hide' : 'Details'}</button>
        <button type="button" class="nx-btn" onclick="hopClearRateQuote()">Clear</button>
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopPlaceRateCartOrders()">Place orders</button>
      </div>
    </div>
    <div class="hop-rate-cart-body${open ? '' : ' hidden'}">
      ${blocks}
      <p id="hop-rate-cart-status" class="nx-text-dim hop-rate-cart-status"></p>
    </div>
  `;
}

async function hopPlaceRateCartOrders() {
  const groups = hopRateCartGroups();
  if (!groups.length) {
    alert('Quote cart empty.');
    return;
  }
  const status = document.getElementById('hop-rate-cart-status');
  if (status) status.textContent = 'Creating supplier orders…';
  try {
    const data = await hopApi('/api/v1/hop/rate-cart/place-orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        groups: groups.map((g) => ({
          sheet_id: g.sheet_id,
          supplier_name: g.supplier_name,
          items: g.items,
          order_value: Math.round(g.landed_total * 100) / 100,
        })),
      }),
    });
    hopState.rateCart = [];
    hopSaveRateCart();
    const created = data?.orders || [];
    if (status) {
      status.textContent = `Created ${created.length} order(s): ${created.map((o) => o.po_number || o.id).join(', ')}`;
    }
    alert(`Orders placed for ${created.length} supplier(s). Check Orders / PO.`);
    openHopView('orders');
  } catch (e) {
    if (status) status.textContent = '';
    alert(e.message || 'Could not place orders');
  }
}

function hopPruneCartForClearedRates(productKeys, sheetId) {
  hopLoadRateCart();
  const keys = new Set((productKeys || []).map((k) => String(k)));
  hopState.rateCart = (hopState.rateCart || []).filter((c) => {
    if (sheetId != null && String(c.sheet_id) === String(sheetId) && (!keys.size || keys.has(c.product_key))) {
      return false;
    }
    if (keys.size && keys.has(c.product_key) && sheetId == null) return false;
    return true;
  });
  hopSaveRateCart();
}

function hopToggleSelectAllRates(checked) {
  document.querySelectorAll('.hop-rate-row-check').forEach((el) => {
    const tr = el.closest('tr');
    if (tr && tr.classList.contains('hop-rate-row-hidden')) {
      el.checked = false;
      return;
    }
    el.checked = Boolean(checked);
  });
}

function hopSelectedRateProductKeys() {
  return [...document.querySelectorAll('.hop-rate-row-check:checked')]
    .map((el) => decodeURIComponent(el.value || ''))
    .filter(Boolean);
}

async function hopClearRatesApi(payload) {
  const data = await hopApi('/api/v1/hop/rate-lines/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const n = Number(data?.deleted_lines || 0);
  if (payload?.clear_all) {
    nexoraToast(n ? `Cleared ${n} rate line(s) and all sheets.` : 'All rate data cleared.', 'success');
  } else if (n === 0) {
    nexoraToast('No matching rates found to delete.', 'warn');
  } else {
    nexoraToast(`Deleted ${n} rate line(s).`, 'success');
  }
  return data;
}

/** Stable line ids from the loaded matrix — preferred over product_key for deletes. */
function hopLineIdsForProductKey(productKey, sheetId = null) {
  const product = (hopState.rateMatrix?.products || []).find((p) => p.product_key === productKey);
  if (!product) return [];
  const ids = [];
  Object.values(product.offers || {}).forEach((offer) => {
    if (!offer || offer.missing || offer.line_id == null || offer.line_id === '') return;
    if (sheetId != null && String(offer.sheet_id) !== String(sheetId)) return;
    const n = Number(offer.line_id);
    if (Number.isFinite(n)) ids.push(n);
  });
  return ids;
}

async function hopClearSingleProduct(productKeyEnc) {
  const key = decodeURIComponent(productKeyEnc || '');
  if (!key) return;
  if (!(await nexoraConfirm('Delete this item from comparison? Rates for this product (all suppliers) will be removed.', {
    title: 'Delete item',
    danger: true,
    okText: 'Delete item',
  }))) return;
  try {
    const lineIds = hopLineIdsForProductKey(key);
    await hopClearRatesApi({ product_keys: [key], line_ids: lineIds });
    hopPruneCartForClearedRates([key], null);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearSingleRate(productKeyEnc, sheetId) {
  const key = decodeURIComponent(productKeyEnc || '');
  if (!key || sheetId == null) return;
  if (!(await nexoraConfirm('Remove this supplier rate for this item only?', {
    title: 'Remove rate',
    danger: true,
    okText: 'Remove',
  }))) return;
  try {
    const lineIds = hopLineIdsForProductKey(key, sheetId);
    await hopClearRatesApi({ product_keys: [key], line_ids: lineIds, sheet_id: sheetId });
    hopPruneCartForClearedRates([key], sheetId);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Remove failed');
  }
}

async function hopClearSelectedRates() {
  const keys = hopSelectedRateProductKeys();
  if (!keys.length) {
    alert('Pehle checkbox se items select karo.');
    return;
  }
  if (!(await nexoraConfirm(`Delete ${keys.length} selected item(s) from comparison?`, {
    title: 'Delete selected items',
    danger: true,
    okText: 'Delete items',
  }))) return;
  try {
    const lineIds = keys.flatMap((k) => hopLineIdsForProductKey(k));
    await hopClearRatesApi({ product_keys: keys, line_ids: lineIds });
    hopPruneCartForClearedRates(keys, null);
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearAllRates() {
  if (!(await nexoraConfirm('Delete ALL items and rate sheets? Yeh undo nahi hoga.', {
    title: 'Delete everything',
    danger: true,
    okText: 'Delete all',
  }))) return;
  try {
    await hopClearRatesApi({ clear_all: true });
    hopState.rateCart = [];
    hopSaveRateCart();
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopClearSheetRates(sheetId, supplierNameEnc) {
  const label = decodeURIComponent(supplierNameEnc || '') || `sheet #${sheetId}`;
  if (!(await nexoraConfirm(`Clear all item rates for vendor ${label}? Sheet column bhi hata di jayegi.`, {
    title: 'Clear vendor rates',
    danger: true,
    okText: 'Clear vendor',
  }))) return;
  try {
    await hopClearRatesApi({ sheet_id: sheetId });
    hopPruneCartForClearedRates([], sheetId);
    // prune all cart items for this sheet
    hopLoadRateCart();
    hopState.rateCart = (hopState.rateCart || []).filter((c) => String(c.sheet_id) !== String(sheetId));
    hopSaveRateCart();
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Clear failed');
  }
}

const HOP_LEAD_STAGES = [
  'new_lead', 'contacted', 'meeting_scheduled', 'samples_sent', 'boq_received',
  'quotation_sent', 'negotiation', 'po_expected', 'order_won', 'lost',
];

const HOP_PROJECT_STAGES = [
  'lead', 'meeting', 'requirement', 'sample', 'boq', 'vendor', 'quotation',
  'negotiation', 'po', 'production', 'dispatch', 'invoice', 'payment',
  'after_sales', 'closed', 'lost',
];

function formatHopKpiValue(value) {
  if (value == null || value === '') return { text: 'N/A', na: true };
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return { text: 'N/A', na: true };
    if (Math.abs(value) >= 1000) return { text: value.toLocaleString('en-IN'), na: false };
    return { text: String(value), na: false };
  }
  return { text: String(value), na: false };
}

function renderHopKpiCard(label, value, moduleKey) {
  const formatted = formatHopKpiValue(value);
  const target = moduleKey ? `onclick="openHopView('${moduleKey}')"` : '';
  return `
    <button type="button" class="hop-kpi-card" ${target}>
      <p class="hop-kpi-label">${foEscapeText(label)}</p>
      <p class="hop-kpi-value${formatted.na ? ' is-na' : ''}">${foEscapeText(formatted.text)}</p>
    </button>`;
}

function hopFilled(value) {
  return value != null && String(value).trim() !== '';
}

function hopCell(value) {
  return hopFilled(value) ? foEscapeText(value) : '—';
}

function hopMoney(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN');
}

/** Always show % as 00.00 (never 00.000 / trailing junk). */
function hopPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return n.toFixed(2);
}

/** Expand short serials like 120 → HOP/2025-26/120 (matches backend hop_doc_numbers). */
const HOP_DOC_PREFIX_BY_TYPE = {
  1: 'HOP',
  27: 'HOPPI',
  83: 'HOPPR',
  3: 'RCPT',
  65: 'HOPSO',
  30: 'HOPDC',
  82: 'HOPDC',
  21: 'HOPCN',
};

function hopIndianFyLabel(ymd) {
  const s = String(ymd || '').slice(0, 10);
  if (s.length < 7) return '';
  const y = Number(s.slice(0, 4));
  const m = Number(s.slice(5, 7));
  if (!Number.isFinite(y) || !Number.isFinite(m)) return '';
  const start = m >= 4 ? y : y - 1;
  return `${start}-${String(start + 1).slice(-2)}`;
}

function hopFormatDocNo(raw, txnDate, txnType) {
  const s = String(raw || '').trim();
  if (!s) return '';
  if (/[A-Za-z].*\/|\/.*\d{2,4}/.test(s) || s.includes('/')) return s;
  if (!/^\d{1,6}$/.test(s)) return s;
  const pfx = HOP_DOC_PREFIX_BY_TYPE[Number(txnType) || 0];
  const fy = hopIndianFyLabel(txnDate);
  if (!pfx || !fy) return s;
  const serial = s.replace(/^0+/, '') || s;
  return `${pfx}/${fy}/${serial}`;
}

function hopIsMobileView() {
  return !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);
}

function hopDigitsOnly(value) {
  return String(value || '').replace(/\D/g, '');
}

function hopCallHref(mobile) {
  const raw = String(mobile || '').trim();
  if (!raw) return '';
  const clean = raw.replace(/[^\d+]/g, '');
  if (!clean) return '';
  return `tel:${clean}`;
}

function hopWhatsAppHref(mobile) {
  const digits = hopDigitsOnly(mobile);
  if (!digits) return '';
  const withCountry = digits.length === 10 ? `91${digits}` : digits;
  return `https://wa.me/${withCountry}`;
}

function hopContactSelectState(type) {
  if (!hopState.contactSelect[type]) {
    hopState.contactSelect[type] = { mode: false, ids: [] };
  }
  return hopState.contactSelect[type];
}

function hopContactApiBase(type) {
  return type === 'vendors' ? '/api/v1/hop/vendors' : '/api/v1/hop/customers';
}

function hopContactLabel(row) {
  return String(row?.company || row?.contact_person || `ID ${row?.id || ''}`).trim();
}

function hopContactIcon(name) {
  if (name === 'call') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.62 10.79a15.46 15.46 0 0 0 6.59 6.59l2.2-2.2a1 1 0 0 1 1-.24c1.1.37 2.28.57 3.49.57a1 1 0 0 1 1 1V20a1 1 0 0 1-1 1C10.3 21 3 13.7 3 4a1 1 0 0 1 1-1h3.49a1 1 0 0 1 1 1c0 1.21.2 2.39.57 3.49a1 1 0 0 1-.25 1l-2.19 2.3Z" fill="currentColor"/></svg>';
  }
  if (name === 'whatsapp') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.52 3.48A11.91 11.91 0 0 0 12.04 0C5.42 0 .04 5.38.04 12c0 2.11.55 4.17 1.6 5.99L0 24l6.18-1.62A11.94 11.94 0 0 0 12.03 24h.01c6.62 0 12-5.38 12-12 0-3.2-1.25-6.21-3.52-8.52ZM12.04 21.9h-.01a9.86 9.86 0 0 1-5.03-1.38l-.36-.21-3.67.96.98-3.58-.24-.37a9.86 9.86 0 0 1-1.52-5.27c0-5.44 4.42-9.86 9.86-9.86 2.63 0 5.1 1.02 6.96 2.89a9.78 9.78 0 0 1 2.89 6.96c0 5.44-4.43 9.86-9.86 9.86Zm5.41-7.39c-.3-.15-1.77-.87-2.05-.97-.27-.1-.47-.15-.66.15-.2.3-.76.97-.94 1.16-.17.2-.35.22-.65.08-.3-.15-1.27-.47-2.41-1.5a8.94 8.94 0 0 1-1.67-2.08c-.18-.3-.02-.46.13-.61.13-.13.3-.35.45-.52.15-.18.2-.3.3-.5.1-.2.05-.37-.02-.52-.08-.15-.67-1.62-.92-2.23-.24-.57-.48-.5-.66-.5h-.56c-.2 0-.52.08-.8.37-.27.3-1.04 1.01-1.04 2.47s1.07 2.86 1.22 3.06c.15.2 2.1 3.2 5.1 4.49.71.31 1.27.5 1.7.64.72.23 1.38.2 1.9.12.58-.09 1.77-.72 2.02-1.42.25-.69.25-1.28.18-1.41-.08-.13-.28-.2-.58-.35Z" fill="currentColor"/></svg>';
  }
  if (name === 'edit') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25Zm2.92 2.33H5v-.92l9.1-9.1.92.92-9.1 9.1ZM20.71 7.04a1 1 0 0 0 0-1.41L18.37 3.3a1 1 0 0 0-1.41 0l-1.7 1.7L19 8.74l1.71-1.7Z" fill="currentColor"/></svg>';
  }
  if (name === 'email') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2Zm0 4-8 5L4 8V6l8 5 8-5v2Z" fill="currentColor"/></svg>';
  }
  if (name === 'pin') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7Zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5Z" fill="currentColor"/></svg>';
  }
  if (name === 'gst') {
    return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm4 18H6V4h7v5h5v11ZM8 13h8v2H8v-2Zm0 4h5v2H8v-2Z" fill="currentColor"/></svg>';
  }
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 7h12l-1 14H7L6 7Zm3-3h6l1 2H8l1-2Z" fill="currentColor"/></svg>';
}

function hopRenderQuickContactActions(mobile, type, id, label) {
  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);
  const callBtn = callHref
    ? `<a class="nx-btn hop-contact-icon-btn" href="${callHref}" title="Call" onclick="event.stopPropagation()">${hopContactIcon('call')}</a>`
    : `<button type="button" class="nx-btn hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('call')}</button>`;
  const waBtn = waHref
    ? `<a class="nx-btn nx-btn-primary hop-contact-icon-btn" href="${waHref}" target="_blank" rel="noopener noreferrer" title="WhatsApp" onclick="event.stopPropagation()">${hopContactIcon('whatsapp')}</a>`
    : `<button type="button" class="nx-btn nx-btn-primary hop-contact-icon-btn is-disabled" disabled title="Mobile number missing">${hopContactIcon('whatsapp')}</button>`;
  const editBtn = `<button type="button" class="nx-btn hop-contact-icon-btn hop-contact-edit" onclick="event.stopPropagation();hopEditContact('${type}', ${id})" title="Edit">${hopContactIcon('edit')}</button>`;
  const delBtn = `<button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label || '')}')" title="Delete">${hopContactIcon('delete')}</button>`;
  return `
    <div class="hop-contact-actions">
      ${callBtn}
      ${waBtn}
      ${editBtn}
      ${delBtn}
    </div>`;
}

/** Prefer Parties workspace when that is the active CRM view. */
function hopContactReturnView(type) {
  if (hopState.view === 'parties') return 'parties';
  if (type === 'vendors' || type === 'vendor') return hopState.view === 'parties' ? 'parties' : 'vendors';
  if (type === 'customers' || type === 'customer') return hopState.view === 'parties' ? 'parties' : 'customers';
  return hopState.view || 'parties';
}

function hopRenderDesktopContactToolbar(type, count) {
  const state = hopContactSelectState(type);
  const selected = state.ids.length;
  const allChecked = selected === count && count > 0;
  return `
    <div class="hop-desk-toolbar" data-hop-contact-toolbar="${type}">
      <button type="button" class="nx-btn hop-desk-select-btn${allChecked ? ' is-active' : ''}" onclick="hopSelectAllContacts('${type}', ${allChecked ? 'false' : 'undefined'})">
        <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor"><path d="M3 4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4Z${allChecked ? 'M14.3 7.3a1 1 0 0 0-1.4-1.4L8.5 10.3 7.1 8.9a1 1 0 0 0-1.4 1.4l2.1 2.1a1 1 0 0 0 1.4 0l5.1-5.1Z' : ''}"/></svg>
        ${allChecked ? 'Deselect All' : 'Select All'}
      </button>
      <span class="hop-desk-count">${count} records</span>
      <span class="hop-desk-toolbar-spacer"></span>
      ${selected ? `<button type="button" class="nx-btn hop-desk-bulk-del-btn" onclick="hopBulkDeleteContacts('${type}')">${hopContactIcon('delete')} Delete ${selected} selected</button>` : ''}
    </div>`;
}

function hopRenderMobileContactToolbar(type, count) {
  const state = hopContactSelectState(type);
  const selected = state.ids.length;
  const mode = state.mode;
  return `
    <div class="hop-contact-toolbar" data-hop-contact-toolbar="${type}">
      <button type="button" class="nx-btn${mode ? ' nx-btn-primary' : ''}" onclick="hopToggleContactSelectMode('${type}')">${mode ? 'Done' : 'Select'}</button>
      <button type="button" class="nx-btn${mode ? '' : ' hidden'}" onclick="hopSelectAllContacts('${type}')">All (${count})</button>
      <button type="button" class="nx-btn hop-contact-icon-btn hop-toolbar-bulk-del${mode && selected ? '' : ' hidden'}" onclick="hopBulkDeleteContacts('${type}')" title="Delete selected (${selected})">${hopContactIcon('delete')}${selected ? ` ${selected}` : ''}</button>
    </div>`;
}

function hopRenderMobileContactCards(rows, type) {
  if (!rows.length) {
    return '<p class="nx-text-dim">No contacts yet — add your first record.</p>';
  }
  const isVendor = type === 'vendors';
  const state = hopContactSelectState(type);
  return `${hopRenderMobileContactToolbar(type, rows.length)}<div class="hop-contact-cards${state.mode ? ' is-select-mode' : ''}">${
    rows.map((r) => {
      const id = Number(r.id);
      const title = hopCell(r.company);
      const subtitle = hopCell(r.contact_person);
      const mobile = hopCell(r.mobile);
      const city = hopCell(r.city);
      const label = hopContactLabel(r);
      const checked = state.ids.includes(id) ? ' checked' : '';
      const detailRows = isVendor
        ? [
            ['Products', hopCell(r.products)],
            ['GST', hopCell(r.gst_no)],
            ['Email', hopCell(r.email)],
            ['Lead time', hopCell(r.lead_time_days)],
            ['Payment', hopCell(r.payment_terms)],
            ['On-time %', hopCell(r.on_time_pct)],
            ['Quality', hopCell(r.quality_rating)],
            ['Rating', hopCell(r.rating)],
          ]
        : [
            ['Type', hopCell(r.customer_type)],
            ['Hotel', hopCell(r.hotel_brand)],
            ['Architect', hopCell(r.architect)],
            ['Consultant', hopCell(r.consultant)],
            ['Potential', hopCell(r.annual_potential)],
            ['Rating', hopCell(r.potential_rating)],
            ['Status', hopCell(r.status)],
            ['Assigned', hopCell(r.assigned_to)],
          ];
      return `
        <article class="hop-contact-card${checked ? ' is-selected' : ''}" data-hop-contact-type="${type}" data-hop-contact-id="${id}" data-hop-contact-label="${foEscapeAttr(label)}">
          <div class="hop-contact-card-top">
            <label class="hop-contact-check-wrap">
              <input type="checkbox" class="hop-contact-check" value="${id}"${checked} onchange="hopToggleContactSelected('${type}', ${id}, this.checked)" />
            </label>
            <button type="button" class="hop-contact-main" onclick="hopToggleContactDetails(this)">
              <p class="hop-contact-company">${title}</p>
              <p class="hop-contact-sub">${subtitle}</p>
              <p class="hop-contact-sub">${mobile} · ${city}</p>
            </button>
          </div>
          ${hopRenderQuickContactActions(r.mobile, type, id, label)}
          <div class="hop-contact-details hidden">
            ${detailRows.map(([k, v]) => `<p><span>${k}</span><strong>${v}</strong></p>`).join('')}
          </div>
        </article>`;
    }).join('')
  }</div>`;
}

function hopToggleContactDetails(btn) {
  const card = btn?.closest('.hop-contact-card');
  if (!card) return;
  const type = card.getAttribute('data-hop-contact-type') || 'customers';
  const id = Number(card.getAttribute('data-hop-contact-id'));
  hopOpenContactDetail(type, id);
}

function hopOpenContactDetail(type, id) {
  const rows = type === 'vendors' ? (hopState.vendors || []) : (hopState.customers || []);
  const r = rows.find((x) => Number(x.id) === Number(id));
  if (!r) return;
  const isVendor = type === 'vendors';
  const label = hopContactLabel(r);
  const mobile = hopCell(r.mobile);
  const callHref = hopCallHref(r.mobile);
  const waHref = hopWhatsAppHref(r.mobile);

  const fields = isVendor
    ? [
        ['Company', r.company], ['Contact Person', r.contact_person], ['Mobile', r.mobile],
        ['Email', r.email], ['City', r.city], ['Products', r.products],
        ['GST No', r.gst_no], ['Lead Time (days)', r.lead_time_days],
        ['Payment Terms', r.payment_terms], ['On-time %', r.on_time_pct],
        ['Quality Rating', r.quality_rating], ['Rating', r.rating],
        ['Address', r.address], ['Remarks', r.remarks],
      ]
    : [
        ['Company', r.company], ['Contact Person', r.contact_person], ['Mobile', r.mobile],
        ['Email', r.email], ['City', r.city], ['Industry', r.industry],
        ['Customer Type', r.customer_type], ['Hotel Brand', r.hotel_brand],
        ['Architect', r.architect], ['Consultant', r.consultant],
        ['Annual Potential', r.annual_potential], ['Source', r.source],
        ['Rating', r.potential_rating], ['Status', r.status],
        ['Assigned To', r.assigned_to], ['GST No', r.gst_no],
        ['PAN', r.pan], ['Address', r.address], ['Source', r.source], ['Remarks', r.remarks],
      ];

  const callBtn = callHref
    ? `<a class="nx-btn hop-detail-action-btn" href="${callHref}">${hopContactIcon('call')} Call</a>`
    : '';
  const waBtn = waHref
    ? `<a class="nx-btn hop-detail-action-btn hop-detail-wa" href="${waHref}" target="_blank" rel="noopener noreferrer">${hopContactIcon('whatsapp')} WhatsApp</a>`
    : '';

  const overlay = document.createElement('div');
  overlay.id = 'hop-contact-detail-overlay';
  overlay.className = 'hop-detail-overlay';
  overlay.innerHTML = `
    <div class="hop-detail-backdrop" onclick="hopCloseContactDetail()"></div>
    <div class="hop-detail-panel">
      <div class="hop-detail-header">
        <button type="button" class="nx-btn hop-detail-back" onclick="hopCloseContactDetail()" title="Back">&larr;</button>
        <h3 class="hop-detail-title">${foEscapeText(label)}</h3>
        <button type="button" class="nx-btn hop-contact-icon-btn hop-detail-edit-btn" onclick="hopCloseContactDetail();hopEditContact('${type}', ${id})" title="Edit">${hopContactIcon('edit')}</button>
        <button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="hopCloseContactDetail();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label)}')" title="Delete">${hopContactIcon('delete')}</button>
      </div>
      <div class="hop-detail-quick-actions">
        ${callBtn}${waBtn}
        <button type="button" class="nx-btn hop-detail-action-btn" onclick="hopCloseContactDetail();hopEditContact('${type}', ${id})">${hopContactIcon('edit')} Edit</button>
        <button type="button" class="nx-btn hop-detail-action-btn hop-detail-del" onclick="hopCloseContactDetail();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label)}')">${hopContactIcon('delete')} Delete</button>
      </div>
      <div class="hop-detail-fields">
        ${fields.map(([k, v]) => {
          const val = hopCell(v);
          return `<div class="hop-detail-field"><span class="hop-detail-label">${foEscapeText(k)}</span><span class="hop-detail-value">${val}</span></div>`;
        }).join('')}
      </div>
    </div>`;
  document.getElementById('hop-contact-detail-overlay')?.remove();
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-open'));
}

function hopCloseContactDetail() {
  const el = document.getElementById('hop-contact-detail-overlay');
  if (!el) return;
  el.classList.remove('is-open');
  setTimeout(() => el.remove(), 250);
}

function hopCloseContactActionMenu() {
  document.getElementById('hop-contact-action-sheet')?.remove();
}

function hopOpenContactActionMenu(type, id, label) {
  hopCloseContactActionMenu();
  const sheet = document.createElement('div');
  sheet.id = 'hop-contact-action-sheet';
  sheet.className = 'hop-contact-sheet';
  sheet.innerHTML = `
    <div class="hop-contact-sheet-backdrop" onclick="hopCloseContactActionMenu()"></div>
    <div class="hop-contact-sheet-card">
      <p class="hop-contact-sheet-title">${foEscapeText(label || 'Contact actions')}</p>
      <button type="button" class="nx-btn hop-contact-sheet-btn" onclick="hopCloseContactActionMenu();hopEditContact('${type}', ${id});">
        ${hopContactIcon('edit')} Edit
      </button>
      <button type="button" class="nx-btn hop-contact-sheet-btn hop-contact-delete" onclick="hopCloseContactActionMenu();hopDeleteContact('${type}', ${id}, '${foEscapeAttr(label || '')}');">
        ${hopContactIcon('delete')} Delete
      </button>
      <button type="button" class="nx-btn hop-contact-sheet-btn" onclick="hopCloseContactActionMenu()">Cancel</button>
    </div>`;
  document.body.appendChild(sheet);
}

function hopBindMobileContactCards(type) {
  const root = document.querySelector(`[data-hop-contact-toolbar="${type}"]`)?.parentElement;
  if (!root) return;
  const mains = root.querySelectorAll('.hop-contact-main');
  mains.forEach((mainBtn) => {
    if (mainBtn.dataset.hopLongPressBound === '1') return;
    mainBtn.dataset.hopLongPressBound = '1';
    let timer = null;
    let moved = false;
    let startX = 0;
    let startY = 0;
    const clearTimer = () => {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    };
    mainBtn.addEventListener('touchstart', (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;
      moved = false;
      startX = touch.clientX;
      startY = touch.clientY;
      clearTimer();
      timer = window.setTimeout(() => {
        timer = null;
        const card = mainBtn.closest('.hop-contact-card');
        if (!card) return;
        const contactType = card.getAttribute('data-hop-contact-type') || type;
        const contactId = Number(card.getAttribute('data-hop-contact-id'));
        const label = card.getAttribute('data-hop-contact-label') || '';
        if (navigator.vibrate) navigator.vibrate(20);
        hopOpenContactActionMenu(contactType, contactId, label);
      }, 650);
    }, { passive: true });
    mainBtn.addEventListener('touchmove', (event) => {
      const touch = event.changedTouches?.[0];
      if (!touch) return;
      if (Math.abs(touch.clientX - startX) > 12 || Math.abs(touch.clientY - startY) > 12) {
        moved = true;
        clearTimer();
      }
    }, { passive: true });
    mainBtn.addEventListener('touchend', () => {
      if (moved) clearTimer();
      clearTimer();
    }, { passive: true });
    mainBtn.addEventListener('touchcancel', clearTimer, { passive: true });
  });
}

function hopToggleContactSelectMode(type) {
  const state = hopContactSelectState(type);
  state.mode = !state.mode;
  if (!state.mode) state.ids = [];
  openHopView(type);
}

function hopToggleContactSelected(type, id, checked) {
  const state = hopContactSelectState(type);
  const numId = Number(id);
  if (checked) {
    if (!state.ids.includes(numId)) state.ids.push(numId);
  } else {
    state.ids = state.ids.filter((x) => x !== numId);
  }
  const card = document.querySelector(`.hop-contact-card[data-hop-contact-type="${type}"][data-hop-contact-id="${numId}"]`);
  card?.classList.toggle('is-selected', checked);
  // Update mobile toolbar
  const bulkBtn = document.querySelector(`[data-hop-contact-toolbar="${type}"] .hop-contact-delete`);
  if (bulkBtn) {
    bulkBtn.textContent = `Delete (${state.ids.length})`;
    bulkBtn.classList.toggle('hidden', !state.mode || !state.ids.length);
  }
  // Update desktop toolbar
  _hopUpdateDesktopBulkBtn(type);
}

function _hopUpdateDesktopBulkBtn(type) {
  const state = hopContactSelectState(type);
  const toolbar = document.querySelector(`.hop-desk-toolbar[data-hop-contact-toolbar="${type}"]`);
  if (!toolbar) return;
  const existing = toolbar.querySelector('.hop-desk-bulk-del-btn');
  if (state.ids.length) {
    if (existing) {
      existing.innerHTML = `${hopContactIcon('delete')} Delete ${state.ids.length} selected`;
    } else {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'nx-btn hop-desk-bulk-del-btn';
      btn.onclick = () => hopBulkDeleteContacts(type);
      btn.innerHTML = `${hopContactIcon('delete')} Delete ${state.ids.length} selected`;
      toolbar.appendChild(btn);
    }
  } else if (existing) {
    existing.remove();
  }
}

function hopSelectAllContacts(type, checked) {
  const state = hopContactSelectState(type);
  const rows = type === 'vendors' ? (hopState.vendors || []) : (hopState.customers || []);
  if (checked === false) {
    state.ids = [];
  } else {
    state.ids = rows.map((r) => Number(r.id)).filter((id) => Number.isFinite(id));
  }
  openHopView(type);
}

async function hopEditContact(type, id) {
  const kind = type === 'vendors' || type === 'vendor' ? 'vendor' : 'customer';
  const rows = kind === 'vendor' ? (hopState.vendors || []) : (hopState.customers || []);
  let row = rows.find((r) => Number(r.id) === Number(id));
  if (!row && hopState._parties) {
    const p = hopState._parties.find((x) => Number(x.id) === Number(id) && x._type === (kind === 'vendor' ? 'vendor' : 'customer'));
    if (p) row = p;
  }
  if (!row) {
    try {
      row = await hopApi(`${hopContactApiBase(kind === 'vendor' ? 'vendors' : 'customers')}/${id}`);
    } catch (e) {
      alert(e.message || 'Could not load contact');
      return;
    }
  }
  hopOpenPartyEditModal(kind, row);
}

async function hopDeleteContact(type, id, label) {
  const name = label || `contact #${id}`;
  const base = hopContactApiBase(type);
  let usage = null;
  try {
    usage = await hopApi(`${base}/${id}/usage`);
  } catch (_) {
    usage = null;
  }
  let inUse = !!(usage && usage.in_use && Number(usage.total || 0) > 0);
  let summary = (usage && usage.summary) || '';
  let msg = `Delete "${name}"?`;
  let title = 'Delete contact';
  if (inUse) {
    title = 'Party is in use';
    msg = `"${name}" is used in ${summary}. Delete anyway? Linked records (deals, invoices, etc.) will keep the name, but the party link will be removed.`;
  }
  if (!(await nexoraConfirm(msg, {
    title,
    danger: true,
    okText: inUse ? 'Delete anyway' : 'Delete',
  }))) return;

  const doDelete = async (force) => {
    const url = force ? `${base}/${id}?force=true` : `${base}/${id}`;
    let response;
    try {
      response = await fetchWithAuth(url, { method: 'DELETE' });
    } catch (e) {
      if (isSessionTimeoutError(e)) return null;
      throw e;
    }
    const data = await parseApiJson(response);
    return { response, data };
  };

  try {
    let { response, data } = await doDelete(inUse);
    if (response == null) return;
    if (response.status === 409 && data.requires_confirmation) {
      const u = data.data?.usage || {};
      summary = u.summary || summary || 'linked records';
      const ok = await nexoraConfirm(
        data.message
          || `"${name}" is used in ${summary}. Delete anyway?`,
        {
          title: 'Party is in use',
          danger: true,
          okText: 'Delete anyway',
        },
      );
      if (!ok) return;
      ({ response, data } = await doDelete(true));
      if (response == null) return;
      inUse = true;
    }
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Delete failed'));
    }
    const state = hopContactSelectState(type);
    state.ids = state.ids.filter((x) => x !== Number(id));
    if (type === 'vendors' || type === 'vendor') hopState.vendors = [];
    else hopState.customers = [];
    hopCloseContactDetail();
    hopClosePartyEditModal();
    if (typeof nexoraToast === 'function') {
      nexoraToast(inUse ? 'Party deleted (links cleared).' : 'Party deleted.', 'ok');
    }
    openHopView(hopContactReturnView(type));
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

async function hopBulkDeleteContacts(type) {
  const state = hopContactSelectState(type);
  const ids = [...state.ids];
  if (!ids.length) return;
  if (!(await nexoraConfirm(`Delete ${ids.length} selected contact(s)?`, {
    title: 'Bulk delete',
    danger: true,
    okText: 'Delete all',
  }))) return;
  const base = hopContactApiBase(type);
  try {
    let response;
    try {
      response = await fetchWithAuth(`${base}/bulk-delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
    } catch (e) {
      if (isSessionTimeoutError(e)) return;
      throw e;
    }
    let data = await parseApiJson(response);
    if (response.status === 409 && data.requires_confirmation) {
      const blockedN = (data.data?.blocked || []).length;
      const ok = await nexoraConfirm(
        data.message
          || `${blockedN || 'Some'} contact(s) are used in deals/invoices. Delete anyway?`,
        {
          title: 'Parties in use',
          danger: true,
          okText: 'Delete anyway',
        },
      );
      if (!ok) return;
      response = await fetchWithAuth(`${base}/bulk-delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids, force: true }),
      });
      data = await parseApiJson(response);
    }
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Bulk delete failed'));
    }
    const result = data.data || {};
    const deleted = (result.deleted || []).length;
    const errors = result.errors || [];
    state.ids = [];
    state.mode = false;
    if (type === 'vendors') hopState.vendors = [];
    else hopState.customers = [];
    openHopView(hopContactReturnView(type));
    if (errors.length) {
      alert(`Deleted ${deleted}. ${errors.length} could not be deleted.`);
    } else if (typeof nexoraToast === 'function') {
      nexoraToast(`Deleted ${deleted} contact(s).`, 'ok');
    }
  } catch (e) {
    alert(e.message || 'Bulk delete failed');
  }
}

async function hopApi(path, options) {
  let response;
  try {
    response = await fetchWithAuth(path, options);
  } catch (e) {
    if (isSessionTimeoutError(e)) return null;
    throw e;
  }
  const data = await parseApiJson(response);
  if (!response.ok || !data.success) {
    throw new Error(getApiErrorMessage(data, 'Request failed'));
  }
  return data.data;
}

function hopMount() {
  return document.getElementById('hop-module-mount');
}

function hopHideAllViews() {
  document.querySelectorAll('.hop-view').forEach((el) => el.classList.add('hidden'));
  const mount = hopMount();
  if (mount) mount.innerHTML = '';
}

function hopScrollMainToTop() {
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  if (main) main.scrollTop = 0;
  const shell = document.getElementById('hop-executive-workspace');
  if (shell) shell.scrollTop = 0;
  const page = document.scrollingElement || document.documentElement;
  if (page) page.scrollTop = 0;
  window.scrollTo(0, 0);
  // After async content paints, keep top (layout can reflow and leave scroll low)
  requestAnimationFrame(() => {
    if (main) main.scrollTop = 0;
    window.scrollTo(0, 0);
  });
}

/** Scroll an element into view inside .hop-main (not the locked body). */
function hopScrollIntoMain(el, offset = 12) {
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  if (!main || !el) return;
  const mainRect = main.getBoundingClientRect();
  const elRect = el.getBoundingClientRect();
  main.scrollTop += (elRect.top - mainRect.top - offset);
}

function hopSetMainFullpage(enabled) {
  const on = !!enabled;
  const main = document.querySelector('#hop-executive-workspace .hop-main');
  const shell = document.querySelector('#hop-executive-workspace .hop-shell');
  const ws = document.getElementById('hop-executive-workspace');
  main?.classList.toggle('hop-main--fullpage', on);
  shell?.classList.toggle('hop-shell--module', on);
  ws?.classList.toggle('hop-ws--fullscreen', on);
  // Overwrite NEXORA top bar (Ask NEXORA / Workspace / profile / bell) for max work room.
  document.body.classList.toggle('hop-module-fullscreen', on);
  document.documentElement.classList.toggle('hop-module-fullscreen', on);
}

/** Mobile / fullscreen back — closes overlays first, then pops view history. */
function hopGoBack() {
  if (document.getElementById('hop-inv-ctx-menu')) {
    hopCloseInvRowContextMenu();
    return;
  }
  if (document.getElementById('hop-vyp-doc-overlay')) {
    if (typeof hopCloseManualDocOverlay === 'function') hopCloseManualDocOverlay();
    return;
  }
  if (document.getElementById('hop-party-edit-modal')) {
    hopClosePartyEditModal();
    return;
  }
  if (document.getElementById('nx-confirm-modal') && !document.getElementById('nx-confirm-modal').classList.contains('hidden')) {
    document.getElementById('nx-confirm-cancel')?.click();
    return;
  }
  if (document.getElementById('hop-party-txn-overlay')) {
    hopClosePartyTxnDetail();
    return;
  }
  if (document.getElementById('hop-contact-detail-overlay')) {
    hopCloseContactDetail();
    return;
  }
  if (document.getElementById('hop-contact-action-sheet')) {
    hopCloseContactActionMenu();
    return;
  }
  // CRM lead detail → always Leads list (board/list), never random history
  if (hopState.view === 'deals' && hopState.dealDetailId) {
    hopCloseDealDetail();
    return;
  }
  const stack = hopState.viewHistory || [];
  const prev = stack.pop();
  hopState.viewHistory = stack;
  openHopView(prev || 'dashboard', { skipHistory: true });
}

function hopBackButtonHtml(label) {
  const text = label || 'Back';
  return `<button type="button" class="nx-btn hop-mobile-back" onclick="hopGoBack()" aria-label="${foEscapeAttr(text)}" title="${foEscapeAttr(text)}">
    <svg viewBox="0 0 20 20" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M12.7 4.3a1 1 0 0 1 0 1.4L8.4 10l4.3 4.3a1 1 0 1 1-1.4 1.4l-5-5a1 1 0 0 1 0-1.4l5-5a1 1 0 0 1 1.4 0Z"/></svg>
    <span>${foEscapeText(text)}</span>
  </button>`;
}

function openHopView(viewName, opts) {
  // Legacy alias
  if (viewName === 'quotations') viewName = 'sale_estimates';
  if (viewName === 'deal') viewName = 'deals';
  // CRM UI removed — engines (/api/v1/hop/deals*) kept for rebuild
  const hopCrmUiHidden = new Set([
    'deals', 'leads', 'pipeline', 'meetings', 'projects', 'project-hub', 'project_hub', 'funnel',
  ]);
  if (hopCrmUiHidden.has(String(viewName || ''))) {
    if (typeof nexoraToast === 'function') {
      nexoraToast('CRM interface removed — new UI coming soon. APIs still available.', 'ok');
    }
    viewName = 'dashboard';
  }
  const next = viewName || 'dashboard';
  const prev = hopState.view;

  // Leaving Theme with an unsaved preview → ask keep / discard first
  if (
    prev === 'theme'
    && next !== 'theme'
    && !(opts && opts.skipThemeConfirm)
  ) {
    if (hopThemePageDirty()) {
      hopPromptThemeLeave(next, opts);
      return;
    }
    hopThemePageBaseline = null;
  }
  if (next === 'theme' && prev !== 'theme') {
    hopCaptureThemeBaseline();
  }
  // Commission: hide edit ribbon until user clicks a transaction
  if (next === 'commission' && prev !== 'commission' && hopState.commissionUi) {
    hopState.commissionUi.selectedId = null;
    hopState.commissionUi.sheet = null;
  }

  if (!opts?.skipHistory && prev && prev !== next) {
    if (!Array.isArray(hopState.viewHistory)) hopState.viewHistory = [];
    hopState.viewHistory.push(prev);
    if (hopState.viewHistory.length > 40) hopState.viewHistory.shift();
  }
  hopState.view = next;
  hopHideAllViews();
  hopScrollMainToTop();
  document.querySelectorAll('.hop-nav-btn[data-hop-view]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.hopView === hopState.view);
  });
  // Accordion: keep only the active group's fold open
  const v = String(hopState.view || '');
  let foldId = null;
  if (
    v.startsWith('sale_')
    || v === 'invoices'
    || v === 'payments'
    || v === 'commission'
  ) {
    foldId = 'sale';
  } else if (v.startsWith('purchase_')) {
    foldId = 'purchase';
  } else if (v === 'journal_entries') {
    foldId = 'accounting';
  } else if (v === 'vyapar_import' || v === 'wipe_data' || v === 'theme' || v === 'company_profile') {
    foldId = 'settings';
  } else if (v === 'hop_manual_doc_create') {
    foldId = 'sale';
  }
  hopOpenNavFoldExclusive(foldId);

  // Dashboard keeps padded scroll layout; every other menu opens as full-page workspace
  // (overwrites Ask NEXORA / Workspace / profile / bell top bar).
  hopSetMainFullpage(hopState.view !== 'dashboard');

  if (hopState.view === 'dashboard') {
    hopState.viewHistory = [];
    document.getElementById('hop-view-dashboard')?.classList.remove('hidden');
    document.getElementById('hop-view-project-hub')?.classList.remove('hop-view--fullpage');
    Promise.resolve(loadHopExecutiveSnapshot()).finally(() => hopScrollMainToTop());
  } else if (hopState.view === 'project-hub' || hopState.view === 'project_hub') {
    const hub = document.getElementById('hop-view-project-hub');
    hub?.classList.remove('hidden');
    hub?.classList.add('hop-view--fullpage', 'hop-view--module');
    hopSetMainFullpage(true);
    const pid = opts?.projectId || hopState.hub?.project?.id;
    if (pid) Promise.resolve(loadHopProjectHub(pid)).finally(() => hopScrollMainToTop());
  } else {
    const mount = hopMount();
    if (!mount) {
      console.error('HoP mount missing');
    } else {
      mount.innerHTML = '<div class="hop-view hop-view--fullpage hop-view--module"><div class="mod-page"><div class="mod-body"><p class="nx-text-dim">Loading…</p></div></div></div>';
      hopSetMainFullpage(true);
      const loaders = {
        parties: renderHopPartiesModule,
        customers: renderHopCustomersModule,
        vyapar_import: renderHopVyaparImportModule,
        wipe_data: renderHopWipeDataModule,
        theme: renderHopThemeModule,
        company_profile: renderHopFirmProfileModule,
        hop_manual_doc_create: renderHopManualDocCreateModule,
        visiting_card: () => {
          // Visiting card lives under Parties → Add Party → Scan
          openHopView('parties');
          requestAnimationFrame(() => hopOpenAddPartyChooser());
        },
        deals: renderHopDealsModule,
        projects: renderHopProjectsModule,
        leads: renderHopLeadsModule,
        meetings: renderHopMeetingsModule,
        quotations: renderHopSaleEstimatesModule,
        sale_estimates: renderHopSaleEstimatesModule,
        sale_proforma: (m) => renderHopSaleLedgerModule(m, 'sale_proforma'),
        sale_orders: (m) => renderHopSaleLedgerModule(m, 'sale_orders'),
        sale_challan: (m) => renderHopSaleLedgerModule(m, 'sale_challan'),
        sale_returns: (m) => renderHopSaleLedgerModule(m, 'sale_returns'),
        sale_fa: (m) => renderHopSaleLedgerModule(m, 'sale_fa'),
        purchase_bills: (m) => renderHopSaleLedgerModule(m, 'purchase_bills'),
        purchase_payment_out: (m) => renderHopSaleLedgerModule(m, 'purchase_payment_out'),
        purchase_expenses: (m) => renderHopSaleLedgerModule(m, 'purchase_expenses'),
        purchase_orders: (m) => renderHopSaleLedgerModule(m, 'purchase_orders'),
        purchase_returns: (m) => renderHopSaleLedgerModule(m, 'purchase_returns'),
        purchase_fa: (m) => renderHopSaleLedgerModule(m, 'purchase_fa'),
        journal_entries: (m) => renderHopSaleLedgerModule(m, 'journal_entries'),
        vendors: renderHopVendorsModule,
        vendor_cmp: renderHopVendorCmpModule,
        samples: renderHopSamplesModule,
        products: renderHopProductsModule,
        fabric_preview: renderHopFabricPreviewModule,
        orders: renderHopOrdersModule,
        dispatches: renderHopDispatchesModule,
        invoices: renderHopInvoicesModule,
        commission: renderHopCommissionModule,
        payments: renderHopPaymentInModule,
        complaints: renderHopComplaintsModule,
        pipeline: renderHopPipelineModule,
        funnel: renderHopFunnelModule,
        receivables: renderHopReceivablesModule,
        customer_dash: renderHopCustomerDashModule,
        daily: renderHopDailyModule,
        profit: renderHopProfitModule,
        targets: renderHopTargetsModule,
      };
      const fn = loaders[hopState.view];
      if (fn) {
        Promise.resolve(fn(mount)).catch((err) => {
          console.error('HoP view load failed', hopState.view, err);
          try {
            mount.innerHTML = hopModuleShell(
              'Error',
              String(hopState.view || ''),
              '',
              '',
              `<p class="nx-oc-error">${foEscapeText(err?.message || String(err) || 'Failed to load')}</p>`,
            );
          } catch (_) {
            mount.innerHTML = `<p class="nx-oc-error">Failed to load</p>`;
          }
        }).finally(() => {
          hopSetMainFullpage(true);
          hopScrollMainToTop();
        });
      } else {
        mount.innerHTML = hopModuleShell('HoP', 'Unknown', '', '', `<p class="nx-oc-error">Unknown view</p>`);
        hopSetMainFullpage(true);
      }
    }
  }

  // Close drawer after navigation so Android WebView does not cancel the tap.
  window.setTimeout(() => {
    if (typeof closeMobileNav === 'function') closeMobileNav();
  }, 80);
}

function hopSetNavFoldOpen(id, open) {
  const fold = document.querySelector(`[data-hop-fold="${CSS.escape(String(id || ''))}"]`);
  if (!fold) return;
  fold.classList.toggle('is-collapsed', !open);
  fold.querySelector('.hop-nav-fold-toggle')?.setAttribute('aria-expanded', open ? 'true' : 'false');
}

/** Accordion: open one fold, collapse all others. Pass null to collapse all. */
function hopOpenNavFoldExclusive(id) {
  document.querySelectorAll('#hop-executive-workspace .hop-nav-fold[data-hop-fold]').forEach((el) => {
    const fid = el.getAttribute('data-hop-fold');
    hopSetNavFoldOpen(fid, Boolean(id) && fid === id);
  });
  if (id) hopScrollNavFoldIntoView(id);
  else if (typeof bdSyncSidebarScroll === 'function') {
    requestAnimationFrame(() => bdSyncSidebarScroll({ preserveScroll: true }));
  }
}

function hopToggleNavFold(id) {
  const fold = document.querySelector(`[data-hop-fold="${CSS.escape(String(id || ''))}"]`);
  if (!fold) return;
  const willOpen = fold.classList.contains('is-collapsed');
  if (willOpen) hopOpenNavFoldExclusive(id);
  else {
    hopSetNavFoldOpen(id, false);
    if (typeof bdSyncSidebarScroll === 'function') {
      requestAnimationFrame(() => bdSyncSidebarScroll({ preserveScroll: true }));
    }
  }
}

/** After expand, scroll the rail only if the fold (or its open items) is clipped. */
function hopScrollNavFoldIntoView(idOrEl) {
  const fold = typeof idOrEl === 'string' || typeof idOrEl === 'number'
    ? document.querySelector(`.hop-nav-fold[data-hop-fold="${CSS.escape(String(idOrEl || ''))}"]`)
    : idOrEl;
  if (!fold || !fold.getBoundingClientRect) return;

  if (typeof bdSyncSidebarScroll === 'function') bdSyncSidebarScroll({ preserveScroll: true });

  const scrollParent = (() => {
    let el = fold.parentElement;
    while (el && el !== document.body) {
      if (el.classList.contains('hop-nav-list') || el.classList.contains('nav-list') || el.scrollHeight > el.clientHeight + 2) {
        const style = window.getComputedStyle(el);
        const oy = style.overflowY;
        if (oy === 'auto' || oy === 'scroll' || oy === 'overlay' || el.classList.contains('is-scrollable') || el.classList.contains('hop-nav-list') || el.classList.contains('nav-list')) {
          return el;
        }
      }
      el = el.parentElement;
    }
    return fold.closest('.hop-nav-list, .nav-list');
  })();

  const run = () => {
    const pad = 10;
    const target = scrollParent;
    if (!target) return;

    if (typeof bdSyncSidebarScroll === 'function') bdSyncSidebarScroll({ preserveScroll: true });

    if (target.scrollHeight <= target.clientHeight + 2) {
      return;
    }

    const items = fold.querySelector('.hop-nav-fold-items');
    const lastSub = items
      ? Array.from(items.querySelectorAll('.nav-item, .hop-nav-btn, .hop-nav-sub')).filter((el) => {
          const cs = window.getComputedStyle(el);
          return cs.display !== 'none' && cs.visibility !== 'hidden';
        }).pop()
      : null;
    const anchor = lastSub || fold;
    const parentRect = target.getBoundingClientRect();
    const anchorRect = anchor.getBoundingClientRect();
    const foldRect = fold.getBoundingClientRect();

    if (anchorRect.bottom > parentRect.bottom - pad) {
      target.scrollBy({ top: anchorRect.bottom - parentRect.bottom + pad, behavior: 'smooth' });
    } else if (foldRect.top < parentRect.top + pad) {
      target.scrollBy({ top: foldRect.top - parentRect.top - pad, behavior: 'smooth' });
    }
  };
  requestAnimationFrame(() => requestAnimationFrame(run));
}

/** Reliable mobile taps — ignore scroll gestures; only open on a real tap. */
function bindHopNavClicks() {
  const nav = document.querySelector('#hop-executive-workspace .hop-nav');
  if (!nav || nav.dataset.hopNavBound === '1') return;
  nav.dataset.hopNavBound = '1';

  const TAP_MOVE_PX = 14;
  let startX = 0;
  let startY = 0;
  let moved = false;
  let startTarget = null;
  let suppressClickUntil = 0;
  let lastNavAt = 0;

  const isNavControl = (el) => {
    if (!el || !el.closest) return null;
    // Include fold toggles (Sale / Purchase / Settings) — they have no data-hop-view.
    return el.closest(
      '.hop-nav-btn[data-hop-view], button.hop-nav-logout, button.hop-nav-fold-toggle, .hop-nav-fold-toggle',
    );
  };

  const runNav = (btn) => {
    if (!btn || btn.disabled || btn.classList.contains('is-soon')) return;
    const now = Date.now();
    if (now - lastNavAt < 350) return;
    lastNavAt = now;
    if (btn.classList.contains('hop-nav-logout')) {
      if (typeof logout === 'function') logout();
      return;
    }
    if (btn.classList.contains('hop-nav-fold-toggle')) {
      const fold = btn.closest('[data-hop-fold]');
      const foldId = fold?.getAttribute('data-hop-fold');
      if (foldId) hopToggleNavFold(foldId);
      return;
    }
    const view = btn.getAttribute('data-hop-view');
    if (view) openHopView(view);
  };

  nav.addEventListener(
    'touchstart',
    (event) => {
      const touch = event.changedTouches && event.changedTouches[0];
      if (!touch) return;
      startX = touch.clientX;
      startY = touch.clientY;
      moved = false;
      startTarget = isNavControl(event.target);
    },
    { capture: true, passive: true },
  );

  nav.addEventListener(
    'touchmove',
    (event) => {
      const touch = event.changedTouches && event.changedTouches[0];
      if (!touch) return;
      if (
        Math.abs(touch.clientX - startX) > TAP_MOVE_PX
        || Math.abs(touch.clientY - startY) > TAP_MOVE_PX
      ) {
        moved = true;
      }
    },
    { capture: true, passive: true },
  );

  nav.addEventListener(
    'touchend',
    (event) => {
      const btn = isNavControl(event.target);
      // Finger moved = scroll — do not open the item under the finger.
      if (moved || !btn || btn !== startTarget) {
        startTarget = null;
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      suppressClickUntil = Date.now() + 500;
      runNav(btn);
      startTarget = null;
    },
    { capture: true, passive: false },
  );

  nav.addEventListener(
    'click',
    (event) => {
      if (Date.now() < suppressClickUntil) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const btn = isNavControl(event.target);
      if (!btn) return;
      // Phones: fold toggles + nav items run via touchend; still allow fold
      // if a browser synthesizes click without a matched touchend.
      if (window.matchMedia('(pointer: coarse)').matches) {
        if (btn.classList.contains('hop-nav-fold-toggle')) {
          event.preventDefault();
          event.stopPropagation();
          runNav(btn);
        } else {
          event.preventDefault();
          event.stopPropagation();
        }
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      runNav(btn);
    },
    true,
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    hopInitTheme();
    bindHopNavClicks();
  });
} else {
  hopInitTheme();
  bindHopNavClicks();
}

function hopDebouncedReload(kind) {
  clearTimeout(hopState.reloadTimers[kind]);
  hopState.reloadTimers[kind] = setTimeout(() => openHopView(kind), 280);
}

/** Locked compact page chrome (Vyapar density) for every full-page module. */
function hopModuleShell(eyebrow, title, subtitle, actionsHtml, bodyHtml) {
  const isFullpage = hopState.view && hopState.view !== 'dashboard';
  if (isFullpage) {
    return `
    <div class="hop-view hop-view--fullpage hop-view--module hop-view--tx">
      <div class="mod-page inv-shell">
        <div class="inv-topbar">
          <div class="inv-topbar-left">
            ${hopBackButtonHtml('Back')}
            <h2 class="inv-title">${foEscapeText(title)}</h2>
          </div>
          <div class="inv-topbar-actions">${actionsHtml || ''}</div>
        </div>
        <div class="inv-body hop-tx-body">${bodyHtml || ''}</div>
      </div>
    </div>`;
  }
  return `
    <div class="hop-view">
      <header class="hop-view-header hop-view-header-row">
        <div>
          <p class="nx-text-faint hop-eyebrow">${foEscapeText(eyebrow)}</p>
          <h2 class="nx-display">${foEscapeText(title)}</h2>
          ${subtitle ? `<p class="nx-text-dim">${subtitle}</p>` : ''}
        </div>
        <div class="hop-header-actions">${actionsHtml || ''}</div>
      </header>
      <div class="hop-view-body">${bodyHtml}</div>
    </div>`;
}

/** Compact KPI strip: Paid + Unpaid + … = Total style. */
function hopTxCards(cards) {
  if (!cards || !cards.length) return '';
  return `<div class="inv-cards">${cards.map((c) => {
    const op = c.op ? `<span class="inv-op">${foEscapeText(c.op)}</span>` : '';
    const tone = c.tone ? ` inv-card--${foEscapeAttr(c.tone)}` : '';
    const idAttr = c.id ? ` id="${foEscapeAttr(c.id)}"` : '';
    const val = c.valueHtml != null ? c.valueHtml : foEscapeText(String(c.value ?? '—'));
    return `${op}<div class="inv-card${tone}"><span>${foEscapeText(c.label || '')}</span><strong${idAttr}>${val}</strong></div>`;
  }).join('')}</div>`;
}

function hopTxToolbar(innerHtml) {
  if (!innerHtml) return '';
  return `<div class="inv-toolbar">${innerHtml}</div>`;
}

function hopFilterListTable(input, tbodyId) {
  const q = String(input?.value || '').trim().toLowerCase();
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.querySelectorAll('tr').forEach((tr) => {
    if (tr.querySelector('.inv-empty')) return;
    tr.style.display = !q || (tr.textContent || '').toLowerCase().includes(q) ? '' : 'none';
  });
}

/**
 * Locked transactions table — fills remaining viewport height.
 * opts: { label, count, tbodyId, search, searchValue, searchPlaceholder, searchId, className }
 */
function hopTable(headers, rowsHtml, emptyCols, opts) {
  let o = {};
  let cols = emptyCols;
  if (emptyCols && typeof emptyCols === 'object' && !Array.isArray(emptyCols)) {
    o = emptyCols;
    cols = o.emptyCols;
  } else if (opts && typeof opts === 'object') {
    o = opts;
  }
  cols = cols || (headers || []).length || 1;
  const tid = o.tbodyId || `hop-tb-${Math.random().toString(36).slice(2, 9)}`;
  const label = o.label || 'Transactions';
  const countHtml = o.count != null ? `<span class="inv-count">${foEscapeText(String(o.count))} rows</span>` : '';
  const searchHtml = o.search === false
    ? ''
    : `<input${o.searchId ? ` id="${foEscapeAttr(o.searchId)}"` : ''} class="inv-search" type="search" placeholder="${foEscapeAttr(o.searchPlaceholder || 'Search…')}"
         value="${foEscapeAttr(o.searchValue || '')}" oninput="hopFilterListTable(this, '${tid}')" />`;
  const body = rowsHtml || `<tr><td colspan="${cols}" class="inv-empty">No rows yet — add your first record.</td></tr>`;
  return `
    <div class="inv-table-card hop-tx-table${o.className ? ` ${foEscapeAttr(o.className)}` : ''}">
      <div class="inv-table-head">
        <strong>${foEscapeText(label)}</strong>
        ${countHtml}
        ${searchHtml}
      </div>
      <div class="inv-table-wrap">
        <table class="inv-table">
          <thead><tr>${(headers || []).map((h) => {
            const isNum = /amount|value|balance|paid|qty|%|hrs|outstanding/i.test(String(h));
            return `<th${isNum ? ' class="inv-num"' : ''}>${foEscapeText(h)}</th>`;
          }).join('')}</tr></thead>
          <tbody id="${tid}">${body}</tbody>
        </table>
      </div>
    </div>`;
}

async function loadHopExecutiveSnapshot() {
  const loading = document.getElementById('hop-exec-loading');
  const content = document.getElementById('hop-exec-content');
  const errorEl = document.getElementById('hop-exec-error');
  const grid = document.getElementById('hop-kpi-grid');
  const meta = document.getElementById('hop-exec-meta');
  const notes = document.getElementById('hop-exec-notes');
  if (!grid) return;
  loading?.classList.remove('hidden');
  content?.classList.add('hidden');
  errorEl?.classList.add('hidden');
  try {
    const snap = await hopApi('/api/v1/hop/executive/snapshot');
    const today = snap.today || {};
    const monthly = snap.monthly || {};
    const target = Number(monthly.target || 0);
    const sales = Number(monthly.sales || 0);
    const salesVsTarget = target > 0
      ? `${sales.toLocaleString('en-IN')} / ${target.toLocaleString('en-IN')}`
      : (sales > 0 ? `${sales.toLocaleString('en-IN')} / no target set` : '0 / no target set');
    grid.innerHTML = [
      renderHopKpiCard('Quotations Pending', today.quotations_pending, 'quotations'),
      renderHopKpiCard('Quotations Sent', today.quotations_sent, 'quotations'),
      renderHopKpiCard('Orders Won', today.orders_won, 'orders'),
      renderHopKpiCard('Production', today.production_orders, 'orders'),
      renderHopKpiCard('Dispatch Due', today.dispatches_due, 'dispatches'),
      renderHopKpiCard('Outstanding', today.outstanding_receivables, 'receivables'),
      renderHopKpiCard('Collections Today', today.payments_received_today, 'payments'),
      renderHopKpiCard('Sales vs Target', salesVsTarget, 'targets'),
      renderHopKpiCard('Gross Profit %', snap.gross_profit_pct, 'profit'),
      renderHopKpiCard('Cash Available', snap.cash_available, null),
    ].join('');
    if (meta) meta.textContent = `Workspace ${snap.workspace_id || 'house_of_prizm'} · as of ${snap.as_of || '—'}`;
    if (notes) {
      const n = snap.notes || {};
      notes.textContent = [n.gross_profit_pct, n.cash_available].filter(Boolean).join(' · ');
    }
    loading?.classList.add('hidden');
    content?.classList.remove('hidden');
  } catch (error) {
    loading?.classList.add('hidden');
    if (errorEl) {
      errorEl.textContent = error.message || 'Unable to load snapshot';
      errorEl.classList.remove('hidden');
    }
  }
}

async function hopEnsureLookups() {
  try {
    if (!hopState.customers.length) hopState.customers = await hopApi('/api/v1/hop/customers') || [];
    if (!hopState.projects.length) hopState.projects = await hopApi('/api/v1/hop/projects') || [];
    if (!hopState.vendors.length) hopState.vendors = await hopApi('/api/v1/hop/vendors') || [];
    if (!hopState.invoices.length) hopState.invoices = await hopApi('/api/v1/hop/invoices') || [];
    if (!hopState.orders.length) hopState.orders = await hopApi('/api/v1/hop/orders') || [];
  } catch (_) { /* ignore */ }
}

function hopCustomerOptions(selectedId, opts) {
  const withAdd = opts && opts.withAddNew;
  const rows = [];
  const sel = selectedId != null && selectedId !== '' ? String(selectedId) : '';
  rows.push(`<option value=""${sel === '' ? ' selected' : ''}>— Select customer —</option>`);
  if (withAdd) {
    rows.push('<option value="__new__">+ Add new party…</option>');
  }
  hopState.customers.forEach((c) => {
    rows.push(`<option value="${c.id}"${String(c.id) === sel ? ' selected' : ''}>${foEscapeText(c.company)}</option>`);
  });
  return rows.join('');
}

function hopProjectOptions(selectedId) {
  return ['<option value="">— Select project —</option>']
    .concat(hopState.projects.map((p) => `<option value="${p.id}"${String(p.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(p.project_name)}</option>`))
    .join('');
}

function hopVendorOptions(selectedId) {
  return ['<option value="">— Select vendor —</option>']
    .concat(hopState.vendors.map((v) => `<option value="${v.id}"${String(v.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(v.company)}</option>`))
    .join('');
}

function hopInvoiceOptions(selectedId) {
  return ['<option value="">— Select invoice —</option>']
    .concat(hopState.invoices.map((i) => `<option value="${i.id}"${String(i.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(i.invoice_no)} · bal ${hopMoney(i.balance)}</option>`))
    .join('');
}

function hopOrderOptions(selectedId) {
  return ['<option value="">— Select order —</option>']
    .concat(hopState.orders.map((o) => `<option value="${o.id}"${String(o.id) === String(selectedId || '') ? ' selected' : ''}>${foEscapeText(o.po_number || `#${o.id}`)}</option>`))
    .join('');
}

function hopStageOptions(list, selected) {
  return list.map((s) => `<option value="${s}"${s === selected ? ' selected' : ''}>${foEscapeText(s)}</option>`).join('');
}

function hopLeadAddNewParty() {
  const sel = document.getElementById('f-lcustomer');
  if (sel) sel.value = '';
  hopState._leadFormAwaitingCustomer = true;
  if (typeof hopOpenPartyEditModal === 'function') {
    hopOpenPartyEditModal('customer', null);
  } else if (typeof window.hopOpenPartyEditModal === 'function') {
    window.hopOpenPartyEditModal('customer', null);
  }
}

function hopLeadCustomerChange() {
  const sel = document.getElementById('f-lcustomer');
  if (!sel) return;
  if (sel.value === '__new__') {
    hopLeadAddNewParty();
  }
}

function hopLeadProjectNameInput() {
  /* project is free-text only on lead form */
}

async function hopEnsureProductCatalogue() {
  if (Array.isArray(hopState._productCatalogue) && hopState._productCatalogue.length) return hopState._productCatalogue;
  try {
    hopState._productCatalogue = await hopApi('/api/v1/hop/products') || [];
  } catch (_) {
    hopState._productCatalogue = [];
  }
  return hopState._productCatalogue;
}

function hopLeadProductsSyncHidden() {
  const list = hopState._leadProducts || [];
  const hidden = document.getElementById('f-lproducts');
  if (hidden) hidden.value = list.join(', ');
}

function hopLeadProductsRenderChips() {
  const wrap = document.getElementById('f-lproducts-chips');
  if (!wrap) return;
  const list = hopState._leadProducts || [];
  wrap.innerHTML = list.map((name, idx) => `
    <span class="hop-prod-chip">
      ${foEscapeText(name)}
      <button type="button" aria-label="Remove" onclick="hopLeadProductsRemove(${idx})">×</button>
    </span>`).join('');
  hopLeadProductsSyncHidden();
  const countEl = document.getElementById('f-lproducts-count');
  if (countEl) countEl.textContent = list.length ? `${list.length} selected` : 'None selected';
}

function hopLeadProductsRemove(idx) {
  if (!Array.isArray(hopState._leadProducts)) return;
  hopState._leadProducts.splice(idx, 1);
  hopLeadProductsRenderChips();
  // Refresh picker list if open
  if (document.getElementById('hop-lead-prod-overlay')) hopLeadProductsFillPicker();
}

function hopLeadProductsAdd(name) {
  const n = String(name || '').trim();
  if (!n) return;
  if (!Array.isArray(hopState._leadProducts)) hopState._leadProducts = [];
  const exists = hopState._leadProducts.some((x) => x.toLowerCase() === n.toLowerCase());
  if (!exists) hopState._leadProducts.push(n);
  hopLeadProductsRenderChips();
  const q = document.getElementById('hop-lead-prod-q');
  if (q) {
    q.value = '';
    q.focus();
  }
  hopLeadProductsFillPicker();
}

function hopCloseLeadProductsPicker() {
  document.getElementById('hop-lead-prod-overlay')?.remove();
}

async function hopOpenLeadProductsPicker() {
  try {
    await hopEnsureProductCatalogue();
  } catch (_) { /* ignore */ }
  hopCloseLeadProductsPicker();
  const overlay = document.createElement('div');
  overlay.id = 'hop-lead-prod-overlay';
  overlay.className = 'hop-lead-prod-overlay is-open';
  overlay.innerHTML = `
    <div class="hop-lead-prod-backdrop" data-hop-prod-backdrop="1"></div>
    <div class="hop-lead-prod-dialog" role="dialog" aria-modal="true" aria-label="Products interested">
      <div class="hop-lead-prod-head">
        <div>
          <p class="hop-lead-prod-kicker">Products interested</p>
          <h3>Select products</h3>
          <p class="hop-lead-prod-sub">Search catalogue or add a name manually. Select multiple — after each pick, search stays open for the next.</p>
        </div>
        <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--ghost" data-hop-prod-done="1">Done</button>
      </div>
      <div class="hop-lead-prod-selected" id="hop-lead-prod-selected-chips"></div>
      <div class="hop-lead-prod-search">
        <input id="hop-lead-prod-q" type="text" autocomplete="off" placeholder="Type product name…" />
      </div>
      <div class="hop-lead-prod-table-wrap">
        <div class="hop-lead-prod-table-head">
          <span>Item</span><span>Sale price</span><span>Purchase price</span><span>Stock</span><span></span>
        </div>
        <div id="hop-lead-prod-rows" class="hop-lead-prod-rows"></div>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const dialog = overlay.querySelector('.hop-lead-prod-dialog');
  dialog?.addEventListener('mousedown', (e) => e.stopPropagation());
  dialog?.addEventListener('click', (e) => e.stopPropagation());
  overlay.querySelector('[data-hop-prod-backdrop]')?.addEventListener('click', () => hopCloseLeadProductsPicker());
  overlay.querySelector('[data-hop-prod-done]')?.addEventListener('click', () => hopCloseLeadProductsPicker());
  const q = document.getElementById('hop-lead-prod-q');
  q?.addEventListener('input', () => hopLeadProductsFillPicker());
  q?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const val = String(q.value || '').trim();
      if (val) hopLeadProductsAdd(val);
    }
  });
  hopLeadProductsSyncPickerChips();
  hopLeadProductsFillPicker();
  requestAnimationFrame(() => q?.focus());
}

function hopLeadProductsSyncPickerChips() {
  const wrap = document.getElementById('hop-lead-prod-selected-chips');
  if (!wrap) return;
  const list = hopState._leadProducts || [];
  wrap.innerHTML = list.length
    ? list.map((name, idx) => `
      <span class="hop-prod-chip">
        ${foEscapeText(name)}
        <button type="button" aria-label="Remove" data-hop-prod-remove="${idx}">×</button>
      </span>`).join('')
    : '<span class="hop-lead-prod-empty">No products selected yet</span>';
  wrap.querySelectorAll('[data-hop-prod-remove]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      hopLeadProductsRemove(Number(btn.getAttribute('data-hop-prod-remove')));
    });
  });
}

function hopLeadProductsFillPicker() {
  const qEl = document.getElementById('hop-lead-prod-q');
  const rowsEl = document.getElementById('hop-lead-prod-rows');
  if (!rowsEl) return;
  const q = String(qEl?.value || '').trim();
  const ql = q.toLowerCase();
  const all = hopState._productCatalogue || [];
  const selected = new Set((hopState._leadProducts || []).map((x) => x.toLowerCase()));
  const matches = (!ql ? all.slice(0, 40) : all.filter((p) => {
    const blob = `${p.name || ''} ${p.code || ''} ${p.brand || ''} ${p.category || ''}`.toLowerCase();
    return blob.includes(ql);
  }).slice(0, 50));

  const parts = [];
  if (q) {
    parts.push(`<button type="button" class="hop-lead-prod-add-row" data-hop-prod-add="${foEscapeAttr(q)}">+ Add “${foEscapeText(q)}” as custom product</button>`);
  }
  matches.forEach((p) => {
    const label = p.name || p.code || `Product #${p.id}`;
    const already = selected.has(String(label).toLowerCase());
    parts.push(`<div class="hop-lead-prod-row${already ? ' is-picked' : ''}">
      <strong>${foEscapeText(label)}</strong>
      <span>${hopMoney(p.selling_price)}</span>
      <span>${hopMoney(p.purchase_price)}</span>
      <span>${foEscapeText(p.stock_qty ?? '—')}</span>
      <button type="button" class="hop-custom-studio-btn hop-custom-studio-btn--primary hop-lead-prod-pick"
        data-hop-prod-add="${foEscapeAttr(label)}" ${already ? 'disabled' : ''}>${already ? 'Added' : 'Select'}</button>
    </div>`);
  });
  if (!matches.length) {
    parts.push(`<p class="hop-lead-prod-empty">No catalogue match.${q ? ' Use + Add above to keep this name.' : ''}</p>`);
  }
  rowsEl.innerHTML = parts.join('');
  rowsEl.querySelectorAll('[data-hop-prod-add]').forEach((btn) => {
    if (btn.disabled) return;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      hopLeadProductsAdd(btn.getAttribute('data-hop-prod-add') || '');
    });
  });
  hopLeadProductsSyncPickerChips();
}

/* ---------- Vyapar-style Edit Party modal (Nexora theme) ---------- */
const HOP_INDIAN_STATES = [
  'Andhra Pradesh','Arunachal Pradesh','Assam','Bihar','Chhattisgarh','Delhi','Goa','Gujarat','Haryana',
  'Himachal Pradesh','Jharkhand','Karnataka','Kerala','Madhya Pradesh','Maharashtra','Manipur','Meghalaya',
  'Mizoram','Nagaland','Odisha','Punjab','Rajasthan','Sikkim','Tamil Nadu','Telangana','Tripura',
  'Uttar Pradesh','Uttarakhand','West Bengal','Jammu and Kashmir','Ladakh','Puducherry','Chandigarh',
];

/** GSTIN first-2-digit → state (same as backend decode). */
const HOP_GSTIN_STATE_CODES = {
  '01': 'Jammu and Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab', '04': 'Chandigarh',
  '05': 'Uttarakhand', '06': 'Haryana', '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
  '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh', '13': 'Nagaland', '14': 'Manipur',
  '15': 'Mizoram', '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam', '19': 'West Bengal',
  '20': 'Jharkhand', '21': 'Odisha', '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
  '27': 'Maharashtra', '29': 'Karnataka', '30': 'Goa', '32': 'Kerala', '33': 'Tamil Nadu',
  '34': 'Puducherry', '36': 'Telangana', '37': 'Andhra Pradesh', '38': 'Ladakh',
};

let _hopGstLookupTimer = null;
let _hopGstLookupLast = '';

function hopClosePartyEditModal() {
  if (_hopGstLookupTimer) clearTimeout(_hopGstLookupTimer);
  _hopGstLookupTimer = null;
  _hopGstLookupLast = '';
  document.getElementById('hop-party-edit-modal')?.remove();
  hopState.contactEdit = null;
}

function hopPartyModalSetTab(tab) {
  const root = document.getElementById('hop-party-edit-modal');
  if (!root) return;
  root.querySelectorAll('[data-party-tab]').forEach((btn) => {
    btn.classList.toggle('is-active', btn.getAttribute('data-party-tab') === tab);
  });
  root.querySelectorAll('[data-party-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.getAttribute('data-party-panel') !== tab);
  });
}

function hopPartyGstHint(msg, kind) {
  const el = document.getElementById('pm-gst-hint');
  if (!el) return;
  el.textContent = msg || '';
  el.classList.toggle('is-error', kind === 'error');
  el.classList.toggle('is-ok', kind === 'ok');
}

function hopPartyApplyGstLocal(gstin) {
  const g = String(gstin || '').trim().toUpperCase();
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(g)) return false;
  const state = HOP_GSTIN_STATE_CODES[g.slice(0, 2)];
  const pan = g.slice(2, 12);
  const stateEl = document.getElementById('pm-state');
  const panEl = document.getElementById('pm-pan');
  const gstTypeEl = document.getElementById('pm-gst-type');
  if (stateEl && state) {
    const hasOpt = Array.from(stateEl.options).some((o) => o.value === state || o.textContent === state);
    if (hasOpt) stateEl.value = state;
  }
  if (panEl && pan && !String(panEl.value || '').trim()) panEl.value = pan;
  if (gstTypeEl && String(gstTypeEl.value || '').includes('Unregistered')) {
    gstTypeEl.value = 'Registered Business - Regular';
  }
  return true;
}

function hopPartyFillFromGstLookup(data, force) {
  if (!data) return;
  const setIf = (id, val) => {
    if (val == null || val === '') return;
    const el = document.getElementById(id);
    if (!el) return;
    if (force || !String(el.value || '').trim()) el.value = val;
  };
  if (data.state) {
    const stateEl = document.getElementById('pm-state');
    if (stateEl) {
      const hasOpt = Array.from(stateEl.options).some((o) => o.value === data.state || o.textContent === data.state);
      if (hasOpt) stateEl.value = data.state;
    }
  }
  setIf('pm-pan', data.pan);
  setIf('pm-billing', data.billing_name || data.company);
  setIf('pm-name', data.company || data.billing_name);
  setIf('pm-address', data.address);
  setIf('pm-shipping', data.shipping_address || data.address);
  setIf('pm-city', data.city);
  if (data.gst_type) {
    const gstTypeEl = document.getElementById('pm-gst-type');
    if (gstTypeEl && (force || String(gstTypeEl.value || '').includes('Unregistered'))) {
      gstTypeEl.value = data.gst_type;
    }
  }
  const nameEl = document.getElementById('pm-name');
  const hint = document.getElementById('pm-bill-hint');
  if (hint && nameEl?.value) hint.textContent = `“${nameEl.value}” will be printed on your invoice.`;
}

async function hopPartyFetchGstDetails(force) {
  const input = document.getElementById('pm-gstin');
  const btn = document.getElementById('pm-gst-fetch');
  const v = String(input?.value || '').trim().toUpperCase();
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(v)) {
    hopPartyGstHint('Enter a valid 15-character GSTIN first.', 'error');
    return;
  }
  hopPartyApplyGstLocal(v);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Fetching…';
  }
  hopPartyGstHint('Looking up GSTIN details…');
  try {
    const data = await hopApi(`/api/v1/hop/gstin-lookup?gstin=${encodeURIComponent(v)}`);
    hopPartyFillFromGstLookup(data, !!force);
    _hopGstLookupLast = v;
    const hasAddr = !!(data?.address);
    if (hasAddr) {
      hopPartyGstHint(data.message || 'Billing address filled from GSTIN.', 'ok');
      hopPartyModalSetTab('gst');
    } else {
      hopPartyGstHint(
        data?.message || 'State & PAN filled from GSTIN. Full address needs GST registry API key on server.',
        'ok'
      );
    }
  } catch (e) {
    hopPartyApplyGstLocal(v);
    hopPartyGstHint(e?.message || 'GST lookup failed. State/PAN still applied.', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Fetch details';
    }
  }
}

function hopPartyGstCheck() {
  const input = document.getElementById('pm-gstin');
  const mark = document.getElementById('pm-gst-ok');
  if (!input || !mark) return;
  const v = String(input.value || '').trim().toUpperCase();
  input.value = v;
  const ok = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/.test(v);
  mark.classList.toggle('is-valid', ok);
  mark.classList.toggle('hidden', !v);
  const fetchBtn = document.getElementById('pm-gst-fetch');
  if (fetchBtn) fetchBtn.disabled = !ok;
  if (!ok) {
    if (v.length >= 15) hopPartyGstHint('GSTIN format looks invalid.', 'error');
    else if (!v) hopPartyGstHint('');
    return;
  }
  hopPartyApplyGstLocal(v);
  hopPartyGstHint('Valid GSTIN — State/PAN applied. Click Fetch details for address.', 'ok');
  if (_hopGstLookupTimer) clearTimeout(_hopGstLookupTimer);
  // Auto-fetch once when a new valid GSTIN is fully typed (empty address only).
  _hopGstLookupTimer = setTimeout(() => {
    if (_hopGstLookupLast === v) return;
    const addr = String(document.getElementById('pm-address')?.value || '').trim();
    if (!addr) hopPartyFetchGstDetails(false);
  }, 650);
}

function hopPartyParseAdditionalFields(raw) {
  const fallback = [
    { enabled: true, name: 'Mobile Number', show_in_print: true, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
    { enabled: false, name: '', show_in_print: false, value: '' },
  ];
  if (!raw) return fallback;
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!Array.isArray(parsed) || !parsed.length) return fallback;
    while (parsed.length < 4) parsed.push({ enabled: false, name: '', show_in_print: false, value: '' });
    return parsed.slice(0, 4).map((f) => ({
      enabled: !!f.enabled,
      name: f.name || '',
      show_in_print: !!f.show_in_print,
      value: f.value || '',
    }));
  } catch (_) {
    return fallback;
  }
}

function hopPartyCollectAdditionalFields() {
  const out = [];
  for (let i = 0; i < 4; i += 1) {
    out.push({
      enabled: !!document.getElementById(`pm-af-en-${i}`)?.checked,
      name: document.getElementById(`pm-af-name-${i}`)?.value || '',
      show_in_print: !!document.getElementById(`pm-af-print-${i}`)?.checked,
      value: document.getElementById(`pm-af-val-${i}`)?.value || '',
    });
  }
  return JSON.stringify(out);
}

function hopPartySetCreditLimitMode(mode) {
  const custom = mode === 'custom';
  document.getElementById('pm-credit-no')?.classList.toggle('is-active', !custom);
  document.getElementById('pm-credit-custom')?.classList.toggle('is-active', custom);
  const wrap = document.getElementById('pm-credit-limit-wrap');
  if (wrap) wrap.classList.toggle('hidden', !custom);
  const flag = document.getElementById('pm-credit-no-limit');
  if (flag) flag.value = custom ? '0' : '1';
}

function hopPartyCopyBillingToShipping() {
  const bill = document.getElementById('pm-address')?.value || '';
  const ship = document.getElementById('pm-shipping');
  if (ship) ship.value = bill;
}

const HOP_DEFAULT_PARTY_GROUPS = ['Buyer', 'Supplier'];

function hopPartyGroupStorageKey() {
  return 'hop_party_groups_v1';
}

function hopPartyGroupSavedList() {
  try {
    const raw = localStorage.getItem(hopPartyGroupStorageKey());
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.map((x) => String(x || '').trim()).filter(Boolean) : [];
  } catch (_) {
    return [];
  }
}

function hopPartyGroupPersist(name) {
  const n = String(name || '').trim();
  if (!n) return;
  const all = new Set([...HOP_DEFAULT_PARTY_GROUPS, ...hopPartyGroupSavedList(), n]);
  localStorage.setItem(hopPartyGroupStorageKey(), JSON.stringify([...all].sort((a, b) => a.localeCompare(b))));
}

function hopPartyGroupAllOptions() {
  const fromData = [];
  (hopState.customers || []).forEach((c) => {
    if (c.customer_type) fromData.push(String(c.customer_type).trim());
  });
  (hopState._parties || []).forEach((p) => {
    if (p.customer_type) fromData.push(String(p.customer_type).trim());
    if (p._type === 'vendor') fromData.push('Supplier');
  });
  const set = new Set([...HOP_DEFAULT_PARTY_GROUPS, ...hopPartyGroupSavedList(), ...fromData.filter(Boolean)]);
  return [...set].sort((a, b) => a.localeCompare(b));
}

function hopPartyGroupCloseMenu() {
  document.getElementById('pm-group-menu')?.classList.add('hidden');
}

function hopPartyGroupOpenMenu() {
  const menu = document.getElementById('pm-group-menu');
  const input = document.getElementById('pm-group');
  if (!menu || !input) return;
  hopPartyGroupRenderMenu(input.value || '');
  menu.classList.remove('hidden');
}

function hopPartyGroupRenderMenu(query) {
  const menu = document.getElementById('pm-group-menu');
  if (!menu) return;
  const q = String(query || '').trim().toLowerCase();
  const groups = hopPartyGroupAllOptions().filter((g) => !q || g.toLowerCase().includes(q));
  const exact = hopPartyGroupAllOptions().some((g) => g.toLowerCase() === q);
  menu.innerHTML = `
    <button type="button" class="nx-party-group-new" onclick="hopPartyGroupCreateNew()">+ New Group</button>
    ${groups.map((g) => `
      <button type="button" class="nx-party-group-opt" onclick="hopPartyGroupSelect('${foEscapeAttr(g)}')">${foEscapeText(g)}</button>
    `).join('') || '<p class="nx-party-group-empty">No groups yet</p>'}
    ${q && !exact ? `<button type="button" class="nx-party-group-opt nx-party-group-create" onclick="hopPartyGroupSelect('${foEscapeAttr(String(query || '').trim())}')">Create “${foEscapeText(String(query || '').trim())}”</button>` : ''}
  `;
}

function hopPartyGroupSelect(name) {
  const n = String(name || '').trim();
  if (!n) return;
  hopPartyGroupPersist(n);
  const input = document.getElementById('pm-group');
  if (input) input.value = n;
  hopPartyGroupCloseMenu();
}

function hopPartyGroupCreateNew() {
  const typed = String(document.getElementById('pm-group')?.value || '').trim();
  const name = window.prompt('New party group name', typed || '');
  if (name == null) return;
  const clean = String(name).trim();
  if (!clean) return;
  hopPartyGroupSelect(clean);
}

function hopPartyGroupOnInput() {
  const input = document.getElementById('pm-group');
  hopPartyGroupOpenMenu();
  hopPartyGroupRenderMenu(input?.value || '');
}

function hopCloseAddPartyChooser() {
  document.getElementById('hop-add-party-chooser')?.remove();
}

/** Party modals live on document.body (outside workspace) — stamp active theme so CSS can match. */
function hopDecoratePartyModalTheme(modal) {
  if (!modal) return;
  const theme = document.documentElement.getAttribute('data-hop-theme') || HOP_DEFAULT_THEME;
  modal.setAttribute('data-party-theme', theme);
  modal.classList.remove('hop-party-theme-dark', 'hop-party-theme-light');
  const isLight = theme === 'bright' || theme === 'emerald' || theme === 'custom';
  modal.classList.add(isLight ? 'hop-party-theme-light' : 'hop-party-theme-dark');
}

function hopOpenAddPartyChooser() {
  hopCloseAddPartyChooser();
  hopClosePartyScanModal();
  const modal = document.createElement('div');
  modal.id = 'hop-add-party-chooser';
  modal.className = 'nx-party-modal hop-add-party-chooser';
  hopDecoratePartyModalTheme(modal);
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopCloseAddPartyChooser()"></div>
    <div class="nx-party-modal-card hop-add-party-card" role="dialog" aria-label="Add Party">
      <div class="nx-party-modal-head">
        <h2>Add Party</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopCloseAddPartyChooser()" title="Close">&times;</button>
      </div>
      <div class="hop-add-party-choices">
        <button type="button" class="hop-add-party-choice" onclick="hopAddPartyViaScan()">
          <strong>Scan visiting card</strong>
          <span>Capture card · auto-fill details</span>
        </button>
        <button type="button" class="hop-add-party-choice hop-add-party-choice--manual" onclick="hopAddPartyManual()">
          <strong>Manual</strong>
          <span>Enter details yourself</span>
        </button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('is-open'));
}

function hopAddPartyManual() {
  hopCloseAddPartyChooser();
  hopOpenPartyEditModal('customer', null);
}

function hopAddPartyViaScan() {
  hopCloseAddPartyChooser();
  hopOpenPartyScanModal();
}

function hopClosePartyScanModal() {
  document.getElementById('hop-party-scan-modal')?.remove();
  hopState.vcardMode = null;
}

function hopOpenPartyScanModal() {
  hopClosePartyScanModal();
  hopState.vcardMode = 'party';
  hopState.visitingCardFile = null;
  const modal = document.createElement('div');
  modal.id = 'hop-party-scan-modal';
  modal.className = 'nx-party-modal hop-party-scan-modal';
  hopDecoratePartyModalTheme(modal);
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopClosePartyScanModal()"></div>
    <div class="nx-party-modal-card hop-party-scan-card" role="dialog" aria-label="Scan visiting card">
      <div class="nx-party-modal-head">
        <h2>Scan visiting card</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopClosePartyScanModal()" title="Close">&times;</button>
      </div>
      <div class="hop-party-scan-body">
        <div class="hop-vcard-source">
          <button type="button" class="hop-vcard-src-btn hop-vcard-src-btn--primary" onclick="hopPickPhoto('hop-vcard-cam')">Camera</button>
          <button type="button" class="hop-vcard-src-btn" onclick="hopPickPhoto('hop-vcard-gal')">Gallery</button>
        </div>
        <input id="hop-vcard-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-vcard-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-vcard-preview" class="hop-vcard-stage is-empty">
          <div class="hop-vcard-empty">
            <strong>Take or upload card photo</strong>
            <span>Clear photo · good light · flat card</span>
          </div>
        </div>
        <div class="hop-vcard-capture-foot">
          <button type="button" class="nx-btn" onclick="hopClosePartyScanModal(); hopOpenAddPartyChooser();">Back</button>
          <button type="button" class="nx-btn nx-btn-primary" id="hop-vcard-scan-btn" onclick="hopScanVisitingCard()">Read card</button>
        </div>
        <p id="hop-vcard-status" class="hop-vcard-status">Waiting for photo</p>
        <div id="hop-vcard-form" class="hidden" aria-hidden="true"></div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('is-open'));

  const bindPreview = (inputId) => {
    const input = document.getElementById(inputId);
    input?.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const otherId = inputId === 'hop-vcard-cam' ? 'hop-vcard-gal' : 'hop-vcard-cam';
      const other = document.getElementById(otherId);
      if (other) other.value = '';
      hopState.visitingCardFile = file;
      const box = document.getElementById('hop-vcard-preview');
      if (box) {
        box.classList.remove('is-empty');
        box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
      }
      const status = document.getElementById('hop-vcard-status');
      if (status) {
        status.textContent = 'Photo ready — reading…';
        status.classList.add('is-busy');
        status.classList.remove('is-error', 'is-ok');
      }
      hopScheduleVisitingCardScan();
    });
  };
  bindPreview('hop-vcard-cam');
  bindPreview('hop-vcard-gal');
}

function hopPartyRowFromCardFields(f) {
  const fields = f || {};
  return {
    company: fields.company || '',
    billing_name: fields.company || '',
    contact_person: fields.contact_person || '',
    mobile: fields.mobile || '',
    email: fields.email || '',
    city: fields.city || '',
    customer_type: fields.customer_type || 'Buyer',
    gst_no: fields.gst_no || '',
    pan: fields.pan || '',
    address: fields.address || '',
    remarks: fields.remarks || '',
    source: 'visiting_card',
  };
}

function hopOpenPartyEditModal(kind, row) {
  const isVendor = kind === 'vendor';
  const isEdit = !!(row && row.id != null);
  const data = row || {};
  hopState.contactEdit = isEdit ? { kind, id: Number(data.id) } : { kind, id: null };
  const title = isEdit ? 'Edit Party' : 'Add Party';
  const partyName = data.company || '';
  const billingName = data.billing_name || data.company || '';
  const group = isVendor ? 'Supplier' : (data.customer_type || 'Buyer');
  const gstType = data.gst_type || data.industry || (data.gst_no ? 'Registered Business - Regular' : 'Unregistered');
  const stateVal = data.state || '';
  const txnBal = Number(data._balance || 0);
  const openingBal = data.opening_balance != null && data.opening_balance !== '' ? data.opening_balance : '';
  const openingDate = (data.opening_balance_date || new Date().toISOString().slice(0, 10));
  const noLimit = data.credit_no_limit == null ? 1 : Number(data.credit_no_limit);
  const creditLimit = data.credit_limit != null && data.credit_limit !== '' ? data.credit_limit : '';
  const shipping = data.shipping_address || data.address || '';
  const billingAddr = data.address || '';
  const addFields = hopPartyParseAdditionalFields(data.additional_fields);
  if (data.mobile && addFields[0] && addFields[0].name.toLowerCase().includes('mobile') && !addFields[0].value) {
    addFields[0].value = data.mobile;
  }
  const stateOpts = HOP_INDIAN_STATES.map((s) =>
    `<option value="${foEscapeAttr(s)}"${String(stateVal).toLowerCase() === s.toLowerCase() ? ' selected' : ''}>${foEscapeText(s)}</option>`
  ).join('');
  const afRows = addFields.map((f, i) => `
    <div class="nx-party-af-row">
      <label class="nx-party-af-check"><input type="checkbox" id="pm-af-en-${i}"${f.enabled ? ' checked' : ''} /></label>
      <input id="pm-af-name-${i}" class="nx-party-af-name" placeholder="Additional Field ${i + 1} Name" value="${foEscapeAttr(f.name)}" />
      <label class="nx-party-af-print">
        <span>Show in Print</span>
        <input type="checkbox" id="pm-af-print-${i}"${f.show_in_print ? ' checked' : ''} />
      </label>
      <input id="pm-af-val-${i}" class="nx-party-af-val" placeholder="Enter value" value="${foEscapeAttr(f.value)}" />
    </div>`).join('');

  document.getElementById('hop-party-edit-modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'hop-party-edit-modal';
  modal.className = 'nx-party-modal';
  hopDecoratePartyModalTheme(modal);
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopClosePartyEditModal()"></div>
    <div class="nx-party-modal-card" role="dialog" aria-label="${foEscapeAttr(title)}">
      <div class="nx-party-modal-head">
        <h2>${foEscapeText(title)}</h2>
        <button type="button" class="nx-party-modal-close" onclick="hopClosePartyEditModal()" title="Close">&times;</button>
      </div>
      <div class="nx-party-modal-body">
        <div class="nx-party-primary">
          <label class="nx-party-field">
            <span>Party Name *</span>
            <input id="pm-name" value="${foEscapeAttr(partyName)}" placeholder="Party / company name"
              oninput="hopPartyNameOnInput(this)" />
            <small id="pm-bill-hint" class="nx-party-hint">${partyName ? `“${foEscapeText(partyName)}” will be printed on your invoice.` : ''}</small>
            <div id="pm-live-dup" class="hop-party-live-dup hidden" aria-live="polite"></div>
          </label>
          <label class="nx-party-field">
            <span>Billing Name</span>
            <input id="pm-billing" value="${foEscapeAttr(billingName)}" placeholder="Name on invoice" />
          </label>
          <label class="nx-party-field nx-party-gst-wrap">
            <span>GSTIN</span>
            <div class="nx-party-gst-row">
              <div class="nx-party-gst-input-wrap">
                <input id="pm-gstin" value="${foEscapeAttr(data.gst_no || '')}" placeholder="22AAAAA0000A1Z5" maxlength="15"
                  oninput="hopPartyGstCheck();hopPartyLiveDupCheck()" onblur="hopPartyGstCheck();hopPartyLiveDupCheck()" />
                <span id="pm-gst-ok" class="nx-party-gst-ok hidden" title="Valid GSTIN">✓</span>
              </div>
              <button type="button" id="pm-gst-fetch" class="nx-party-gst-fetch" onclick="hopPartyFetchGstDetails(true)" disabled title="Auto-fill name, state &amp; address from GSTIN">Fetch details</button>
            </div>
            <small id="pm-gst-hint" class="nx-party-hint nx-party-gst-hint"></small>
          </label>
          <label class="nx-party-field">
            <span>Phone Number</span>
            <input id="pm-phone" value="${foEscapeAttr(data.mobile || '')}" placeholder="10-digit mobile" oninput="hopPartyLiveDupCheck()" />
          </label>
          <label class="nx-party-field nx-party-group-field">
            <span>Party Group</span>
            <div class="nx-party-group-combo">
              <input id="pm-group" type="text" autocomplete="off" value="${foEscapeAttr(group === 'Vendor' ? 'Supplier' : group)}"
                placeholder="Search or select group"
                onfocus="hopPartyGroupOpenMenu()"
                oninput="hopPartyGroupOnInput()"
                onkeydown="if(event.key==='Escape'){hopPartyGroupCloseMenu();}" />
              <button type="button" class="nx-party-group-caret" onclick="hopPartyGroupOpenMenu()" tabindex="-1" aria-label="Open groups">▾</button>
              <div id="pm-group-menu" class="nx-party-group-menu hidden"></div>
            </div>
          </label>
        </div>

        <div class="nx-party-tabs">
          <button type="button" class="nx-party-tab is-active" data-party-tab="gst" onclick="hopPartyModalSetTab('gst')">GST &amp; Address</button>
          <button type="button" class="nx-party-tab" data-party-tab="credit" onclick="hopPartyModalSetTab('credit')">Credit &amp; Balance <span class="nx-party-tab-badge">New</span></button>
          <button type="button" class="nx-party-tab" data-party-tab="more" onclick="hopPartyModalSetTab('more')">Additional Fields</button>
        </div>

        <div class="nx-party-panels">
          <div class="nx-party-panel" data-party-panel="gst">
            <div class="nx-party-gst-grid">
              <div class="nx-party-gst-left">
                <label class="nx-party-field">
                  <span>GST Type</span>
                  <select id="pm-gst-type">
                    <option${String(gstType).includes('Unregistered') ? ' selected' : ''}>Unregistered</option>
                    <option${String(gstType).includes('Regular') ? ' selected' : ''}>Registered Business - Regular</option>
                    <option${String(gstType).includes('Composition') ? ' selected' : ''}>Registered Business - Composition</option>
                  </select>
                </label>
                <label class="nx-party-field">
                  <span>State</span>
                  <select id="pm-state">
                    <option value="">Select state</option>
                    ${stateOpts}
                  </select>
                </label>
                <label class="nx-party-field">
                  <span>Email ID</span>
                  <input id="pm-email" type="email" value="${foEscapeAttr(data.email || '')}" placeholder="name@company.com" />
                </label>
                <label class="nx-party-field">
                  <span>Contact Person</span>
                  <input id="pm-contact" value="${foEscapeAttr(data.contact_person || '')}" placeholder="Contact person" />
                </label>
                <label class="nx-party-field">
                  <span>City</span>
                  <input id="pm-city" value="${foEscapeAttr(data.city || '')}" placeholder="City" />
                </label>
              </div>
              <div class="nx-party-addr-col">
                <div class="nx-party-addr-head">
                  <strong>Billing Address</strong>
                  <button type="button" class="nx-party-addr-add" onclick="document.getElementById('pm-address')?.focus()">+ Add New Address</button>
                </div>
                <div class="nx-party-addr-card">
                  <textarea id="pm-address" rows="6" placeholder="Billing address">${foEscapeText(billingAddr)}</textarea>
                </div>
              </div>
              <div class="nx-party-addr-col">
                <div class="nx-party-addr-head">
                  <strong>Shipping Address</strong>
                  <button type="button" class="nx-party-addr-add" onclick="hopPartyCopyBillingToShipping()">+ Same as Billing</button>
                </div>
                <div class="nx-party-addr-card">
                  <textarea id="pm-shipping" rows="6" placeholder="Shipping address">${foEscapeText(shipping)}</textarea>
                </div>
              </div>
            </div>
          </div>

          <div class="nx-party-panel hidden" data-party-panel="credit">
            <div class="nx-party-credit-grid">
              <label class="nx-party-field">
                <span>Opening Balance</span>
                <input id="pm-opening-balance" type="number" step="0.01" value="${foEscapeAttr(openingBal)}" placeholder="0.00" />
              </label>
              <label class="nx-party-field">
                <span>As Of Date</span>
                <input id="pm-opening-date" type="date" value="${foEscapeAttr(String(openingDate).slice(0, 10))}" />
              </label>
              <div class="nx-party-field nx-party-span2">
                <span>Credit Limit</span>
                <input type="hidden" id="pm-credit-no-limit" value="${noLimit ? '1' : '0'}" />
                <div class="nx-party-credit-toggle">
                  <button type="button" id="pm-credit-no" class="nx-party-tog${noLimit ? ' is-active' : ''}" onclick="hopPartySetCreditLimitMode('none')">No Limit</button>
                  <button type="button" id="pm-credit-custom" class="nx-party-tog${!noLimit ? ' is-active' : ''}" onclick="hopPartySetCreditLimitMode('custom')">Custom Limit</button>
                </div>
                <div id="pm-credit-limit-wrap" class="nx-party-credit-limit-wrap${!noLimit ? '' : ' hidden'}">
                  <input id="pm-credit-limit" type="number" step="0.01" value="${foEscapeAttr(creditLimit)}" placeholder="Enter credit limit" />
                </div>
              </div>
              <label class="nx-party-field">
                <span>Current Receivable (from transactions)</span>
                <input type="text" value="₹ ${txnBal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}" readonly />
                <small class="nx-party-hint">Auto-calculated from party transactions.</small>
              </label>
              <label class="nx-party-field">
                <span>Status</span>
                <select id="pm-status">
                  <option value="active"${(data.status || 'active') === 'active' ? ' selected' : ''}>Active</option>
                  <option value="inactive"${data.status === 'inactive' ? ' selected' : ''}>Inactive</option>
                </select>
              </label>
            </div>
          </div>

          <div class="nx-party-panel hidden" data-party-panel="more">
            <div class="nx-party-af-list">
              ${afRows}
            </div>
            <div class="nx-party-more-grid" style="margin-top:16px;">
              <label class="nx-party-field"><span>PAN</span><input id="pm-pan" value="${foEscapeAttr(data.pan || '')}" /></label>
              <label class="nx-party-field"><span>Hotel Brand</span><input id="pm-hotel" value="${foEscapeAttr(data.hotel_brand || '')}" /></label>
              <label class="nx-party-field"><span>Architect</span><input id="pm-architect" value="${foEscapeAttr(data.architect || '')}" /></label>
              <label class="nx-party-field"><span>Consultant</span><input id="pm-consultant" value="${foEscapeAttr(data.consultant || '')}" /></label>
              <label class="nx-party-field"><span>Rating</span><input id="pm-rating" value="${foEscapeAttr(data.potential_rating || data.rating || '')}" /></label>
              <label class="nx-party-field"><span>Assigned To</span><input id="pm-assigned" value="${foEscapeAttr(data.assigned_to || '')}" /></label>
              <label class="nx-party-field"><span>Products (vendor)</span><input id="pm-products" value="${foEscapeAttr(data.products || '')}" /></label>
              <label class="nx-party-field"><span>Payment Terms</span><input id="pm-payterms" value="${foEscapeAttr(data.payment_terms || '')}" /></label>
              <label class="nx-party-field nx-party-span2"><span>Remarks</span><input id="pm-remarks" value="${foEscapeAttr(data.remarks || '')}" /></label>
            </div>
          </div>
        </div>
      </div>
      <div class="nx-party-modal-foot">
        ${isEdit ? `<button type="button" class="nx-btn nx-party-btn-delete" onclick="hopDeleteFromPartyModal()">Delete</button>` : '<span></span>'}
        <div class="nx-party-foot-right">
          <button type="button" class="nx-btn" onclick="hopClosePartyEditModal()">Cancel</button>
          <button type="button" id="pm-save-btn" class="nx-btn nx-btn-primary" onclick="hopSavePartyModal()">${isEdit ? 'Update' : 'Save'}</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(modal);
  // Close group menu when clicking outside the combo.
  modal.addEventListener('mousedown', (ev) => {
    if (!ev.target.closest?.('.nx-party-group-combo')) hopPartyGroupCloseMenu();
  });
  requestAnimationFrame(() => {
    modal.classList.add('is-open');
    hopPartyGstCheck();
    hopPartySetCreditLimitMode(noLimit ? 'none' : 'custom');
    document.getElementById('pm-name')?.focus();
  });
}

async function hopDeleteFromPartyModal() {
  const edit = hopState.contactEdit;
  if (!edit?.id) return;
  const type = edit.kind === 'vendor' ? 'vendors' : 'customers';
  const name = document.getElementById('pm-name')?.value || `contact #${edit.id}`;
  await hopDeleteContact(type, edit.id, name);
}

function hopPartyDupBannerClear() {
  document.getElementById('pm-dup-banner')?.remove();
}

function hopPartyNameOnInput(el) {
  const hint = document.getElementById('pm-bill-hint');
  if (hint) {
    const v = String(el?.value || '').trim();
    hint.textContent = v ? `“${v}” will be printed on your invoice.` : '';
  }
  hopPartyLiveDupCheck();
}

let hopPartyLiveDupTimer = null;

function hopPartyLiveDupClear(slotId) {
  const slot = document.getElementById(slotId || 'pm-live-dup');
  if (!slot) return;
  slot.classList.add('hidden');
  slot.innerHTML = '';
}

/** Live fuzzy party match while typing (Add/Edit Party + Deal new party). */
function hopPartyLiveDupCheck(opts = {}) {
  const nameId = opts.nameId || 'pm-name';
  const phoneId = opts.phoneId || 'pm-phone';
  const gstId = opts.gstId || 'pm-gstin';
  const slotId = opts.slotId || 'pm-live-dup';
  const nameEl = document.getElementById(nameId);
  const slot = document.getElementById(slotId);
  if (!nameEl || !slot) return;
  const company = String(nameEl.value || '').trim();
  clearTimeout(hopPartyLiveDupTimer);
  if (company.length < 3) {
    hopPartyLiveDupClear(slotId);
    return;
  }
  hopPartyLiveDupTimer = setTimeout(async () => {
    try {
      const edit = hopState.contactEdit;
      const payload = {
        company,
        mobile: document.getElementById(phoneId)?.value || '',
        gst_no: document.getElementById(gstId)?.value || '',
        party_type: 'both',
      };
      if (edit?.id && nameId === 'pm-name') {
        payload.exclude_id = edit.id;
        payload.exclude_party_type = edit.kind === 'vendor' ? 'vendor' : 'customer';
      }
      const data = await hopApi('/api/v1/hop/parties/check-duplicates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const matches = data?.matches || [];
      if (!matches.length || String(nameEl.value || '').trim() !== company) {
        hopPartyLiveDupClear(slotId);
        return;
      }
      const unique = [];
      const seen = new Set();
      for (const m of matches) {
        const n = String(m?.company || '').trim();
        const key = n.toLowerCase();
        if (!n || seen.has(key)) continue;
        seen.add(key);
        unique.push(m);
        if (unique.length >= 4) break;
      }
      const canPick = typeof window.hopCrmUseMatchedParty === 'function' && slotId === 'crm-newparty-dup';
      slot.classList.remove('hidden');
      slot.innerHTML = `
        <div class="hop-party-live-dup-title">Similar party already in system</div>
        <ul class="hop-party-live-dup-list">
          ${unique.map((m) => {
            const n = String(m.company || '').trim();
            if (canPick && m.id && (m.party_type || 'customer') === 'customer') {
              return `<li><button type="button" class="hop-party-live-dup-pick" onclick="hopCrmUseMatchedParty(${Number(m.id)})">Use “${foEscapeText(n)}”</button></li>`;
            }
            return `<li>${foEscapeText(n)}</li>`;
          }).join('')}
        </ul>
        <p class="hop-party-live-dup-hint">${canPick
          ? 'Click a match to use the existing party, or keep typing to save as new.'
          : 'Saving will ask confirmation — use the existing party if it’s the same person.'}</p>`;
    } catch (_) {
      /* ignore preview errors */
    }
  }, 380);
}

function hopPartyUniqueNames(matches) {
  const seen = new Set();
  const names = [];
  for (const m of matches || []) {
    const n = String(m?.company || '').trim();
    const key = n.toLowerCase();
    if (!n || seen.has(key)) continue;
    seen.add(key);
    names.push(n);
  }
  return names;
}

/** User-facing duplicate copy — names only, no scores / fuzzy jargon. */
function hopPartyDupConfirmMessage(matches, apiMessage) {
  const names = hopPartyUniqueNames(matches);
  if (names.length === 1) {
    return `A party is already saved as “${names[0]}”.\n\nDo you really want to save this as a new party?`;
  }
  if (names.length > 1) {
    const listed = names.slice(0, 3).map((n) => `“${n}”`).join(', ');
    return `Similar parties are already saved: ${listed}.\n\nDo you really want to save this as a new party?`;
  }
  return (
    apiMessage
    || 'A party with a similar name is already saved.\n\nDo you really want to save this as a new party?'
  );
}

function hopPartyDupBannerShow(message, matches) {
  hopPartyDupBannerClear();
  const body = document.querySelector('#hop-party-edit-modal .nx-party-modal-body');
  if (!body) return;
  const banner = document.createElement('div');
  banner.id = 'pm-dup-banner';
  banner.className = 'nx-party-dup-banner';
  banner.innerHTML = `
    <div class="nx-party-dup-title">Party already saved</div>
    <p>${foEscapeText(message || 'A party with a similar name is already saved.')}</p>
    <p class="nx-party-dup-hint">Choose <strong>Yes, save</strong> only if this is a different party.</p>`;
  body.prepend(banner);
  banner.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function hopSavePartyModal() {
  const btn = document.getElementById('pm-save-btn');
  const prevLabel = btn?.textContent || 'Update';
  try {
    const edit = hopState.contactEdit || { kind: 'customer', id: null };
    const group = String(document.getElementById('pm-group')?.value || 'Buyer').trim() || 'Buyer';
    hopPartyGroupPersist(group);
    const asVendor = /supplier|vendor/i.test(group);
    const kind = asVendor ? 'vendor' : 'customer';
    const saveKind = edit.id ? edit.kind : kind;
    const company = String(document.getElementById('pm-name')?.value || '').trim();
    if (!company) {
      alert('Party Name is required');
      document.getElementById('pm-name')?.focus();
      return;
    }
    hopPartyDupBannerClear();
    if (btn) {
      btn.disabled = true;
      btn.textContent = 'Saving…';
    }

    // Auto-fill state from address if user left it blank.
    let state = document.getElementById('pm-state')?.value || '';
    if (!state) {
      const blob = `${document.getElementById('pm-address')?.value || ''} ${document.getElementById('pm-shipping')?.value || ''}`;
      const hit = HOP_INDIAN_STATES.find((s) => blob.toLowerCase().includes(s.toLowerCase()));
      if (hit) {
        state = hit;
        const sel = document.getElementById('pm-state');
        if (sel) sel.value = hit;
      }
    }

    const city = String(document.getElementById('pm-city')?.value || '').trim() || null;
    const gstType = document.getElementById('pm-gst-type')?.value || '';
    const creditNoLimit = document.getElementById('pm-credit-no-limit')?.value === '1' ? 1 : 0;
    let additionalFields = '[]';
    try {
      additionalFields = hopPartyCollectAdditionalFields();
    } catch (_) {
      additionalFields = '[]';
    }
    const sharedExtra = {
      billing_name: document.getElementById('pm-billing')?.value || company,
      shipping_address: document.getElementById('pm-shipping')?.value || '',
      state,
      gst_type: gstType,
      opening_balance: document.getElementById('pm-opening-balance')?.value || '',
      opening_balance_date: document.getElementById('pm-opening-date')?.value || '',
      credit_limit: creditNoLimit ? '' : (document.getElementById('pm-credit-limit')?.value || ''),
      credit_no_limit: creditNoLimit,
      additional_fields: additionalFields,
    };
    const payloadCustomer = {
      company,
      contact_person: document.getElementById('pm-contact')?.value || '',
      mobile: document.getElementById('pm-phone')?.value || '',
      email: document.getElementById('pm-email')?.value || '',
      city,
      industry: gstType,
      customer_type: group,
      hotel_brand: document.getElementById('pm-hotel')?.value || '',
      architect: document.getElementById('pm-architect')?.value || '',
      consultant: document.getElementById('pm-consultant')?.value || '',
      potential_rating: document.getElementById('pm-rating')?.value || '',
      assigned_to: document.getElementById('pm-assigned')?.value || '',
      address: document.getElementById('pm-address')?.value || '',
      gst_no: document.getElementById('pm-gstin')?.value || '',
      pan: document.getElementById('pm-pan')?.value || '',
      status: document.getElementById('pm-status')?.value || 'active',
      remarks: document.getElementById('pm-remarks')?.value || '',
      ...sharedExtra,
    };
    const payloadVendor = {
      company,
      contact_person: document.getElementById('pm-contact')?.value || '',
      mobile: document.getElementById('pm-phone')?.value || '',
      email: document.getElementById('pm-email')?.value || '',
      city,
      products: document.getElementById('pm-products')?.value || '',
      gst_no: document.getElementById('pm-gstin')?.value || '',
      payment_terms: document.getElementById('pm-payterms')?.value || '',
      rating: document.getElementById('pm-rating')?.value || '',
      address: document.getElementById('pm-address')?.value || '',
      status: document.getElementById('pm-status')?.value || 'active',
      ...sharedExtra,
    };
    const payload = saveKind === 'vendor' ? payloadVendor : payloadCustomer;
    const urlBase = saveKind === 'vendor' ? '/api/v1/hop/vendors' : '/api/v1/hop/customers';
    const isEdit = !!(edit && edit.id);
    const saveUrl = isEdit ? `${urlBase}/${edit.id}` : urlBase;
    const result = await hopCreatePartyWithDupConfirm(saveUrl, payload, {
      method: isEdit ? 'PATCH' : 'POST',
    });
    if (result === false) {
      if (typeof nexoraToast === 'function') {
        nexoraToast('Save cancelled. Change the party name, or confirm if it is a new party.', 'warn', { duration: 5500 });
      }
      return;
    }
    if (result == null) {
      throw new Error('Session expired or save blocked. Please login again and retry Update.');
    }
    hopState.customers = [];
    hopState.vendors = [];
    hopClosePartyEditModal();
    if (typeof nexoraToast === 'function') {
      nexoraToast(isEdit ? 'Party updated.' : 'Party saved.', 'success');
    }
    if (hopState._leadFormAwaitingCustomer && saveKind === 'customer') {
      hopState._leadFormAwaitingCustomer = false;
      try {
        hopState.customers = await hopApi('/api/v1/hop/customers') || [];
      } catch (_) { hopState.customers = []; }
      const sel = document.getElementById('f-lcustomer');
      if (sel) {
        const newId = result?.id || result?.customer_id;
        sel.innerHTML = hopCustomerOptions(newId, { withAddNew: true });
        if (newId) sel.value = String(newId);
      }
      return;
    }
    openHopView(hopContactReturnView(saveKind === 'vendor' ? 'vendors' : 'customers'));
  } catch (e) {
    console.error('hopSavePartyModal failed', e);
    alert(e?.message || 'Save failed');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prevLabel;
    }
  }
}

/** Create/update party; on fuzzy duplicate ask user before force_save. Returns false if cancelled. */
async function hopCreatePartyWithDupConfirm(url, payload, options = {}) {
  const method = options.method || 'POST';
  let response;
  try {
    response = await fetchWithAuth(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    if (isSessionTimeoutError(e)) return null;
    throw e;
  }
  const data = await parseApiJson(response);
  if (response.status === 409 && (data.requires_confirmation || data?.error?.code === 'DUPLICATE_CONFIRM')) {
    const matches = data.data?.matches || [];
    const msg = hopPartyDupConfirmMessage(matches, data.message);
    hopPartyDupBannerShow(msg.replace(/\n+/g, ' '), matches);
    const ok = await nexoraConfirm(msg, {
      title: 'Party already saved',
      okText: 'Yes, save',
      cancelText: 'Cancel',
    });
    if (!ok) return false;
    hopPartyDupBannerClear();
    return hopApi(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, force_save: true }),
    });
  }
  if (!response.ok || !data.success) {
    throw new Error(getApiErrorMessage(data, 'Request failed'));
  }
  return data.data;
}

/* ---------- Parties (Vyapar-style unified view) ---------- */

/** Vyapar txn_type → Sale / Purchase menus (single source of truth). */
const HOP_TXN_SALE_INVOICE = new Set([1]);           // Sale Invoice only (real sale)
const HOP_TXN_ESTIMATE = new Set([27]);             // Estimate (HOPPI…) — not type 30
const HOP_TXN_SALE_RETURN = new Set([21]);           // Sale Return / Credit Note
const HOP_TXN_PAYMENT_IN = new Set([3]);            // Payment-In (Vyapar type 3)
const HOP_TXN_PAYMENT_OUT = new Set([4]);           // Payment-Out (Vyapar type 4)
const HOP_TXN_PURCHASE_BILL = new Set([2]);
const HOP_TXN_EXPENSE = new Set([7]);
const HOP_TXN_PURCHASE_RETURN = new Set([16]);
const HOP_TXN_OTHER_DOCS = new Set([65, 81, 82, 83, 30]); // SO, Journal, Challan, Proforma
const HOP_TXN_SALE_ORDER = new Set([65]);
const HOP_TXN_JOURNAL = new Set([81]);               // Journal Entry (menu hidden until Accounting)
const HOP_TXN_CHALLAN = new Set([30, 82]);           // 30 = this firm’s Delivery Challan; 82 = other Vyapar builds
const HOP_TXN_PROFORMA = new Set([83]);

/** Docs that are not collectible — no Paid / Unpaid / Record payment. */
function hopTxnIsNonReceivableType(ty) {
  const t = Number(ty || 0);
  return HOP_TXN_ESTIMATE.has(t)
    || HOP_TXN_PROFORMA.has(t)
    || HOP_TXN_SALE_ORDER.has(t)
    || HOP_TXN_CHALLAN.has(t);
}

function hopSaleDocIsNonReceivable(kind) {
  const k = String(kind || hopState.saleDocKind || hopState.view || '');
  return k === 'sale_estimates'
    || k === 'sale_proforma'
    || k === 'sale_orders'
    || k === 'sale_challan'
    || k === 'quotations';
}

/** Every imported type must appear in exactly one bucket (journal stays hidden in nav). */
const HOP_TXN_MENU_COVERAGE = new Set([
  ...HOP_TXN_SALE_INVOICE, ...HOP_TXN_ESTIMATE, ...HOP_TXN_PROFORMA,
  ...HOP_TXN_PAYMENT_IN, ...HOP_TXN_SALE_ORDER, ...HOP_TXN_CHALLAN,
  ...HOP_TXN_SALE_RETURN, ...HOP_TXN_PURCHASE_BILL, ...HOP_TXN_PAYMENT_OUT,
  ...HOP_TXN_EXPENSE, ...HOP_TXN_PURCHASE_RETURN, ...HOP_TXN_JOURNAL,
]);

function hopTxnTypeOf(row) {
  return Number(row?.txn_type || 0);
}

function hopTxnStatusOf(row) {
  return String(row?.status_text || row?.status || '').trim().toLowerCase();
}

/** Never show Vyapar's "Approved" wording in the UI — map to Final. */
function hopScrubVyaparStatusLabel(label) {
  let raw = String(label || '').trim();
  if (!raw) return raw;
  if (/^approv(ed|e|al)?$/i.test(raw)) return 'Final';
  return raw
    .replace(/\bapproved\b/gi, 'Final')
    .replace(/\bapprove\b/gi, 'Final')
    .replace(/\bapproval\b/gi, 'Final');
}

/** Cancelled / draft / void — never count in Total Sale or Balance. */
function hopTxnIsCancelledOrDraft(row) {
  const s = hopTxnStatusOf(row);
  return /cancel|void|draft|rejected|deleted/.test(s);
}

/**
 * Final money docs for Total Sale / Balance.
 * Estimates (incl. Vyapar Final/Approved) are shown in the list but do not count as sale.
 * Journals count for Balance (write-off) but not Total Sale.
 */
function hopTxnIsFinalForSaleMath(row) {
  if (hopTxnIsCancelledOrDraft(row)) return false;
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_ESTIMATE.has(ty)) return false;
  if (HOP_TXN_JOURNAL.has(ty)) return true;
  const s = hopTxnStatusOf(row);
  if (!s) return true;
  if (s === 'approved' || s === 'approve' || s === 'final') return false;
  return true;
}

/**
 * Total Sale contribution:
 *   + Sale Invoice amount
 *   − Sale Return / Credit Note amount
 * Estimate / Quotation / Proforma / Orders → 0
 */
function hopPartyTxnSaleContribution(row) {
  if (!hopTxnIsFinalForSaleMath(row)) return 0;
  const ty = hopTxnTypeOf(row);
  const amt = parseFloat(row?.total_amount || 0) || 0;
  if (HOP_TXN_SALE_INVOICE.has(ty)) return amt;
  if (HOP_TXN_SALE_RETURN.has(ty)) return -amt;
  return 0;
}

/**
 * Party closing balance (receivable).
 * Sale Invoice due − returns − unused Payment-In.
 *
 * Journals: do NOT subtract again here. In Vyapar, a journal settlement marks the
 * related Sale as Paid (balance 0). Counting journal −total on top of that creates
 * a bogus negative balance (e.g. Anjali −2895).
 * We never invent Payment-In / write-off rows — only trust imported invoice balances.
 */
function hopPartyTxnBalanceContribution(row) {
  if (!hopTxnIsFinalForSaleMath(row)) return 0;
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_ESTIMATE.has(ty)) return 0;
  if (HOP_TXN_JOURNAL.has(ty)) return 0;
  if (HOP_TXN_OTHER_DOCS.has(ty)) return 0;
  const bal = parseFloat(row?.balance_amount || 0) || 0;
  if (HOP_TXN_SALE_INVOICE.has(ty)) return bal;
  if (HOP_TXN_SALE_RETURN.has(ty) || HOP_TXN_PAYMENT_IN.has(ty)) return -bal;
  if (HOP_TXN_PURCHASE_BILL.has(ty) || HOP_TXN_EXPENSE.has(ty)) return bal;
  if (HOP_TXN_PAYMENT_OUT.has(ty)) return -bal;
  return 0;
}

function hopComputePartyTotalSale(txns) {
  return (txns || []).reduce((s, t) => s + hopPartyTxnSaleContribution(t), 0);
}

/** Primary party ledger rows (sale docs + estimates + payments). Rest go under Other. */
function hopTxnIsPrimaryPartyDoc(row) {
  const ty = hopTxnTypeOf(row);
  if (HOP_TXN_OTHER_DOCS.has(ty)) return false;
  if (hopTxnIsCancelledOrDraft(row)) return false;
  if (HOP_TXN_ESTIMATE.has(ty)) return true; // show Final estimates like Vyapar
  if (!hopTxnIsFinalForSaleMath(row)) return false;
  return HOP_TXN_SALE_INVOICE.has(ty) || HOP_TXN_SALE_RETURN.has(ty) || HOP_TXN_PAYMENT_IN.has(ty);
}

function hopPartyTxnDisplayLabel(row) {
  const ty = hopTxnTypeOf(row);
  const raw = String(row?.txn_label || '').trim();
  if (HOP_TXN_ESTIMATE.has(ty)) {
    if (/^sale$/i.test(raw)) return 'Estimate';
    return raw || 'Estimate';
  }
  if (HOP_TXN_JOURNAL.has(ty)) return raw || 'Journal Entry';
  if (HOP_TXN_SALE_ORDER.has(ty)) return raw || 'Sale Order';
  if (ty === 1) return raw || 'Sale Invoice';
  if (ty === 21) return raw && !/^sale return$/i.test(raw) ? raw : 'Sale Return / Credit Note';
  return raw || (ty ? `Txn ${ty}` : '—');
}

function hopPartyTxnDisplayStatus(row) {
  const ty = hopTxnTypeOf(row);
  const raw = String(row.status_text || row.status || '').trim();
  // Estimates / Proforma / SO / Challan: not payment docs — no Unpaid / Paid badge.
  if (hopTxnIsNonReceivableType(ty)) return '';
  // Journals are posted adjustments (NPA wipe-off etc.), not open bills.
  if (HOP_TXN_JOURNAL.has(ty)) {
    if (/cancel/i.test(raw)) return 'Cancelled';
    return 'Posted';
  }
  const amt = parseFloat(row.total_amount || 0) || 0;
  const bal = parseFloat(row.balance_amount || 0) || 0;
  if (bal <= 0.009 && amt > 0.009) return 'Paid';
  // Do not invent Partial from total−balance alone (line+tax often inflates total).
  // Only show Partial when backend says so AND balance is clearly below a trusted total.
  if (/partial/i.test(raw) && amt > bal + 0.05 && bal > 0.05) {
    const paid = amt - bal;
    // Tiny residual vs bill → treat as Open (rounding / tax noise).
    if (paid <= Math.max(1, amt * 0.001)) return 'Open';
    return 'Partial';
  }
  if (/^paid$/i.test(raw) && bal > 0.05) return 'Open';
  const shown = raw && !/^partial$/i.test(raw) ? hopScrubVyaparStatusLabel(raw) : 'Open';
  return shown || 'Open';
}

function hopPartyTxnRowHtml(row) {
  const amt = parseFloat(row.total_amount || 0);
  const ty = hopTxnTypeOf(row);
  const isNonReceivable = hopTxnIsNonReceivableType(ty);
  const isJournal = HOP_TXN_JOURNAL.has(ty);
  // For display total on unpaid sale docs, don't show inflated amount above due balance.
  const balRaw = parseFloat(row.balance_amount || 0) || 0;
  const displayAmt = (!isNonReceivable && !isJournal && balRaw > 0.05 && amt > balRaw + 0.05 && hopPartyTxnDisplayStatus(row) === 'Open')
    ? balRaw
    : amt;
  const bal = (isNonReceivable || isJournal) ? 0 : balRaw;
  const status = hopPartyTxnDisplayStatus(row);
  const s = String(status).toLowerCase();
  const statusClass = !status
    ? 'is-na'
    : (s.includes('paid') || s.includes('used') || s.includes('final') || s.includes('posted')
      ? 'is-paid'
      : (s.includes('cancel') ? 'is-unpaid' : (s.includes('partial') ? 'is-partial' : 'is-partial')));
  const label = hopPartyTxnDisplayLabel(row);
  const docNo = hopFormatDocNo(row.txn_number, row.txn_date, row.txn_type);
  const tip = isNonReceivable
    ? 'Not a receivable — payment is against Sale Invoice'
    : (isJournal ? 'Imported Journal (shown for history). Settlement is on the Sale (Paid) — not counted again in Balance' : '');
  const statusTip = isNonReceivable ? 'No payment status on this document' : '';
  const balCell = (isNonReceivable || isJournal)
    ? `<td class="pty-txn-amt pty-txn-bal-na" title="${foEscapeAttr(isJournal ? 'Journal history only — Balance comes from Sale Paid/Open' : 'No receivable balance on this document')}">—</td>`
    : `<td class="pty-txn-amt${bal > 0 ? ' is-due' : ''}">₹ ${bal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>`;
  const id = Number(row.id || 0);
  const statusCell = status
    ? `<span class="pty-status ${statusClass}" title="${foEscapeAttr(statusTip)}">${foEscapeText(status)}</span>`
    : `<span class="pty-status is-na" title="${foEscapeAttr(statusTip)}">—</span>`;
  return `<tr class="pty-txn-row" role="button" tabindex="0"
    onclick="hopOpenPartyTxnDetail(${id})"
    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();hopOpenPartyTxnDetail(${id});}"
    title="Click to view transaction">
    <td title="${foEscapeAttr(tip || label)}">${foEscapeText(label)}</td>
    <td class="pty-txn-no" title="${foEscapeAttr(docNo || '')}">${foEscapeText(docNo || '—')}</td>
    <td>${foEscapeText((row.txn_date || '').slice(0, 10))}</td>
    <td class="pty-txn-amt">₹ ${displayAmt.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
    ${balCell}
    <td>${statusCell}</td>
    <td class="pty-txn-view"><span class="pty-txn-view-ico" aria-hidden="true">›</span></td>
  </tr>`;
}

function hopPartyTxnTableHtml(rows) {
  if (!rows.length) return '';
  return `<table class="pty-txn-table">
    <thead><tr>
      <th>Type</th><th>Number</th><th>Date</th><th>Total</th><th>Balance</th><th>Status</th><th></th>
    </tr></thead>
    <tbody>${rows.map(hopPartyTxnRowHtml).join('')}</tbody>
  </table>`;
}

function hopTxnModuleViewForType(ty) {
  const t = Number(ty || 0);
  if (HOP_TXN_SALE_INVOICE.has(t)) return 'sale_invoices';
  if (HOP_TXN_ESTIMATE.has(t)) return 'sale_estimates';
  if (HOP_TXN_PROFORMA.has(t)) return 'sale_proforma';
  if (HOP_TXN_PAYMENT_IN.has(t)) return 'sale_payment_in';
  if (HOP_TXN_SALE_ORDER.has(t)) return 'sale_orders';
  if (HOP_TXN_CHALLAN.has(t)) return 'sale_challan';
  if (HOP_TXN_SALE_RETURN.has(t)) return 'sale_returns';
  if (HOP_TXN_PURCHASE_BILL.has(t)) return 'purchase_bills';
  if (HOP_TXN_PAYMENT_OUT.has(t)) return 'purchase_payment_out';
  if (HOP_TXN_EXPENSE.has(t)) return 'purchase_expenses';
  if (HOP_TXN_PURCHASE_RETURN.has(t)) return 'purchase_returns';
  if (HOP_TXN_JOURNAL.has(t)) return 'journal_entries';
  return null;
}

function hopClosePartyTxnDetail() {
  const el = document.getElementById('hop-party-txn-overlay');
  if (!el) return;
  el.classList.remove('is-open');
  setTimeout(() => el.remove(), 220);
}

function hopOpenPartyTxnInModule(txnId) {
  const row = (hopState._partyTxns || []).find((t) => Number(t.id) === Number(txnId));
  if (!row) return;
  const view = hopTxnModuleViewForType(row.txn_type);
  if (!view) {
    if (typeof nexoraToast === 'function') nexoraToast('No list module for this document type yet.', 'warn');
    return;
  }
  hopClosePartyTxnDetail();
  const q = String(row.txn_number || '').trim();
  hopState.invoiceUi = {
    ...(hopState.invoiceUi || {}),
    period: 'all',
    from: '',
    to: '',
    status: 'all',
    q,
    party: String(row.party_name || '').trim().toLowerCase(),
  };
  openHopView(view);
}

function hopPreviewMoney(n) {
  const v = Number(n || 0);
  return `₹ ${v.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function hopPreviewDate(ymd) {
  const s = String(ymd || '').slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return s || '—';
  return `${m[3]}-${m[2]}-${m[1]}`;
}

function hopPrintPartyTxnPreview() {
  const sheet = document.getElementById('hop-doc-preview-sheet');
  if (!sheet) return;
  const meta = hopState.docPreviewMeta || {};
  const title = meta.docNumber
    ? `${meta.docTitle || 'Document'} — ${meta.docNumber}`
    : (meta.docTitle || 'Document Preview');
  const styles = (window.hopDocPrintStylesheet || hopDocPrintStylesheetFallback)();
  const w = window.open('', '_blank', 'noopener,noreferrer,width=920,height=1100');
  if (!w) {
    window.print();
    return;
  }
  w.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${foEscapeText(title)}</title>
    <style>${styles}</style></head><body>${sheet.outerHTML}</body></html>`);
  w.document.close();
  w.focus();
  setTimeout(() => { w.print(); }, 350);
}

function hopDocPrintStylesheetFallback() {
  return `
    body{font-family:Arial,Helvetica,sans-serif;color:#111;margin:16px;background:#fff;}
    table{width:100%;border-collapse:collapse;}
    th,td{border:1px solid #94a3b8;padding:5px 6px;font-size:11px;}
    th{background:#1d4ed8;color:#fff;text-align:left;}
    .num{text-align:right;font-variant-numeric:tabular-nums;}
    .cen{text-align:center;}
    @media print{body{margin:0;padding:0;}}`;
}

function hopDocSafeFilename(meta) {
  return String(meta?.docNumber || meta?.docTitle || 'document')
    .replace(/[/\\:*?"<>|]+/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 96) || 'quotation';
}

async function hopDownloadPdfBlob(path, filename) {
  if (typeof fetchWithAuth !== 'function') {
    throw new Error('Sign in required to download PDF');
  }
  const response = await fetchWithAuth(path);
  if (!response.ok) {
    let msg = 'PDF download failed';
    try {
      const data = await response.json();
      msg = getApiErrorMessage(data, msg);
    } catch (_) {
      msg = `PDF download failed (${response.status})`;
    }
    throw new Error(msg);
  }
  const blob = await response.blob();
  if (!blob || blob.size < 32) {
    throw new Error('PDF file is empty');
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.pdf') ? filename : `${filename}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function hopDownloadDocPreview() {
  const ids = hopState.docPreviewIds || {};
  const pid = Number(ids.partyTxnId || 0);
  const sid = Number(ids.sourceTxnId || 0);
  const meta = hopState.docPreviewMeta || {};
  const safeName = hopDocSafeFilename(meta);
  if (!pid && !sid) {
    if (typeof nexoraToast === 'function') nexoraToast('Preview not ready yet', 'warn');
    return;
  }
  const path = pid
    ? `/api/v1/hop/party-transactions/${pid}/download.pdf`
    : `/api/v1/hop/documents/download.pdf?source_txn_id=${sid}`;
  if (typeof nexoraToast === 'function') nexoraToast('Preparing PDF…', 'ok');
  try {
    await hopDownloadPdfBlob(path, `${safeName}.pdf`);
    if (typeof nexoraToast === 'function') nexoraToast('PDF downloaded', 'ok');
  } catch (e) {
    console.error('PDF download failed', e);
    if (typeof nexoraToast === 'function') {
      nexoraToast(e?.message || 'PDF download failed', 'err');
    }
  }
}

function hopUpdateDocPreviewHead(data) {
  const head = document.querySelector('#hop-party-txn-overlay .hop-doc-preview-head-title');
  if (!head || !data) return;
  const header = data.header || {};
  const title = header.doc_title || 'Document';
  const num = header.doc_number || '';
  head.textContent = num ? `${title} · ${num}` : title;
  hopState.docPreviewMeta = { docTitle: title, docNumber: num };
}

function hopUpdateDocPreviewFoot(data, partyTxnId) {
  const foot = document.querySelector('#hop-party-txn-overlay .hop-doc-preview-foot');
  if (!foot || !data) return;
  const pid = Number(partyTxnId || hopState.docPreviewIds?.partyTxnId || 0);
  const header = data.header || {};
  const sourceTxnId = Number(header.source_txn_id ?? hopState.docPreviewIds?.sourceTxnId ?? 0);
  const txnType = Number(header.txn_type || 0);
  const canEdit = pid > 0 && sourceTxnId < 0 && (txnType === 27 || txnType === 83);
  foot.querySelector('.hop-doc-preview-duplicate')?.remove();
  if (!canEdit) return;
  const dup = document.createElement('button');
  dup.type = 'button';
  dup.className = 'nx-btn hop-doc-preview-duplicate';
  dup.textContent = 'Duplicate';
  dup.onclick = () => {
    hopClosePartyTxnDetail();
    hopOpenManualDocDuplicate(pid);
  };
  const closeBtn = foot.querySelector('.hop-doc-preview-close');
  if (closeBtn) foot.insertBefore(dup, closeBtn);
  else foot.appendChild(dup);
}

async function hopOpenPartyTxnDetail(txnId) {
  return hopOpenSaleDocPreview(txnId, 0);
}

/** Open Vyapar-style Preview from Sale/Purchase lists or Parties ledger. */
async function hopOpenSaleDocPreview(partyTxnId, sourceTxnId) {
  const pid = Number(partyTxnId || 0) || 0;
  const sid = Number(sourceTxnId || 0) || 0;
  if (!pid && !sid) {
    if (typeof nexoraToast === 'function') {
      nexoraToast('Preview not available for this row yet.', 'warn');
    } else {
      alert('Preview not available for this row yet.');
    }
    return;
  }
  hopClosePartyTxnDetail();
  hopState.docPreviewIds = { partyTxnId: pid, sourceTxnId: sid };
  const overlay = document.createElement('div');
  overlay.id = 'hop-party-txn-overlay';
  overlay.className = 'hop-doc-preview-overlay';
  const openListBtn = pid
    ? `<button type="button" class="nx-btn" onclick="hopOpenPartyTxnInModule(${pid})">Open in list</button>`
    : '';
  overlay.innerHTML = `
    <div class="hop-doc-preview-backdrop" onclick="hopClosePartyTxnDetail()"></div>
    <div class="hop-doc-preview-modal" role="dialog" aria-modal="true" aria-label="Document preview">
      <div class="hop-doc-preview-head">
        <h2 class="hop-doc-preview-head-title">Document Preview</h2>
        <button type="button" class="hop-doc-preview-x" onclick="hopClosePartyTxnDetail()" aria-label="Close">&times;</button>
      </div>
      <div class="hop-doc-preview-scroll" id="hop-doc-preview-body">
        <div class="hop-doc-preview-loading">Loading document…</div>
      </div>
      <div class="hop-doc-preview-foot">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopDownloadDocPreview()">Download PDF</button>
        <button type="button" class="nx-btn" onclick="hopPrintPartyTxnPreview()">Print</button>
        ${openListBtn}
        <button type="button" class="nx-btn hop-doc-preview-close" onclick="hopClosePartyTxnDetail()">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  requestAnimationFrame(() => overlay.classList.add('is-open'));

  try {
    const url = pid
      ? `/api/v1/hop/party-transactions/${pid}/preview`
      : `/api/v1/hop/documents/preview?source_txn_id=${sid}`;
    const data = await hopApi(url);
    const body = document.getElementById('hop-doc-preview-body');
    if (body) body.innerHTML = (window.hopRenderDocPreviewHtml || hopRenderDocPreviewHtml)(data);
    hopUpdateDocPreviewHead(data);
    hopUpdateDocPreviewFoot(data, pid);
  } catch (e) {
    const body = document.getElementById('hop-doc-preview-body');
    if (body) {
      body.innerHTML = `<div class="hop-doc-preview-loading">${foEscapeText(e?.message || 'Failed to load preview')}</div>`;
    }
  }
}

function hopRenderDocPreviewHtml(data) {
  if (!data) return '<div class="hop-doc-preview-loading">No data</div>';
  const firm = data.firm || {};
  const party = data.party || {};
  const header = data.header || {};
  const totals = data.totals || {};
  const lines = data.lines || [];
  const title = header.doc_title || 'Document';
  const forLabel = /purchase|payment\s*out|expense/i.test(title) ? `${title} From` : `${title} For`;

  const firmBlock = `
    <div class="hop-doc-firm">
      <div class="hop-doc-firm-text">
        <div class="hop-doc-firm-name">${foEscapeText(firm.name || 'House of Prizm')}</div>
        ${firm.address ? `<div class="hop-doc-muted">${foEscapeText(firm.address)}</div>` : ''}
        ${firm.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(firm.phone)}</div>` : ''}
        ${firm.email ? `<div class="hop-doc-muted">Email: ${foEscapeText(firm.email)}</div>` : ''}
        ${firm.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(firm.gstin)}</div>` : ''}
        ${firm.state ? `<div class="hop-doc-muted">State: ${foEscapeText(firm.state)}</div>` : ''}
      </div>
      <div class="hop-doc-logo" aria-hidden="true">${foEscapeText((firm.name || 'HOP').slice(0, 1).toUpperCase())}</div>
    </div>`;

  const metaBlock = `
    <div class="hop-doc-meta">
      <div>
        <div class="hop-doc-meta-label">${foEscapeText(forLabel)}</div>
        <div class="hop-doc-party-name">${foEscapeText(party.billing_name || party.name || '—')}</div>
        ${party.address ? `<div class="hop-doc-muted">${foEscapeText(party.address)}</div>` : ''}
        ${party.contact_person ? `<div class="hop-doc-muted">Contact: ${foEscapeText(party.contact_person)}</div>` : ''}
        ${party.phone ? `<div class="hop-doc-muted">Phone: ${foEscapeText(party.phone)}</div>` : ''}
        ${party.email ? `<div class="hop-doc-muted">Email: ${foEscapeText(party.email)}</div>` : ''}
        ${party.gstin ? `<div class="hop-doc-muted">GSTIN: ${foEscapeText(party.gstin)}</div>` : ''}
        ${party.state ? `<div class="hop-doc-muted">State: ${foEscapeText(party.state)}</div>` : ''}
      </div>
      <div class="hop-doc-meta-right">
        <div><span class="hop-doc-meta-label">${foEscapeText(title)} No.</span> ${foEscapeText(header.doc_number || '—')}</div>
        <div><span class="hop-doc-meta-label">Date</span> ${foEscapeText(hopPreviewDate(header.doc_date))}</div>
        ${header.status ? `<div><span class="hop-doc-meta-label">Status</span> ${foEscapeText(header.status)}</div>` : ''}
      </div>
    </div>`;

  let linesHtml = '';
  if (lines.length) {
    const rows = lines.map((ln, i) => {
      const taxPct = Number(ln.tax_pct || 0);
      const taxAmt = Number(ln.tax_amount || 0);
      const qty = Number(ln.qty || 0);
      const disc = Number(ln.discount_amount || 0);
      const lineTotal = Number(ln.line_total || 0);
      let rate = Number(ln.rate || 0);
      // Fallback when import missed Vyapar priceperunit.
      if (!(rate > 0) && qty > 0 && lineTotal > 0) {
        rate = (lineTotal - taxAmt + disc) / qty;
      }
      const taxCell = taxPct > 0
        ? `${hopPreviewMoney(taxAmt)} (${taxPct}%)`
        : hopPreviewMoney(taxAmt);
      return `<tr>
        <td>${i + 1}</td>
        <td>
          <div class="hop-doc-item-name">${foEscapeText(ln.item_name || 'Item')}</div>
          ${ln.description ? `<div class="hop-doc-muted">${foEscapeText(ln.description)}</div>` : ''}
        </td>
        <td>${foEscapeText(ln.hsn || '')}</td>
        <td class="num">${qty.toLocaleString('en-IN')}</td>
        <td>${foEscapeText(ln.unit || 'Pcs')}</td>
        <td class="num">${hopPreviewMoney(rate)}</td>
        <td class="num">${taxCell}</td>
        <td class="num">${hopPreviewMoney(lineTotal)}</td>
      </tr>`;
    }).join('');
    linesHtml = `
      <table class="hop-doc-table">
        <thead>
          <tr>
            <th>#</th><th>Item Name</th><th>HSN/SAC</th><th>Quantity</th>
            <th>Unit</th><th>Price/Unit</th><th>GST</th><th>Amount</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="num"><strong>Total</strong></td>
            <td class="num"><strong>${Number(totals.qty || 0).toLocaleString('en-IN')}</strong></td>
            <td></td><td></td>
            <td class="num"><strong>${hopPreviewMoney(totals.tax_total)}</strong></td>
            <td class="num"><strong>${hopPreviewMoney(totals.grand_total)}</strong></td>
          </tr>
        </tfoot>
      </table>`;
  } else {
    linesHtml = `
      <div class="hop-doc-missing">
        <strong>No item lines in this preview yet.</strong>
        <p>${foEscapeText(data.lines_missing_hint || 'Re-import Vyapar backup to load item details.')}</p>
        <p class="hop-doc-muted">Header total: <strong>${hopPreviewMoney(totals.grand_total || header.total_amount)}</strong>
        · Balance: <strong>${hopPreviewMoney(header.balance_amount)}</strong></p>
      </div>`;
  }

  const taxLabel = totals.tax_pct > 0 ? `Tax @ ${totals.tax_pct}%` : 'Tax';
  const notes = header.notes || '';
  const bankBits = [firm.bank_name, firm.bank_account, firm.bank_ifsc, firm.bank_holder].filter(Boolean);
  const bankHtml = bankBits.length
    ? `<div class="hop-doc-bank">
        <div class="hop-doc-meta-label">Bank Details</div>
        ${firm.bank_name ? `<div>Bank Name: ${foEscapeText(firm.bank_name)}</div>` : ''}
        ${firm.bank_account ? `<div>Account No: ${foEscapeText(firm.bank_account)}</div>` : ''}
        ${firm.bank_ifsc ? `<div>IFSC: ${foEscapeText(firm.bank_ifsc)}</div>` : ''}
        ${firm.bank_holder ? `<div>Account Holder: ${foEscapeText(firm.bank_holder)}</div>` : ''}
      </div>`
    : '';

  return `
    <div class="hop-doc-preview-sheet" id="hop-doc-preview-sheet">
      ${firmBlock}
      <div class="hop-doc-title">${foEscapeText(title)}</div>
      ${metaBlock}
      ${linesHtml}
      <div class="hop-doc-bottom">
        <div class="hop-doc-bottom-left">
          ${notes ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Description</div><div>${foEscapeText(notes)}</div></div>` : ''}
          <div class="hop-doc-section">
            <div class="hop-doc-meta-label">${foEscapeText(title)} Amount in Words</div>
            <div>${foEscapeText(totals.amount_in_words || '')}</div>
          </div>
          ${data.terms ? `<div class="hop-doc-section"><div class="hop-doc-meta-label">Terms and Conditions</div><div>${foEscapeText(data.terms)}</div></div>` : ''}
          ${bankHtml}
        </div>
        <div class="hop-doc-totals">
          <div class="hop-doc-tot-row"><span>Sub Total</span><strong>${hopPreviewMoney(totals.sub_total)}</strong></div>
          <div class="hop-doc-tot-row"><span>${foEscapeText(taxLabel)}</span><strong>${hopPreviewMoney(totals.tax_total)}</strong></div>
          <div class="hop-doc-tot-row hop-doc-tot-grand"><span>Total</span><strong>${hopPreviewMoney(totals.grand_total)}</strong></div>
          <div class="hop-doc-sign">
            <div class="hop-doc-muted">For ${foEscapeText(firm.name || 'House of Prizm')}</div>
            <div class="hop-doc-sign-space"></div>
            <div>Authorized Signatory</div>
          </div>
        </div>
      </div>
    </div>`;
}

async function renderHopPartiesModule(mount) {
  let customers = [], vendors = [], partyTxns = [];
  try {
    const settled = await Promise.allSettled([
      hopApi('/api/v1/hop/customers'),
      hopApi('/api/v1/hop/vendors'),
      hopApi('/api/v1/hop/party-transactions'),
    ]);
    const pick = (i, fallback = []) => {
      const r = settled[i];
      if (r.status === 'fulfilled' && Array.isArray(r.value)) return r.value;
      if (r.status === 'fulfilled' && r.value == null) return fallback;
      return fallback;
    };
    customers = pick(0);
    vendors = pick(1);
    partyTxns = pick(2);
    const fatal = settled.slice(0, 2).every((r) => r.status === 'rejected');
    if (fatal) {
      const err = settled[0].status === 'rejected' ? settled[0].reason : settled[1].reason;
      throw err || new Error('Could not load parties');
    }
    hopState.customers = customers;
    hopState.vendors = vendors;
  } catch (e) {
    const msg = String(e?.message || e || 'Request failed');
    const hint = /502|503|error page/i.test(msg)
      ? `${msg} — Render server restart/deploy ho raha ho sakta hai. 30–60 sec wait karke Ctrl+Shift+R try karo.`
      : msg;
    mount.innerHTML = hopModuleShell('CRM', 'Parties', '', '', `<p class="nx-oc-error">${foEscapeText(hint)}</p>`);
    return;
  }

  const parties = [
    ...customers.map(c => ({ ...c, _type: 'customer', _balance: 0, _total_sale: 0 })),
    ...vendors.map(v => ({ ...v, _type: 'vendor', _balance: 0, _total_sale: 0 })),
  ];

  // Party Balance / Total Sale — Quotation & Proforma never count as sale.
  // Total Sale = Sale Invoice − Sale Return / Credit Note
  // Balance = receivable from money docs only (excludes Estimate/Proforma/SO/Challan).
  const balByParty = {};
  const saleByParty = {};
  for (const t of partyTxns) {
    const key = `${t.party_type}:${t.party_id}`;
    balByParty[key] = (balByParty[key] || 0) + hopPartyTxnBalanceContribution(t);
    saleByParty[key] = (saleByParty[key] || 0) + hopPartyTxnSaleContribution(t);
  }
  for (const p of parties) {
    const key = `${p._type}:${p.id}`;
    p._balance = balByParty[key] || 0;
    p._total_sale = saleByParty[key] || 0;
  }

  parties.sort((a, b) => (a.company || '').localeCompare(b.company || ''));

  hopState._parties = parties;
  hopState._partyTxns = partyTxns;
  hopState._partyFilter = hopState._partyFilter || '';
  // Keep previous selection if still present; otherwise auto-select first party.
  const prev = hopState._partySelected;
  const stillThere = prev && parties.some((p) => p._type === prev._type && Number(p.id) === Number(prev.id));
  hopState._partySelected = stillThere ? parties.find((p) => p._type === prev._type && Number(p.id) === Number(prev.id)) : (parties[0] || null);

  // True full-page side workspace (no CRM eyebrow shell).
  mount.innerHTML = `
    <div class="hop-view hop-view--fullpage hop-view--parties">
      <div class="pty-page">
        <div class="pty-topbar">
          <div class="pty-topbar-left">
            ${hopBackButtonHtml('Back')}
            <h2 class="pty-topbar-title">Parties</h2>
            <span class="pty-topbar-sub">${parties.length} contacts</span>
          </div>
          <div class="pty-topbar-actions">
            <button type="button" class="nx-btn nx-btn-primary" onclick="hopOpenAddPartyChooser()">+ Add Party</button>
          </div>
        </div>
        <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
        <div class="pty-layout" style="display:grid;grid-template-columns:minmax(280px,340px) minmax(0,1fr);flex:1;min-height:0;width:100%;height:100%;">
          <div class="pty-sidebar">
            <div class="pty-search-row">
              <input id="pty-search" class="pty-search" type="search" placeholder="Search Party Name" value="${foEscapeText(hopState._partyFilter)}" oninput="hopFilterParties(this.value)" />
            </div>
            <div class="pty-list-header">
              <span class="pty-lh-name">Party Name</span>
              <span class="pty-lh-amt">Balance</span>
            </div>
            <div id="pty-list" class="pty-list">
              ${_hopRenderPartyList(parties, hopState._partyFilter)}
            </div>
          </div>
          <div id="pty-detail" class="pty-detail" style="min-width:0;overflow:auto;">
            ${hopState._partySelected ? _hopRenderPartyDetail(hopState._partySelected, partyTxns) : _hopPartyEmptyDetail()}
          </div>
        </div>
      </div>
    </div>`;
  hopSetMainFullpage(true);
}

function _hopRenderPartyList(parties, filter) {
  const q = (filter || '').toLowerCase().trim();
  const filtered = q ? parties.filter(p => (p.company || '').toLowerCase().includes(q) || (p.contact_person || '').toLowerCase().includes(q) || String(p.customer_type || '').toLowerCase().includes(q)) : parties;
  if (!filtered.length) return '<p class="pty-empty-list">No parties found.</p>';
  return filtered.map(p => {
    const sel = hopState._partySelected && Number(hopState._partySelected.id) === Number(p.id) && hopState._partySelected._type === p._type;
    const bal = p._balance ? `₹ ${Number(p._balance).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '0.00';
    const group = String(p.customer_type || (p._type === 'vendor' ? 'Supplier' : 'Buyer')).trim() || (p._type === 'vendor' ? 'Supplier' : 'Buyer');
    const name = String(p.company || '—').trim();
    const initial = (name.charAt(0) || 'P').toUpperCase();
    const badgeClass = p._type === 'vendor' ? 'pty-badge-v' : 'pty-badge-c';
    return `<button type="button" class="pty-item${sel ? ' is-active' : ''}"
      onclick="hopSelectParty('${p._type}', ${p.id})"
      ondblclick="hopEditContact('${p._type === 'vendor' ? 'vendors' : 'customers'}', ${p.id})"
      title="${foEscapeAttr(group)} · Double-click to edit">
      <span class="pty-badge ${badgeClass}" title="${foEscapeAttr(group)}">${foEscapeText(initial)}</span>
      <span class="pty-item-main">
        <span class="pty-item-name">${foEscapeText(name)}</span>
      </span>
      <span class="pty-item-bal${p._balance > 0 ? ' is-due' : ''}">${bal}</span>
    </button>`;
  }).join('');
}

function _hopPartyEmptyDetail() {
  return `<div class="pty-no-selection">
    <svg viewBox="0 0 24 24" width="40" height="40" fill="currentColor" style="opacity:.1"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4Zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4Z"/></svg>
    <p>Select a party to view details</p>
  </div>`;
}

function _hopRenderPartyDetail(party, partyTxns) {
  const isVendor = party._type === 'vendor';
  const partyGroup = String(
    party.customer_type || party.party_group || (isVendor ? 'Supplier' : 'Buyer')
  ).trim() || (isVendor ? 'Supplier' : 'Buyer');
  const address = party.address || '';
  const shipping = party.shipping_address || '';
  const mobile = party.mobile || '';
  const email = party.email || '';
  const gst = party.gst_no || '';
  const city = party.city || '';
  const state = party.state || '';
  const contactPerson = party.contact_person || '';
  const place = [city, state].filter(Boolean).join(', ');

  const partyRows = (partyTxns || [])
    .filter((t) => t.party_type === party._type && Number(t.party_id) === Number(party.id))
    .sort((a, b) => String(b.txn_date || '').localeCompare(String(a.txn_date || '')));

  const callHref = hopCallHref(mobile);
  const waHref = hopWhatsAppHref(mobile);
  const typeKey = isVendor ? 'vendors' : 'customers';

  return `
    <div class="pty-detail-header">
      <div class="pty-detail-info">
        <div class="pty-detail-title-row">
          <div class="pty-detail-heading">
            <h3 class="pty-detail-name">${foEscapeText(party.company || '—')}</h3>
            <span class="pty-detail-type" title="Party Group">${foEscapeText(partyGroup)}</span>
          </div>
          <div class="pty-detail-actions">
            ${callHref ? `<a class="pty-action-btn" href="${callHref}" title="Call">${hopContactIcon('call')}</a>` : ''}
            ${waHref ? `<a class="pty-action-btn pty-action-wa" href="${waHref}" target="_blank" title="WhatsApp">${hopContactIcon('whatsapp')}</a>` : ''}
            <button type="button" class="nx-btn nx-btn-primary pty-detail-edit" onclick="hopEditContact('${typeKey}', ${party.id})">${hopContactIcon('edit')} Edit</button>
            <button type="button" class="nx-btn hop-contact-icon-del pty-detail-del" onclick="hopDeleteContact('${typeKey}', ${party.id}, '${foEscapeAttr(party.company || '')}')">${hopContactIcon('delete')} Delete</button>
          </div>
        </div>

        <div class="pty-detail-meta">
          ${gst ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('gst')}</span><span class="pty-meta-text"><em>GSTIN</em> ${foEscapeText(gst)}</span></div>` : ''}
          ${contactPerson ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('edit')}</span><span class="pty-meta-text"><em>Contact</em> ${foEscapeText(contactPerson)}</span></div>` : ''}
          ${address ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('pin')}</span><span class="pty-meta-text"><em>Billing</em> ${foEscapeText(address)}</span></div>` : ''}
          ${shipping && shipping !== address ? `<div class="pty-meta-row"><span class="pty-meta-ico">${hopContactIcon('pin')}</span><span class="pty-meta-text"><em>Shipping</em> ${foEscapeText(shipping)}</span></div>` : ''}
        </div>

        <div class="pty-detail-contact">
          ${mobile ? `<${callHref ? `a href="${callHref}"` : 'span'} class="pty-chip pty-chip-phone">${hopContactIcon('call')}<span>${foEscapeText(mobile)}</span></${callHref ? 'a' : 'span'}>` : ''}
          ${email ? `<a class="pty-chip pty-chip-mail" href="mailto:${foEscapeAttr(email)}">${hopContactIcon('email')}<span>${foEscapeText(email)}</span></a>` : ''}
          ${place ? `<span class="pty-chip pty-chip-place">${hopContactIcon('pin')}<span>${foEscapeText(place)}</span></span>` : ''}
        </div>
      </div>
    </div>

    <div class="pty-txn-section">
      <div class="pty-txn-header">
        <strong>Transactions</strong>
        <span class="nx-text-dim">${partyRows.length} document${partyRows.length === 1 ? '' : 's'}</span>
        <span class="pty-total-sale" title="Sale Invoice / Sale Bill − Credit Note / Sale Return. Excludes Quotation, Proforma, Orders, Cancelled &amp; drafts.">
          Total Sale <em>${hopMoney(hopComputePartyTotalSale(partyRows))}</em>
        </span>
      </div>
      ${partyRows.length
        ? hopPartyTxnTableHtml(partyRows)
        : '<p class="pty-no-txn">No documents for this party yet.</p>'}
    </div>`;
}

function hopSelectParty(type, id) {
  const parties = hopState._parties || [];
  const party = parties.find(p => p._type === type && Number(p.id) === Number(id));
  if (!party) return;
  hopState._partySelected = party;
  const list = document.getElementById('pty-list');
  if (list) list.innerHTML = _hopRenderPartyList(hopState._parties || [], hopState._partyFilter || '');
  // Render detail pane (desktop split)
  const detail = document.getElementById('pty-detail');
  if (detail) detail.innerHTML = _hopRenderPartyDetail(party, hopState._partyTxns || []);
  // Mobile / narrow: open full contact card overlay so Edit / Delete / details are usable.
  if (hopIsMobileView()) {
    hopOpenContactDetail(type === 'vendor' ? 'vendors' : 'customers', party.id);
  }
}

function hopFilterParties(q) {
  hopState._partyFilter = q || '';
  const list = document.getElementById('pty-list');
  if (list) list.innerHTML = _hopRenderPartyList(hopState._parties || [], q);
}

/* ---------- Customers ---------- */
async function renderHopCustomersModule(mount) {
  const q = hopState.search.customers || '';
  let rows = [];
  try { rows = await hopApi(`/api/v1/hop/customers?q=${encodeURIComponent(q)}`) || []; hopState.customers = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('CRM', 'Customers', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div class="hop-toolbar">
      <input id="hop-q" class="hop-search" type="search" value="${foEscapeText(q)}" placeholder="Search company, contact, city…" oninput="hopFilterModule('customers')" />
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopIsMobileView()
      ? hopRenderMobileContactCards(rows, 'customers')
      : `${hopRenderDesktopContactToolbar('customers', rows.length)}
         ${hopTable(
        ['', 'Company', 'Contact', 'Mobile', 'City', 'Type', 'Hotel', 'Architect', 'Consultant', 'Potential', 'Rating', 'Status', 'Assigned', ''],
        rows.map((r) => {
          const state = hopContactSelectState('customers');
          const checked = state.ids.includes(Number(r.id)) ? ' checked' : '';
          return `<tr class="hop-clickable-row" onclick="hopOpenContactDetail('customers', ${r.id})" style="cursor:pointer">
          <td onclick="event.stopPropagation()"><input type="checkbox" class="hop-desk-check" value="${r.id}"${checked} onchange="hopToggleContactSelected('customers', ${r.id}, this.checked)" /></td>
          <td>${hopCell(r.company)}</td><td>${hopCell(r.contact_person)}</td><td>${hopCell(r.mobile)}</td>
          <td>${hopCell(r.city)}</td><td>${hopCell(r.customer_type)}</td><td>${hopCell(r.hotel_brand)}</td>
          <td>${hopCell(r.architect)}</td><td>${hopCell(r.consultant)}</td><td>${hopCell(r.annual_potential)}</td>
          <td>${hopCell(r.potential_rating)}</td><td>${hopCell(r.status)}</td><td>${hopCell(r.assigned_to)}</td>
          <td><button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('customers', ${r.id}, '${foEscapeAttr(hopContactLabel(r))}')" title="Delete">${hopContactIcon('delete')}</button></td>
        </tr>`}).join(''),
      )}`}`;
  mount.innerHTML = hopModuleShell('CRM', 'Customers', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopOpenAddPartyChooser()">+ Add Party</button>`, body);
  if (hopIsMobileView()) requestAnimationFrame(() => hopBindMobileContactCards('customers'));
}

/* ---------- Visiting Card Reader ---------- */

async function renderHopThemeModule(mount) {
  const saved = hopGetTheme();
  const current = hopGetDisplayedTheme();
  const c = hopThemeLivePreview?.colors || hopGetCustomColors();
  const themeCard = ({ id, swatchClass, title, chip, dots, onclick, side, main, variant }) => {
    const showing = current === id;
    const isSaved = saved === id && !hopThemeLivePreview;
    const isPreview = hopThemeLivePreview && hopThemeLivePreview.theme === id;
    const dotsHtml = (dots || []).map((d) => `<span class="hop-theme-dot" style="background:${d}"></span>`).join('');
    const sideStyle = side ? ` style="background:${side}"` : (id === 'custom' ? ` style="background:${c.sidebar}"` : '');
    const mainStyle = main ? ` style="background:${main}"` : (id === 'custom' ? ` style="background:${c.bg}"` : '');
    const cardClass = variant === 'gallery'
      ? `hop-theme-card hop-theme-card--gallery${showing ? ' is-active' : ''}`
      : `hop-theme-card hop-theme-card--compact${showing ? ' is-active' : ''}`;
    const swatchClassFull = variant === 'gallery'
      ? `hop-theme-swatch hop-theme-swatch--gallery ${swatchClass || ''}`
      : `hop-theme-swatch hop-theme-swatch--compact ${swatchClass || ''}`;
    const badge = isPreview
      ? '<span class="hop-theme-badge hop-theme-badge--preview">Preview</span>'
      : (isSaved ? '<span class="hop-theme-badge">Active</span>' : (showing ? '<span class="hop-theme-badge hop-theme-badge--preview">Preview</span>' : ''));
    return `
      <button type="button" class="${cardClass}" onclick="${onclick || `hopRequestTheme('${id}')`}">
        ${badge}
        <div class="${swatchClassFull}" aria-hidden="true">
          <div class="hop-theme-swatch-side"${sideStyle}></div>
          <div class="hop-theme-swatch-main"${mainStyle}></div>
        </div>
        <div class="hop-theme-card-body">
          <div class="hop-theme-card-top">
            <h3>${title}</h3>
            ${chip || ''}
          </div>
          <div class="hop-theme-dots" aria-hidden="true">${dotsHtml}</div>
        </div>
      </button>`;
  };
  const coreDefs = [
    {
      id: 'emerald',
      swatchClass: 'hop-theme-swatch--emerald',
      title: 'Emerald Gold',
      chip: '<span class="hop-theme-chip hop-theme-chip--rec">Signature</span>',
      dots: ['#123C32', '#F8F4EA', '#C9A227'],
    },
    {
      id: 'bright',
      swatchClass: 'hop-theme-swatch--bright',
      title: 'Bright',
      chip: '<span class="hop-theme-chip">Workday</span>',
      dots: ['#0f2744', '#f4f7fb', '#0d9488'],
    },
  ];
  const luxuryDefs = Object.values(HOP_LUXURY_THEMES).map((t) => ({
    id: t.id,
    swatchClass: 'hop-theme-swatch--luxury',
    title: t.title,
    chip: t.chip ? `<span class="hop-theme-chip hop-theme-chip--rec">${t.chip}</span>` : '',
    dots: [t.colors.sidebar, t.colors.bg, t.colors.accent],
    side: t.colors.sidebar,
    main: t.colors.bg,
  }));
  const customDef = {
    id: 'custom',
    swatchClass: 'hop-theme-swatch--custom',
    title: 'Custom studio',
    chip: '<span class="hop-theme-chip">Studio</span>',
    dots: [c.sidebar, c.bg, c.accent],
    onclick: 'hopOpenCustomThemeStudio()',
  };

  const applyBar = `
      <div class="hop-theme-apply-bar" id="hop-theme-apply-bar">
        <p class="hop-theme-apply-hint" id="hop-theme-apply-hint">Pick a theme to preview — nothing is saved until you Apply.</p>
        <button type="button" class="hop-theme-apply-btn" id="hop-theme-apply-btn" onclick="hopCommitThemeApply()" disabled>Apply theme</button>
      </div>`;

  // North Head Settings — same compact shade-card grid as HoP
  if (mount && mount.id === 'bd-settings-theme-mount') {
    const coreCards = coreDefs.map((d) => themeCard(d)).join('');
    const luxuryCards = luxuryDefs.map((d) => themeCard(d)).join('');
    const customCard = themeCard(customDef);
    mount.innerHTML = `
      <div class="hop-theme-studio hop-theme-studio--compact hop-theme-studio--bd">
        <div class="hop-theme-studio-intro hop-theme-studio-intro--compact">
          <p class="hop-theme-studio-kicker">Appearance</p>
          <p class="hop-theme-studio-lead">
            Try any theme live. Saved only for this login when you press Apply — other users keep their own look.
          </p>
        </div>
        <div class="hop-theme-grid hop-theme-grid--compact">
          ${coreCards}
          ${luxuryCards}
          ${customCard}
        </div>
        ${applyBar}
      </div>`;
    hopUpdateThemeApplyBar();
    return;
  }

  const coreCards = coreDefs.map((d) => themeCard(d)).join('');
  const luxuryCards = luxuryDefs.map((d) => themeCard(d)).join('');
  const customCard = themeCard(customDef);
  const studioInner = `
      <div class="hop-theme-grid hop-theme-grid--compact">
        ${coreCards}
        ${luxuryCards}
        ${customCard}
      </div>
      ${applyBar}`;
  const body = `
    <div class="hop-theme-studio hop-theme-studio--compact">
      <div class="hop-theme-studio-intro hop-theme-studio-intro--compact">
        <p class="hop-theme-studio-kicker">Appearance</p>
        <p class="hop-theme-studio-lead">
          Try any theme live. Saved only for this login when you press Apply — other users keep their own look.
        </p>
      </div>
      ${studioInner}
    </div>`;
  mount.innerHTML = hopModuleShell('Settings', 'Theme', '', '', body);
  hopUpdateThemeApplyBar();
}

async function renderHopWipeDataModule(mount) {
  const body = `
    <div class="nx-card" style="max-width:560px;padding:22px 24px;">
      <p class="nx-text-dim" style="margin:0 0 14px;line-height:1.5">
        This permanently deletes all House of Prizm business data:
        parties, invoices, payments, products, quotations, party transactions, and related records.
      </p>
      <p class="nx-text-dim" style="margin:0 0 18px;line-height:1.5">
        Login users are kept. This cannot be undone.
      </p>
      <label class="hop-vcard-field" style="margin-bottom:14px">
        <span>Confirm by typing <strong>WIPE</strong></span>
        <input id="hop-wipe-confirm" type="text" autocomplete="off" placeholder="WIPE" />
      </label>
      <label class="hop-vcard-field" style="margin-bottom:18px">
        <span>Your login password <em style="font-style:normal;color:#64748b">(required)</em></span>
        <input id="hop-wipe-password" type="password" autocomplete="current-password" placeholder="Enter your password" />
      </label>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button type="button" class="nx-btn" style="border-color:rgba(248,113,113,.45);color:#f87171" onclick="hopRunWipeData()">Wipe all data</button>
        <button type="button" class="nx-btn" onclick="openHopView('dashboard')">Cancel</button>
      </div>
      <p id="hop-wipe-status" class="nx-text-dim" style="margin:14px 0 0;font-size:.78rem"></p>
    </div>`;
  mount.innerHTML = hopModuleShell('Settings', 'Wipe all data', '', '', body);
}

async function hopRunWipeData() {
  const confirmEl = document.getElementById('hop-wipe-confirm');
  const statusEl = document.getElementById('hop-wipe-status');
  const password = document.getElementById('hop-wipe-password')?.value || '';
  const typed = String(confirmEl?.value || '').trim();
  if (typed !== 'WIPE') {
    if (statusEl) statusEl.textContent = 'Type WIPE exactly to confirm.';
    confirmEl?.focus();
    return;
  }
  if (!String(password || '').trim()) {
    if (statusEl) statusEl.textContent = 'Enter your login password to continue.';
    document.getElementById('hop-wipe-password')?.focus();
    return;
  }
  if (!(await nexoraConfirm('Delete ALL business data now? This cannot be undone.', {
    title: 'Wipe all data',
    danger: true,
    okText: 'Wipe now',
  }))) return;
  if (statusEl) statusEl.textContent = 'Wiping…';
  try {
    const data = await hopApi('/api/v1/hop/settings/wipe-data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, confirm: 'WIPE' }),
    });
    // Clear client caches so UI does not show stale rows.
    hopState.customers = [];
    hopState.vendors = [];
    hopState.invoices = [];
    hopState.quotations = [];
    hopState.orders = [];
    hopState._parties = [];
    hopState._partyTxns = [];
    hopState._partySelected = null;
    hopState.vyaparImportPreview = null;
    hopState.vyaparBackupFile = null;
    if (statusEl) {
      statusEl.textContent = `Done. Cleared ${data?.tables_cleared || 0} tables (${data?.rows_deleted || 0} rows).`;
    }
    alert(`Data wiped.\nTables: ${data?.tables_cleared || 0}\nRows deleted: ${data?.rows_deleted || 0}`);
    openHopView('parties');
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message || 'Wipe failed';
    alert(e.message || 'Wipe failed');
  }
}

async function renderHopVyaparImportModule(mount) {
  const preview = hopState.vyaparImportPreview;
  const src = preview?.source || {};
  const det = preview?.detected || {};
  const hasPreview = !!preview;

  const sizeKB = src.sqlite_bytes ? (src.sqlite_bytes / 1024).toFixed(0) : '—';
  const salesCount = (det.txn_type_split || {})['1'] || 0;
  const purchaseCount = (det.txn_type_split || {})['2'] || 0;
  const paymentInCount = (det.txn_type_split || {})['3'] || 0;
  const paymentOutCount = (det.txn_type_split || {})['4'] || 0;

  const body = `
    <div class="vyp-page">
      <input id="hop-vyapar-file" class="hop-file-hidden" type="file" accept=".vyb,.vyp,application/octet-stream" />

      <div class="vyp-row">
        <div class="vyp-col vyp-col-upload">
          <div class="vyp-drop-zone" onclick="hopPickVyaparBackup()">
            <svg viewBox="0 0 48 48" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 30V16a10 10 0 0 1 20 0v14"/><path d="M10 28a6 6 0 0 0 0 12h28a6 6 0 0 0 0-12"/><path d="M24 20v14m-5-5 5 5 5-5"/></svg>
            <p class="vyp-drop-label">Drop <strong>.vyb</strong> / <strong>.vyp</strong> here or click to browse</p>
            <p id="hop-vyapar-status" class="vyp-drop-file">${hasPreview ? foEscapeText((hopState.vyaparBackupFile?.name) || src.filename || '') : 'No file selected'}</p>
          </div>
          <div class="vyp-btn-row">
            <button type="button" class="vyp-btn vyp-btn-scan" onclick="hopPreviewVyaparBackup()">Preview</button>
            <button type="button" class="vyp-btn vyp-btn-import${hasPreview ? '' : ' is-disabled'}" onclick="hopRunVyaparImport()" ${hasPreview ? '' : 'disabled'}>Import</button>
          </div>
        </div>

        <div class="vyp-col vyp-col-preview">
          ${hasPreview ? `
            <div class="vyp-firm-row">
              <strong>${foEscapeText(src.firm_name || '—')}</strong>
              <span>${foEscapeText(src.tables || 0)} tables · ${sizeKB} KB</span>
            </div>
            <div class="vyp-stats-row">
              <div class="vyp-s"><span>${foEscapeText(det.parties_total || 0)}</span><small>Parties</small></div>
              <div class="vyp-s"><span>${foEscapeText(det.items_total || 0)}</span><small>Items</small></div>
              <div class="vyp-s"><span>${foEscapeText(det.transactions_total || 0)}</span><small>Txns</small></div>
            </div>
            <div class="vyp-tags">
              <span class="vyp-t vyp-t-s">${salesCount} Sales</span>
              <span class="vyp-t vyp-t-p">${purchaseCount} Purchase</span>
              <span class="vyp-t vyp-t-y">${paymentInCount} Pay-In · ${paymentOutCount} Pay-Out</span>
            </div>
            <p class="vyp-hint">Duplicates auto-skipped. Existing blank fields get updated.</p>
          ` : `
            <div class="vyp-empty">
              <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" style="opacity:.15"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm4 18H6V4h7v5h5v11Z"/></svg>
              <p>Select a backup file and click <strong>Preview</strong></p>
            </div>
          `}
        </div>
      </div>
    </div>`;

  mount.innerHTML = hopModuleShell(
    'Migration',
    'Vyapar Import',
    'Convert & import backup into House of Prizm',
    `<button type="button" class="nx-btn" onclick="openHopView('customers')">← Back</button>`,
    body,
  );

  const input = document.getElementById('hop-vyapar-file');
  input?.addEventListener('change', () => {
    const file = input.files && input.files[0];
    if (!file) return;
    hopState.vyaparBackupFile = file;
    const status = document.getElementById('hop-vyapar-status');
    if (status) { status.textContent = file.name; status.classList.add('is-active'); }
    document.querySelector('.vyp-btn-import')?.removeAttribute('disabled');
    document.querySelector('.vyp-btn-import')?.classList.remove('is-disabled');
  });
}

function _vypShowLoader(msg) {
  _vypHideLoader();
  const el = document.createElement('div');
  el.id = 'vyp-loader-overlay';
  el.className = 'vyp-loader';
  el.innerHTML = `
    <div class="vyp-loader-card">
      <div class="vyp-spinner"></div>
      <p class="vyp-loader-msg">${foEscapeText(msg)}</p>
    </div>`;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add('is-open'));
}
function _vypHideLoader() {
  const el = document.getElementById('vyp-loader-overlay');
  if (el) { el.classList.remove('is-open'); setTimeout(() => el.remove(), 200); }
}
function _vypResultDialog(success, title, lines) {
  _vypHideLoader();
  const el = document.createElement('div');
  el.id = 'vyp-result-overlay';
  el.className = 'vyp-result';
  el.innerHTML = `
    <div class="vyp-result-backdrop" onclick="document.getElementById('vyp-result-overlay')?.remove()"></div>
    <div class="vyp-result-card">
      <div class="vyp-result-icon ${success ? 'is-ok' : 'is-fail'}">
        ${success
          ? '<svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor"><path d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2Z"/></svg>'
          : '<svg viewBox="0 0 24 24" width="36" height="36" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm1 15h-2v-2h2v2Zm0-4h-2V7h2v6Z"/></svg>'
        }
      </div>
      <h4 class="vyp-result-title">${foEscapeText(title)}</h4>
      <div class="vyp-result-body">${lines.map(l => `<p>${l}</p>`).join('')}</div>
      <button type="button" class="vyp-btn vyp-btn-import" onclick="document.getElementById('vyp-result-overlay')?.remove()" style="margin-top:12px;width:100%">OK</button>
    </div>`;
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add('is-open'));
}

async function hopPreviewVyaparBackup() {
  const file = hopState.vyaparBackupFile;
  if (!file) {
    _vypResultDialog(false, 'No file selected', ['Please select a .vyb or .vyp backup file first.']);
    return;
  }
  const fd = new FormData();
  fd.append('backup_file', file, file.name || 'backup.vyb');
  _vypShowLoader('Scanning backup…');
  try {
    const data = await hopApi('/api/v1/hop/vyapar-import/preview', { method: 'POST', body: fd });
    hopState.vyaparImportPreview = data;
    _vypHideLoader();
    openHopView('vyapar_import');
  } catch (e) {
    const msg = String(e.message || '');
    const hint = /502|503|error page|Failed to fetch|network/i.test(msg)
      ? 'Render server timed out on this large backup. Wait 30s, hard-refresh (Ctrl+Shift+R), then try Preview again. If it still fails, import from a local Flask run.'
      : (msg || 'Could not read the backup file.');
    _vypResultDialog(false, 'Preview Failed', [hint]);
  }
}

async function hopRunVyaparImport() {
  const file = hopState.vyaparBackupFile;
  if (!file) {
    _vypResultDialog(false, 'No file selected', ['Please select a .vyb or .vyp backup file first.']);
    return;
  }
  if (!(await nexoraConfirm(
    'Import / refresh from this Vyapar backup?\n\nSafe re-import: existing parties, invoices & transactions update in place — no duplicates. Only new Vyapar rows will be added.',
    {
    title: 'Confirm Import',
    danger: true,
    okText: 'Import',
  }))) return;
  const fd = new FormData();
  fd.append('backup_file', file, file.name || 'backup.vyb');
  _vypShowLoader('Importing data… this can take a few minutes on large backups');
  try {
    const data = await hopApi('/api/v1/hop/vyapar-import/apply', { method: 'POST', body: fd });
    const lines = [
      `<span class="vyp-r-label">Customers</span> <strong>${data.customers_created}</strong> created, ${data.customers_skipped} skipped`,
      `<span class="vyp-r-label">Vendors</span> <strong>${data.vendors_created}</strong> created, ${data.vendors_skipped} skipped`,
      `<span class="vyp-r-label">Products</span> <strong>${data.products_created}</strong> created, ${data.products_skipped} skipped`,
      `<span class="vyp-r-label">Invoices</span> <strong>${data.invoices_created || 0}</strong> created, ${data.invoices_updated || 0} updated`,
      `<span class="vyp-r-label">Payments</span> <strong>${data.payments_created || 0}</strong> created, ${data.payments_skipped || 0} skipped`,
      `<span class="vyp-r-label">All Txns</span> <strong>${data.party_txns_created || 0}</strong> imported, ${data.party_txns_skipped || 0} refreshed`,
      `<span class="vyp-r-label">Fuzzy party match</span> <strong>${data.party_fuzzy_matched || 0}</strong> linked (no duplicate)`,
    ];
    if (data.errors && data.errors.length) {
      lines.push(`<span class="vyp-r-warn">${data.errors.length} error(s) during import</span>`);
    }
    _vypResultDialog(true, 'Import Successful', lines);
    hopState.customers = [];
    hopState.vendors = [];
  } catch (e) {
    const msg = String(e.message || '');
    const hint = /502|503|error page|Failed to fetch|network/i.test(msg)
      ? 'Server timed out while importing (large backup). Wait a minute and retry Import once — do not double-click. For very large files, run import on local Flask.'
      : (msg || 'Something went wrong during import.');
    _vypResultDialog(false, 'Import Failed', [hint]);
  }
}

function hopPickVyaparBackup() {
  const input = document.getElementById('hop-vyapar-file');
  if (input) input.click();
}

/* ---------- Visiting Card Reader ---------- */
async function renderHopVisitingCardModule(mount) {
  const body = `
    <div class="hop-vcard-page">
      <div class="hop-vcard-layout">
        <section class="hop-vcard-panel hop-vcard-capture">
          <div class="hop-vcard-panel-head">
            <h3>Capture</h3>
            <span class="hop-vcard-hint">Camera or file · auto-reads on upload</span>
          </div>
          <div class="hop-vcard-source">
            <button type="button" class="hop-vcard-src-btn hop-vcard-src-btn--primary" onclick="hopPickPhoto('hop-vcard-cam')">
              <span class="hop-vcard-src-ico" aria-hidden="true"></span>
              Camera
            </button>
            <button type="button" class="hop-vcard-src-btn" onclick="hopPickPhoto('hop-vcard-gal')">
              <span class="hop-vcard-src-ico hop-vcard-src-ico--file" aria-hidden="true"></span>
              Gallery
            </button>
          </div>
          <input id="hop-vcard-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
          <input id="hop-vcard-gal" class="hop-file-hidden" type="file" accept="image/*" />
          <div id="hop-vcard-preview" class="hop-vcard-stage is-empty">
            <div class="hop-vcard-empty">
              <strong>Drop visiting card here</strong>
              <span>Clear photo · good light · flat card works best</span>
            </div>
          </div>
          <div class="hop-vcard-capture-foot">
            <button type="button" class="nx-btn nx-btn-primary" id="hop-vcard-scan-btn" onclick="hopScanVisitingCard()">Read card</button>
            <p id="hop-vcard-status" class="hop-vcard-status">Waiting for photo</p>
          </div>
        </section>
        <section id="hop-vcard-form" class="hop-vcard-panel hop-vcard-review is-idle">
          <div class="hop-vcard-idle">
            <strong>Review</strong>
            <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
          </div>
        </section>
      </div>
    </div>
  `;
  mount.innerHTML = hopModuleShell(
    'CRM',
    'Visiting Card',
    '',
    `<button type="button" class="nx-btn" onclick="openHopView('customers')">← Customers</button>`,
    body,
  );

  const setPreview = (file) => {
    const box = document.getElementById('hop-vcard-preview');
    if (!file || !box) return;
    hopState.visitingCardFile = file;
    box.classList.remove('is-empty');
    box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
    const status = document.getElementById('hop-vcard-status');
    if (status) {
      status.textContent = 'Photo ready — reading…';
      status.classList.add('is-busy');
      status.classList.remove('is-error', 'is-ok');
    }
    const form = document.getElementById('hop-vcard-form');
    if (form) {
      form.classList.add('is-idle');
      form.classList.remove('is-ready');
      form.innerHTML = `
        <div class="hop-vcard-idle">
          <strong>Review</strong>
          <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
        </div>`;
    }
    hopScheduleVisitingCardScan();
  };

  const bindPreview = (inputId) => {
    const input = document.getElementById(inputId);
    input?.addEventListener('change', () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const otherId = inputId === 'hop-vcard-cam' ? 'hop-vcard-gal' : 'hop-vcard-cam';
      const other = document.getElementById(otherId);
      if (other) other.value = '';
      setPreview(file);
    });
  };
  bindPreview('hop-vcard-cam');
  bindPreview('hop-vcard-gal');
}

function hopScheduleVisitingCardScan() {
  window.clearTimeout(hopState._vcardScanTimer);
  hopState._vcardScanTimer = window.setTimeout(() => hopScanVisitingCard(), 450);
}

function hopIsLikelyMobileDevice() {
  const ua = navigator.userAgent || '';
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
  if (navigator.maxTouchPoints > 2 && Math.min(window.screen.width, window.screen.height) < 920) {
    return true;
  }
  return false;
}

function hopCloseWebcamCapture() {
  const overlay = document.getElementById('hop-webcam-overlay');
  const stream = hopState._webcamStream;
  if (stream) {
    try {
      stream.getTracks().forEach((t) => t.stop());
    } catch (_) { /* ignore */ }
    hopState._webcamStream = null;
  }
  if (overlay) overlay.remove();
}

/**
 * Cross-platform photo pick:
 * - Phone / Android app: native camera or gallery via <input capture>
 * - Desktop browser / desktop app: live webcam modal (getUserMedia), else file picker
 */
function hopPickPhoto(inputId) {
  const el = document.getElementById(inputId);
  if (!el) return;
  const wantsCamera = inputId.includes('-cam') || el.hasAttribute('capture');

  if (
    wantsCamera
    && !hopIsLikelyMobileDevice()
    && navigator.mediaDevices
    && typeof navigator.mediaDevices.getUserMedia === 'function'
  ) {
    hopOpenWebcamCapture(
      (file) => hopApplyCapturedFile(inputId, el, file),
      () => {
        // Webcam blocked — fall back to file chooser (same as Gallery/Files)
        try { el.removeAttribute('capture'); } catch (_) { /* ignore */ }
        el.click();
      },
    );
    return;
  }
  el.click();
}

function hopApplyCapturedFile(inputId, inputEl, file) {
  if (!file) return;
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    if (inputEl) inputEl.files = dt.files;
  } catch (_) { /* Safari / some WebViews block DataTransfer */ }

  if (inputId.startsWith('hop-vcard')) {
    hopState.visitingCardFile = file;
    const box = document.getElementById('hop-vcard-preview');
    if (box) {
      box.classList.remove('is-empty');
      box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Visiting card" />`;
    }
    const status = document.getElementById('hop-vcard-status');
    if (status) {
      status.textContent = 'Photo ready — reading…';
      status.classList.add('is-busy');
      status.classList.remove('is-error', 'is-ok');
    }
    const form = document.getElementById('hop-vcard-form');
    if (form) {
      form.classList.add('is-idle');
      form.classList.remove('is-ready');
      form.innerHTML = `
        <div class="hop-vcard-idle">
          <strong>Review</strong>
          <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
        </div>`;
    }
    hopScheduleVisitingCardScan();
    return;
  }

  hopState.fabricPreview = hopState.fabricPreview || {};
  if (inputId.includes('fabric-item')) {
    hopState.fabricPreview.itemFile = file;
    const box = document.getElementById('hop-fabric-item-preview');
    if (box) box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Preview" />`;
  } else if (inputId.includes('fabric-swatch')) {
    hopState.fabricPreview.fabricFile = file;
    const box = document.getElementById('hop-fabric-swatch-preview');
    if (box) box.innerHTML = `<img src="${URL.createObjectURL(file)}" alt="Preview" />`;
  }

  try {
    inputEl?.dispatchEvent(new Event('change', { bubbles: true }));
  } catch (_) { /* ignore */ }
}

function hopOpenWebcamCapture(onCapture, onFallback) {
  hopCloseWebcamCapture();
  const overlay = document.createElement('div');
  overlay.id = 'hop-webcam-overlay';
  overlay.className = 'hop-webcam-overlay';
  overlay.innerHTML = `
    <div class="hop-webcam-dialog" role="dialog" aria-modal="true" aria-label="Camera">
      <header class="hop-webcam-header">
        <strong>Camera</strong>
        <span class="nx-text-dim">Works on web &amp; desktop — allow camera access if asked</span>
      </header>
      <video id="hop-webcam-video" class="hop-webcam-video" playsinline autoplay muted></video>
      <p id="hop-webcam-error" class="nx-oc-error hidden"></p>
      <div class="hop-webcam-actions">
        <button type="button" class="nx-btn nx-btn-primary" id="hop-webcam-snap">Capture</button>
        <button type="button" class="nx-btn" id="hop-webcam-files">Use file instead</button>
        <button type="button" class="nx-btn" id="hop-webcam-cancel">Cancel</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const video = document.getElementById('hop-webcam-video');
  const errEl = document.getElementById('hop-webcam-error');
  const finishFallback = () => {
    hopCloseWebcamCapture();
    if (typeof onFallback === 'function') onFallback();
  };

  document.getElementById('hop-webcam-cancel')?.addEventListener('click', () => hopCloseWebcamCapture());
  document.getElementById('hop-webcam-files')?.addEventListener('click', finishFallback);
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) hopCloseWebcamCapture();
  });

  document.getElementById('hop-webcam-snap')?.addEventListener('click', () => {
    if (!video || !video.videoWidth) {
      if (errEl) {
        errEl.textContent = 'Camera not ready yet — wait a second or use file instead.';
        errEl.classList.remove('hidden');
      }
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) {
        finishFallback();
        return;
      }
      const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
      hopCloseWebcamCapture();
      if (typeof onCapture === 'function') onCapture(file);
    }, 'image/jpeg', 0.92);
  });

  navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    audio: false,
  }).then((stream) => {
    hopState._webcamStream = stream;
    if (video) {
      video.srcObject = stream;
      video.play?.().catch(() => {});
    }
  }).catch((err) => {
    if (errEl) {
      errEl.textContent = (err && err.message) ? err.message : 'Camera permission denied';
      errEl.classList.remove('hidden');
    }
    // Auto-offer file fallback after a beat
    setTimeout(finishFallback, 600);
  });
}

async function hopScanVisitingCard() {
  const status = document.getElementById('hop-vcard-status');
  const btn = document.getElementById('hop-vcard-scan-btn');
  const formWrap = document.getElementById('hop-vcard-form');
  const file = hopState.visitingCardFile
    || document.getElementById('hop-vcard-cam')?.files?.[0]
    || document.getElementById('hop-vcard-gal')?.files?.[0];
  if (!file) {
    if (status) {
      status.textContent = 'Add a card photo first.';
      status.classList.add('is-error');
      status.classList.remove('is-busy', 'is-ok');
    }
    return;
  }
  if (status) {
    status.textContent = 'Reading card…';
    status.classList.add('is-busy');
    status.classList.remove('is-error', 'is-ok');
  }
  if (btn) btn.disabled = true;
  try {
    const fd = new FormData();
    fd.append('card_image', file, file.name || 'visiting_card.jpg');
    const response = await fetchWithAuth('/api/v1/hop/customers/scan-card', { method: 'POST', body: fd });
    let data;
    try {
      data = await parseApiJson(response);
    } catch (parseErr) {
      if (response.status === 502 || response.status === 503) {
        throw new Error(
          'Server crash / busy (502). Render pe GEMINI_API_KEY set karo, 1 min wait karke dubara try karo.',
        );
      }
      throw new Error(
        response.status === 401
          ? 'Session expire ho gayi — dubara login karo.'
          : (parseErr.message || 'Server ne JSON ki jagah error page bheja. Hard refresh karo.'),
      );
    }
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Card scan failed'));
    }
    const payload = data.data || {};
    const f = payload.fields || {};
    hopState.visitingCardDraft = f;
    if (hopState.vcardMode === 'party') {
      if (status) {
        status.textContent = 'Card read — opening form…';
        status.classList.remove('is-busy', 'is-error');
        status.classList.add('is-ok');
      }
      hopClosePartyScanModal();
      hopOpenPartyEditModal('customer', hopPartyRowFromCardFields(f));
      return;
    }
    const preview = (payload.raw_text_preview || '').trim();
    if (status) {
      let msg = payload.note || 'Verify fields, then save';
      if (payload.engine) msg += ` · ${payload.engine}`;
      if (payload.confidence) msg += ` · ${payload.confidence}`;
      if (!payload.gemini_configured) msg += ' · Tip: GEMINI_API_KEY for best accuracy';
      if (preview && (payload.confidence === 'low' || !f.company)) {
        msg += ` · OCR: ${preview.slice(0, 90)}${preview.length > 90 ? '…' : ''}`;
      }
      status.textContent = msg;
      status.classList.remove('is-busy', 'is-error');
      status.classList.add('is-ok');
    }
    if (formWrap) {
      formWrap.classList.remove('is-idle', 'hidden');
      formWrap.classList.add('is-ready');
      formWrap.innerHTML = `
        <div class="hop-vcard-panel-head">
          <h3>Review &amp; save</h3>
          <span class="hop-vcard-hint">Edit before save · nothing is stored until Save</span>
        </div>
        <div class="hop-vcard-form-grid">
          <label class="hop-vcard-field hop-vcard-span2"><span>Company *</span><input id="vc-company" value="${foEscapeAttr(f.company || '')}" placeholder="Company name" /></label>
          <label class="hop-vcard-field"><span>Contact</span><input id="vc-contact" value="${foEscapeAttr(f.contact_person || '')}" placeholder="Contact person" /></label>
          <label class="hop-vcard-field"><span>Mobile</span><input id="vc-mobile" value="${foEscapeAttr(f.mobile || '')}" placeholder="Mobile" /></label>
          <label class="hop-vcard-field"><span>Email</span><input id="vc-email" value="${foEscapeAttr(f.email || '')}" placeholder="Email" /></label>
          <label class="hop-vcard-field"><span>City</span><input id="vc-city" value="${foEscapeAttr(f.city || '')}" placeholder="City" /></label>
          <label class="hop-vcard-field"><span>Type</span><input id="vc-type" value="${foEscapeAttr(f.customer_type || '')}" placeholder="Hotel / Designer / …" /></label>
          <label class="hop-vcard-field"><span>GSTIN</span><input id="vc-gst" value="${foEscapeAttr(f.gst_no || '')}" placeholder="GSTIN" /></label>
          <label class="hop-vcard-field"><span>PAN</span><input id="vc-pan" value="${foEscapeAttr(f.pan || '')}" placeholder="PAN" /></label>
          <label class="hop-vcard-field hop-vcard-span2"><span>Address</span><input id="vc-address" value="${foEscapeAttr(f.address || '')}" placeholder="Address" /></label>
          <label class="hop-vcard-field hop-vcard-span2"><span>Remarks</span><input id="vc-remarks" value="${foEscapeAttr(f.remarks || '')}" placeholder="Remarks" /></label>
        </div>
        <div class="hop-vcard-form-actions">
          <button type="button" class="nx-btn" onclick="hopResetVisitingCardReview()">Discard</button>
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveVisitingCardCustomer()">Save as customer</button>
        </div>`;
      requestAnimationFrame(() => hopScrollIntoMain(formWrap, 8));
    }
  } catch (e) {
    if (status) {
      status.textContent = e.message || 'Card scan failed';
      status.classList.add('is-error');
      status.classList.remove('is-busy', 'is-ok');
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function hopResetVisitingCardReview() {
  const form = document.getElementById('hop-vcard-form');
  if (!form) return;
  form.classList.add('is-idle');
  form.classList.remove('is-ready');
  form.innerHTML = `
    <div class="hop-vcard-idle">
      <strong>Review</strong>
      <p>Card padhne ke baad fields yahan aayenge. Verify karke Save dabao — auto-save nahi hota.</p>
    </div>`;
}

function foEscapeAttr(value) {
  return foEscapeText(value).replace(/"/g, '&quot;');
}

async function hopSaveVisitingCardCustomer() {
  const company = document.getElementById('vc-company')?.value?.trim();
  if (!company) {
    alert('Company name required — card se miss hua ho to type karo.');
    return;
  }
  const payload = {
    company,
    contact_person: document.getElementById('vc-contact')?.value,
    mobile: document.getElementById('vc-mobile')?.value,
    email: document.getElementById('vc-email')?.value,
    city: document.getElementById('vc-city')?.value,
    customer_type: document.getElementById('vc-type')?.value,
    gst_no: document.getElementById('vc-gst')?.value,
    pan: document.getElementById('vc-pan')?.value,
    address: document.getElementById('vc-address')?.value,
    remarks: document.getElementById('vc-remarks')?.value,
    source: 'visiting_card',
  };
  try {
    const result = await hopCreatePartyWithDupConfirm('/api/v1/hop/customers', payload, {
      method: 'POST',
    });
    if (result === false) {
      if (typeof nexoraToast === 'function') {
        nexoraToast('Save cancelled. Existing similar party rakha gaya.', 'warn', { duration: 4500 });
      }
      return;
    }
    if (result == null) return;
    if (typeof nexoraToast === 'function') nexoraToast('Party saved.', 'ok');
    else alert('Party saved.');
    openHopView('parties');
  } catch (e) {
    alert(e.message || 'Save failed');
  }
}

/* ---------- Deals (new stepwise CRM) ---------- */
const HOP_DEAL_STEPS = [
  { id: 'lead', label: 'Lead received', optional: false },
  { id: 'contacting', label: 'Call / Mail / Message / Visit', optional: false },
  { id: 'appointment', label: 'Appointment', optional: false },
  { id: 'discovery', label: 'Requirement meeting', optional: false },
  { id: 'cataloging', label: 'Cataloging', optional: false },
  { id: 'quotation', label: 'Quotation', optional: false },
  { id: 'negotiation', label: 'Negotiation', optional: false },
  { id: 'po_received', label: 'Customer PO', optional: false },
  { id: 'advance', label: 'Advance payment', optional: true },
  { id: 'vendor_order', label: 'Vendor order', optional: false },
  { id: 'inbound', label: 'Vendor dispatch / inbound track', optional: false },
  { id: 'godown', label: 'Goods at godown', optional: true },
  { id: 'repack', label: 'Repacking', optional: true },
  { id: 'outbound', label: 'Ship to client', optional: false },
  { id: 'delivered', label: 'Delivered', optional: false },
  { id: 'installation', label: 'Installation', optional: true },
  { id: 'collection', label: 'Payment follow-up', optional: false },
  { id: 'closed', label: 'Closed / Won', optional: false },
];

function hopDealStepLabel(id) {
  return (HOP_DEAL_STEPS.find((s) => s.id === id) || {}).label || id || '—';
}

async function renderHopDealsModule(mount) {
  // CRM UI retired — backend hop_deals engines remain. New interface TBD.
  hopState.dealDetailId = null;
  hopState.dealDetail = null;
  mount.innerHTML = hopModuleShell(
    'CRM',
    'Leads',
    '',
    '',
    `<div class="nx-card" style="padding:24px;max-width:520px">
      <h3 style="margin:0 0 8px">CRM interface removed</h3>
      <p class="nx-text-dim" style="margin:0 0 16px">
        The previous Leads / Deals screens were cleared so we can rebuild the UI.
        Deal engines and APIs are still in place.
      </p>
      <button type="button" class="nx-btn nx-btn-primary" onclick="openHopView('dashboard')">Go to Dashboard</button>
    </div>`,
  );
}

const HOP_DEAL_PHASES = [
  { id: 'engage', label: 'Engage', from: 0, to: 2 },
  { id: 'discover', label: 'Discover', from: 3, to: 4 },
  { id: 'sell', label: 'Sell', from: 5, to: 8 },
  { id: 'fulfill', label: 'Fulfill', from: 9, to: 13 },
  { id: 'close', label: 'Close', from: 14, to: 17 },
];

function hopDealPhaseForIndex(idx) {
  return HOP_DEAL_PHASES.find((p) => idx >= p.from && idx <= p.to) || HOP_DEAL_PHASES[0];
}

async function renderHopDealDetail(mount, dealId) {
  let deal;
  try {
    deal = await hopApi(`/api/v1/hop/deals/${dealId}`);
  } catch (e) {
    hopState.dealDetailId = null;
    mount.innerHTML = hopModuleShell('CRM', 'Deal', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  hopState.dealDetail = deal;
  const steps = deal.steps || HOP_DEAL_STEPS;
  const idx = Number(deal.step_index || 0);
  const isOpen = deal.status === 'open';
  const partyLabel = deal.customer_company || deal.party_name || '—';
  const total = steps.length;
  const progressPct = deal.status === 'closed'
    ? 100
    : Math.round((Math.min(idx, total - 1) / Math.max(total - 1, 1)) * 100);
  const cur = steps[idx] || steps[0];
  const phase = hopDealPhaseForIndex(idx);
  const nextStep = isOpen && idx + 1 < total ? steps[idx + 1] : null;

  const phasesHtml = HOP_DEAL_PHASES.map((p) => {
    let pState = 'locked';
    if (deal.status === 'closed') pState = 'done';
    else if (deal.status === 'lost' && idx >= p.from && idx <= p.to) pState = 'lost';
    else if (idx > p.to) pState = 'done';
    else if (idx >= p.from && idx <= p.to) pState = 'current';
    const phaseSteps = steps.slice(p.from, p.to + 1).map((s, j) => {
      const si = p.from + j;
      let st = 'locked';
      if (deal.status === 'closed' || si < idx) st = 'done';
      else if (deal.status === 'lost' && si === idx) st = 'lost';
      else if (si === idx) st = 'current';
      return `<li class="hop-deal-phase-step hop-deal-phase-step--${st}">
        <span class="hop-deal-phase-mark">${st === 'done' ? '✓' : si + 1}</span>
        <span>${foEscapeText(s.label)}${s.optional ? ' <em>opt</em>' : ''}</span>
      </li>`;
    }).join('');
    return `<div class="hop-deal-phase hop-deal-phase--${pState}">
      <div class="hop-deal-phase-head">
        <span class="hop-deal-phase-dot"></span>
        <strong>${foEscapeText(p.label)}</strong>
        ${pState === 'current' ? '<span class="hop-deal-phase-now">Now</span>' : ''}
      </div>
      ${pState === 'current' || pState === 'lost' ? `<ul class="hop-deal-phase-steps">${phaseSteps}</ul>` : ''}
    </div>`;
  }).join('');

  const actions = isOpen ? `
    <div class="hop-deal-actions">
      <button type="button" class="nx-btn nx-btn-primary" onclick="hopDealAction('complete_step')">Complete step</button>
      ${cur?.optional ? `<button type="button" class="nx-btn" onclick="hopDealAction('skip_step')">Skip</button>` : ''}
      <button type="button" class="nx-btn hop-deal-lost-btn" onclick="hopDealAction('mark_lost')">Mark lost</button>
    </div>
    <label class="hop-deal-note-label">Note
      <input id="hop-deal-step-note" class="hop-deal-note-input" placeholder="What happened on this step?" />
    </label>
    ${nextStep ? `<p class="hop-deal-next-hint">Next → <strong>${foEscapeText(nextStep.label)}</strong></p>` : ''}
  ` : `
    <div class="hop-deal-actions">
      <button type="button" class="nx-btn nx-btn-primary" onclick="hopDealAction('reopen')">Reopen deal</button>
    </div>
  `;

  const events = (deal.events || []).slice(0, 12).map((ev) => `
    <li class="hop-deal-event">
      <span class="hop-deal-event-time">${foEscapeText(String(ev.created_at || '').replace('T', ' ').slice(0, 16))}</span>
      <div class="hop-deal-event-body">
        <span class="hop-deal-event-title">${foEscapeText(ev.title || ev.event_type)}</span>
        ${ev.detail ? `<span class="hop-deal-event-detail">${foEscapeText(ev.detail)}</span>` : ''}
      </div>
    </li>
  `).join('') || '<li class="hop-deal-event hop-deal-event--empty">No activity yet</li>';

  const snapRow = (label, value) => value
    ? `<div class="hop-deal-snap-row"><span>${foEscapeText(label)}</span><strong>${value}</strong></div>`
    : '';

  const body = `
    <div class="hop-deal-detail">
      <header class="hop-deal-hero">
        <div class="hop-deal-hero-main">
          <div class="hop-deal-hero-top">
            <span class="hop-deal-code">${foEscapeText(deal.deal_number || '')}</span>
            <span class="hop-deal-status hop-deal-status--${foEscapeAttr(deal.status || 'open')}">${foEscapeText(deal.status || 'open')}</span>
          </div>
          <h3 class="hop-deal-title">${foEscapeText(deal.title)}</h3>
          <p class="hop-deal-meta">
            <span class="hop-deal-meta-pill"><em>Party</em> ${foEscapeText(partyLabel)}</span>
            ${deal.customer_mobile ? `<span class="hop-deal-meta-pill"><em>Mobile</em> ${foEscapeText(deal.customer_mobile)}</span>` : ''}
            <span class="hop-deal-meta-pill"><em>Value</em> ${hopMoney(deal.expected_value)}</span>
            <span class="hop-deal-meta-pill"><em>Phase</em> ${foEscapeText(phase.label)}</span>
          </p>
        </div>
        <div class="hop-deal-detail-btns">
          <button type="button" class="nx-btn" onclick="hopCloseDealDetail()">Back</button>
          <button type="button" class="nx-btn" onclick="hopEditDeal(${deal.id})">Edit</button>
          <button type="button" class="nx-btn hop-contact-icon-del" onclick="hopDeleteDeal(${deal.id}, '${foEscapeAttr(deal.title || '')}')">Delete</button>
        </div>
      </header>

      <div class="hop-deal-grid">
        <section class="hop-deal-progress-card">
          <div class="hop-deal-progress-head">
            <div>
              <p class="hop-deal-kicker">Current step</p>
              <h4 class="hop-deal-now-title">${foEscapeText(cur?.label || deal.current_step)}</h4>
              <p class="hop-deal-progress-sub">${foEscapeText(phase.label)} · Step ${Math.min(idx + 1, total)} of ${total}</p>
            </div>
            <div class="hop-deal-progress-pct" aria-label="${progressPct} percent">${progressPct}%</div>
          </div>
          <div class="hop-deal-progress-bar" aria-hidden="true"><span style="width:${progressPct}%"></span></div>
          ${actions}
        </section>

        <aside class="hop-deal-snapshot">
          <p class="hop-deal-kicker">Deal facts</p>
          ${snapRow('Party', foEscapeText(partyLabel))}
          ${snapRow('Value', hopMoney(deal.expected_value))}
          ${snapRow('Source', foEscapeText(deal.source || ''))}
          ${snapRow('Assigned', foEscapeText(deal.assigned_to || ''))}
          ${snapRow('Fulfillment', foEscapeText(deal.fulfillment_mode === 'drop_ship' ? 'Drop-ship' : (deal.fulfillment_mode === 'godown' ? 'Via godown' : '')))}
          ${snapRow('Products', foEscapeText(deal.products_interested || ''))}
          ${deal.notes ? `<p class="hop-deal-snap-notes">${foEscapeText(deal.notes)}</p>` : '<p class="hop-deal-snap-empty">No extra notes</p>'}
        </aside>
      </div>

      <section class="hop-deal-journey">
        <p class="hop-deal-kicker">Journey</p>
        <div class="hop-deal-phases">${phasesHtml}</div>
      </section>

      <section class="hop-deal-activity-wrap">
        <p class="hop-deal-kicker">Activity</p>
        <ul class="hop-deal-timeline">${events}</ul>
      </section>
    </div>`;
  mount.innerHTML = hopModuleShell(
    'CRM',
    deal.title || 'Deal',
    `${deal.deal_number || ''} · ${partyLabel}`,
    `<button type="button" class="nx-btn" onclick="hopEditDeal(${deal.id})">Edit</button>`,
    body,
  );
}

function hopOpenDeal(id) {
  hopState.dealDetailId = Number(id);
  openHopView('deals', { skipHistory: true });
}

function hopCloseDealDetail() {
  hopState.dealDetailId = null;
  hopState.dealDetail = null;
  openHopView('deals', { skipHistory: true });
}

async function hopDealAction(action) {
  const deal = hopState.dealDetail;
  if (!deal?.id) return;
  const note = document.getElementById('hop-deal-step-note')?.value || '';
  let payload = { action };
  if (action === 'complete_step' || action === 'skip_step') payload.step_note = note;
  if (action === 'mark_lost') {
    const reason = prompt('Lost reason?', note || 'Lost before payment');
    if (reason == null) return;
    payload.lost_reason = reason;
  }
  try {
    await hopApi(`/api/v1/hop/deals/${deal.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (typeof nexoraToast === 'function') nexoraToast('Deal updated', 'ok');
    openHopView('deals', { skipHistory: true });
  } catch (e) {
    alert(e.message || 'Update failed');
  }
}

function hopCloseDealFormModal() {
  document.getElementById('hop-deal-form-modal')?.remove();
}

async function hopShowDealForm(editRow) {
  hopCloseDealFormModal();
  if (!(hopState.customers || []).length) {
    try { hopState.customers = await hopApi('/api/v1/hop/customers') || []; } catch (_) { /* ignore */ }
  }
  const row = editRow || {};
  const isEdit = !!row.id;
  const custOpts = ['<option value="">Select existing party…</option>']
    .concat((hopState.customers || []).map((c) =>
      `<option value="${c.id}"${String(c.id) === String(row.customer_id || '') ? ' selected' : ''}>${foEscapeText(c.company)}</option>`))
    .join('');
  const modal = document.createElement('div');
  modal.id = 'hop-deal-form-modal';
  modal.className = 'nx-party-modal hop-deal-form-modal';
  hopDecoratePartyModalTheme(modal);
  modal.innerHTML = `
    <div class="nx-party-modal-backdrop" onclick="hopCloseDealFormModal()"></div>
    <div class="nx-party-modal-card hop-deal-form-card" role="dialog" aria-modal="true" aria-label="${isEdit ? 'Edit Deal' : 'New Deal'}">
      <div class="nx-party-modal-head">
        <div class="hop-deal-form-head-copy">
          <p class="hop-deal-form-kicker">CRM · Deal</p>
          <h2>${isEdit ? 'Edit Deal' : 'New Deal'}</h2>
        </div>
        <button type="button" class="nx-party-modal-close" onclick="hopCloseDealFormModal()" title="Close">&times;</button>
      </div>
      <div class="nx-party-modal-body hop-deal-form-body">
        <section class="hop-deal-form-section">
          <p class="hop-deal-form-sec-title">Deal</p>
          <div class="hop-deal-form-grid">
            <label class="nx-party-field hop-deal-form-span-2">
              <span>Deal title *</span>
              <input id="f-dtitle" value="${foEscapeAttr(row.title || '')}" placeholder="e.g. Sofa Lead · Westin curtains" autocomplete="off" />
            </label>
            <label class="nx-party-field">
              <span>Expected value</span>
              <input id="f-dvalue" type="number" min="0" step="1" value="${foEscapeAttr(row.expected_value ?? '')}" placeholder="₹" />
            </label>
            <label class="nx-party-field">
              <span>Source</span>
              <input id="f-dsource" value="${foEscapeAttr(row.source || '')}" placeholder="Walk-in · Referral · WhatsApp" />
            </label>
            <label class="nx-party-field">
              <span>Assigned to</span>
              <input id="f-dassigned" value="${foEscapeAttr(row.assigned_to || '')}" placeholder="Sales person" />
            </label>
            <label class="nx-party-field">
              <span>Fulfillment</span>
              <select id="f-dfulfill">
                <option value="">Decide later</option>
                <option value="godown"${row.fulfillment_mode === 'godown' ? ' selected' : ''}>Via godown</option>
                <option value="drop_ship"${row.fulfillment_mode === 'drop_ship' ? ' selected' : ''}>Drop-ship</option>
              </select>
            </label>
          </div>
        </section>

        <section class="hop-deal-form-section">
          <p class="hop-deal-form-sec-title">Party</p>
          <p class="hop-deal-form-sec-hint">Pick an existing Vyapar / CRM party, or add a new one below.</p>
          <div class="hop-deal-form-grid">
            <label class="nx-party-field hop-deal-form-span-2">
              <span>Existing party</span>
              <select id="f-dcustomer">${custOpts}</select>
            </label>
          </div>
          <div class="hop-deal-form-newparty">
            <p class="hop-deal-form-sec-title hop-deal-form-sec-title--sub">Or create new party</p>
            <div class="hop-deal-form-grid">
              <label class="nx-party-field hop-deal-form-span-2">
                <span>Company / party name</span>
                <input id="f-dnewparty" placeholder="Type name — similar parties appear below" autocomplete="off"
                  oninput="hopPartyLiveDupCheck({nameId:'f-dnewparty',phoneId:'f-dnewmobile',gstId:'f-dnewgst',slotId:'f-dnewparty-dup'})" />
                <div id="f-dnewparty-dup" class="hop-party-live-dup hidden" aria-live="polite"></div>
              </label>
              <label class="nx-party-field">
                <span>Mobile</span>
                <input id="f-dnewmobile" inputmode="tel" placeholder="10-digit"
                  oninput="hopPartyLiveDupCheck({nameId:'f-dnewparty',phoneId:'f-dnewmobile',gstId:'f-dnewgst',slotId:'f-dnewparty-dup'})" />
              </label>
              <label class="nx-party-field">
                <span>GSTIN</span>
                <input id="f-dnewgst" placeholder="Optional"
                  oninput="hopPartyLiveDupCheck({nameId:'f-dnewparty',phoneId:'f-dnewmobile',gstId:'f-dnewgst',slotId:'f-dnewparty-dup'})" />
              </label>
            </div>
          </div>
        </section>

        <section class="hop-deal-form-section">
          <p class="hop-deal-form-sec-title">Notes</p>
          <div class="hop-deal-form-grid">
            <label class="nx-party-field hop-deal-form-span-2">
              <span>Products interested</span>
              <input id="f-dproducts" value="${foEscapeAttr(row.products_interested || '')}" placeholder="Sofa, curtains, mattress…" />
            </label>
            <label class="nx-party-field hop-deal-form-span-2">
              <span>Internal notes</span>
              <textarea id="f-dnotes" rows="2" placeholder="Anything the team should know…">${foEscapeText(row.notes || '')}</textarea>
            </label>
          </div>
        </section>
      </div>
      <div class="nx-party-modal-foot">
        <button type="button" class="nx-btn" onclick="hopCloseDealFormModal()">Cancel</button>
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveDeal(${isEdit ? row.id : 'null'})">${isEdit ? 'Update deal' : 'Create deal'}</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  requestAnimationFrame(() => modal.classList.add('is-open'));
  document.getElementById('f-dtitle')?.focus();
}

async function hopEditDeal(id) {
  let row = (hopState.deals || []).find((d) => Number(d.id) === Number(id));
  if (!row && hopState.dealDetail && Number(hopState.dealDetail.id) === Number(id)) {
    row = hopState.dealDetail;
  }
  if (!row) {
    try { row = await hopApi(`/api/v1/hop/deals/${id}`); } catch (e) {
      alert(e.message || 'Load failed');
      return;
    }
  }
  await hopShowDealForm(row);
}

async function hopSaveDeal(editId) {
  const title = document.getElementById('f-dtitle')?.value?.trim();
  if (!title) {
    alert('Deal title required');
    return;
  }
  const customerId = document.getElementById('f-dcustomer')?.value || '';
  const newPartyName = document.getElementById('f-dnewparty')?.value?.trim();
  const payload = {
    title,
    customer_id: customerId || null,
    source: document.getElementById('f-dsource')?.value,
    expected_value: document.getElementById('f-dvalue')?.value,
    assigned_to: document.getElementById('f-dassigned')?.value,
    fulfillment_mode: document.getElementById('f-dfulfill')?.value || null,
    products_interested: document.getElementById('f-dproducts')?.value,
    notes: document.getElementById('f-dnotes')?.value,
  };
  if (!customerId && newPartyName) {
    payload.new_party = {
      company: newPartyName,
      mobile: document.getElementById('f-dnewmobile')?.value,
      gst_no: document.getElementById('f-dnewgst')?.value,
      source: payload.source || 'CRM Deal',
    };
  }
  try {
    const url = editId ? `/api/v1/hop/deals/${editId}` : '/api/v1/hop/deals';
    const method = editId ? 'PATCH' : 'POST';
    let response;
    try {
      response = await fetchWithAuth(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      if (isSessionTimeoutError(e)) return;
      throw e;
    }
    let data = await parseApiJson(response);
    if (response.status === 409 && data.requires_confirmation) {
      const matches = data.data?.matches || [];
      const msg = typeof hopPartyDupConfirmMessage === 'function'
        ? hopPartyDupConfirmMessage(matches, data.message)
        : (data.message || 'A similar party already exists. Save as a new party anyway?');
      const ok = await nexoraConfirm(msg, {
        title: 'Party already saved',
        okText: 'Yes, save new party',
        cancelText: 'Cancel',
      });
      if (!ok) return;
      response = await fetchWithAuth(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, force_save: true, new_party: payload.new_party
          ? { ...payload.new_party, force_save: true }
          : undefined }),
      });
      data = await parseApiJson(response);
    }
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Save failed'));
    }
    hopState.customers = [];
    hopCloseDealFormModal();
    if (typeof nexoraToast === 'function') nexoraToast(editId ? 'Deal updated' : 'Deal created', 'ok');
    openHopView('deals', { skipHistory: true });
  } catch (e) {
    alert(e.message || 'Save failed');
  }
}

async function hopDeleteDeal(id, title) {
  const ok = typeof nexoraConfirm === 'function'
    ? await nexoraConfirm(`Delete lead “${title || id}”? Ye undo nahi hoga.`, {
      title: 'Delete lead',
      danger: true,
      okText: 'Delete',
    })
    : window.confirm(`Delete lead “${title || id}”?`);
  if (!ok) return;
  try {
    await hopApi(`/api/v1/hop/deals/${id}`, { method: 'DELETE' });
    hopState.dealDetailId = null;
    if (typeof nexoraToast === 'function') nexoraToast('Deal deleted', 'ok');
    openHopView('deals', { skipHistory: true });
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

/* ---------- Projects ---------- */
async function renderHopProjectsModule(mount) {
  const q = hopState.search.projects || '';
  let rows = [];
  try { rows = await hopApi(`/api/v1/hop/projects?q=${encodeURIComponent(q)}`) || []; hopState.projects = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Projects', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Projects', value: rows.length, tone: 'unpaid' },
    ])}
    ${hopTable(
      ['Project', 'Customer', 'Hotel', 'Stage', 'Value', 'Prob %', 'Completion %', 'Next Milestone', 'Assigned', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.customer_company || r.client_name)}</td>
        <td>${hopCell(r.hotel_name)}</td><td><span class="hop-stage-pill">${hopCell(r.stage)}</span></td>
        <td class="inv-num">${hopMoney(r.project_value ?? r.expected_value)}</td><td>${hopCell(r.probability_pct)}</td>
        <td>${hopCell(r.completion_pct)}</td><td>${hopCell(r.next_milestone)}</td><td>${hopCell(r.assigned_to)}</td>
        <td><button type="button" class="nx-btn" onclick="openHopProjectHub(${r.id})">Open hub</button></td>
      </tr>`).join(''),
      { label: 'Projects', count: rows.length, searchValue: q, searchId: 'hop-q' },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Projects', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('project')">+ New Project</button>`, body);
}

/* ---------- Leads ---------- */
async function renderHopLeadsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/leads') || []; hopState.leads = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Leads', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'Leads', value: rows.length, tone: 'unpaid' }])}
    ${hopTable(
      ['Lead No', 'Customer', 'Project', 'Source', 'Value', 'Priority', 'Sales', 'Stage', 'Prob %', 'Follow-up', 'Status', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.lead_number)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.source)}</td><td class="inv-num">${hopMoney(r.expected_value)}</td><td>${hopCell(r.priority)}</td>
        <td>${hopCell(r.assigned_to)}</td>
        <td><select onchange="hopPatchLead(${r.id}, this.value)">${hopStageOptions(HOP_LEAD_STAGES, r.stage)}</select></td>
        <td>${hopCell(r.probability_pct)}</td><td>${hopCell(r.next_follow_up)}</td><td>${hopCell(r.status)}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Project</button>` : '—'}</td>
      </tr>`).join(''),
      { label: 'Leads', count: rows.length, searchId: 'hop-q' },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Leads', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('lead')">+ New Lead</button>`, body);
}

async function hopPatchLead(id, stage) {
  try {
    await hopApi(`/api/v1/hop/leads/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage }) });
    openHopView('leads');
  } catch (e) { alert(e.message); }
}

/* ---------- Meetings ---------- */
async function renderHopMeetingsModule(mount) {
  let rows = [];
  let dash = null;
  try {
    rows = await hopApi('/api/v1/hop/meetings') || [];
    hopState.meetings = rows;
    dash = await hopApi('/api/v1/hop/reports/meetings');
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sales', 'Meetings', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const c = dash?.counts || {};
  const body = `
    ${hopTxCards([
      { label: 'Today', value: c.today ?? 0, tone: 'paid' },
      { label: 'Upcoming', value: c.upcoming ?? 0, tone: 'unpaid', op: '+' },
      { label: 'Missed', value: c.missed ?? 0, tone: 'overdue', op: '+' },
      { label: 'Follow-up Due', value: c.follow_up_due ?? 0, tone: 'total', op: '=' },
    ])}
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTable(
      ['When', 'Title', 'Customer', 'Project', 'Location', 'Status', 'Outcome', 'Next Action', 'Follow-up', 'Expected Value', 'Prob %'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.scheduled_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td>${hopCell(r.title)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.location)}</td><td>${hopCell(r.status)}</td><td>${hopCell(r.outcome)}</td>
        <td>${hopCell(r.next_action)}</td><td>${hopCell(r.follow_up_at)}</td>
        <td class="inv-num">${hopMoney(r.expected_order_value)}</td><td>${hopCell(r.probability_pct)}</td>
      </tr>`).join(''),
      { label: 'Meetings', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Sales', 'Meetings', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('meeting')">+ New Meeting</button>`, body);
}

/* ---------- Sale ledger docs (Estimate / Proforma / SO / Challan / Credit Note / FA) ---------- */
const HOP_SALE_LEDGER = {
  sale_estimates: {
    title: 'Estimate / Quotation',
    types: [...HOP_TXN_ESTIMATE],
    empty: 'No estimates / quotations in this filter.',
    numberLabel: 'Estimate no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopOpenManualDocCreate(27,'commercial')">+ Commercial Quotation</button>
      <button type="button" class="nx-btn inv-add-btn" onclick="hopOpenManualDocCreate(27,'standard')">+ Estimate</button>`,
  },
  sale_proforma: {
    title: 'Proforma Invoice',
    types: [...HOP_TXN_PROFORMA],
    empty: 'No proforma invoices in this filter.',
    numberLabel: 'Proforma no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopOpenManualDocCreate(83,'standard')">+ New Proforma</button>`,
  },
  sale_orders: {
    title: 'Sale Order',
    types: [...HOP_TXN_SALE_ORDER],
    empty: 'No sale orders in this filter.',
    numberLabel: 'Order no',
  },
  sale_challan: {
    title: 'Delivery Challan',
    types: [...HOP_TXN_CHALLAN],
    empty: 'No delivery challans in this filter.',
    numberLabel: 'Challan no',
  },
  sale_returns: {
    title: 'Sale Return / Credit Note',
    types: [...HOP_TXN_SALE_RETURN],
    empty: 'No sale returns / credit notes in this filter.',
    numberLabel: 'Credit note no',
  },
  sale_fa: {
    title: 'Sale FA',
    types: [],
    labelQ: 'fa',
    empty: 'No Sale FA records in this filter.',
    numberLabel: 'FA no',
  },
  sale_payment_in: {
    title: 'Payment-In',
    types: [...HOP_TXN_PAYMENT_IN],
    empty: 'No payment-in records in this filter.',
    numberLabel: 'Receipt no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopShowForm('payment')">+ Record Payment</button>`,
  },
  invoices: {
    title: 'Sale Invoices',
    empty: 'No invoices in this filter.',
    numberLabel: 'Invoice no',
    addHtml: `<button type="button" class="nx-btn nx-btn-primary inv-add-btn" onclick="hopShowForm('invoice')">+ Add Sale</button>`,
  },
  purchase_bills: {
    title: 'Purchase Bills',
    types: [...HOP_TXN_PURCHASE_BILL],
    empty: 'No purchase bills in this filter.',
    numberLabel: 'Bill no',
  },
  purchase_payment_out: {
    title: 'Payment-Out',
    types: [...HOP_TXN_PAYMENT_OUT],
    empty: 'No payment-out records in this filter.',
    numberLabel: 'Voucher no',
  },
  purchase_expenses: {
    title: 'Expenses',
    types: [...HOP_TXN_EXPENSE],
    empty: 'No expenses in this filter.',
    numberLabel: 'Expense no',
  },
  purchase_orders: {
    title: 'Purchase Order',
    types: [],
    labelQ: 'purchase order',
    empty: 'No purchase orders in this filter.',
    numberLabel: 'PO no',
  },
  journal_entries: {
    title: 'Journal Entry',
    types: [...HOP_TXN_JOURNAL],
    empty: 'No journal entries in this filter.',
    numberLabel: 'Entry no',
  },
  purchase_returns: {
    title: 'Purchase Return / Dr. Note',
    types: [...HOP_TXN_PURCHASE_RETURN],
    empty: 'No purchase returns / debit notes in this filter.',
    numberLabel: 'Return no',
  },
  purchase_fa: {
    title: 'Purchase FA',
    types: [],
    labelQ: 'fa',
    empty: 'No Purchase FA records in this filter.',
    numberLabel: 'FA no',
  },
};

const HOP_NON_SALE_NOTE_RE = /\[(estimate\/quotation|estimate|proforma invoice|sale return|sales order|delivery challan)\]/i;

async function hopFilterSaleInvoicesOnly(invoices) {
  const nonSaleNos = new Set();
  const saleNos = new Set();
  try {
    const [nonSale, sale] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=21,27,30,65,81,82,83'),
      hopApi('/api/v1/hop/party-transactions?txn_types=1'),
    ]);
    (nonSale || []).forEach((t) => {
      const n = String(t.txn_number || '').trim().toLowerCase();
      if (n) nonSaleNos.add(n);
    });
    (sale || []).forEach((t) => {
      const n = String(t.txn_number || '').trim().toLowerCase();
      if (n) saleNos.add(n);
    });
  } catch (_) { /* ledger optional */ }
  return (invoices || []).filter((inv) => {
    const no = String(inv.invoice_no || '').trim().toLowerCase();
    const notes = String(inv.notes || '');
    if (HOP_NON_SALE_NOTE_RE.test(notes)) return false;
    // Legacy bad import: Vyapar type 27 Estimates were tagged "[Sale]" (not "[Sale Invoice]").
    if (/\[Sale\]/i.test(notes) && !/\[Sale Invoice\]/i.test(notes)) return false;
    if (no && nonSaleNos.has(no) && !saleNos.has(no)) return false;
    return true;
  });
}

function hopNormalizeLedgerToInvoice(r, fallbackLabel) {
  let amount = Number(r.total_amount ?? r.amount ?? r.value ?? 0);
  let balance = Number(r.balance_amount ?? r.balance ?? 0);
  const txnType = r.txn_type != null && r.txn_type !== '' ? Number(r.txn_type) : null;
  const isNonReceivable = Number.isFinite(txnType) && hopTxnIsNonReceivableType(txnType);
  if (isNonReceivable) balance = 0;
  if (!isNonReceivable && balance > amount + 0.05) amount = balance;
  const paidExplicit = r.paid_amount;
  let paid = isNonReceivable
    ? 0
    : (paidExplicit != null
      ? Number(paidExplicit || 0)
      : Math.max(0, amount - balance));
  const statusRaw = String(r.status_text || r.status || '').toLowerCase();
  let status = String(r.status || 'open');
  if (isNonReceivable) {
    // Quote-like docs: never map to unpaid/paid from balance.
    status = '';
  } else if (statusRaw === 'paid' || statusRaw.includes('paid') || statusRaw.includes('used') || balance <= 0.009) status = 'paid';
  else if (statusRaw.includes('partial')) status = 'partial';
  else if (statusRaw.includes('overdue')) status = 'overdue';
  else if (paid > 0 && balance > 0) status = 'partial';
  else if (amount > 0 && paid <= 0) status = 'unpaid';
  // Legacy tax-inclusive inflate: amount>due with no receipt → snap amount to balance (fully unpaid).
  if (!isNonReceivable && balance > 0.05 && amount > balance + 0.05 && paidExplicit == null) {
    if (status === 'unpaid' || status === 'open' || status === 'overdue' || statusRaw.includes('open')) {
      amount = balance;
      paid = 0;
      status = statusRaw.includes('overdue') ? 'overdue' : 'unpaid';
    }
  }
  return {
    invoice_date: String(r.txn_date || r.invoice_date || r.quote_date || r.paid_at || '').slice(0, 10),
    invoice_no: hopFormatDocNo(
      r.txn_number || r.invoice_no || r.quote_no || '',
      r.txn_date || r.invoice_date || r.quote_date || r.paid_at,
      r.txn_type,
    ),
    customer_company: r.party_name || r.customer_company || '',
    project_name: r.project_name || '',
    amount,
    paid_amount: paid,
    balance,
    due_date: isNonReceivable ? '' : String(r.due_date || r.txn_due_date || '').slice(0, 10),
    status,
    notes: r.txn_label || r.notes || fallbackLabel || '',
    txn_type: Number.isFinite(txnType) ? txnType : undefined,
    txn_label: r.txn_label || fallbackLabel || '',
    // Preview keys: party ledger id preferred; else Vyapar source_txn_id.
    party_txn_id: (() => {
      if (r.party_txn_id != null && r.party_txn_id !== '') return Number(r.party_txn_id);
      // hop_party_transactions rows expose txn_number (ledger API).
      if (r.txn_number !== undefined && r.id != null && r.id !== '') return Number(r.id);
      return null;
    })(),
    source_txn_id: r.source_txn_id != null && r.source_txn_id !== '' ? Number(r.source_txn_id) : null,
  };
}

/** Defense: keep only allowed Vyapar txn types (API filter can be stale/missing). */
function hopFilterRowsByTxnTypes(rows, types) {
  const allow = new Set((types || []).map(Number).filter((n) => Number.isFinite(n)));
  if (!allow.size) return rows || [];
  return (rows || []).filter((r) => {
    const ty = Number(r.txn_type);
    if (Number.isFinite(ty)) return allow.has(ty);
    // Normalized rows without txn_type: fall back to label text for payment filters.
    if (allow.size === 1 && allow.has(3)) {
      return /payment\s*in/i.test(String(r.txn_label || r.notes || ''));
    }
    if (allow.size === 1 && allow.has(4)) {
      return /payment\s*out/i.test(String(r.txn_label || r.notes || ''));
    }
    return false;
  });
}

async function hopLoadSaleDocRows(kind) {
  const cfg = HOP_SALE_LEDGER[kind] || HOP_SALE_LEDGER.invoices;
  if (kind === 'invoices') {
    // Source of truth = Vyapar type 1 ledger. hop_invoices can miss newer imports
    // and reuse the same invoice_no across years (e.g. 106 in 2025 and 2026).
    const [ledgerRaw, invoices] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=1').catch(() => []),
      hopApi('/api/v1/hop/invoices').catch(() => []),
    ]);
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw || [], [1]);
    const fromLedger = ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Sale Invoice'));
    const ledgerKeys = new Set(
      ledger.map((r) => {
        const no = String(r.txn_number || '').trim().toLowerCase();
        const d = String(r.txn_date || '').slice(0, 10);
        return `${no}|${d}`;
      }).filter((k) => k !== '|'),
    );
    const ledgerNos = new Set(
      ledger.map((r) => String(r.txn_number || '').trim().toLowerCase()).filter(Boolean),
    );
    const fromInv = (await hopFilterSaleInvoicesOnly(invoices || [])).filter((inv) => {
      const no = String(inv.invoice_no || '').trim().toLowerCase();
      const d = String(inv.invoice_date || '').slice(0, 10);
      if (ledgerKeys.has(`${no}|${d}`)) return false;
      // Prefer ledger when the same serial exists there (avoids showing stale year collision).
      if (no && ledgerNos.has(no)) return false;
      return true;
    }).map((inv) => hopNormalizeLedgerToInvoice({
      invoice_date: inv.invoice_date,
      invoice_no: inv.invoice_no,
      customer_company: inv.customer_company,
      amount: inv.amount,
      paid_amount: inv.paid_amount,
      balance: inv.balance,
      due_date: inv.due_date,
      status: inv.status,
      notes: inv.notes || 'Sale Invoice',
      txn_label: 'Sale Invoice',
      txn_type: 1,
      source_txn_id: inv.source_txn_id,
      project_name: inv.project_name,
    }, 'Sale Invoice'));
    return [...fromLedger, ...fromInv];
  }
  if (kind === 'sale_estimates') {
    const [ledgerRaw, quotes] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=27').catch(() => []),
      hopApi('/api/v1/hop/quotations').catch(() => []),
    ]);
    hopState.quotations = quotes || [];
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw, [27]);
    const fromLedger = ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Estimate'));
    const ledgerNos = new Set(
      ledger.map((r) => String(r.txn_number || '').trim().toLowerCase()).filter(Boolean),
    );
    const fromQuotes = (quotes || [])
      .filter((q) => !ledgerNos.has(String(q.quote_no || '').trim().toLowerCase()))
      .map((q) => hopNormalizeLedgerToInvoice({
        quote_date: q.quote_date,
        quote_no: q.quote_no,
        customer_company: q.customer_company,
        value: q.value,
        balance: Number(q.value || 0),
        paid_amount: 0,
        status: q.status || 'sent',
        notes: 'Estimate',
        txn_label: 'Estimate',
        txn_type: 27,
        project_name: q.project_name,
      }, 'Estimate'));
    return [...fromLedger, ...fromQuotes];
  }
  if (kind === 'sale_payment_in') {
    const [ledgerRaw, payments] = await Promise.all([
      hopApi('/api/v1/hop/party-transactions?txn_types=3').catch(() => []),
      hopApi('/api/v1/hop/payments').catch(() => []),
    ]);
    // Never trust API alone — Payment-Out (4) must not appear here.
    const ledger = hopFilterRowsByTxnTypes(ledgerRaw, [3]);
    if (ledger.length) {
      return ledger.map((r) => hopNormalizeLedgerToInvoice(r, 'Payment In'));
    }
    return (payments || []).map((p) => hopNormalizeLedgerToInvoice({
      paid_at: p.paid_at,
      invoice_no: p.invoice_no || `PAY-${p.id}`,
      customer_company: p.customer_company,
      amount: p.amount,
      paid_amount: p.amount,
      balance: 0,
      status: 'paid',
      notes: p.notes || 'Payment In',
      txn_label: 'Payment In',
      txn_type: 3,
      project_name: p.project_name,
    }, 'Payment In'));
  }
  const qs = cfg.types?.length
    ? `txn_types=${cfg.types.join(',')}`
    : (cfg.labelQ ? `label_q=${encodeURIComponent(cfg.labelQ)}` : '');
  let rows = await hopApi(`/api/v1/hop/party-transactions${qs ? `?${qs}` : ''}`) || [];
  // Always enforce type filter client-side (stale API may ignore txn_types).
  if (cfg.types?.length) {
    rows = hopFilterRowsByTxnTypes(rows, cfg.types);
  } else if (cfg.labelQ) {
    const q = String(cfg.labelQ).toLowerCase();
    rows = (rows || []).filter((r) => {
      const label = String(r.txn_label || '').toLowerCase();
      if (!label.includes(q)) return false;
      // Never treat Journal (81) / Sales Order as Purchase Order.
      if (kind === 'purchase_orders' && (
        HOP_TXN_JOURNAL.has(Number(r.txn_type))
        || HOP_TXN_SALE_ORDER.has(Number(r.txn_type))
        || /sales?\s*order|journal/i.test(label)
      )) {
        return false;
      }
      return true;
    });
  }
  if (kind === 'sale_fa') {
    rows = rows.filter((r) => /fa/i.test(String(r.txn_label || '')) && !/proforma/i.test(String(r.txn_label || '')) && !/purchase/i.test(String(r.txn_label || '')));
  }
  if (kind === 'purchase_fa') {
    rows = rows.filter((r) => /fa/i.test(String(r.txn_label || '')) && /purchase/i.test(String(r.txn_label || '')));
  }
  // Purchase Return: only type 16. Never fall back to unfiltered "all txns".
  if (kind === 'purchase_returns') {
    rows = hopFilterRowsByTxnTypes(rows, [16]).filter((r) => {
      const label = String(r.txn_label || '').toLowerCase();
      const no = String(r.txn_number || '').toUpperCase();
      if (/estimate|quotation|sale|proforma|payment|hoppi/i.test(label)) return false;
      if (no.startsWith('HOPPI')) return false;
      return true;
    });
  }
  return rows.map((r) => hopNormalizeLedgerToInvoice(r, cfg.title));
}

function hopSaleDocMeta() {
  return hopState.saleDocMeta || HOP_SALE_LEDGER.invoices;
}

function hopBindSaleDocPartyOutsideClick() {
  hopInvoicePartyLabelSync();
  if (!window.__hopInvoicePartyDocBound) {
    window.__hopInvoicePartyDocBound = true;
    document.addEventListener('mousedown', (ev) => {
      if (!ev.target.closest?.('.inv-party-dd') && !ev.target.closest?.('#inv-party-panel')) {
        hopInvoicePartyCloseMenu();
      }
    });
    window.addEventListener('scroll', () => hopInvoicePartyCloseMenu(), true);
    window.addEventListener('resize', () => hopInvoicePartyCloseMenu());
  }
}

async function renderHopSaleDocListModule(mount, kind) {
  const cfg = HOP_SALE_LEDGER[kind] || HOP_SALE_LEDGER.invoices;
  hopState.saleDocMeta = cfg;
  hopState.saleDocKind = kind;
  if (!hopState.invoiceUi) hopState.invoiceUi = { period: 'this_month', status: 'all', q: '', party: '', from: '', to: '' };
  if (hopSaleDocIsNonReceivable(kind)) hopState.invoiceUi.status = 'all';
  let rows = [];
  try {
    rows = await hopLoadSaleDocRows(kind);
    hopState.invoices = rows;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', cfg.title, '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  if (!hopState.invoiceUi.from && hopState.invoiceUi.period !== 'all' && hopState.invoiceUi.period !== 'custom') {
    const range = hopInvoicePeriodRange(hopState.invoiceUi.period || 'this_month');
    hopState.invoiceUi.from = range.from;
    hopState.invoiceUi.to = range.to;
  }
  // Delivery Challan / older docs: default "This Month" can hide everything.
  // If the range filters to zero but data exists, open All Time automatically.
  if (
    String(hopState.invoiceUi.period || '') === 'this_month'
    && rows.length > 0
    && hopFilteredInvoices().length === 0
  ) {
    hopState.invoiceUi.period = 'all';
    hopState.invoiceUi.from = '';
    hopState.invoiceUi.to = '';
  }
  const ui = hopState.invoiceUi;
  const parties = [...new Set(rows.map((r) => r.customer_company || r.party_name || r.client_name).filter(Boolean))]
    .sort((a, b) => String(a).localeCompare(String(b)));
  hopState.invoicePartyNames = parties;

  const filtered = hopFilteredInvoices();
  const sum = hopInvoiceSummary(filtered);
  const numberLabel = cfg.numberLabel || 'Number';
  const isPayVoucher = hopIsPaymentVoucherKind(kind);
  const isNonReceivable = hopSaleDocIsNonReceivable(kind);
  const paidLabel = isPayVoucher ? 'Used' : 'Paid';
  const unpaidLabel = isPayVoucher ? 'Unused' : 'Unpaid';

  document.querySelectorAll('body > #inv-party-panel').forEach((el) => el.remove());

  const statusFilterHtml = isNonReceivable
    ? ''
    : `<select id="inv-status" class="inv-ctrl" onchange="hopInvoiceApplyFilters()">
            <option value="all"${ui.status === 'all' ? ' selected' : ''}>All Status</option>
            <option value="paid"${ui.status === 'paid' ? ' selected' : ''}>${paidLabel}</option>
            <option value="unpaid"${ui.status === 'unpaid' ? ' selected' : ''}>${unpaidLabel}</option>
            <option value="partial"${ui.status === 'partial' ? ' selected' : ''}>Partial</option>
            ${isPayVoucher ? '' : `<option value="overdue"${ui.status === 'overdue' ? ' selected' : ''}>Overdue</option>
            <option value="open"${ui.status === 'open' ? ' selected' : ''}>Open</option>`}
          </select>`;

  const summaryCards = isNonReceivable
    ? hopTxCards([
        { label: 'Documents', value: String(sum.count), tone: 'neutral', id: 'inv-sum-count-card' },
        { label: 'Total value', valueHtml: hopMoney(sum.total), tone: 'total', id: 'inv-sum-total' },
      ])
    : hopTxCards(isPayVoucher
          ? [
              { label: 'Used', valueHtml: hopMoney(sum.received), tone: 'paid', id: 'inv-sum-paid' },
              { label: 'Unused', valueHtml: hopMoney(sum.unpaid), tone: 'unpaid', op: '+', id: 'inv-sum-unpaid' },
              { label: 'Total', valueHtml: hopMoney(sum.total), tone: 'total', op: '=', id: 'inv-sum-total' },
            ]
          : [
              { label: 'Paid', valueHtml: hopMoney(sum.received), tone: 'paid', id: 'inv-sum-paid' },
              { label: 'Unpaid', valueHtml: hopMoney(sum.unpaid), tone: 'unpaid', op: '+', id: 'inv-sum-unpaid' },
              { label: 'Overdue', valueHtml: hopMoney(sum.overdue), tone: 'overdue', op: '+', id: 'inv-sum-overdue' },
              { label: 'Total', valueHtml: hopMoney(sum.total), tone: 'total', op: '=', id: 'inv-sum-total' },
            ]);

  const body = `
        <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
        ${hopTxToolbar(`
          <select id="inv-period" class="inv-ctrl" onchange="hopInvoiceOnPeriodChange()">
            <option value="today"${ui.period === 'today' ? ' selected' : ''}>Today</option>
            <option value="this_week"${ui.period === 'this_week' ? ' selected' : ''}>This Week</option>
            <option value="this_month"${ui.period === 'this_month' ? ' selected' : ''}>This Month</option>
            <option value="last_month"${ui.period === 'last_month' ? ' selected' : ''}>Last Month</option>
            <option value="this_quarter"${ui.period === 'this_quarter' ? ' selected' : ''}>This Quarter</option>
            <option value="this_year"${ui.period === 'this_year' ? ' selected' : ''}>This Year</option>
            <option value="all"${ui.period === 'all' ? ' selected' : ''}>All Time</option>
            <option value="custom"${ui.period === 'custom' ? ' selected' : ''}>Custom</option>
          </select>
          <input type="date" id="inv-from" class="inv-ctrl inv-date" value="${foEscapeAttr(ui.from || '')}" onchange="hopInvoiceApplyFilters()" />
          <span class="inv-sep">to</span>
          <input type="date" id="inv-to" class="inv-ctrl inv-date" value="${foEscapeAttr(ui.to || '')}" onchange="hopInvoiceApplyFilters()" />
          ${statusFilterHtml}
          <div class="inv-party-dd">
            <button type="button" id="inv-party-toggle" class="inv-ctrl inv-party-toggle" onclick="hopInvoicePartyToggle(event)" aria-haspopup="listbox" aria-expanded="false">
              <span id="inv-party-label" class="inv-party-label${!ui.party ? ' is-placeholder' : ''}">${foEscapeText(ui.party || 'All Parties')}</span>
              <span class="inv-party-caret" aria-hidden="true">▾</span>
            </button>
            <div id="inv-party-panel" class="inv-party-panel hidden" role="listbox">
              <div class="inv-party-search-wrap">
                <input id="inv-party-q" class="inv-party-q" type="search" autocomplete="off"
                  placeholder="Type to search party…"
                  oninput="hopInvoicePartyOnInput()"
                  onkeydown="if(event.key==='Escape'){hopInvoicePartyCloseMenu();}" />
              </div>
              <div id="inv-party-menu" class="inv-party-menu-list"></div>
            </div>
          </div>
          <button type="button" class="inv-text-btn" onclick="hopInvoiceResetFilters()">Reset</button>
          <span class="inv-toolbar-spacer"></span>
          <button type="button" class="inv-text-btn" onclick="hopInvoiceExportCsv()">Excel</button>
          <button type="button" class="inv-text-btn" onclick="window.print()">Print</button>
        `)}
        ${summaryCards}
        <div class="inv-table-card">
          <div class="inv-table-head">
            <strong>Transactions</strong>
            <span id="inv-sum-count" class="inv-count">${sum.count} txns</span>
            <input id="inv-search" class="inv-search" type="search" placeholder="Search…"
              value="${foEscapeAttr(ui.q || '')}" oninput="hopInvoiceApplyFilters()" />
          </div>
          <div class="inv-table-wrap">
            <table class="inv-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>${foEscapeText(numberLabel)}</th>
                  <th>Party Name</th>
                  <th class="inv-num">Amount</th>
                  <th class="inv-num">${isNonReceivable ? '—' : 'Balance'}</th>
                  <th>${isNonReceivable ? '—' : 'Due date'}</th>
                  <th>Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody id="inv-tbody">${hopRenderInvoiceRows(filtered)}</tbody>
            </table>
          </div>
        </div>`;
  const section = String(kind || '').startsWith('purchase_')
    ? 'Purchase'
    : (kind === 'journal_entries' ? 'Accounting' : 'Sale');
  mount.innerHTML = hopModuleShell(section, cfg.title, '', cfg.addHtml || '', body);
  hopBindSaleDocPartyOutsideClick();
}

async function renderHopSaleLedgerModule(mount, kind) {
  return renderHopSaleDocListModule(mount, kind);
}

async function renderHopSaleEstimatesModule(mount) {
  return renderHopSaleDocListModule(mount, 'sale_estimates');
}

async function renderHopPaymentInModule(mount) {
  return renderHopSaleDocListModule(mount, 'sale_payment_in');
}

/* ---------- Quotations ---------- */
async function renderHopQuotationsModule(mount) {
  return renderHopSaleEstimatesModule(mount);
}

async function hopPatchQuote(id, status) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    openHopView('sale_estimates');
  } catch (e) { alert(e.message); }
}

async function hopReviseQuote(id) {
  try {
    await hopApi(`/api/v1/hop/quotations/${id}/revise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    openHopView('sale_estimates');
  } catch (e) { alert(e.message); }
}

/* ---------- Vendors / Compare / Samples / Products ---------- */
async function renderHopVendorsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/vendors') || []; hopState.vendors = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Procurement', 'Vendors', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopIsMobileView()
      ? hopRenderMobileContactCards(rows, 'vendors')
      : `${hopRenderDesktopContactToolbar('vendors', rows.length)}
         ${hopTable(
        ['', 'Company', 'Products', 'GST', 'Contact', 'Mobile', 'City', 'Rating', 'Lead Time', 'Payment Terms', 'On-time %', 'Quality', ''],
        rows.map((r) => {
          const state = hopContactSelectState('vendors');
          const checked = state.ids.includes(Number(r.id)) ? ' checked' : '';
          return `<tr class="hop-clickable-row" onclick="hopOpenContactDetail('vendors', ${r.id})" style="cursor:pointer">
          <td onclick="event.stopPropagation()"><input type="checkbox" class="hop-desk-check" value="${r.id}"${checked} onchange="hopToggleContactSelected('vendors', ${r.id}, this.checked)" /></td>
          <td>${hopCell(r.company)}</td><td>${hopCell(r.products)}</td><td>${hopCell(r.gst_no)}</td>
          <td>${hopCell(r.contact_person)}</td><td>${hopCell(r.mobile)}</td><td>${hopCell(r.city)}</td>
          <td>${hopCell(r.rating)}</td><td>${hopCell(r.lead_time_days)}</td><td>${hopCell(r.payment_terms)}</td>
          <td>${hopCell(r.on_time_pct)}</td><td>${hopCell(r.quality_rating)}</td>
          <td><button type="button" class="nx-btn hop-contact-icon-btn hop-contact-icon-del" onclick="event.stopPropagation();hopDeleteContact('vendors', ${r.id}, '${foEscapeAttr(hopContactLabel(r))}')" title="Delete">${hopContactIcon('delete')}</button></td>
        </tr>`}).join(''),
      )}`}`;
  mount.innerHTML = hopModuleShell('Procurement', 'Vendors', 'Supplier performance & terms',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('vendor')">+ New Vendor</button>`, body);
  if (hopIsMobileView()) requestAnimationFrame(() => hopBindMobileContactCards('vendors'));
}

function hopReadRateFiltersFromDom() {
  const f = hopState.rateFilters || (hopState.rateFilters = { q: '' });
  const qEl = document.getElementById('hop-rate-filter-q');
  if (qEl) f.q = String(qEl.value || '').trim();
  return f;
}

function hopResetRateFilters() {
  hopState.rateFilters = { q: '' };
  const qEl = document.getElementById('hop-rate-filter-q');
  if (qEl) qEl.value = '';
  hopApplyRateMatrixFilters();
}

function hopProductMatchesRateFilters(p, filters) {
  const f = filters || hopState.rateFilters || {};
  const q = String(f.q || '').trim().toLowerCase();
  if (!q) return true;
  const blob = `${p.label || ''} ${p.size || ''} ${p.quality_hint || ''} ${p.product_key || ''}`.toLowerCase();
  return blob.includes(q);
}

function hopApplyRateMatrixFilters() {
  const f = hopReadRateFiltersFromDom();
  const rows = document.querySelectorAll('#hop-rate-matrix-body tr[data-product-key]');
  let visible = 0;
  rows.forEach((tr) => {
    const p = {
      product_key: decodeURIComponent(tr.getAttribute('data-product-key') || ''),
      label: tr.getAttribute('data-label') || '',
      size: tr.getAttribute('data-size') || '',
      quality_hint: tr.getAttribute('data-quality') || '',
    };
    const ok = hopProductMatchesRateFilters(p, f);
    tr.classList.toggle('hop-rate-row-hidden', !ok);
    if (ok) visible += 1;
  });

  const countEl = document.getElementById('hop-rate-filter-count');
  if (countEl) {
    const total = rows.length;
    countEl.textContent = !f.q || visible === total ? `${total} products` : `${visible} / ${total}`;
  }

  document.querySelectorAll('#hop-rate-suggest-list li[data-product-key]').forEach((li) => {
    const key = decodeURIComponent(li.getAttribute('data-product-key') || '');
    const product = (hopState.rateMatrix?.products || []).find((x) => x.product_key === key);
    if (!product) {
      li.classList.add('hop-rate-row-hidden');
      return;
    }
    li.classList.toggle('hop-rate-row-hidden', !hopProductMatchesRateFilters(product, f));
  });
}

async function renderHopVendorCmpModule(mount) {
  let matrix = { suppliers: [], products: [], suggestions: [], summary: {} };
  let sheets = [];
  hopLoadRateCart();
  try {
    matrix = await hopApi('/api/v1/hop/rate-compare') || matrix;
    sheets = await hopApi('/api/v1/hop/rate-sheets') || [];
  } catch (e) {
    mount.innerHTML = hopModuleShell('Procurement', 'Vendor Comparison', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  hopState.rateMatrix = matrix;
  if (!hopState.rateFilters) hopState.rateFilters = { q: '' };
  const filters = hopState.rateFilters;
  const suppliers = matrix.suppliers || [];
  const products = matrix.products || [];
  const suggestions = matrix.suggestions || [];
  const summary = matrix.summary || {};
  const cartCount = (hopState.rateCart || []).length;
  const cartKeys = new Set((hopState.rateCart || []).map((c) => c.product_key));

  const headCells = [
    `<input type="checkbox" id="hop-rate-select-all" title="Select all" onchange="hopToggleSelectAllRates(this.checked)" />`,
    'Product',
    ...suppliers.map((s) => hopCell(s.supplier_name)),
    'Best / cart',
    'Item',
  ];
  const bodyRows = products.map((p) => {
    const enc = encodeURIComponent(p.product_key || '');
    const quotedIds = Object.entries(p.offers || {})
      .filter(([, o]) => o && !o.missing && Number(o.rate) > 0)
      .map(([sid]) => sid)
      .join(',');
    const inCart = cartKeys.has(p.product_key);
    const cells = suppliers.map((s) => {
      const offer = (p.offers || {})[String(s.sheet_id)];
      if (!offer || offer.missing) {
        return '<td class="hop-rate-empty"><div class="hop-rate-val">₹0</div><div class="hop-rate-sub">not quoted</div></td>';
      }
      if (offer.rate == null || Number(offer.rate) === 0) {
        return '<td class="hop-rate-empty"><div class="hop-rate-val">₹0</div></td>';
      }
      const isBest = p.best && String(p.best.sheet_id) === String(s.sheet_id);
      const cellInCart = (hopState.rateCart || []).some(
        (c) => c.product_key === p.product_key && String(c.sheet_id) === String(s.sheet_id),
      );
      return `<td class="${isBest ? 'hop-rate-best' : ''}">
        <div class="hop-rate-val">${hopMoney(offer.rate)}</div>
        <div class="hop-rate-sub">+${hopCell(offer.gst_pct)}% → ${hopMoney(offer.landed_rate)}</div>
        <div class="hop-rate-cell-actions">
          <button type="button" class="nx-btn hop-rate-add-btn${cellInCart ? ' is-in-cart' : ''}"
            onclick="hopAddToRateCart('${enc}', ${s.sheet_id})">${cellInCart ? 'Added +' : 'Add to cart'}</button>
          <button type="button" class="nx-btn hop-rate-clear-btn" title="Remove this supplier rate for this item"
            onclick="hopClearSingleRate('${enc}', ${s.sheet_id})">✕</button>
        </div>
      </td>`;
    }).join('');
    let bestCell = '<td>—</td>';
    if (p.best) {
      const tag = (p.supplier_count || 0) >= 2 ? 'cheapest' : 'only this seller';
      bestCell = `<td class="hop-rate-best-col">
        <strong>${hopCell(p.best.supplier_name)}</strong>
        <div class="hop-rate-sub">${hopMoney(p.best.landed_rate)} landed · ${tag}</div>
        <button type="button" class="nx-btn hop-rate-add-btn" onclick="hopAddBestToRateCart('${enc}')">Add best</button>
      </td>`;
    }
    return `<tr data-product-key="${enc}"
      data-label="${foEscapeText(p.label || '')}"
      data-size="${foEscapeText(p.size || '')}"
      data-quality="${foEscapeText(p.quality_hint || '')}"
      data-category="${foEscapeText(p.category || '')}"
      data-scount="${Number(p.supplier_count || 0)}"
      data-quoted="${foEscapeText(quotedIds)}"
      data-in-cart="${inCart ? '1' : '0'}">
      <td><input type="checkbox" class="hop-rate-row-check" value="${enc}" title="Select item" /></td>
      <td><strong>${hopCell(p.label)}</strong>${p.size ? `<div class="hop-rate-sub">${hopCell(p.size)}</div>` : ''}${p.quality_hint ? `<div class="hop-rate-sub">${hopCell(p.quality_hint)}</div>` : ''}</td>
      ${cells}${bestCell}
      <td><button type="button" class="nx-btn hop-rate-clear-btn" title="Delete this item from all suppliers"
        onclick="hopClearSingleProduct('${enc}')">Delete item</button></td>
    </tr>`;
  }).join('');

  const matrixTableHtml = products.length
    ? `<div class="hop-table-wrap"><table class="data-table hop-table hop-rate-matrix-table">
        <thead><tr>${headCells.map((h) => `<th>${h}</th>`).join('')}</tr></thead>
        <tbody id="hop-rate-matrix-body">${bodyRows}</tbody>
      </table></div>`
    : '<p class="nx-text-dim">Matrix empty — upload supplier rates.</p>';

  const suggestHtml = suggestions.length
    ? `<div class="hop-rate-suggest nx-card">
        <h4>Suggestion — cheapest where 2+ sellers quoted</h4>
        <ul id="hop-rate-suggest-list">${suggestions.map((s) =>
          `<li data-product-key="${encodeURIComponent(s.product_key || '')}"><strong>${hopCell(s.label)}</strong> → <strong>${hopCell(s.best_supplier)}</strong> at ${hopMoney(s.best_landed)} landed · ${s.alternatives} sellers compared
            <button type="button" class="nx-btn hop-rate-add-btn" onclick="hopAddBestToRateCart('${encodeURIComponent(s.product_key || '')}')">Add best</button>
          </li>`
        ).join('')}</ul>
      </div>`
    : `<div class="hop-rate-suggest nx-card"><p class="nx-text-dim">Jab same product 2+ suppliers pe milega, yahan cheapest suggestion aayegi.</p></div>`;

  const sheetList = sheets.length
    ? hopTable(
      ['Supplier', 'Source', 'File', 'Lines', 'Notes', ''],
      sheets.map((r) => `<tr>
        <td>${hopCell(r.supplier_name)}</td>
        <td>${hopCell(r.source_type)}</td>
        <td>${hopCell(r.source_filename || '—')}</td>
        <td>${hopCell(r.line_count)}</td>
        <td>${hopCell(r.notes)}</td>
        <td class="hop-rate-cell-actions">
          <button type="button" class="nx-btn hop-rate-clear-btn" title="Vendor-wise: clear all items for this supplier"
            onclick="hopClearSheetRates(${r.id}, '${encodeURIComponent(r.supplier_name || '')}')">Clear vendor</button>
          <button type="button" class="nx-btn" onclick="hopDeleteRateSheet(${r.id})">Delete sheet</button>
        </td>
      </tr>`).join(''),
    )
    : '<p class="nx-text-dim">No rate sheets yet. Upload a supplier file or paste rates.</p>';

  const body = `
    <div class="hop-rate-summary">
      <span>${summary.supplier_count || 0} suppliers</span>
      <span>${summary.product_count || 0} products</span>
      <span>${summary.comparable_count || 0} multi-quote</span>
      <span>${cartCount} in quote cart</span>
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    <div id="hop-rate-cart-panel" class="nx-card hop-rate-cart-card"></div>
    <div class="nx-card hop-rate-matrix-card">
      <div class="hop-rate-matrix-toolbar">
        <h4>Full rate matrix</h4>
        <div class="hop-rate-product-filter">
          <input id="hop-rate-filter-q" class="hop-search hop-rate-filter-q" type="search"
            placeholder="Filter product…"
            value="${foEscapeText(filters.q || '')}"
            oninput="hopApplyRateMatrixFilters()" />
          <span id="hop-rate-filter-count" class="hop-rate-filter-count nx-text-dim">${products.length} products</span>
          <button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopClearSelectedRates()">Delete selected</button>
        </div>
      </div>
      <div class="hop-rate-scroll">
        ${matrixTableHtml}
      </div>
    </div>
    ${suggestHtml}
    <div class="nx-card hop-rate-sheets-card">
      <h4>Supplier rate sheets</h4>
      ${sheetList}
    </div>
    <div class="nx-card hop-rate-legacy-card">
      <h4>Legacy project comparisons</h4>
      <p class="nx-text-dim">Old single-row winner picks (still available).</p>
      <button type="button" class="nx-btn" onclick="hopShowForm('vendor_cmp')">+ Add project comparison row</button>
      <div id="hop-legacy-cmp"></div>
    </div>
  `;

  mount.innerHTML = hopModuleShell(
    'Procurement',
    'Vendor Comparison',
    'Compare → clear unwanted rates → cart → supplier orders',
    `<button type="button" class="nx-btn" onclick="hopClearRateQuote()">Clear quote cart</button>
     <button type="button" class="nx-btn hop-rate-clear-btn" onclick="hopClearAllRates()">Clear all rate data</button>
     <button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('rate_sheet')">+ Upload / add rates</button>`,
    body,
  ).replace('class="hop-view hop-view--fullpage hop-view--module"', 'class="hop-view hop-view--fullpage hop-view--module hop-vendor-cmp-compact"')
   .replace('class="hop-view hop-view--fullpage"', 'class="hop-view hop-view--fullpage hop-vendor-cmp-compact"')
   .replace('class="hop-view"', 'class="hop-view hop-vendor-cmp-compact"');

  hopRenderRateCartPanel();
  hopApplyRateMatrixFilters();

  hopApi('/api/v1/hop/vendor-comparisons').then((rows) => {
    const el = document.getElementById('hop-legacy-cmp');
    if (!el || !rows?.length) return;
    el.innerHTML = hopTable(
      ['Project', 'Product', 'Vendor', 'Rate', 'Winner'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.product_name)}</td>
        <td>${hopCell(r.vendor_company)}</td><td>${hopMoney(r.rate)}</td>
        <td>${r.is_winner ? '★' : '—'}</td>
      </tr>`).join(''),
    );
  }).catch(() => {});
}

async function hopSeedRateSamples() {
  alert('Sample quotes disabled. Upload your supplier files — rates come only from uploads.');
}

async function hopDeleteRateSheet(id) {
  if (!(await nexoraConfirm('Delete this supplier rate sheet?', {
    title: 'Delete rate sheet',
    danger: true,
    okText: 'Delete',
  }))) return;
  try {
    await hopApi(`/api/v1/hop/rate-sheets/${id}`, { method: 'DELETE' });
    openHopView('vendor_cmp');
  } catch (e) {
    alert(e.message || 'Delete failed');
  }
}

function hopSupplierFromFilename(filename) {
  const base = String(filename || '').replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
  let name = base
    .replace(/[_\-+.]+/g, ' ')
    .replace(/\b(20\d{2}|19\d{2})([01]\d)([0-3]\d)(?:[_\s-]?\d{4,6})?\b/g, ' ')
    .replace(/\b(quote|rate|rates|sheet|price|list|supplier|vendor|scan|img|image|photo|copy|final|new)\b/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!name) name = base.replace(/[_\-+.]+/g, ' ').trim() || 'Supplier';
  // Title-case words, keep short ALLCAPS (UMD, GSB)
  return name.replace(/\b([A-Za-z][A-Za-z0-9]*)\b/g, (w) => {
    if (w.length <= 4 && w === w.toUpperCase()) return w.toUpperCase();
    return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  });
}

function hopSourceFromFilename(filename) {
  const ext = String(filename || '').split('.').pop()?.toLowerCase() || '';
  if (ext === 'pdf') return 'pdf';
  if (['xlsx', 'xlsm', 'xls', 'ods', 'csv', 'tsv'].includes(ext)) return 'excel';
  if (['doc', 'docx', 'rtf', 'odt'].includes(ext)) return 'word';
  if (['jpg', 'jpeg', 'png', 'bmp', 'gif', 'webp', 'tif', 'tiff', 'heic', 'heif'].includes(ext)) return 'image';
  if (['txt'].includes(ext)) return 'quote';
  return 'manual';
}

function hopOnRateFileSelected() {
  const fileInput = document.getElementById('f-rs-file');
  const file = fileInput?.files?.[0];
  const status = document.getElementById('f-rs-upload-status');
  if (!file) return;
  const supplierEl = document.getElementById('f-rs-supplier');
  const sourceEl = document.getElementById('f-rs-source');
  const titleEl = document.getElementById('f-rs-title');
  const detectedSupplier = hopSupplierFromFilename(file.name);
  const detectedSource = hopSourceFromFilename(file.name);
  if (supplierEl) {
    supplierEl.value = detectedSupplier;
    supplierEl.dataset.autoFilled = '1';
  }
  if (sourceEl) {
    if ([...sourceEl.options].some((o) => o.value === detectedSource)) {
      sourceEl.value = detectedSource;
    }
    sourceEl.dataset.autoFilled = '1';
  }
  if (titleEl && !titleEl.value.trim()) {
    titleEl.value = `${detectedSupplier} — ${file.name}`;
  }
  if (status) {
    status.textContent = `Detected: supplier “${detectedSupplier}” · source ${detectedSource} (from ${file.name})`;
  }
}

async function hopUploadRateSheet() {
  const status = document.getElementById('f-rs-upload-status');
  const fileInput = document.getElementById('f-rs-file');
  const file = fileInput?.files?.[0];
  if (!file) {
    alert('Choose a file first (PDF, Excel, Word/RTF, JPG/BMP/PNG, CSV…)');
    fileInput?.click();
    return;
  }
  // Always sync from filename when empty / still auto-filled
  hopOnRateFileSelected();
  let supplier = document.getElementById('f-rs-supplier')?.value?.trim();
  if (!supplier) {
    supplier = hopSupplierFromFilename(file.name);
    const el = document.getElementById('f-rs-supplier');
    if (el) el.value = supplier;
  }
  const source = document.getElementById('f-rs-source')?.value || hopSourceFromFilename(file.name);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('supplier_name', supplier);
  fd.append('source_type', source);
  fd.append('title', document.getElementById('f-rs-title')?.value || `${supplier} — ${file.name}`);
  fd.append('notes', document.getElementById('f-rs-notes')?.value || '');
  fd.append('create_sheet', '1');
  if (status) status.textContent = 'Uploading & OCR… (handwriting 15–40 sec lag sakta hai)';
  try {
    const data = await hopApi('/api/v1/hop/rate-sheets/upload', { method: 'POST', body: fd });
    const lines = data?.lines || [];
    const warnings = data?.warnings || [];
    const lineCount = data?.line_count ?? lines.length;
    if (lines.length && document.getElementById('f-rs-lines')) {
      document.getElementById('f-rs-lines').value = lines.map((ln) =>
        [ln.product_name, ln.size || '', ln.rate, ln.gst_pct ?? 5].join(' | ')
      ).join('\n');
    }
    if (data?.source_type && document.getElementById('f-rs-source')) {
      const sel = document.getElementById('f-rs-source');
      if ([...sel.options].some((o) => o.value === data.source_type)) sel.value = data.source_type;
    }
    if (data?.created && lineCount > 0) {
      const msg = `Saved ${lineCount} rates for ${data.sheet?.supplier_name || supplier} (${data.parse_method || source})`;
      if (status) status.textContent = warnings.length ? `${msg}. ${warnings[0]}` : msg;
      alert(msg);
      openHopView('vendor_cmp');
      return;
    }
    if (!lines.length) {
      const detail = (warnings || []).join('\n') || 'No rates detected from this file.';
      if (status) status.textContent = detail;
      alert(`${detail}\n\nPaste lines manually (Product | Size | Rate | GST) then click Save pasted lines.`);
      return;
    }
    // Parsed but not created — keep form filled for manual save
    if (status) status.textContent = `Parsed ${lineCount} lines — click Save pasted lines to store.`;
    alert(`Parsed ${lineCount} lines. Click “Save pasted lines” to add to comparison.`);
  } catch (e) {
    if (status) status.textContent = '';
    alert(e.message || 'Upload failed');
  }
}

async function renderHopSamplesModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/samples') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Samples', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'Samples', value: rows.length, tone: 'unpaid' }])}
    ${hopTable(
      ['Sample', 'Customer', 'Project', 'Sent', 'Courier', 'Tracking', 'Return', 'Approval', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.sample_name)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.sent_at)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.tracking_number)}</td>
        <td>${hopCell(r.return_status)}</td><td>${hopCell(r.approval_status)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
      { label: 'Samples', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Sample Management', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('sample')">+ New Sample</button>`, body);
}

async function renderHopProductsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/products') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Catalogue', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([{ label: 'SKUs', value: rows.length, tone: 'total' }])}
    ${hopTable(
      ['Code', 'Name', 'Brand', 'Category', 'Sell', 'Buy', 'Logistics', 'GST%', 'Comm%', 'Net Profit', 'Margin %', 'Stock', 'Vendor'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.code)}</td><td>${hopCell(r.name)}</td><td>${hopCell(r.brand)}</td><td>${hopCell(r.category)}</td>
        <td class="inv-num">${hopMoney(r.selling_price)}</td><td class="inv-num">${hopMoney(r.purchase_price)}</td><td class="inv-num">${hopMoney(r.logistics_cost)}</td>
        <td>${hopCell(r.gst_pct)}</td><td>${hopCell(r.commission_pct)}</td>
        <td class="inv-num">${hopMoney(r.net_profit)}</td><td>${hopCell(r.margin_pct)}</td><td>${hopCell(r.stock_qty)}</td>
        <td>${hopCell(r.vendor_company)}</td>
      </tr>`).join(''),
      { label: 'Catalogue', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Products', 'Product Catalogue', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('product')">+ New Product</button>`, body);
}

async function renderHopFabricPreviewModule(mount) {
  hopState.fabricPreview = hopState.fabricPreview || {
    demoFabricId: 'demo-linen-sand',
    fabricLabel: 'Linen Sand',
  };
  let bank = { demo_fabrics: [], catalogue: [], engine: 'demo', hint: '' };
  try {
    bank = await hopApi('/api/v1/hop/fabric-preview/fabrics') || bank;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Products', 'Fabric Preview', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  const demos = bank.demo_fabrics || [];
  const demoOptions = demos.map((f) => {
    const sel = f.id === hopState.fabricPreview.demoFabricId ? ' selected' : '';
    return `<option value="${foEscapeText(f.id)}"${sel}>${foEscapeText(f.name)} (${foEscapeText(f.category)})</option>`;
  }).join('');

  const body = `
    <div class="hop-fabric-banner nx-card">
      <strong>DEMO mode</strong> — free partner pitch. Paid AI render unlocks after budget approval.
      <span class="nx-text-dim">Engine: ${foEscapeText(bank.engine || 'demo')} · Same on app, web &amp; desktop</span>
    </div>
    <div class="hop-fabric-grid">
      <div class="nx-card hop-fabric-card">
        <h3>1. Client item (sofa / chair)</h3>
        <p class="nx-text-dim">Camera (phone/webcam) ya Files se photo</p>
        <div class="hop-photo-actions">
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopPickPhoto('hop-fabric-item-cam')">Camera</button>
          <button type="button" class="nx-btn" onclick="hopPickPhoto('hop-fabric-item-gal')">Files / Gallery</button>
        </div>
        <input id="hop-fabric-item-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-fabric-item-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-fabric-item-preview" class="hop-fabric-thumb nx-text-dim">No photo yet</div>
      </div>
      <div class="nx-card hop-fabric-card">
        <h3>2. Fabric</h3>
        <label class="hop-fabric-label">Demo bank (ready now)</label>
        <select id="hop-fabric-demo-select" onchange="hopFabricDemoChanged()">
          ${demoOptions}
        </select>
        <label class="hop-fabric-label">Ya fabric photo</label>
        <div class="hop-photo-actions">
          <button type="button" class="nx-btn nx-btn-primary" onclick="hopPickPhoto('hop-fabric-swatch-cam')">Camera</button>
          <button type="button" class="nx-btn" onclick="hopPickPhoto('hop-fabric-swatch-gal')">Files / Gallery</button>
        </div>
        <input id="hop-fabric-swatch-cam" class="hop-file-hidden" type="file" accept="image/*" capture="environment" />
        <input id="hop-fabric-swatch-gal" class="hop-file-hidden" type="file" accept="image/*" />
        <div id="hop-fabric-swatch-preview" class="hop-fabric-thumb nx-text-dim">Optional fabric photo</div>
        <p class="nx-text-dim hop-fabric-hint">${foEscapeText(bank.hint || '')}</p>
      </div>
    </div>
    <div class="hop-fabric-actions">
      <button type="button" class="nx-btn nx-btn-primary" id="hop-fabric-render-btn" onclick="hopRunFabricPreview()">Render preview</button>
    </div>
    <p id="hop-fabric-status" class="nx-text-dim"></p>
    <div id="hop-fabric-result" class="hop-fabric-result hidden"></div>
  `;

  mount.innerHTML = hopModuleShell(
    'Field Sales',
    'Fabric Preview',
    'Sofa photo + fabric → instant demo render for the client',
    '',
    body,
  );

  const wireFabricFile = (camId, galId, previewId, storeKey) => {
    const onPick = (input) => {
      const file = input.files && input.files[0];
      const box = document.getElementById(previewId);
      if (!file) return;
      hopState.fabricPreview = hopState.fabricPreview || {};
      hopState.fabricPreview[storeKey] = file;
      const other = document.getElementById(input.id === camId ? galId : camId);
      if (other) other.value = '';
      if (box) {
        const url = URL.createObjectURL(file);
        box.innerHTML = `<img src="${url}" alt="Preview" />`;
      }
    };
    document.getElementById(camId)?.addEventListener('change', (e) => onPick(e.target));
    document.getElementById(galId)?.addEventListener('change', (e) => onPick(e.target));
  };
  wireFabricFile('hop-fabric-item-cam', 'hop-fabric-item-gal', 'hop-fabric-item-preview', 'itemFile');
  wireFabricFile('hop-fabric-swatch-cam', 'hop-fabric-swatch-gal', 'hop-fabric-swatch-preview', 'fabricFile');
}

function hopFabricDemoChanged() {
  const sel = document.getElementById('hop-fabric-demo-select');
  if (!sel) return;
  hopState.fabricPreview = hopState.fabricPreview || {};
  hopState.fabricPreview.demoFabricId = sel.value;
  hopState.fabricPreview.fabricLabel = sel.options[sel.selectedIndex]?.text || sel.value;
}

async function hopRunFabricPreview() {
  const status = document.getElementById('hop-fabric-status');
  const result = document.getElementById('hop-fabric-result');
  const btn = document.getElementById('hop-fabric-render-btn');
  const demoSel = document.getElementById('hop-fabric-demo-select');
  const itemFile = hopState.fabricPreview?.itemFile
    || document.getElementById('hop-fabric-item-cam')?.files?.[0]
    || document.getElementById('hop-fabric-item-gal')?.files?.[0];
  if (!itemFile) {
    if (status) status.textContent = 'Pehle sofa / chair ki photo lo (Camera ya Gallery).';
    return;
  }

  const form = new FormData();
  form.append('item_image', itemFile);
  const fabricFile = hopState.fabricPreview?.fabricFile
    || document.getElementById('hop-fabric-swatch-cam')?.files?.[0]
    || document.getElementById('hop-fabric-swatch-gal')?.files?.[0];
  if (fabricFile) {
    form.append('fabric_image', fabricFile);
    form.append('fabric_label', fabricFile.name || 'Fabric photo');
  } else if (demoSel?.value) {
    form.append('demo_fabric_id', demoSel.value);
    form.append('fabric_label', demoSel.options[demoSel.selectedIndex]?.text || demoSel.value);
  } else {
    if (status) status.textContent = 'Demo fabric choose karo ya fabric photo lo.';
    return;
  }

  if (status) status.textContent = 'Rendering demo preview…';
  if (result) {
    result.classList.add('hidden');
    result.innerHTML = '';
  }
  if (btn) btn.disabled = true;

  try {
    const response = await fetchWithAuth('/api/v1/hop/fabric-preview/render', {
      method: 'POST',
      body: form,
    });
    const data = await parseApiJson(response);
    if (!response.ok || !data.success) {
      throw new Error(getApiErrorMessage(data, 'Render failed'));
    }
    const payload = data.data || {};
    const src = `data:${payload.mime || 'image/jpeg'};base64,${payload.image_base64}`;
    if (status) {
      status.textContent = payload.note || 'Done';
    }
    if (result) {
      result.classList.remove('hidden');
      result.innerHTML = `
        <div class="nx-card hop-fabric-result-card">
          <div class="hop-fabric-result-meta">
            <span>${foEscapeText(payload.fabric_label || 'Fabric')}</span>
            <span class="nx-text-dim">${foEscapeText(payload.engine || 'demo')} · ${foEscapeText(payload.fabric_source || '')}</span>
          </div>
          <img src="${src}" alt="Fabric preview result" />
          <p class="nx-text-dim">Client ko phone pe dikhao. Paid AI baad me same button se unlock hoga.</p>
        </div>`;
      requestAnimationFrame(() => hopScrollIntoMain(result, 8));
    }
  } catch (e) {
    if (status) status.textContent = e.message || 'Render failed';
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------- Orders / Dispatch / Invoices / Payments / Complaints ---------- */
async function renderHopOrdersModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/orders') || []; hopState.orders = rows; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Orders', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const totalVal = rows.reduce((s, r) => s + Number(r.order_value || 0), 0);
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Orders', value: rows.length, tone: 'unpaid' },
      { label: 'Total Value', valueHtml: hopMoney(totalVal), tone: 'total', op: '=' },
    ])}
    ${hopTable(
      ['PO No', 'Client', 'Project', 'Value', 'Supplier', 'Expected Delivery', 'Production', 'Dispatch', 'Invoice', 'Won At', ''],
      rows.map((r) => `<tr>
        <td>${hopCell(r.po_number)}</td><td>${hopCell(r.client_name || r.customer_company)}</td>
        <td>${hopCell(r.project_name)}</td><td class="inv-num">${hopMoney(r.order_value)}</td><td>${hopCell(r.supplier || r.vendor_company)}</td>
        <td>${hopCell(r.expected_delivery)}</td>
        <td><select onchange="hopPatchOrder(${r.id}, 'production_status', this.value)">
          ${['pending','ordered','in_production','qc','packed','ready','completed','delayed'].map((s) => `<option value="${s}"${s === r.production_status ? ' selected' : ''}>${s}</option>`).join('')}
        </select></td>
        <td>${hopCell(r.dispatch_status)}</td><td>${hopCell(r.invoice_status)}</td><td>${hopCell((r.won_at || '').slice(0, 10))}</td>
        <td>${r.project_id ? `<button type="button" class="nx-btn" onclick="openHopProjectHub(${r.project_id})">Hub</button>` : '—'}</td>
      </tr>`).join(''),
      { label: 'Orders / PO', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Sales Ops', 'Orders / PO', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('order')">+ New Order</button>`, body);
}

async function hopPatchOrder(id, field, value) {
  try {
    await hopApi(`/api/v1/hop/orders/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [field]: value }) });
    openHopView('orders');
  } catch (e) { alert(e.message); }
}

async function renderHopDispatchesModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/dispatches') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Dispatch', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const pending = rows.filter((r) => !r.delivered_at && String(r.delivery_status || '').toLowerCase() !== 'delivered').length;
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Dispatches', value: rows.length, tone: 'unpaid' },
      { label: 'In Transit / Open', value: pending, tone: 'overdue', op: '+' },
    ])}
    ${hopTable(
      ['Project', 'PO', 'Status', 'Tracking', 'Courier', 'Delivery', 'Dispatched', 'Delivered', 'E-way', 'Docket', 'POD', 'Install'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.project_name)}</td><td>${hopCell(r.po_number)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.delivery_status)}</td>
        <td>${hopCell(r.dispatched_at)}</td><td>${hopCell(r.delivered_at)}</td>
        <td>${hopCell(r.eway_bill)}</td><td>${hopCell(r.docket_number)}</td>
        <td>${r.pod_received ? 'Yes' : '—'}</td><td>${r.installation_pending ? 'Pending' : '—'}</td>
      </tr>`).join(''),
      { label: 'Dispatch', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Dispatch', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('dispatch')">+ New Dispatch</button>`, body);
}

function hopDateYmd(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function hopInvoicePeriodRange(period) {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  if (period === 'all') return { from: '', to: '' };
  if (period === 'today') {
    const t = hopDateYmd(now);
    return { from: t, to: t };
  }
  if (period === 'this_week') {
    const day = (now.getDay() + 6) % 7; // Mon=0
    const start = new Date(y, m, now.getDate() - day);
    return { from: hopDateYmd(start), to: hopDateYmd(now) };
  }
  if (period === 'last_month') {
    const start = new Date(y, m - 1, 1);
    const end = new Date(y, m, 0);
    return { from: hopDateYmd(start), to: hopDateYmd(end) };
  }
  if (period === 'this_quarter') {
    const q = Math.floor(m / 3) * 3;
    return { from: hopDateYmd(new Date(y, q, 1)), to: hopDateYmd(now) };
  }
  if (period === 'this_year') {
    return { from: `${y}-01-01`, to: hopDateYmd(now) };
  }
  // this_month (default)
  return { from: hopDateYmd(new Date(y, m, 1)), to: hopDateYmd(new Date(y, m + 1, 0)) };
}

function hopIsPaymentVoucherKind(kind) {
  const k = String(kind || hopState.saleDocKind || hopState.view || '').trim();
  // Payment-In (Sale) + Payment-Out (Purchase) — Vyapar: Used / Unused, not Paid / Unpaid.
  return k === 'purchase_payment_out'
    || k === 'sale_payment_in'
    || k === 'payments';
}

function hopInvoiceEffectiveStatus(r) {
  if (hopTxnIsNonReceivableType(r.txn_type) || hopSaleDocIsNonReceivable()) {
    // Proforma / Estimate / SO / Challan — payment is against Sale Invoice, not these docs.
    return '';
  }
  const bal = Number(r.balance || 0);
  const paid = Number(r.paid_amount || 0);
  const amount = Number(r.amount || 0);
  const due = String(r.due_date || '').slice(0, 10);
  const today = hopDateYmd(new Date());
  // Payment-In / Payment-Out: Used = fully applied, Unused = balance left (Vyapar wording).
  if (hopIsPaymentVoucherKind() || HOP_TXN_PAYMENT_IN.has(Number(r.txn_type)) || HOP_TXN_PAYMENT_OUT.has(Number(r.txn_type))) {
    if (bal <= 0.009) return 'paid'; // Used
    if (paid > 0 && bal > 0) return 'partial';
    return 'unpaid'; // Unused
  }
  if (bal <= 0.009 || String(r.status || '').toLowerCase() === 'paid') return 'paid';
  if (due && due < today && bal > 0) return 'overdue';
  if (paid > 0 && bal > 0) return 'partial';
  if (amount > 0 && paid <= 0) return 'unpaid';
  return String(r.status || 'open').toLowerCase() || 'open';
}

function hopInvoiceStatusBadge(status) {
  const s = String(status || '').toLowerCase().trim();
  if (!s) {
    return `<span class="inv-badge inv-badge--na">—</span>`;
  }
  let label;
  if (hopIsPaymentVoucherKind()) {
    if (s === 'paid') label = 'Used';
    else if (s === 'unpaid' || s === 'open') label = 'Unused';
    else if (s === 'partial') label = 'Partial';
    else label = hopScrubVyaparStatusLabel(s.charAt(0).toUpperCase() + s.slice(1));
  } else {
    if (s === 'partial') label = 'Partial';
    else if (s === 'unpaid') label = 'Unpaid';
    else if (s === 'final' || s === 'approved' || s === 'approve') label = '—';
    else label = hopScrubVyaparStatusLabel(s.charAt(0).toUpperCase() + s.slice(1));
  }
  if (!label || label === '—' || /^final$/i.test(label)) {
    return `<span class="inv-badge inv-badge--na">—</span>`;
  }
  const badgeClass = (s === 'approved' || s === 'approve' || s === 'final') ? 'na' : s;
  return `<span class="inv-badge inv-badge--${foEscapeAttr(badgeClass)}">${foEscapeText(label)}</span>`;
}

function hopFilteredInvoices() {
  const ui = hopState.invoiceUi || {};
  const q = String(ui.q || '').trim().toLowerCase();
  const party = String(ui.party || '').trim().toLowerCase();
  const status = String(ui.status || 'all');
  const from = String(ui.from || '').slice(0, 10);
  const to = String(ui.to || '').slice(0, 10);
  return (hopState.invoices || []).filter((r) => {
    const d = String(r.invoice_date || r.created_at || '').slice(0, 10);
    if (from && d && d < from) return false;
    if (to && d && d > to) return false;
    if (!d && (from || to) && ui.period !== 'all') return false;
    const eff = hopInvoiceEffectiveStatus(r);
    if (status !== 'all') {
      if (status === 'open' && !['open', 'unpaid', 'partial', 'overdue'].includes(eff)) return false;
      else if (status !== 'open' && eff !== status) return false;
    }
    if (party) {
      const name = String(r.customer_company || r.party_name || r.client_name || '').toLowerCase();
      if (!name.includes(party)) return false;
    }
    if (q) {
      const hay = [
        r.invoice_no, r.customer_company, r.project_name, r.status, r.notes, r.due_date,
      ].map((x) => String(x || '').toLowerCase()).join(' ');
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function hopInvoiceSummary(rows) {
  let total = 0;
  let received = 0;
  let balance = 0;
  let overdue = 0;
  rows.forEach((r) => {
    total += Number(r.amount || 0);
    received += Number(r.paid_amount || 0);
    const bal = Number(r.balance || 0);
    balance += bal;
    if (hopInvoiceEffectiveStatus(r) === 'overdue') overdue += bal;
  });
  const unpaid = Math.max(0, balance - overdue);
  return { total, received, balance, overdue, unpaid, count: rows.length };
}

function hopCloseInvRowContextMenu() {
  document.getElementById('hop-inv-ctx-menu')?.remove();
  document.removeEventListener('keydown', hopInvCtxEscHandler);
}

function hopInvCtxEscHandler(e) {
  if (e.key === 'Escape') hopCloseInvRowContextMenu();
}

function hopShowInvRowContextMenu(e, opts) {
  e.preventDefault();
  e.stopPropagation();
  hopCloseInvRowContextMenu();
  const partyTxnId = Number(opts?.partyTxnId || 0);
  const sourceTxnId = Number(opts?.sourceTxnId || 0);
  const canEdit = !!opts?.canEdit;
  const canPreview = !!opts?.canPreview;
  if (!canPreview && !canEdit) return;

  const menu = document.createElement('div');
  menu.id = 'hop-inv-ctx-menu';
  menu.className = 'hop-inv-ctx-menu';
  menu.setAttribute('role', 'menu');

  const label = opts?.label || 'Document';
  let items = `<div class="hop-inv-ctx-head">${foEscapeText(label)}</div>`;
  if (canPreview) {
    items += `<button type="button" class="hop-inv-ctx-item" role="menuitem"
      onclick="hopCloseInvRowContextMenu();hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId});">
      <span class="hop-inv-ctx-ico">👁</span> View
    </button>`;
  }
  if (canEdit) {
    items += `<button type="button" class="hop-inv-ctx-item" role="menuitem"
      onclick="hopCloseInvRowContextMenu();hopOpenManualDocEdit(${partyTxnId});">
      <span class="hop-inv-ctx-ico">✎</span> Edit
    </button>`;
    items += `<button type="button" class="hop-inv-ctx-item" role="menuitem"
      onclick="hopCloseInvRowContextMenu();hopOpenManualDocDuplicate(${partyTxnId});">
      <span class="hop-inv-ctx-ico">⧉</span> Duplicate
    </button>`;
    items += `<button type="button" class="hop-inv-ctx-item hop-inv-ctx-danger" role="menuitem"
      onclick="hopCloseInvRowContextMenu();hopDeleteManualDoc(${partyTxnId});">
      <span class="hop-inv-ctx-ico">🗑</span> Delete
    </button>`;
  }
  menu.innerHTML = items;
  document.body.appendChild(menu);

  const rect = menu.getBoundingClientRect();
  let left = e.clientX;
  let top = e.clientY;
  if (left + rect.width > window.innerWidth - 8) left = window.innerWidth - rect.width - 8;
  if (top + rect.height > window.innerHeight - 8) top = window.innerHeight - rect.height - 8;
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;

  document.addEventListener('keydown', hopInvCtxEscHandler);
  setTimeout(() => {
    document.addEventListener('click', hopCloseInvRowContextMenu, { once: true });
    document.addEventListener('contextmenu', hopCloseInvRowContextMenu, { once: true });
  }, 0);
}

function hopRenderInvoiceRows(rows) {
  const meta = hopSaleDocMeta();
  const empty = meta.empty || 'No invoices in this filter.';
  const isNonReceivable = hopSaleDocIsNonReceivable();
  if (!rows.length) {
    const total = (hopState.invoices || []).length;
    const tip = total > 0
      ? `${empty} (${total} total outside this date/status filter — switch period to All Time.)`
      : empty;
    return `<tr><td colspan="8" class="inv-empty">${foEscapeText(tip)}</td></tr>`;
  }
  return rows.map((r) => {
    const eff = hopInvoiceEffectiveStatus(r);
    const party = r.customer_company || r.party_name || r.client_name || '';
    const partyTxnId = Number(r.party_txn_id || 0) || 0;
    const sourceTxnId = Number(r.source_txn_id || 0) || 0;
    const txnTypeNum = Number(r.txn_type || 0);
    const canEdit = partyTxnId > 0 && sourceTxnId < 0 && (txnTypeNum === 27 || txnTypeNum === 83);
    const canPreview = partyTxnId > 0 || sourceTxnId > 0;
    const ctxMenu = (canPreview || canEdit)
      ? ` oncontextmenu="hopShowInvRowContextMenu(event,{partyTxnId:${partyTxnId},sourceTxnId:${sourceTxnId},canEdit:${canEdit},canPreview:${canPreview},label:${JSON.stringify(party)}})"`
      : '';
    const click = canPreview
      ? ` class="inv-row is-clickable" role="button" tabindex="0" title="Click to preview · Right-click for actions"
         onclick="hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId})"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId});}"${ctxMenu}`
      : ` class="inv-row"${ctxMenu}`;
    const balCell = isNonReceivable ? '—' : hopMoney(r.balance);
    const dueCell = isNonReceivable ? '—' : hopCell(r.due_date);
    const payBtn = isNonReceivable
      ? ''
      : `<button type="button" class="inv-ico-btn" title="Record payment" onclick="hopShowForm('payment')">₹</button>`;
    return `<tr${click}>
      <td class="inv-date">${hopCell(String(r.invoice_date || '').slice(0, 10))}</td>
      <td class="inv-no" title="${foEscapeAttr(hopFormatDocNo(r.invoice_no, r.invoice_date, r.txn_type) || '')}">${hopCell(hopFormatDocNo(r.invoice_no, r.invoice_date, r.txn_type))}</td>
      <td>${hopCell(party)}</td>
      <td class="inv-num">${hopMoney(r.amount)}</td>
      <td class="inv-num">${balCell}</td>
      <td>${dueCell}</td>
      <td>${hopInvoiceStatusBadge(eff)}</td>
      <td class="inv-actions" onclick="event.stopPropagation()">
        ${canPreview ? `<button type="button" class="inv-ico-btn" title="Preview" onclick="hopOpenSaleDocPreview(${partyTxnId}, ${sourceTxnId})">👁</button>` : ''}
        ${canEdit ? `<button type="button" class="inv-ico-btn" title="Duplicate" onclick="hopOpenManualDocDuplicate(${partyTxnId})">⧉</button>` : ''}
        ${canEdit ? `<button type="button" class="inv-ico-btn" title="Edit" onclick="hopOpenManualDocEdit(${partyTxnId})">✎</button>` : ''}
        ${canEdit ? `<button type="button" class="inv-ico-btn" title="Delete" onclick="hopDeleteManualDoc(${partyTxnId})">🗑</button>` : ''}
        ${payBtn}
      </td>
    </tr>`;
  }).join('');
}

async function hopDeleteManualDoc(partyTxnId) {
  const id = Number(partyTxnId || 0);
  if (!id) return;
  let ok = false;
  if (typeof nexoraConfirm === 'function') {
    ok = await nexoraConfirm(
      'Delete this quotation / proforma permanently?\n\nThis cannot be undone.',
      {
        title: 'Delete document?',
        okText: 'Yes, delete',
        cancelText: 'No',
        danger: true,
      },
    );
  } else {
    ok = !!window.confirm('Delete this quotation / proforma permanently?');
  }
  if (!ok) return;
  hopApi(`/api/v1/hop/party-transactions/${id}`, { method: 'DELETE' })
    .then(() => {
      if (typeof nexoraToast === 'function') nexoraToast('Document deleted', 'ok');
      const view = hopState.view || 'sale_estimates';
      openHopView(view);
    })
    .catch((e) => {
      if (typeof nexoraToast === 'function') nexoraToast(e?.message || 'Delete failed', 'err');
      else alert(e?.message || 'Delete failed');
    });
}

function hopRefreshInvoiceUi() {
  const rows = hopFilteredInvoices();
  const sum = hopInvoiceSummary(rows);
  const tbody = document.getElementById('inv-tbody');
  if (tbody) tbody.innerHTML = hopRenderInvoiceRows(rows);
  const setTxt = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  setTxt('inv-sum-paid', hopMoney(sum.received));
  setTxt('inv-sum-unpaid', hopMoney(sum.unpaid));
  setTxt('inv-sum-overdue', hopMoney(sum.overdue));
  setTxt('inv-sum-total', hopMoney(sum.total));
  setTxt('inv-sum-count', `${sum.count} txns`);
}

function hopInvoiceOnPeriodChange() {
  const period = document.getElementById('inv-period')?.value || 'this_month';
  hopState.invoiceUi.period = period;
  if (period !== 'custom') {
    const range = hopInvoicePeriodRange(period);
    hopState.invoiceUi.from = range.from;
    hopState.invoiceUi.to = range.to;
    const fromEl = document.getElementById('inv-from');
    const toEl = document.getElementById('inv-to');
    if (fromEl) fromEl.value = range.from;
    if (toEl) toEl.value = range.to;
  }
  hopRefreshInvoiceUi();
}

function hopInvoiceApplyFilters() {
  const ui = hopState.invoiceUi;
  ui.period = document.getElementById('inv-period')?.value || ui.period;
  ui.from = document.getElementById('inv-from')?.value || '';
  ui.to = document.getElementById('inv-to')?.value || '';
  ui.status = document.getElementById('inv-status')?.value || 'all';
  // Party filter uses selected value (exact), not the search box text.
  ui.party = hopState.invoiceUi.party || '';
  ui.q = document.getElementById('inv-search')?.value || '';
  if (ui.from || ui.to) {
    const preset = hopInvoicePeriodRange(ui.period);
    if (ui.period !== 'custom' && (ui.from !== preset.from || ui.to !== preset.to)) {
      ui.period = 'custom';
      const periodEl = document.getElementById('inv-period');
      if (periodEl) periodEl.value = 'custom';
    }
  }
  hopRefreshInvoiceUi();
}

function hopInvoicePartyLabelSync() {
  const label = document.getElementById('inv-party-label');
  if (!label) return;
  const party = String(hopState.invoiceUi?.party || '').trim();
  label.textContent = party || 'All Parties';
  label.classList.toggle('is-placeholder', !party);
}

function hopInvoicePartyCloseMenu() {
  const panel = document.getElementById('inv-party-panel');
  if (panel) panel.classList.add('hidden');
  const toggle = document.getElementById('inv-party-toggle');
  if (toggle) {
    toggle.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
  }
}

function hopInvoicePartyToggle(ev) {
  ev?.preventDefault?.();
  ev?.stopPropagation?.();
  const panel = document.getElementById('inv-party-panel');
  if (!panel) return;
  if (panel.classList.contains('hidden')) hopInvoicePartyOpenMenu();
  else hopInvoicePartyCloseMenu();
}

function hopInvoicePartyOpenMenu() {
  const panel = document.getElementById('inv-party-panel');
  const toggle = document.getElementById('inv-party-toggle');
  const search = document.getElementById('inv-party-q');
  if (!panel || !toggle) return;
  // Escape overflow:hidden ancestors so the dropdown is visible.
  if (panel.parentElement !== document.body) document.body.appendChild(panel);
  const rect = toggle.getBoundingClientRect();
  const width = Math.max(rect.width, 280);
  let left = rect.left;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
  panel.style.position = 'fixed';
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(rect.bottom + 4)}px`;
  panel.style.width = `${Math.round(width)}px`;
  panel.style.zIndex = '5000';
  if (search) search.value = '';
  hopInvoicePartyRenderMenu('');
  panel.classList.remove('hidden');
  toggle.classList.add('is-open');
  toggle.setAttribute('aria-expanded', 'true');
  requestAnimationFrame(() => search?.focus());
}

function hopInvoicePartyRenderMenu(query) {
  const menu = document.getElementById('inv-party-menu');
  if (!menu) return;
  const q = String(query || '').trim().toLowerCase();
  const all = hopState.invoicePartyNames || [];
  const selected = String(hopState.invoiceUi?.party || '').trim();
  const matches = q ? all.filter((p) => p.toLowerCase().includes(q)) : all;
  const rows = [];
  rows.push(`<button type="button" class="inv-party-opt${!selected ? ' is-active' : ''}" data-party="" onclick="hopInvoicePartySelect('')">All Parties</button>`);
  matches.slice(0, 100).forEach((p) => {
    const active = selected === p ? ' is-active' : '';
    rows.push(`<button type="button" class="inv-party-opt${active}" data-party="${foEscapeAttr(p)}" onclick="hopInvoicePartySelect(this.getAttribute('data-party') || '')">${foEscapeText(p)}</button>`);
  });
  if (!matches.length && q) {
    rows.push(`<div class="inv-party-empty">No party match</div>`);
  }
  menu.innerHTML = rows.join('');
}

function hopInvoicePartyOnInput() {
  const q = document.getElementById('inv-party-q')?.value || '';
  hopInvoicePartyRenderMenu(q);
  const panel = document.getElementById('inv-party-panel');
  if (panel?.classList.contains('hidden')) hopInvoicePartyOpenMenu();
}

function hopInvoicePartySelect(name) {
  hopState.invoiceUi.party = name || '';
  hopInvoicePartyLabelSync();
  hopInvoicePartyCloseMenu();
  hopInvoiceApplyFilters();
}

function hopInvoiceResetFilters() {
  hopState.invoiceUi = { period: 'this_month', from: '', to: '', status: 'all', q: '', party: '' };
  const range = hopInvoicePeriodRange('this_month');
  hopState.invoiceUi.from = range.from;
  hopState.invoiceUi.to = range.to;
  const periodEl = document.getElementById('inv-period');
  const fromEl = document.getElementById('inv-from');
  const toEl = document.getElementById('inv-to');
  const statusEl = document.getElementById('inv-status');
  const searchEl = document.getElementById('inv-search');
  const partyQ = document.getElementById('inv-party-q');
  if (periodEl) periodEl.value = 'this_month';
  if (fromEl) fromEl.value = range.from;
  if (toEl) toEl.value = range.to;
  if (statusEl) statusEl.value = 'all';
  if (searchEl) searchEl.value = '';
  if (partyQ) partyQ.value = '';
  hopInvoicePartyLabelSync();
  hopInvoicePartyCloseMenu();
  hopRefreshInvoiceUi();
}

function hopInvoiceExportCsv() {
  const rows = hopFilteredInvoices();
  const meta = hopSaleDocMeta();
  const header = ['Date', meta.numberLabel || 'Number', 'Party', 'Project', 'Amount', 'Paid', 'Balance', 'Due', 'Status'];
  const lines = [header.join(',')];
  rows.forEach((r) => {
    const cells = [
      r.invoice_date, r.invoice_no, r.customer_company, r.project_name,
      r.amount, r.paid_amount, r.balance, r.due_date, hopInvoiceEffectiveStatus(r),
    ].map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`);
    lines.push(cells.join(','));
  });
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  const slug = String(meta.title || 'sale-docs').toLowerCase().replace(/[^a-z0-9]+/g, '-');
  a.download = `hop-${slug}-${hopDateYmd(new Date())}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function renderHopInvoicesModule(mount) {
  return renderHopSaleDocListModule(mount, 'invoices');
}

async function renderHopPaymentsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/payments') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Ops', 'Payments', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const total = rows.reduce((s, r) => s + Number(r.amount || 0), 0);
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Payments', value: rows.length, tone: 'unpaid' },
      { label: 'Collected', valueHtml: hopMoney(total), tone: 'paid', op: '=' },
    ])}
    ${hopTable(
      ['Paid At', 'Invoice', 'Customer', 'Project', 'Amount', 'Method', 'Notes'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.paid_at || '').slice(0, 16))}</td><td>${hopCell(r.invoice_no)}</td>
        <td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td class="inv-num">${hopMoney(r.amount)}</td><td>${hopCell(r.method)}</td><td>${hopCell(r.notes)}</td>
      </tr>`).join(''),
      { label: 'Payment-In', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Ops', 'Payments', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('payment')">+ Record Payment</button>`, body);
}

async function renderHopComplaintsModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/complaints') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Support', 'Complaints', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const open = rows.filter((r) => !/closed|resolved|done/i.test(String(r.status || ''))).length;
  const body = `
    <div id="hop-form-slot" class="nx-card hop-form-card hidden"></div>
    ${hopTxCards([
      { label: 'Total', value: rows.length, tone: 'total' },
      { label: 'Open', value: open, tone: 'overdue', op: '+' },
    ])}
    ${hopTable(
      ['Date', 'Customer', 'Project', 'Issue', 'Assigned', 'Status', 'Resolution Hrs', 'Feedback'],
      rows.map((r) => `<tr>
        <td>${hopCell(r.complaint_date)}</td><td>${hopCell(r.customer_company)}</td><td>${hopCell(r.project_name)}</td>
        <td>${hopCell(r.issue)}</td><td>${hopCell(r.assigned_to)}</td><td>${hopCell(r.status)}</td>
        <td>${hopCell(r.resolution_time_hours)}</td><td>${hopCell(r.feedback)}</td>
      </tr>`).join(''),
      { label: 'Complaints', count: rows.length },
    )}`;
  mount.innerHTML = hopModuleShell('Support', 'Complaints & After Sales', '',
    `<button type="button" class="nx-btn nx-btn-primary" onclick="hopShowForm('complaint')">+ New Complaint</button>`, body);
}

/* ---------- Reports ---------- */
async function renderHopPipelineModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/lead_pipeline') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Lead Pipeline', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const k = data.kpis || {};
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Conversion %', k.conversion_rate_pct, null)}
      ${renderHopKpiCard('Win Ratio %', k.win_ratio_pct, null)}
      ${renderHopKpiCard('Open Value', k.total_open_value, null)}
      ${renderHopKpiCard('Avg Sales Cycle', k.average_sales_cycle_days, null)}
    </div>
    ${hopTable(['Stage', 'Count', 'Value'],
      (data.stages || []).map((r) => `<tr><td>${hopCell(r.stage)}</td><td>${hopCell(r.count)}</td><td>${hopMoney(r.value)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Lead Pipeline', 'Stage × count × value', '', body);
}

async function renderHopFunnelModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/reports/funnel') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Sales Funnel', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <p class="nx-text-dim">Every project sits in exactly one funnel stage.</p>
    <div class="hop-funnel">
      ${rows.map((r) => `
        <div class="hop-funnel-step">
          <div class="hop-funnel-label">${foEscapeText(r.stage)}</div>
          <div class="hop-funnel-count">${foEscapeText(String(r.count))}</div>
          <div class="hop-funnel-value">${hopMoney(r.value)}</div>
        </div>`).join('<div class="hop-funnel-arrow">↓</div>')}
    </div>
    ${hopTable(['Stage', 'Projects', 'Value'],
      rows.map((r) => `<tr><td>${hopCell(r.stage)}</td><td>${hopCell(r.count)}</td><td>${hopMoney(r.value)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Sales Funnel', 'Lead → … → Payment', '', body);
}

async function hopCommissionEnsureParties() {
  try {
    if (!Array.isArray(hopState.customers) || !hopState.customers.length) {
      hopState.customers = await hopApi('/api/v1/hop/customers') || [];
    }
  } catch (_) { hopState.customers = hopState.customers || []; }
  try {
    if (!Array.isArray(hopState.vendors) || !hopState.vendors.length) {
      hopState.vendors = await hopApi('/api/v1/hop/vendors') || [];
    }
  } catch (_) { hopState.vendors = hopState.vendors || []; }
  return [
    ...(hopState.customers || []).map((c) => ({ ...c, _type: 'customer' })),
    ...(hopState.vendors || []).map((v) => ({ ...v, _type: 'vendor' })),
  ].sort((a, b) => String(a.company || '').localeCompare(String(b.company || '')));
}

function hopCommissionPartyKey(type, id) {
  if (!type || !id) return '';
  return `${type}:${id}`;
}

function hopCommissionPartyOptionsHtml(parties, selectedKey) {
  const opts = [`<option value="">— Select party —</option>`];
  for (const p of parties) {
    const key = hopCommissionPartyKey(p._type, p.id);
    const label = `${p.company || p.contact_person || `ID ${p.id}`}${p._type === 'vendor' ? ' (Vendor)' : ''}`;
    opts.push(`<option value="${foEscapeAttr(key)}"${key === selectedKey ? ' selected' : ''}>${foEscapeText(label)}</option>`);
  }
  return opts.join('');
}

function hopCommissionViewToggleHtml(ui) {
  return `
    <div class="hop-comm-view-toggle" role="tablist">
      <button type="button" class="hop-comm-view-btn${ui.view === 'invoices' ? ' is-active' : ''}"
        onclick="hopCommissionSetView('invoices')">Invoices</button>
      <button type="button" class="hop-comm-view-btn${ui.view === 'records' ? ' is-active' : ''}"
        onclick="hopCommissionSetView('records')">Records</button>
      <button type="button" class="hop-comm-view-btn${ui.view === 'by_person' ? ' is-active' : ''}"
        onclick="hopCommissionSetView('by_person')">By person</button>
    </div>`;
}

async function renderHopCommissionModule(mount) {
  if (!hopState.commissionUi) {
    hopState.commissionUi = {
      q: '', selectedId: null, sheet: null, filter: 'all', view: 'invoices',
      dateFrom: '', dateTo: '', agentKey: '', period: 'all', paymentStatus: '',
    };
  }
  const ui = hopState.commissionUi;
  if (!ui.view) ui.view = 'invoices';
  if (ui.dateFrom == null) ui.dateFrom = '';
  if (ui.dateTo == null) ui.dateTo = '';
  if (ui.agentKey == null) ui.agentKey = '';
  if (ui.paymentStatus == null) ui.paymentStatus = '';

  const viewToggle = hopCommissionViewToggleHtml(ui);

  if (ui.view === 'records') {
    await renderHopCommissionRecords(mount, ui, viewToggle);
    return;
  }
  if (ui.view === 'by_person') {
    await renderHopCommissionByPerson(mount, ui, viewToggle);
    return;
  }

  let invoices = [];
  let parties = [];
  try {
    const settled = await Promise.allSettled([
      hopApi('/api/v1/hop/commission/invoices'),
      hopCommissionEnsureParties(),
    ]);
    invoices = settled[0].status === 'fulfilled' ? (settled[0].value || []) : [];
    parties = settled[1].status === 'fulfilled' ? (settled[1].value || []) : [];
    if (settled[0].status === 'rejected') throw settled[0].reason;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }

  let sheet = null;
  if (ui.selectedId) {
    try {
      sheet = await hopApi(`/api/v1/hop/commission/worksheet?party_txn_id=${ui.selectedId}`) || null;
      ui.sheet = sheet;
    } catch (e) {
      sheet = null;
      ui.sheet = null;
    }
  } else {
    ui.sheet = null;
  }

  const q = String(ui.q || '').trim().toLowerCase();
  let rows = invoices.filter((r) => {
    if (ui.filter === 'saved' && !r.has_entry) return false;
    if (ui.filter === 'pending' && r.has_entry) return false;
    if (!q) return true;
    return String(r.party_name || '').toLowerCase().includes(q)
      || String(r.invoice_no || '').toLowerCase().includes(q)
      || String(r.agent_name || '').toLowerCase().includes(q);
  });

  const saved = invoices.filter((r) => r.has_entry);
  const sumComm = saved.reduce((s, r) => s + Number(r.commission_amount || 0), 0);
  const sumTds = saved.reduce((s, r) => s + Number(r.tds_amount || 0), 0);
  const sumNet = saved.reduce((s, r) => s + Number(r.net_commission || 0), 0);

  const bill = sheet?.bill || null;
  const entry = sheet?.entry || {};
  const selectedInv = (invoices || []).find((r) => Number(r.party_txn_id) === Number(ui.selectedId)) || null;
  const cPct = entry.commission_pct != null && entry.commission_pct !== ''
    ? Number(entry.commission_pct).toFixed(2)
    : '';
  const tPct = entry.tds_pct != null && entry.tds_pct !== ''
    ? Number(entry.tds_pct).toFixed(2)
    : '';
  const notes = entry.notes || '';
  // Prefer worksheet entry status; never let a stale list paid_on override explicit unpaid
  const statusRow = {
    payment_status: (entry.payment_status != null && String(entry.payment_status).trim() !== '')
      ? entry.payment_status
      : selectedInv?.payment_status,
    paid_on: (String(entry.payment_status || '').toLowerCase() === 'unpaid')
      ? ''
      : (entry.paid_on || (String(selectedInv?.payment_status || '').toLowerCase() === 'unpaid' ? '' : (selectedInv?.paid_on || ''))),
    expense_source_txn_id: entry.expense_source_txn_id ?? selectedInv?.expense_source_txn_id,
    expense_txn_number: entry.expense_txn_number || selectedInv?.expense_txn_number,
    origin: entry.origin || selectedInv?.origin,
  };
  const payStatus = hopCommissionIsPaid(statusRow) ? 'paid' : 'unpaid';
  const paidOn = payStatus === 'paid' ? String(statusRow.paid_on || '').slice(0, 10) : '';
  const vyaparPaid = String(entry.vyapar_payment_status || '') === 'paid'
    || (!!entry.expense_source_txn_id || !!entry.expense_txn_number
      || !!selectedInv?.expense_source_txn_id || !!selectedInv?.expense_txn_number);
  const statusHint = (entry.expense_txn_number || entry.expense_source_txn_id
    || selectedInv?.expense_txn_number || selectedInv?.expense_source_txn_id)
    ? `<span class="hop-comm-status-lock nx-text-dim">Vyapar expense${(entry.expense_txn_number || selectedInv?.expense_txn_number) ? ` · ${foEscapeText(entry.expense_txn_number || selectedInv?.expense_txn_number)}` : ''}${entry.vyapar_payment_status ? ` · ${foEscapeText(String(entry.vyapar_payment_status))}` : ''}</span>`
    : '';
  const agentKey = hopCommissionPartyKey(entry.agent_party_type, entry.agent_party_id);
  // Legacy text-only entries: show matching party by name if possible
  let resolvedAgentKey = agentKey;
  if (!resolvedAgentKey && entry.agent_name) {
    const hit = parties.find((p) => String(p.company || '').trim().toLowerCase() === String(entry.agent_name).trim().toLowerCase());
    if (hit) resolvedAgentKey = hopCommissionPartyKey(hit._type, hit.id);
  }

  const worksheetHtml = bill ? `
    <div class="hop-comm-bar" id="hop-comm-bar">
      <div class="hop-comm-bar-main">
        <div class="hop-comm-bar-title">
          <strong>Invoice ${foEscapeText(bill.invoice_no)}</strong>
          <span class="hop-comm-bar-party">${foEscapeText(bill.party_name)}</span>
          <span class="hop-comm-bar-meta">${foEscapeText(bill.invoice_date || '')} · Before tax <b>${hopMoney(bill.amount_before_tax)}</b></span>
        </div>
        <div class="hop-comm-bar-fields">
          <label class="hop-comm-agent-field">Paid to (Party)
            <select id="hop-comm-agent" class="hop-comm-agent-select">
              ${hopCommissionPartyOptionsHtml(parties, resolvedAgentKey)}
            </select>
          </label>
          <label>Status
            <select id="hop-comm-pay-status" class="inv-ctrl"
              data-vyapar-paid="${vyaparPaid ? '1' : '0'}"
              data-expense-no="${foEscapeAttr(entry.expense_txn_number || '')}"
              onchange="hopCommissionOnPayStatusChange(this.value)">
              <option value="unpaid"${payStatus === 'unpaid' ? ' selected' : ''}>Unpaid</option>
              <option value="paid"${payStatus === 'paid' ? ' selected' : ''}>Paid</option>
            </select>
            ${statusHint}
          </label>
          <label id="hop-comm-paid-on-wrap"${payStatus !== 'paid' ? ' style="opacity:.45"' : ''}>Paid on
            <input id="hop-comm-paid-on" type="date" value="${foEscapeAttr(paidOn)}"
              ${payStatus !== 'paid' ? 'disabled' : ''} />
          </label>
          <label>Comm %<input id="hop-comm-pct" type="number" min="0" step="0.01" value="${foEscapeAttr(String(cPct))}" oninput="hopCommissionRecalc()" /></label>
          <label>TDS %<input id="hop-comm-tds" type="number" min="0" step="0.01" value="${foEscapeAttr(String(tPct))}" oninput="hopCommissionRecalc()" /></label>
          <label class="hop-comm-notes">Notes<input id="hop-comm-notes" type="text" value="${foEscapeAttr(notes)}" placeholder="Optional" /></label>
        </div>
        <div class="hop-comm-bar-results">
          <div><span>Commission</span><strong id="hop-comm-amt">${hopMoney(entry.commission_amount || 0)}</strong></div>
          <div><span>TDS</span><strong id="hop-comm-tds-amt">${hopMoney(entry.tds_amount || 0)}</strong></div>
          <div><span>Net</span><strong id="hop-comm-net">${hopMoney(entry.net_commission || 0)}</strong></div>
        </div>
      </div>
      <div class="hop-comm-bar-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopCommissionSave()">Save</button>
        <button type="button" class="nx-btn" onclick="hopOpenSaleDocPreview(${Number(bill.party_txn_id) || 0}, ${Number(bill.source_txn_id) || 0})">Preview</button>
        <button type="button" class="inv-text-btn" onclick="hopCommissionClearSelection()">Close</button>
        <span id="hop-comm-status" class="nx-text-dim"></span>
      </div>
    </div>` : '';

  const tbody = rows.length
    ? rows.map((r) => {
      const sel = Number(ui.selectedId) === Number(r.party_txn_id);
      let status;
      if (!r.has_entry) {
        status = '<span class="inv-badge inv-badge--open">Set</span>';
      } else {
        // Same Paid/Unpaid rules as Records (payment_status / paid_on / expense)
        status = hopCommissionPayBadge(r.payment_status, r);
      }
      return `<tr class="inv-row is-clickable${sel ? ' is-selected' : ''}"
        onclick="hopCommissionSelectBill(${Number(r.party_txn_id) || 0})">
        <td>${hopCell(r.invoice_date)}</td>
        <td>${hopCell(hopFormatDocNo(r.invoice_no, r.invoice_date, 1))}</td>
        <td>${hopCell(r.party_name)}</td>
        <td>${r.has_entry ? hopCell(r.agent_name || '—') : '—'}</td>
        <td class="inv-num">${hopMoney(r.invoice_total)}</td>
        <td class="inv-num">${r.has_entry ? `${hopPct(r.commission_pct)}%` : '—'}</td>
        <td class="inv-num">${r.has_entry ? `${hopPct(r.tds_pct)}%` : '—'}</td>
        <td class="inv-num">${r.has_entry ? hopMoney(r.commission_amount) : '—'}</td>
        <td class="inv-num">${r.has_entry ? hopMoney(r.tds_amount) : '—'}</td>
        <td class="inv-num"><strong>${r.has_entry ? hopMoney(r.net_commission) : '—'}</strong></td>
        <td>${status}</td>
      </tr>`;
    }).join('')
    : `<tr><td colspan="11" class="inv-empty">No tax invoices match this filter.</td></tr>`;

  const body = `
        ${hopTxToolbar(`
          ${viewToggle}
          <select id="hop-comm-filter" class="inv-ctrl" onchange="hopCommissionSetFilter(this.value)">
            <option value="all"${ui.filter === 'all' ? ' selected' : ''}>All invoices</option>
            <option value="saved"${ui.filter === 'saved' ? ' selected' : ''}>Commission saved</option>
            <option value="pending"${ui.filter === 'pending' ? ' selected' : ''}>Not set yet</option>
          </select>
          <span class="inv-toolbar-spacer"></span>
          <button type="button" class="inv-text-btn" onclick="hopCommissionClearSelection()">Clear selection</button>
        `)}
        ${hopTxCards([
          { label: 'Bills', value: String(invoices.length), tone: 'neutral' },
          { label: 'Commission', valueHtml: hopMoney(sumComm), tone: 'paid' },
          { label: 'TDS', valueHtml: hopMoney(sumTds), tone: 'overdue', op: '+' },
          { label: 'Net Payable', valueHtml: hopMoney(sumNet), tone: 'total', op: '=' },
        ])}
        ${worksheetHtml}
        <div class="inv-table-card">
          <div class="inv-table-head">
            <strong>Transactions</strong>
            <span class="inv-count">${rows.length} txns</span>
            <input id="hop-comm-q" class="inv-search" type="search" placeholder="Search party, invoice, agent…"
              value="${foEscapeAttr(ui.q || '')}" oninput="hopCommissionOnSearch(this.value)" />
          </div>
          <div class="inv-table-wrap">
            <table class="inv-table hop-comm-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Invoice no</th>
                  <th>Party Name</th>
                  <th>Paid to</th>
                  <th class="inv-num">Amount</th>
                  <th class="inv-num">Comm %</th>
                  <th class="inv-num">TDS %</th>
                  <th class="inv-num">Commission</th>
                  <th class="inv-num">TDS</th>
                  <th class="inv-num">Net</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody id="hop-comm-tbody">${tbody}</tbody>
            </table>
          </div>
        </div>`;

  mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', body);
  hopCommissionRecalc();
  const paySel = document.getElementById('hop-comm-pay-status');
  if (paySel && bill) {
    paySel.value = payStatus;
    paySel.setAttribute('data-prev-status', payStatus);
    if (hopState.commissionUi) hopState.commissionUi._ribbonPayStatus = payStatus;
    hopCommissionApplyPayStatusUi(payStatus === 'paid');
    if (payStatus === 'paid' && paidOn) {
      const paidInput = document.getElementById('hop-comm-paid-on');
      if (paidInput && !paidInput.value) paidInput.value = paidOn;
    }
  }
}

function hopCommissionDateFilterBar(ui, payees) {
  const period = ui.period || 'all';
  const payeeOpts = [`<option value="">All people</option>`].concat(
    (payees || []).map((p) => {
      const key = p.key || hopCommissionPartyKey(p.agent_party_type, p.agent_party_id) || `name:${p.agent_name || p.label}`;
      const label = p.label || p.agent_name || key;
      return `<option value="${foEscapeAttr(key)}"${key === (ui.agentKey || '') ? ' selected' : ''}>${foEscapeText(label)}</option>`;
    }),
  ).join('');
  return `
    <div class="hop-comm-date-bar">
      <label>Period
        <select id="hop-comm-period" class="inv-ctrl" onchange="hopCommissionOnPeriodChange(this.value)">
          <option value="today"${period === 'today' ? ' selected' : ''}>Today</option>
          <option value="this_week"${period === 'this_week' ? ' selected' : ''}>This Week</option>
          <option value="this_month"${period === 'this_month' ? ' selected' : ''}>This Month</option>
          <option value="last_month"${period === 'last_month' ? ' selected' : ''}>Last Month</option>
          <option value="this_quarter"${period === 'this_quarter' ? ' selected' : ''}>This Quarter</option>
          <option value="this_year"${period === 'this_year' ? ' selected' : ''}>This Year</option>
          <option value="all"${period === 'all' ? ' selected' : ''}>All Time</option>
          <option value="custom"${period === 'custom' ? ' selected' : ''}>Custom</option>
        </select>
      </label>
      <label>From<input id="hop-comm-date-from" type="date" value="${foEscapeAttr(ui.dateFrom || '')}"
        onchange="hopCommissionSetDateFilter('dateFrom', this.value)" /></label>
      <label>To<input id="hop-comm-date-to" type="date" value="${foEscapeAttr(ui.dateTo || '')}"
        onchange="hopCommissionSetDateFilter('dateTo', this.value)" /></label>
      <label class="hop-comm-agent-filter">Paid to
        <select id="hop-comm-agent-filter" class="inv-ctrl" onchange="hopCommissionSetAgentFilter(this.value)">
          ${payeeOpts}
        </select>
      </label>
      <label>Status
        <select id="hop-comm-status-filter" class="inv-ctrl" onchange="hopCommissionSetPaymentStatusFilter(this.value)">
          <option value=""${!(ui.paymentStatus) ? ' selected' : ''}>All</option>
          <option value="paid"${ui.paymentStatus === 'paid' ? ' selected' : ''}>Paid</option>
          <option value="unpaid"${ui.paymentStatus === 'unpaid' ? ' selected' : ''}>Unpaid</option>
        </select>
      </label>
      <button type="button" class="inv-text-btn" onclick="hopCommissionClearDateFilters()">Clear filters</button>
      <input id="hop-comm-q" class="inv-search" type="search" placeholder="Search…"
        value="${foEscapeAttr(ui.q || '')}" oninput="hopCommissionOnSearchRecords(this.value)" />
    </div>`;
}

function hopCommissionRecordsQuery(opts) {
  const ui = hopState.commissionUi || {};
  const ignoreAgent = !!(opts && opts.ignoreAgent);
  const params = new URLSearchParams();
  if (ui.q) params.set('q', ui.q);
  if (ui.dateFrom) params.set('date_from', ui.dateFrom);
  if (ui.dateTo) params.set('date_to', ui.dateTo);
  if (ui.paymentStatus === 'paid' || ui.paymentStatus === 'unpaid') {
    params.set('payment_status', ui.paymentStatus);
  }
  if (!ignoreAgent && ui.agentKey) {
    if (ui.agentKey.startsWith('name:')) {
      params.set('agent_name', ui.agentKey.slice(5));
    } else if (ui.agentKey.includes(':')) {
      const [t, id] = ui.agentKey.split(':');
      if ((t === 'customer' || t === 'vendor') && id) {
        params.set('agent_party_type', t);
        params.set('agent_party_id', id);
      }
    }
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

function hopCommissionIsExpenseDocNo(no) {
  return /^comm\s*\//i.test(String(no || '').trim());
}

function hopCommissionIsPaid(r) {
  if (!r) return false;
  const st = String(r.payment_status || '').toLowerCase();
  if (st === 'unpaid') return false;
  if (st === 'paid') return true;
  // No explicit status yet — Vyapar expense link / paid_on imply Paid
  if (r.expense_source_txn_id || r.expense_txn_number || r.origin === 'expense') return true;
  if (String(r.paid_on || '').trim()) return true;
  return false;
}

function hopCommissionPayBadge(status, row) {
  const paid = hopCommissionIsPaid({ ...(row || {}), payment_status: status != null ? status : row?.payment_status });
  if (paid) return '<span class="inv-badge inv-badge--paid">Paid</span>';
  return '<span class="inv-badge inv-badge--open">Unpaid</span>';
}

function hopCommissionDocLink(label, partyTxnId, sourceTxnId) {
  const text = String(label || '').trim();
  if (!text) return '—';
  const pid = Number(partyTxnId || 0) || 0;
  const sid = Number(sourceTxnId || 0) || 0;
  if (!pid && !sid) return foEscapeText(text);
  return `<a href="#" class="hop-comm-link" title="Open preview"
    onclick="event.preventDefault();event.stopPropagation();hopOpenSaleDocPreview(${pid}, ${sid})">${foEscapeText(text)}</a>`;
}

/** Sale invoice number → sale invoice preview. */
function hopCommissionInvoiceCellHtml(r) {
  const label = hopFormatDocNo(r.invoice_no, r.invoice_date || r.when, 1);
  if (!label) return '—';
  // Standalone Comm/… rows are expense vouchers, not sale invoices
  if (hopCommissionIsExpenseDocNo(r.invoice_no) || (!r.party_txn_id && r.expense_source_txn_id)) {
    return hopCommissionDocLink(label, 0, r.expense_source_txn_id);
  }
  return hopCommissionDocLink(label, r.party_txn_id, r.source_txn_id);
}

/** Expense Comm/… number → commission expense preview. */
function hopCommissionExpenseCellHtml(r) {
  const exp = String(r.expense_txn_number || '').trim();
  if (!exp) return '<span class="nx-text-dim">—</span>';
  return hopCommissionDocLink(exp, 0, r.expense_source_txn_id);
}

function hopCommissionSummaryCards(summary) {
  return hopTxCards([
    { label: 'People', value: String(summary.people || 0), tone: 'neutral' },
    { label: 'Bills', value: String(summary.bills || 0), tone: 'neutral' },
    { label: 'Commission', valueHtml: hopMoney(summary.commission_amount || 0), tone: 'paid' },
    { label: 'TDS', valueHtml: hopMoney(summary.tds_amount || 0), tone: 'overdue', op: '−' },
    { label: 'Net paid', valueHtml: hopMoney(summary.net_commission || 0), tone: 'total', op: '=' },
  ]);
}

async function renderHopCommissionRecords(mount, ui, viewToggle) {
  hopCommissionEnsurePeriodDates(ui);
  let data = { records: [], summary: {}, payees: [] };
  try {
    data = await hopApi(`/api/v1/hop/commission/records${hopCommissionRecordsQuery()}`) || data;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const summary = data.summary || {};
  const records = data.records || [];
  const payees = data.payees || [];
  // Keep selected payee visible even if filtered out of current result set
  if (ui.agentKey && !payees.some((p) => p.key === ui.agentKey)) {
    const label = ui.agentKey.startsWith('name:') ? ui.agentKey.slice(5) : ui.agentKey;
    payees.unshift({ key: ui.agentKey, label, agent_name: label });
  }
  const tbody = records.length
    ? records.map((r) => {
      const paid = hopCommissionIsPaid(r);
      return `
      <tr class="inv-row">
        <td>${hopCell(r.when || '—')}</td>
        <td><strong>${hopCell(r.agent_name)}</strong></td>
        <td>${hopCommissionInvoiceCellHtml(r)}</td>
        <td>${hopCell(r.party_name)}</td>
        <td class="inv-num">${r.commission_pct != null ? `${hopPct(r.commission_pct)}%` : '—'}</td>
        <td class="inv-num">${hopMoney(r.commission_amount)}</td>
        <td class="inv-num">${hopMoney(r.tds_amount)}</td>
        <td class="inv-num"><strong>${hopMoney(r.net_commission)}</strong></td>
        <td>${hopCommissionPayBadge(r.payment_status, r)}</td>
        <td>${hopCell(paid ? (r.paid_on || r.when || '—') : '—')}</td>
        <td>${hopCommissionExpenseCellHtml(r)}</td>
        <td class="nx-text-dim">${hopCell(r.notes || '')}</td>
      </tr>`;
    }).join('')
    : `<tr><td colspan="12" class="inv-empty">No commission records for this date range. Save commission on an invoice with Paid to + Status.</td></tr>`;

  const body = `
    ${hopTxToolbar(`${viewToggle}`)}
    ${hopCommissionDateFilterBar(ui, payees)}
    ${hopCommissionSummaryCards(summary)}
    <div class="inv-table-card">
      <div class="inv-table-head">
        <strong>Commission records</strong>
        <span class="inv-count">${records.length} entries</span>
      </div>
      <div class="inv-table-wrap">
        <table class="inv-table hop-comm-table">
          <thead>
            <tr>
              <th>When</th>
              <th>Paid to</th>
              <th>Invoice</th>
              <th>Sale party</th>
              <th class="inv-num">Comm %</th>
              <th class="inv-num">Commission</th>
              <th class="inv-num">TDS</th>
              <th class="inv-num">Net</th>
              <th>Status</th>
              <th>Paid on</th>
              <th>Expense</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>${tbody}</tbody>
        </table>
      </div>
    </div>`;

  mount.innerHTML = hopModuleShell('Sale', 'Commission', 'Who got how much, and when', '', body);
}

async function renderHopCommissionByPerson(mount, ui, viewToggle) {
  hopCommissionEnsurePeriodDates(ui);
  let data = { agents: [], summary: {}, payees: [] };
  try {
    // Payees from flat records (date-scoped); groups from by-agent
    const settled = await Promise.allSettled([
      hopApi(`/api/v1/hop/commission/by-agent${hopCommissionRecordsQuery()}`),
      hopApi(`/api/v1/hop/commission/records${hopCommissionRecordsQuery({ ignoreAgent: true })}`),
    ]);
    data = settled[0].status === 'fulfilled' ? (settled[0].value || data) : data;
    const flat = settled[1].status === 'fulfilled' ? (settled[1].value || {}) : {};
    data.payees = flat.payees || [];
    if (settled[0].status === 'rejected') throw settled[0].reason;
  } catch (e) {
    mount.innerHTML = hopModuleShell('Sale', 'Commission', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const summary = data.summary || {};
  const agents = data.agents || [];
  const payees = data.payees || [];
  if (ui.agentKey && !payees.some((p) => p.key === ui.agentKey)) {
    const label = ui.agentKey.startsWith('name:') ? ui.agentKey.slice(5) : ui.agentKey;
    payees.unshift({ key: ui.agentKey, label, agent_name: label });
  }

  // Client-side agent filter for by-person when party/name selected
  let shown = agents;
  if (ui.agentKey) {
    shown = agents.filter((a) => {
      const key = a.agent_party_id && a.agent_party_type
        ? hopCommissionPartyKey(a.agent_party_type, a.agent_party_id)
        : `name:${a.agent_name}`;
      return key === ui.agentKey || String(a.agent_name || '') === (ui.agentKey.startsWith('name:') ? ui.agentKey.slice(5) : '');
    });
  }

  const cards = hopCommissionSummaryCards({
    people: shown.length,
    bills: shown.reduce((s, a) => s + Number(a.bills || 0), 0),
    commission_amount: shown.reduce((s, a) => s + Number(a.commission_amount || 0), 0),
    tds_amount: shown.reduce((s, a) => s + Number(a.tds_amount || 0), 0),
    net_commission: shown.reduce((s, a) => s + Number(a.net_commission || 0), 0),
  });

  const blocks = shown.length
    ? shown.map((a) => {
      const rows = (a.entries || []).map((e) => {
        const paid = hopCommissionIsPaid(e);
        return `
        <tr class="inv-row">
          <td>${hopCell(e.when || e.invoice_date || '—')}</td>
          <td>${hopCommissionInvoiceCellHtml(e)}</td>
          <td>${hopCell(e.party_name)}</td>
          <td class="inv-num">${e.commission_pct != null ? `${hopPct(e.commission_pct)}%` : '—'}</td>
          <td class="inv-num">${hopMoney(e.commission_amount)}</td>
          <td class="inv-num">${hopMoney(e.tds_amount)}</td>
          <td class="inv-num"><strong>${hopMoney(e.net_commission)}</strong></td>
          <td>${hopCommissionPayBadge(e.payment_status, e)}</td>
          <td>${hopCell(paid ? (e.paid_on || e.when || '—') : '—')}</td>
          <td>${hopCommissionExpenseCellHtml(e)}</td>
        </tr>`;
      }).join('');
      return `
        <div class="hop-comm-agent-card">
          <div class="hop-comm-agent-head">
            <div>
              <strong class="hop-comm-agent-name">${foEscapeText(a.agent_name)}</strong>
              <span class="inv-count">${a.bills} bill${a.bills === 1 ? '' : 's'}</span>
            </div>
            <div class="hop-comm-agent-totals">
              <span>Comm <b>${hopMoney(a.commission_amount)}</b></span>
              <span>TDS <b>${hopMoney(a.tds_amount)}</b></span>
              <span>Net <b>${hopMoney(a.net_commission)}</b></span>
            </div>
          </div>
          <div class="inv-table-wrap">
            <table class="inv-table hop-comm-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Invoice</th>
                  <th>Party</th>
                  <th class="inv-num">Comm %</th>
                  <th class="inv-num">Commission</th>
                  <th class="inv-num">TDS</th>
                  <th class="inv-num">Net</th>
                  <th>Status</th>
                  <th>Paid on</th>
                  <th>Expense</th>
                </tr>
              </thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        </div>`;
    }).join('')
    : `<div class="inv-empty hop-comm-empty">No saved commission yet. Open an invoice, link <b>Paid to</b> party, and Save.</div>`;

  const body = `
    ${hopTxToolbar(`${viewToggle}`)}
    ${hopCommissionDateFilterBar(ui, payees)}
    ${cards}
    <div class="hop-comm-by-person">${blocks}</div>`;

  mount.innerHTML = hopModuleShell('Sale', 'Commission', 'Totals by person', '', body);
}

function hopCommissionOnSearch(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.q = String(value || '');
  // Debounced-ish soft re-render of list only via filter in-place
  const tbody = document.getElementById('hop-comm-tbody');
  if (!tbody) return;
  const q = String(value || '').trim().toLowerCase();
  let visible = 0;
  tbody.querySelectorAll('tr').forEach((tr) => {
    if (tr.querySelector('.inv-empty')) return;
    const show = !q || (tr.textContent || '').toLowerCase().includes(q);
    tr.style.display = show ? '' : 'none';
    if (show) visible += 1;
  });
  const count = document.querySelector('.hop-view--tx .inv-count');
  if (count) count.textContent = `${visible} txns`;
}

let _hopCommRecordsSearchTimer = null;
function hopCommissionOnSearchRecords(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.q = String(value || '');
  clearTimeout(_hopCommRecordsSearchTimer);
  _hopCommRecordsSearchTimer = setTimeout(() => {
    openHopView('commission', { skipHistory: true });
  }, 280);
}

function hopCommissionEnsurePeriodDates(ui) {
  if (!ui) return;
  if (!ui.period) ui.period = 'all';
  if (ui.period === 'custom') return;
  if (ui.period === 'all') {
    // Keep empty unless user already typed custom dates while on All Time
    return;
  }
  const range = hopInvoicePeriodRange(ui.period);
  ui.dateFrom = range.from;
  ui.dateTo = range.to;
}

function hopCommissionOnPeriodChange(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  const period = value || 'all';
  hopState.commissionUi.period = period;
  if (period === 'custom') {
    openHopView('commission', { skipHistory: true });
    return;
  }
  if (period === 'all') {
    hopState.commissionUi.dateFrom = '';
    hopState.commissionUi.dateTo = '';
  } else {
    const range = hopInvoicePeriodRange(period);
    hopState.commissionUi.dateFrom = range.from;
    hopState.commissionUi.dateTo = range.to;
  }
  openHopView('commission', { skipHistory: true });
}

function hopCommissionSetDateFilter(field, value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi[field] = String(value || '').slice(0, 10);
  hopState.commissionUi.period = 'custom';
  openHopView('commission', { skipHistory: true });
}

function hopCommissionSetAgentFilter(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.agentKey = String(value || '');
  openHopView('commission', { skipHistory: true });
}

function hopCommissionClearDateFilters() {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.period = 'all';
  hopState.commissionUi.dateFrom = '';
  hopState.commissionUi.dateTo = '';
  hopState.commissionUi.agentKey = '';
  hopState.commissionUi.paymentStatus = '';
  hopState.commissionUi.q = '';
  openHopView('commission', { skipHistory: true });
}

function hopCommissionSetPaymentStatusFilter(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  const v = String(value || '').toLowerCase();
  hopState.commissionUi.paymentStatus = (v === 'paid' || v === 'unpaid') ? v : '';
  openHopView('commission', { skipHistory: true });
}

async function hopCommissionConfirmUnpaid(sel) {
  const vyaparPaid = sel && String(sel.getAttribute('data-vyapar-paid') || '') === '1';
  const expNo = sel ? String(sel.getAttribute('data-expense-no') || '').trim() : '';
  const title = 'Change to Unpaid?';
  let message;
  if (vyaparPaid) {
    message = `This entry is already Paid in your software`
      + (expNo ? ` (Vyapar expense ${expNo})` : ' (Vyapar)')
      + `.\n\nAre you still sure you want to change the status to Unpaid?`;
  } else {
    message = 'This commission is currently Paid.\n\nAre you sure you want to change it to Unpaid?';
  }
  if (typeof nexoraConfirm === 'function') {
    return !!(await nexoraConfirm(message, {
      title,
      okText: 'Yes, set Unpaid',
      cancelText: 'No, keep Paid',
      danger: true,
    }));
  }
  return !!(window.confirm && window.confirm(message));
}

async function hopCommissionOnPayStatusChange(value) {
  const sel = document.getElementById('hop-comm-pay-status');
  const paid = String(value || '') === 'paid';
  const prev = String(sel?.getAttribute('data-prev-status') || hopState.commissionUi?._ribbonPayStatus || '');
  if (!paid && prev === 'paid') {
    const ok = await hopCommissionConfirmUnpaid(sel);
    if (!ok) {
      if (sel) sel.value = 'paid';
      hopCommissionApplyPayStatusUi(true);
      return;
    }
  }
  if (sel) sel.setAttribute('data-prev-status', paid ? 'paid' : 'unpaid');
  if (hopState.commissionUi) hopState.commissionUi._ribbonPayStatus = paid ? 'paid' : 'unpaid';
  hopCommissionApplyPayStatusUi(paid);
}

function hopCommissionApplyPayStatusUi(paid) {
  const input = document.getElementById('hop-comm-paid-on');
  const wrap = document.getElementById('hop-comm-paid-on-wrap');
  if (input) {
    input.disabled = !paid;
    if (!paid) {
      input.value = '';
    } else if (!input.value) {
      const today = new Date();
      const y = today.getFullYear();
      const m = String(today.getMonth() + 1).padStart(2, '0');
      const d = String(today.getDate()).padStart(2, '0');
      input.value = `${y}-${m}-${d}`;
    }
  }
  if (wrap) wrap.style.opacity = paid ? '' : '.45';
}

function hopCommissionSetView(view) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  const allowed = { invoices: 1, records: 1, by_person: 1 };
  hopState.commissionUi.view = allowed[view] ? view : 'invoices';
  if (view !== 'invoices') {
    hopState.commissionUi.selectedId = null;
    hopState.commissionUi.sheet = null;
  }
  openHopView('commission', { skipHistory: true });
}

function hopCommissionSetFilter(value) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.filter = value || 'all';
  openHopView('commission', { skipHistory: true });
}

function hopCommissionClearSelection() {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.selectedId = null;
  hopState.commissionUi.sheet = null;
  openHopView('commission', { skipHistory: true });
}

function hopCommissionSelectBill(partyTxnId) {
  if (!hopState.commissionUi) hopState.commissionUi = {};
  hopState.commissionUi.view = 'invoices';
  const id = Number(partyTxnId) || null;
  // Click same row again → hide ribbon
  if (id && Number(hopState.commissionUi.selectedId) === id) {
    hopState.commissionUi.selectedId = null;
    hopState.commissionUi.sheet = null;
  } else {
    hopState.commissionUi.selectedId = id;
    hopState.commissionUi.sheet = null;
  }
  openHopView('commission', { skipHistory: true });
}

/** Open linked sale invoice (or expense voucher) preview — same modal as Sale Invoices. */
function hopCommissionOpenDoc(partyTxnId, sourceTxnId, expenseSourceTxnId) {
  const pid = Number(partyTxnId || 0) || 0;
  const sid = Number(sourceTxnId || 0) || 0;
  const esid = Number(expenseSourceTxnId || 0) || 0;
  if (pid || sid) {
    return hopOpenSaleDocPreview(pid, sid);
  }
  if (esid) {
    return hopOpenSaleDocPreview(0, esid);
  }
  if (typeof nexoraToast === 'function') {
    nexoraToast('No linked invoice/expense to preview for this row.', 'warn');
  }
}

function hopCommissionRecalc() {
  const sheet = hopState.commissionUi?.sheet;
  const before = Number(sheet?.bill?.amount_before_tax || 0);
  const cPct = Number(document.getElementById('hop-comm-pct')?.value || 0);
  const tPct = Number(document.getElementById('hop-comm-tds')?.value || 0);
  const commission = Math.round(before * (Number.isFinite(cPct) ? cPct : 0) / 100 * 100) / 100;
  const tds = Math.round(commission * (Number.isFinite(tPct) ? tPct : 0) / 100 * 100) / 100;
  const net = Math.round((commission - tds) * 100) / 100;
  const amt = document.getElementById('hop-comm-amt');
  const tdsEl = document.getElementById('hop-comm-tds-amt');
  const netEl = document.getElementById('hop-comm-net');
  if (amt) amt.textContent = hopMoney(commission);
  if (tdsEl) tdsEl.textContent = hopMoney(tds);
  if (netEl) netEl.textContent = hopMoney(net);
}

function hopCommissionReadAgentPayload() {
  const raw = String(document.getElementById('hop-comm-agent')?.value || '').trim();
  if (raw.includes(':')) {
    const [type, id] = raw.split(':');
    if ((type === 'customer' || type === 'vendor') && id) {
      const opt = document.getElementById('hop-comm-agent')?.selectedOptions?.[0];
      const label = String(opt?.textContent || '').replace(/\s*\(Vendor\)\s*$/, '').trim();
      return {
        agent_party_type: type,
        agent_party_id: Number(id) || null,
        agent_name: label || '',
      };
    }
  }
  return { agent_party_type: '', agent_party_id: null, agent_name: '' };
}

async function hopCommissionSave() {
  const id = hopState.commissionUi?.selectedId;
  if (!id) return;
  const status = document.getElementById('hop-comm-status');
  if (status) status.textContent = 'Saving…';
  try {
    const agent = hopCommissionReadAgentPayload();
    const sel = document.getElementById('hop-comm-pay-status');
    const payStatus = String(sel?.value || 'unpaid').toLowerCase() === 'paid' ? 'paid' : 'unpaid';
    const prevStatus = String(
      sel?.getAttribute('data-prev-status')
      || hopState.commissionUi?._ribbonPayStatus
      || ''
    ).toLowerCase();
    const wasPaid = prevStatus === 'paid'
      || hopCommissionIsPaid(hopState.commissionUi?.sheet?.entry || {});
    if (payStatus === 'unpaid' && wasPaid) {
      const ok = await hopCommissionConfirmUnpaid(sel);
      if (!ok) {
        if (sel) sel.value = 'paid';
        hopCommissionApplyPayStatusUi(true);
        if (status) status.textContent = '';
        return;
      }
    }
    const paidOn = payStatus === 'paid'
      ? (document.getElementById('hop-comm-paid-on')?.value || '')
      : '';
    if (payStatus === 'paid' && !paidOn) {
      throw new Error('Select Paid on date when status is Paid');
    }
    const data = await hopApi('/api/v1/hop/commission/worksheet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        party_txn_id: id,
        commission_pct: Number(document.getElementById('hop-comm-pct')?.value || 0),
        tds_pct: Number(document.getElementById('hop-comm-tds')?.value || 0),
        notes: document.getElementById('hop-comm-notes')?.value || '',
        payment_status: payStatus,
        paid_on: paidOn || null,
        ...agent,
      }),
    });
    if (hopState.commissionUi) {
      hopState.commissionUi.sheet = data;
      hopState.commissionUi._ribbonPayStatus = payStatus;
      // Keep ribbon open on the same bill after save
      hopState.commissionUi.selectedId = id;
    }
    if (status) status.textContent = 'Saved';
    // Soft reload — keep selection; avoid blank "Loading…" hang
    const mount = hopMount() || document.getElementById('hop-module-mount');
    if (mount) {
      try {
        await renderHopCommissionModule(mount);
      } catch (renderErr) {
        console.error('Commission re-render failed', renderErr);
        openHopView('commission', { skipHistory: true });
      }
    }
    if (typeof nexoraToast === 'function') {
      nexoraToast(payStatus === 'unpaid' ? 'Saved as Unpaid' : 'Commission saved', 'ok');
    }
  } catch (e) {
    if (status) status.textContent = e.message || 'Save failed';
    if (typeof nexoraToast === 'function') nexoraToast(e.message || 'Save failed', 'error');
  }
}

async function renderHopReceivablesModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/receivables') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Receivables', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const a = data.ageing || {};
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('0–30 Days', a['0_30'], null)}
      ${renderHopKpiCard('31–60 Days', a['31_60'], null)}
      ${renderHopKpiCard('61–90 Days', a['61_90'], null)}
      ${renderHopKpiCard('90+ Days', a['90_plus'], null)}
    </div>
    <h3 class="hop-section-title">Highest outstanding</h3>
    ${hopTable(['Customer', 'Outstanding'],
      (data.top_customers || []).map((r) => `<tr><td>${hopCell(r.customer)}</td><td>${hopMoney(r.outstanding)}</td></tr>`).join(''))}
    <h3 class="hop-section-title">Open invoices</h3>
    ${hopTable(['Invoice', 'Customer', 'Balance', 'Due'],
      (data.invoices || []).map((r) => `<tr><td>${hopCell(r.invoice_no)}</td><td>${hopCell(r.customer_company)}</td><td>${hopMoney(r.balance)}</td><td>${hopCell(r.due_date)}</td></tr>`).join(''))}`;
  mount.innerHTML = hopModuleShell('Reports', 'Receivable Ageing', '', '', body);
}

async function renderHopCustomerDashModule(mount) {
  let rows = [];
  try { rows = await hopApi('/api/v1/hop/reports/customers') || []; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Customer Dashboard', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const withBiz = rows.filter((r) => Number(r.total_business || 0) > 0 || Number(r.outstanding || 0) > 0).length;
  const body = `
    ${hopTxCards([
      { label: 'Customers', value: String(rows.length), tone: 'neutral' },
      { label: 'With sales', value: String(withBiz), tone: 'paid' },
      { label: 'Total business', valueHtml: hopMoney(rows.reduce((s, r) => s + Number(r.total_business || 0), 0)), tone: 'total' },
      { label: 'Outstanding', valueHtml: hopMoney(rows.reduce((s, r) => s + Number(r.outstanding || 0), 0)), tone: 'overdue' },
    ])}
    ${hopTable(
      ['Company', 'City', 'Rating', 'Total Business', 'Invoices', 'AOV', 'Outstanding', 'Last Meeting', 'Last Purchase'],
      rows.map((r) => `<tr>
        <td><strong>${hopCell(r.company)}</strong></td>
        <td>${hopCell(r.city)}</td>
        <td>${hopCell(r.potential_rating)}</td>
        <td class="inv-num">${hopMoney(r.total_business)}</td>
        <td class="inv-num">${hopCell(r.invoice_count != null ? r.invoice_count : r.projects)}</td>
        <td class="inv-num">${hopMoney(r.average_order_value)}</td>
        <td class="inv-num">${hopMoney(r.outstanding)}</td>
        <td>${hopCell((r.last_meeting || '').toString().slice(0, 10))}</td>
        <td>${hopCell((r.last_purchase || '').toString().slice(0, 10))}</td>
      </tr>`).join('') || '<tr><td colspan="9" class="inv-empty">No customers yet. Import from Vyapar to fill this dashboard.</td></tr>',
      { label: 'Customer Dashboard', count: rows.length, className: 'hop-customer-dash-table' },
    )}`;
  mount.innerHTML = hopModuleShell(
    'Reports',
    'Customer Dashboard',
    'From Vyapar sale invoices · outstanding · last purchase',
    '',
    body,
  );
}

async function renderHopDailyModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/daily') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Daily Activity', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <p class="nx-text-dim">Day: ${foEscapeText(data.day || '')}</p>
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Leads', data.leads_created, null)}
      ${renderHopKpiCard('Meetings', data.meetings, null)}
      ${renderHopKpiCard('Samples', data.samples_sent, null)}
      ${renderHopKpiCard('Follow-ups', data.follow_ups, null)}
      ${renderHopKpiCard('Quotes', data.quotes_sent, null)}
      ${renderHopKpiCard('Orders Closed', data.orders_closed, null)}
      ${renderHopKpiCard('Collections', data.collections, null)}
      ${renderHopKpiCard('Calls', data.calls, null)}
    </div>`;
  mount.innerHTML = hopModuleShell('Reports', 'Daily Activity Report', '', '', body);
}

async function renderHopProfitModule(mount) {
  let data = {};
  try { data = await hopApi('/api/v1/hop/reports/profit') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Profitability', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Revenue', data.revenue, null)}
      ${renderHopKpiCard('COGS', data.cogs, null)}
      ${renderHopKpiCard('Gross Profit', data.gross_profit, null)}
      ${renderHopKpiCard('Gross Margin %', data.gross_margin_pct, null)}
      ${renderHopKpiCard('Expenses', data.expenses, null)}
      ${renderHopKpiCard('Net Profit', data.net_profit, null)}
    </div>
    <p class="nx-text-dim">${foEscapeText((data.notes && data.notes.cogs) || '')}</p>`;
  mount.innerHTML = hopModuleShell('Reports', 'Profitability', 'Order & catalogue based', '', body);
}

async function renderHopTargetsModule(mount) {
  let snap = {};
  try { snap = await hopApi('/api/v1/hop/executive/snapshot') || {}; } catch (e) {
    mount.innerHTML = hopModuleShell('Reports', 'Monthly Target', '', '', `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`);
    return;
  }
  const m = snap.monthly || {};
  const sales = Number(m.sales || 0);
  const target = Number(m.target || 0);
  const pending = Math.max(0, target - sales);
  const body = `
    <div class="hop-kpi-grid hop-kpi-grid-sm">
      ${renderHopKpiCard('Period', m.period_label, null)}
      ${renderHopKpiCard('Target', target, null)}
      ${renderHopKpiCard('Achieved', sales, null)}
      ${renderHopKpiCard('Pending / Gap', pending, null)}
    </div>
    <div id="hop-form-slot" class="nx-card hop-form-card">
      <strong>Set / update monthly target</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Period (YYYY-MM)<input id="hop-t-period" value="${foEscapeText(m.period_label || '')}" /></label>
        <label>Target Amount<input id="hop-t-amount" type="number" step="any" value="${target || ''}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSaveTarget()">Save Target</button>
      </div>
    </div>`;
  mount.innerHTML = hopModuleShell('Reports', 'Monthly Target', '', '', body);
}

async function hopSaveTarget() {
  try {
    await hopApi('/api/v1/hop/targets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        period_label: document.getElementById('hop-t-period')?.value,
        target_amount: document.getElementById('hop-t-amount')?.value,
      }),
    });
    openHopView('targets');
  } catch (e) { alert(e.message); }
}

/* ---------- Project Hub ---------- */
async function openHopProjectHub(projectId) {
  // CRM / Project Hub temporarily disabled
  if (typeof nexoraToast === 'function') nexoraToast('CRM is temporarily hidden', 'ok');
  openHopView('dashboard');
}

async function loadHopProjectHub(projectId) {
  const body = document.getElementById('hop-hub-body');
  const tabs = document.getElementById('hop-hub-tabs');
  if (body) body.innerHTML = '<p class="nx-text-dim">Loading project hub…</p>';
  try {
    const hub = await hopApi(`/api/v1/hop/projects/${projectId}/hub`);
    hopState.hub = hub;
    const p = hub.project || {};
    document.getElementById('hop-hub-title').textContent = p.project_name || 'Project';
    document.getElementById('hop-hub-sub').textContent = [
      hub.customer?.company || p.customer_company,
      p.hotel_name,
      p.architect,
      p.consultant,
    ].filter(Boolean).join(' · ');
    const stageSel = document.getElementById('hop-hub-stage');
    if (stageSel) {
      stageSel.innerHTML = hopStageOptions(hub.funnel_stages || HOP_PROJECT_STAGES, p.stage);
    }
    const tabDefs = [
      ['overview', 'Overview'],
      ['meetings', 'Meetings'],
      ['samples', 'Samples'],
      ['vendors', 'Vendors'],
      ['quotations', 'Quotations'],
      ['orders', 'PO'],
      ['dispatches', 'Dispatch'],
      ['invoices', 'Invoices'],
      ['payments', 'Payments'],
      ['complaints', 'Complaints'],
      ['timeline', 'Timeline'],
    ];
    if (!tabDefs.find((t) => t[0] === hopState.hubTab)) hopState.hubTab = 'overview';
    tabs.innerHTML = tabDefs.map(([k, label]) => `
      <button type="button" class="hop-tab${hopState.hubTab === k ? ' active' : ''}" onclick="hopSetHubTab('${k}')">${label}</button>
    `).join('');
    renderHopHubTab();
  } catch (e) {
    if (body) body.innerHTML = `<p class="nx-oc-error">${foEscapeText(e.message)}</p>`;
  }
}

function hopSetHubTab(tab) {
  hopState.hubTab = tab;
  hopScrollMainToTop();
  const tabs = document.getElementById('hop-hub-tabs');
  if (tabs && hopState.hub) {
    const tabDefs = [
      ['overview', 'Overview'], ['meetings', 'Meetings'], ['samples', 'Samples'], ['vendors', 'Vendors'],
      ['quotations', 'Quotations'], ['orders', 'PO'], ['dispatches', 'Dispatch'], ['invoices', 'Invoices'],
      ['payments', 'Payments'], ['complaints', 'Complaints'], ['timeline', 'Timeline'],
    ];
    tabs.innerHTML = tabDefs.map(([k, label]) => `
      <button type="button" class="hop-tab${hopState.hubTab === k ? ' active' : ''}" onclick="hopSetHubTab('${k}')">${label}</button>
    `).join('');
  }
  renderHopHubTab();
}

function renderHopHubTab() {
  const hub = hopState.hub;
  const body = document.getElementById('hop-hub-body');
  if (!hub || !body) return;
  const p = hub.project || {};
  const tab = hopState.hubTab;
  if (tab === 'overview') {
    body.innerHTML = `
      <div class="hop-hub-overview">
        <div class="nx-card hop-form-card">
          <div class="hop-form-grid">
            <div><span class="nx-text-faint">Customer</span><div>${hopCell(hub.customer?.company || p.customer_company)}</div></div>
            <div><span class="nx-text-faint">Hotel / Site</span><div>${hopCell(p.hotel_name)}</div></div>
            <div><span class="nx-text-faint">Site Address</span><div>${hopCell(p.site_address)}</div></div>
            <div><span class="nx-text-faint">Architect</span><div>${hopCell(p.architect)}</div></div>
            <div><span class="nx-text-faint">Consultant</span><div>${hopCell(p.consultant)}</div></div>
            <div><span class="nx-text-faint">Stage</span><div>${hopCell(p.stage)}</div></div>
            <div><span class="nx-text-faint">Project Value</span><div>${hopMoney(p.project_value ?? p.expected_value)}</div></div>
            <div><span class="nx-text-faint">Probability</span><div>${hopCell(p.probability_pct)}%</div></div>
            <div><span class="nx-text-faint">Completion</span><div>${hopCell(p.completion_pct)}%</div></div>
            <div><span class="nx-text-faint">Next Milestone</span><div>${hopCell(p.next_milestone)}</div></div>
            <div><span class="nx-text-faint">Issues</span><div>${hopCell(p.issues)}</div></div>
            <div><span class="nx-text-faint">Assigned</span><div>${hopCell(p.assigned_to)}</div></div>
          </div>
        </div>
        <div class="hop-kpi-grid hop-kpi-grid-sm">
          ${renderHopKpiCard('Meetings', (hub.meetings || []).length, null)}
          ${renderHopKpiCard('Quotations', (hub.quotations || []).length, null)}
          ${renderHopKpiCard('Orders', (hub.orders || []).length, null)}
          ${renderHopKpiCard('Invoices', (hub.invoices || []).length, null)}
        </div>
      </div>`;
    return;
  }
  if (tab === 'timeline') {
    const rows = hub.timeline || [];
    body.innerHTML = hopTable(
      ['When', 'Type', 'Title', 'Detail'],
      rows.map((r) => `<tr>
        <td>${hopCell((r.activity_at || '').replace('T', ' ').slice(0, 16))}</td>
        <td>${hopCell(r.activity_type)}</td><td>${hopCell(r.title)}</td><td>${hopCell(r.detail)}</td>
      </tr>`).join(''),
    );
    return;
  }
  const map = {
    meetings: {
      headers: ['When', 'Title', 'Outcome', 'Next Action'],
      rows: (hub.meetings || []).map((r) => `<tr><td>${hopCell((r.scheduled_at || '').slice(0, 16))}</td><td>${hopCell(r.title)}</td><td>${hopCell(r.outcome)}</td><td>${hopCell(r.next_action)}</td></tr>`),
    },
    samples: {
      headers: ['Sample', 'Sent', 'Tracking', 'Approval'],
      rows: (hub.samples || []).map((r) => `<tr><td>${hopCell(r.sample_name)}</td><td>${hopCell(r.sent_at)}</td><td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.approval_status)}</td></tr>`),
    },
    vendors: {
      headers: ['Product', 'Vendor', 'Rate', 'Winner'],
      rows: (hub.vendor_comparisons || []).map((r) => `<tr><td>${hopCell(r.product_name)}</td><td>${hopCell(r.vendor_company)}</td><td>${hopMoney(r.rate)}</td><td>${r.is_winner ? '★' : '—'}</td></tr>`),
    },
    quotations: {
      headers: ['Quote', 'Ver', 'Value', 'Status'],
      rows: (hub.quotations || []).map((r) => `<tr><td>${hopCell(r.quote_no)}</td><td>${hopCell(r.version)}</td><td>${hopMoney(r.value)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
    orders: {
      headers: ['PO', 'Value', 'Production', 'Dispatch'],
      rows: (hub.orders || []).map((r) => `<tr><td>${hopCell(r.po_number)}</td><td>${hopMoney(r.order_value)}</td><td>${hopCell(r.production_status)}</td><td>${hopCell(r.dispatch_status)}</td></tr>`),
    },
    dispatches: {
      headers: ['Status', 'Tracking', 'Courier', 'Delivery'],
      rows: (hub.dispatches || []).map((r) => `<tr><td>${hopCell(r.status)}</td><td>${hopCell(r.tracking_number)}</td><td>${hopCell(r.courier)}</td><td>${hopCell(r.delivery_status)}</td></tr>`),
    },
    invoices: {
      headers: ['Invoice', 'Amount', 'Balance', 'Status'],
      rows: (hub.invoices || []).map((r) => `<tr><td>${hopCell(r.invoice_no)}</td><td>${hopMoney(r.amount)}</td><td>${hopMoney(r.balance)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
    payments: {
      headers: ['Paid At', 'Amount', 'Method'],
      rows: (hub.payments || []).map((r) => `<tr><td>${hopCell((r.paid_at || '').slice(0, 16))}</td><td>${hopMoney(r.amount)}</td><td>${hopCell(r.method)}</td></tr>`),
    },
    complaints: {
      headers: ['Date', 'Issue', 'Status'],
      rows: (hub.complaints || []).map((r) => `<tr><td>${hopCell(r.complaint_date)}</td><td>${hopCell(r.issue)}</td><td>${hopCell(r.status)}</td></tr>`),
    },
  };
  const block = map[tab];
  if (!block) {
    body.innerHTML = '<p class="nx-text-dim">Empty</p>';
    return;
  }
  body.innerHTML = hopTable(block.headers, block.rows.join(''));
}

async function hopChangeProjectStage() {
  const hub = hopState.hub;
  if (!hub?.project?.id) return;
  const stage = document.getElementById('hop-hub-stage')?.value;
  try {
    await hopApi(`/api/v1/hop/projects/${hub.project.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage }),
    });
    await loadHopProjectHub(hub.project.id);
  } catch (e) { alert(e.message); }
}

/* ---------- Forms ---------- */
async function hopShowForm(kind, editRow) {
  // Party create/edit uses Vyapar-style modal (Nexora theme).
  if (kind === 'customer' || kind === 'vendor') {
    hopOpenPartyEditModal(kind, editRow || null);
    return;
  }
  // Rate sheet form does not need CRM lookups — open immediately
  if (kind !== 'rate_sheet') {
    await hopEnsureLookups();
  }
  const slot = document.getElementById('hop-form-slot');
  if (!slot) {
    alert('Upload form area missing — refresh Vendor Compare (Ctrl+Shift+R).');
    return;
  }
  slot.classList.remove('hidden');
  hopState.contactEdit = null;
  if (editRow && editRow.id != null && (kind === 'customer' || kind === 'vendor')) {
    hopState.contactEdit = { kind, id: Number(editRow.id) };
  }
  const cancel = `hopState.contactEdit=null; hopState._leadFormAwaitingCustomer=false; hopCloseLeadProductsPicker(); document.getElementById('hop-form-slot').classList.add('hidden')`;

  if (kind === 'customer') {
    const row = editRow || {};
    slot.innerHTML = `
      <strong>${row.id ? 'Edit Customer' : 'New Customer'}</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Company *<input id="f-company" value="${foEscapeAttr(row.company || '')}" /></label>
        <label>Contact<input id="f-contact" value="${foEscapeAttr(row.contact_person || '')}" /></label>
        <label>Mobile<input id="f-mobile" value="${foEscapeAttr(row.mobile || '')}" /></label>
        <label>Email<input id="f-email" value="${foEscapeAttr(row.email || '')}" /></label>
        <label>City<input id="f-city" value="${foEscapeAttr(row.city || '')}" /></label>
        <label>Type<input id="f-type" placeholder="Hotel / Designer" value="${foEscapeAttr(row.customer_type || '')}" /></label>
        <label>Hotel Brand<input id="f-hotel" value="${foEscapeAttr(row.hotel_brand || '')}" /></label>
        <label>Architect<input id="f-architect" value="${foEscapeAttr(row.architect || '')}" /></label>
        <label>Consultant<input id="f-consultant" value="${foEscapeAttr(row.consultant || '')}" /></label>
        <label>Potential<input id="f-potential" type="number" value="${foEscapeAttr(row.annual_potential ?? '')}" /></label>
        <label>Rating A/B/C<input id="f-rating" value="${foEscapeAttr(row.potential_rating || '')}" /></label>
        <label>Assigned<input id="f-assigned" value="${foEscapeAttr(row.assigned_to || '')}" /></label>
        <label class="hop-form-span-2">Address<input id="f-address" value="${foEscapeAttr(row.address || '')}" /></label>
        <label>GST<input id="f-gst" value="${foEscapeAttr(row.gst_no || '')}" /></label>
        <label>PAN<input id="f-pan" value="${foEscapeAttr(row.pan || '')}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('customer')">${row.id ? 'Update' : 'Save'}</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'project') {
    slot.innerHTML = `
      <strong>New Project</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project Name *<input id="f-pname" /></label>
        <label>Customer<select id="f-pcustomer">${hopCustomerOptions()}</select></label>
        <label>Hotel<input id="f-photel" /></label>
        <label>Site Address<input id="f-psite" /></label>
        <label>Architect<input id="f-parch" /></label>
        <label>Consultant<input id="f-pcons" /></label>
        <label>Stage<select id="f-pstage">${hopStageOptions(HOP_PROJECT_STAGES, 'lead')}</select></label>
        <label>Value<input id="f-pvalue" type="number" /></label>
        <label>Probability %<input id="f-pprob" type="number" /></label>
        <label>Assigned<input id="f-passigned" /></label>
        <label>Next Milestone<input id="f-pmilestone" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('project')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'lead') {
    slot.innerHTML = `
      <strong>New Lead</strong>
      <p class="nx-text-dim" style="font-size:0.78rem;">Type a project name — a new project is created on save if needed.</p>
      <div class="hop-form-grid" style="margin-top:10px;">
        <div>
          <label>Customer
            <select id="f-lcustomer" onchange="hopLeadCustomerChange()">${hopCustomerOptions(null, { withAddNew: true })}</select>
          </label>
          <button type="button" class="hop-lead-add-party-btn" onclick="hopLeadAddNewParty()">+ Add new party</button>
        </div>
        <label class="hop-form-span-2">Project name
          <input id="f-lpname" placeholder="e.g. Holiday Inn Dwarka" />
        </label>
        <label>Source<input id="f-lsource" /></label>
        <label>Expected Value<input id="f-lvalue" type="number" /></label>
        <label>Priority<input id="f-lpriority" /></label>
        <label>Sales Person<input id="f-lassigned" /></label>
        <label>Stage<select id="f-lstage">${hopStageOptions(HOP_LEAD_STAGES, 'new_lead')}</select></label>
        <label>Probability %<input id="f-lprob" type="number" /></label>
        <label>Next Follow-up<input id="f-lfollow" type="date" /></label>
        <label>Expected Closure<input id="f-lclosure" type="date" /></label>
        <div class="hop-form-span-2 hop-prod-interest">
          <span>Products Interested</span>
          <div class="hop-prod-interest-box hop-prod-interest-box--trigger">
            <div id="f-lproducts-chips" class="hop-prod-interest-chips"></div>
            <button type="button" class="hop-prod-interest-open" onclick="hopOpenLeadProductsPicker()">
              <span>Search &amp; select products…</span>
              <span id="f-lproducts-count" class="hop-prod-interest-count">None selected</span>
            </button>
            <input type="hidden" id="f-lproducts" value="" />
          </div>
        </div>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('lead')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
    hopState._leadProducts = [];
    hopLeadProductsRenderChips();
    hopEnsureProductCatalogue();
  }
  if (kind === 'meeting') {
    const nowLocal = new Date();
    nowLocal.setMinutes(nowLocal.getMinutes() - nowLocal.getTimezoneOffset());
    slot.innerHTML = `
      <strong>New Meeting</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Title<input id="f-mtitle" value="Client Meeting" /></label>
        <label>When *<input id="f-mwhen" type="datetime-local" value="${nowLocal.toISOString().slice(0, 16)}" /></label>
        <label>Customer<select id="f-mcustomer">${hopCustomerOptions()}</select></label>
        <label>Project<select id="f-mproject">${hopProjectOptions()}</select></label>
        <label>Location<input id="f-mloc" /></label>
        <label>Agenda<input id="f-magenda" /></label>
        <label>Outcome<input id="f-moutcome" /></label>
        <label>Next Action<input id="f-mnext" /></label>
        <label>Follow-up<input id="f-mfollow" type="date" /></label>
        <label>Expected Value<input id="f-mvalue" type="number" /></label>
        <label>Probability %<input id="f-mprob" type="number" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('meeting')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'quotation') {
    slot.innerHTML = `
      <strong>New Quotation</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-qproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-qcustomer">${hopCustomerOptions()}</select></label>
        <label>Value<input id="f-qvalue" type="number" /></label>
        <label>Margin %<input id="f-qmargin" type="number" /></label>
        <label>Status<select id="f-qstatus"><option>draft</option><option>sent</option><option>negotiation</option></select></label>
        <label>Sales Person<input id="f-qsales" /></label>
        <label>Expected Closure<input id="f-qclosure" type="date" /></label>
        <label>Payment Terms<input id="f-qpay" /></label>
        <label class="hop-form-span-2">Notes<input id="f-qnotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('quotation')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'vendor') {
    const row = editRow || {};
    slot.innerHTML = `
      <strong>${row.id ? 'Edit Vendor' : 'New Vendor'}</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Company *<input id="f-vcompany" value="${foEscapeAttr(row.company || '')}" /></label>
        <label>Products<input id="f-vproducts" value="${foEscapeAttr(row.products || '')}" /></label>
        <label>GST<input id="f-vgst" value="${foEscapeAttr(row.gst_no || '')}" /></label>
        <label>Contact<input id="f-vcontact" value="${foEscapeAttr(row.contact_person || '')}" /></label>
        <label>Mobile<input id="f-vmobile" value="${foEscapeAttr(row.mobile || '')}" /></label>
        <label>Email<input id="f-vemail" value="${foEscapeAttr(row.email || '')}" /></label>
        <label>City<input id="f-vcity" value="${foEscapeAttr(row.city || '')}" /></label>
        <label>Rating<input id="f-vrating" type="number" step="0.1" value="${foEscapeAttr(row.rating ?? '')}" /></label>
        <label>Lead Time Days<input id="f-vlead" type="number" value="${foEscapeAttr(row.lead_time_days ?? '')}" /></label>
        <label>Payment Terms<input id="f-vpay" value="${foEscapeAttr(row.payment_terms || '')}" /></label>
        <label>On-time %<input id="f-vontime" type="number" value="${foEscapeAttr(row.on_time_pct ?? '')}" /></label>
        <label>Quality Rating<input id="f-vqual" type="number" step="0.1" value="${foEscapeAttr(row.quality_rating ?? '')}" /></label>
        <label>Certificates<input id="f-vcert" value="${foEscapeAttr(row.certificates || '')}" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('vendor')">${row.id ? 'Update' : 'Save'}</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'vendor_cmp') {
    slot.innerHTML = `
      <strong>Vendor Comparison Row</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project *<select id="f-vcproject">${hopProjectOptions()}</select></label>
        <label>Vendor *<select id="f-vcvendor">${hopVendorOptions()}</select></label>
        <label>Product<input id="f-vcproduct" /></label>
        <label>Rate<input id="f-vcrate" type="number" /></label>
        <label>Lead Time<input id="f-vclead" type="number" /></label>
        <label>MOQ<input id="f-vcmoq" /></label>
        <label>Quality<input id="f-vcqual" /></label>
        <label>Certification<input id="f-vccert" /></label>
        <label>Payment Terms<input id="f-vcpay" /></label>
        <label>Winner?<select id="f-vcwin"><option value="0">No</option><option value="1">Yes</option></select></label>
        <label class="hop-form-span-2">Recommendation<input id="f-vcrec" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('vendor_cmp')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'rate_sheet') {
    slot.innerHTML = `
      <strong>Add supplier rate sheet</strong>
      <p class="nx-text-dim" style="margin:6px 0 0;">Handwriting OCR stack: <strong>Gemini Vision</strong> → Azure Read → EasyOCR → PaddleOCR → RapidOCR. Sirf is file se rates — koi demo data nahi.</p>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label class="hop-form-span-2">Upload file *
          <input id="f-rs-file" type="file" onchange="hopOnRateFileSelected()" accept=".pdf,.doc,.docx,.rtf,.odt,.txt,.csv,.tsv,.xlsx,.xlsm,.xls,.ods,.jpg,.jpeg,.png,.bmp,.gif,.webp,.tif,.tiff,.heic,.heif,application/pdf,image/*,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/rtf" />
        </label>
        <label>Supplier name <span class="nx-text-dim">(auto)</span><input id="f-rs-supplier" placeholder="Auto from filename" /></label>
        <label>Source <span class="nx-text-dim">(auto)</span><select id="f-rs-source"><option value="manual">Manual</option><option value="pdf">PDF</option><option value="excel">Excel</option><option value="word">Word / WordPad</option><option value="image">Image / photo</option><option value="handwritten">Handwritten</option><option value="quote">Quote</option></select></label>
        <label class="hop-form-span-2">Title<input id="f-rs-title" placeholder="Auto from file" /></label>
        <label class="hop-form-span-2">Notes<input id="f-rs-notes" placeholder="Freight extra, advance, etc." /></label>
        <label class="hop-form-span-2">Rate lines (optional if file parses)
          <textarea id="f-rs-lines" rows="8" placeholder="Bedsheet | 110x112 | 715 | 5&#10;Duvet Cover | 110x114 | 1498 | 5&#10;Bath Towel | 30x60 | 432 | 5"></textarea>
        </label>
      </div>
      <p id="f-rs-upload-status" class="nx-text-dim" style="margin-top:8px;"></p>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopUploadRateSheet()">Upload &amp; save</button>
        <button type="button" class="nx-btn" onclick="hopSave('rate_sheet')">Save pasted lines</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'sample') {
    slot.innerHTML = `
      <strong>New Sample</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Sample Name *<input id="f-sname" /></label>
        <label>Project<select id="f-sproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-scustomer">${hopCustomerOptions()}</select></label>
        <label>Courier<input id="f-scourier" /></label>
        <label>Tracking<input id="f-strack" /></label>
        <label>Approval<select id="f-sapproval"><option>pending</option><option>approved</option><option>rejected</option></select></label>
        <label>Return Status<input id="f-sreturn" /></label>
        <label class="hop-form-span-2">Notes<input id="f-snotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('sample')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'product') {
    slot.innerHTML = `
      <strong>New Product</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Name *<input id="f-prname" /></label>
        <label>Code<input id="f-prcode" /></label>
        <label>Brand<input id="f-prbrand" /></label>
        <label>Category<input id="f-prcat" placeholder="Bed Linen / Fabric…" /></label>
        <label>Sell Price<input id="f-prsell" type="number" /></label>
        <label>Purchase Price<input id="f-prbuy" type="number" /></label>
        <label>Logistics<input id="f-prlog" type="number" /></label>
        <label>GST %<input id="f-prgst" type="number" /></label>
        <label>Commission %<input id="f-prcomm" type="number" /></label>
        <label>Stock<input id="f-prstock" type="number" /></label>
        <label>Vendor<select id="f-prvendor">${hopVendorOptions()}</select></label>
        <label>MOQ<input id="f-prmoq" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('product')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'order') {
    slot.innerHTML = `
      <strong>New Order / PO</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>PO Number<input id="f-opo" /></label>
        <label>Project<select id="f-oproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-ocustomer">${hopCustomerOptions()}</select></label>
        <label>Client Name<input id="f-oclient" /></label>
        <label>Order Value<input id="f-ovalue" type="number" /></label>
        <label>Vendor<select id="f-ovendor">${hopVendorOptions()}</select></label>
        <label>Supplier Text<input id="f-osupplier" /></label>
        <label>Expected Delivery<input id="f-odelivery" type="date" /></label>
        <label>Mark Won?<select id="f-owon"><option value="1">Yes</option><option value="0">No</option></select></label>
        <label>Type<select id="f-otype"><option value="customer_po">Customer PO</option><option value="vendor_po">Vendor PO</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('order')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'dispatch') {
    slot.innerHTML = `
      <strong>New Dispatch</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-dproject">${hopProjectOptions()}</select></label>
        <label>Order<select id="f-dorder">${hopOrderOptions()}</select></label>
        <label>Status<select id="f-dstatus"><option>ready</option><option>dispatched</option><option>in_transit</option><option>delivered</option></select></label>
        <label>Tracking<input id="f-dtrack" /></label>
        <label>Courier<input id="f-dcourier" /></label>
        <label>E-way Bill<input id="f-deway" /></label>
        <label>Docket<input id="f-ddocket" /></label>
        <label>Due Date<input id="f-ddue" type="date" /></label>
        <label>Installation Pending?<select id="f-dinstall"><option value="0">No</option><option value="1">Yes</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('dispatch')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'invoice') {
    slot.innerHTML = `
      <strong>New Invoice</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-iproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-icustomer">${hopCustomerOptions()}</select></label>
        <label>Amount *<input id="f-iamount" type="number" /></label>
        <label>GST Amount<input id="f-igst" type="number" /></label>
        <label>Due Date<input id="f-idue" type="date" /></label>
        <label>Paid Already<input id="f-ipaid" type="number" value="0" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('invoice')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'payment') {
    slot.innerHTML = `
      <strong>Record Payment</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Invoice *<select id="f-payinvoice">${hopInvoiceOptions()}</select></label>
        <label>Amount *<input id="f-payamount" type="number" /></label>
        <label>Method<input id="f-paymethod" placeholder="NEFT / Cheque / UPI" /></label>
        <label>Paid At<input id="f-payat" type="date" /></label>
        <label class="hop-form-span-2">Notes<input id="f-paynotes" /></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('payment')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }
  if (kind === 'complaint') {
    slot.innerHTML = `
      <strong>New Complaint</strong>
      <div class="hop-form-grid" style="margin-top:10px;">
        <label>Project<select id="f-cproject">${hopProjectOptions()}</select></label>
        <label>Customer<select id="f-ccustomer">${hopCustomerOptions()}</select></label>
        <label class="hop-form-span-2">Issue *<input id="f-cissue" /></label>
        <label>Assigned<input id="f-cassigned" /></label>
        <label>Status<select id="f-cstatus"><option>open</option><option>in_progress</option><option>resolved</option><option>closed</option></select></label>
      </div>
      <div class="hop-form-actions">
        <button type="button" class="nx-btn nx-btn-primary" onclick="hopSave('complaint')">Save</button>
        <button type="button" class="nx-btn" onclick="${cancel}">Cancel</button>
      </div>`;
  }

  // Bring form into the .hop-main scrollport (body is locked under HoP)
  try {
    requestAnimationFrame(() => {
      hopScrollIntoMain(slot, 8);
      const focusEl = slot.querySelector('input:not([type="file"]), select, textarea');
      if (focusEl) setTimeout(() => focusEl.focus(), 80);
    });
  } catch (_) { /* ignore */ }
}

async function hopSave(kind) {
  const map = {
    customer: {
      url: '/api/v1/hop/customers',
      view: 'customers',
      payload: () => ({
        company: document.getElementById('f-company')?.value,
        contact_person: document.getElementById('f-contact')?.value,
        mobile: document.getElementById('f-mobile')?.value,
        email: document.getElementById('f-email')?.value,
        city: document.getElementById('f-city')?.value,
        customer_type: document.getElementById('f-type')?.value,
        hotel_brand: document.getElementById('f-hotel')?.value,
        architect: document.getElementById('f-architect')?.value,
        consultant: document.getElementById('f-consultant')?.value,
        annual_potential: document.getElementById('f-potential')?.value,
        potential_rating: document.getElementById('f-rating')?.value,
        assigned_to: document.getElementById('f-assigned')?.value,
        address: document.getElementById('f-address')?.value,
        gst_no: document.getElementById('f-gst')?.value,
        pan: document.getElementById('f-pan')?.value,
      }),
    },
    project: {
      url: '/api/v1/hop/projects',
      view: 'projects',
      payload: () => ({
        project_name: document.getElementById('f-pname')?.value,
        customer_id: document.getElementById('f-pcustomer')?.value,
        hotel_name: document.getElementById('f-photel')?.value,
        site_address: document.getElementById('f-psite')?.value,
        architect: document.getElementById('f-parch')?.value,
        consultant: document.getElementById('f-pcons')?.value,
        stage: document.getElementById('f-pstage')?.value,
        project_value: document.getElementById('f-pvalue')?.value,
        probability_pct: document.getElementById('f-pprob')?.value,
        assigned_to: document.getElementById('f-passigned')?.value,
        next_milestone: document.getElementById('f-pmilestone')?.value,
      }),
    },
    lead: {
      url: '/api/v1/hop/leads',
      view: 'leads',
      payload: () => ({
        customer_id: document.getElementById('f-lcustomer')?.value,
        project_id: '',
        project_name: document.getElementById('f-lpname')?.value,
        source: document.getElementById('f-lsource')?.value,
        expected_value: document.getElementById('f-lvalue')?.value,
        priority: document.getElementById('f-lpriority')?.value,
        assigned_to: document.getElementById('f-lassigned')?.value,
        stage: document.getElementById('f-lstage')?.value,
        probability_pct: document.getElementById('f-lprob')?.value,
        next_follow_up: document.getElementById('f-lfollow')?.value,
        expected_closure_date: document.getElementById('f-lclosure')?.value,
        products_interested: document.getElementById('f-lproducts')?.value,
      }),
    },
    meeting: {
      url: '/api/v1/hop/meetings',
      view: 'meetings',
      payload: () => {
        const when = document.getElementById('f-mwhen')?.value;
        return {
          title: document.getElementById('f-mtitle')?.value,
          scheduled_at: when ? new Date(when).toISOString() : '',
          customer_id: document.getElementById('f-mcustomer')?.value,
          project_id: document.getElementById('f-mproject')?.value,
          location: document.getElementById('f-mloc')?.value,
          agenda: document.getElementById('f-magenda')?.value,
          outcome: document.getElementById('f-moutcome')?.value,
          next_action: document.getElementById('f-mnext')?.value,
          follow_up_at: document.getElementById('f-mfollow')?.value,
          expected_order_value: document.getElementById('f-mvalue')?.value,
          probability_pct: document.getElementById('f-mprob')?.value,
        };
      },
    },
    quotation: {
      url: '/api/v1/hop/quotations',
      view: 'sale_estimates',
      payload: () => ({
        project_id: document.getElementById('f-qproject')?.value,
        customer_id: document.getElementById('f-qcustomer')?.value,
        value: document.getElementById('f-qvalue')?.value,
        margin_pct: document.getElementById('f-qmargin')?.value,
        status: document.getElementById('f-qstatus')?.value,
        sales_person: document.getElementById('f-qsales')?.value,
        expected_closure_date: document.getElementById('f-qclosure')?.value,
        payment_terms: document.getElementById('f-qpay')?.value,
        notes: document.getElementById('f-qnotes')?.value,
      }),
    },
    vendor: {
      url: '/api/v1/hop/vendors',
      view: 'vendors',
      payload: () => ({
        company: document.getElementById('f-vcompany')?.value,
        products: document.getElementById('f-vproducts')?.value,
        gst_no: document.getElementById('f-vgst')?.value,
        contact_person: document.getElementById('f-vcontact')?.value,
        mobile: document.getElementById('f-vmobile')?.value,
        email: document.getElementById('f-vemail')?.value,
        city: document.getElementById('f-vcity')?.value,
        rating: document.getElementById('f-vrating')?.value,
        lead_time_days: document.getElementById('f-vlead')?.value,
        payment_terms: document.getElementById('f-vpay')?.value,
        on_time_pct: document.getElementById('f-vontime')?.value,
        quality_rating: document.getElementById('f-vqual')?.value,
        certificates: document.getElementById('f-vcert')?.value,
      }),
    },
    vendor_cmp: {
      url: '/api/v1/hop/vendor-comparisons',
      view: 'vendor_cmp',
      payload: () => ({
        project_id: document.getElementById('f-vcproject')?.value,
        vendor_id: document.getElementById('f-vcvendor')?.value,
        product_name: document.getElementById('f-vcproduct')?.value,
        rate: document.getElementById('f-vcrate')?.value,
        lead_time_days: document.getElementById('f-vclead')?.value,
        moq: document.getElementById('f-vcmoq')?.value,
        quality_note: document.getElementById('f-vcqual')?.value,
        certification: document.getElementById('f-vccert')?.value,
        payment_terms: document.getElementById('f-vcpay')?.value,
        is_winner: document.getElementById('f-vcwin')?.value,
        recommendation: document.getElementById('f-vcrec')?.value,
      }),
    },
    rate_sheet: {
      url: '/api/v1/hop/rate-sheets',
      view: 'vendor_cmp',
      payload: () => {
        const text = document.getElementById('f-rs-lines')?.value || '';
        const lines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean).map((line) => {
          const parts = line.split('|').map((p) => p.trim());
          if (parts.length >= 3) {
            return {
              product_name: parts[0],
              size: parts[1],
              rate: parts[2],
              gst_pct: parts[3] || 5,
            };
          }
          // fallback: "Bedsheet 110x112 715 +5%"
          const m = line.match(/^(.+?)\s+(\d{1,3}\s*[x×]\s*\d{1,3}|[Ll]|Free\s*Size)\s+(\d+(?:\.\d+)?)\s*(?:\+?\s*(\d+(?:\.\d+)?)\s*%?)?$/i);
          if (m) {
            return { product_name: m[1].trim(), size: m[2], rate: m[3], gst_pct: m[4] || 5 };
          }
          return null;
        }).filter(Boolean);
        return {
          supplier_name: document.getElementById('f-rs-supplier')?.value,
          title: document.getElementById('f-rs-title')?.value,
          source_type: document.getElementById('f-rs-source')?.value,
          notes: document.getElementById('f-rs-notes')?.value,
          lines,
        };
      },
    },
    sample: {
      url: '/api/v1/hop/samples',
      view: 'samples',
      payload: () => ({
        sample_name: document.getElementById('f-sname')?.value,
        project_id: document.getElementById('f-sproject')?.value,
        customer_id: document.getElementById('f-scustomer')?.value,
        courier: document.getElementById('f-scourier')?.value,
        tracking_number: document.getElementById('f-strack')?.value,
        approval_status: document.getElementById('f-sapproval')?.value,
        return_status: document.getElementById('f-sreturn')?.value,
        notes: document.getElementById('f-snotes')?.value,
      }),
    },
    product: {
      url: '/api/v1/hop/products',
      view: 'products',
      payload: () => ({
        name: document.getElementById('f-prname')?.value,
        code: document.getElementById('f-prcode')?.value,
        brand: document.getElementById('f-prbrand')?.value,
        category: document.getElementById('f-prcat')?.value,
        selling_price: document.getElementById('f-prsell')?.value,
        purchase_price: document.getElementById('f-prbuy')?.value,
        logistics_cost: document.getElementById('f-prlog')?.value,
        gst_pct: document.getElementById('f-prgst')?.value,
        commission_pct: document.getElementById('f-prcomm')?.value,
        stock_qty: document.getElementById('f-prstock')?.value,
        vendor_id: document.getElementById('f-prvendor')?.value,
        moq: document.getElementById('f-prmoq')?.value,
      }),
    },
    order: {
      url: '/api/v1/hop/orders',
      view: 'orders',
      payload: () => ({
        po_number: document.getElementById('f-opo')?.value,
        project_id: document.getElementById('f-oproject')?.value,
        customer_id: document.getElementById('f-ocustomer')?.value,
        client_name: document.getElementById('f-oclient')?.value,
        order_value: document.getElementById('f-ovalue')?.value,
        vendor_id: document.getElementById('f-ovendor')?.value,
        supplier: document.getElementById('f-osupplier')?.value,
        expected_delivery: document.getElementById('f-odelivery')?.value,
        mark_won: document.getElementById('f-owon')?.value === '1',
        order_type: document.getElementById('f-otype')?.value,
      }),
    },
    dispatch: {
      url: '/api/v1/hop/dispatches',
      view: 'dispatches',
      payload: () => ({
        project_id: document.getElementById('f-dproject')?.value,
        order_id: document.getElementById('f-dorder')?.value,
        status: document.getElementById('f-dstatus')?.value,
        tracking_number: document.getElementById('f-dtrack')?.value,
        courier: document.getElementById('f-dcourier')?.value,
        eway_bill: document.getElementById('f-deway')?.value,
        docket_number: document.getElementById('f-ddocket')?.value,
        due_date: document.getElementById('f-ddue')?.value,
        installation_pending: document.getElementById('f-dinstall')?.value,
        dispatched_at: document.getElementById('f-dstatus')?.value === 'dispatched' || document.getElementById('f-dstatus')?.value === 'in_transit'
          ? new Date().toISOString().slice(0, 10) : null,
      }),
    },
    invoice: {
      url: '/api/v1/hop/invoices',
      view: 'invoices',
      payload: () => ({
        project_id: document.getElementById('f-iproject')?.value,
        customer_id: document.getElementById('f-icustomer')?.value,
        amount: document.getElementById('f-iamount')?.value,
        gst_amount: document.getElementById('f-igst')?.value,
        due_date: document.getElementById('f-idue')?.value,
        paid_amount: document.getElementById('f-ipaid')?.value,
      }),
    },
    payment: {
      url: '/api/v1/hop/payments',
      view: 'payments',
      payload: () => ({
        invoice_id: document.getElementById('f-payinvoice')?.value,
        amount: document.getElementById('f-payamount')?.value,
        method: document.getElementById('f-paymethod')?.value,
        paid_at: document.getElementById('f-payat')?.value,
        notes: document.getElementById('f-paynotes')?.value,
      }),
    },
    complaint: {
      url: '/api/v1/hop/complaints',
      view: 'complaints',
      payload: () => ({
        project_id: document.getElementById('f-cproject')?.value,
        customer_id: document.getElementById('f-ccustomer')?.value,
        issue: document.getElementById('f-cissue')?.value,
        assigned_to: document.getElementById('f-cassigned')?.value,
        status: document.getElementById('f-cstatus')?.value,
      }),
    },
  };
  const cfg = map[kind];
  if (!cfg) return;
  const edit = hopState.contactEdit;
  const isEdit = edit && edit.kind === kind && edit.id;
  try {
    const url = isEdit ? `${cfg.url}/${edit.id}` : cfg.url;
    const method = isEdit ? 'PATCH' : 'POST';
    const body = cfg.payload();
    let saved;
    if (kind === 'customer' || kind === 'vendor') {
      saved = await hopCreatePartyWithDupConfirm(url, body, { method });
      if (saved === false) return;
      if (saved == null) return;
    } else {
      await hopApi(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    }
    hopState.contactEdit = null;
    hopState._leadFormAwaitingCustomer = false;
    hopCloseLeadProductsPicker();
    document.getElementById('hop-form-slot')?.classList.add('hidden');
    // invalidate caches
    hopState.customers = [];
    hopState.projects = [];
    hopState.vendors = [];
    hopState.invoices = [];
    hopState.orders = [];
    const nextView = (kind === 'customer' || kind === 'vendor')
      ? hopContactReturnView(kind === 'vendor' ? 'vendors' : 'customers')
      : cfg.view;
    openHopView(nextView);
  } catch (e) {
    alert(e.message);
  }
}

function hopFilterModule(view) {
  hopState.search[view] = document.getElementById('hop-q')?.value || '';
  hopDebouncedReload(view);
}

window.loadHopExecutiveSnapshot = loadHopExecutiveSnapshot;
window.openHopView = openHopView;
window.hopGoBack = hopGoBack;
window.hopToggleNavFold = hopToggleNavFold;
window.openHopProjectHub = openHopProjectHub;
window.hopDebouncedReload = hopDebouncedReload;
window.hopShowForm = hopShowForm;
window.hopOpenDeal = hopOpenDeal;
window.hopCloseDealDetail = hopCloseDealDetail;
window.hopDealAction = hopDealAction;
window.hopShowDealForm = hopShowDealForm;
window.hopCloseDealFormModal = hopCloseDealFormModal;
window.hopEditDeal = hopEditDeal;
window.hopSaveDeal = hopSaveDeal;
window.hopDeleteDeal = hopDeleteDeal;
window.hopLeadCustomerChange = hopLeadCustomerChange;
window.hopLeadAddNewParty = hopLeadAddNewParty;
window.hopOpenLeadProductsPicker = hopOpenLeadProductsPicker;
window.hopCloseLeadProductsPicker = hopCloseLeadProductsPicker;
window.hopLeadProductsFillPicker = hopLeadProductsFillPicker;
window.hopLeadProductsAdd = hopLeadProductsAdd;
window.hopLeadProductsRemove = hopLeadProductsRemove;
window.hopSave = hopSave;
window.hopPatchLead = hopPatchLead;
window.hopPatchQuote = hopPatchQuote;
window.hopReviseQuote = hopReviseQuote;
window.hopPatchOrder = hopPatchOrder;
window.hopSaveTarget = hopSaveTarget;
window.hopSetHubTab = hopSetHubTab;
window.hopChangeProjectStage = hopChangeProjectStage;
window.hopFilterModule = hopFilterModule;
window.hopSeedRateSamples = hopSeedRateSamples;
window.hopDeleteRateSheet = hopDeleteRateSheet;
window.hopUploadRateSheet = hopUploadRateSheet;
window.hopOnRateFileSelected = hopOnRateFileSelected;
window.hopClearRateQuote = hopClearRateQuote;
window.hopAddToRateCart = hopAddToRateCart;
window.hopAddBestToRateCart = hopAddBestToRateCart;
window.hopRemoveRateCartItem = hopRemoveRateCartItem;
window.hopUpdateRateCartQty = hopUpdateRateCartQty;
window.hopToggleRateCartExpand = hopToggleRateCartExpand;
window.hopPlaceRateCartOrders = hopPlaceRateCartOrders;
window.hopToggleSelectAllRates = hopToggleSelectAllRates;
window.hopClearSingleProduct = hopClearSingleProduct;
window.hopClearSingleRate = hopClearSingleRate;
window.hopClearSelectedRates = hopClearSelectedRates;
window.hopClearAllRates = hopClearAllRates;
window.hopClearSheetRates = hopClearSheetRates;
window.hopApplyRateMatrixFilters = hopApplyRateMatrixFilters;
window.hopResetRateFilters = hopResetRateFilters;
window.hopToggleContactDetails = hopToggleContactDetails;
window.hopToggleContactSelectMode = hopToggleContactSelectMode;
window.hopToggleContactSelected = hopToggleContactSelected;
window.hopSelectAllContacts = hopSelectAllContacts;
window.hopDeleteContact = hopDeleteContact;
window.hopEditContact = hopEditContact;
window.hopBulkDeleteContacts = hopBulkDeleteContacts;
window.hopCloseContactActionMenu = hopCloseContactActionMenu;
window.hopOpenContactDetail = hopOpenContactDetail;
window.hopCloseContactDetail = hopCloseContactDetail;
window.hopOpenAddPartyChooser = hopOpenAddPartyChooser;
window.hopCloseAddPartyChooser = hopCloseAddPartyChooser;
window.hopAddPartyManual = hopAddPartyManual;
window.hopAddPartyViaScan = hopAddPartyViaScan;
window.hopOpenPartyScanModal = hopOpenPartyScanModal;
window.hopClosePartyScanModal = hopClosePartyScanModal;
window.hopOpenPartyEditModal = hopOpenPartyEditModal;
window.hopClosePartyEditModal = hopClosePartyEditModal;
window.hopPartyModalSetTab = hopPartyModalSetTab;
window.hopPartyGstCheck = hopPartyGstCheck;
window.hopPartyFetchGstDetails = hopPartyFetchGstDetails;
window.hopSavePartyModal = hopSavePartyModal;
window.hopCreatePartyWithDupConfirm = hopCreatePartyWithDupConfirm;
window.hopPartyLiveDupCheck = hopPartyLiveDupCheck;
window.hopPartyNameOnInput = hopPartyNameOnInput;
window.hopDeleteFromPartyModal = hopDeleteFromPartyModal;
window.hopPartySetCreditLimitMode = hopPartySetCreditLimitMode;
window.hopPartyCopyBillingToShipping = hopPartyCopyBillingToShipping;
window.hopPartyGroupOpenMenu = hopPartyGroupOpenMenu;
window.hopPartyGroupCloseMenu = hopPartyGroupCloseMenu;
window.hopPartyGroupOnInput = hopPartyGroupOnInput;
window.hopPartyGroupSelect = hopPartyGroupSelect;
window.hopPartyGroupCreateNew = hopPartyGroupCreateNew;
window.hopResetVisitingCardReview = hopResetVisitingCardReview;
window.hopFilterListTable = hopFilterListTable;
window.hopInvoicePartyOpenMenu = hopInvoicePartyOpenMenu;
window.hopInvoicePartyCloseMenu = hopInvoicePartyCloseMenu;
window.hopInvoicePartyToggle = hopInvoicePartyToggle;
window.hopInvoicePartyOnInput = hopInvoicePartyOnInput;
window.hopInvoicePartySelect = hopInvoicePartySelect;
window.hopInvoiceOnPeriodChange = hopInvoiceOnPeriodChange;
window.hopInvoiceApplyFilters = hopInvoiceApplyFilters;
window.hopInvoiceResetFilters = hopInvoiceResetFilters;
window.hopInvoiceExportCsv = hopInvoiceExportCsv;
window.hopSelectParty = hopSelectParty;
window.hopFilterParties = hopFilterParties;
window.hopOpenPartyTxnDetail = hopOpenPartyTxnDetail;
window.hopOpenSaleDocPreview = hopOpenSaleDocPreview;
window.hopDeleteManualDoc = hopDeleteManualDoc;
window.hopDownloadDocPreview = hopDownloadDocPreview;
window.hopShowInvRowContextMenu = hopShowInvRowContextMenu;
window.hopCloseInvRowContextMenu = hopCloseInvRowContextMenu;
window.hopClosePartyTxnDetail = hopClosePartyTxnDetail;
window.hopOpenPartyTxnInModule = hopOpenPartyTxnInModule;
window.hopPrintPartyTxnPreview = hopPrintPartyTxnPreview;
window.hopPreviewVyaparBackup = hopPreviewVyaparBackup;
window.hopRunVyaparImport = hopRunVyaparImport;
window.hopPickVyaparBackup = hopPickVyaparBackup;
window.hopRunWipeData = hopRunWipeData;
