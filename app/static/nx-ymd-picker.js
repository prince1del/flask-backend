/* Year → Month → Day date picker (replaces native <input type="date"> calendar). */
(function (global) {
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

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
    return { y: Number(m[1]), mo: Number(m[2]), d: Number(m[3]) };
  }

  function formatLabel(iso) {
    const p = parseIso(iso);
    if (!p) return 'Select date';
    return `${String(p.d).padStart(2, '0')} ${MONTHS[p.mo - 1]} ${p.y}`;
  }

  function daysInMonth(y, mo) {
    return new Date(y, mo, 0).getDate();
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
    const label = root.querySelector('.nx-ymd-label');
    if (label) label.textContent = formatLabel(input.value);
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
    const state = { step: 'year', y: null, mo: null };

    root.innerHTML = `
      <button type="button" class="nx-ymd-trigger" aria-haspopup="dialog">
        <span class="nx-ymd-label">${esc(formatLabel(input.value))}</span>
        <span class="nx-ymd-caret" aria-hidden="true">▾</span>
      </button>
      <div class="nx-ymd-panel hidden" role="dialog" aria-label="Pick date">
        <div class="nx-ymd-nav">
          <button type="button" class="nx-ymd-back hidden" aria-label="Back">←</button>
          <div class="nx-ymd-title">Select year</div>
        </div>
        <div class="nx-ymd-body"></div>
      </div>
    `;

    const trigger = root.querySelector('.nx-ymd-trigger');
    const panel = root.querySelector('.nx-ymd-panel');
    const body = root.querySelector('.nx-ymd-body');
    const titleEl = root.querySelector('.nx-ymd-title');
    const backBtn = root.querySelector('.nx-ymd-back');

    function setIso(y, mo, d) {
      input.value = `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
      syncLabel(input.id);
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('input', { bubbles: true }));
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

    trigger.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (root.classList.contains('is-open')) closePanel();
      else openPanel();
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

  global.NxYmdPicker = { init, mount, syncLabel, todayIso, closeAll };
  global.initNxYmdPickers = init;
  global.syncNxYmdPicker = syncLabel;
})(window);
