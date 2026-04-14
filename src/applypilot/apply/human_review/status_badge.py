"""Status badge injection."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

import types

try:
    import websocket as _websocket
except ModuleNotFoundError:
    _websocket = types.SimpleNamespace(WebSocket=type("_M", (), {"__init__": lambda *a, **k: None}))


def inject_status_badge(cdp_port: int, worker_id: int) -> bool:
    """Inject a persistent floating status badge into every page in this Chrome.

    Uses Playwright's addInitScript so it persists across navigations.
    Polls the worker's HTTP server (7380+worker_id) every 2s and displays
    job title, status, and Take Over / Give Back buttons.

    Returns True on success.
    """
    server_port = 7380 + worker_id

    badge_js = f"""
(function() {{
  if (window.__ap_badge) return;
  window.__ap_badge = true;
  var PORT = {server_port};
  var WID = {worker_id};

  function _createBadge() {{
    if (document.getElementById('__ap_badge')) return;
    var el = document.createElement('div');
    el.id = '__ap_badge';
    el.style.cssText = [
      'position:fixed', 'bottom:12px', 'right:12px', 'z-index:2147483647',
      'background:#1e1b4b', 'color:#e0e7ff',
      'font-family:system-ui,monospace', 'font-size:12px',
      'padding:8px 12px', 'border-radius:8px',
      'box-shadow:0 4px 12px rgba(0,0,0,0.5)',
      'border:2px solid transparent',
      'min-width:180px', 'max-width:320px',
      'user-select:none', 'cursor:default',
      'transition:border-color 0.15s,box-shadow 0.15s,opacity 0.2s'
    ].join(';');

    var title = document.createElement('div');
    title.id = '__ap_badge_title';
    title.style.cssText = 'font-weight:700;font-size:11px;color:#a5b4fc;margin-bottom:3px';
    title.textContent = 'W{worker_id} • connecting...';

    var status = document.createElement('div');
    status.id = '__ap_badge_status';
    status.style.cssText = 'font-size:11px;color:#c7d2fe;white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
    status.textContent = '...';

    var btns = document.createElement('div');
    btns.id = '__ap_badge_btns';
    btns.style.cssText = 'margin-top:6px;display:none;gap:6px;flex-wrap:wrap';

    el.appendChild(title);
    el.appendChild(status);
    el.appendChild(btns);

    function _tryInsert() {{
      if (document.body) document.body.appendChild(el);
      else setTimeout(_tryInsert, 100);
    }}
    _tryInsert();

    // Highlight badge yellow when this Chrome window is the active/focused one
    function _updateFocus() {{
      var badge = document.getElementById('__ap_badge');
      if (!badge) return;
      if (document.hasFocus()) {{
        badge.style.borderColor = '#fbbf24';
        badge.style.boxShadow = '0 4px 12px rgba(0,0,0,0.5),0 0 0 3px rgba(251,191,36,0.35)';
      }} else {{
        badge.style.borderColor = 'transparent';
        badge.style.boxShadow = '0 4px 12px rgba(0,0,0,0.5)';
      }}
    }}
    window.addEventListener('focus', _updateFocus);
    window.addEventListener('blur', _updateFocus);
    // Run once immediately in case this window is already focused
    setTimeout(_updateFocus, 50);
  }}

  function _poll() {{
    fetch('http://localhost:' + PORT + '/api/status', {{signal: AbortSignal.timeout(1500)}})
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        var el = document.getElementById('__ap_badge');
        if (!el) return;
        var t = document.getElementById('__ap_badge_title');
        var s = document.getElementById('__ap_badge_status');
        var btns = document.getElementById('__ap_badge_btns');
        var st = d.status || 'idle';
        var colors = {{
          applying: '#4ade80', paused_by_user: '#fbbf24', idle: '#6b7280',
          waiting_human: '#c084fc', needs_human: '#c084fc',
          waiting_answer: '#fbbf24', applied: '#34d399',
          failed: '#f87171', credits_exhausted: '#f87171'
        }};
        var color = colors[st] || '#94a3b8';
        t.textContent = 'W' + WID + ' • ' + st;
        t.style.color = color;
        s.textContent = d.jobTitle ? d.jobTitle.slice(0,45) : '(idle)';

        // Show buttons based on status
        btns.innerHTML = '';
        btns.style.display = 'none';
        if (st === 'applying') {{
          btns.style.display = 'flex';
          var b = document.createElement('button');
          b.textContent = '⏸ Take Over';
          b.style.cssText = 'background:#4f46e5;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer';
          b.onclick = function() {{
            b.textContent = 'Taking over...';
            fetch('http://localhost:' + PORT + '/api/takeover', {{method:'POST'}});
          }};
          btns.appendChild(b);
        }} else if (st === 'paused_by_user') {{
          btns.style.display = 'flex';
          var bb = document.createElement('button');
          bb.textContent = '▶ Give Back';
          bb.style.cssText = 'background:#059669;color:#fff;border:none;border-radius:4px;padding:3px 8px;font-size:11px;cursor:pointer';
          bb.onclick = function() {{
            bb.textContent = 'Resuming...';
            fetch('http://localhost:' + PORT + '/api/handback', {{method:'POST'}});
          }};
          btns.appendChild(bb);
        }}
      }})
      .catch(function() {{
        var t = document.getElementById('__ap_badge_title');
        if (t) {{ t.textContent = 'W' + WID + ' • offline'; t.style.color = '#6b7280'; }}
      }});
  }}

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', _createBadge);
  }} else {{
    _createBadge();
  }}
  setInterval(_poll, 2000);
  setTimeout(_poll, 500);
}})();
"""

    badge_js_escaped = badge_js.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

    node_script = f"""
const {{ chromium }} = require('@playwright/test');
(async () => {{
  try {{
    const b = await chromium.connectOverCDP('http://localhost:{cdp_port}');
    const ctx = b.contexts()[0];
    const js = `{badge_js_escaped}`;
    await ctx.addInitScript(js);
    const pages = ctx.pages();
    for (const p of pages) {{
      try {{ await p.evaluate(js); }} catch(e) {{}}
    }}
    await b.close();
    process.exit(0);
  }} catch(e) {{
    process.stderr.write(e.message + '\\n');
    process.exit(1);
  }}
}})();
"""

    try:
        result = subprocess.run(
            ["node", "-e", node_script],
            timeout=15,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.debug("Status badge injection failed: %s", result.stderr[:200])
            return False
        return True
    except Exception as e:
        logger.debug("Status badge injection error: %s", e)
        return False
