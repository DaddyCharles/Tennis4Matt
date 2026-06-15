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

function showToast(message, type = 'success') {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
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
  if (!confirm('Delete this lead permanently?')) return;
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
  if (!confirm('This will replace your current keywords. Continue?')) return;
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
  if (!confirm('Delete this group?')) return;
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
    user_title: get('user_title'),
    user_name: get('user_name'),
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
  if (!confirm('This will restart the setup wizard. Continue?')) return;
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
