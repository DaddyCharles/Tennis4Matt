/* Ivan — calendar with Day / Week / Month views. */

const Calendar = (() => {
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  const DAY_START = 7;   // 07:00
  const DAY_END = 21;    // 21:00

  let view = localStorage.getItem('ivan_cal_view') || 'week';
  let cursor = new Date();

  function iso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }
  function mondayOf(date) {
    const d = new Date(date);
    const day = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - day);
    d.setHours(0, 0, 0, 0);
    return d;
  }
  function statusBadge(s) { return `<span class="status-badge status-${s}">${s}</span>`; }

  function calIcon(cond) {
    const c = (cond || '').toLowerCase();
    if (c.includes('thunder') || c.includes('storm')) return 'ti-storm';
    if (c.includes('rain') || c.includes('shower') || c.includes('drizzle')) return 'ti-cloud-rain';
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return 'ti-mist';
    if (c.includes('snow')) return 'ti-snowflake';
    if (c.includes('partly')) return 'ti-cloud-sun';
    if (c.includes('cloud') || c.includes('overcast')) return 'ti-cloud';
    if (c.includes('sun') || c.includes('clear')) return 'ti-sun';
    return 'ti-cloud';
  }

  async function weatherMap() {
    const wx = {};
    try {
      const wd = await Coach.getJSON('/api/weather/week');
      (wd.days || []).forEach(day => { wx[day.date] = day; });
    } catch (e) {}
    return wx;
  }

  function lessonClick(l) {
    return `Coach.openAddLesson({student_id:'${l.student_id}',date:'${l.date}',start_time:'${l.start_time}',blocks:${l.blocks}})`;
  }

  /* ---- DAY VIEW ------------------------------------------------------- */
  async function renderDayView() {
    const grid = document.getElementById('calendar-grid');
    const dIso = iso(cursor);
    let data = { lessons: [], earnings: 0, rain_prob: 0 };
    try { data = await Coach.getJSON('/api/calendar/day?date=' + encodeURIComponent(dIso)); } catch (e) {}
    const lessons = (data.lessons || []).slice().sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
    const byStart = {};
    lessons.forEach(l => { (byStart[l.start_time] = byStart[l.start_time] || []).push(l); });

    let rows = '';
    for (let h = DAY_START; h <= DAY_END; h++) {
      for (let mm of [0, 30]) {
        const t = `${String(h).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
        const here = byStart[t] || [];
        if (here.length) {
          rows += here.map(l => {
            let actions = '';
            if (l.status === 'scheduled') {
              actions = `<div class="cal-day-actions">
                <button class="client-btn complete" onclick="event.stopPropagation();Calendar._complete('${l.id}')"><i class="ti ti-check"></i></button>
                <button class="client-btn cancel" onclick="event.stopPropagation();Calendar._cancel('${l.id}')"><i class="ti ti-x"></i></button></div>`;
            } else if (l.status === 'cancelled') {
              actions = `<div class="cal-day-actions"><button class="client-btn" onclick="event.stopPropagation();rescheduleLesson('${l.id}')"><i class="ti ti-calendar-plus"></i></button></div>`;
            }
            return `<div class="cal-day-row">
              <div class="cal-day-time">${Coach.to12h(t)}</div>
              <div class="cal-day-block status-${l.status}" onclick="${lessonClick(l)}">
                <div class="cal-day-block-main"><strong>${l.student_name}</strong>
                  <span>${l.duration_minutes} min · ${Coach.money(l.price)}</span></div>
                ${statusBadge(l.status)}${actions}
              </div></div>`;
          }).join('');
        } else {
          rows += `<div class="cal-day-row">
            <div class="cal-day-time">${Coach.to12h(t)}</div>
            <div class="cal-day-slot" onclick="Coach.openAddLesson({date:'${dIso}',start_time:'${t}'})"></div>
          </div>`;
        }
      }
    }

    const wx = (await weatherMap())[dIso];
    const wxStrip = wx
      ? `<div class="cal-day-wx"><i class="ti ${calIcon(wx.condition)}"></i>
          <span>${wx.temp_max != null ? wx.temp_max + '°' : ''}</span>
          <span class="cal-rain">${wx.rain_prob}% rain</span></div>`
      : '';

    grid.innerHTML = `<div class="cal-day-view">
      <div class="cal-day-topbar">
        ${wxStrip}
        <div class="cal-day-earn">${Coach.money(data.earnings || 0)}</div>
      </div>
      <div class="cal-day-slots">${rows}</div>
    </div>`;
  }

  /* ---- WEEK VIEW ------------------------------------------------------ */
  async function renderWeekView() {
    const grid = document.getElementById('calendar-grid');
    const weekStart = mondayOf(cursor);
    let lessons = [];
    try { const d = await Coach.getJSON('/api/lessons?date='); lessons = d.lessons || []; } catch (e) {}
    const wx = await weatherMap();
    const todayIso = iso(new Date());
    let html = '';
    for (let i = 0; i < 7; i++) {
      const d = new Date(weekStart); d.setDate(d.getDate() + i);
      const dIso = iso(d);
      const dayLessons = lessons
        .filter(l => l.date === dIso && l.status !== 'cancelled')
        .sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
      const total = dayLessons.reduce((s, l) => s + Number(l.price || 0), 0);
      const w = wx[dIso];
      let wxHtml = '';
      if (w) {
        const rainBadge = w.rain_prob > 40 ? `<span class="cal-rain">${w.rain_prob}%</span>` : '';
        wxHtml = `<span class="cal-wx"><i class="ti ${calIcon(w.condition)}"></i>
          <span class="cal-wx-temp">${w.temp_max != null ? w.temp_max + '°' : ''}</span>${rainBadge}</span>`;
      }
      html += `<div class="cal-day${dIso === todayIso ? ' today' : ''}">
        <div class="cal-day-head">
          <span class="cal-dow">${DAYS[i]} ${d.getDate()}</span>
          ${wxHtml}
          <button class="cal-day-add" title="Add lesson" onclick="Coach.openAddLesson({date:'${dIso}'})"><i class="ti ti-plus"></i></button>
        </div>
        <div class="cal-day-body">
          ${dayLessons.map(l => `
            <div class="cal-lesson status-${l.status}" onclick="${lessonClick(l)}">
              <span class="cal-time">${Coach.to12h(l.start_time)}</span>
              <span class="cal-student">${l.student_name}</span>
              ${statusBadge(l.status)}
            </div>`).join('') || '<div class="cal-empty">—</div>'}
        </div>
        ${total ? `<div class="cal-day-total">${Coach.money(total)}</div>` : ''}
      </div>`;
    }
    grid.innerHTML = html;
  }

  /* ---- MONTH VIEW ----------------------------------------------------- */
  async function renderMonthView() {
    const grid = document.getElementById('calendar-grid');
    const year = cursor.getFullYear();
    const month = cursor.getMonth(); // 0-based
    let summary = {};
    try {
      const d = await Coach.getJSON(`/api/calendar/month?year=${year}&month=${month + 1}`);
      (d.days || []).forEach(x => { summary[x.date] = x; });
    } catch (e) {}

    const first = new Date(year, month, 1);
    const gridStart = mondayOf(first);
    const todayIso = iso(new Date());

    let cells = DAYS.map(d => `<div class="month-dow">${d}</div>`).join('');
    for (let i = 0; i < 42; i++) {
      const d = new Date(gridStart); d.setDate(d.getDate() + i);
      const dIso = iso(d);
      const other = d.getMonth() !== month;
      const info = summary[dIso];
      const classes = ['month-day'];
      if (dIso === todayIso) classes.push('today');
      if (other) classes.push('other-month');
      const dot = info && info.lesson_count
        ? `<span class="month-lesson-dot"><i class="ti ti-circle-filled" style="font-size:7px"></i>${info.lesson_count}</span>` : '';
      const earn = info && info.earnings ? `<div class="month-earnings">${Coach.money(info.earnings)}</div>` : '';
      const rain = info && info.rain_prob > 50 ? `<span class="month-rain-icon"><i class="ti ti-droplet"></i></span>` : '';
      cells += `<div class="${classes.join(' ')}" onclick="Calendar._openDay('${dIso}')">
        <div class="month-day-num">${d.getDate()} ${rain}</div>
        ${dot}${earn}
      </div>`;
    }
    grid.innerHTML = `<div class="month-grid">${cells}</div>`;
  }

  /* ---- shared --------------------------------------------------------- */
  function updateViewButtons() {
    document.querySelectorAll('.cal-view-pill').forEach(b =>
      b.classList.toggle('active', b.dataset.view === view));
  }

  function updateHeader() {
    const el = document.getElementById('cal-range');
    if (!el) return;
    if (view === 'day') {
      el.textContent = cursor.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    } else if (view === 'week') {
      const s = mondayOf(cursor); const e = new Date(s); e.setDate(e.getDate() + 6);
      const opts = { month: 'short', day: 'numeric' };
      el.textContent = `${s.toLocaleDateString(undefined, opts)} – ${e.toLocaleDateString(undefined, opts)}`;
    } else {
      el.textContent = `${MONTHS[cursor.getMonth()]} ${cursor.getFullYear()}`;
    }
  }

  async function renderCalendar() {
    updateViewButtons();
    updateHeader();
    if (view === 'day') await renderDayView();
    else if (view === 'month') await renderMonthView();
    else await renderWeekView();
  }

  function switchView(v) {
    view = v;
    localStorage.setItem('ivan_cal_view', v);
    renderCalendar();
  }
  function navigate(dir) {
    if (view === 'day') cursor.setDate(cursor.getDate() + dir);
    else if (view === 'week') cursor.setDate(cursor.getDate() + dir * 7);
    else cursor.setMonth(cursor.getMonth() + dir);
    renderCalendar();
  }
  function goToToday() { cursor = new Date(); renderCalendar(); }

  async function _complete(id) {
    try { await Coach.postJSON(`/api/lessons/${id}/complete`); Coach.toast('Marked complete', 'success'); renderCalendar(); }
    catch (e) { Coach.toast(e.message, 'error'); }
  }
  async function _cancel(id) {
    if (!await Coach.confirm({ title: 'Cancel lesson?', message: 'This lesson will be marked as cancelled.', confirmText: 'Cancel lesson', cancelText: 'Keep' })) return;
    try { await Coach.postJSON(`/api/lessons/${id}/cancel`); Coach.toast('Cancelled', 'info'); renderCalendar(); }
    catch (e) { Coach.toast(e.message, 'error'); }
  }
  function _openDay(dIso) {
    cursor = new Date(dIso + 'T00:00:00');
    view = 'day';
    localStorage.setItem('ivan_cal_view', 'day');
    renderCalendar();
  }

  function init() {
    cursor = new Date();
    // On phones, week/month grids are too small — default to Day view.
    // (Local-only override; the saved desktop preference is left untouched.)
    if (window.matchMedia('(max-width: 768px)').matches && view !== 'day') {
      view = 'day';
    }
    renderCalendar();
  }

  return {
    init, switchView, navigate, goToToday, refresh: renderCalendar,
    _complete, _cancel, _openDay,
    // backwards-compat aliases
    prev: () => navigate(-1), next: () => navigate(1), thisWeek: goToToday,
  };
})();
