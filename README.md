# Miner Rig Control (Fedora / Linux)

GUI control panel for GPU mining (Rigel) and/or CPU mining (XMRig) via
unMineable, with a slider to adjust GPU power limit on the fly.

## First-time setup on a fresh Fedora install

1. Clone or download this repo.
2. Make the installer executable and run it:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```
3. Choose GPU only / CPU only / Both when prompted.
4. Enter your BTC payout address and a worker name for this machine.
5. **If the script installed NVIDIA drivers for the first time, reboot now**
   (`sudo reboot`) before trying to run the GPU miner - the kernel module
   needs a reboot to load.
6. Run the control panel:
   ```bash
   python3 miner_control.py
   ```

## GPU vendor support

`install.sh` auto-detects your GPU via `lspci`:

- **NVIDIA** → installs drivers via RPM Fusion + `akmod-nvidia`, downloads
  **Rigel**. GPU power limit is adjustable live via the slider in
  `miner_control.py`.
- **AMD** → no separate proprietary driver needed (the `amdgpu` kernel
  driver ships with the Linux kernel already); installs Mesa's OpenCL
  runtime, downloads **TeamRedMiner**. Defaults to mining RVN via kawpow
  (TeamRedMiner doesn't support XelisHash). There's no power slider on
  this path yet — TeamRedMiner doesn't expose one universal power-limit
  flag across AMD GPU generations the way Rigel does for NVIDIA. Simple
  start/stop is provided instead. Fine-grained AMD power tuning is
  normally done separately via `rocm-smi` or `corectrl` — ask if you want
  that wired into the controller too.
- If detection is inconclusive, the installer asks you to pick manually.

Since detection happens per-machine, the same repo works whether a given
machine has an NVIDIA desktop card or an AMD laptop GPU — each just gets
its own `config.json` with the right vendor and binary.

## What install.sh does

- Installs base dependencies: `curl`, `tar`, `python3-tkinter`, `xterm`, `jq`.
- If GPU mining was selected and no NVIDIA driver is detected
  (`nvidia-smi` missing), it enables RPM Fusion (free + nonfree) and
  installs `akmod-nvidia` + CUDA support, plus the kernel headers needed
  to build the driver module. **This step requires a reboot afterward.**
- If NVIDIA drivers are already installed, it skips straight past that.
- Downloads the latest Linux release of Rigel and/or XMRig directly from
  their official GitHub releases (always current, nothing bundled/stale).
- Creates `config.json` from `config.example.json` with your wallet and
  worker name filled in.

Safe to re-run - it skips anything already done (drivers, repo setup, etc.)
and won't overwrite an existing `config.json`.

## Re-running on other Fedora machines

Same repo, same steps on each machine - clone it, run `install.sh`, pick
what that particular machine should mine with. Each machine gets its own
`config.json` and downloaded binaries in `bin/`, so nothing conflicts
between machines even on the same repo.

## Notes on sudo prompts

`--pl` (GPU power limit) and XMRig's huge-pages/priority settings both need
root. Rather than running the whole GUI as root (which causes X11 display
permission issues on Linux), only the miner processes themselves are
launched with `sudo` - inside their own terminal windows. You'll be asked
for your password there each time a miner starts or restarts (e.g. every
time you move the GPU slider). If that gets old, you can add a scoped
`NOPASSWD` sudoers rule just for the rigel/xmrig binaries - ask if you want
help setting that up safely.

## Notes

- `config.json` and `bin/` are git-ignored (see `.gitignore`) so your wallet
  address and large binaries never get committed.
- To update to a newer Rigel/XMRig version, delete `bin/rigel` or
  `bin/xmrig` and re-run `install.sh`.
- Miner output shows up in its own terminal window, not inside the control
  panel - the panel just starts/stops/restarts them. The window stays open
  after a crash so you can read the error instead of it vanishing.
