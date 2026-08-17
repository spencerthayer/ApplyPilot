// ApplyPilot Settings page.
//
// Hash-routed sidebar nav; each section is a stub that subsequent steps
// (Integrations, Q&A, Preferences, Credentials, Sessions) will fill in.
//
// Backend access mirrors popup.js: probe http://localhost:(BASE_PORT+wid)
// for the worker that has the always-on HTTP server up. /api/* endpoints
// already include CORS headers, so fetch() works from chrome-extension://
// origins.

const BASE_PORT   = 7380;
const MAX_WORKERS = 5;

// ── Sidebar nav: hash-routed section toggle ───────────────────────────────────

const SECTIONS = [
  'integrations',
  'preferences',
  'qa',
  'credentials',
  'sessions',
  'about',
];

// Listeners notified when the active section changes; populated by each
// section's own module below. Each listener gets (newName, oldName).
const _sectionListeners = [];
let _currentSection = null;

function onSectionChange(fn) { _sectionListeners.push(fn); }

function showSection(name) {
  if (!SECTIONS.includes(name)) name = 'integrations';
  document.querySelectorAll('nav a').forEach(a => {
    a.classList.toggle('active', a.dataset.section === name);
  });
  document.querySelectorAll('main section').forEach(s => {
    s.classList.toggle('active', s.id === `sec-${name}`);
  });
  if (location.hash.slice(1) !== name) {
    history.replaceState(null, '', `#${name}`);
  }
  const old = _currentSection;
  _currentSection = name;
  for (const fn of _sectionListeners) {
    try { fn(name, old); } catch (e) { console.error(e); }
  }
}

document.querySelectorAll('nav a').forEach(a => {
  a.addEventListener('click', () => showSection(a.dataset.section));
});

window.addEventListener('hashchange', () => {
  showSection(location.hash.slice(1) || 'integrations');
});

// ── Backend helper ────────────────────────────────────────────────────────────

// Custom error so callers can tell a semantic 4xx from a "no worker" outage.
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * Probe each worker port for an endpoint until one responds.
 *   2xx        → return parsed JSON
 *   4xx        → throw ApiError with the body text immediately
 *                (validation errors are identical across workers since
 *                they all hit the same DB; no point probing the rest)
 *   5xx / net  → record and try the next worker; throw aggregate if all fail
 */
