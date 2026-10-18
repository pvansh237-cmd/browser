/**
 * server.js — WebRTC SIGNALING server (tiny text messages only).
 *
 * Video / mouse / keyboard NEVER pass through here. This server only helps
 * the two laptops find each other (PIN -> offer/answer) and hands out the
 * STUN/TURN settings. Once connected the laptops talk directly (or via TURN).
 *
 * Reliability features:
 *  - ping/pong heartbeat so Render's proxy never drops idle sockets
 *  - agent can RESUME its room (same PIN) after a network blip / server restart
 *  - room is kept for 15 min if the agent's signaling socket drops
 *  - self-ping so the free Render instance doesn't fall asleep
 *  - TURN credentials fetched from your provider's API (cached, auto-refreshed)
 */
const express = require('express');
const http = require('http');
const crypto = require('crypto');
const path = require('path');
const WebSocket = require('ws');

const app = express();
app.disable('x-powered-by');
app.use(express.static(path.join(__dirname, 'public')));

// ------------------------------------------------------------------ ICE / TURN
const STUN = { urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] };
const ICE_TTL_MS = 10 * 60 * 1000;   // refresh TURN credentials every 10 min
const ICE_RETRY_MS = 30 * 1000;      // retry sooner if the API failed
let iceCache = { servers: null, at: 0, ttl: 0 };

function isIceServer(s) { return s && s.urls && (typeof s.urls === 'string' || Array.isArray(s.urls)); }

// Accepts: [ {urls,username,credential}, ... ]  |  { iceServers: [...] }
//          { iceServers: {urls,...} }  |  { urls, username, credential }
function normalizeIce(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data.filter(isIceServer);
  if (data.iceServers) return normalizeIce(data.iceServers);
  if (isIceServer(data)) return [data];
  return [];
}

function turnApiUrl() {
  if (process.env.TURN_API_URL) return process.env.TURN_API_URL;
  if (process.env.METERED_DOMAIN && process.env.METERED_API_KEY) {
    return `https://${process.env.METERED_DOMAIN}/api/v1/turn/credentials?apiKey=${encodeURIComponent(process.env.METERED_API_KEY)}`;
  }
  return null;
}

async function getIceServers() {
  const now = Date.now();
  if (iceCache.servers && now - iceCache.at < iceCache.ttl) return iceCache.servers;

  let turn = [];
  let apiFailed = false;
  const url = turnApiUrl();
  if (url) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 7000);
      const res = await fetch(url, { signal: ctl.signal });
      clearTimeout(t);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      turn = normalizeIce(await res.json());
      if (!turn.length) throw new Error('API returned no usable ICE servers');
    } catch (e) {
      apiFailed = true;
      console.error('[ice] TURN API failed:', e.message);
      if (iceCache.servers) {            // keep the last good credentials
        iceCache.at = now; iceCache.ttl = ICE_RETRY_MS;
        return iceCache.servers;
      }
    }
  }
  if (!turn.length && process.env.TURN_URL) {
    turn = [{
      urls: process.env.TURN_URL.split(',').map(s => s.trim()).filter(Boolean),
      username: process.env.TURN_USER,
      credential: process.env.TURN_PASS,
    }];
  }
  const servers = [STUN, ...turn];
  iceCache = { servers, at: now, ttl: apiFailed ? ICE_RETRY_MS : ICE_TTL_MS };
  return servers;
}

// ------------------------------------------------------------------ rooms
// code -> { code, token, hostWs|null, viewers: Map<id, ws>, cleanupTimer }
const rooms = new Map();
const HOST_GRACE_MS = 15 * 60 * 1000;

function send(ws, obj) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    try { ws.send(JSON.stringify(obj)); } catch { /* ignore */ }
  }
}

function generateCode() {
  let code;
  do { code = Math.floor(100000 + Math.random() * 900000).toString(); } while (rooms.has(code));
  return code;
}

// ---- brute-force protection (per IP)
const fails = new Map();
const FAIL_WINDOW_MS = 10 * 60 * 1000;
const MAX_FAILS = 10;
function isBlocked(ip) {
  const f = fails.get(ip);
  if (!f) return false;
  if (Date.now() - f.first > FAIL_WINDOW_MS) { fails.delete(ip); return false; }
  return f.count >= MAX_FAILS;
}
function addFail(ip) {
  const f = fails.get(ip);
  if (!f || Date.now() - f.first > FAIL_WINDOW_MS) fails.set(ip, { count: 1, first: Date.now() });
  else f.count++;
}

app.get('/health', (req, res) => {
  res.json({ ok: true, rooms: rooms.size, uptime: Math.round(process.uptime()) });
});

const server = http.createServer(app);
const wss = new WebSocket.Server({ server, maxPayload: 1024 * 1024 });

