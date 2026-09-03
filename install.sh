#!/usr/bin/env bash
#
# Miner Rig Installer - Fedora
# -----------------------------
# Treats this like a fresh install: sets up NVIDIA drivers (via RPM Fusion),
# installs required packages, downloads the latest Rigel and/or XMRig
# release from GitHub, and walks you through config.json.
#
# USAGE:
#   chmod +x install.sh
#   ./install.sh
#
# Safe to re-run - it skips steps that are already done.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()  { echo -e "${CYAN}==>${NC} $1"; }
warn() { echo -e "${YELLOW}WARNING:${NC} $1"; }
err()  { echo -e "${RED}ERROR:${NC} $1"; }

REBOOT_NEEDED=0

# ---------------------------------------------------------------------
# 0. Sanity checks
# ---------------------------------------------------------------------
if [[ ! -f /etc/fedora-release ]]; then
    warn "This script is written for Fedora specifically (uses dnf + RPM Fusion)."
    warn "It may not work correctly on other distros. Continuing anyway..."
fi

if [[ $EUID -eq 0 ]]; then
    err "Don't run this whole script as root/sudo - it calls sudo itself where needed."
    exit 1
fi

echo ""
log "=== Miner Rig Installer (Fedora) ==="
echo ""

# ---------------------------------------------------------------------
# 1. Ask what to install
# ---------------------------------------------------------------------
echo "What would you like to install on this machine?"
echo "  1) GPU only  (Rigel + NVIDIA drivers)"
echo "  2) CPU only  (XMRig)"
echo "  3) Both"
read -rp "Enter 1, 2, or 3: " CHOICE

INSTALL_GPU=false
INSTALL_CPU=false
case "$CHOICE" in
    1) INSTALL_GPU=true ;;
    2) INSTALL_CPU=true ;;
    3) INSTALL_GPU=true; INSTALL_CPU=true ;;
    *) err "Invalid choice."; exit 1 ;;
esac

# ---------------------------------------------------------------------
# 2. Base dependencies (needed regardless of GPU/CPU choice)
# ---------------------------------------------------------------------
log "Installing base dependencies (curl, tar, python3-tkinter, a terminal emulator)..."
sudo dnf install -y curl tar python3-tkinter xterm jq

# ---------------------------------------------------------------------
# 3. NVIDIA drivers (only if GPU mining was selected)
# ---------------------------------------------------------------------
if $INSTALL_GPU; then
    if command -v nvidia-smi &>/dev/null; then
        log "nvidia-smi already found - NVIDIA drivers appear to be installed. Skipping driver setup."
    else
        log "No NVIDIA drivers detected. Setting up RPM Fusion + akmod-nvidia..."

        FEDORA_VERSION="$(rpm -E %fedora)"

        # Enable RPM Fusion free + nonfree if not already enabled
        if ! dnf repolist | grep -qi "rpmfusion-nonfree"; then
            log "Enabling RPM Fusion repositories..."
            sudo dnf install -y \
                "https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-${FEDORA_VERSION}.noarch.rpm" \
                "https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${FEDORA_VERSION}.noarch.rpm"
        else
            log "RPM Fusion already enabled."
        fi

        log "Installing kernel headers/devel (required to build the NVIDIA kernel module)..."
        sudo dnf install -y kernel-devel-"$(uname -r)" kernel-headers gcc make dkms acpid libglvnd-glx libglvnd-opengl libglvnd-devel pkgconfig || \
            warn "Some kernel-devel packages may not have matched exactly - if the NVIDIA module fails to build later, check 'sudo dnf install kernel-devel' manually."

        log "Installing akmod-nvidia and CUDA support (this can take a while)..."
        sudo dnf install -y akmod-nvidia xorg-x11-drv-nvidia-cuda

        log "Waiting for the NVIDIA kernel module to build via akmods (can take a few minutes)..."
        sudo akmods --force || true
        sleep 5

        REBOOT_NEEDED=1
        warn "NVIDIA driver installed but requires a REBOOT before it's active."
        warn "Re-run this script after rebooting to continue GPU miner setup, or continue now"
        warn "and just make sure to reboot before actually running the GPU miner."
    fi
fi

