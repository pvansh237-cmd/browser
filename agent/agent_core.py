"""
agent_core.py — runs on the laptop whose screen is shared.

 * Connects to the signaling server (tiny text messages) with the PIN.
 * Screen video + mouse/keyboard go over a WebRTC link (direct, or via TURN).
 * Stays alive by itself:
     - if the signaling socket drops it reconnects and keeps the SAME PIN
     - if the viewer's link dies, the viewer re-joins and gets a fresh link
     - a failed screen grab (UAC prompt, lock screen) never kills the stream
     - keys/buttons are released if the link drops (no stuck keys)
"""

import asyncio
import json
import logging

import av
import cv2
import mss
import numpy as np
import pyautogui
import websockets
from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription, VideoStreamTrack)

logging.basicConfig(level=logging.WARNING)

# pyautogui waits 0.1 s after EVERY call by default -> laggy mouse/keyboard. Turn that off.
pyautogui.PAUSE = 0
pyautogui.FAILSAFE = False
pyautogui.MINIMUM_DURATION = 0

# ======================= settings you can tune =======================
FPS = 15              # 10 = lighter, 20-30 = smoother (more CPU/bandwidth)
MAX_WIDTH = 1280      # 960 = lighter, 1600/1920 = sharper
# Bitrate adapts to the network between these steps (bits/second).
BITRATE_STEPS = [300_000, 600_000, 1_000_000, 1_600_000, 2_500_000]
MONITOR_INDEX = 1     # 1 = main monitor, 2 = second monitor...
SINGLE_VIEWER = True  # a new viewer replaces the old one (saves CPU, avoids two people fighting)
SCROLL_GAIN = 1.2     # scroll speed (bigger = faster)
# =====================================================================


def _tune_encoder():
    """aiortc's default video bitrate is tiny (0.5-1.5 Mbps) and restarts the encoder on every
    network estimate change (blur/stutter). Use fixed steps instead. Never fatal if aiortc changes."""
    try:
        import aiortc.mediastreams as ms
        from aiortc.codecs import h264, vpx

        ms.VIDEO_PTIME = 1 / FPS

        def snap(bitrate):
            ok = [s for s in BITRATE_STEPS if s <= bitrate * 0.85]
            return ok[-1] if ok else BITRATE_STEPS[0]

        for mod, cls in ((vpx, vpx.Vp8Encoder), (h264, h264.H264Encoder)):
            mod.MIN_BITRATE = BITRATE_STEPS[0]
            mod.MAX_BITRATE = BITRATE_STEPS[-1]
            mod.DEFAULT_BITRATE = BITRATE_STEPS[1]
            attr = "_%s__target_bitrate" % cls.__name__
            cls.target_bitrate = property(
                lambda self, a=attr: getattr(self, a),
                lambda self, v, a=attr: setattr(self, a, snap(v)),
            )
    except Exception as e:  # pragma: no cover
        print("[warn] could not tune video encoder:", e)


_tune_encoder()


def build_ice_servers(raw):
    servers = []
    for s in raw or [{"urls": "stun:stun.l.google.com:19302"}]:
        try:
            servers.append(RTCIceServer(urls=s["urls"], username=s.get("username"),
                                        credential=s.get("credential")))
        except Exception as e:
            print("[warn] bad ICE server entry:", e)
    return servers


