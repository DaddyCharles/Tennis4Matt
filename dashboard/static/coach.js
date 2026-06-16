/* Ivan — shared front-end helpers (weather, modals, lessons). */

const Coach = (() => {
  function money(n) {
    const v = Number(n || 0);
    return '$' + v.toFixed(2);
  }

  function toast(msg, kind) {
    let el = document.getElementById('coach-toast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'coach-toast';
      el.className = 'coach-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'coach-toast show ' + (kind || 'info');
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.className = 'coach-toast'; }, 3000);
  }

  function confirm(opts) {
    opts = opts || {};
    const title = opts.title || 'Are you sure?';
    const message = opts.message || '';
    const confirmText = opts.confirmText || 'Confirm';
    const cancelText = opts.cancelText || 'Cancel';
    const danger = opts.danger !== false;
    return new Promise((resolve) => {
      let ov = document.getElementById('coach-confirm');
      if (ov) ov.remove();
      ov = document.createElement('div');
      ov.id = 'coach-confirm';
      ov.className = 'modal-overlay confirm-overlay open';
      ov.innerHTML = `
        <div class="modal-box confirm-box" role="alertdialog" aria-modal="true">
          <div class="confirm-icon ${danger ? 'danger' : ''}"><i class="ti ${danger ? 'ti-alert-triangle' : 'ti-help-circle'}"></i></div>
          <h2 class="confirm-title"></h2>
          <p class="confirm-message"></p>
          <div class="confirm-actions">
            <button class="btn btn-secondary" data-act="cancel"></button>
            <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok"></button>
          </div>
        </div>`;
      ov.querySelector('.confirm-title').textContent = title;
      ov.querySelector('.confirm-message').textContent = message;
      ov.querySelector('[data-act="cancel"]').textContent = cancelText;
      ov.querySelector('[data-act="ok"]').textContent = confirmText;
      document.body.appendChild(ov);
      const close = (val) => {
        ov.classList.remove('open');
        document.removeEventListener('keydown', onKey);
        setTimeout(() => ov.remove(), 200);
        resolve(val);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') close(false);
        if (e.key === 'Enter') close(true);
      };
      ov.querySelector('[data-act="cancel"]').onclick = () => close(false);
      ov.querySelector('[data-act="ok"]').onclick = () => close(true);
      ov.addEventListener('click', (e) => { if (e.target === ov) close(false); });
      document.addEventListener('keydown', onKey);
      setTimeout(() => { const b = ov.querySelector('[data-act="ok"]'); if (b) b.focus(); }, 30);
    });
  }

  /* Disable a button while an async action runs (prevents double-click). */
  async function guard(btnOrEvent, fn) {
    let btn = btnOrEvent;
    if (btnOrEvent && btnOrEvent.currentTarget) btn = btnOrEvent.currentTarget;
    if (btn && btn.dataset && btn.dataset.busy) return;
    if (btn) { btn.dataset.busy = '1'; btn.disabled = true; btn.classList.add('loading'); }
    try {
      return await fn();
    } finally {
      if (btn) { delete btn.dataset.busy; btn.disabled = false; btn.classList.remove('loading'); }
    }
  }

  async function api(url, opts) {
    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      throw new Error('Something went wrong — please try again');
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-json */ }
    if (!res.ok) throw new Error(data.message || 'Something went wrong — please try again');
    return data;
  }

  async function getJSON(url) { return api(url); }
  async function postJSON(url, body) {
    return api(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }
  async function del(url) { return api(url, { method: 'DELETE' }); }

  function initials(name) {
    return (name || '?').split(' ').filter(Boolean).slice(0, 2)
      .map(p => p[0].toUpperCase()).join('');
  }

  function to12h(hhmm) {
    if (!hhmm) return '';
    const [h, m] = hhmm.split(':').map(Number);
    const ap = h >= 12 ? 'PM' : 'AM';
    const hr = ((h + 11) % 12) + 1;
    return `${hr}:${String(m).padStart(2, '0')} ${ap}`;
  }

  function addMinutes(hhmm, mins) {
    const [h, m] = hhmm.split(':').map(Number);
    const total = h * 60 + m + mins;
    const nh = Math.floor(total / 60) % 24;
    const nm = total % 60;
    return `${String(nh).padStart(2, '0')}:${String(nm).padStart(2, '0')}`;
  }

  function blocksLabel(blocks) {
    const mins = blocks * 30;
    const h = Math.floor(mins / 60), m = mins % 60;
    const parts = [];
    if (h) parts.push(h + ' hour' + (h !== 1 ? 's' : ''));
    if (m) parts.push(m + ' minutes');
    return parts.join(' ') || '0 minutes';
  }

  /* ---- Add Lesson modal ------------------------------------------------ */
  let _students = [];
  let _selectedDuration = 60;
  let _priceTouched = false;
  let _recurWeeks = 10;

  async function ensureStudents() {
    if (_students.length) return _students;
    const data = await getJSON('/api/students');
    _students = data.students || [];
    return _students;
  }

  function formatDuration(mins) {
    const h = Math.floor(mins / 60), m = mins % 60;
    if (h && m) return `${h}h ${m}m`;
    if (h) return h === 1 ? '1 hour' : `${h} hours`;
    return `${m} min`;
  }

  function durationPrices() {
    const dp = window.DURATION_PRICES;
    return (dp && Object.keys(dp).length) ? dp : { 30: 45, 45: 65, 60: 80, 90: 110, 120: 140 };
  }

  function updateLessonPrice(mins) {
    const priceEl = document.getElementById('lesson-price');
    if (!priceEl || _priceTouched) return;
    const prices = durationPrices();
    if (prices[mins] != null) priceEl.value = prices[mins];
  }

  function setDuration(mins) {
    _selectedDuration = mins;
    const hidden = document.getElementById('lesson-duration');
    if (hidden) hidden.value = mins;
    const disp = document.getElementById('dur-display');
    if (disp) disp.textContent = formatDuration(mins);
    document.querySelectorAll('#dur-quick .dur-pill').forEach(p =>
      p.classList.toggle('active', Number(p.dataset.mins) === mins));
    updateLessonPrice(mins);
    syncRepeatEcho();
  }

  function stepDuration(delta) {
    let mins = _selectedDuration + delta;
    mins = Math.max(15, Math.min(300, mins));
    setDuration(mins);
  }

  function renderPricePresets() {
    const wrap = document.getElementById('price-presets');
    if (!wrap) return;
    const presets = window.PRICE_PRESETS || [];
    wrap.innerHTML = presets.map(p =>
      `<button type="button" class="price-pill" data-amount="${p.amount}">${p.name} $${p.amount}</button>`).join('');
    wrap.querySelectorAll('.price-pill').forEach(btn => {
      btn.onclick = () => {
        const priceEl = document.getElementById('lesson-price');
        if (priceEl) priceEl.value = btn.dataset.amount;
        _priceTouched = true;
      };
    });
  }

  function onStudentChange(val) {
    const box = document.getElementById('new-student-fields');
    if (box) box.style.display = val === '__new__' ? 'block' : 'none';
    if (val === '__new__') attachPhoneValidation('new-student-phone');
  }

  function currentLessonTimeDisplay() {
    const t = (document.getElementById('lesson-time') || {}).value || '09:00';
    return to12h(t);
  }

  function syncRepeatEcho() {
    const rt = document.getElementById('repeat-time');
    const rd = document.getElementById('repeat-dur');
    if (rt) rt.textContent = currentLessonTimeDisplay();
    if (rd) rd.textContent = formatDuration(_selectedDuration);
    renderRecur();
  }

  function setWeeks(n) { _recurWeeks = n; renderRecur(); }
  function stepWeeks(delta) {
    const base = _recurWeeks || 1;
    _recurWeeks = Math.max(1, Math.min(52, base + delta));
    renderRecur();
  }
  function renderRecur() {
    const hidden = document.getElementById('recur-weeks');
    if (hidden) hidden.value = _recurWeeks;
    const label = document.getElementById('recur-weeks-label');
    if (label) label.textContent = _recurWeeks === 0
      ? 'Ongoing' : `${_recurWeeks} week${_recurWeeks > 1 ? 's' : ''}`;
    document.querySelectorAll('.rw-chip').forEach(c => {
      const m = (c.getAttribute('onclick') || '').match(/-?\d+/);
      c.classList.toggle('active', !!m && Number(m[0]) === _recurWeeks);
    });
    const confirm = document.getElementById('recur-confirm');
    if (!confirm) return;
    const dateVal = (document.getElementById('lesson-date') || {}).value;
    const d = dpParseISO(dateVal);
    if (!d) { confirm.textContent = ''; return; }
    const time = currentLessonTimeDisplay();
    const dayName = d.toLocaleDateString('en-AU', { weekday: 'long' });
    const dayEl = document.getElementById('repeat-day');
    if (dayEl) dayEl.textContent = dayName;
    if (_recurWeeks === 0) {
      confirm.textContent = `Repeats every ${dayName} at ${time}, ongoing (you can stop anytime).`;
    } else {
      const end = new Date(d);
      end.setDate(end.getDate() + (_recurWeeks - 1) * 7);
      const endStr = end.toLocaleDateString('en-AU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
      confirm.textContent = `This will create ${_recurWeeks} lesson${_recurWeeks > 1 ? 's' : ''}, every ${dayName} at ${time}, through to ${endStr}.`;
    }
  }

  async function openAddLesson(prefill) {
    prefill = prefill || {};
    await ensureStudents();
    const modal = document.getElementById('lesson-modal');
    if (!modal) return;
    const sel = document.getElementById('lesson-student');
    sel.innerHTML =
      '<option value="__new__">+ Add new student</option>' +
      '<option value="">— Select existing —</option>' +
      _students.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    sel.value = prefill.student_id || '';
    onStudentChange(sel.value);

    const nsName = document.getElementById('new-student-name');
    if (nsName) nsName.value = '';
    const nsPhone = document.getElementById('new-student-phone');
    if (nsPhone) nsPhone.value = '';
    const nsLevel = document.getElementById('new-student-level');
    if (nsLevel) nsLevel.value = 'Beginner';

    setDatePickerValue('lesson-date', ('date' in prefill) ? prefill.date : new Date().toISOString().slice(0, 10));
    setTimePickerValue('lesson-time', ('start_time' in prefill) ? prefill.start_time : '09:00');

    let dur = 60;
    if (prefill.duration_minutes) dur = Number(prefill.duration_minutes);
    else if (prefill.blocks) dur = Number(prefill.blocks) * 30;
    _priceTouched = false;
    document.querySelectorAll('#dur-quick .dur-pill').forEach(p => {
      p.onclick = () => setDuration(Number(p.dataset.mins));
    });
    setDuration(dur);

    renderPricePresets();
    const priceEl = document.getElementById('lesson-price');
    if (prefill.price != null && prefill.price !== '') {
      priceEl.value = prefill.price;
      _priceTouched = true;
    }
    priceEl.oninput = () => { _priceTouched = true; };

    document.getElementById('lesson-notes').value = prefill.notes || '';
    _recurWeeks = 10;
    const rec = document.getElementById('lesson-recurring');
    if (rec) { rec.checked = false; toggleRecurring(); }

    const timeInput = document.getElementById('lesson-time');
    if (timeInput && !timeInput.dataset.echoWired) {
      timeInput.dataset.echoWired = '1';
      timeInput.addEventListener('change', syncRepeatEcho);
    }
    const dateInput = document.getElementById('lesson-date');
    if (dateInput && !dateInput.dataset.recurWired) {
      dateInput.dataset.recurWired = '1';
      dateInput.addEventListener('change', renderRecur);
    }

    modal.classList.add('open');
  }

  function closeModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.remove('open');
  }

  function toggleRecurring() {
    const rec = document.getElementById('lesson-recurring');
    const box = document.getElementById('recurring-options');
    const on = !!(rec && rec.checked);
    if (box) box.style.display = on ? 'block' : 'none';
    if (on) syncRepeatEcho();
  }

  async function saveLesson(ev) {
   return guard(ev, async () => {
    try {
      const sel = document.getElementById('lesson-student');
      if (!sel.value) { toast('Pick a student or add a new one', 'error'); return; }
      let newStudent = null;
      if (sel.value === '__new__') {
        const nm = (document.getElementById('new-student-name').value || '').trim();
        if (!nm) { toast('Enter the new student’s name', 'error'); return; }
        const phoneEl = document.getElementById('new-student-phone');
        if (phoneEl.value.trim() && !validateAusPhone(phoneEl.value)) {
          toast('Enter a valid Australian mobile number', 'error');
          phoneEl.classList.add('input-error');
          return;
        }
        newStudent = {
          name: nm,
          phone: phoneEl.value,
          level: document.getElementById('new-student-level').value,
        };
      }
      const date = document.getElementById('lesson-date').value;
      if (!date) { toast('Pick a date', 'error'); return; }
      const time = document.getElementById('lesson-time').value;
      const duration = Number(document.getElementById('lesson-duration').value || 60);
      const recurring = !!(document.getElementById('lesson-recurring') || {}).checked;
      const dayNames = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
      const dow = dayNames[(new Date(date).getDay() + 6) % 7];
      const body = {
        student_id: sel.value,
        date,
        start_time: time,
        duration_minutes: duration,
        price: Number(document.getElementById('lesson-price').value || 0),
        notes: document.getElementById('lesson-notes').value,
        recurring,
        recur_weeks: recurring ? _recurWeeks : null,
        recurring_rule: recurring ? {
          frequency: 'weekly',
          day_of_week: dow,
          time,
          duration_minutes: duration,
          weeks: _recurWeeks,
          end_date: null,
          exceptions: [],
        } : null,
      };
      if (newStudent) {
        body.new_student_name = newStudent.name;
        body.new_student_phone = newStudent.phone;
        body.new_student_level = newStudent.level;
      }
      const res = await postJSON('/api/lessons/add', body);
      toast(res.created_count > 1 ? `${res.created_count} lessons added` : 'Lesson added', 'success');
      closeModal('lesson-modal');
      _students = [];
      if (typeof window.onLessonsChanged === 'function') window.onLessonsChanged();
    } catch (e) { toast(e.message, 'error'); }
   });
  }

  /* ---- Add Student modal ---------------------------------------------- */
  async function openAddStudent(prefillName) {
    const modal = document.getElementById('student-modal');
    if (!modal) return;
    document.getElementById('student-name').value = prefillName || '';
    document.getElementById('student-phone').value = '';
    document.getElementById('student-email').value = '';
    document.getElementById('student-level').value = 'Beginner';
    document.getElementById('student-age').value = 'Adult';
    document.getElementById('student-price').value = 80;
    document.getElementById('student-duration').value = 60;
    document.getElementById('student-notes').value = '';
    attachPhoneValidation('student-phone');
    modal.classList.add('open');
  }

  async function saveStudent(ev) {
   return guard(ev, async () => {
    try {
      const name = document.getElementById('student-name').value.trim();
      if (!name) { toast('Name required', 'error'); return; }
      const phoneEl = document.getElementById('student-phone');
      if (phoneEl.value.trim() && !validateAusPhone(phoneEl.value)) {
        toast('Enter a valid Australian mobile number', 'error');
        phoneEl.classList.add('input-error');
        return;
      }
      const body = {
        name,
        phone: document.getElementById('student-phone').value,
        email: document.getElementById('student-email').value,
        level: document.getElementById('student-level').value,
        age_group: document.getElementById('student-age').value,
        default_price: Number(document.getElementById('student-price').value || 80),
        default_duration: Number(document.getElementById('student-duration').value || 60),
        notes: document.getElementById('student-notes').value,
      };
      const res = await postJSON('/api/students/add', body);
      toast('Student added', 'success');
      closeModal('student-modal');
      _students = [];
      if (typeof window.onStudentsChanged === 'function') window.onStudentsChanged(res.student_id);
    } catch (e) { toast(e.message, 'error'); }
   });
  }

  /* ---- Australian phone validation ------------------------------------ */
  function validateAusPhone(value) {
    const v = String(value || '').replace(/\s+/g, '');
    return /^(\+61|0)[4][0-9]{8}$/.test(v);
  }
  function formatAusPhone(value) {
    let v = String(value || '').replace(/\s+/g, '');
    if (v.startsWith('+61')) v = '0' + v.slice(3);
    const d = v.replace(/\D/g, '').slice(0, 10);
    if (d.length <= 4) return d;
    if (d.length <= 7) return d.slice(0, 4) + ' ' + d.slice(4);
    return d.slice(0, 4) + ' ' + d.slice(4, 7) + ' ' + d.slice(7);
  }
  function attachPhoneValidation(input) {
    const el = typeof input === 'string' ? document.getElementById(input) : input;
    if (!el || el.dataset.phoneWired) return;
    el.dataset.phoneWired = '1';
    let err = el.parentNode.querySelector('.phone-error');
    if (!err) {
      err = document.createElement('div');
      err.className = 'phone-error';
      err.textContent = 'Enter a valid Australian mobile number';
      err.hidden = true;
      el.parentNode.appendChild(err);
    }
    el.addEventListener('blur', () => {
      if (!el.value.trim()) { err.hidden = true; el.classList.remove('input-error'); return; }
      el.value = formatAusPhone(el.value);
      const ok = validateAusPhone(el.value);
      err.hidden = ok;
      el.classList.toggle('input-error', !ok);
    });
    el.addEventListener('input', () => {
      if (err.hidden) return;
      const ok = validateAusPhone(el.value);
      err.hidden = ok;
      el.classList.toggle('input-error', !ok);
    });
  }

  /* ---- Weather strip --------------------------------------------------- */
  const WIND_BEARING = {
    N: 0, NNE: 22.5, NE: 45, ENE: 67.5, E: 90, ESE: 112.5, SE: 135, SSE: 157.5,
    S: 180, SSW: 202.5, SW: 225, WSW: 247.5, W: 270, WNW: 292.5, NW: 315, NNW: 337.5,
  };
  function weatherIcon(cond) {
    const c = (cond || '').toLowerCase();
    if (c.includes('thunder') || c.includes('storm')) return 'ti-storm';
    if (c.includes('rain') || c.includes('shower') || c.includes('drizzle')) return 'ti-cloud-rain';
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return 'ti-mist';
    if (c.includes('partly')) return 'ti-cloud-sun';
    if (c.includes('cloud') || c.includes('overcast')) return 'ti-cloud';
    if (c.includes('sun') || c.includes('clear')) return 'ti-sun';
    return 'ti-cloud';
  }
  async function renderWeather(targetId) {
    const el = document.getElementById(targetId || 'weather-strip');
    if (!el) return;
    try {
      const w = await getJSON('/api/weather');
      if (!w || !w.condition) {
        el.className = 'card weather-strip';
        el.innerHTML = '<div class="weather-empty">Weather unavailable</div>';
        return;
      }
      const play = w.playability || {};
      const rating = (play.rating || 'Good');
      const ratingCls = rating.toLowerCase();
      const icon = weatherIcon(w.condition);
      const bearing = WIND_BEARING[(w.wind_direction || '').toUpperCase()] || 0;
      const hours = (w.hourly || []).map(h => {
        const rp = Number(h.rain_prob || 0);
        const barCls = rp >= 50 ? 'high' : (rp >= 20 ? 'med' : '');
        return `<div class="wx-hour"><span>${Coach.to12h(h.hour)}</span><strong>${h.temp}&deg;</strong><span class="wx-rain">${rp}%</span><span class="wx-rainbar ${barCls}"></span></div>`;
      }).join('');
      let sunsetSoon = false;
      if (w.sunset_time) {
        const [sh, sm] = w.sunset_time.split(':').map(Number);
        const sd = new Date(); sd.setHours(sh, sm, 0, 0);
        const mins = (sd - new Date()) / 60000;
        sunsetSoon = mins > 0 && mins <= 120;
      }
      el.className = `card weather-strip wx-rating-${ratingCls}`;
      el.innerHTML = `
        <div class="wx-main">
          <div class="wx-icon-big"><i class="ti ${icon}"></i></div>
          <div class="wx-temp">${w.temp_c}&deg;<span class="wx-feels">feels ${w.feels_like_c}&deg;</span></div>
          <div class="wx-cond">
            <div class="wx-condname">${w.condition}</div>
            <div class="wx-meta"><span class="wx-wind"><i class="ti ti-arrow-narrow-up wx-wind-arrow" style="transform:rotate(${bearing}deg)"></i> ${w.wind_kmh} km/h ${w.wind_direction}</span>
              &nbsp;<i class="ti ti-droplet"></i> ${w.rain_prob}%
              &nbsp;<i class="ti ti-sun"></i> UV ${w.uv_label}</div>
          </div>
          <div class="playability playability-${ratingCls}">
            <div class="play-rating">${rating}</div>
            <div class="play-msg">${play.message || ''}</div>
          </div>
        </div>
        <div class="wx-sub">
          <span class="wx-sunset-pill ${sunsetSoon ? 'soon' : ''}"><i class="ti ti-sunset"></i> Lights on at ${Coach.to12h(w.sunset_time)}</span>
        </div>
        <div class="wx-hours">${hours}</div>`;
    } catch (e) {
      el.className = 'card weather-strip';
      el.innerHTML = '<div class="weather-empty">Weather unavailable</div>';
    }
  }

  /* ---- Enter-to-submit in open modals --------------------------------- */
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'textarea') return;
    const modal = document.querySelector('.modal-overlay.open:not(.confirm-overlay)');
    if (!modal) return;
    const primary = modal.querySelector('.btn-primary');
    if (primary && !primary.disabled) { e.preventDefault(); primary.click(); }
  });

  return {
    money, toast, confirm, guard, getJSON, postJSON, del, initials, to12h, addMinutes,
    blocksLabel, openAddLesson, closeModal, toggleRecurring, saveLesson,
    openAddStudent, saveStudent, renderWeather, ensureStudents,
    validateAusPhone, formatAusPhone, attachPhoneValidation,
    setTimePickerValue, initTimePickers,
    setDuration, stepDuration, formatDuration, onStudentChange,
    setWeeks, stepWeeks, renderRecur,
  };
})();

