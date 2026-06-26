#!/usr/bin/env python3
"""
sysmon.py - Simple system monitor with ntfy.sh push + command-response.

Publishes a system status to ntfy.sh and, subscribed to the same topic,
answers commands from anyone who knows the topic.

Commands (always English):
    status  -> full system report
    up      -> short "alive" + uptime
    ping    -> pong
    disk    -> disk usage
    mem     -> memory usage
    temp    -> CPU temperature (Raspberry Pi)
    top     -> top 5 processes by CPU
    version -> running script version
    update  -> self-update from the repo, then restart
    docs    -> push links with "Open docs" / "GitHub" buttons
    help    -> command list

Config via environment:
    SYSMON_TOPIC    ntfy topic (required)
    SYSMON_SERVER   ntfy server URL          (default https://ntfy.sh)
    SYSMON_LANG     response language en|hu   (default en)
    SYSMON_INTERVAL watchdog seconds; 0=off   (default 300)

Message priority scales with severity (disk/mem/temp thresholds):
    ok -> default | warn -> high | crit -> urgent

Beyond answering commands, the daemon runs a watchdog: every
SYSMON_INTERVAL seconds it re-checks status and pushes only when the
severity level changes (degrade or recover), so you get alerts unasked.
"""

import os
import sys
import json
import time
import socket
import shutil
import threading
import subprocess
import urllib.request

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
TOPIC  = os.environ.get("SYSMON_TOPIC", "sysmon-CHANGE_ME")
SERVER = os.environ.get("SYSMON_SERVER", "https://ntfy.sh").rstrip("/")
LANG   = os.environ.get("SYSMON_LANG", "en").lower()
if LANG not in ("en", "hu"):
    LANG = "en"
try:
    INTERVAL = int(os.environ.get("SYSMON_INTERVAL", "300"))   # watchdog period; 0 disables
except ValueError:
    INTERVAL = 300

HOSTNAME = socket.gethostname()
SELF_TAG = f"sysmon-{HOSTNAME}"          # loop-prevention: recognise own pushes

PUB_URL = f"{SERVER}/{TOPIC}"
SUB_URL = f"{SERVER}/{TOPIC}/json"

VERSION = "1.3.0"
UPDATE_URL = os.environ.get(
    "SYSMON_UPDATE_URL",
    "https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/sysmon.py")
DOCS_URL = "https://vmynick.github.io/rmt_sysmon_ntfy/"
REPO_URL = "https://github.com/vmynick/rmt_sysmon_ntfy"

COMMANDS = {"status", "up", "ping", "disk", "mem", "temp", "top", "help",
            "version", "update", "docs"}

# severity thresholds (percent for disk/mem, Celsius for temp)
TH = {
    "disk": (80, 92),   # warn, crit
    "mem":  (85, 95),
    "temp": (70, 80),
}
PRIO = {"ok": "default", "warn": "high", "crit": "urgent"}
SEV_ORDER = ("ok", "warn", "crit")

# rate limit + dedup
RATE_MAX, RATE_WINDOW = 8, 60
_sent_times = []
_send_lock = threading.Lock()     # daemon publishes from both the listener and watchdog threads
DEDUP_WINDOW = 4
_last_cmd = {"text": None, "ts": 0.0}

