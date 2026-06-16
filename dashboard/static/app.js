/* Ivan — dashboard client (vanilla JS, no frameworks). */

// --------------------------------------------------------------- Utilities

async function apiCall(url, method = 'GET', body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  return data;
}

async function confirmDialog(opts) {
  if (typeof Coach !== 'undefined' && Coach.confirm) return Coach.confirm(opts);
  return window.confirm(opts.message || opts.title || 'Are you sure?');
}

function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

async function rescheduleLesson(lessonId) {
  try {
    const res = await fetch(`/api/lessons/${lessonId}/reschedule-data`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.message || 'Could not load lesson');
    if (typeof Coach === 'undefined' || !Coach.openAddLesson) {
      showToast('Reschedule not available here', 'error');
      return;
    }
    Coach.openAddLesson({
      student_id: data.student_id,
      blocks: data.blocks,
      price: data.price,
      date: '',
      start_time: '',
      notes: data.original_date ? `Rescheduled from ${data.original_date}` : '',
    });
  } catch (e) {
    showToast(e.message || 'Reschedule failed', 'error');
  }
}

function levelOf(line) {
  const m = line.match(/\]\s\[(\w+)\]/);
  return m ? m[1] : 'INFO';
}

function timeAgo(iso) {
  if (!iso) return 'never';
  const then = new Date(iso);
  if (isNaN(then.getTime())) return iso;
  let s = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} minute${m !== 1 ? 's' : ''} ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} hour${h !== 1 ? 's' : ''} ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d !== 1 ? 's' : ''} ago`;
}

function countUp(el, target, duration = 1200) {
  if (!el) return;
  target = Number(target) || 0;
  if (target <= 0) { el.textContent = '0'; return; }
  const start = performance.now();
  function frame(now) {
    const p = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(eased * target).toString();
    if (p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function createRipple(event) {
  const btn = event && event.currentTarget;
  if (!btn) return;
  const rect = btn.getBoundingClientRect();
  const size = Math.max(rect.width, rect.height);
  const dot = document.createElement('span');
  dot.className = 'ripple-dot';
  dot.style.width = dot.style.height = `${size}px`;
  dot.style.left = `${(event.clientX || rect.left + rect.width / 2) - rect.left - size / 2}px`;
  dot.style.top = `${(event.clientY || rect.top + rect.height / 2) - rect.top - size / 2}px`;
  dot.style.animation = 'rippleFx 0.6s ease-out forwards';
  btn.appendChild(dot);
  setTimeout(() => dot.remove(), 650);
}

// ------------------------------------------------------------- Live feeds

let renderedLogCount = 0;
let logFirstRender = true;

function renderLogs(lines) {
  const panel = document.getElementById('log-panel');
  if (!panel) return;
  const atBottom = panel.scrollHeight - panel.scrollTop - panel.clientHeight < 40;

  // Full rebuild on first render or when the buffer shrank (e.g. cleared/rotated).
  const rebuild = logFirstRender || lines.length < renderedLogCount
    || panel.querySelector('.empty-state');
  if (rebuild) {
    panel.innerHTML = '';
    renderedLogCount = 0;
  }

  const animate = !logFirstRender;
  for (let i = renderedLogCount; i < lines.length; i++) {
    const line = lines[i];
    const level = levelOf(line);
    const span = document.createElement('span');
    span.className = `log-line ${level}`;
    if (animate) {
      span.classList.add('log-new');
      if (level === 'SUCCESS') span.classList.add('log-success-flash');
    }
    span.textContent = line;
    panel.appendChild(span);
  }
  renderedLogCount = lines.length;
  logFirstRender = false;
  if (atBottom) panel.scrollTop = panel.scrollHeight;
}

async function pollLogs() {
  if (!document.getElementById('log-panel')) return;
  try {
    const data = await apiCall('/api/logs');
    renderLogs(data.logs || []);
  } catch (e) { /* ignore transient errors */ }
}

function setEl(id, fn) { const el = document.getElementById(id); if (el) fn(el); }
function setText(id, val) { if (val === undefined || val === null) return; setEl(id, (el) => { el.textContent = val; }); }

const PAGE_LOADED_AT = Date.now();
let bannerDismissedType = null;
let bannerCurrentType = null;

function updateBanner(data) {
  const banner = document.getElementById('notif-banner');
  if (!banner) return;
  const textEl = document.getElementById('notif-banner-text');
  const actionEl = document.getElementById('notif-banner-action');

  let b = null;
  if (!data.facebook_connected) {
    b = { type: 'fb', cls: 'amber', icon: 'ti-alert-triangle', text: 'Facebook not connected — Go to Settings to log in',
          action: { label: 'Go to Settings', href: '/settings' } };
  } else if (data.groups_count === 0) {
    b = { type: 'groups', cls: 'amber', icon: 'ti-alert-triangle', text: 'No groups configured — Add groups to start monitoring',
          action: { label: 'Add Groups', href: '/groups' } };
  }

  bannerCurrentType = b ? b.type : null;
  if (!b || b.type === bannerDismissedType) { banner.hidden = true; return; }
  if (b.type !== bannerDismissedType) bannerDismissedType = null;

  banner.className = `notif-banner ${b.cls}`;
  banner.hidden = false;
  if (textEl) {
    textEl.textContent = '';
    if (b.icon) {
      const ico = document.createElement('i');
      ico.className = `ti ${b.icon} notif-banner-icon`;
      textEl.appendChild(ico);
    }
    textEl.appendChild(document.createTextNode(b.text));
  }
  if (actionEl) {
    if (b.action) {
      actionEl.hidden = false;
      actionEl.textContent = b.action.label;
      actionEl.onclick = () => { window.location.href = b.action.href; };
    } else {
      actionEl.hidden = true;
    }
  }
}

function dismissBanner() {
  bannerDismissedType = bannerCurrentType;
  const banner = document.getElementById('notif-banner');
  if (banner) banner.hidden = true;
}

let prevRunning = null;

function applyStatus(data) {
  const running = !!data.running;

  // Top bar Start/Stop buttons
  setEl('btn-start', (el) => { el.hidden = running; });
  setEl('btn-stop', (el) => { el.hidden = !running; });

  // Scale-pulse the now-visible button when the run state flips.
  if (prevRunning !== null && prevRunning !== running) {
    const el = document.getElementById(running ? 'btn-stop' : 'btn-start');
    if (el) { el.classList.remove('btn-pulse'); void el.offsetWidth; el.classList.add('btn-pulse'); }
  }
  prevRunning = running;

  // Generic status badge (other pages / fallback)
  setEl('status-badge', (el) => {
    el.className = `status-badge ${running ? 'running' : 'stopped'}`;
    el.textContent = running ? 'RUNNING' : 'STOPPED';
  });

  // Sidebar bot status pill
  setEl('sidebar-status', (el) => { el.className = `sidebar-status-pill ${running ? 'running' : 'stopped'}`; });
  setText('sidebar-status-text', running ? 'RUNNING' : 'STOPPED');

  // FB chip in topbar
  setEl('fb-chip', (el) => {
    el.className = `conn-chip ${data.facebook_connected ? 'ok' : 'bad'}`;
    el.textContent = data.facebook_connected ? '● Facebook Connected' : '● Not Connected — Go to Settings';
  });

  // Live feed dot
  const feedDot = document.querySelector('.feed-title-dot');
  if (feedDot) feedDot.className = `feed-title-dot ${running ? 'running' : ''}`;

  // Connection status bar (4 indicators)
  setEl('cb-fb', (el) => {
    el.className = `conn-state ${data.facebook_connected ? 'ok' : 'bad'}`;
    el.textContent = data.facebook_connected ? 'Connected' : 'Not Connected';
  });
  setEl('cb-fb-dot', (el) => { el.className = `conn-dot ${data.facebook_connected ? 'ok' : 'bad'}`; });
  setEl('cb-bot', (el) => {
    el.className = `conn-state ${running ? 'ok' : 'bad'}`;
    el.textContent = running ? 'Running' : 'Stopped';
  });
  setEl('cb-bot-dot', (el) => { el.className = `conn-dot ${running ? 'ok' : 'bad'}`; });
  setEl('cb-scan', (el) => {
    const m = data.next_scan_minutes;
    el.textContent = (m === null || m === undefined) ? 'Paused'
      : (m === 0 ? 'due now' : `in ${m} minute${m !== 1 ? 's' : ''}`);
  });

  // Stat values — let the initial count-up animation play out before clobbering.
  if (Date.now() - PAGE_LOADED_AT > 900) {
    setText('stat-today', data.today_count);
    setText('stat-total', data.total_leads);
    setText('stat-groups', data.groups_count);
  }
  setEl('stat-lastscan', (el) => { el.textContent = timeAgo(data.last_scan); });
  setEl('stat-lastscan-sub', (el) => { el.textContent = running ? 'monitoring' : 'bot stopped'; });

  // Sidebar nav badges/dots
  setEl('nav-leads-badge', (el) => {
    if (data.new_count > 0) { el.hidden = false; el.textContent = data.new_count; }
    else el.hidden = true;
  });
  setEl('nav-groups-dot', (el) => { el.className = `nav-dot ${data.groups_count > 0 ? 'ok' : 'bad'}`; });
  setEl('nav-keywords-dot', (el) => { el.className = `nav-dot ${data.keywords_count > 0 ? 'ok' : 'bad'}`; });

  // Quick action "NEW" badge
  setEl('qa-new-badge', (el) => {
    if (data.new_count > 0) { el.hidden = false; el.textContent = `${data.new_count} NEW`; }
    else el.hidden = true;
  });

  updateBanner(data);
}

async function clearLog() {
  const data = await apiCall('/api/logs/clear', 'POST');
  if (data.status === 'ok') { renderLogs([]); showToast('Log cleared'); }
  else showToast(data.message || 'Failed to clear log', 'error');
}

async function pollStatus() {
  try {
    const data = await apiCall('/api/status');
    applyStatus(data);
  } catch (e) { /* ignore */ }
}

// ------------------------------------------------------------ Bot control

async function startBot(event) {
  if (event) createRipple(event);
  const data = await apiCall('/bot/start', 'POST');
  if (data.status === 'ok') { showToast('Bot started'); pollStatus(); }
  else showToast(data.message || 'Failed to start', 'error');
}

async function stopBot(event) {
  if (event) createRipple(event);
  const data = await apiCall('/bot/stop', 'POST');
  if (data.status === 'ok') { showToast('Bot stopped'); pollStatus(); }
  else showToast(data.message || 'Failed to stop', 'error');
}

// --------------------------------------------------------- Reply modal

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('open');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('open');
}

let currentReplyLeadId = null;

async function openReplyModal(leadId, postUrl) {
  currentReplyLeadId = leadId;
  if (postUrl) window.open(postUrl, '_blank');
  const textarea = document.getElementById('reply-text');
  if (textarea) textarea.value = 'Loading suggested reply...';
  openModal('reply-modal');
  try {
    const data = await apiCall(`/leads/${leadId}/reply-text`);
    if (textarea) textarea.value = data.reply_text || '';
  } catch (e) {
    if (textarea) textarea.value = '';
  }
}

function copyReplyText() {
  const textarea = document.getElementById('reply-text');
  if (!textarea) return;
  textarea.select();
  navigator.clipboard.writeText(textarea.value).then(
    () => showToast('Reply copied to clipboard'),
    () => { document.execCommand('copy'); showToast('Reply copied'); }
  );
}

async function markReplied() {
  if (!currentReplyLeadId) return;
  const textarea = document.getElementById('reply-text');
  const data = await apiCall(`/leads/${currentReplyLeadId}/reply`, 'POST', {
    reply_text: textarea ? textarea.value : '',
  });
  if (data.status === 'ok') {
    showToast('Marked as replied');
    closeModal('reply-modal');
    setTimeout(() => location.reload(), 600);
  } else {
    showToast(data.message || 'Failed', 'error');
  }
}

// ---------------------------------------------------------- Lead actions

async function setLeadStatus(leadId, status) {
  const data = await apiCall(`/leads/${leadId}/status`, 'POST', { status });
  if (data.status === 'ok') { showToast(`Marked ${status}`); setTimeout(() => location.reload(), 500); }
  else showToast(data.message || 'Failed', 'error');
}

async function deleteLead(leadId) {
  if (!await confirmDialog({ title: 'Delete lead?', message: 'This lead will be permanently removed.', confirmText: 'Delete' })) return;
  const data = await apiCall(`/leads/${leadId}/delete`, 'POST');
  if (data.status === 'ok') { showToast('Lead deleted'); setTimeout(() => location.reload(), 500); }
  else showToast(data.message || 'Failed', 'error');
}

// ----------------------------------------------------- Leads filtering

function filterLeads() {
  const statusSel = document.getElementById('filter-status');
  const groupSel = document.getElementById('filter-group');
  const search = document.getElementById('filter-search');
  const fromDate = document.getElementById('filter-from');
  const toDate = document.getElementById('filter-to');
  const rows = Array.from(document.querySelectorAll('#leads-table tbody tr'));

  const sVal = statusSel ? statusSel.value : '';
  const gVal = groupSel ? groupSel.value : '';
  const qVal = search ? search.value.toLowerCase() : '';
  const fVal = fromDate ? fromDate.value : '';
  const tVal = toDate ? toDate.value : '';

  rows.forEach((row) => {
    const rStatus = row.dataset.status || '';
    const rGroup = row.dataset.group || '';
    const rDate = row.dataset.date || '';
    const rText = (row.textContent || '').toLowerCase();
    let show = true;
    if (sVal && rStatus !== sVal) show = false;
    if (gVal && rGroup !== gVal) show = false;
    if (qVal && !rText.includes(qVal)) show = false;
    if (fVal && rDate < fVal) show = false;
    if (tVal && rDate > tVal) show = false;
    row.dataset.match = show ? '1' : '0';
  });
  paginateLeads(1);
}

let currentPage = 1;
const PAGE_SIZE = 20;

function paginateLeads(page) {
  currentPage = page;
  const rows = Array.from(document.querySelectorAll('#leads-table tbody tr'))
    .filter((r) => r.dataset.match !== '0');
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (currentPage > pages) currentPage = pages;
  document.querySelectorAll('#leads-table tbody tr').forEach((r) => { r.style.display = 'none'; });
  const start = (currentPage - 1) * PAGE_SIZE;
  rows.slice(start, start + PAGE_SIZE).forEach((r) => { r.style.display = ''; });

  const info = document.getElementById('page-info');
  if (info) info.textContent = `Page ${currentPage} of ${pages} (${total} leads)`;
  const prev = document.getElementById('page-prev');
  const next = document.getElementById('page-next');
  if (prev) prev.disabled = currentPage <= 1;
  if (next) next.disabled = currentPage >= pages;
}

// ---------------------------------------------------------------- Keywords

function addKeyword(categoryId) {
  const input = document.getElementById(`kw-input-${categoryId}`);
  if (!input || !input.value.trim()) return;
  const container = document.getElementById(`kw-chips-${categoryId}`);
  const value = input.value.trim();
  const chip = buildChip(value, () => chip.remove());
  container.appendChild(chip);
  input.value = '';
}

function addExclusion() {
  const input = document.getElementById('excl-input');
  if (!input || !input.value.trim()) return;
  const container = document.getElementById('excl-chips');
  const value = input.value.trim();
  const chip = buildChip(value, () => chip.remove());
  chip.classList.add('chip-exclusion');
  container.appendChild(chip);
  input.value = '';
}

// ----------------------------------------------------- Keyword tabs / presets

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.tab === tab);
  });
  document.querySelectorAll('.tab-panel').forEach((p) => {
    p.classList.toggle('active', p.id === `tab-${tab}`);
  });
}

async function loadPreset(presetId) {
  if (!await confirmDialog({ title: 'Load preset?', message: 'This will replace your current keywords.', confirmText: 'Load preset', danger: false })) return;
  const data = await apiCall(`/keywords/load-preset/${presetId}`, 'POST');
  if (data.status === 'ok') {
    showToast(`Preset loaded (${data.categories_loaded} categories)`);
    setTimeout(() => location.reload(), 600);
  } else {
    showToast(data.message || 'Failed to load preset', 'error');
  }
}

function buildChip(value, onRemove) {
  const chip = document.createElement('span');
  chip.className = 'chip';
  chip.dataset.value = value;
  const text = document.createElement('span');
  text.textContent = value;
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.textContent = '×';
  btn.onclick = onRemove;
  chip.appendChild(text);
  chip.appendChild(btn);
  return chip;
}

async function saveKeywords() {
  const categories = [];
  document.querySelectorAll('[data-category]').forEach((catEl) => {
    const id = catEl.dataset.category;
    const label = catEl.dataset.label;
    const templateId = catEl.dataset.template;
    const keywords = Array.from(catEl.querySelectorAll('.chip'))
      .map((c) => c.dataset.value);
    categories.push({ id, label, keywords, reply_template_id: templateId });
  });
  const exclusions = Array.from(document.querySelectorAll('#excl-chips .chip'))
    .map((c) => c.dataset.value);
  const data = await apiCall('/keywords/save', 'POST', { categories, exclusions });
  if (data.status === 'ok') showToast('Keywords saved');
  else showToast(data.message || 'Failed', 'error');
}

// ------------------------------------------------------------------ Groups

async function addGroup() {
  const name = document.getElementById('group-name').value.trim();
  const url = document.getElementById('group-url').value.trim();
  const loc = document.getElementById('group-location').value.trim();
  if (!name || !url) { showToast('Name and URL are required', 'error'); return; }
  const data = await apiCall('/groups/add', 'POST', { name, url, location: loc });
  if (data.status === 'ok') { showToast('Group added'); setTimeout(() => window.location.reload(), 500); }
  else showToast(data.message || 'Failed', 'error');
}

async function toggleGroup(groupId) {
  const data = await apiCall(`/groups/${groupId}/toggle`, 'POST');
  if (data.status === 'ok') { showToast(data.active ? 'Group activated' : 'Group deactivated'); setTimeout(() => window.location.reload(), 400); }
  else showToast(data.message || 'Failed', 'error');
}

async function deleteGroup(groupId) {
  if (!await confirmDialog({ title: 'Delete group?', message: 'This group will be permanently removed.', confirmText: 'Delete' })) return;
  const data = await apiCall(`/groups/${groupId}/delete`, 'POST');
  if (data.status === 'ok') { showToast('Group deleted'); setTimeout(() => window.location.reload(), 400); }
  else showToast(data.message || 'Failed', 'error');
}

// ------------------------------------------------------------------ Replies

function addReplyVariant(templateId) {
  const container = document.getElementById(`tmpl-msgs-${templateId}`);
  if (!container) return;
  const wrap = document.createElement('div');
  wrap.className = 'form-group reply-variant';
  wrap.innerHTML = `
    <div class="flex gap-1">
      <textarea class="form-input reply-message"></textarea>
      <button type="button" class="btn btn-danger btn-small" onclick="this.closest('.reply-variant').remove()">Delete</button>
    </div>`;
  container.appendChild(wrap);
}

async function saveReplies() {
  const templates = [];
  document.querySelectorAll('[data-template-id]').forEach((tEl) => {
    const id = tEl.dataset.templateId;
    const label = tEl.dataset.templateLabel;
    const messages = Array.from(tEl.querySelectorAll('.reply-message'))
      .map((t) => t.value.trim())
      .filter((t) => t.length > 0);
    templates.push({ id, label, messages });
  });
  const data = await apiCall('/replies/save', 'POST', { templates });
  if (data.status === 'ok') showToast('Replies saved');
  else showToast(data.message || 'Failed', 'error');
}

// ----------------------------------------------------------------- Settings

async function saveSettings() {
  const get = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
  const checked = (id) => { const el = document.getElementById(id); return el ? el.checked : false; };
  const body = {
    scan_interval_minutes: get('scan_interval_minutes'),
    active_hours_start: get('active_hours_start'),
    active_hours_end: get('active_hours_end'),
    daily_limit: get('daily_limit'),
    min_delay_seconds: get('min_delay_seconds'),
    max_delay_seconds: get('max_delay_seconds'),
    headless_mode: checked('headless_mode'),
    email_notifications: checked('email_notifications'),
    email_address: get('email_address'),
    email_smtp: get('email_smtp'),
    email_password: get('email_password'),
  };
  const data = await apiCall('/settings/save', 'POST', body);
  if (data.status === 'ok') showToast('Settings saved');
  else showToast(data.message || 'Failed', 'error');
}

async function loginFacebook() {
  const data = await apiCall('/settings/login', 'POST');
  if (data.status === 'ok') showToast(data.message || 'Login window opening...');
  else showToast(data.message || 'Failed', 'error');
}

async function testSession() {
  const indicator = document.getElementById('session-result');
  if (indicator) indicator.textContent = 'Checking session...';
  const data = await apiCall('/settings/test-session', 'POST');
  if (indicator) {
    indicator.textContent = data.message || '';
    indicator.className = data.status === 'ok' ? 'text-success' : 'text-danger';
  }
  showToast(data.message || 'Done', data.status === 'ok' ? 'success' : 'error');
}

// --------------------------------------------------------- Settings: setup

async function resetOnboarding() {
  if (!await confirmDialog({ title: 'Restart setup?', message: 'This will restart the setup wizard.', confirmText: 'Restart', danger: false })) return;
  await fetch('/onboarding/reset', { method: 'POST' });
  window.location.href = '/onboarding';
}

async function enableStartup() {
  const data = await apiCall('/settings/startup/enable', 'POST');
  const badge = document.getElementById('startup-status');
  if (data.status === 'ok') {
    if (badge) { badge.textContent = 'ENABLED'; badge.className = 'badge badge-replied'; }
    showToast('Will start automatically with Windows');
  } else {
    showToast(data.message || 'Failed', 'error');
  }
}

async function disableStartup() {
  const data = await apiCall('/settings/startup/disable', 'POST');
  const badge = document.getElementById('startup-status');
  if (data.status === 'ok') {
    if (badge) { badge.textContent = 'DISABLED'; badge.className = 'badge badge-ignored'; }
    showToast('Removed from Windows startup');
  } else {
    showToast(data.message || 'Failed', 'error');
  }
}

// ------------------------------------------------------------- Onboarding

const ONB_EXCLUSIONS = ['table tennis', 'ping pong', 'beach tennis', 'tennis ball machine for sale'];

function onbSetDots(step) {
  document.querySelectorAll('.progress-dot').forEach((d) => {
    const n = Number(d.dataset.step);
    d.classList.toggle('active', n === step);
    d.classList.toggle('completed', n < step);
  });
  const counter = document.getElementById('step-num');
  if (counter) counter.textContent = step;
}

function onbGoTo(step) {
  const steps = Array.from(document.querySelectorAll('.onb-step'));
  const current = steps.find((s) => !s.hidden);
  const next = steps.find((s) => Number(s.dataset.step) === step);
  if (!next) return;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const reveal = () => {
    steps.forEach((s) => { s.hidden = s !== next; s.classList.remove('slide-out'); });
    onbSetDots(step);
    if (!reduceMotion) {
      next.classList.remove('slide-in');
      void next.offsetWidth;
      next.classList.add('slide-in');
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (current && current !== next && !reduceMotion) {
    current.classList.add('slide-out');
    setTimeout(reveal, 280);
  } else {
    reveal();
  }
}

function onbCurrentStep() {
  const visible = Array.from(document.querySelectorAll('.onb-step')).find((s) => !s.hidden);
  return visible ? Number(visible.dataset.step) : 1;
}

async function onbNext(n) {
  const data = await apiCall(`/onboarding/step/${n}`, 'POST');
  if (data.status === 'ok') {
    onbGoTo(data.next_step);
  } else {
    showToast(data.message || 'Could not continue', 'error');
  }
}

function onbBack(n) {
  if (n > 1) onbGoTo(n - 1);
}

function onbSkip(n) {
  onbGoTo(n + 1);
}

// Step 2 — Facebook
async function onbConnectFacebook() {
  const data = await apiCall('/settings/login', 'POST');
  showToast(data.message || 'Opening login window...', data.status === 'ok' ? 'success' : 'error');
  const btn = document.getElementById('onb-logged-in-btn');
  if (btn) btn.style.display = '';
}

// Step 3 — Groups
function onbPickSuggestion(el) {
  const name = el.dataset.name || '';
  const nameInput = document.getElementById('onb-group-name');
  if (nameInput) nameInput.value = name;
  el.classList.add('added');
  const urlInput = document.getElementById('onb-group-url');
  if (urlInput) urlInput.focus();
}

async function onbAddGroup() {
  const name = (document.getElementById('onb-group-name') || {}).value || '';
  const url = (document.getElementById('onb-group-url') || {}).value || '';
  const loc = (document.getElementById('onb-group-location') || {}).value || '';
  if (!name.trim() || !url.trim()) {
    showToast('Name and URL are required', 'error');
    return;
  }
  const data = await apiCall('/groups/add', 'POST', { name: name.trim(), url: url.trim(), location: loc.trim() });
  if (data.status === 'ok' && data.group) {
    const container = document.getElementById('onb-groups-chips');
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.dataset.id = data.group.id;
    chip.innerHTML = `<span></span><button type="button" class="chip-remove">×</button>`;
    chip.querySelector('span').textContent = data.group.name;
    chip.querySelector('button').onclick = () => onbRemoveGroup(data.group.id);
    container.appendChild(chip);
    document.getElementById('onb-group-name').value = '';
    document.getElementById('onb-group-url').value = '';
    showToast('Group added');
  } else {
    showToast(data.message || 'Failed to add group', 'error');
  }
}

async function onbRemoveGroup(groupId) {
  const data = await apiCall(`/groups/${groupId}/delete`, 'POST');
  if (data.status === 'ok') {
    const chip = document.querySelector(`#onb-groups-chips .chip[data-id="${groupId}"]`);
    if (chip) chip.remove();
  } else {
    showToast(data.message || 'Failed to remove', 'error');
  }
}

