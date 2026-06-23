// imapsync UI - vanilla JS, zero deps (besides Tailwind via CDN).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ----- account form management -----------------------------------------

const accountsRoot = $('#accounts');
const tplAcc = $('#tpl-account');
const runRoot = $('#runs');
const tplRun = $('#tpl-run');

function makeAccount(initial = {}) {
  const node = tplAcc.content.firstElementChild.cloneNode(true);
  node.dataset.id = crypto.randomUUID().slice(0, 8);
  $('.acc-name', node).value = initial.name || `account-${accountsRoot.children.length + 1}`;
  $('.host1', node).value = initial.host1 || '';
  $('.port1', node).value = initial.port1 || '';
  $('.user1', node).value = initial.user1 || '';
  $('.password1', node).value = initial.password1 || '';
  $('.host2', node).value = initial.host2 || '';
  $('.port2', node).value = initial.port2 || '';
  $('.user2', node).value = initial.user2 || '';
  $('.password2', node).value = initial.password2 || '';
  if (initial.automap) $('.opt-automap', node).checked = true;
  if (initial.delete2) $('.opt-delete2', node).checked = true;
  if (initial.dry) $('.opt-dry', node).checked = true;

  $('.btn-remove', node).addEventListener('click', () => node.remove());
  accountsRoot.appendChild(node);
  return node;
}

function readAccounts() {
  return $$('.account', accountsRoot).map((node) => ({
    name: $('.acc-name', node).value,
    host1: $('.host1', node).value.trim(),
    port1: parseIntOrNull($('.port1', node).value),
    user1: $('.user1', node).value,
    password1: $('.password1', node).value,
    enc1: $('.enc1', node).value,
    host2: $('.host2', node).value.trim(),
    port2: parseIntOrNull($('.port2', node).value),
    user2: $('.user2', node).value,
    password2: $('.password2', node).value,
    enc2: $('.enc2', node).value,
    options: {
      ssl1: $('.enc1', node).value === 'ssl',
      tls1: $('.enc1', node).value === 'tls',
      ssl2: $('.enc2', node).value === 'ssl',
      tls2: $('.enc2', node).value === 'tls',
      delete2: $('.opt-delete2', node).checked,
      automap: $('.opt-automap', node).checked,
      dry: $('.opt-dry', node).checked,
    },
  }));
}

function parseIntOrNull(v) {
  if (!v) return null;
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}

$('#btn-add').addEventListener('click', () => makeAccount());
$('#btn-clear').addEventListener('click', () => {
  $$('.logs').forEach((l) => (l.textContent = ''));
});

// seed with one row
makeAccount();

// ----- run / progress rendering ----------------------------------------

const activeRuns = new Map(); // run_id -> { ws, panel, accounts: Map(acc_id -> DOM) }

$('#btn-start').addEventListener('click', async () => {
  const accounts = readAccounts().filter(
    (a) => a.host1 && a.user1 && a.password1 && a.host2 && a.user2 && a.password2,
  );
  if (!accounts.length) {
    alert('Fill at least one account pair (host/user/password on both sides).');
    return;
  }
  const parallel = $('#parallel').checked;
  const btn = $('#btn-start');
  btn.disabled = true;
  btn.textContent = 'Starting…';
  try {
    const resp = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ accounts, parallel }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    openRun(data.run_id, data.snapshot);
  } catch (err) {
    alert('Failed to start: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Start sync';
  }
});

