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

# wizard_pick "<label>" item1 item2 ...
#   lists the items (prompts to stderr), reads a selection from the tty,
#   echoes the chosen names as a comma-separated list on stdout.
#   accepts numbers, names, '*' = all, Enter = none.
wizard_pick(){
  local label="$1"; shift
  local -a items=("$@")
  local n=${#items[@]} i=1 it tok sel out="" ans=""
  if [ "$n" -eq 0 ]; then say "  (no ${label}s detected)" >&2; return 0; fi
  say "Detected ${label}s:" >&2
  for it in "${items[@]}"; do printf '  %2d) %s\n' "$i" "$it" >&2; i=$((i+1)); done
  printf '%sPick %ss to monitor — numbers/names, * = all, Enter = none: %s' \
    "$c_y" "$label" "$c_0" >&2
  read -r ans <&3 || true
  [ -z "$ans" ] && return 0
  if [ "$ans" = "*" ]; then (IFS=,; echo "${items[*]}"); return 0; fi
  for tok in ${ans//,/ }; do
    case "$tok" in
      ''|*[!0-9]*) sel="$tok" ;;                 # has a non-digit -> treat as a name
      *)           sel="${items[$((tok-1))]:-}" ;;  # pure number -> index into the list
    esac
    [ -n "$sel" ] && out="${out:+$out,}$sel"
  done
  echo "$out"
}

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

# --- existing install? update (keep settings) or clean ---
unit_get(){ $SUDO sed -n "s/^Environment=$1=//p" "$SVC" 2>/dev/null | head -n1; }
MODE=clean
EXIST_TOPIC=""; EXIST_SERVER=""; EXIST_LANG=""; EXIST_INTERVAL=""
if [ -f "$SVC" ]; then
  EXIST_TOPIC="$(unit_get SYSMON_TOPIC)"
  EXIST_SERVER="$(unit_get SYSMON_SERVER)"
  EXIST_LANG="$(unit_get SYSMON_LANG)"
  EXIST_INTERVAL="$(unit_get SYSMON_INTERVAL)"
  say "${c_y}Existing install found${c_0} (${SVC})."
  say "  topic: ${EXIST_TOPIC:-?}  lang: ${EXIST_LANG:-?}  server: ${EXIST_SERVER:-?}"
  ANS=""
  if have_tty; then
    ask "[U]pdate (keep settings) or [c]lean install? [U]:"; read -r ANS <&3 || true
  fi
  case "${ANS:-u}" in c|C|clean) MODE=clean;; *) MODE=update;; esac
  ok "mode: ${MODE}"
fi

# --- topic ---
TOPIC="${SYSMON_TOPIC:-}"
[ -z "$TOPIC" ] && [ "$MODE" = update ] && TOPIC="$EXIST_TOPIC"
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
[ -z "$LANG_SEL" ] && [ "$MODE" = update ] && LANG_SEL="$EXIST_LANG"
if [ -z "$LANG_SEL" ]; then
  if have_tty; then
    ask "Response language en/hu [en]:"; read -r LANG_SEL <&3 || true
  fi
  LANG_SEL="${LANG_SEL:-en}"
fi
case "$LANG_SEL" in hu|HU) LANG_SEL=hu;; *) LANG_SEL=en;; esac
ok "language: ${LANG_SEL}  (commands stay English)"

# --- server (env, else keep existing on update, else ntfy.sh) ---
SERVER="${SYSMON_SERVER:-}"
[ -z "$SERVER" ] && [ "$MODE" = update ] && SERVER="$EXIST_SERVER"
SERVER="${SERVER:-https://ntfy.sh}"
ok "server: ${SERVER}"

# --- watchdog interval (env, else keep existing on update, else 300) ---
INTERVAL_SEL="${SYSMON_INTERVAL:-}"
[ -z "$INTERVAL_SEL" ] && [ "$MODE" = update ] && INTERVAL_SEL="$EXIST_INTERVAL"
INTERVAL_SEL="${INTERVAL_SEL:-300}"
ok "watchdog: every ${INTERVAL_SEL}s"