// Step 4 — Keywords
function onbTogglePreset(el) {
  el.classList.toggle('selected');
  onbSyncAllCard();
}

function onbToggleAll(el) {
  const cards = Array.from(document.querySelectorAll('.onb-step[data-step="4"] .preset-card[data-cat-id]'));
  const turnOn = !el.classList.contains('selected');
  cards.forEach((c) => c.classList.toggle('selected', turnOn));
  el.classList.toggle('selected', turnOn);
}

function onbSyncAllCard() {
  const cards = Array.from(document.querySelectorAll('.onb-step[data-step="4"] .preset-card[data-cat-id]'));
  const allCard = document.querySelector('.onb-step[data-step="4"] .preset-card[data-all]');
  if (!allCard) return;
  const allSelected = cards.length > 0 && cards.every((c) => c.classList.contains('selected'));
  allCard.classList.toggle('selected', allSelected);
}

function onbAddCustomKeyword() {
  const input = document.getElementById('onb-kw-input');
  if (!input || !input.value.trim()) return;
  const container = document.getElementById('onb-kw-chips');
  const value = input.value.trim();
  const chip = buildChip(value, () => chip.remove());
  container.appendChild(chip);
  input.value = '';
}

async function onbSaveKeywords() {
  const categories = [];
  document.querySelectorAll('.onb-step[data-step="4"] .preset-card.selected[data-cat-id]').forEach((c) => {
    let keywords = [];
    try { keywords = JSON.parse(c.dataset.keywords || '[]'); } catch (e) { keywords = []; }
    categories.push({
      id: c.dataset.catId,
      label: c.dataset.catLabel,
      keywords,
      reply_template_id: c.dataset.template || 'general',
    });
  });
  const custom = Array.from(document.querySelectorAll('#onb-kw-chips .chip')).map((c) => c.dataset.value);
  if (custom.length) {
    categories.push({ id: 'custom', label: 'Custom Keywords', keywords: custom, reply_template_id: 'general' });
  }
  if (!categories.length) {
    showToast('Pick at least one keyword set', 'error');
    return;
  }
  const saved = await apiCall('/keywords/save', 'POST', { categories, exclusions: ONB_EXCLUSIONS });
  if (saved.status !== 'ok') {
    showToast(saved.message || 'Could not save keywords', 'error');
    return;
  }
  onbNext(4);
}

