"""Ui — extracted from human_review."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _build_ui_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>ApplyPilot — Human Review</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; padding: 2rem; }
  h1 { font-size: 1.5rem; margin-bottom: 0.4rem; }
  .subtitle { color: #94a3b8; margin-bottom: 2rem; font-size: 0.9rem; }
  .empty { color: #64748b; font-size: 1rem; margin-top: 2rem; }
  .job-list { display: flex; flex-direction: column; gap: 1rem; }
  .job-card {
    background: #1e293b; border-radius: 10px; padding: 1.25rem;
    border-left: 4px solid #7c3aed;
  }
  .job-title { font-size: 1rem; font-weight: 700; margin-bottom: 0.4rem; }
  .job-meta { font-size: 0.8rem; color: #94a3b8; margin-bottom: 0.6rem; }
  .reason-badge {
    display: inline-block; font-size: 0.7rem; font-weight: 700;
    padding: 0.15rem 0.5rem; border-radius: 4px;
    background: #7c3aed33; color: #c4b5fd;
    text-transform: uppercase; margin-right: 0.5rem;
  }
  .instructions { font-size: 0.82rem; color: #e2e8f0; margin: 0.5rem 0; line-height: 1.5; }
  .actions { display: flex; gap: 0.75rem; margin-top: 0.75rem; }
  .btn-start {
    background: #7c3aed; color: #fff; border: none; border-radius: 6px;
    padding: 0.5rem 1rem; font-size: 0.85rem; font-weight: 600; cursor: pointer;
  }
  .btn-start:hover { background: #6d28d9; }
  .btn-start:disabled { background: #334155; color: #64748b; cursor: not-allowed; }
  .btn-skip {
    background: transparent; color: #ef4444; border: 1px solid #ef444433;
    border-radius: 6px; padding: 0.5rem 1rem; font-size: 0.85rem; cursor: pointer;
  }
  .btn-skip:hover { background: #ef444411; }
  .status-msg { font-size: 0.8rem; color: #94a3b8; margin-top: 0.5rem; }
  .result-ok { color: #22c55e; font-weight: 600; }
  .result-fail { color: #ef4444; font-weight: 600; }
  .log-box {
    background: #0f172a; border-radius: 6px; padding: 0.75rem;
    font-size: 0.72rem; font-family: monospace; color: #94a3b8;
    max-height: 200px; overflow-y: auto; margin-top: 0.5rem;
    white-space: pre-wrap; word-break: break-all;
    display: none;
  }
  .log-box.active { display: block; }
</style>
</head>
<body>
<h1>&#9872; Human Review Queue</h1>
<p class="subtitle">Jobs parked for human assistance. Click Start to open Chrome at the stuck page.</p>
<div id="content"><p class="empty">Loading...</p></div>

<script>
async function load() {
  const r = await fetch('/api/jobs');
  const jobs = await r.json();
  const el = document.getElementById('content');
  if (!jobs.length) {
    el.innerHTML = '<p class="empty">No jobs in the human review queue. Run <code>applypilot apply</code> to generate more.</p>';
    return;
  }
  el.innerHTML = '<div class="job-list">' + jobs.map(j => renderJob(j)).join('') + '</div>';
}

function renderJob(j) {
  const h = j.hash;
  return `<div class="job-card" id="card-${h}">
    <div class="job-title">${esc(j.title || 'Unknown Position')}</div>
    <div class="job-meta">
      <span class="reason-badge">${esc(j.needs_human_reason || '?')}</span>
      ${esc(j.site || '')} &nbsp;·&nbsp; Score: ${j.fit_score || '?'}/10
    </div>
    <div class="instructions">${esc(j.needs_human_instructions || '')}</div>
    <div class="actions">
      <button class="btn-start" id="btn-${h}" onclick="startJob('${h}')">Start &#8594; Open Chrome</button>
      <button class="btn-skip" onclick="skipJob('${h}')">Skip</button>
    </div>
    <div class="status-msg" id="status-${h}"></div>
    <div class="log-box" id="log-${h}"></div>
  </div>`;
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function startJob(h) {
  const btn = document.getElementById('btn-' + h);
  const status = document.getElementById('status-' + h);
  btn.disabled = true;
  btn.textContent = 'Opening Chrome...';
  status.textContent = 'Launching browser and injecting banner...';

  const r = await fetch('/api/start/' + h, { method: 'POST' });
  if (!r.ok) {
    const e = await r.json();
    if (r.status === 409) {
      status.textContent = 'Another session is active. Finish it first.';
    } else {
      status.textContent = 'Error: ' + (e.error || 'unknown');
    }
    btn.disabled = false;
    btn.textContent = 'Start → Open Chrome';
    return;
  }

  status.textContent = 'Chrome open with overlay. Complete the action and click Done in the banner.';

  // Start SSE stream to watch agent output
  startSSE(h);
}

function startSSE(h) {
  const log = document.getElementById('log-' + h);
  const status = document.getElementById('status-' + h);
  const es = new EventSource('/api/result-stream/' + h);

  es.onmessage = function(e) {
    const d = JSON.parse(e.data);
    if (d.text) {
      log.classList.add('active');
      log.textContent += d.text.replace(/\\\\n/g, '\\n');
      log.scrollTop = log.scrollHeight;
    }
  };

  es.addEventListener('done', function(e) {
    const d = JSON.parse(e.data);
    const result = d.result || '';
    status.innerHTML = result === 'applied'
      ? '<span class="result-ok">&#10003; Applied successfully!</span>'
      : '<span class="result-fail">&#10007; Result: ' + esc(result) + '</span>';
    es.close();
    // Refresh the list after 3 seconds
    setTimeout(load, 3000);
  });

  es.addEventListener('chrome_closed', function() {
    status.textContent = 'Chrome was closed. Click Start to try again.';
    const btn = document.getElementById('btn-' + h);
    if (btn) { btn.disabled = false; btn.textContent = 'Start → Open Chrome'; }
    es.close();
  });

  es.onerror = function() { es.close(); };
}

async function skipJob(h) {
  if (!confirm('Skip this job permanently?')) return;
  await fetch('/api/skip/' + h, { method: 'POST' });
  load();
}

load();
</script>
</body>
</html>"""