async function registerHost(ws, msg) {
  const ice = await getIceServers();
  const wantCode = typeof msg.code === 'string' && /^\d{6}$/.test(msg.code) ? msg.code : null;
  const wantToken = typeof msg.token === 'string' && msg.token.length >= 16 ? msg.token : null;

  let room = null;
  let resumed = false;

  if (wantCode && wantToken) {
    const existing = rooms.get(wantCode);
    if (existing && existing.token === wantToken) {
      room = existing; resumed = true;                       // normal resume
    } else if (!existing) {
      room = { code: wantCode, token: wantToken, hostWs: null, viewers: new Map(), cleanupTimer: null };
      rooms.set(wantCode, room); resumed = true;            // server restarted: restore same PIN
    }
  }
  if (!room) {
    const code = generateCode();
    room = { code, token: crypto.randomBytes(16).toString('hex'), hostWs: null, viewers: new Map(), cleanupTimer: null };
    rooms.set(code, room);
  }

  if (room.cleanupTimer) { clearTimeout(room.cleanupTimer); room.cleanupTimer = null; }
  if (room.hostWs && room.hostWs !== ws) {
    room.hostWs.role = null;                                // old socket no longer the host
    try { room.hostWs.terminate(); } catch { /* ignore */ }
  }
  room.hostWs = ws;
  ws.role = 'host';
  ws.code = room.code;

  send(ws, { type: 'code', code: room.code, token: room.token, iceServers: ice, resumed });
  console.log(`[host] room ${room.code} ${resumed ? 'resumed' : 'created'}`);
  if (resumed) for (const v of room.viewers.values()) send(v, { type: 'host-online' });
}

async function joinViewer(ws, msg) {
  if (isBlocked(ws.ip)) {
    send(ws, { type: 'error', reason: 'blocked', message: 'Too many wrong PINs. Try again in a few minutes.' });
    return;
  }
  const code = typeof msg.code === 'string' ? msg.code : '';
  const room = /^\d{6}$/.test(code) ? rooms.get(code) : null;
  if (!room) {
    addFail(ws.ip);
    send(ws, { type: 'error', reason: 'invalid', message: 'Wrong PIN, or the host app is not running.' });
    return;
  }
  const ice = await getIceServers();
  ws.role = 'viewer';
  ws.code = code;
  room.viewers.set(ws.id, ws);
  send(ws, { type: 'joined', iceServers: ice });

  if (room.hostWs && room.hostWs.readyState === WebSocket.OPEN) {
    send(room.hostWs, { type: 'host:viewer-joined', viewerId: ws.id, iceServers: ice });
    console.log(`[viewer] ${ws.id} joined room ${code}`);
  } else {
    send(ws, { type: 'host-offline' });
  }
}

wss.on('connection', (ws, req) => {
  ws.id = crypto.randomBytes(5).toString('hex');
  ws.ip = ((req.headers['x-forwarded-for'] || req.socket.remoteAddress || '') + '').split(',')[0].trim();
  ws.role = null;
  ws.code = null;
  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });
  ws.on('error', () => {});

  ws.on('message', async (data) => {
    let msg;
    try { msg = JSON.parse(data.toString()); } catch { return; }
    try {
      switch (msg.type) {
        case 'host:register': await registerHost(ws, msg); break;
        case 'viewer:join': await joinViewer(ws, msg); break;

        case 'signal:offer': {            // host -> viewer
          const room = ws.role === 'host' ? rooms.get(ws.code) : null;
          const v = room && room.viewers.get(msg.to);
          if (v) send(v, { type: 'signal:offer', from: ws.id, offer: msg.offer });
          break;
        }
        case 'signal:answer': {           // viewer -> host
          const room = ws.role === 'viewer' ? rooms.get(ws.code) : null;
          if (room) send(room.hostWs, { type: 'signal:answer', from: ws.id, answer: msg.answer });
          break;
        }
        case 'ping': send(ws, { type: 'pong' }); break;
      }
    } catch (e) {
      console.error('[ws] handler error:', e.message);
    }
  });

  ws.on('close', () => {
    const room = ws.code ? rooms.get(ws.code) : null;
    if (!room) return;
    if (ws.role === 'host' && room.hostWs === ws) {
      room.hostWs = null;
      for (const v of room.viewers.values()) send(v, { type: 'host-offline' });
      // keep the room so the agent can come back with the same PIN
      room.cleanupTimer = setTimeout(() => {
        if (!room.hostWs) { rooms.delete(room.code); console.log(`[host] room ${room.code} expired`); }
      }, HOST_GRACE_MS);
      console.log(`[host] room ${room.code} signaling dropped (kept for resume)`);
    } else if (ws.role === 'viewer') {
      // NOTE: we do NOT tell the host. The P2P link may still be healthy even if
      // this signaling socket dropped. The agent watches the P2P state itself.
      room.viewers.delete(ws.id);
    }
  });
});

// heartbeat: keeps proxies from closing idle sockets, drops dead ones
setInterval(() => {
  wss.clients.forEach((ws) => {
    if (!ws.isAlive) return ws.terminate();
    ws.isAlive = false;
    try { ws.ping(); } catch { /* ignore */ }
  });
}, 25000);

// Render free instances sleep after ~15 min without inbound HTTP -> ping ourselves
if (process.env.RENDER_EXTERNAL_URL) {
  setInterval(() => {
    fetch(process.env.RENDER_EXTERNAL_URL + '/health').catch(() => {});
  }, 9 * 60 * 1000);
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, async () => {
  console.log(`✅ Signaling server running on port ${PORT}`);
  const ice = await getIceServers();
  const turn = ice.length - 1;
  console.log(turn > 0 ? `✅ TURN configured (${turn} server entry)` : '⚠️  No TURN configured — set TURN_API_URL or TURN_URL/TURN_USER/TURN_PASS');
});

process.on('SIGTERM', () => { server.close(); process.exit(0); });