/* ===========================================================================
   Global custom time picker — used in Settings and the Add Lesson modal.
   onclick="openTimePicker(this)" must be globally available.
   =========================================================================== */
function tpFmt12(hhmm) {
  const parts = (hhmm || '00:00').split(':');
  const h = parseInt(parts[0], 10) || 0;
  const m = (parts[1] || '00').padStart(2, '0');
  const period = h >= 12 ? 'PM' : 'AM';
  const h12 = (h % 12) || 12;
  return `${h12}:${m} ${period}`;
}
function initTimePickers() {
  document.querySelectorAll('.time-picker').forEach(tp => {
    const input = tp.querySelector('.tp-input');
    if (!input) return;
    const val = input.value || '00:00';
    const disp = tp.querySelector('.tp-value');
    if (disp) disp.textContent = tpFmt12(val);
    buildTPColumns(tp, val);
  });
}
function buildTPColumns(tp, val) {
  const parts = (val || '00:00').split(':');
  const h = parseInt(parts[0], 10) || 0;
  const m = (parts[1] || '00').padStart(2, '0');
  const period = h >= 12 ? 'PM' : 'AM';
  const h12 = String((h % 12) || 12);
  const cols = { hour: [], minute: [], period: ['AM', 'PM'] };
  for (let i = 1; i <= 12; i++) cols.hour.push(String(i));
  for (let i = 0; i < 60; i += 15) cols.minute.push(String(i).padStart(2, '0'));
  const sel = { hour: h12, minute: m, period: period };
  tp.querySelectorAll('.tp-col').forEach(col => {
    const key = col.dataset.col;
    col.innerHTML = '';
    cols[key].forEach(opt => {
      const el = document.createElement('div');
      el.className = 'tp-opt' + (opt === sel[key] ? ' selected' : '');
      el.textContent = opt;
      el.addEventListener('click', (e) => { e.stopPropagation(); selectTPOpt(tp, key, opt); });
      col.appendChild(el);
    });
  });
}
function selectTPOpt(tp, key, opt) {
  const col = tp.querySelector(`.tp-col[data-col="${key}"]`);
  col.querySelectorAll('.tp-opt').forEach(o => o.classList.toggle('selected', o.textContent === opt));
  const get = (k) => {
    const s = tp.querySelector(`.tp-col[data-col="${k}"] .tp-opt.selected`);
    return s ? s.textContent : null;
  };
  let h = parseInt(get('hour') || '12', 10);
  const m = get('minute') || '00';
  const period = get('period') || 'AM';
  if (period === 'PM' && h !== 12) h += 12;
  if (period === 'AM' && h === 12) h = 0;
  const hhmm = `${String(h).padStart(2, '0')}:${m}`;
  const input = tp.querySelector('.tp-input');
  input.value = hhmm;
  const disp = tp.querySelector('.tp-value');
  if (disp) disp.textContent = tpFmt12(hhmm);
  input.dispatchEvent(new Event('change'));
}
function openTimePicker(btn) {
  const tp = btn.closest('.time-picker');
  const dd = tp.querySelector('.tp-dropdown');
  const isOpen = dd.classList.contains('open');
  document.querySelectorAll('.tp-dropdown.open').forEach(d => d.classList.remove('open'));
  if (!isOpen) dd.classList.add('open');
}
function setTimePickerValue(name, hhmm) {
  const tp = document.querySelector(`.time-picker[data-name="${name}"]`);
  if (!tp) return;
  const input = tp.querySelector('.tp-input');
  input.value = hhmm || '00:00';
  const disp = tp.querySelector('.tp-value');
  if (disp) disp.textContent = tpFmt12(input.value);
  buildTPColumns(tp, input.value);
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('.time-picker')) {
    document.querySelectorAll('.tp-dropdown.open').forEach(d => d.classList.remove('open'));
  }
});
document.addEventListener('DOMContentLoaded', initTimePickers);

