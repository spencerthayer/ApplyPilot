// ApplyPilot Control Panel — background service worker
// Polls all worker servers every 3s and updates the extension badge.
// Constants from config.js (loaded first via manifest).

async function fetchWorker(workerId) {
    const port = APPLYPILOT.BASE_PORT + workerId;
  try {
    const resp = await fetch(`http://localhost:${port}/api/status`, {
        signal: AbortSignal.timeout(APPLYPILOT.API_TIMEOUT_MS),
    });
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

async function pollAll() {
  const results = await Promise.all(
      Array.from({length: APPLYPILOT.MAX_WORKERS}, (_, i) => fetchWorker(i))
  );
  const workers = results
    .map((d, i) => (d ? { ...d, workerId: d.workerId ?? i } : null))
    .filter(Boolean);

  await chrome.storage.local.set({ workers, lastPoll: Date.now() });
  updateBadge(workers);
}

function updateBadge(workers) {
    const hitl = workers.filter(w => w.status === 'waiting_human' || w.status === 'needs_human');
    const applying = workers.filter(w => w.status === 'applying');
    const paused = workers.filter(w => w.status === 'paused_by_user');

  if (hitl.length > 0) {
    chrome.action.setBadgeText({ text: '!' });
      chrome.action.setBadgeBackgroundColor({color: [168, 85, 247, 255]});
  } else if (paused.length > 0) {
    chrome.action.setBadgeText({ text: String(paused.length) });
      chrome.action.setBadgeBackgroundColor({color: [234, 179, 8, 255]});
  } else if (applying.length > 0) {
    chrome.action.setBadgeText({ text: String(applying.length) });
      chrome.action.setBadgeBackgroundColor({color: [34, 197, 94, 255]});
  } else {
    chrome.action.setBadgeText({ text: '' });
  }
}

function makeWorkerIcon(workerId, size) {
    const color = APPLYPILOT.WORKER_COLORS[workerId % APPLYPILOT.WORKER_COLORS.length];
  const canvas = new OffscreenCanvas(size, size);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = '#ffffff';
  ctx.font = `bold ${Math.round(size * 0.6)}px system-ui, sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(workerId), size / 2, size / 2 + 1);
  return ctx.getImageData(0, 0, size, size);
}

// Set per-worker icon if config is available
if (typeof WORKER_CONFIG !== 'undefined' && WORKER_CONFIG.workerId !== null) {
    const wid = WORKER_CONFIG.workerId;
    chrome.storage.local.set({myWorkerId: wid});
  try {
    chrome.action.setIcon({
      imageData: {
          16: makeWorkerIcon(wid, 16),
          48: makeWorkerIcon(wid, 48),
          128: makeWorkerIcon(wid, 128),
      },
    });
  } catch (_) {
  }
}

// Alarms + polling
chrome.alarms.create('poll', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(alarm => {
    if (alarm.name === 'poll') pollAll();
});
setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 25000);
chrome.runtime.onInstalled.addListener(pollAll);
chrome.runtime.onStartup.addListener(pollAll);
chrome.runtime.onMessage.addListener((msg, _, sendResponse) => {
    if (msg.type === 'poll') {
        pollAll().then(() => sendResponse({ok: true}));
        return true;
    }
});
setInterval(pollAll, APPLYPILOT.POLL_MS);
pollAll();