# ---------------------------------------------------------------------
# 4. Helper: download latest GitHub release asset matching a pattern
# ---------------------------------------------------------------------
download_latest_release() {
    local name="$1" owner="$2" repo="$3" pattern="$4" dest_dir="$5"

    log "Looking up latest $name release..."
    local api_url="https://api.github.com/repos/${owner}/${repo}/releases/latest"
    local asset_url
    asset_url=$(curl -s "$api_url" | jq -r --arg pat "$pattern" \
        '.assets[] | select(.name | test($pat; "i")) | .browser_download_url' | head -n1)

    if [[ -z "$asset_url" || "$asset_url" == "null" ]]; then
        err "Could not find a matching Linux release asset for $name."
        err "Check https://github.com/${owner}/${repo}/releases manually and place the binary in $dest_dir"
        return 1
    fi

    local filename
    filename=$(basename "$asset_url")
    log "Found $name: $filename"

    mkdir -p "$dest_dir"
    log "Downloading..."
    curl -L -o "/tmp/$filename" "$asset_url"

    log "Extracting to $dest_dir..."
    case "$filename" in
        *.tar.gz|*.tgz) tar -xzf "/tmp/$filename" -C "$dest_dir" --strip-components=1 ;;
        *.tar.xz)       tar -xJf "/tmp/$filename" -C "$dest_dir" --strip-components=1 ;;
        *.zip)          command -v unzip &>/dev/null || sudo dnf install -y unzip
                        unzip -o "/tmp/$filename" -d "$dest_dir" ;;
        *) err "Unknown archive format for $filename - extract it manually into $dest_dir"; return 1 ;;
    esac
    rm -f "/tmp/$filename"

    # make sure the binary itself is executable, whatever it's actually named
    find "$dest_dir" -maxdepth 1 -type f -iname "${6:-$name}*" -exec chmod +x {} \; 2>/dev/null || true

    log "$name installed to $dest_dir"
}

# ---------------------------------------------------------------------
# 5. Download miners
# ---------------------------------------------------------------------
if $INSTALL_GPU; then
    download_latest_release "Rigel" "rigelminer" "rigel" "linux.*\\.tar\\.gz$" "$BIN_DIR/rigel" "rigel" || true
fi

if $INSTALL_CPU; then
    download_latest_release "XMRig" "xmrig" "xmrig" "linux-static-x64\\.tar\\.gz$" "$BIN_DIR/xmrig" "xmrig" || true
fi

# ---------------------------------------------------------------------
# 6. config.json setup
# ---------------------------------------------------------------------
CONFIG_PATH="$SCRIPT_DIR/config.json"

if [[ ! -f "$CONFIG_PATH" ]]; then
    echo ""
    log "=== Wallet Setup ==="
    read -rp "Enter your BTC payout address (used for both GPU and CPU mining): " BTC_ADDRESS
    read -rp "Enter a worker/rig name for this machine (e.g. FedoraDesktop): " WORKER_NAME

    python3 - "$SCRIPT_DIR/config.example.json" "$CONFIG_PATH" "$BTC_ADDRESS" "$WORKER_NAME" "$INSTALL_GPU" "$INSTALL_CPU" <<'PYEOF'
import json, sys

example_path, out_path, btc_address, worker_name, install_gpu, install_cpu = sys.argv[1:7]

with open(example_path) as f:
    config = json.load(f)

config["worker_name"] = worker_name
config["gpu"]["wallet"] = f"XEL:{btc_address}"
config["gpu"]["enabled"] = (install_gpu == "true")
config["cpu"]["wallet"] = f"BTC:{btc_address}"
config["cpu"]["enabled"] = (install_cpu == "true")

with open(out_path, "w") as f:
    json.dump(config, f, indent=2)

print(f"Wrote {out_path}")
PYEOF
else
    warn "config.json already exists - leaving it as-is. Edit it by hand if needed."
fi

# ---------------------------------------------------------------------
# 7. Done
# ---------------------------------------------------------------------
echo ""
log "=== Install complete ==="
echo "Run the control panel with:"
echo "    python3 miner_control.py"
echo ""

if [[ $REBOOT_NEEDED -eq 1 ]]; then
    warn "REBOOT REQUIRED before the GPU miner will work (fresh NVIDIA driver install)."
    warn "Run: sudo reboot"
fi