# --- version-check interval (env, else keep existing on update, else 86400) ---
UPDATE_CHECK_SEL="${SYSMON_UPDATE_CHECK:-}"
[ -z "$UPDATE_CHECK_SEL" ] && [ "$MODE" = update ] && UPDATE_CHECK_SEL="$(unit_get SYSMON_UPDATE_CHECK)"
UPDATE_CHECK_SEL="${UPDATE_CHECK_SEL:-86400}"
ok "version-check: every ${UPDATE_CHECK_SEL}s"

# --- extra-checks wizard (services / docker containers to include in `status`) ---
CHECK_SERVICES="${SYSMON_CHECK_SERVICES:-}"
CHECK_DOCKER="${SYSMON_CHECK_DOCKER:-}"
if [ "$MODE" = update ]; then
  [ -z "$CHECK_SERVICES" ] && CHECK_SERVICES="$(unit_get SYSMON_CHECK_SERVICES)"
  [ -z "$CHECK_DOCKER" ]   && CHECK_DOCKER="$(unit_get SYSMON_CHECK_DOCKER)"
fi
# only run the wizard interactively and when nothing was preset (env/update)
if [ -z "${CHECK_SERVICES}${CHECK_DOCKER}" ] && have_tty; then
  say ""
  say "${c_b}Extra checks${c_0} — pick services/containers to add to the ${c_b}status${c_0} report"
  say "${c_d}(a stopped one raises the alert priority). Skip both with Enter.${c_0}"
  DOCKER_CANDS=()
  if command -v docker >/dev/null 2>&1; then
    mapfile -t DOCKER_CANDS < <($SUDO docker ps --format '{{.Names}}' 2>/dev/null || true)
  fi
  CHECK_DOCKER="$(wizard_pick "docker container" "${DOCKER_CANDS[@]}")"
  mapfile -t SVC_CANDS < <(systemctl list-units --type=service --state=running \
                            --no-legend --plain 2>/dev/null | awk '{print $1}' | sed 's/\.service$//')
  CHECK_SERVICES="$(wizard_pick "service" "${SVC_CANDS[@]}")"
fi
[ -n "$CHECK_DOCKER" ]   && ok "containers: ${CHECK_DOCKER}"
[ -n "$CHECK_SERVICES" ] && ok "services: ${CHECK_SERVICES}"

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

# convenience wrapper: `sudo sysmon configure` / `sudo sysmon status` ...
$SUDO tee /usr/local/bin/sysmon >/dev/null <<WRAP
#!/bin/sh
exec /usr/bin/python3 ${DEST}/sysmon.py "\$@"
WRAP
$SUDO chmod +x /usr/local/bin/sysmon
ok "wrapper: sysmon  (try: sudo sysmon configure)"

# --- run user ---
RUN_USER="${SUDO_USER:-$(id -un)}"
# note: $DEST stays root-owned on purpose — the service user cannot overwrite
# its own code. Updating is done by re-running this installer (update mode).

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
Environment=SYSMON_UPDATE_CHECK=${UPDATE_CHECK_SEL}
Environment=SYSMON_CHECK_SERVICES=${CHECK_SERVICES}
Environment=SYSMON_CHECK_DOCKER=${CHECK_DOCKER}
ExecStart=/usr/bin/python3 ${DEST}/sysmon.py daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable sysmon.service >/dev/null 2>&1 || true
$SUDO systemctl restart sysmon.service          # restart so update reloads new code
ok "service running (${MODE})"

say ""
say "${c_b}Done.${c_0}"
say "  Subscribe on your phone (ntfy app): topic ${c_b}${TOPIC}${c_0}, server ${SERVER}"
say "  Send a command from anywhere:"
say "    ${c_g}curl -d status ${SERVER}/${TOPIC}${c_0}"
say ""
say "  Edit checks: ${SUDO:+sudo }sysmon configure"
say "  Logs:    journalctl -u sysmon -f"
say "  Stop:    ${SUDO:+sudo }systemctl stop sysmon"
say "  Remove:  ${SUDO:+sudo }systemctl disable --now sysmon && ${SUDO:+sudo }rm ${SVC} ${DEST}/sysmon.py /usr/local/bin/sysmon"
say ""
