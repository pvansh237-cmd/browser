'use strict';
/*
 * Viewer. Keeps itself connected: if the P2P link dies, stalls, or the
 * signaling socket drops, it re-joins with the same PIN automatically.
 */
const el = (id) => document.getElementById(id);
const joinBox = el('joinBox'), viewerWrap = el('viewerWrap'), pinInput = el('pinInput');
const joinBtn = el('joinBtn'), statusEl = el('status'), connStateEl = el('connState');
const statsEl = el('stats'), video = el('screen'), holder = el('videoHolder');
const fsBtn = el('fsBtn'), discBtn = el('discBtn');

const S = {
  pin: null, ws: null, pc: null, ctrl: null, moveCh: null,
  want: false, needJoin: false, everConnected: false,
  retry: 0, wsRetry: 0, reconnecting: false, timers: {},
  lastBytes: 0, lastBytesAt: 0, lastStatsAt: 0,
};
let ICE_SERVERS = [{ urls: 'stun:stun.l.google.com:19302' }];

function setTimer(name, fn, ms) { clearTimeout(S.timers[name]); S.timers[name] = setTimeout(fn, ms); }
function clearTimer(name) { clearTimeout(S.timers[name]); S.timers[name] = null; }
function setConn(text, cls) { connStateEl.textContent = text; connStateEl.className = cls || ''; }
const pcConnected = () => S.pc && S.pc.connectionState === 'connected';

// ------------------------------------------------------------ signaling
function start() {
  const code = pinInput.value.trim();
  if (!/^\d{6}$/.test(code)) { statusEl.textContent = 'Enter a valid 6-digit PIN'; return; }
  statusEl.textContent = '';
  Object.assign(S, { pin: code, want: true, everConnected: false, retry: 0, wsRetry: 0, reconnecting: false, needJoin: true });
  statusEl.textContent = 'Connecting...';
  openSignaling();
}