# ----------------------------------------------------------------------------
# i18n  (labels only; commands stay English)
# ----------------------------------------------------------------------------
T = {
    "en": {
        "host": "Host", "up": "Up", "load": "Load", "mem": "Mem",
        "disk": "Disk", "temp": "Temp", "extra": "extra",
        "alive": "{h} is alive. Uptime: {u}",
        "started": "{h} sysmon started.",
        "online": "{h} online",
        "help": "Commands: status, up, ping, disk, mem, temp, top, "
                "version, update, docs, help",
        "top": "Top CPU ({h})",
        "docs": "sysmon docs & links — tap a button below.",
        "na": "n/a", "sent": "sent", "failed": "failed",
        "ver": "{h} sysmon v{v}",
        "up_to_date": "{h} already up to date (v{v}).",
        "upd_avail_title": "{h} update available",
        "upd_avail": "New version v{new} available (running v{cur}). "
                     "Tap 'Update now' or send 'update'.",
        "updating": "{h} updating v{cur} -> v{new}, restarting...",
        "upd_fail": "{h} update failed: {e}",
        "upd_perm": "{h} cannot write {p} (needs root). Re-run install.sh.",
        "degraded": "{h} {sev}",
        "recovered": "{h} recovered",
        "err_topic": "ERROR: set SYSMON_TOPIC (env or top of script).",
        "usage": "Usage: sysmon.py [daemon|status|print]",
    },
    "hu": {
        "host": "Gep", "up": "Fut", "load": "Terheles", "mem": "Memoria",
        "disk": "Lemez", "temp": "Hom.", "extra": "extra",
        "alive": "{h} elek. Uzemido: {u}",
        "started": "{h} sysmon elindult.",
        "online": "{h} online",
        "help": "Parancsok: status, up, ping, disk, mem, temp, top, "
                "version, update, docs, help",
        "top": "Top CPU ({h})",
        "docs": "sysmon dokumentacio es linkek — koppints egy gombra lent.",
        "na": "n/a", "sent": "elkuldve", "failed": "sikertelen",
        "ver": "{h} sysmon v{v}",
        "up_to_date": "{h} mar naprakesz (v{v}).",
        "upd_avail_title": "{h} frissites elerheto",
        "upd_avail": "Uj verzio elerheto: v{new} (jelenleg v{cur}). "
                     "Koppints az 'Update now'-ra, vagy kuldj 'update'-et.",
        "updating": "{h} frissites v{cur} -> v{new}, ujraindul...",
        "upd_fail": "{h} frissites sikertelen: {e}",
        "upd_perm": "{h} nem irhato {p} (root kell). Futtasd ujra az install.sh-t.",
        "degraded": "{h} {sev}",
        "recovered": "{h} helyreallt",
        "err_topic": "HIBA: allitsd be a SYSMON_TOPIC-ot (env vagy a script teteje).",
        "usage": "Hasznalat: sysmon.py [daemon|status|print]",
    },
}
def t(key, **kw):
    return T[LANG][key].format(**kw)

def sev_max(*sevs):
    return max(sevs, key=lambda s: SEV_ORDER.index(s))

# ----------------------------------------------------------------------------
# ntfy action buttons  (tap in the notification -> POSTs a command to the topic)
# ----------------------------------------------------------------------------
def _action(label, command):
    return f"http, {label}, {PUB_URL}, method=POST, body={command}, clear=true"

def _view(label, url):
    return f"view, {label}, {url}"

def status_actions():
    # ntfy allows up to 3 action buttons per message
    return "; ".join(_action(l, c) for l, c in
                     (("Status", "status"), ("Top", "top"), ("Disk", "disk")))

# ----------------------------------------------------------------------------
# collectors
# ----------------------------------------------------------------------------
def get_uptime():
    try:
        with open("/proc/uptime") as f:
            secs = float(f.readline().split()[0])
        d, rem = divmod(int(secs), 86400)
        h, rem = divmod(rem, 3600)
        m, _ = divmod(rem, 60)
        return f"{d}d {h}h {m}m"
    except Exception:
        return t("na")

def get_load():
    try:
        return "%.2f %.2f %.2f" % os.getloadavg()
    except Exception:
        return t("na")

def _sev(pct, kind):
    w, c = TH[kind]
    return "crit" if pct >= c else "warn" if pct >= w else "ok"

def get_mem():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":")
                info[k] = int(v.split()[0])
        total = info["MemTotal"] / 1024
        avail = info.get("MemAvailable", info["MemFree"]) / 1024
        used = total - avail
        pct = used / total * 100 if total else 0
        return f"{used:.0f}/{total:.0f} MB ({pct:.0f}%)", _sev(pct, "mem")
    except Exception:
        return t("na"), "ok"

def get_disk():
    try:
        tot, u, _ = shutil.disk_usage("/")
        pct = u / tot * 100 if tot else 0
        return f"{u // 2**30}/{tot // 2**30} GB ({pct:.0f}%)", _sev(pct, "disk")
    except Exception:
        return t("na"), "ok"

def get_temp():
    val = None
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], timeout=3)
        s = out.decode().strip().replace("temp=", "")
        val = float(s.replace("'C", "").replace("\u00b0C", ""))
        txt = s
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                val = int(f.read()) / 1000
            txt = f"{val:.1f}'C"
        except Exception:
            return t("na"), "ok"
    return txt, _sev(val, "temp")

def get_top():
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "comm,%cpu", "--sort=-%cpu"], timeout=3
        ).decode().splitlines()[1:6]
        return "\n".join(f"  {l.strip()}" for l in out)
    except Exception:
        return t("na")

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return t("na")

# ----------------------------------------------------------------------------
# reusable checks for extra_tasks()  (each returns a (label, value, severity) tuple)
# ----------------------------------------------------------------------------
def check_service(name):
    ok = subprocess.call(["systemctl", "is-active", "--quiet", name]) == 0
    return (name, "up" if ok else "DOWN", "ok" if ok else "crit")

