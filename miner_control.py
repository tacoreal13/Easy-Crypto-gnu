#!/usr/bin/env python3
"""
Miner Control (Linux/Fedora) - GPU power slider + CPU start/stop
--------------------------------------------------------------------
Reads config.json (created by install.sh) and gives you a GUI to:
  - Drag a slider to change Rigel's GPU power limit (kills + restarts
    Rigel with the new value, opens its own terminal window)
  - Start/stop XMRig independently (separate hardware, no conflict)

Only the miner processes are run with sudo (needed for --pl to take
effect) - the GUI itself stays as your normal user, avoiding X11
display permission issues. You'll get a sudo password prompt inside
each miner's own terminal window when it starts/restarts.

USAGE:
    python3 miner_control.py
"""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
RIGEL_PATH = os.path.join(SCRIPT_DIR, "bin", "rigel", "rigel")
TEAMREDMINER_PATH = os.path.join(SCRIPT_DIR, "bin", "teamredminer", "teamredminer")
XMRIG_PATH = os.path.join(SCRIPT_DIR, "bin", "xmrig", "xmrig")


# ============================= Config =============================

def load_config():
    if not os.path.exists(CONFIG_PATH):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing config.json",
            f"Couldn't find config.json in:\n{SCRIPT_DIR}\n\n"
            "Run install.sh first, or copy config.example.json to config.json "
            "and fill in your wallet address.",
        )
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


# ============================= Terminal / process helpers =============================

def detect_terminal():
    """Return an argv prefix list for launching a command in a visible terminal window."""
    candidates = [
        (["gnome-terminal", "--"], "gnome-terminal"),
        (["konsole", "-e"], "konsole"),
        (["xfce4-terminal", "-e"], "xfce4-terminal"),
        (["terminator", "-x"], "terminator"),
        (["xterm", "-e"], "xterm"),
    ]
    for argv_prefix, binary in candidates:
        if shutil.which(binary):
            return argv_prefix
    return None


def wrap_with_hold(cmd):
    """Wrap a command so the terminal stays open and shows the exit code after
    the process ends - lets you actually read crash errors instead of the
    window vanishing instantly.
    """
    quoted = shlex.join(cmd)
    shell_script = (
        f"{quoted}; "
        f'ec=$?; echo; echo "--- process exited with code $ec ---"; '
        f'read -p "Press Enter to close this window..."'
    )
    return ["bash", "-c", shell_script]


def kill_process_group(proc):
    if proc is None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


# ============================= Main App =============================