function openRun(runId, snapshot) {
  const node = tplRun.content.firstElementChild.cloneNode(true);
  node.dataset.runId = runId;
  $('.run-id', node).textContent = '#' + runId;
  const accountsContainer = $('.accounts', node);

  const accountPanes = new Map();
  for (const a of snapshot.accounts) {
    const pane = buildAccountProgressPane(a);
    accountsContainer.appendChild(pane.el);
    accountPanes.set(a.id, pane);
  }

  $('.btn-abort', node).addEventListener('click', async () => {
    await fetch(`/api/sync/${runId}/abort`, { method: 'POST' });
  });

  runRoot.prepend(node);
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/${runId}`);
  setConnStatus('connecting');
  ws.onopen = () => setConnStatus('live');
  ws.onclose = () => {
    setConnStatus('idle');
    setRunStatus(node, 'disconnected', 'warn');
  };
  ws.onmessage = (msg) => {
    const payload = JSON.parse(msg.data);
    handleEvent(node, accountPanes, payload);
  };
  activeRuns.set(runId, { ws, panel: node, accounts: accountPanes });
}

function buildAccountProgressPane(acc) {
  const el = document.createElement('div');
  el.className = 'rounded-xl border border-line p-4 bg-bg/40';
  el.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <div class="flex items-center gap-2 min-w-0">
        <span class="dot w-1.5 h-1.5 rounded-full bg-white/30"></span>
        <span class="text-sm font-semibold truncate">${escapeHtml(acc.name)}</span>
        <span class="text-xs text-white/40 mono truncate">${escapeHtml(acc.user1)}@${escapeHtml(acc.host1)} → ${escapeHtml(acc.user2)}@${escapeHtml(acc.host2)}</span>
      </div>
      <div class="flex items-center gap-3 text-xs text-white/50">
        <span class="phase text-white/40">queued</span>
        <span class="rc text-white/40"></span>
      </div>
    </div>
    <div class="progress-wrap mb-2">
      <div class="flex items-center justify-between text-[11px] text-white/50 mb-1">
        <span class="folder-name">—</span>
        <span class="pct mono">0%</span>
      </div>
      <div class="h-1.5 rounded-full bg-line overflow-hidden">
        <div class="bar h-full bg-accent" style="width: 0%"></div>
      </div>
    </div>
    <div class="grid grid-cols-2 gap-2 text-[11px] text-white/50 mb-2">
      <div>host1: <span class="bytes-h1 mono">0 B</span></div>
      <div>host2: <span class="bytes-h2 mono">0 B</span></div>
      <div>errors: <span class="err-count mono">0</span></div>
      <div>folders: <span class="folders mono">0</span></div>
    </div>
    <details class="logs-wrap">
      <summary class="text-[11px] text-white/40 hover:text-white/70 transition">view log</summary>
      <div class="logs mono scrollbar text-[11px] text-white/60 mt-2 max-h-64 overflow-auto bg-bg/60 rounded-md p-2 border border-line"></div>
    </details>
  `;
  return {
    el,
    bar: el.querySelector('.bar'),
    pct: el.querySelector('.pct'),
    folder: el.querySelector('.folder-name'),
    dot: el.querySelector('.dot'),
    phase: el.querySelector('.phase'),
    rc: el.querySelector('.rc'),
    bytesH1: el.querySelector('.bytes-h1'),
    bytesH2: el.querySelector('.bytes-h2'),
    errCount: el.querySelector('.err-count'),
    folders: el.querySelector('.folders'),
    logs: el.querySelector('.logs'),
  };
}

