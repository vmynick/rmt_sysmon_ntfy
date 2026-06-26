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
    help    -> command list

Config via environment:
    SYSMON_TOPIC   ntfy topic (required)
    SYSMON_SERVER  ntfy server URL          (default https://ntfy.sh)
    SYSMON_LANG    response language en|hu   (default en)

Message priority scales with severity (disk/mem/temp thresholds):
    ok -> default | warn -> high | crit -> urgent
"""

import os
import sys
import json
import time
import socket
import shutil
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

HOSTNAME = socket.gethostname()
SELF_TAG = f"sysmon-{HOSTNAME}"          # loop-prevention: recognise own pushes

PUB_URL = f"{SERVER}/{TOPIC}"
SUB_URL = f"{SERVER}/{TOPIC}/json"

COMMANDS = {"status", "up", "ping", "disk", "mem", "temp", "top", "help"}

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
        "help": "Commands: status, up, ping, disk, mem, temp, top, help",
        "top": "Top CPU ({h})",
        "na": "n/a", "sent": "sent", "failed": "failed",
        "err_topic": "ERROR: set SYSMON_TOPIC (env or top of script).",
        "usage": "Usage: sysmon.py [daemon|status|print]",
    },
    "hu": {
        "host": "Gep", "up": "Fut", "load": "Terheles", "mem": "Memoria",
        "disk": "Lemez", "temp": "Hom.", "extra": "extra",
        "alive": "{h} elek. Uzemido: {u}",
        "started": "{h} sysmon elindult.",
        "online": "{h} online",
        "help": "Parancsok: status, up, ping, disk, mem, temp, top, help",
        "top": "Top CPU ({h})",
        "na": "n/a", "sent": "elkuldve", "failed": "sikertelen",
        "err_topic": "HIBA: allitsd be a SYSMON_TOPIC-ot (env vagy a script teteje).",
        "usage": "Hasznalat: sysmon.py [daemon|status|print]",
    },
}
def t(key, **kw):
    return T[LANG][key].format(**kw)

def sev_max(*sevs):
    return max(sevs, key=lambda s: SEV_ORDER.index(s))

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
# extra task hook
# ----------------------------------------------------------------------------
def extra_tasks():
    """Return list of (label, value, severity) tuples. severity: ok|warn|crit."""
    results = []
    # ok = subprocess.call(["systemctl","is-active","--quiet","nginx"]) == 0
    # results.append(("nginx", "up" if ok else "DOWN", "ok" if ok else "crit"))
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
def publish(message, title=None, tags=None, severity="ok"):
    now = time.time()
    global _sent_times
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
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"[publish error] {e}", file=sys.stderr)
        return False

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
        publish(msg, title=f"{HOSTNAME} status", tags="bar_chart", severity=sev)
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
                title=t("online", h=HOSTNAME), tags="rocket", severity=sev)
        subscribe_loop()
    else:
        print(t("usage")); sys.exit(1)

if __name__ == "__main__":
    main()