// Step 5 — Replies
function onbAddReply() {
  const container = document.getElementById('onb-replies');
  const count = container.querySelectorAll('.reply-item').length;
  if (count >= 5) {
    showToast('Maximum of 5 replies', 'error');
    return;
  }
  const item = document.createElement('div');
  item.className = 'reply-item';
  item.innerHTML = `<div class="reply-number">Reply ${count + 1}</div><textarea placeholder="Write a reply..."></textarea>`;
  container.appendChild(item);
  if (count + 1 >= 5) document.getElementById('onb-add-reply').style.display = 'none';
}

async function onbFinish() {
  const messages = Array.from(document.querySelectorAll('#onb-replies textarea'))
    .map((t) => t.value.trim())
    .filter((t) => t.length > 0);
  if (!messages.length) {
    showToast('Write at least one reply', 'error');
    return;
  }
  const templates = [
    { id: 'coaching', label: 'Coaching Reply', messages },
    { id: 'hitting', label: 'Hitting Partner Reply', messages },
    { id: 'general', label: 'General Reply', messages },
  ];
  const saved = await apiCall('/replies/save', 'POST', { templates });
  if (saved.status !== 'ok') {
    showToast(saved.message || 'Could not save replies', 'error');
    return;
  }
  const done = await apiCall('/onboarding/complete', 'POST');
  if (done.status === 'ok') {
    window.location.href = done.redirect || '/';
  } else {
    showToast(done.message || 'Could not finish setup', 'error');
  }
}