def check_docker(name):
    """Is Docker container <name> running? running=ok, stopped/absent=crit."""
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        return (f"docker:{name}", "running" if out == "true" else "stopped",
                "ok" if out == "true" else "crit")
    except subprocess.CalledProcessError:
        return (f"docker:{name}", "absent", "crit")     # no such container
    except Exception:
        return (f"docker:{name}", t("na"), "ok")          # docker missing/unreachable

# ----------------------------------------------------------------------------
# extra task hook
# ----------------------------------------------------------------------------
def extra_tasks():
    """Return list of (label, value, severity) tuples. severity: ok|warn|crit."""
    results = []
    # results.append(check_service("nginx"))
    # results.append(check_docker("homeassistant"))
    return results

def build_status(full=True):
    mem, sm = get_mem()
    disk, sd = get_disk()
    temp, st = get_temp()
    lines = [
        f"{t('host')}: {HOSTNAME} ({get_ip()})",
        f"{t('up')}:   {get_uptime()}",
        f"{t('load')}: {get_load()}",
        f"{t('mem')}:  {mem}",
        f"{t('disk')}: {disk}",
        f"{t('temp')}: {temp}",
    ]
    sev = sev_max(sm, sd, st)
    if full:
        extras = extra_tasks()
        if extras:
            lines.append(f"--- {t('extra')} ---")
            for k, v, s in extras:
                lines.append(f"{k}: {v}")
                sev = sev_max(sev, s)
    return "\n".join(lines), sev

# ----------------------------------------------------------------------------
# ntfy publish
# ----------------------------------------------------------------------------
def publish(message, title=None, tags=None, severity="ok", actions=None):
    now = time.time()
    global _sent_times
    with _send_lock:
        _sent_times = [x for x in _sent_times if now - x < RATE_WINDOW]
        if len(_sent_times) >= RATE_MAX:
            print(f"[rate-limit] dropped ({RATE_MAX}/{RATE_WINDOW}s)", file=sys.stderr)
            return False
        _sent_times.append(now)

    req = urllib.request.Request(PUB_URL, data=message.encode("utf-8"), method="POST")
    all_tags = SELF_TAG + ("," + tags if tags else "")
    if severity == "crit":
        all_tags += ",rotating_light"
    elif severity == "warn":
        all_tags += ",warning"
    req.add_header("Tags", all_tags)
    req.add_header("Priority", PRIO.get(severity, "default"))
    if title:
        req.add_header("Title", title)
    if actions:
        req.add_header("Actions", actions)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"[publish error] {e}", file=sys.stderr)
        return False

# ----------------------------------------------------------------------------
# self-update  (fetch latest sysmon.py, replace this file, re-exec)
# ----------------------------------------------------------------------------
def _fetch_remote_script():
    req = urllib.request.Request(UPDATE_URL)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")