# --------------------------------------------------------------------------- screen
class ScreenTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self):
        super().__init__()
        self._sct = None
        self._monitor = None
        self._last = None
        self._fails = 0
        self._open()

    def _open(self):
        try:
            if self._sct:
                self._sct.close()
        except Exception:
            pass
        self._sct = mss.mss()
        mons = self._sct.monitors
        self._monitor = mons[MONITOR_INDEX] if MONITOR_INDEX < len(mons) else mons[1]

    @property
    def monitor(self):
        return self._monitor

    def _grab(self):
        raw = np.array(self._sct.grab(self._monitor))
        img = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
        h, w = img.shape[:2]
        if w > MAX_WIDTH:
            img = cv2.resize(img, (MAX_WIDTH, (int(MAX_WIDTH * h / w) // 2) * 2),
                             interpolation=cv2.INTER_AREA)
        elif w % 2 or h % 2:
            img = img[: h - h % 2, : w - w % 2]
        return img

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        try:
            img = self._grab()
            self._last = img
            self._fails = 0
        except Exception as e:
            # secure desktop / lock screen / display change: keep the stream alive with the last frame
            self._fails += 1
            if self._fails in (1, 50):
                print("[warn] screen grab failed:", e)
            try:
                self._open()
            except Exception:
                pass
            img = self._last if self._last is not None else np.zeros((720, 1280, 3), np.uint8)
        frame = av.VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def stop(self):
        super().stop()
        try:
            if self._sct:
                self._sct.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- input
def _build_code_map():
    m = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        m["Key" + c.upper()] = c
    for d in "0123456789":
        m["Digit" + d] = d
        m["Numpad" + d] = "num" + d
    for i in range(1, 13):
        m["F%d" % i] = "f%d" % i
    m.update({
        "Minus": "-", "Equal": "=", "BracketLeft": "[", "BracketRight": "]", "Backslash": "\\",
        "Semicolon": ";", "Quote": "'", "Comma": ",", "Period": ".", "Slash": "/", "Backquote": "`",
        "IntlBackslash": "\\",
        "Space": "space", "Enter": "enter", "NumpadEnter": "enter", "Backspace": "backspace",
        "Tab": "tab", "Escape": "esc", "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left",
        "ArrowRight": "right", "Home": "home", "End": "end", "PageUp": "pageup",
        "PageDown": "pagedown", "Insert": "insert", "Delete": "delete", "CapsLock": "capslock",
        "ShiftLeft": "shiftleft", "ShiftRight": "shiftright", "ControlLeft": "ctrlleft",
        "ControlRight": "ctrlright", "AltLeft": "altleft", "AltRight": "altright",
        "MetaLeft": "winleft", "MetaRight": "winright", "ContextMenu": "apps",
        "NumpadAdd": "add", "NumpadSubtract": "subtract", "NumpadMultiply": "multiply",
        "NumpadDivide": "divide", "NumpadDecimal": "decimal", "PrintScreen": "printscreen",
        "NumLock": "numlock", "ScrollLock": "scrolllock", "Pause": "pause",
    })
    return {k: v for k, v in m.items() if v in pyautogui.KEYBOARD_KEYS}


CODE_MAP = _build_code_map()

_LEGACY_KEYS = {
    " ": "space", "Enter": "enter", "Backspace": "backspace", "Tab": "tab", "Escape": "esc",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    "Shift": "shift", "Control": "ctrl", "Alt": "alt", "Meta": "win", "Delete": "delete",
    "CapsLock": "capslock",
}


def map_key(code, key):
    if code in CODE_MAP:
        return CODE_MAP[code]
    if key in _LEGACY_KEYS:
        return _LEGACY_KEYS[key]
    if key and len(key) == 1:
        return key.lower()
    return None


class Controller:
    """Turns messages from the viewer into mouse/keyboard actions on this PC."""

    def __init__(self, monitor):
        self.mon = monitor
        self.keys = set()
        self.buttons = set()

    def _xy(self, msg):
        x = min(1.0, max(0.0, float(msg.get("x", 0))))
        y = min(1.0, max(0.0, float(msg.get("y", 0))))
        return (int(self.mon["left"] + x * (self.mon["width"] - 1)),
                int(self.mon["top"] + y * (self.mon["height"] - 1)))

    def handle(self, raw):
        try:
            msg = json.loads(raw)
            action = msg.get("action")
            if action == "move":
                pyautogui.moveTo(*self._xy(msg), _pause=False)
            elif action == "down":
                x, y = self._xy(msg)
                btn = msg.get("button", "left")
                pyautogui.moveTo(x, y, _pause=False)
                pyautogui.mouseDown(button=btn, _pause=False)
                self.buttons.add(btn)
            elif action == "up":
                x, y = self._xy(msg)
                btn = msg.get("button", "left")
                pyautogui.moveTo(x, y, _pause=False)
                pyautogui.mouseUp(button=btn, _pause=False)
                self.buttons.discard(btn)
            elif action == "click":  # old viewers
                x, y = self._xy(msg)
                pyautogui.click(x, y, button=msg.get("button", "left"), _pause=False)
            elif action == "scroll":
                dy = float(msg.get("dy", msg.get("amount", 0) * -50))
                dx = float(msg.get("dx", 0))
                if int(dy * SCROLL_GAIN):
                    pyautogui.scroll(int(-dy * SCROLL_GAIN), _pause=False)
                if int(dx * SCROLL_GAIN):
                    try:
                        pyautogui.hscroll(int(dx * SCROLL_GAIN), _pause=False)
                    except Exception:
                        pass
            elif action == "key_down":
                k = map_key(msg.get("code"), msg.get("key"))
                if k:
                    pyautogui.keyDown(k, _pause=False)
                    self.keys.add(k)
            elif action == "key_up":
                k = map_key(msg.get("code"), msg.get("key"))
                if k:
                    pyautogui.keyUp(k, _pause=False)
                    self.keys.discard(k)
            elif action == "release_all":
                self.release_all()
        except Exception as e:
            print("control error:", e)

    def release_all(self):
        for k in list(self.keys):
            try:
                pyautogui.keyUp(k, _pause=False)
            except Exception:
                pass
        for b in list(self.buttons):
            try:
                pyautogui.mouseUp(button=b, _pause=False)
            except Exception:
                pass
        self.keys.clear()
        self.buttons.clear()


# --------------------------------------------------------------------------- agent
class Peer:
    def __init__(self, pc, track, controller):
        self.pc = pc
        self.track = track
        self.controller = controller


class Agent:
    def __init__(self, on_pin_ready, on_status, stop_event):
        self.on_pin_ready = on_pin_ready
        self.on_status = on_status
        self.stop_event = stop_event
        self.peers = {}      # viewer_id -> Peer (kept alive even if signaling drops)
        self.code = None
        self.token = None
        self.ice = build_ice_servers(None)
        self.tasks = set()

    # ---- peers
    async def close_peer(self, viewer_id, peer):
        if self.peers.get(viewer_id) is peer:
            del self.peers[viewer_id]
        try:
            peer.controller.release_all()
        except Exception:
            pass
        try:
            peer.track.stop()
        except Exception:
            pass
        try:
            await peer.pc.close()
        except Exception:
            pass

    async def close_all(self):
        for vid, p in list(self.peers.items()):
            await self.close_peer(vid, p)

    async def start_peer(self, ws, viewer_id, ice_raw):
        if ice_raw:
            self.ice = build_ice_servers(ice_raw)
        for vid, p in list(self.peers.items()):
            if SINGLE_VIEWER or vid == viewer_id:
                await self.close_peer(vid, p)

        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=self.ice))
        track = ScreenTrack()
        controller = Controller(track.monitor)
        peer = Peer(pc, track, controller)
        self.peers[viewer_id] = peer

        @pc.on("connectionstatechange")
        async def _on_state():
            if self.peers.get(viewer_id) is not peer:
                return
            st = pc.connectionState
            if st == "connected":
                self.on_status("✅ Viewer connected (live)")
            elif st in ("failed", "closed"):
                self.on_status("Viewer link ended — waiting for viewer to reconnect...")
                await self.close_peer(viewer_id, peer)
            else:
                self.on_status(f"Link state: {st}")

        pc.addTrack(track)
        control = pc.createDataChannel("control")
        move = pc.createDataChannel("move", ordered=False, maxRetransmits=0)
        for ch in (control, move):
            ch.on("message", controller.handle)
            ch.on("close", controller.release_all)

        await pc.setLocalDescription(await pc.createOffer())   # waits for ICE gathering (incl. TURN)
        if self.peers.get(viewer_id) is not peer:
            return
        await ws.send(json.dumps({
            "type": "signal:offer", "to": viewer_id,
            "offer": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
        }))
        self.on_status("Offer sent, connecting...")

    async def _safe_start_peer(self, ws, viewer_id, ice_raw):
        try:
            await self.start_peer(ws, viewer_id, ice_raw)
        except Exception as e:
            self.on_status(f"Could not start link ({type(e).__name__}); viewer will retry")
            print("start_peer error:", repr(e))

    async def handle_message(self, ws, raw):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        t = msg.get("type")
        if t == "host:viewer-joined":
            task = asyncio.ensure_future(self._safe_start_peer(ws, msg["viewerId"], msg.get("iceServers")))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
        elif t == "signal:answer":
            peer = self.peers.get(msg.get("from"))
            if peer:
                try:
                    a = msg["answer"]
                    await peer.pc.setRemoteDescription(RTCSessionDescription(sdp=a["sdp"], type=a["type"]))
                except Exception as e:
                    print("answer error:", repr(e))

    # ---- signaling session (re-created whenever the socket drops)
    async def session(self, server_url):
        async with websockets.connect(server_url, max_size=None, open_timeout=60,
                                      ping_interval=20, ping_timeout=40) as ws:
            reg = {"type": "host:register"}
            if self.code and self.token:
                reg.update(code=self.code, token=self.token)
            await ws.send(json.dumps(reg))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if reply.get("type") != "code":
                raise RuntimeError("unexpected reply from server")
            self.code, self.token = reply["code"], reply["token"]
            self.ice = build_ice_servers(reply.get("iceServers"))
            self.on_pin_ready(self.code)
            self.on_status("Waiting for a viewer to connect..." if not self.peers
                           else "Signaling restored (viewer link still running)")

            async def close_on_stop():
                await self.stop_event.wait()
                await ws.close()

            watcher = asyncio.ensure_future(close_on_stop())
            try:
                async for raw in ws:
                    await self.handle_message(ws, raw)
            finally:
                watcher.cancel()

    async def run(self, server_url):
        backoff = 1
        while not self.stop_event.is_set():
            started = asyncio.get_event_loop().time()
            try:
                await self.session(server_url)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.on_status(f"Server connection lost ({type(e).__name__}). Retrying in {backoff}s...")
            if self.stop_event.is_set():
                break
            if asyncio.get_event_loop().time() - started > 30:
                backoff = 1          # it was a healthy session; retry fast
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 10)
        await self.close_all()
        for t in list(self.tasks):
            t.cancel()


async def run_agent(server_url, on_pin_ready, on_status, stop_event):
    await Agent(on_pin_ready, on_status, stop_event).run(server_url)