function openSignaling() {
  if (S.ws && (S.ws.readyState === 0 || S.ws.readyState === 1)) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}`);
  S.ws = ws;
  ws.onopen = () => { S.wsRetry = 0; if (S.needJoin) joinRoom(); };
  ws.onmessage = (ev) => { try { onSignal(JSON.parse(ev.data)); } catch (e) { console.error(e); } };
  ws.onerror = () => {};
  ws.onclose = () => {
    if (S.ws !== ws) return;
    S.ws = null;
    if (!S.want) return;
    const delay = Math.min(1000 * 2 ** S.wsRetry, 8000);
    S.wsRetry++;
    if (!pcConnected()) {
      if (S.everConnected) setConn('⚠️ Server link lost, reconnecting...', 'warn');
      else statusEl.textContent = 'Server is waking up / unreachable, retrying...';
    }
    setTimer('ws', openSignaling, delay);
  };
}

function joinRoom() {
  if (!S.ws || S.ws.readyState !== 1) return;
  S.ws.send(JSON.stringify({ type: 'viewer:join', code: S.pin }));
  setTimer('offer', () => scheduleReconnect('no offer from host'), 30000);
}

function requestJoin() {
  S.needJoin = true;
  if (S.ws && S.ws.readyState === 1) joinRoom();
  else openSignaling();
}

async function onSignal(msg) {
  switch (msg.type) {
    case 'joined':
      S.needJoin = false;
      if (msg.iceServers) ICE_SERVERS = msg.iceServers;
      joinBox.style.display = 'none';
      viewerWrap.style.display = 'flex';
      statusEl.textContent = '';
      setConn(S.everConnected ? '⚠️ Reconnecting...' : 'Waiting for host...', 'warn');
      break;

    case 'signal:offer':
      clearTimer('offer');
      await handleOffer(msg.offer);
      break;

    case 'host-offline':
      if (!pcConnected()) setConn('Host app is offline — waiting for it to come back...', 'warn');
      setTimer('offer', () => scheduleReconnect('host offline too long'), 20000);
      break;

    case 'host-online':
      if (!pcConnected()) requestJoin();
      break;

    case 'error':
      if (!S.everConnected && (msg.reason === 'invalid' || msg.reason === 'blocked')) {
        stopAll();
        statusEl.textContent = msg.message;
      } else {
        setConn(msg.message, 'warn');
        setTimer('offer', () => scheduleReconnect('retry after error'), 5000);
      }
      break;
  }
}

// ------------------------------------------------------------ WebRTC
function waitIce(pc, ms) {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') return resolve();
    const done = () => { clearTimeout(t); pc.removeEventListener('icegatheringstatechange', check); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    const t = setTimeout(done, ms);   // don't hang forever: send what we have
    pc.addEventListener('icegatheringstatechange', check);
  });
}

function closePc() {
  clearTimer('conn'); clearTimer('disc');
  if (S.pc) {
    S.pc.onconnectionstatechange = null;
    try { S.pc.close(); } catch { /* ignore */ }
  }
  S.pc = null; S.ctrl = null; S.moveCh = null;
}

async function handleOffer(offer) {
  closePc();
  const pc = new RTCPeerConnection({ iceServers: ICE_SERVERS });
  S.pc = pc;

  pc.ontrack = (e) => {
    if (video.srcObject !== e.streams[0]) video.srcObject = e.streams[0];
    video.play().catch(() => {});
  };
  pc.ondatachannel = (e) => {
    if (e.channel.label === 'move') S.moveCh = e.channel; else S.ctrl = e.channel;
  };
  pc.onconnectionstatechange = () => onPcState(pc);

  try {
    await pc.setRemoteDescription(new RTCSessionDescription(offer));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    await waitIce(pc, 5000);
    if (S.pc !== pc) return;
    S.ws.send(JSON.stringify({
      type: 'signal:answer',
      answer: { type: pc.localDescription.type, sdp: pc.localDescription.sdp },
    }));
  } catch (e) {
    console.error('handleOffer failed', e);
    scheduleReconnect('offer handling failed');
    return;
  }
  setTimer('conn', () => { if (!pcConnected()) scheduleReconnect('connect timeout'); }, 30000);
}

function onPcState(pc) {
  if (pc !== S.pc) return;
  const st = pc.connectionState;
  console.log('connectionState:', st, 'ice:', pc.iceConnectionState);
  if (st === 'connected') {
    clearTimer('conn'); clearTimer('disc'); clearTimer('offer');
    S.everConnected = true; S.retry = 0; S.reconnecting = false;
    const now = performance.now();
    S.lastBytes = 0; S.lastBytesAt = now; S.lastStatsAt = now;
    setConn('✅ Connected — live peer-to-peer');
    video.focus();
  } else if (st === 'disconnected') {
    setConn('⚠️ Network hiccup, trying to recover...', 'warn');
    setTimer('disc', () => { if (!pcConnected()) scheduleReconnect('disconnected too long'); }, 8000);
  } else if (st === 'failed' || st === 'closed') {
    scheduleReconnect('state ' + st);
  } else {
    setConn('Connecting... (' + st + ')', 'warn');
  }
}

function scheduleReconnect(reason) {
  if (!S.want || S.reconnecting) return;
  console.warn('reconnect:', reason);
  S.reconnecting = true;
  clearTimer('offer'); clearTimer('conn'); clearTimer('disc');
  closePc();
  S.retry++;
  const delay = Math.min(500 * 2 ** (S.retry - 1), 8000);
  if (!S.everConnected && S.retry >= 3) {
    setConn('❌ Cannot form a connection. Check TURN settings on the server.', 'bad');
  } else {
    setConn(`⚠️ Connection lost — reconnecting (attempt ${S.retry})...`, 'warn');
  }
  setTimer('reco', () => { S.reconnecting = false; requestJoin(); }, delay);
}

// watchdog: if video bytes stop arriving, the link is dead even if the state says "connected"
setInterval(async () => {
  if (!S.want || !pcConnected()) return;
  try {
    const stats = await S.pc.getStats();
    let bytes = 0, fps = 0, w = 0, h = 0;
    stats.forEach((r) => {
      if (r.type === 'inbound-rtp' && (r.kind === 'video' || r.mediaType === 'video')) {
        bytes = r.bytesReceived || 0; fps = r.framesPerSecond || 0;
        w = r.frameWidth || 0; h = r.frameHeight || 0;
      }
    });
    const now = performance.now();
    if (bytes > S.lastBytes) {
      const kbps = ((bytes - S.lastBytes) * 8) / ((now - S.lastStatsAt) / 1000) / 1000;
      statsEl.textContent = `${w}x${h} · ${Math.round(fps)} fps · ${Math.round(kbps)} kbps`;
      S.lastBytes = bytes; S.lastBytesAt = now;
    }
    S.lastStatsAt = now;
    if (now - S.lastBytesAt > 12000) scheduleReconnect('video stalled');
  } catch { /* ignore */ }
}, 3000);

function stopAll() {
  S.want = false; S.reconnecting = false;
  Object.keys(S.timers).forEach(clearTimer);
  closePc();
  if (S.ws) { const w = S.ws; S.ws = null; try { w.close(); } catch { /* ignore */ } }
  video.srcObject = null;
  viewerWrap.style.display = 'none';
  joinBox.style.display = 'flex';
  statsEl.textContent = '';
}

joinBtn.addEventListener('click', start);
pinInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') start(); });
discBtn.addEventListener('click', stopAll);
fsBtn.addEventListener('click', () => {
  if (document.fullscreenElement) document.exitFullscreen();
  else if (holder.requestFullscreen) holder.requestFullscreen();
});

// ------------------------------------------------------------ remote control
function sendControl(payload, unreliable) {
  const ch = unreliable && S.moveCh && S.moveCh.readyState === 'open' ? S.moveCh : S.ctrl;
  if (ch && ch.readyState === 'open') ch.send(JSON.stringify(payload));
}

function pos(e) {
  const r = video.getBoundingClientRect();
  const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
  return { x, y };
}
const BTN = ['left', 'middle', 'right'];

let lastMove = 0;
video.addEventListener('mousemove', (e) => {
  const now = performance.now();
  if (now - lastMove < 25) return;
  lastMove = now;
  sendControl({ action: 'move', ...pos(e) }, true);
});

const down = new Set();
video.addEventListener('mousedown', (e) => {
  e.preventDefault();
  video.focus();
  down.add(e.button);
  sendControl({ action: 'down', ...pos(e), button: BTN[e.button] || 'left' });
});
window.addEventListener('mouseup', (e) => {
  if (!down.has(e.button)) return;
  down.delete(e.button);
  sendControl({ action: 'up', ...pos(e), button: BTN[e.button] || 'left' });
});
video.addEventListener('contextmenu', (e) => e.preventDefault());
video.addEventListener('dragstart', (e) => e.preventDefault());

// scroll: normalised to pixels, batched every 30 ms (works for mouse wheel & touchpad, vertical + horizontal)
let wDx = 0, wDy = 0, wTimer = null;
video.addEventListener('wheel', (e) => {
  e.preventDefault();
  const k = e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? 800 : 1;
  wDx += e.deltaX * k; wDy += e.deltaY * k;
  if (!wTimer) {
    wTimer = setTimeout(() => {
      sendControl({ action: 'scroll', dx: wDx, dy: wDy });
      wDx = 0; wDy = 0; wTimer = null;
    }, 30);
  }
}, { passive: false });

// keyboard: physical key codes (works with Shift, Ctrl+C/V, F-keys, arrows, numpad...)
video.addEventListener('keydown', (e) => {
  if (e.code === 'F11' || e.code === 'F12') return;   // leave browser fullscreen/devtools alone
  e.preventDefault();
  if (e.repeat) return;
  sendControl({ action: 'key_down', code: e.code, key: e.key });
});
video.addEventListener('keyup', (e) => {
  if (e.code === 'F11' || e.code === 'F12') return;
  e.preventDefault();
  sendControl({ action: 'key_up', code: e.code, key: e.key });
});

// never leave a key/button stuck on the remote PC
const releaseAll = () => { down.clear(); sendControl({ action: 'release_all' }); };
video.addEventListener('blur', releaseAll);
window.addEventListener('blur', releaseAll);
document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });
