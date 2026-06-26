#!/usr/bin/env bash
#
# sysmon one-line installer.
#
#   curl -fsSL https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/install.sh | bash
#
# Non-interactive (no prompts) via env vars:
#   SYSMON_TOPIC=sysmon-ab12 SYSMON_LANG=hu \
#     curl -fsSL .../install.sh | bash
#
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main"
DEST="/opt/sysmon"
SVC="/etc/systemd/system/sysmon.service"

c_g=$'\033[32m'; c_y=$'\033[33m'; c_d=$'\033[2m'; c_b=$'\033[1m'; c_0=$'\033[0m'
say(){ printf '%s\n' "$*"; }
ok(){  printf '%s[ok]%s %s\n' "$c_g" "$c_0" "$*"; }
ask(){ printf '%s%s%s ' "$c_y" "$*" "$c_0"; }

# stdin may be the pipe (curl|bash) -> read prompts from the terminal
TTY=/dev/tty
have_tty(){ [ -e "$TTY" ] && exec 3<"$TTY"; }

say ""
say "${c_b}sysmon installer${c_0}"
say "${c_d}-----------------------------------------------------------${c_0}"
say "This will:"
say "  - check/install python3   (stdlib only, no pip packages)"
say "  - download sysmon.py into ${c_b}${DEST}${c_0}"
say "  - create a systemd service (auto-start on boot, auto-restart)"
say "  - start it immediately"
say "${c_d}-----------------------------------------------------------${c_0}"
say ""

# --- root ---
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO=sudo
  else
    say "${c_y}Needs root. Re-run as root or install sudo.${c_0}"; exit 1
  fi
else
  SUDO=""
fi

# --- topic ---
TOPIC="${SYSMON_TOPIC:-}"
if [ -z "$TOPIC" ]; then
  GEN="sysmon-$(python3 -c 'import secrets;print(secrets.token_hex(8))' 2>/dev/null \
        || head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  if have_tty; then
    say "Pick an ntfy topic. The topic name ${c_b}is the password${c_0} - keep it private."
    ask "Topic [Enter = ${GEN}]:"; read -r TOPIC <&3 || true
  fi
  TOPIC="${TOPIC:-$GEN}"
fi
ok "topic: ${TOPIC}"

# --- language ---
LANG_SEL="${SYSMON_LANG:-}"
if [ -z "$LANG_SEL" ]; then
  if have_tty; then
    ask "Response language en/hu [en]:"; read -r LANG_SEL <&3 || true
  fi
  LANG_SEL="${LANG_SEL:-en}"
fi
case "$LANG_SEL" in hu|HU) LANG_SEL=hu;; *) LANG_SEL=en;; esac
ok "language: ${LANG_SEL}  (commands stay English)"

# --- server (env only, default ntfy.sh) ---
SERVER="${SYSMON_SERVER:-https://ntfy.sh}"
ok "server: ${SERVER}"

# --- watchdog interval (env only, seconds; 0 disables proactive alerts) ---
INTERVAL_SEL="${SYSMON_INTERVAL:-300}"
ok "watchdog: every ${INTERVAL_SEL}s"

# --- python3 ---
if ! command -v python3 >/dev/null 2>&1; then
  say "Installing python3..."
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y python3
  else
    say "${c_y}No apt-get; install python3 manually and re-run.${c_0}"; exit 1
  fi
fi
ok "python3: $(python3 --version 2>&1)"

# --- fetch script ---
$SUDO mkdir -p "$DEST"
if [ -f "$(dirname "$0")/sysmon.py" ] 2>/dev/null; then
  $SUDO cp "$(dirname "$0")/sysmon.py" "$DEST/sysmon.py"      # local run
else
  $SUDO curl -fsSL "$REPO_RAW/sysmon.py" -o "$DEST/sysmon.py" # piped run
fi
$SUDO chmod +x "$DEST/sysmon.py"
ok "installed: ${DEST}/sysmon.py"

# --- run user ---
RUN_USER="${SUDO_USER:-$(id -un)}"

# let the service user own its script dir so the 'update' command can self-overwrite
$SUDO chown -R "$RUN_USER" "$DEST" 2>/dev/null || true

# allow vcgencmd temperature reads (Raspberry Pi: /dev/vcio is group 'video')
if getent group video >/dev/null 2>&1; then
  $SUDO usermod -aG video "$RUN_USER" 2>/dev/null && ok "temp access: ${RUN_USER} added to 'video' group" || true
fi

# --- systemd service ---
$SUDO tee "$SVC" >/dev/null <<UNIT
[Unit]
Description=sysmon ntfy system monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Environment=SYSMON_TOPIC=${TOPIC}
Environment=SYSMON_SERVER=${SERVER}
Environment=SYSMON_LANG=${LANG_SEL}
Environment=SYSMON_INTERVAL=${INTERVAL_SEL}
ExecStart=/usr/bin/python3 ${DEST}/sysmon.py daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now sysmon.service
ok "service running"

say ""
say "${c_b}Done.${c_0}"
say "  Subscribe on your phone (ntfy app): topic ${c_b}${TOPIC}${c_0}, server ${SERVER}"
say "  Send a command from anywhere:"
say "    ${c_g}curl -d status ${SERVER}/${TOPIC}${c_0}"
say ""
say "  Logs:    journalctl -u sysmon -f"
say "  Stop:    ${SUDO:+sudo }systemctl stop sysmon"
say "  Remove:  ${SUDO:+sudo }systemctl disable --now sysmon && ${SUDO:+sudo }rm ${SVC} ${DEST}/sysmon.py"
say ""
