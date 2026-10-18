@echo off
REM Build ScreenShareAgent.exe (run this on Windows, inside the agent folder)
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --name ScreenShareAgent ^
  --collect-all aiortc --collect-all av --collect-all aioice --collect-all pylibsrtp ^
  --collect-all pyee --collect-all google_crc32c ^
  gui_agent.py
echo.
echo Done. Your exe is in:  dist\ScreenShareAgent.exe
echo Put a file named server_url.txt (your Render link) next to the exe, or paste the link in the app.
pause
