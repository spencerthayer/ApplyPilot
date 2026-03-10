// ApplyPilot — in-page capture button
// Injected on all http/https pages. Discovers a live apply worker (ports 7380–7384)
// and shows a small floating pill when one is reachable.

const BASE_WORKER_PORT = 7380;
const MAX_WORKERS = 5;
let _workerPort = null; // port of the live worker we found

// --- Job page detection --------------------------------------------------
// Show the button only on pages that look like a job posting.
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
  // Fallback: check page title for job-like keywords
  const title = document.title.toLowerCase();
  return /engineer|developer|devops|platform|backend|frontend|staff|principal|senior/.test(title)
    && /job|position|opening|career|apply/.test(title);
}

if (!looksLikeJobPage()) {
  // Not a job page — don't inject anything
  throw new Error('not a job page');  // stops this content script cleanly
}

// --- UI -----------------------------------------------------------------

let _pill = null;
let _state = 'idle'; // idle | loading | success | exists | error

function createPill() {
  const pill = document.createElement('div');
  pill.id = 'applypilot-capture-pill';
  pill.style.cssText = `
    position: fixed;
    bottom: 18px;
    right: 18px;
    z-index: 2147483647;
    background: #312e81;
    color: #e0e7ff;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 12px;
    font-weight: 500;
    padding: 7px 14px 7px 10px;
    border-radius: 20px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: background 0.15s, opacity 0.15s;
    user-select: none;
    border: 1px solid #4f46e5;
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

function setPillState(state, msg) {
  _state = state;
  if (!_pill) return;
  const configs = {
    idle:    { bg: '#312e81', border: '#4f46e5', text: '📥 Add to ApplyPilot' },
    loading: { bg: '#1e1b4b', border: '#3730a3', text: '⏳ Adding…' },
    success: { bg: '#064e3b', border: '#059669', text: '✓ Added to pipeline' },
    exists:  { bg: '#1e3a5f', border: '#2563eb', text: msg || '↩ Already in pipeline' },
    error:   { bg: '#450a0a', border: '#b91c1c', text: '✗ ' + (msg || 'Error') },
  };
  const cfg = configs[state] || configs.idle;
  _pill.style.background = cfg.bg;
  _pill.style.borderColor = cfg.border;
  _pill.textContent = cfg.text;

  // Auto-reset to idle after 4s for non-idle states
  if (state !== 'idle' && state !== 'loading') {
    setTimeout(() => setPillState('idle'), 4000);
  }
}

// --- Capture logic -------------------------------------------------------

function getDescription() {
  // Grab up to 3000 chars of visible job description text (best-effort)
  const selectors = [
    '[class*="description"]', '[class*="job-desc"]', '[class*="posting"]',
    'article', 'main', '.content', '#content',
  ];
  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el && el.innerText && el.innerText.length > 100) {
      return el.innerText.slice(0, 3000).trim();
    }
  }
  return '';
}

async function captureJob() {
  if (_state === 'loading') return;
  setPillState('loading');

  const url   = location.href;
  const title = document.title.replace(/\s*[-|].*$/, '').trim(); // strip "| Company" suffix

  // Re-discover worker if needed
  if (!_workerPort) await findWorker();
  if (!_workerPort) {
    setPillState('error', 'No active workers');
    return;
  }

  try {
    const resp = await fetch(`http://localhost:${_workerPort}/api/add-job`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title }),
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    if (data.status === 'queued') {
      setPillState('success');
    } else if (data.status === 'exists') {
      const label = data.applyStatus ? `Already ${data.applyStatus}` : 'Already in pipeline';
      setPillState('exists', label);
    } else {
      setPillState('error', 'Unexpected response');
    }
  } catch (err) {
    _workerPort = null; // reset so next click re-discovers
    setPillState('error', err.message.slice(0, 40));
  }
}

// --- Discover a live worker and inject pill -------------------------------

async function findWorker() {
  for (let i = 0; i < MAX_WORKERS; i++) {
    const port = BASE_WORKER_PORT + i;
    try {
      const resp = await fetch(`http://localhost:${port}/api/status`, {
        signal: AbortSignal.timeout(1000),
      });
      if (resp.ok) { _workerPort = port; return port; }
    } catch { /* skip */ }
  }
  return null;
}

async function init() {
  const port = await findWorker();
  if (!port) return; // no workers running — stay silent

  _pill = createPill();
  setPillState('idle');
  document.body.appendChild(_pill);
}

// Run after DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