class MinerController:
    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.root.title("Miner Control (Fedora)")
        self.root.geometry("560x600")

        self.gpu_process = None
        self.cpu_process = None
        self.restart_lock = threading.Lock()

        gpu_cfg = config.get("gpu", {})
        self.gpu_vendor = gpu_cfg.get("vendor", "auto")
        gpu_binary = TEAMREDMINER_PATH if self.gpu_vendor == "amd" else RIGEL_PATH
        self.gpu_enabled = gpu_cfg.get("enabled", False) and os.path.exists(gpu_binary)
        cpu_cfg = config.get("cpu", {})
        self.cpu_enabled = cpu_cfg.get("enabled", False) and os.path.exists(XMRIG_PATH)

        self._build_ui()

        if self.gpu_enabled:
            initial_pl = gpu_cfg.get("default_power_limit", 100) if self.gpu_vendor != "amd" else None
            self.start_gpu(initial_pl)

    # ---------------- UI ----------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        if not self.gpu_enabled and not self.cpu_enabled:
            ttk.Label(
                self.root,
                text="Neither GPU nor CPU mining is enabled/installed.\n"
                     "Check config.json and make sure bin/rigel/rigel "
                     "or bin/xmrig/xmrig exists (run install.sh).",
                foreground="red",
                justify="left",
            ).pack(**pad)

        if self.gpu_enabled:
            gpu_cfg = self.config["gpu"]
            gpu_label = "GPU Mining (TeamRedMiner)" if self.gpu_vendor == "amd" else "GPU Mining (Rigel)"
            ttk.Label(self.root, text=gpu_label, font=("Sans", 14, "bold")).pack(**pad)

            if self.gpu_vendor == "amd":
                # TeamRedMiner doesn't have a single verified cross-GPU power-limit flag
                # like Rigel's --pl, so this is simple start/stop rather than a slider.
                # Fine-grained power tuning on AMD is usually done separately via
                # rocm-smi or corectrl - ask if you want that wired in too.
                ttk.Label(
                    self.root,
                    text="(Power tuning for AMD not wired in yet - see README)",
                    foreground="gray",
                ).pack(**pad)
                self.gpu_status_label = ttk.Label(self.root, text="Starting TeamRedMiner...", foreground="blue")
                self.gpu_status_label.pack(**pad)

                gpu_btn_frame = ttk.Frame(self.root)
                gpu_btn_frame.pack(**pad)
                ttk.Button(gpu_btn_frame, text="Stop GPU Miner", command=self.stop_gpu).pack(side="left", padx=5)
                ttk.Button(gpu_btn_frame, text="Restart", command=lambda: self.restart_gpu(None)).pack(side="left", padx=5)
            else:
                self.gpu_value_label = ttk.Label(self.root, text=f"{gpu_cfg.get('default_power_limit', 100)} W", font=("Sans", 20))
                self.gpu_value_label.pack(**pad)

                self.gpu_slider = ttk.Scale(
                    self.root,
                    from_=gpu_cfg.get("min_power_limit", 60),
                    to=gpu_cfg.get("max_power_limit", 130),
                    orient="horizontal",
                    command=self._on_slide,
                    length=440,
                )
                self.gpu_slider.set(gpu_cfg.get("default_power_limit", 100))
                self.gpu_slider.pack(**pad)
                self.gpu_slider.bind("<ButtonRelease-1>", self._on_release)

                self.gpu_status_label = ttk.Label(self.root, text="Starting Rigel...", foreground="blue")
                self.gpu_status_label.pack(**pad)

                gpu_btn_frame = ttk.Frame(self.root)
                gpu_btn_frame.pack(**pad)
                ttk.Button(gpu_btn_frame, text="Stop GPU Miner", command=self.stop_gpu).pack(side="left", padx=5)
                ttk.Button(gpu_btn_frame, text="Restart Now", command=lambda: self.restart_gpu(int(self.gpu_slider.get()))).pack(side="left", padx=5)

            ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=10, pady=10)

        if self.cpu_enabled:
            ttk.Label(self.root, text="CPU Mining (XMRig)", font=("Sans", 14, "bold")).pack(**pad)
            self.cpu_status_label = ttk.Label(self.root, text="XMRig not running", foreground="gray")
            self.cpu_status_label.pack(**pad)

            cpu_btn_frame = ttk.Frame(self.root)
            cpu_btn_frame.pack(**pad)
            ttk.Button(cpu_btn_frame, text="Start XMRig", command=self.start_cpu).pack(side="left", padx=5)
            ttk.Button(cpu_btn_frame, text="Stop XMRig", command=self.stop_cpu).pack(side="left", padx=5)

            ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=10, pady=10)

        ttk.Label(self.root, text="Controller log (miner output appears in its own terminal window):").pack(**pad)
        self.log = scrolledtext.ScrolledText(self.root, height=10, width=64, state="disabled", bg="black", fg="#00ff88")
        self.log.pack(padx=10, pady=(0, 10))

    def _log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_slide(self, value):
        self.gpu_value_label.config(text=f"{int(float(value))} W")

    def _on_release(self, event):
        self.restart_gpu(int(self.gpu_slider.get()))

    def _launch(self, cmd, title):
        term_prefix = detect_terminal()
        if term_prefix is None:
            self._log_line(
                "[ERROR] No terminal emulator found (tried gnome-terminal, konsole, "
                "xfce4-terminal, terminator, xterm). Install one, e.g.: sudo dnf install xterm"
            )
            return None
        full_cmd = term_prefix + wrap_with_hold(cmd)
        self._log_line(f"$ {' '.join(cmd)}")
        return subprocess.Popen(full_cmd, start_new_session=True)

    # ---------------- GPU (Rigel) ----------------

    def build_gpu_command(self, pl=None):
        gpu_cfg = self.config["gpu"]
        worker = self.config.get("worker_name", "FedoraRig")

        if self.gpu_vendor == "amd":
            cmd = [
                TEAMREDMINER_PATH,
                "-a", gpu_cfg.get("algorithm_amd", "kawpow"),
                "-o", gpu_cfg.get("pool_amd", "stratum+ssl://kp.unmineable.com:4444"),
                "-u", f'{gpu_cfg["wallet_amd"]}.{worker}',
                "-p", gpu_cfg.get("password", "x"),
            ]
        else:
            cmd = [
                RIGEL_PATH,
                "-a", gpu_cfg.get("algorithm_nvidia", "xelishashv3"),
                "-o", gpu_cfg.get("pool_nvidia", "stratum+tcp://xelishash.unmineable.com:3333"),
                "-u", f'{gpu_cfg["wallet_nvidia"]}.{worker}',
                "-p", gpu_cfg.get("password", "x"),
                "--pl", str(pl if pl is not None else gpu_cfg.get("default_power_limit", 100)),
                "--temp-limit", gpu_cfg.get("temp_limit", "tc[75-80]"),
            ]
        return ["sudo"] + cmd

    def start_gpu(self, pl=None):
        miner_name = "TeamRedMiner" if self.gpu_vendor == "amd" else "Rigel"
        self.gpu_process = self._launch(self.build_gpu_command(pl), miner_name)
        if self.gpu_process:
            label = "Running (see TeamRedMiner's window)" if self.gpu_vendor == "amd" else f"Running at {pl} W (see Rigel's window)"
            self.gpu_status_label.config(text=label, foreground="green")

    def stop_gpu(self):
        if self.gpu_process and self.gpu_process.poll() is None:
            self.gpu_status_label.config(text="Stopping...", foreground="orange")
            kill_process_group(self.gpu_process)
            self.gpu_status_label.config(text="Stopped", foreground="red")
            self._log_line("[GPU miner + terminal window closed]")

    def restart_gpu(self, new_pl):
        def work():
            with self.restart_lock:
                label = "Restarting..." if new_pl is None else f"Restarting at {new_pl} W..."
                self.root.after(0, self.gpu_status_label.config, {"text": label, "foreground": "orange"})
                if self.gpu_process and self.gpu_process.poll() is None:
                    kill_process_group(self.gpu_process)
                time.sleep(0.5)
                self.root.after(0, self.start_gpu, new_pl)
        threading.Thread(target=work, daemon=True).start()

    # ---------------- CPU (XMRig) ----------------

    def build_cpu_command(self):
        cpu_cfg = self.config["cpu"]
        cmd = [
            XMRIG_PATH,
            "-o", cpu_cfg["pool"],
            "-a", cpu_cfg.get("algo", "rx/0"),
            "-u", f'{cpu_cfg["wallet"]}.{self.config.get("worker_name", "FedoraRig")}',
            "-p", cpu_cfg.get("password", "x"),
        ]
        if cpu_cfg.get("tls", True):
            cmd.append("--tls")
        return ["sudo"] + cmd

    def start_cpu(self):
        if self.cpu_process and self.cpu_process.poll() is None:
            self._log_line("[XMRig already running]")
            return
        self.cpu_process = self._launch(self.build_cpu_command(), "XMRig")
        if self.cpu_process:
            self.cpu_status_label.config(text="XMRig running (see its own window)", foreground="green")

    def stop_cpu(self):
        if self.cpu_process and self.cpu_process.poll() is None:
            self.cpu_status_label.config(text="Stopping...", foreground="orange")
            kill_process_group(self.cpu_process)
            self.cpu_status_label.config(text="XMRig stopped", foreground="gray")
            self._log_line("[XMRig + terminal window closed]")

    def on_close(self):
        self.stop_gpu()
        self.stop_cpu()
        self.root.destroy()


if __name__ == "__main__":
    cfg = load_config()
    root = tk.Tk()
    app = MinerController(root, cfg)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