// ------------------------------------------------------------------- Init

document.addEventListener('DOMContentLoaded', function () {
  // Count-up animation for stat values on first paint.
  document.querySelectorAll('.stat-value[data-count]').forEach((el) => {
    countUp(el, el.getAttribute('data-count'));
  });

  pollLogs();
  pollStatus();
  if (document.getElementById('log-panel')) setInterval(pollLogs, 5000);
  setInterval(pollStatus, 10000);

  if (document.getElementById('leads-table')) {
    document.querySelectorAll('#leads-table tbody tr').forEach((r) => { r.dataset.match = '1'; });
    paginateLeads(1);
  }

  // Close modals on overlay click.
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.classList.remove('open');
    });
  });

  // Smooth page transition on sidebar navigation.
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduceMotion) {
    document.querySelectorAll('.sidebar .nav-link').forEach((link) => {
      link.addEventListener('click', (e) => {
        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || link.target === '_blank'
            || e.metaKey || e.ctrlKey || e.shiftKey) return;
        if (href === window.location.pathname) return;
        e.preventDefault();
        document.body.classList.add('page-leaving');
        setTimeout(() => { window.location.href = href; }, 150);
      });
    });
  }
});

/* ============================================================================
   Help & Guided Setup system
   ========================================================================== */

let _helpSettings = null;
async function getHelpSettings() {
  if (_helpSettings) return _helpSettings;
  try { _helpSettings = await (await fetch('/api/settings/help')).json(); }
  catch (e) { _helpSettings = { show_help_button: true, show_feature_tips: true, completed_tours: [], dismissed_tips: [] }; }
  return _helpSettings;
}

