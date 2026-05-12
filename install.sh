#!/bin/bash
# ──────────────────────────────────────────────────────────────
#  3x-ui Monitor — Installer
#
#  Basic:
#    curl -fsSL https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main/install.sh | sudo bash
#
#  With SOCKS5 proxy:
#    curl -fsSL ... | sudo bash -s -- --proxy socks5://1.2.3.4:1080
#    curl -fsSL ... | sudo bash -s -- --proxy socks5://user:pass@1.2.3.4:1080
#
#  Or set env var before piping:
#    export PROXY=socks5://1.2.3.4:1080
#    curl -fsSL ... | sudo bash
# ──────────────────────────────────────────────────────────────
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/phoseinq/3x-ui-monitor/main"
DIR="/opt/boyitor"

# ── colours ───────────────────────────────────────────────────
R=$'\033[0;31m'; G=$'\033[0;32m'; B=$'\033[0;34m'
Y=$'\033[1;33m'; C=$'\033[0;36m'; N=$'\033[0m'; BLD=$'\033[1m'

ok()   { echo -e "  ${G}+${N}  $*"; }
info() { echo -e "  ${C}>${N}  $*"; }
err()  { echo -e "  ${R}!${N}  $*"; exit 1; }
line() { echo -e "${B}────────────────────────────────────────────${N}"; }

# ── parse args ────────────────────────────────────────────────
PROXY="${PROXY:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --proxy|-p) PROXY="$2"; shift 2 ;;
    *) err "Unknown option: $1  (use --proxy socks5://host:port)" ;;
  esac
done

# ── proxy setup ───────────────────────────────────────────────
CURL_PROXY=()
if [[ -n "$PROXY" ]]; then
  CURL_PROXY=(--proxy "$PROXY")
  export ALL_PROXY="$PROXY"
  export HTTPS_PROXY="$PROXY"
  export HTTP_PROXY="$PROXY"
fi

# ── header ────────────────────────────────────────────────────
echo
line
echo -e "${B}${BLD}      3x-ui Monitor Dashboard Installer     ${N}"
line
echo

# ── pre-checks ────────────────────────────────────────────────
[[ "$EUID" -ne 0 ]]              && err "Run as root:  sudo bash install.sh"
command -v python3 &>/dev/null   || err "python3 not found — install it first:  apt install python3"
command -v curl   &>/dev/null    || err "curl not found — install it first:  apt install curl"

[[ -n "$PROXY" ]] && info "Proxy: $PROXY"

# ── 1. Python packages ────────────────────────────────────────
echo -e "${Y}[1/4] Installing Python packages...${N}"

_pip() { pip3 install "$@" -q 2>/dev/null; }

# pysocks lets pip route through SOCKS5 via ALL_PROXY
if [[ "$PROXY" == socks* ]]; then
  info "Installing PySocks for SOCKS5 support..."
  apt-get install -y -qq python3-socks 2>/dev/null \
    || pip3 install pysocks -q 2>/dev/null \
    || true
fi

if ! _pip flask requests; then
  info "pip3 failed — installing via apt..."
  apt-get update -qq
  apt-get install -y -qq python3-pip
  _pip flask requests || err "Failed to install Python packages"
fi

_pip tzdata 2>/dev/null || true   # ZoneInfo timezone data for older systems

ok "Python packages ready"

# ── 2. Download files ─────────────────────────────────────────
echo
echo -e "${Y}[2/4] Downloading files to ${DIR}...${N}"
mkdir -p "$DIR"

_dl() {
  info "Downloading $1..."
  curl -fsSL "${CURL_PROXY[@]}" "${REPO_RAW}/$1" -o "${DIR}/$1" \
    || err "Download failed: $1"
}

_dl dashboard.py
_dl monitor.py
_dl boy.py
chmod +x "${DIR}/boy.py"
ln -sf "${DIR}/boy.py" /usr/local/bin/boy

ok "Files ready  (boy available system-wide)"

# ── 3. Secret key ─────────────────────────────────────────────
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
sed -i "s/boyitor-2026-change-me/${SECRET}/" "${DIR}/dashboard.py"
ok "Secret key generated"

# ── 4. Systemd services ───────────────────────────────────────
echo
echo -e "${Y}[3/4] Creating systemd services...${N}"

PYTHON=$(command -v python3)

cat > /etc/systemd/system/xui-dashboard.service << UNIT
[Unit]
Description=3x-ui Traffic Dashboard
After=network.target

[Service]
ExecStart=${PYTHON} ${DIR}/dashboard.py
WorkingDirectory=${DIR}
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/boyitor.service << UNIT
[Unit]
Description=3x-ui Traffic Monitor
After=network.target xui-dashboard.service

[Service]
ExecStart=${PYTHON} ${DIR}/monitor.py
WorkingDirectory=${DIR}
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable xui-dashboard boyitor -q
systemctl restart xui-dashboard boyitor
ok "Services started"

# ── 5. Verify ─────────────────────────────────────────────────
echo
echo -e "${Y}[4/4] Verifying...${N}"
sleep 2

if ! systemctl is-active --quiet xui-dashboard; then
  echo -e "  ${R}! xui-dashboard failed to start — last 20 log lines:${N}"
  journalctl -u xui-dashboard -n 20 --no-pager
  exit 1
fi
ok "xui-dashboard is running"

# ── done ──────────────────────────────────────────────────────
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$IP" ]] && IP="YOUR_SERVER_IP"

echo
line
echo -e "${G}${BLD}  Done! Dashboard is up and running.${N}"
line
echo
echo -e "  ${C}URL:${N}       http://${IP}:5000"
echo -e "  ${C}First run:${N} open the URL and register your admin account"
echo -e "  ${C}Settings:${N}  enter your 3x-ui panel URL, username and password"
echo
echo -e "  ${Y}boy CLI:${N}"
echo -e "    boy status"
echo -e "    boy restart"
echo -e "    boy user <username>      change admin username"
echo -e "    boy pass <password>      change admin password"
echo -e "    boy port <number>        change dashboard port"
echo -e "    boy https on --cert /path/cert.pem --key /path/key.pem"
echo -e "    boy remove               uninstall services"
echo
echo -e "  ${Y}Logs:${N}"
echo -e "    journalctl -u xui-dashboard -f"
echo -e "    journalctl -u boyitor   -f"
echo