function handleEvent(runNode, accountPanes, payload) {
  const { type, account_id, data } = payload;
  const pane = account_id ? accountPanes.get(account_id) : null;

  if (type === 'snapshot') {
    // initial state; could re-render but we already built panes
    return;
  }

  if (type === 'run_finished') {
    setRunStatus(runNode, data.status, data.status === 'completed' ? 'ok' : data.status === 'aborted' ? 'warn' : 'err');
    return;
  }

  if (!pane) return;

  switch (type) {
    case 'started':
      pane.dot.classList.replace('bg-white/30', 'bg-warn');
      pane.dot.classList.add('dot-running');
      pane.phase.textContent = 'connecting…';
      break;
    case 'line':
      appendLog(pane, data.text);
      break;
    case 'folder':
      if (data.kind === 'folder_start') {
        pane.folder.textContent = data.name;
        pane.phase.textContent = `syncing ${truncate(data.name, 32)}`;
        pane.folders.textContent = String(parseInt(pane.folders.textContent, 10) + 1);
      }
      break;
    case 'progress':
      pane.bar.style.width = data.percent + '%';
      pane.pct.textContent = data.percent + '%';
      pane.folder.textContent = `${truncate(data.source_folder, 24)} (${data.current}/${data.total})`;
      break;
    case 'size':
      if (data.side === 'host1') pane.bytesH1.textContent = humanBytes(data.bytes);
      else pane.bytesH2.textContent = humanBytes(data.bytes);
      break;
    case 'stat':
      if (data.errors !== undefined) pane.errCount.textContent = data.errors;
      if (data.exit_code !== undefined) pane.rc.textContent = `exit ${data.exit_code}`;
      if (data.sync_good) {
        pane.dot.classList.replace('bg-warn', 'bg-ok');
        pane.dot.classList.remove('dot-running');
        pane.phase.textContent = 'sync good';
      }
      break;
    case 'finished':
      pane.dot.classList.remove('dot-running');
      pane.dot.classList.replace('bg-warn', data.exit_code === 0 ? 'bg-ok' : 'bg-err');
      pane.phase.textContent = data.exit_code === 0 ? 'done' : `failed (${data.exit_code})`;
      pane.rc.textContent = `exit ${data.exit_code}`;
      pane.bar.style.width = '100%';
      pane.pct.textContent = '100%';
      break;
  }
}

function appendLog(pane, text) {
  const line = document.createElement('div');
  line.className = 'log-line';
  if (/error|fail|fatal/i.test(text)) line.classList.add('err');
  else if (/warn|skip/i.test(text)) line.classList.add('warn');
  else if (/The sync looks good|Hoora|Exiting with return value 0/i.test(text)) line.classList.add('ok');
  line.textContent = text;
  pane.logs.appendChild(line);
  // auto-scroll if user is near bottom
  const nearBottom = pane.logs.scrollHeight - pane.logs.scrollTop - pane.logs.clientHeight < 80;
  if (nearBottom) pane.logs.scrollTop = pane.logs.scrollHeight;
  // cap at 2000 lines
  while (pane.logs.childElementCount > 2000) pane.logs.firstChild.remove();
}

function setRunStatus(node, status, kind) {
  const el = node.querySelector('.run-status');
  el.textContent = status;
  el.className = 'run-status text-xs px-2 py-0.5 rounded-md border ' +
    (kind === 'ok' ? 'border-ok/40 bg-ok/10 text-ok' :
     kind === 'warn' ? 'border-warn/40 bg-warn/10 text-warn' :
     kind === 'err' ? 'border-err/40 bg-err/10 text-err' :
     'border-line bg-line/60 text-white/60');
}

function setConnStatus(state) {
  const el = $('#conn-status');
  if (state === 'live') {
    el.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-ok dot-running"></span> live';
  } else if (state === 'connecting') {
    el.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-warn dot-running"></span> connecting';
  } else {
    el.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-white/30"></span> idle';
  }
}

// ----- utils ----------------------------------------------------------

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
function truncate(s, n) {
  s = String(s ?? '');
  return s.length > n ? s.slice(0, n - 1) + '…' : s;
}
function humanBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

// ----- persist form between reloads -----------------------------------

const LS_KEY = 'imapsync-ui:accounts';
function persist() {
  const data = $$('.account', accountsRoot).map((n) => ({
    name: $('.acc-name', n).value,
    host1: $('.host1', n).value,
    port1: $('.port1', n).value,
    user1: $('.user1', n).value,
    host2: $('.host2', n).value,
    port2: $('.port2', n).value,
    user2: $('.user2', n).value,
    automap: $('.opt-automap', n).checked,
    delete2: $('.opt-delete2', n).checked,
    dry: $('.opt-dry', n).checked,
    enc1: $('.enc1', n).value,
    enc2: $('.enc2', n).value,
  }));
  localStorage.setItem(LS_KEY, JSON.stringify(data));
}
function restore() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const arr = JSON.parse(raw);
    accountsRoot.innerHTML = '';
    for (const a of arr) makeAccount(a);
    if (!accountsRoot.children.length) makeAccount();
  } catch {}
}
accountsRoot.addEventListener('input', persist);
restore();