function helpPageKey() {
  const p = window.location.pathname;
  if (p === '/' || p.startsWith('/coach')) return 'coach';
  if (p.startsWith('/money-owed')) return 'money_owed';
  if (p.startsWith('/leads')) return 'leads';
  for (const k of ['calendar', 'students', 'earnings', 'invoices', 'expenses', 'tax', 'packages', 'sms', 'dashboard', 'settings']) {
    if (p.startsWith('/' + k)) return k;
  }
  return 'coach';
}

const HELP_CONTENT = {
  coach: {
    title: 'Home',
    body: 'Your daily home screen. The left side is Today’s Schedule — switch between Compact and Timeline with the toggle, and tap a lesson to complete, text or cancel it. The right Weather Rail shows the current conditions, a court-conditions callout, this week’s forecast, and Quick Stats including any money owed.',
    tour: 'basics',
    actions: [
      { icon: 'ti-plus', label: 'Add a lesson', onclick: "closeHelp(); if(window.Coach) Coach.openAddLesson();" },
      { icon: 'ti-calendar', label: 'Open calendar', href: '/calendar' },
      { icon: 'ti-cash', label: 'Money owed', href: '/money-owed' },
    ],
  },
  calendar: {
    title: 'Calendar',
    body: 'Your lesson schedule in Day, Week or Month view — use the toggle to switch. Tap any empty slot or the + on a day to add a lesson, and tap an existing lesson to edit, complete or cancel it.',
    tour: 'book_lesson',
    actions: [
      { icon: 'ti-plus', label: 'Add a lesson', onclick: "closeHelp(); if(window.Coach) Coach.openAddLesson();" },
      { icon: 'ti-home', label: 'Back to Home', href: '/coach' },
      { icon: 'ti-users', label: 'Manage students', href: '/students' },
    ],
  },
  students: {
    title: 'Students',
    body: 'All your students in one place. Add new students here, or add one on the fly while booking a lesson. Tap a student to see their full lesson history, payments and notes.',
    tour: 'add_student',
    actions: [
      { icon: 'ti-user-plus', label: 'Add a student', onclick: "closeHelp(); if(window.Coach) Coach.openAddStudent();" },
      { icon: 'ti-calendar', label: 'Open calendar', href: '/calendar' },
      { icon: 'ti-message-2', label: 'Send an SMS', href: '/sms' },
    ],
  },
  money_owed: {
    title: 'Money Owed',
    body: 'Everyone with unpaid lessons, newest debts first. Each card shows how much they owe, how many lessons, and how many days it has been outstanding (amber after a week, red after two). Tap “Mark All Paid” once they pay, or SMS them a reminder.',
    tour: 'money_owed',
    actions: [
      { icon: 'ti-home', label: 'Back to Home', href: '/coach' },
      { icon: 'ti-users', label: 'View students', href: '/students' },
      { icon: 'ti-chart-bar', label: 'View earnings', href: '/earnings' },
    ],
  },
  leads: {
    title: 'Facebook Leads',
    body: 'People Ivan found asking about coaching in your Facebook groups. Each lead is scored 1–5 stars by how likely they are to book, and tagged New, Contacted or Booked. Use “Suggest Reply” to draft a message to copy into Facebook, then “Convert” to turn a lead into a student. Filter by status, group or text up top.',
    tour: 'leads',
    actions: [
      { icon: 'ti-search', label: 'Keywords', href: '/keywords' },
      { icon: 'ti-users', label: 'Groups', href: '/groups' },
      { icon: 'ti-settings', label: 'Lead Monitor settings', href: '/settings' },
    ],
  },
  earnings: {
    title: 'Earnings',
    body: 'Track your income with weekly, monthly and daily charts. Update your lesson prices here too.',
    tour: 'earnings_tax',
    actions: [
      { icon: 'ti-settings', label: 'Update prices', href: '/settings' },
      { icon: 'ti-file-invoice', label: 'View invoices', href: '/invoices' },
      { icon: 'ti-calculator', label: 'Tax estimator', href: '/tax' },
    ],
  },
  invoices: {
    title: 'Invoices',
    body: 'Generate PDF invoices for completed lessons. Download and send to students.',
    tour: 'earnings_tax',
    actions: [
      { icon: 'ti-settings', label: 'Invoice settings', href: '/settings' },
      { icon: 'ti-chart-bar', label: 'View earnings', href: '/earnings' },
      { icon: 'ti-home', label: 'Back to Today', href: '/coach' },
    ],
  },
  expenses: {
    title: 'Expenses',
    body: 'Log your business expenses. Ivan tracks your ATO deductions including km travel and home office hours.',
    tour: 'earnings_tax',
    actions: [
      { icon: 'ti-calculator', label: 'Tax estimator', href: '/tax' },
      { icon: 'ti-settings', label: 'Settings', href: '/settings' },
      { icon: 'ti-chart-bar', label: 'View earnings', href: '/earnings' },
    ],
  },
  tax: {
    title: 'Tax Estimator',
    body: 'See your estimated tax based on real earnings and expenses. Shows GST threshold and quarterly PAYG amounts.',
    tour: 'earnings_tax',
    actions: [
      { icon: 'ti-chart-bar', label: 'View earnings', href: '/earnings' },
      { icon: 'ti-receipt', label: 'View expenses', href: '/expenses' },
      { icon: 'ti-settings', label: 'Tax settings', href: '/settings' },
    ],
  },
  packages: {
    title: 'Packages',
    body: 'Sell lesson packs upfront at a discount. Ivan tracks how many sessions remain for each student.',
    tour: 'basics',
    actions: [
      { icon: 'ti-users', label: 'View students', href: '/students' },
      { icon: 'ti-file-invoice', label: 'View invoices', href: '/invoices' },
      { icon: 'ti-home', label: 'Back to Today', href: '/coach' },
    ],
  },
  sms: {
    title: 'SMS',
    body: 'Send text messages to individual students or groups. Set up Twilio first in Settings to enable this.',
    tour: 'send_sms',
    actions: [
      { icon: 'ti-settings', label: 'SMS settings', href: '/settings' },
      { icon: 'ti-users', label: 'Manage students', href: '/students' },
      { icon: 'ti-home', label: 'Back to Today', href: '/coach' },
    ],
  },
  dashboard: {
    title: 'Lead Monitor',
    body: 'Finds people looking for tennis coaching in Facebook groups. Start the bot to begin monitoring.',
    tour: 'basics',
    actions: [
      { icon: 'ti-target', label: 'View leads', href: '/leads' },
      { icon: 'ti-search', label: 'Keywords', href: '/keywords' },
      { icon: 'ti-users', label: 'Groups', href: '/groups' },
    ],
  },
  settings: {
    title: 'Settings',
    body: 'Everything about Ivan, grouped into tap-to-open sections. Set your prices and quick presets, your weekly availability (a start and end time per day, with handy presets), and connect SMS (Twilio) and AI (GroqCloud). Both the SMS and AI sections have a “How to set this up?” panel that walks you through it step by step.',
    tour: 'settings',
    actions: [
      { icon: 'ti-route', label: 'Take Ivan Basics tour', onclick: "closeHelp(); startTour('basics');" },
      { icon: 'ti-settings', label: 'Settings tour', onclick: "closeHelp(); startTour('settings');" },
      { icon: 'ti-home', label: 'Back to Home', href: '/coach' },
    ],
  },
};