async function probeWorkers(path, opts = {}) {
  const transportErrors = [];
  for (let wid = 0; wid < MAX_WORKERS; wid++) {
    let r;
    try {
      r = await fetch(`http://localhost:${BASE_PORT + wid}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(2000),
        ...opts,
      });
    } catch (e) {
      transportErrors.push(`worker-${wid}: ${e.message}`);
      continue;
    }
    if (r.ok) return r.json();
    if (r.status >= 400 && r.status < 500) {
      const detail = await r.text().catch(() => '');
      throw new ApiError(detail || `HTTP ${r.status}`, r.status);
    }
    transportErrors.push(`worker-${wid}: HTTP ${r.status}`);
  }
  throw new Error(
    `No worker responded. Run \`applypilot apply\`. ` +
    `Probed: ${transportErrors.join('; ')}`
  );
}

// Exposed so future section modules can reuse the same probe logic.
window.AP = { probeWorkers, BASE_PORT, MAX_WORKERS };

// ── Integrations section ──────────────────────────────────────────────────────

const INTEGRATIONS_POLL_MS = 5000;
let _integrationsTimer = null;

function pillForGmail(status) {
  switch (status) {
    case 'valid':         return { cls: 'pill-ok',   text: '✓ Valid' };
    case 'expiring_soon': return { cls: 'pill-warn', text: '⚠ Expiring' };
    case 'expired':       return { cls: 'pill-err',  text: '✗ Expired' };
    case 'missing':       return { cls: 'pill-mute', text: '— Not configured' };
    default:              return { cls: 'pill-err',  text: '✗ Error' };
  }
}

function fmtRelative(seconds) {
  const abs = Math.abs(seconds);
  let unit;
  if (abs >= 86400)     unit = `${Math.floor(abs / 86400)}d`;
  else if (abs >= 3600) unit = `${Math.floor(abs / 3600)}h`;
  else if (abs >= 60)   unit = `${Math.floor(abs / 60)}m`;
  else                  unit = `${abs}s`;
  return seconds < 0 ? `${unit} ago` : `in ${unit}`;
}

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderIntegrations(data) {
  const out = [];

  // ── Gmail ──
  const g = data.gmail || {};
  const gp = pillForGmail(g.status);
  out.push(`<div class="card">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
      <strong style="font-size:15px">Gmail</strong>
      <span class="pill ${gp.cls}">${gp.text}</span>
    </div>`);
  if (g.configured) {
    out.push(`<div style="font-size:12px;color:#94a3b8;line-height:1.6">`);
    out.push(`Access token ${fmtRelative(g.expires_in_seconds || 0)}<br>`);
    out.push(`Scopes: <code style="color:#cbd5e1">${esc(g.scopes || 'unknown')}</code>`);
    out.push(`</div>`);
  } else {
    out.push(`<div style="font-size:12px;color:#94a3b8;line-height:1.6">
      No credentials at <code>~/.gmail-mcp/credentials.json</code>.
      Re-authentication will create them.
    </div>`);
  }
  out.push(`<div style="margin-top:12px;display:flex;gap:8px">
    <button class="btn btn-warn" data-int-action="gmail-reauth">Re-authenticate Gmail</button>
    <button class="btn btn-secondary" data-int-action="refresh">Refresh status</button>
  </div>
  <div id="gmail-feedback" style="margin-top:8px;font-size:12px;color:#94a3b8"></div>
  </div>`);

  // ── ATS sessions ──
  out.push(`<h2>ATS Sessions</h2>`);
  const sessions = data.ats_sessions || [];
  if (!sessions.length) {
    out.push(`<div class="placeholder">
      No saved sessions. The pipeline saves ATS cookies automatically after each
      successful apply.
    </div>`);
  } else {
    out.push(`<div class="card" style="padding:0">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        <thead><tr style="border-bottom:1px solid #2d2d4e">
          <th style="text-align:left;padding:10px 16px;color:#94a3b8;font-weight:500">ATS</th>
          <th style="text-align:left;padding:10px 16px;color:#94a3b8;font-weight:500">Cookies</th>
          <th style="text-align:left;padding:10px 16px;color:#94a3b8;font-weight:500">Age</th>
        </tr></thead>
        <tbody>`);
    for (const s of sessions) {
      const ageStr = s.age_hours == null ? '—'
        : s.age_hours < 1 ? `${Math.round(s.age_hours * 60)}m`
        : s.age_hours < 24 ? `${s.age_hours.toFixed(1)}h`
        : `${Math.floor(s.age_hours / 24)}d`;
      out.push(`<tr style="border-bottom:1px solid #1e293b">
        <td style="padding:10px 16px"><strong>${esc(s.slug)}</strong></td>
        <td style="padding:10px 16px">${s.has_cookies ? '✓' : '—'}</td>
        <td style="padding:10px 16px;color:#94a3b8">${ageStr}</td>
      </tr>`);
    }
    out.push(`</tbody></table></div>`);
  }

  return out.join('');
}

async function loadIntegrations() {
  const target = document.getElementById('integrations-content');
  if (!target) return;
  try {
    const data = await window.AP.probeWorkers('/api/integrations');
    target.innerHTML = renderIntegrations(data);
    bindIntegrationActions();
  } catch (err) {
    target.innerHTML = `<div class="placeholder">
      Could not reach the apply backend.<br>
      Run <code>applypilot apply</code> in a terminal.<br>
      <span style="color:#475569;font-size:11px;display:block;margin-top:8px">${esc(err.message)}</span>
    </div>`;
  }
}

function bindIntegrationActions() {
  document.querySelectorAll('[data-int-action]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.intAction;
      const fb = document.getElementById('gmail-feedback');
      btn.disabled = true;
      try {
        if (action === 'gmail-reauth') {
          if (fb) fb.textContent = 'Starting Gmail OAuth flow — your browser will open the consent screen…';
          const r = await window.AP.probeWorkers('/api/integrations/gmail/reauth', {
            method: 'POST', body: '{}',
          });
          if (r.status === 'started') {
            if (fb) fb.textContent = 'OAuth flow running in a browser tab. After you approve, click "Refresh status".';
          } else {
            if (fb) fb.textContent = `Failed: ${r.error || 'unknown error'}`;
          }
        } else if (action === 'refresh') {
          await loadIntegrations();
        }
      } catch (e) {
        if (fb) fb.textContent = `Error: ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    });
  });
}

// Auto-poll while the Integrations section is the active one. Stop polling
// when the user navigates away so we don't keep hitting the backend forever.
function startIntegrationsPoll() {
  if (_integrationsTimer) return;
  loadIntegrations();
  _integrationsTimer = setInterval(loadIntegrations, INTEGRATIONS_POLL_MS);
}
function stopIntegrationsPoll() {
  if (_integrationsTimer) {
    clearInterval(_integrationsTimer);
    _integrationsTimer = null;
  }
}

onSectionChange(name => {
  if (name === 'integrations') startIntegrationsPoll();
  else                          stopIntegrationsPoll();
});

// ── Preferences section ───────────────────────────────────────────────────────
//
// Edit the three text-based config files: profile.json, searches.yaml, and
// company_limits.yaml. Backend parse-validates before writing so a stray
// comma can't brick the next pipeline run. A single-slot .bak file is
// kept on every save.

const PREFS_FILES = [
  { key: 'profile',        label: 'Profile',           hint: 'Personal info, EEO answers, file paths. profile.json — agent reads this for every form fill.' },
  { key: 'searches',       label: 'Searches',          hint: 'Discovery queries: keywords, locations, salary floor, sites. searches.yaml — applied by `applypilot run discover`.' },
  { key: 'company-limits', label: 'Company Limits',    hint: 'Per-company in-flight caps to spread applications. company_limits.yaml — read on every acquire_job(), no restart needed.' },
];

let _activePrefKey = 'profile';

function prefsShellHTML() {
  const tabs = PREFS_FILES.map(f =>
    `<a class="pref-tab${f.key === _activePrefKey ? ' active' : ''}" data-pref="${f.key}"
        style="padding:8px 16px;border-bottom:2px solid transparent;cursor:pointer;color:#94a3b8;font-size:13px;font-weight:500">
       ${f.label}
     </a>`
  ).join('');
  return `
  <div style="display:flex;gap:0;border-bottom:1px solid #2d2d4e;margin-bottom:16px">
    ${tabs}
  </div>
  <div id="pref-hint" style="font-size:12px;color:#94a3b8;margin-bottom:8px"></div>
  <div id="pref-path" style="font-size:11px;color:#475569;margin-bottom:8px;font-family:monospace"></div>
  <textarea id="pref-text"
    style="width:100%;height:480px;padding:12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-family:'SF Mono',Menlo,monospace;font-size:12px;line-height:1.5;resize:vertical"></textarea>
  <div style="margin-top:12px;display:flex;gap:8px;align-items:center">
    <button class="btn" id="pref-save">Save</button>
    <button class="btn btn-secondary" id="pref-reload">Discard changes</button>
    <span id="pref-fb" style="margin-left:12px;font-size:12px;color:#94a3b8"></span>
  </div>`;
}

function styleActivePrefTab() {
  document.querySelectorAll('.pref-tab').forEach(tab => {
    const isActive = tab.dataset.pref === _activePrefKey;
    tab.classList.toggle('active', isActive);
    tab.style.borderBottomColor = isActive ? '#818cf8' : 'transparent';
    tab.style.color              = isActive ? '#818cf8' : '#94a3b8';
  });
  const meta = PREFS_FILES.find(f => f.key === _activePrefKey);
  document.getElementById('pref-hint').textContent = meta?.hint || '';
}

async function loadPref() {
  const fb = document.getElementById('pref-fb');
  fb.textContent = 'Loading…';
  try {
    const data = await window.AP.probeWorkers(`/api/prefs/${_activePrefKey}`);
    document.getElementById('pref-text').value = data.text || '';
    document.getElementById('pref-path').textContent =
      `${data.path}${data.exists ? '' : ' (not yet created)'}  ·  ${data.format}`;
    fb.textContent = '';
  } catch (e) {
    fb.textContent = `Error: ${e.message}`;
  }
}

async function savePref() {
  const fb = document.getElementById('pref-fb');
  fb.textContent = 'Saving…';
  fb.style.color = '#94a3b8';
  const text = document.getElementById('pref-text').value;
  try {
    const r = await window.AP.probeWorkers(`/api/prefs/${_activePrefKey}`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    fb.textContent = `✓ Saved · ${r.bytes} bytes (.bak written)`;
    fb.style.color = '#6ee7b7';
    setTimeout(() => { fb.textContent = ''; fb.style.color = '#94a3b8'; }, 2500);
  } catch (e) {
    // Probe error response goes to .message of the rejection
    fb.textContent = `Save failed: ${e.message}`;
    fb.style.color = '#fca5a5';
  }
}

function bindPrefsShell() {
  document.querySelectorAll('.pref-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      _activePrefKey = tab.dataset.pref;
      styleActivePrefTab();
      loadPref();
    });
  });
  document.getElementById('pref-save').addEventListener('click', savePref);
  document.getElementById('pref-reload').addEventListener('click', loadPref);
}

let _prefsInitialized = false;
onSectionChange(name => {
  if (name !== 'preferences') return;
  if (!_prefsInitialized) {
    document.getElementById('preferences-content').innerHTML = prefsShellHTML();
    bindPrefsShell();
    _prefsInitialized = true;
  }
  styleActivePrefTab();
  loadPref();
});

// ── Q&A Knowledge section ─────────────────────────────────────────────────────
//
// Browse, search, edit, and delete qa_knowledge rows. The agent's
// lookup_qa() call hits this same table during apply, so edits here
// directly affect future form-fill answers.

let _qaState = { q: '', ats: '', source: '', outcome: '', rows: [], total: 0 };

function qaShellHTML() {
  return `
  <div class="card" style="margin-bottom:16px">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input type="text" id="qa-search" placeholder="Search question or answer…"
        style="flex:1;min-width:240px;padding:8px 12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px"/>
      <select id="qa-source" style="padding:8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px">
        <option value="">Any source</option>
        <option value="human">Human</option>
        <option value="agent">Agent</option>
      </select>
      <select id="qa-outcome" style="padding:8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px">
        <option value="">Any outcome</option>
        <option value="accepted">Accepted</option>
        <option value="unknown">Unknown</option>
        <option value="rejected">Rejected</option>
      </select>
      <button class="btn btn-secondary" id="qa-add-toggle">+ Add</button>
    </div>
    <div id="qa-add-form" style="display:none;margin-top:12px;border-top:1px solid #2d2d4e;padding-top:12px">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <input type="text" id="qa-new-question" placeholder="Question text…"
          style="grid-column:span 2;padding:8px 12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px"/>
        <input type="text" id="qa-new-answer" placeholder="Answer…"
          style="grid-column:span 2;padding:8px 12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px"/>
        <input type="text" id="qa-new-field" placeholder="Field type (radio, select, text, …)"
          style="padding:8px 12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px"/>
        <input type="text" id="qa-new-ats" placeholder="ATS slug (greenhouse, workday, …) — optional"
          style="padding:8px 12px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:6px;font-size:13px"/>
      </div>
      <div style="margin-top:8px">
        <button class="btn" id="qa-add-submit">Save</button>
        <span id="qa-add-fb" style="margin-left:12px;font-size:12px;color:#94a3b8"></span>
      </div>
    </div>
  </div>
  <div id="qa-list">Loading…</div>
  <div style="margin-top:12px;color:#475569;font-size:11px" id="qa-summary"></div>`;
}

function qaRowHTML(r) {
  const src = (r.answer_source || 'unknown').toLowerCase();
  const srcCls = src === 'human' ? 'pill-ok' : src === 'agent' ? 'pill-mute' : 'pill-warn';
  const outcome = r.outcome || 'unknown';
  return `
  <tr data-id="${r.id}" style="border-bottom:1px solid #1e293b;vertical-align:top">
    <td style="padding:10px 14px;width:38%">
      <div data-cell="question_text" class="qa-cell">${esc(r.question_text || '')}</div>
    </td>
    <td style="padding:10px 14px;width:38%">
      <div data-cell="answer_text" class="qa-cell" style="color:#cbd5e1">${esc(r.answer_text || '')}</div>
    </td>
    <td style="padding:10px 14px;font-size:11px;color:#94a3b8">
      <span class="pill ${srcCls}">${src}</span><br>
      ${esc(r.field_type || '—')}<br>
      ${esc(r.ats_slug || '—')}<br>
      <span style="color:#64748b">${esc(outcome)}</span>
    </td>
    <td style="padding:10px 14px;text-align:right;white-space:nowrap">
      <button class="btn btn-secondary" data-qa-action="edit"   data-id="${r.id}" style="padding:4px 10px;font-size:11px">Edit</button>
      <button class="btn btn-warn"      data-qa-action="delete" data-id="${r.id}" style="padding:4px 10px;font-size:11px;margin-left:4px">Delete</button>
    </td>
  </tr>`;
}

function renderQaTable() {
  const list = document.getElementById('qa-list');
  const summary = document.getElementById('qa-summary');
  if (!_qaState.rows.length) {
    list.innerHTML = `<div class="placeholder">No matching Q&amp;A rows.</div>`;
    summary.textContent = `0 of ${_qaState.total}`;
    return;
  }
  const head = `<thead><tr style="border-bottom:1px solid #2d2d4e">
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Question</th>
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Answer</th>
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Meta</th>
    <th></th>
  </tr></thead>`;
  list.innerHTML = `<div class="card" style="padding:0;overflow:hidden">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      ${head}
      <tbody>${_qaState.rows.map(qaRowHTML).join('')}</tbody>
    </table>
  </div>`;
  summary.textContent = `Showing ${_qaState.rows.length} of ${_qaState.total} row(s)`;
  bindQaRowActions();
}

async function loadQa() {
  const params = new URLSearchParams();
  if (_qaState.q)       params.set('q',       _qaState.q);
  if (_qaState.source)  params.set('source',  _qaState.source);
  if (_qaState.outcome) params.set('outcome', _qaState.outcome);
  params.set('limit', '200');
  try {
    const data = await window.AP.probeWorkers(`/api/qa?${params.toString()}`);
    _qaState.rows = data.rows || [];
    _qaState.total = data.total || 0;
    renderQaTable();
  } catch (err) {
    document.getElementById('qa-list').innerHTML = `<div class="placeholder">
      Could not reach backend.<br>
      Run <code>applypilot apply</code>.<br>
      <span style="font-size:11px;color:#475569">${esc(err.message)}</span>
    </div>`;
  }
}

function bindQaShell() {
  const search = document.getElementById('qa-search');
  let searchTimer = null;
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      _qaState.q = search.value.trim();
      loadQa();
    }, 300);
  });
  document.getElementById('qa-source').addEventListener('change', e => {
    _qaState.source = e.target.value;
    loadQa();
  });
  document.getElementById('qa-outcome').addEventListener('change', e => {
    _qaState.outcome = e.target.value;
    loadQa();
  });
  document.getElementById('qa-add-toggle').addEventListener('click', () => {
    const form = document.getElementById('qa-add-form');
    form.style.display = form.style.display === 'none' ? '' : 'none';
  });
  document.getElementById('qa-add-submit').addEventListener('click', async () => {
    const fb = document.getElementById('qa-add-fb');
    const question = document.getElementById('qa-new-question').value.trim();
    const answer   = document.getElementById('qa-new-answer').value.trim();
    if (!question || !answer) {
      fb.textContent = 'Question + answer required.';
      return;
    }
    fb.textContent = 'Saving…';
    try {
      await window.AP.probeWorkers('/api/qa', {
        method: 'POST',
        body: JSON.stringify({
          question, answer,
          source: 'human',
          field_type: document.getElementById('qa-new-field').value.trim() || undefined,
          ats_slug:   document.getElementById('qa-new-ats').value.trim()   || undefined,
        }),
      });
      fb.textContent = '✓ Saved';
      document.getElementById('qa-new-question').value = '';
      document.getElementById('qa-new-answer').value   = '';
      document.getElementById('qa-new-field').value    = '';
      document.getElementById('qa-new-ats').value      = '';
      setTimeout(() => { fb.textContent = ''; }, 1500);
      loadQa();
    } catch (e) {
      fb.textContent = `Error: ${e.message}`;
    }
  });
}

function bindQaRowActions() {
  document.querySelectorAll('[data-qa-action]').forEach(btn => {
    btn.addEventListener('click', () => qaRowAction(btn));
  });
}

async function qaRowAction(btn) {
  const action = btn.dataset.qaAction;
  const id     = parseInt(btn.dataset.id, 10);
  const tr     = btn.closest('tr');
  if (action === 'delete') {
    if (!confirm('Delete this Q&A entry permanently?')) return;
    try {
      await window.AP.probeWorkers(`/api/qa/${id}`, {
        method: 'POST', body: JSON.stringify({ action: 'delete' }),
      });
      tr.remove();
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
    return;
  }
  if (action === 'edit') {
    // Swap each editable cell for an input. Save / Cancel buttons replace
    // the action buttons.
    tr.querySelectorAll('.qa-cell').forEach(cell => {
      const col = cell.parentElement.dataset.col || cell.dataset.cell;
      const val = cell.textContent;
      cell.outerHTML = `<input type="text" data-edit="${col}" value="${esc(val)}"
        style="width:100%;padding:6px 8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:4px;font-size:13px"/>`;
    });
    const actionsCell = btn.parentElement;
    actionsCell.innerHTML = `
      <button class="btn"           data-qa-action="save"   data-id="${id}" style="padding:4px 10px;font-size:11px">Save</button>
      <button class="btn btn-secondary" data-qa-action="cancel" data-id="${id}" style="padding:4px 10px;font-size:11px;margin-left:4px">Cancel</button>`;
    actionsCell.querySelectorAll('[data-qa-action]').forEach(b => {
      b.addEventListener('click', () => qaRowAction(b));
    });
    return;
  }
  if (action === 'cancel') {
    loadQa();
    return;
  }
  if (action === 'save') {
    const updates = {};
    tr.querySelectorAll('input[data-edit]').forEach(inp => {
      updates[inp.dataset.edit] = inp.value;
    });
    try {
      await window.AP.probeWorkers(`/api/qa/${id}`, {
        method: 'POST',
        body: JSON.stringify({ action: 'update', ...updates }),
      });
      loadQa();
    } catch (e) {
      alert(`Save failed: ${e.message}`);
    }
  }
}

let _qaInitialized = false;
onSectionChange(name => {
  if (name !== 'qa') return;
  if (!_qaInitialized) {
    document.getElementById('qa-content').innerHTML = qaShellHTML();
    bindQaShell();
    _qaInitialized = true;
  }
  loadQa();
});

// ── Credentials section ───────────────────────────────────────────────────────
//
// Edit the accounts table — emails + passwords the agent created on
// employer ATS sites during apply. Passwords show as ••••••• by default
// with a per-row "show" toggle.

let _credentialsRows = [];

function credShellHTML() {
  return `
  <div id="cred-warning" style="font-size:12px;color:#fcd34d;background:#422006;padding:8px 12px;border-radius:6px;margin-bottom:12px">
    ⚠ Passwords are stored in plaintext (the agent needs them to log back into ATS sites).
    Anyone with shell access to your machine can read them. Keep the database file safe.
  </div>
  <div id="cred-list">Loading…</div>`;
}

function credRowHTML(r) {
  return `
  <tr data-id="${r.id}" style="border-bottom:1px solid #1e293b;vertical-align:top">
    <td style="padding:10px 14px"><strong>${esc(r.site || '—')}</strong><br>
      <span style="font-size:11px;color:#94a3b8">${esc(r.domain || '')}</span></td>
    <td style="padding:10px 14px">
      <input type="text" data-cred="email" value="${esc(r.email || '')}"
        style="width:100%;padding:6px 8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:4px;font-size:13px"/>
    </td>
    <td style="padding:10px 14px">
      <div style="display:flex;gap:4px;align-items:center">
        <input type="password" data-cred="password" value="${esc(r.password || '')}"
          style="flex:1;padding:6px 8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:4px;font-size:13px;font-family:monospace"/>
        <button class="btn btn-secondary" data-cred-action="reveal" data-id="${r.id}"
          style="padding:4px 8px;font-size:11px" title="Show password">👁</button>
      </div>
    </td>
    <td style="padding:10px 14px;font-size:11px;color:#94a3b8;max-width:200px">
      <textarea data-cred="notes" placeholder="(no notes)"
        style="width:100%;height:48px;padding:6px 8px;background:#0f172a;border:1px solid #2d2d4e;color:#e2e8f0;border-radius:4px;font-size:11px;resize:vertical">${esc(r.notes || '')}</textarea>
      <div style="margin-top:4px;color:#64748b">created ${esc((r.created_at || '').slice(0, 10))}</div>
    </td>
    <td style="padding:10px 14px;text-align:right;white-space:nowrap">
      <button class="btn"           data-cred-action="save"   data-id="${r.id}" style="padding:4px 10px;font-size:11px">Save</button>
      <button class="btn btn-warn"  data-cred-action="delete" data-id="${r.id}" style="padding:4px 10px;font-size:11px;margin-left:4px">Delete</button>
    </td>
  </tr>`;
}

function renderCredentials() {
  const list = document.getElementById('cred-list');
  if (!_credentialsRows.length) {
    list.innerHTML = `<div class="placeholder">
      No accounts yet. The apply agent stores credentials here when it
      creates an account on an ATS site (Workday, iCIMS, etc.) during
      a HITL signup flow.
    </div>`;
    return;
  }
  const head = `<thead><tr style="border-bottom:1px solid #2d2d4e">
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Site</th>
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Email</th>
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Password</th>
    <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Notes</th>
    <th></th>
  </tr></thead>`;
  list.innerHTML = `<div class="card" style="padding:0;overflow:hidden">
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      ${head}
      <tbody>${_credentialsRows.map(credRowHTML).join('')}</tbody>
    </table>
  </div>`;
  bindCredRowActions();
}

async function loadCredentials() {
  try {
    const data = await window.AP.probeWorkers('/api/accounts');
    _credentialsRows = data.rows || [];
    renderCredentials();
  } catch (e) {
    document.getElementById('cred-list').innerHTML =
      `<div class="placeholder">Could not load accounts. <span style="font-size:11px;color:#475569">${esc(e.message)}</span></div>`;
  }
}

function bindCredRowActions() {
  document.querySelectorAll('[data-cred-action]').forEach(btn => {
    btn.addEventListener('click', () => credRowAction(btn));
  });
}

async function credRowAction(btn) {
  const action = btn.dataset.credAction;
  const id     = parseInt(btn.dataset.id, 10);
  const tr     = btn.closest('tr');

  if (action === 'reveal') {
    const pw = tr.querySelector('[data-cred="password"]');
    pw.type = pw.type === 'password' ? 'text' : 'password';
    btn.textContent = pw.type === 'password' ? '👁' : '🙈';
    return;
  }
  if (action === 'delete') {
    if (!confirm('Delete this account row? The agent loses login access to this site.')) return;
    try {
      await window.AP.probeWorkers(`/api/accounts/${id}`, {
        method: 'POST', body: JSON.stringify({ action: 'delete' }),
      });
      tr.remove();
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
    return;
  }
  if (action === 'save') {
    const updates = {};
    tr.querySelectorAll('[data-cred]').forEach(inp => {
      updates[inp.dataset.cred] = inp.value;
    });
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      await window.AP.probeWorkers(`/api/accounts/${id}`, {
        method: 'POST',
        body: JSON.stringify({ action: 'update', ...updates }),
      });
      btn.textContent = '✓';
      setTimeout(() => { btn.textContent = 'Save'; btn.disabled = false; }, 1200);
    } catch (e) {
      btn.textContent = 'Save';
      btn.disabled = false;
      alert(`Save failed: ${e.message}`);
    }
  }
}

let _credInitialized = false;
onSectionChange(name => {
  if (name !== 'credentials') return;
  if (!_credInitialized) {
    document.getElementById('credentials-content').innerHTML = credShellHTML();
    _credInitialized = true;
  }
  loadCredentials();
});

// ── ATS Sessions section ──────────────────────────────────────────────────────
//
// Saved Chrome cookies per ATS platform (greenhouse, workday, …). Lets
// the user clear a stuck session without rooting around in
// ~/.applypilot/chrome-sessions/.

function sessShellHTML() {
  return `
  <div style="font-size:12px;color:#94a3b8;margin-bottom:12px">
    ApplyPilot saves your post-login cookies after each successful apply
    so subsequent runs don't have to re-authenticate. Clear a session
    here if it's gone stale and the agent can't recover.
  </div>
  <div id="sess-list">Loading…</div>`;
}

function sessRowHTML(s) {
  const ageStr = s.age_hours == null ? '—'
    : s.age_hours < 1 ? `${Math.round(s.age_hours * 60)}m ago`
    : s.age_hours < 24 ? `${s.age_hours.toFixed(1)}h ago`
    : `${Math.floor(s.age_hours / 24)}d ago`;
  const statusPill = s.has_cookies
    ? `<span class="pill pill-ok">✓ saved</span>`
    : `<span class="pill pill-mute">— empty</span>`;
  return `
  <tr style="border-bottom:1px solid #1e293b">
    <td style="padding:10px 14px"><strong>${esc(s.slug)}</strong></td>
    <td style="padding:10px 14px">${statusPill}</td>
    <td style="padding:10px 14px;color:#94a3b8;font-size:12px">${esc(ageStr)}</td>
    <td style="padding:10px 14px;text-align:right">
      <button class="btn btn-warn" data-sess-action="clear" data-slug="${esc(s.slug)}"
        ${s.has_cookies ? '' : 'disabled'}
        style="padding:4px 10px;font-size:11px">Clear</button>
    </td>
  </tr>`;
}

async function loadSessions() {
  const list = document.getElementById('sess-list');
  try {
    const data = await window.AP.probeWorkers('/api/integrations');
    const sessions = data.ats_sessions || [];
    if (!sessions.length) {
      list.innerHTML = `<div class="placeholder">No saved sessions yet.</div>`;
      return;
    }
    const head = `<thead><tr style="border-bottom:1px solid #2d2d4e">
      <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">ATS</th>
      <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Cookies</th>
      <th style="text-align:left;padding:10px 14px;color:#94a3b8;font-weight:500;font-size:12px">Saved</th>
      <th></th>
    </tr></thead>`;
    list.innerHTML = `<div class="card" style="padding:0;overflow:hidden">
      <table style="width:100%;border-collapse:collapse;font-size:13px">
        ${head}<tbody>${sessions.map(sessRowHTML).join('')}</tbody>
      </table></div>`;
    list.querySelectorAll('[data-sess-action]').forEach(btn => {
      btn.addEventListener('click', () => sessRowAction(btn));
    });
  } catch (e) {
    list.innerHTML = `<div class="placeholder">Could not load sessions. <span style="font-size:11px;color:#475569">${esc(e.message)}</span></div>`;
  }
}

async function sessRowAction(btn) {
  const slug = btn.dataset.slug;
  if (!confirm(`Clear the saved ${slug} session? The next apply will need to log in again.`)) return;
  btn.disabled = true;
  try {
    await window.AP.probeWorkers(`/api/sessions/${slug}`, {
      method: 'POST', body: JSON.stringify({ action: 'clear' }),
    });
    await loadSessions();
  } catch (e) {
    alert(`Clear failed: ${e.message}`);
    btn.disabled = false;
  }
}

let _sessInitialized = false;
onSectionChange(name => {
  if (name !== 'sessions') return;
  if (!_sessInitialized) {
    document.getElementById('sessions-content').innerHTML = sessShellHTML();
    _sessInitialized = true;
  }
  loadSessions();
});

// ── About: populate version + extension ID ────────────────────────────────────

(async () => {
  try {
    const m = await fetch(chrome.runtime.getURL('manifest.json'))
      .then(r => r.json());
    document.getElementById('ver').textContent = `v${m.version}`;
    document.getElementById('about-version').textContent = m.version;
  } catch { /* ignore */ }
  document.getElementById('about-id').textContent = chrome.runtime.id;
})();

// ── Initial route ─────────────────────────────────────────────────────────────

showSection(location.hash.slice(1) || 'integrations');
