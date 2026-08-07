/* Year → Month → Day date picker — manual type + dropdown pick. */
(function (global) {
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const MONTH_INDEX = Object.fromEntries(
    MONTHS.map((m, i) => [m.toLowerCase(), i + 1]).concat(
      ['january','february','march','april','may','june','july','august','september','october','november','december']
        .map((m, i) => [m, i + 1])
    )
  );

  function todayIso() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function esc(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function parseIso(iso) {
    const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    const y = Number(m[1]);
    const mo = Number(m[2]);
    const d = Number(m[3]);
    if (mo < 1 || mo > 12 || d < 1 || d > daysInMonth(y, mo)) return null;
    return { y, mo, d };
  }

  function formatLabel(iso) {
    const p = parseIso(iso);
    if (!p) return '';
    return `${String(p.d).padStart(2, '0')} ${MONTHS[p.mo - 1]} ${p.y}`;
  }

  function daysInMonth(y, mo) {
    return new Date(y, mo, 0).getDate();
  }

  function toIso(y, mo, d) {
    return `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  /** Accept ISO, DD/MM/YYYY, DD-MM-YYYY, DD MMM YYYY */
  function parseFlexible(raw) {
    const text = String(raw || '').trim();
    if (!text) return null;
    const iso = parseIso(text);
    if (iso) return iso;
    let m = text.match(/^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{4})$/);
    if (m) {
      const d = Number(m[1]);
      const mo = Number(m[2]);
      const y = Number(m[3]);
      if (mo >= 1 && mo <= 12 && d >= 1 && d <= daysInMonth(y, mo)) return { y, mo, d };
    }
    m = text.match(/^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$/);
    if (m) {
      const d = Number(m[1]);
      const mo = MONTH_INDEX[m[2].toLowerCase()];
      const y = Number(m[3]);
      if (mo && d >= 1 && d <= daysInMonth(y, mo)) return { y, mo, d };
    }
    return null;
  }

  function closeAll(exceptRoot) {
    document.querySelectorAll('.nx-ymd.is-open').forEach((el) => {
      if (exceptRoot && el === exceptRoot) return;
      el.classList.remove('is-open');
      el.querySelector('.nx-ymd-panel')?.classList.add('hidden');
    });
  }

  function syncLabel(inputId) {
    const input = document.getElementById(inputId);
    const root = document.querySelector(`.nx-ymd[data-nx-ymd-input="${inputId}"]`);
    if (!input || !root) return;
    const typed = root.querySelector('.nx-ymd-input');
    if (typed) {
      typed.value = formatLabel(input.value) || input.value || '';
      typed.classList.toggle('is-invalid', !!(typed.value.trim() && !parseFlexible(typed.value) && !parseIso(input.value)));
    }
  }

  function mount(rootOrInputId) {
    let root = null;
    let input = null;
    if (typeof rootOrInputId === 'string') {
      root = document.querySelector(`.nx-ymd[data-nx-ymd-input="${rootOrInputId}"]`);
      input = document.getElementById(rootOrInputId);
    } else if (rootOrInputId?.classList?.contains('nx-ymd')) {
      root = rootOrInputId;
      input = document.getElementById(root.getAttribute('data-nx-ymd-input') || '');
    }
    if (!root || !input) return;
    if (root.dataset.mounted === '1') {
      syncLabel(input.id);
      return;
    }
    root.dataset.mounted = '1';
    if (input.type !== 'hidden') input.type = 'hidden';
    const state = { step: 'year', y: null, mo: null };

    root.innerHTML = `
      <div class="nx-ymd-combo">
        <input type="text" class="nx-ymd-input" inputmode="text" autocomplete="off"
          placeholder="DD/MM/YYYY or pick ▾" aria-label="Date (type or pick)"
          value="${esc(formatLabel(input.value) || '')}" />
        <button type="button" class="nx-ymd-pick-btn" aria-haspopup="dialog" title="Pick date">▾</button>
      </div>
      <div class="nx-ymd-panel hidden" role="dialog" aria-label="Pick date">
        <div class="nx-ymd-nav">
          <button type="button" class="nx-ymd-back hidden" aria-label="Back">←</button>
          <div class="nx-ymd-title">Select year</div>
        </div>
        <div class="nx-ymd-body"></div>
      </div>
    `;

    const typed = root.querySelector('.nx-ymd-input');
    const pickBtn = root.querySelector('.nx-ymd-pick-btn');
    const panel = root.querySelector('.nx-ymd-panel');
    const body = root.querySelector('.nx-ymd-body');
    const titleEl = root.querySelector('.nx-ymd-title');
    const backBtn = root.querySelector('.nx-ymd-back');

    function applyParsed(parsed, rewriteDisplay) {
      if (!parsed) return false;
      input.value = toIso(parsed.y, parsed.mo, parsed.d);
      if (rewriteDisplay && typed) typed.value = formatLabel(input.value);
      typed?.classList.remove('is-invalid');
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    }

    function commitTyped() {
      const raw = (typed?.value || '').trim();
      if (!raw) {
        input.value = '';
        typed?.classList.remove('is-invalid');
        return;
      }
      const parsed = parseFlexible(raw);
      if (!applyParsed(parsed, true)) {
        typed?.classList.add('is-invalid');
      }
    }

    function setIso(y, mo, d) {
      applyParsed({ y, mo, d }, true);
    }

    function render() {
      body.innerHTML = '';
      backBtn.classList.toggle('hidden', state.step === 'year');
      if (state.step === 'year') {
        titleEl.textContent = '1 · Select year';
        const nowY = new Date().getFullYear();
        const grid = document.createElement('div');
        grid.className = 'nx-ymd-grid nx-ymd-grid--years';
        for (let y = nowY + 2; y >= nowY - 15; y -= 1) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'nx-ymd-cell';
          if (y === nowY) btn.classList.add('is-current');
          const parsed = parseIso(input.value);
          if (parsed && parsed.y === y) btn.classList.add('is-selected');
          btn.textContent = String(y);
          btn.addEventListener('click', () => {
            state.y = y;
            state.mo = null;
            state.step = 'month';
            render();
          });
          grid.appendChild(btn);
        }
        body.appendChild(grid);
        return;
      }
      if (state.step === 'month') {
        titleEl.textContent = `2 · Select month · ${state.y}`;
        const grid = document.createElement('div');
        grid.className = 'nx-ymd-grid nx-ymd-grid--months';
        MONTHS.forEach((name, idx) => {
          const mo = idx + 1;
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'nx-ymd-cell';
          const parsed = parseIso(input.value);
          if (parsed && parsed.y === state.y && parsed.mo === mo) btn.classList.add('is-selected');
          btn.textContent = name;
          btn.addEventListener('click', () => {
            state.mo = mo;
            state.step = 'day';
            render();
          });
          grid.appendChild(btn);
        });
        body.appendChild(grid);
        return;
      }
      titleEl.textContent = `3 · Select date · ${MONTHS[state.mo - 1]} ${state.y}`;
      const days = daysInMonth(state.y, state.mo);
      const firstDow = new Date(state.y, state.mo - 1, 1).getDay();
      const grid = document.createElement('div');
      grid.className = 'nx-ymd-grid nx-ymd-grid--days';
      ['S', 'M', 'T', 'W', 'T', 'F', 'S'].forEach((wd) => {
        const h = document.createElement('div');
        h.className = 'nx-ymd-wd';
        h.textContent = wd;
        grid.appendChild(h);
      });
      for (let i = 0; i < firstDow; i += 1) {
        const blank = document.createElement('div');
        blank.className = 'nx-ymd-blank';
        grid.appendChild(blank);
      }
      const parsed = parseIso(input.value);
      const today = parseIso(todayIso());
      for (let d = 1; d <= days; d += 1) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'nx-ymd-cell';
        if (today && today.y === state.y && today.mo === state.mo && today.d === d) btn.classList.add('is-current');
        if (parsed && parsed.y === state.y && parsed.mo === state.mo && parsed.d === d) btn.classList.add('is-selected');
        btn.textContent = String(d);
        btn.addEventListener('click', () => {
          setIso(state.y, state.mo, d);
          closePanel();
        });
        grid.appendChild(btn);
      }
      body.appendChild(grid);
    }

    function openPanel() {
      closeAll(root);
      commitTyped();
      const parsed = parseIso(input.value);
      state.step = 'year';
      state.y = parsed?.y || null;
      state.mo = parsed?.mo || null;
      root.classList.add('is-open');
      panel.classList.remove('hidden');
      render();
    }

    function closePanel() {
      root.classList.remove('is-open');
      panel.classList.add('hidden');
    }

    pickBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (root.classList.contains('is-open')) closePanel();
      else openPanel();
    });
    typed.addEventListener('click', (e) => e.stopPropagation());
    typed.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        commitTyped();
        typed.blur();
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        openPanel();
      }
    });
    typed.addEventListener('blur', () => commitTyped());
    typed.addEventListener('input', () => {
      typed.classList.remove('is-invalid');
      const parsed = parseFlexible(typed.value);
      if (parsed) {
        input.value = toIso(parsed.y, parsed.mo, parsed.d);
      }
    });
    backBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (state.step === 'day') {
        state.step = 'month';
        render();
      } else if (state.step === 'month') {
        state.step = 'year';
        state.mo = null;
        render();
      }
    });
    panel.addEventListener('click', (e) => e.stopPropagation());
  }

  function init(scope) {
    const rootEl = scope || document;
    rootEl.querySelectorAll('.nx-ymd[data-nx-ymd-input]').forEach((el) => mount(el));
    rootEl.querySelectorAll('input[type="date"]').forEach((input) => {
      if (!input.id) input.id = `nx-ymd-auto-${Math.random().toString(36).slice(2, 9)}`;
      if (document.querySelector(`.nx-ymd[data-nx-ymd-input="${input.id}"]`)) {
        mount(input.id);
        return;
      }
      const wrap = document.createElement('div');
      wrap.className = 'nx-ymd';
      wrap.setAttribute('data-nx-ymd-input', input.id);
      input.insertAdjacentElement('afterend', wrap);
      const iso = (input.value || '').slice(0, 10);
      input.type = 'hidden';
      if (iso) input.value = iso;
      mount(input.id);
    });
  }

  document.addEventListener('click', () => closeAll());
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeAll();
  });

  global.NxYmdPicker = { init, mount, syncLabel, todayIso, closeAll, parseFlexible };
  global.initNxYmdPickers = init;
  global.syncNxYmdPicker = syncLabel;
})(window);