async function openHelp() {
  const key = helpPageKey();
  const c = HELP_CONTENT[key] || HELP_CONTENT.coach;
  document.getElementById('help-drawer-title').textContent = c.title;
  document.getElementById('help-drawer-body').textContent = c.body;
  const qa = document.getElementById('help-quick-actions');
  qa.innerHTML = (c.actions || []).map(a => {
    const inner = `<i class="ti ${a.icon}"></i> ${a.label}`;
    if (a.href) return `<a class="help-qa" href="${a.href}">${inner}</a>`;
    return `<button class="help-qa" onclick="${a.onclick}">${inner}</button>`;
  }).join('');
  const tourBtn = document.getElementById('help-tour-btn');
  tourBtn.dataset.tour = c.tour || '';
  tourBtn.style.display = c.tour ? '' : 'none';
  document.getElementById('help-overlay').classList.add('open');
  document.getElementById('help-drawer').classList.add('open');
  document.getElementById('help-drawer').setAttribute('aria-hidden', 'false');
}
function closeHelp() {
  document.getElementById('help-overlay').classList.remove('open');
  document.getElementById('help-drawer').classList.remove('open');
  document.getElementById('help-drawer').setAttribute('aria-hidden', 'true');
}
function startPageTour() {
  const tour = document.getElementById('help-tour-btn').dataset.tour;
  closeHelp();
  if (tour) startTour(tour);
}

