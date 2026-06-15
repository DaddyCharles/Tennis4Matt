/* Ivan — week calendar grid. */

const Calendar = (() => {
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  let _weekStart = null;

  function mondayOf(date) {
    const d = new Date(date);
    const day = (d.getDay() + 6) % 7;
    d.setDate(d.getDate() - day);
    d.setHours(0, 0, 0, 0);
    return d;
  }

  function iso(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  }

  function statusBadge(s) { return `<span class="status-badge status-${s}">${s}</span>`; }

  function setRangeLabel() {
    const end = new Date(_weekStart); end.setDate(end.getDate() + 6);
    const opts = { month: 'short', day: 'numeric' };
    const el = document.getElementById('cal-range');
    if (el) el.textContent = `${_weekStart.toLocaleDateString(undefined, opts)} – ${end.toLocaleDateString(undefined, opts)}`;
  }

  async function refresh() {
    setRangeLabel();
    const grid = document.getElementById('calendar-grid');
    let lessons = [];
    try {
      const data = await Coach.getJSON(`/api/lessons?date=`);
      lessons = data.lessons || [];
    } catch (e) {}
    const todayIso = iso(new Date());
    let html = '';
    for (let i = 0; i < 7; i++) {
      const d = new Date(_weekStart); d.setDate(d.getDate() + i);
      const dIso = iso(d);
      const dayLessons = lessons
        .filter(l => l.date === dIso && l.status !== 'cancelled')
        .sort((a, b) => (a.start_time || '').localeCompare(b.start_time || ''));
      const total = dayLessons.reduce((s, l) => s + Number(l.price || 0), 0);
      html += `<div class="cal-day${dIso === todayIso ? ' today' : ''}">
        <div class="cal-day-head">
          <span class="cal-dow">${DAYS[i]}</span>
          <span class="cal-date">${d.getDate()}</span>
        </div>
        <div class="cal-day-body">
          ${dayLessons.map(l => `
            <div class="cal-lesson" onclick="Coach.openAddLesson({student_id:'${l.student_id}',date:'${l.date}',start_time:'${l.start_time}',blocks:${l.blocks}})">
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

  function prev() { _weekStart.setDate(_weekStart.getDate() - 7); refresh(); }
  function next() { _weekStart.setDate(_weekStart.getDate() + 7); refresh(); }
  function thisWeek() { _weekStart = mondayOf(new Date()); refresh(); }

  function init() {
    _weekStart = mondayOf(new Date());
    refresh();
  }

  return { init, prev, next, thisWeek, refresh };
})();
