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
    el._t = setTimeout(() => { el.className = 'coach-toast'; }, 2600);
  }

  async function api(url, opts) {
    const res = await fetch(url, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* non-json */ }
    if (!res.ok) throw new Error(data.message || ('Request failed: ' + res.status));
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
  let _selectedBlocks = 2;

  async function ensureStudents() {
    if (_students.length) return _students;
    const data = await getJSON('/api/students');
    _students = data.students || [];
    return _students;
  }

  function buildBlockSelector() {
    const wrap = document.getElementById('block-selector');
    if (!wrap) return;
    wrap.innerHTML = '';
    const max = 12;
    for (let i = 1; i <= max; i++) {
      const b = document.createElement('div');
      b.className = 'block' + (i <= _selectedBlocks ? ' selected' : '');
      b.textContent = '30m';
      b.dataset.block = i;
      b.onclick = () => { _selectedBlocks = i; refreshBlocks(); };
      wrap.appendChild(b);
    }
    refreshBlocks();
  }

  function refreshBlocks() {
    document.querySelectorAll('#block-selector .block').forEach(el => {
      el.classList.toggle('selected', Number(el.dataset.block) <= _selectedBlocks);
    });
    const total = document.getElementById('block-total');
    if (total) total.textContent = blocksLabel(_selectedBlocks);
    autoFillPrice();
  }

  function autoFillPrice() {
    const priceEl = document.getElementById('lesson-price');
    if (!priceEl) return;
    const sel = document.getElementById('lesson-student');
    const student = _students.find(s => s.id === (sel && sel.value));
    const map = { 1: 45, 2: 80, 3: 110, 4: 140 };
    let price = map[_selectedBlocks];
    if (price == null && student) price = student.default_price;
    if (price == null) price = 40 * _selectedBlocks;
    if (!priceEl.dataset.touched) priceEl.value = price;
  }

  async function openAddLesson(prefill) {
    prefill = prefill || {};
    await ensureStudents();
    const modal = document.getElementById('lesson-modal');
    if (!modal) return;
    const sel = document.getElementById('lesson-student');
    sel.innerHTML = _students.map(s =>
      `<option value="${s.id}">${s.name}</option>`).join('');
    if (prefill.student_id) sel.value = prefill.student_id;
    document.getElementById('lesson-date').value = prefill.date || new Date().toISOString().slice(0, 10);
    document.getElementById('lesson-time').value = prefill.start_time || '09:00';
    _selectedBlocks = prefill.blocks || 2;
    const priceEl = document.getElementById('lesson-price');
    priceEl.dataset.touched = '';
    document.getElementById('lesson-notes').value = '';
    const rec = document.getElementById('lesson-recurring');
    if (rec) { rec.checked = false; toggleRecurring(); }
    buildBlockSelector();
    sel.onchange = autoFillPrice;
    priceEl.oninput = () => { priceEl.dataset.touched = '1'; };
    modal.classList.add('open');
  }

  function closeModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.remove('open');
  }

  function toggleRecurring() {
    const rec = document.getElementById('lesson-recurring');
    const box = document.getElementById('recurring-options');
    if (box) box.style.display = rec && rec.checked ? 'block' : 'none';
  }

  async function saveLesson() {
    try {
      const sel = document.getElementById('lesson-student');
      if (!sel.value) { toast('Add a student first', 'error'); return; }
      const date = document.getElementById('lesson-date').value;
      const time = document.getElementById('lesson-time').value;
      const recurring = !!(document.getElementById('lesson-recurring') || {}).checked;
      const dayNames = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
      const dow = dayNames[(new Date(date).getDay() + 6) % 7];
      const body = {
        student_id: sel.value,
        date,
        start_time: time,
        blocks: _selectedBlocks,
        price: Number(document.getElementById('lesson-price').value || 0),
        notes: document.getElementById('lesson-notes').value,
        recurring,
        recurring_rule: recurring ? {
          frequency: 'weekly',
          day_of_week: dow,
          time,
          blocks: _selectedBlocks,
          end_date: document.getElementById('recurring-end').value || null,
          exceptions: [],
        } : null,
      };
      const res = await postJSON('/api/lessons/add', body);
      toast(res.created_count > 1 ? `${res.created_count} lessons added` : 'Lesson added', 'success');
      closeModal('lesson-modal');
      if (typeof window.onLessonsChanged === 'function') window.onLessonsChanged();
    } catch (e) { toast(e.message, 'error'); }
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
    modal.classList.add('open');
  }

  async function saveStudent() {
    try {
      const name = document.getElementById('student-name').value.trim();
      if (!name) { toast('Name required', 'error'); return; }
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

  return {
    money, toast, getJSON, postJSON, del, initials, to12h, addMinutes,
    blocksLabel, openAddLesson, closeModal, toggleRecurring, saveLesson,
    openAddStudent, saveStudent, renderWeather, ensureStudents,
  };
})();