/* ---- Tour definitions ----------------------------------------------------- */
const TOURS = {
  basics: {
    id: 'basics', name: 'Ivan Basics', steps: [
      { selector: '.sidebar', title: 'Your Main Menu', text: 'Use this to navigate between all parts of Ivan — Home, Calendar, Students, Money Owed, Facebook Leads and more.', position: 'right' },
      { selector: '.schedule-card', title: "Today's Schedule", text: 'Your lessons for today live here. Tap a lesson to mark it complete, text the student, or cancel it.', position: 'right' },
      { selector: '.sched-view-toggle', title: 'Compact or Timeline', text: 'Switch between a tidy Compact list and a full hour-by-hour Timeline of your day.', position: 'bottom' },
      { selector: '.wx-card', title: 'Weather & Court Conditions', text: "Today's weather updates automatically. The callout box tells you if it's good, marginal or poor for tennis — and when the lights come on.", position: 'left' },
      { selector: '.weekpeek-card', title: 'This Week', text: 'A quick look at the week ahead. Tap any day to jump to it in the calendar.', position: 'left' },
      { selector: '.qstats-card', title: 'Quick Stats', text: 'This week and month at a glance, plus any money owed and your next lesson.', position: 'left' },
    ],
  },
  add_student: {
    id: 'add_student', name: 'Adding a Student', steps: [
      { selector: 'a.nav-link[href="/students"]', title: 'Students', text: 'Click Students to manage all your coaching clients.', position: 'right' },
      { selector: 'button[onclick*="openAddStudent"]', title: 'Add Student', text: 'Click here to add a new student.', position: 'bottom' },
      { selector: '#student-name', title: 'Student Details', text: 'Fill in their name, phone, level and default price. The phone number is used for SMS reminders.', position: 'right' },
      { selector: '#student-modal .btn-primary', title: 'Save', text: 'Click Save Student and they appear in your roster immediately. Tip: you can also add a student on the fly while booking a lesson.', position: 'top' },
    ],
  },
  book_lesson: {
    id: 'book_lesson', name: 'Booking a Lesson', steps: [
      { selector: 'button[onclick*="openAddLesson"]', title: 'Add Lesson', text: 'Tap Add Lesson on Home or the Calendar to book a new lesson.', position: 'bottom' },
      { selector: '#lesson-student', title: 'Choose Student', text: 'Pick an existing student — or choose “Add new student” to create one right here without leaving the form.', position: 'right' },
      { selector: '.date-picker', title: 'Date & Time', text: 'Tap to open the dark date picker and choose the day, then set the start time.', position: 'right' },
      { selector: '.duration-picker', title: 'Lesson Duration', text: 'Tap a quick-pick (30, 45, 60, 90 min or 2 hours), or use the +/− stepper for a custom length.', position: 'right' },
      { selector: '#price-presets', title: 'Price', text: 'The price fills in from your settings. Tap a preset to change it instantly, or type any amount.', position: 'right' },
      { selector: '#lesson-recurring', title: 'Repeat Weekly', text: 'Turn this on to repeat the lesson weekly. Then choose how many weeks (4, 8, 10, 12 or Ongoing) and Ivan books them all.', position: 'top' },
    ],
  },
  money_owed: {
    id: 'money_owed', name: 'Money Owed', steps: [
      { selector: 'a.nav-link[href="/money-owed"]', title: 'Money Owed', text: 'Open Money Owed to see exactly who still owes you for lessons.', position: 'right' },
      { selector: '.owed-summary', title: 'Total Outstanding', text: 'The big number is everything owed across all students right now.', position: 'bottom' },
      { selector: '.owed-card', title: 'Each Student', text: 'One card per student — how much they owe, how many lessons, and how long it has been outstanding (amber after a week, red after two).', position: 'top' },
      { selector: 'button[onclick*="markAllPaid"]', title: 'Mark Paid', text: 'When they pay, tap “Mark All Paid” to clear their balance. The SMS button sends a friendly payment reminder.', position: 'top' },
    ],
  },
  leads: {
    id: 'leads', name: 'Facebook Leads', steps: [
      { selector: 'a.nav-link[href="/leads"]', title: 'Facebook Leads', text: 'People Ivan found asking about tennis coaching in your Facebook groups.', position: 'right' },
      { selector: '.lead-stars', title: 'Lead Score', text: 'Each lead is scored 1–5 stars and tagged by intent, so you can spot the hottest prospects first.', position: 'bottom' },
      { selector: '.filter-bar', title: 'Filter', text: 'Narrow the list by status (New / Contacted / Booked), by group, or by searching the post text.', position: 'bottom' },
      { selector: 'button[onclick*="suggestReply"]', title: 'Suggest Reply', text: 'Ivan drafts a reply you can copy and paste into Facebook. Ivan never auto-posts — you stay in control.', position: 'top' },
      { selector: 'button[onclick*="convertLead"]', title: 'Convert to Student', text: 'Once they book, tap Convert to create a student record and mark the lead as Booked.', position: 'top' },
      { selector: '.lead-mark', title: 'Track the Status', text: 'Move each lead through New → Contacted → Booked so you always know where you stand.', position: 'top' },
    ],
  },
  send_sms: {
    id: 'send_sms', name: 'Sending an SMS', steps: [
      { selector: 'a.nav-link[href="/sms"]', title: 'SMS', text: 'Click SMS to send text messages to your students.', position: 'right' },
      { selector: '#sms-tabs, .filter-tabs', title: 'Groups', text: 'Create groups here — for example Monday Group or Advanced Students.', position: 'bottom' },
      { selector: '#sms-template, .template-picker', title: 'Template', text: 'Choose a message template. Rain Cancellation is perfect for weather cancellations.', position: 'bottom' },
      { selector: '#sms-message, textarea', title: 'Compose', text: 'Review and edit the message. {name} automatically becomes each student’s first name.', position: 'top' },
      { selector: '#sms-send, button[onclick*="send"]', title: 'Send', text: 'Tap Send and Ivan texts all selected contacts. You see a delivery report immediately.', position: 'top' },
    ],
  },
  settings: {
    id: 'settings', name: 'Settings Tour', steps: [
      { selector: 'a.nav-link[href="/settings"]', title: 'Settings', text: 'Everything is configured here. Open Settings to begin.', position: 'right' },
      { selector: '.settings-accordion', title: 'Tap-to-Open Sections', text: 'Settings are grouped into sections. Tap a section header to open it; tap again to close it.', position: 'bottom' },
      { selector: '#acc-pricing, [onclick*="\'pricing\'"]', title: 'Pricing & Presets', text: 'Set your per-duration prices and add quick price presets you can tap when booking.', position: 'bottom' },
      { selector: '#acc-availability, [onclick*="availability"]', title: 'Weekly Availability', text: 'Set a start and end time for each day, or tap a preset like Weekday Mornings. Then Save.', position: 'bottom' },
      { selector: '#acc-sms, [onclick*="\'sms\'"]', title: 'SMS Setup', text: 'Connect Twilio so Ivan can text students. Open the “How to set up SMS” panel for step-by-step help.', position: 'bottom' },
      { selector: '#acc-ai, [onclick*="\'ai\'"]', title: 'AI Setup', text: 'Connect a free GroqCloud key for AI extras. Open the “How to set up AI” panel for step-by-step help.', position: 'bottom' },
    ],
  },
  twilio_setup: {
    id: 'twilio_setup', name: 'Setting Up SMS with Twilio', steps: [
      { selector: 'a.nav-link[href="/settings"]', title: 'Settings', text: 'First go to Settings.', position: 'right' },
      { selector: '#acc-sms, [onclick*="\'sms\'"]', title: 'SMS / Twilio', text: 'Open the SMS / Twilio section. The “How to set up SMS” panel inside has the full step-by-step.', position: 'bottom' },
      { selector: '#tw_sid', title: 'Account SID', text: 'Paste your Twilio Account SID here. You get it from the Twilio Console at twilio.com.', position: 'right' },
      { selector: '#tw_token', title: 'Auth Token', text: 'Paste your Auth Token here.', position: 'right' },
      { selector: '#tw_from', title: 'From Number', text: 'Paste the Australian Twilio number you bought (texts are sent from this number).', position: 'right' },
      { selector: 'button[onclick*="testTwilio"]', title: 'Test Connection', text: "Click this to test — you'll receive a test text on your own phone.", position: 'top' },
    ],
  },
  earnings_tax: {
    id: 'earnings_tax', name: 'Earnings and Tax', steps: [
      { selector: 'a.nav-link[href="/earnings"]', title: 'Earnings', text: 'Click Earnings to see your income charts.', position: 'right' },
      { selector: '#chart-weekly', title: 'Weekly Chart', text: 'This shows your earnings for the last 8 weeks.', position: 'bottom' },
      { selector: '.chart-grid, #chart-monthly', title: 'Your Charts', text: 'Weekly, monthly and daily views of your income.', position: 'top' },
      { selector: 'a.nav-link[href="/tax"]', title: 'Tax Estimator', text: 'Click Tax Estimator to see your estimated Australian tax based on real data.', position: 'right' },
    ],
  },
};

