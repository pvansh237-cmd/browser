# Remote Screen Control (WebRTC) — v2

Ek laptop (agent) ki screen doosre laptop ke **browser** mein dikhti hai, mouse / scroll / keyboard se control bhi hota hai.
Doosre (viewer) laptop pe kuch install nahi karna.

```
Agent laptop  <──── video + control (direct P2P, ya TURN relay) ────>  Viewer browser
        \                                                              /
         └────────── Render server (sirf PIN + connection setup) ──────┘
```

## 1. GitHub pe daalo (naya repo)
```powershell
git init
git add .
git commit -m "remote screen v2"
git branch -M main
git remote add origin https://github.com/<tera-username>/<naya-repo>.git
git push -u origin main
```

## 2. Render pe naya Web Service
- New → **Web Service** → apna naya repo chun
- Runtime: **Node** · Build Command: `npm install` · Start Command: `node server.js` · Instance: Free
- (Optional) Settings → Health Check Path: `/health`
- **Environment** mein TURN ki details daal (neeche), phir deploy.

### TURN settings (Render → Environment)
**Turnix.io ke liye (sirf 1 variable zaroori):**

| Key | Value |
|---|---|
| `TURNIX_API_TOKEN` | Turnix dashboard → API Tokens → Create new API Token (token sirf ek baar dikhta hai, copy kar le) |
| `TURNIX_REGION` *(optional)* | jaise `eu-central` — TURN server ka region |
| `TURNIX_TTL` *(optional)* | credentials ki life seconds mein, default 86400 (24 ghante) |

Credentials tujhe dashboard mein khud nahi banane, server API token se apne aap le leta hai (aur refresh bhi karta hai).
Token ko kabhi GitHub pe mat daal, sirf Render ke Environment mein.

Doosre providers ke liye (agar kabhi badalna pade): `TURN_API_URL`, ya `METERED_DOMAIN` + `METERED_API_KEY`, ya `TURN_URL` + `TURN_USER` + `TURN_PASS`.

**Check kaise karein:** deploy ke baad browser mein `https://<tera-link>/health` khol. `"turn":{"configured":true,"source":"turnix"...}` dikhna chahiye.
Render logs mein `[ice] Turnix OK` dikhe to sab theek. `Turnix API failed: HTTP 401` = token galat.

## 3. Agent chalao (jis laptop ki screen share karni hai)
Windows 10/11. Pehle ek baar:
```powershell
cd agent
python -m pip install -r requirements.txt
```
Server link batao (**koi ek**):
- `agent/server_url.txt` file bana ke usme apna Render link likh de (`https://xxxx.onrender.com`), ya
- `python agent.py --server wss://xxxx.onrender.com`, ya
- `agent/settings.py` mein `DEFAULT_SERVER` badal de.

```powershell
python agent.py          # console version (PIN print hota hai)
python gui_agent.py      # window wala version
```
Viewer laptop pe Render ka link browser mein khol, PIN daal, Connect.

## 4. .exe banana (Windows pe)
`agent\build_exe.bat` double-click kar. Exe `agent\dist\ScreenShareAgent.exe` mein banegi.
Exe ke bagal mein `server_url.txt` rakh de (ya app ke box mein link paste kar) — dobara build nahi karna padega.
Agar exe khulte hi band ho jaye to `build_exe.bat` mein `--windowed` hata ke dobara bana, console mein error dikhega.

## 5. Connection 1 ghanta+ kaise chalta rahega
- Server har 25 s mein ping karta hai, aur Render free server ko sone nahi deta (self-ping).
- Agent ka server se link toot jaye to wo **wahi PIN** ke saath khud wapas judta hai. Render restart ho tab bhi.
- Viewer ka link toot jaye ya video ruk jaye (12 s) to browser khud dobara judta hai (PIN dobara nahi daalna).
- UAC prompt / lock screen pe screen grab fail ho to stream band nahi hoti, last frame dikhta rehta hai.
- Link toot jaye to remote PC pe koi key / mouse button atka nahi rehta.
- Agent laptop **sleep na ho** (plug in rakh, Power settings mein sleep = Never). Sleep mein agent chalna band ho jata hai.

## 6. Tuning (`agent/agent_core.py` ke top pe)
| Setting | Matlab |
|---|---|
| `MAX_WIDTH` | 1280 default. Zyada sharp: 1600 / 1920. Halka: 960 |
| `FPS` | 15 default. Smooth: 20-30. Halka: 10 |
| `BITRATE_STEPS` | Net ke hisaab se video khud 0.3 se 2.5 Mbps ke beech badalta hai |
| `MONITOR_INDEX` | 1 = main monitor, 2 = doosra |
| `SINGLE_VIEWER` | True = naya viewer purane ko hata deta hai |
| `SCROLL_GAIN` | Scroll speed |

## Control kaise kaam karta hai
- Mouse: move, left / right / middle click, drag, double-click
- Scroll: mouse wheel + touchpad, vertical + horizontal
- Keyboard: saari keys (Shift, Ctrl+C / V, F-keys, arrows, numpad...). Viewer pe video pe click kar ke focus de.
- Fullscreen button viewer page pe hai.
