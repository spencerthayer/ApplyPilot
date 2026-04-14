// ApplyPilot — in-page capture button
// Injected on all http/https pages. Shows a floating pill on job pages
// when an apply worker is reachable. Constants from config.js.

let _workerPort = null;
let _pill = null;
let _state = 'idle';

// --- Job page detection --------------------------------------------------

const JOB_URL_PATTERNS = [
  /\/jobs?\//i, /\/careers?\//i, /\/job-detail/i, /\/position/i,
  /\/openings?\//i, /\/apply/i, /\/posting/i, /\/vacancy/i,
  /workday\.com/, /greenhouse\.io/, /lever\.co/, /icims\.com/,
  /myworkdayjobs\.com/, /taleo\.net/, /ashbyhq\.com/, /jobvite\.com/,
  /linkedin\.com\/jobs/, /indeed\.com\/viewjob/, /dice\.com\/job-detail/,
];

function looksLikeJobPage() {
  const url = location.href;
  if (JOB_URL_PATTERNS.some(re => re.test(url))) return true;
  const title = document.title.toLowerCase();
  return /engineer|developer|devops|platform|backend|frontend|staff|principal|senior/.test(title)
    && /job|position|opening|career|apply/.test(title);
}

if (!looksLikeJobPage()) throw new Error('not a job page');

// --- UI ------------------------------------------------------------------

function createPill() {
  const pill = document.createElement('div');
  pill.id = 'applypilot-capture-pill';
  pill.style.cssText = `
    position:fixed; bottom:18px; right:18px; z-index:2147483647;
    background:#312e81; color:#e0e7ff;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    font-size:12px; font-weight:500; padding:7px 14px 7px 10px;
    border-radius:20px; box-shadow:0 2px 12px rgba(0,0,0,0.4);
    cursor:pointer; display:flex; align-items:center; gap:6px;
    transition:background 0.15s,opacity 0.15s; user-select:none;
    border:1px solid #4f46e5;
  `;
    pill.addEventListener('mouseenter', () => {
        if (_state === 'idle') pill.style.background = '#3730a3';
    });
    pill.addEventListener('mouseleave', () => {
        if (_state === 'idle') pill.style.background = '#312e81';
    });
  pill.addEventListener('click', captureJob);
  return pill;
}

const PILL_STATES = {
    idle: {bg: '#312e81', border: '#4f46e5', text: '📥 Add to ApplyPilot'},
    loading: {bg: '#1e1b4b', border: '#3730a3', text: '⏳ Adding…'},
    success: {bg: '#064e3b', border: '#059669', text: '✓ Added to pipeline'},
    exists: {bg: '#1e3a5f', border: '#2563eb', text: '↩ Already in pipeline'},
    error: {bg: '#450a0a', border: '#b91c1c', text: '✗ Error'},
};

function setPillState(state, msg) {
  _state = state;
  if (!_pill) return;
    const cfg = PILL_STATES[state] || PILL_STATES.idle;
  _pill.style.background = cfg.bg;
  _pill.style.borderColor = cfg.border;
    _pill.textContent = msg || cfg.text;
  if (state !== 'idle' && state !== 'loading') {
    setTimeout(() => setPillState('idle'), 4000);
  }
}

// --- Capture logic -------------------------------------------------------

async function captureJob() {
  if (_state === 'loading') return;
  setPillState('loading');

  if (!_workerPort) await findWorker();
    if (!_workerPort) {
        setPillState('error', 'No active workers');
        return;
    }

  try {
    const resp = await fetch(`http://localhost:${_workerPort}/api/add-job`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({url: location.href, title: document.title.replace(/\s*[-|].*$/, '').trim()}),
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
      if (data.status === 'queued') setPillState('success');
      else if (data.status === 'exists') setPillState('exists', data.applyStatus ? `Already ${data.applyStatus}` : undefined);
      else setPillState('error', 'Unexpected response');
  } catch (err) {
      _workerPort = null;
    setPillState('error', err.message.slice(0, 40));
  }
}

// --- Worker discovery + init ---------------------------------------------

async function findWorker() {
    for (let i = 0; i < APPLYPILOT.MAX_WORKERS; i++) {
        const port = APPLYPILOT.BASE_PORT + i;
    try {
        const resp = await fetch(`http://localhost:${port}/api/status`, {signal: AbortSignal.timeout(1000)});
      if (resp.ok) { _workerPort = port; return port; }
    } catch {
    }
  }
  return null;
}

async function init() {
    if (!(await findWorker())) return;
  _pill = createPill();
  setPillState('idle');
  document.body.appendChild(_pill);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