/* ---- Tour runtime --------------------------------------------------------- */
let _tourState = null;

function startTour(tourId) {
  const tour = TOURS[tourId];
  if (!tour) return;
  endTour();
  const overlay = document.createElement('div');
  overlay.className = 'tour-overlay';
  document.body.appendChild(overlay);
  _tourState = { tour, index: 0, overlay };
  showTourStep(0);
}

function _cleanupTourEls() {
  document.querySelector('.tour-tooltip')?.remove();
  document.querySelector('.tour-highlight')?.remove();
}

function showTourStep(index) {
  if (!_tourState) return;
  const tour = _tourState.tour;
  if (index < 0) index = 0;
  if (index >= tour.steps.length) { return; }
  _tourState.index = index;
  _cleanupTourEls();

  const step = tour.steps[index];
  const target = document.querySelector(step.selector);
  const rect = target ? target.getBoundingClientRect() : null;
  const visible = rect && rect.width > 0 && rect.height > 0;

  let hi = null;
  if (visible) {
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    hi = document.createElement('div');
    hi.className = 'tour-highlight';
    hi.style.cssText = `top:${rect.top - 4}px;left:${rect.left - 4}px;width:${rect.width + 8}px;height:${rect.height + 8}px;border:2px solid #00c88a;border-radius:8px;box-shadow:0 0 0 4000px rgba(0,0,0,0.6);`;
    document.body.appendChild(hi);
  }

  const tip = document.createElement('div');
  tip.className = 'tour-tooltip';
  const last = index === tour.steps.length - 1;
  tip.innerHTML = `
    <div class="tour-header">
      <span class="tour-step-num">${index + 1} of ${tour.steps.length}</span>
      <button onclick="endTour()" class="tour-skip">Skip tour</button>
    </div>
    <h3 class="tour-title">${step.title}</h3>
    <p class="tour-text">${step.text}</p>
    <div class="tour-dots">
      ${tour.steps.map((_, i) => `<span class="tour-dot ${i === index ? 'active' : (i < index ? 'done' : '')}"></span>`).join('')}
    </div>
    <div class="tour-nav">
      ${index > 0 ? '<button onclick="prevTourStep()" class="btn btn-secondary btn-small">Back</button>' : '<span></span>'}
      ${last
        ? `<button onclick="completeTour('${tour.id}')" class="btn btn-primary btn-small">Done!</button>`
        : '<button onclick="nextTourStep()" class="btn btn-primary btn-small">Next</button>'}
    </div>`;
  document.body.appendChild(tip);
  positionTooltip(tip, rect, step.position);
}

function positionTooltip(tip, rect, position) {
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  const m = 14;
  let top, left;
  if (!rect) {
    top = (vh - th) / 2; left = (vw - tw) / 2;
  } else {
    switch (position) {
      case 'right': left = rect.right + m; top = rect.top; break;
      case 'left': left = rect.left - tw - m; top = rect.top; break;
      case 'top': left = rect.left; top = rect.top - th - m; break;
      default: left = rect.left; top = rect.bottom + m; break; // bottom
    }
  }
  left = Math.max(m, Math.min(left, vw - tw - m));
  top = Math.max(m, Math.min(top, vh - th - m));
  tip.style.left = left + 'px';
  tip.style.top = top + 'px';
}

function nextTourStep() { if (_tourState) showTourStep(_tourState.index + 1); }
function prevTourStep() { if (_tourState) showTourStep(_tourState.index - 1); }
function endTour() {
  _cleanupTourEls();
  document.querySelector('.tour-overlay')?.remove();
  _tourState = null;
}
function completeTour(id) {
  endTour();
  fetch('/api/settings/complete-tour', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tour_id: id }),
  }).then(() => { _helpSettings = null; if (typeof renderTourList === 'function') renderTourList(); });
  if (typeof showToast === 'function') showToast('Tour complete!', 'success');
}
async function resetAllTours() {
  await fetch('/api/settings/reset-tours', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  _helpSettings = null;
  if (typeof renderTourList === 'function') renderTourList();
  if (typeof showToast === 'function') showToast('All tours reset', 'success');
}

/* ---- First-use feature tips ----------------------------------------------- */
const FEATURE_TIPS = {
  sms: { id: 'tip_sms', text: 'New to SMS? Connect Twilio in Settings first — open the “How to set up SMS” panel there for a step-by-step guide.', actions: [{ label: 'Set up SMS', href: '/settings' }, { label: 'Dismiss', dismiss: true }] },
  tax: { id: 'tip_tax', text: 'This is an estimate based on your real Ivan data. Always check with your accountant for official advice.', actions: [{ label: 'Got it', dismiss: true }] },
  expenses: { id: 'tip_expenses', text: 'Log your business expenses here to reduce your taxable income. Ivan uses ATO categories for tennis coaches.', actions: [{ label: 'Got it', dismiss: true }] },
  leads: { id: 'tip_leads', text: 'These are people Ivan found in Facebook groups, scored by how likely they are to book. Use “Suggest Reply” to draft a message, then “Convert” to turn a lead into a student.', actions: [{ label: 'Got it', dismiss: true }] },
  money_owed: { id: 'tip_money_owed', text: 'Everyone with unpaid lessons, oldest debts first. Tap “Mark All Paid” when a student pays, or SMS them a reminder.', actions: [{ label: 'Got it', dismiss: true }] },
};

async function maybeShowFeatureTip() {
  const h = await getHelpSettings();
  if (!h.show_feature_tips) return;
  const key = helpPageKey();
  const tip = FEATURE_TIPS[key];
  if (!tip || (h.dismissed_tips || []).includes(tip.id)) return;
  const content = document.querySelector('.content');
  if (!content) return;
  const card = document.createElement('div');
  card.className = 'feature-tip';
  card.innerHTML = `
    <i class="ti ti-bulb tip-icon"></i>
    <div class="tip-text">${tip.text}</div>
    <div class="tip-actions">
      ${tip.actions.map(a => a.href
        ? `<a class="btn btn-primary" href="${a.href}">${a.label}</a>`
        : `<button class="btn btn-secondary" data-dismiss="1">${a.label}</button>`).join('')}
    </div>`;
  const header = content.querySelector('.page-header');
  if (header && header.nextSibling) content.insertBefore(card, header.nextSibling);
  else content.insertBefore(card, content.firstChild);
  card.querySelectorAll('[data-dismiss]').forEach(btn => btn.addEventListener('click', () => {
    card.remove();
    fetch('/api/settings/dismiss-tip', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tip_id: tip.id }) });
    _helpSettings = null;
  }));
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (_tourState) endTour();
    else if (document.getElementById('help-drawer')?.classList.contains('open')) closeHelp();
  }
});

document.addEventListener('DOMContentLoaded', () => { maybeShowFeatureTip(); });