/* ===========================================================================
   Global custom date picker — dark themed, mirrors the time picker.
   onclick="openDatePicker(this)" must be globally available.
   =========================================================================== */
const DP_MONTHS = ['January','February','March','April','May','June','July',
  'August','September','October','November','December'];
function dpParseISO(iso) {
  if (!iso) return null;
  const [y, m, d] = String(iso).split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d);
}
function dpToISO(dt) {
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`;
}
function dpFmtDisplay(iso) {
  const dt = dpParseISO(iso);
  if (!dt) return '—';
  return `${String(dt.getDate()).padStart(2, '0')}/${String(dt.getMonth() + 1).padStart(2, '0')}/${dt.getFullYear()}`;
}
function dpRender(dp) {
  const input = dp.querySelector('.dp-input');
  const selected = dpParseISO(input ? input.value : '');
  if (!dp._viewDate) {
    const base = selected || new Date();
    dp._viewDate = new Date(base.getFullYear(), base.getMonth(), 1);
  }
  const view = dp._viewDate;
  const my = dp.querySelector('.dp-monthyear');
  if (my) my.textContent = `${DP_MONTHS[view.getMonth()]} ${view.getFullYear()}`;
  const daysWrap = dp.querySelector('.dp-days');
  if (!daysWrap) return;
  daysWrap.innerHTML = '';
  const firstDow = (new Date(view.getFullYear(), view.getMonth(), 1).getDay() + 6) % 7;
  const daysInMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
  const today = new Date(); today.setHours(0, 0, 0, 0);
  for (let i = 0; i < firstDow; i++) {
    const blank = document.createElement('span');
    blank.className = 'dp-day other-month';
    daysWrap.appendChild(blank);
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const cell = document.createElement('button');
    cell.type = 'button';
    cell.className = 'dp-day';
    cell.textContent = d;
    const cellDate = new Date(view.getFullYear(), view.getMonth(), d);
    if (cellDate.getTime() === today.getTime()) cell.classList.add('today');
    if (selected && cellDate.getTime() === selected.getTime()) cell.classList.add('selected');
    cell.addEventListener('click', (e) => {
      e.stopPropagation();
      dpSetValue(dp, dpToISO(cellDate));
      dp.querySelector('.dp-dropdown').classList.remove('open');
    });
    daysWrap.appendChild(cell);
  }
}
function dpSetValue(dp, iso) {
  const input = dp.querySelector('.dp-input');
  if (input) { input.value = iso || ''; input.dispatchEvent(new Event('change')); }
  const disp = dp.querySelector('.dp-value');
  if (disp) disp.textContent = iso ? dpFmtDisplay(iso) : '—';
  const sel = dpParseISO(iso);
  if (sel) dp._viewDate = new Date(sel.getFullYear(), sel.getMonth(), 1);
  dpRender(dp);
}
function setDatePickerValue(name, iso) {
  const dp = document.querySelector(`.date-picker[data-name="${name}"]`);
  if (!dp) return;
  dp._viewDate = null;
  dpSetValue(dp, iso || '');
}
function openDatePicker(btn) {
  const dp = btn.closest('.date-picker');
  const dd = dp.querySelector('.dp-dropdown');
  const isOpen = dd.classList.contains('open');
  document.querySelectorAll('.dp-dropdown.open, .tp-dropdown.open').forEach(d => d.classList.remove('open'));
  if (!isOpen) { dpRender(dp); dd.classList.add('open'); }
}
function dpMonth(btn, delta) {
  const dp = btn.closest('.date-picker');
  if (!dp._viewDate) { const n = new Date(); dp._viewDate = new Date(n.getFullYear(), n.getMonth(), 1); }
  dp._viewDate = new Date(dp._viewDate.getFullYear(), dp._viewDate.getMonth() + delta, 1);
  dpRender(dp);
}
function dpToday(btn) {
  const dp = btn.closest('.date-picker');
  dpSetValue(dp, dpToISO(new Date()));
  dp.querySelector('.dp-dropdown').classList.remove('open');
}
function dpClear(btn) {
  const dp = btn.closest('.date-picker');
  dpSetValue(dp, '');
  dp.querySelector('.dp-dropdown').classList.remove('open');
}
function initDatePickers() {
  document.querySelectorAll('.date-picker').forEach(dp => {
    const input = dp.querySelector('.dp-input');
    const disp = dp.querySelector('.dp-value');
    if (disp) disp.textContent = (input && input.value) ? dpFmtDisplay(input.value) : '—';
  });
}
document.addEventListener('click', (e) => {
  if (!e.target.closest('.date-picker')) {
    document.querySelectorAll('.dp-dropdown.open').forEach(d => d.classList.remove('open'));
  }
});
document.addEventListener('DOMContentLoaded', initDatePickers);
