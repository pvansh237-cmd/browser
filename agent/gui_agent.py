"""
gui_agent.py — Windows desktop app. Build the .exe with build_exe.bat
"""
import asyncio
import threading
import tkinter as tk
from tkinter import messagebox

import settings
from agent_core import run_agent


class AgentState:
    def __init__(self):
        self.stop_event = None
        self.loop = None
        self.thread = None


state = AgentState()


def start_agent_thread(server_url, on_pin_ready, on_status, on_error, on_stopped):
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        state.loop = loop
        stop_event = asyncio.Event()
        state.stop_event = stop_event
        try:
            loop.run_until_complete(run_agent(server_url, on_pin_ready, on_status, stop_event))
        except Exception as e:
            on_error(str(e))
        finally:
            on_stopped()
            try:
                loop.close()
            except Exception:
                pass

    t = threading.Thread(target=runner, daemon=True)
    state.thread = t
    t.start()


def stop_agent():
    if state.loop and state.stop_event:
        state.loop.call_soon_threadsafe(state.stop_event.set)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Screen Share Agent")
        self.geometry("480x500")
        self.resizable(False, False)
        self.configure(bg="#0f1117")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.running = False

        tk.Label(self, text="🖥️ Screen Share Agent", font=("Segoe UI", 16, "bold"),
                 fg="#4fd1c5", bg="#0f1117").pack(pady=(18, 2))
        tk.Label(self, text="Keeps itself connected — just leave it running.",
                 font=("Segoe UI", 9), fg="#9aa0a6", bg="#0f1117").pack(pady=(0, 10))

        tk.Label(self, text="Server link", font=("Segoe UI", 9), fg="#9aa0a6", bg="#0f1117").pack()
        self.server_var = tk.StringVar(value=settings.viewer_link(settings.get_server_url()))
        self.server_entry = tk.Entry(self, textvariable=self.server_var, font=("Segoe UI", 10),
                                     width=42, justify="center", relief="flat")
        self.server_entry.pack(pady=(2, 12), ipady=4)

        self.status_var = tk.StringVar(value="Not started")
        tk.Label(self, textvariable=self.status_var, font=("Segoe UI", 10), fg="#e6e6e6",
                 bg="#0f1117", wraplength=420).pack(pady=(0, 10))

        self.start_btn = tk.Button(self, text="▶  Start Sharing", font=("Segoe UI", 11, "bold"),
                                   bg="#4fd1c5", fg="#0f1117", relief="flat", padx=16, pady=8,
                                   command=self.on_start)
        self.start_btn.pack(pady=4)
        self.stop_btn = tk.Button(self, text="■  Stop", font=("Segoe UI", 10), bg="#f56565",
                                  fg="white", relief="flat", padx=16, pady=6,
                                  command=self.on_stop, state="disabled")
        self.stop_btn.pack(pady=4)

        self.pin_frame = tk.Frame(self, bg="#1a1d27")
        tk.Label(self.pin_frame, text="PIN", font=("Segoe UI", 9), fg="#9aa0a6",
                 bg="#1a1d27").pack(pady=(10, 0))
        self.pin_var = tk.StringVar(value="------")
        tk.Label(self.pin_frame, textvariable=self.pin_var, font=("Consolas", 28, "bold"),
                 fg="#4fd1c5", bg="#1a1d27").pack(pady=(0, 8))
        tk.Label(self.pin_frame, text="Open the server link on the OTHER laptop and enter this PIN.",
                 font=("Segoe UI", 9), fg="#e6e6e6", bg="#1a1d27", wraplength=380).pack(pady=(0, 6))
        tk.Button(self.pin_frame, text="📋 Copy link", font=("Segoe UI", 9, "bold"),
                  command=self.copy_link, bg="#2d3142", fg="white", relief="flat",
                  padx=8).pack(pady=(0, 12))
        self.pin_frame.pack(fill="x", padx=30, pady=10)

    def current_url(self):
        return settings.normalize(self.server_var.get())

    def on_start(self):
        url = self.current_url()
        if settings.is_placeholder(url):
            messagebox.showwarning("Server link missing", "Paste your Render link in the 'Server link' box first.")
            return
        settings.save_server_url(url)
        self.running = True
        self.server_entry.config(state="disabled")
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Connecting to server (first time can take up to a minute)...")
        start_agent_thread(url, self.on_pin_ready, self.on_status, self.on_error, self.on_stopped)

    def on_pin_ready(self, code):
        self.after(0, lambda: self.pin_var.set(code))

    def on_status(self, text):
        self.after(0, lambda: self.status_var.set(text))

    def on_error(self, msg):
        self.after(0, lambda: messagebox.showerror("Agent error", msg))

    def on_stopped(self):
        def ui():
            self.running = False
            self.status_var.set("Stopped")
            self.pin_var.set("------")
            self.server_entry.config(state="normal")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
        self.after(0, ui)

    def on_stop(self):
        self.status_var.set("Stopping...")
        stop_agent()

    def on_close(self):
        stop_agent()
        self.after(300, self.destroy)

    def copy_link(self):
        self.clipboard_clear()
        self.clipboard_append(settings.viewer_link(self.current_url()))


if __name__ == "__main__":
    App().mainloop()