def _parse_version(text):
    for line in text.splitlines():
        if line.startswith("VERSION"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return None

def _newer(remote, local):
    """True if remote version string is strictly newer than local."""
    try:
        return tuple(int(x) for x in remote.split(".")) \
             > tuple(int(x) for x in local.split("."))
    except Exception:
        return bool(remote) and remote != local

def notify_if_update():
    """Check the repo for a newer version; if found, push a note + Update button."""
    try:
        new_ver = _parse_version(_fetch_remote_script())
    except Exception as e:
        print(f"[update-check] {e}", file=sys.stderr)
        return
    if new_ver and _newer(new_ver, VERSION):
        publish(t("upd_avail", h=HOSTNAME, cur=VERSION, new=new_ver),
                title=t("upd_avail_title", h=HOSTNAME), tags="arrow_up",
                actions="; ".join([_action("Update now", "update"),
                                   _view("Docs", DOCS_URL)]))

def do_update():
    """Download the latest script; if newer, overwrite this file and re-exec."""
    try:
        remote = _fetch_remote_script()
    except Exception as e:
        publish(t("upd_fail", h=HOSTNAME, e=e), tags="x")
        return
    new_ver = _parse_version(remote) or "?"
    if new_ver == VERSION:
        publish(t("up_to_date", h=HOSTNAME, v=VERSION), tags="white_check_mark")
        return
    path = os.path.abspath(__file__)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(remote)
    except PermissionError:
        publish(t("upd_perm", h=HOSTNAME, p=path), tags="lock")
        return
    except Exception as e:
        publish(t("upd_fail", h=HOSTNAME, e=e), tags="x")
        return
    publish(t("updating", h=HOSTNAME, cur=VERSION, new=new_ver),
            title=f"{HOSTNAME} update", tags="arrows_counterclockwise")
    os.execv(sys.executable, [sys.executable, path] + sys.argv[1:])   # restart with new code

# ----------------------------------------------------------------------------
# command handling
# ----------------------------------------------------------------------------
def handle_command(cmd):
    cmd = cmd.strip().lower()
    if cmd not in COMMANDS:
        return
    now = time.time()
    if _last_cmd["text"] == cmd and now - _last_cmd["ts"] < DEDUP_WINDOW:
        return
    _last_cmd["text"], _last_cmd["ts"] = cmd, now

    if cmd == "status":
        msg, sev = build_status(full=True)
        publish(msg, title=f"{HOSTNAME} status", tags="bar_chart",
                severity=sev, actions=status_actions())
    elif cmd == "up":
        publish(t("alive", h=HOSTNAME, u=get_uptime()),
                title=f"{HOSTNAME} up", tags="white_check_mark")
    elif cmd == "ping":
        publish(f"pong from {HOSTNAME}", tags="ping_pong")
    elif cmd == "disk":
        v, s = get_disk()
        publish(f"{HOSTNAME} disk: {v}", tags="floppy_disk", severity=s)
    elif cmd == "mem":
        v, s = get_mem()
        publish(f"{HOSTNAME} mem: {v}", tags="brain", severity=s)
    elif cmd == "temp":
        v, s = get_temp()
        publish(f"{HOSTNAME} temp: {v}", tags="thermometer", severity=s)
    elif cmd == "top":
        publish(f"{t('top', h=HOSTNAME)}:\n{get_top()}", tags="fire")
    elif cmd == "version":
        publish(t("ver", h=HOSTNAME, v=VERSION), tags="label")
    elif cmd == "update":
        do_update()
    elif cmd == "docs":
        publish(t("docs"), title="sysmon docs", tags="books",
                actions="; ".join([_view("Open docs", DOCS_URL),
                                    _view("GitHub", REPO_URL)]))
    elif cmd == "help":
        publish(t("help"), title="sysmon help", tags="information_source")

# ----------------------------------------------------------------------------
# subscribe loop
# ----------------------------------------------------------------------------
def subscribe_loop():
    print(f"[sysmon] subscribe: {SUB_URL}  lang={LANG}")
    print(f"[sysmon] self-tag: {SELF_TAG}")
    backoff = 1
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(SUB_URL), timeout=90) as stream:
                backoff = 1
                for raw in stream:
                    if not raw.strip():
                        continue
                    try:
                        evt = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    if evt.get("event") != "message":
                        continue
                    if SELF_TAG in (evt.get("tags") or []):
                        continue
                    handle_command(evt.get("message", ""))
        except KeyboardInterrupt:
            print("\n[sysmon] stopped.")
            return
        except Exception as e:
            print(f"[sysmon] stream lost: {e} -> reconnect {backoff}s", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

# ----------------------------------------------------------------------------
# watchdog: periodic check, push only when the severity level changes
# ----------------------------------------------------------------------------
def watchdog_loop():
    print(f"[sysmon] watchdog: every {INTERVAL}s")
    last = "ok"                       # startup push already reported the initial state
    while True:
        time.sleep(INTERVAL)
        try:
            msg, sev = build_status(full=True)
        except Exception as e:
            print(f"[watchdog] {e}", file=sys.stderr)
            continue
        if sev == last:
            continue                  # no level change -> stay quiet
        if sev == "ok":
            publish(msg, title=t("recovered", h=HOSTNAME),
                    tags="white_check_mark", severity="ok", actions=status_actions())
        else:
            publish(msg, title=t("degraded", h=HOSTNAME, sev=sev.upper()),
                    tags="warning", severity=sev, actions=status_actions())
        last = sev

# ----------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------
def main():
    if TOPIC.endswith("CHANGE_ME"):
        print(t("err_topic"), file=sys.stderr)
        sys.exit(1)
    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode == "status":
        msg, sev = build_status(full=True)
        ok = publish(msg, title=f"{HOSTNAME} status", tags="bar_chart", severity=sev)
        print(t("sent") if ok else t("failed"))
    elif mode == "print":
        print(build_status(full=True)[0])
    elif mode == "daemon":
        msg, sev = build_status(full=True)
        publish(f"{t('started', h=HOSTNAME)}\n{msg}",
                title=t("online", h=HOSTNAME), tags="rocket", severity=sev,
                actions=status_actions())
        threading.Thread(target=notify_if_update, daemon=True).start()   # ping if a newer version is out
        if INTERVAL > 0:
            threading.Thread(target=watchdog_loop, daemon=True).start()
        subscribe_loop()
    else:
        print(t("usage")); sys.exit(1)

if __name__ == "__main__":
    main()
