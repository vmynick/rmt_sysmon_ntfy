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
    net     -> per-interface RX/TX totals
    hosts   -> this host's name/ip/version/uptime (roll-call on a shared topic)
    version     -> running script version
    checkupdate -> report if a newer version is on GitHub (read-only)
    docs        -> push links with "Open docs" / "GitHub" buttons
    (button-only: dismiss, remind6h, remind2d -> manage update notices)
    help    -> command list

Config via environment:
    SYSMON_TOPIC    ntfy topic (required)
    SYSMON_SERVER   ntfy server URL          (default https://ntfy.sh)
    SYSMON_LANG     response language en|hu   (default en)
    SYSMON_INTERVAL watchdog seconds; 0=off   (default 300)
    SYSMON_UPDATE_CHECK version-check seconds; 0=off (default 86400)
    SYSMON_CHECK_SERVICES  comma-separated systemd units to monitor in `status`
    SYSMON_CHECK_DOCKER    comma-separated docker containers to monitor in `status`
    SYSMON_CHECK_PVE       comma-separated Proxmox VM/CT names or VMIDs to monitor
    SYSMON_QUIET           quiet-hours window e.g. 22:00-07:00 (hold non-crit alerts)

Address one host when several share a topic: prefix the command with the
hostname (the @ is optional), e.g. "pve up" or "@pi4 status" (no prefix =
every host answers). On a Proxmox node the report shows the PVE version;
pick the HAOS VM via SYSMON_CHECK_PVE.

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
try:
    UPDATE_CHECK = int(os.environ.get("SYSMON_UPDATE_CHECK", "86400"))  # version-check period; 0 disables
except ValueError:
    UPDATE_CHECK = 86400

# extra checks selected by the installer wizard (comma-separated names)
CHECK_SERVICES = [s.strip() for s in os.environ.get("SYSMON_CHECK_SERVICES", "").split(",") if s.strip()]
CHECK_DOCKER   = [s.strip() for s in os.environ.get("SYSMON_CHECK_DOCKER",   "").split(",") if s.strip()]
CHECK_PVE      = [s.strip() for s in os.environ.get("SYSMON_CHECK_PVE",       "").split(",") if s.strip()]

# quiet hours: hold non-critical watchdog alerts in this local-time window, e.g. "22:00-07:00"
def _hhmm(x):
    h, m = x.split(":"); return int(h) * 60 + int(m)
try:
    _q = os.environ.get("SYSMON_QUIET", "").strip()
    QUIET = tuple(_hhmm(p) for p in _q.split("-")) if _q else None
    if QUIET and len(QUIET) != 2:
        QUIET = None
except Exception:
    QUIET = None

HOSTNAME = socket.gethostname()
SELF_TAG = f"sysmon-{HOSTNAME}"          # loop-prevention: recognise own pushes

PUB_URL = f"{SERVER}/{TOPIC}"
SUB_URL = f"{SERVER}/{TOPIC}/json"

VERSION = "1.10.0"
UPDATE_URL = os.environ.get(
    "SYSMON_UPDATE_URL",
    "https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/sysmon.py")
DOCS_URL = "https://vmynick.github.io/rmt_sysmon_ntfy/"
REPO_URL = "https://github.com/vmynick/rmt_sysmon_ntfy"
UNIT_PATH = os.environ.get("SYSMON_UNIT", "/etc/systemd/system/sysmon.service")

COMMANDS = {"status", "up", "ping", "disk", "mem", "temp", "top", "net",
            "hosts", "help", "version", "checkupdate", "docs",
            "dismiss", "remind6h", "remind2d"}     # update-notice buttons

# severity thresholds (percent for disk/mem, Celsius for temp)
TH = {
    "disk": (80, 92),   # warn, crit
    "mem":  (85, 95),
    "temp": (70, 80),
}
PRIO = {"ok": "default", "warn": "high", "crit": "urgent"}
SEV_ORDER = ("ok", "warn", "crit")
SEV_ICON = {"ok": "🟢", "warn": "🟡", "crit": "🔴"}   # status dot per metric

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
        "help": "Commands: status, up, ping, disk, mem, temp, top, net, "
                "hosts, version, checkupdate, docs, help",
        "top": "Top CPU ({h})",
        "docs": "sysmon docs & links — tap a button below.",
        "na": "n/a", "sent": "sent", "failed": "failed",
        "ver": "{h} sysmon v{v}",
        "up_to_date": "{h} up to date (v{v}).",
        "upd_avail_title": "{h} update available",
        "upd_avail": "New version v{new} available (running v{cur}). "
                     "Tap to see what's new; re-run the installer to update.",
        "upd_fail": "{h} update check failed: {e}",
        "upd_snoozed": "{h} update reminder snoozed for {t}.",
        "upd_dismissed": "{h} dismissed update v{v} — no more notices for it.",
        "degraded": "{h} {sev}",
        "recovered": "{h} recovered",
        "err_topic": "ERROR: set SYSMON_TOPIC (env or top of script).",
        "usage": "Usage: sysmon.py [daemon|status|print|configure|doctor]",
    },
    "hu": {
        "host": "Gep", "up": "Fut", "load": "Terheles", "mem": "Memoria",
        "disk": "Lemez", "temp": "Hom.", "extra": "extra",
        "alive": "{h} elek. Uzemido: {u}",
        "started": "{h} sysmon elindult.",
        "online": "{h} online",
        "help": "Parancsok: status, up, ping, disk, mem, temp, top, net, "
                "hosts, version, checkupdate, docs, help",
        "top": "Top CPU ({h})",
        "docs": "sysmon dokumentacio es linkek — koppints egy gombra lent.",
        "na": "n/a", "sent": "elkuldve", "failed": "sikertelen",
        "ver": "{h} sysmon v{v}",
        "up_to_date": "{h} naprakesz (v{v}).",
        "upd_avail_title": "{h} frissites elerheto",
        "upd_avail": "Uj verzio elerheto: v{new} (jelenleg v{cur}). "
                     "Koppints az ujdonsagokert; frissiteshez futtasd ujra a telepitot.",
        "upd_fail": "{h} frissites-ellenorzes sikertelen: {e}",
        "upd_snoozed": "{h} frissites-emlekezteto elhalasztva: {t}.",
        "upd_dismissed": "{h} v{v} frissites elvetve — nincs tobb ertesites rola.",
        "degraded": "{h} {sev}",
        "recovered": "{h} helyreallt",
        "err_topic": "HIBA: allitsd be a SYSMON_TOPIC-ot (env vagy a script teteje).",
        "usage": "Hasznalat: sysmon.py [daemon|status|print|configure|doctor]",
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

def _hsize(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}P"

def get_net():
    """Per-interface cumulative RX/TX from /proc/net/dev (skip lo and idle ifaces)."""
    try:
        out = []
        for line in open("/proc/net/dev").read().splitlines()[2:]:
            name, _, data = line.partition(":")
            name = name.strip()
            f = data.split()
            if name == "lo" or len(f) < 9:
                continue
            rx, tx = int(f[0]), int(f[8])
            if rx or tx:
                out.append(f"  {name}: ↓{_hsize(rx)} ↑{_hsize(tx)}")
        return "\n".join(out) if out else t("na")
    except Exception:
        return t("na")

# ----------------------------------------------------------------------------
# reusable checks for extra_tasks()  (each returns a (label, value, severity) tuple)
# ----------------------------------------------------------------------------
def check_service(name):
    try:
        ok = subprocess.call(["systemctl", "is-active", "--quiet", name]) == 0
    except Exception:
        return (name, t("na"), "ok")              # no systemctl / not systemd
    return (name, " up" if ok else " DOWN", " ok" if ok else " crit")

def check_docker(name):
    """Is Docker container <name> running? running=ok, stopped/absent=crit."""
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        return (f"docker · {name}  ", "running" if out == "true" else "stopped",
                "ok" if out == "true" else "crit")
    except subprocess.CalledProcessError:
        return (f"docker · {name}  ", "absent", "crit")     # no such container
    except Exception:
        return (f"docker · {name}  ", t("na"), "ok")          # docker missing/unreachable

def get_pve():
    """Proxmox VE version (e.g. '8.1.4') if this is a PVE node, else None."""
    try:
        out = subprocess.check_output(["pveversion"], stderr=subprocess.DEVNULL,
                                      timeout=3).decode().strip()
        parts = out.split("/")                 # pve-manager/8.1.4/<git>
        return parts[1] if len(parts) > 1 else out
    except Exception:
        return None

def _pve_guests():
    """List Proxmox guests as (vmid, name, status, kind) — VMs (qm) + CTs (pct)."""
    guests = []
    for cmd, kind in ((["qm", "list"], "vm"), (["pct", "list"], "ct")):
        try:
            rows = subprocess.check_output(cmd, stderr=subprocess.DEVNULL,
                                           timeout=5).decode().splitlines()[1:]
        except Exception:
            continue
        for row in rows:
            f = row.split()
            if len(f) < 3:
                continue
            if kind == "vm":                   # VMID NAME STATUS ...
                guests.append((f[0], f[1], f[2], "vm"))
            else:                              # VMID STATUS [LOCK] NAME
                guests.append((f[0], f[-1], f[1], "ct"))
    return guests

def _match_guest(name, guests):
    name = str(name)
    for vmid, gname, status, kind in guests:
        if name == vmid or name.lower() == gname.lower():
            return (f"{kind} · {gname}", status, "ok" if status == "running" else "crit")
    return (f"pve · {name}", t("na"), "ok")    # not found / not a PVE host

def check_pve_guest(name):
    """Check a Proxmox VM/CT by name or VMID (e.g. the HAOS VM). running=ok else crit."""
    return _match_guest(name, _pve_guests())

# ----------------------------------------------------------------------------
# extra task hook
# ----------------------------------------------------------------------------
def extra_tasks():
    """Return list of (label, value, severity) tuples. severity: ok|warn|crit.

    Appended to the `status` report; the worst severity here also bumps the
    push priority. Uncomment / edit the examples below for your machine.
    """
    results = []

    # selected by the installer wizard (SYSMON_CHECK_SERVICES / _DOCKER / _PVE)
    for name in CHECK_SERVICES:
        results.append(check_service(name))   # up=ok, down=crit
    for name in CHECK_DOCKER:
        results.append(check_docker(name))    # running=ok, stopped/absent=crit
    if CHECK_PVE:
        guests = _pve_guests()                # fetch once, match all (e.g. the HAOS VM)
        for name in CHECK_PVE:
            results.append(_match_guest(name, guests))

    # --- add your own checks here too, e.g. ---
    # results.append(check_service("nginx"))
    # results.append(check_pve_guest("haos"))

    return results

def _metric(emoji, label, value, sev):
    # trailing status dot only when the metric actually has a severity reading
    dot = "" if value == t("na") else f"  {SEV_ICON.get(sev, '⚪')}"
    return f"{emoji} {label:<5} {value}{dot}"

def build_status(full=True):
    mem, sm = get_mem()
    disk, sd = get_disk()
    temp, st = get_temp()
    lines = [f"🖥️  {HOSTNAME}  ·  {get_ip()}"]
    pve = get_pve()
    if pve:
        lines.append(f"🅿️  PVE   {pve}")          # Proxmox node
    lines += [
        f"⏱️  {t('up'):<5} {get_uptime()}",
        f"📈  {t('load'):<5} {get_load()}",
        _metric("🧠", t("mem"),  mem,  sm),
        _metric("💾", t("disk"), disk, sd),
        _metric("🌡️", t("temp"), temp, st),
    ]
    sev = sev_max(sm, sd, st)
    if full:
        try:
            extras = extra_tasks()                 # never let an extra check crash status
        except Exception as e:
            extras = [("extra_tasks", f"error: {e}", "warn")]
        if extras:
            lines.append(f"\n{t('extra')}")
            for k, v, s in extras:
                s = str(s).strip().lower() or "ok"     # tolerate stray space / case
                lines.append(f"  {SEV_ICON.get(s, '⚪')}  {k}: {v}")
                sev = sev_max(sev, s if s in SEV_ORDER else "warn")
    return "\n".join(lines), sev

# ----------------------------------------------------------------------------
# ntfy publish
# ----------------------------------------------------------------------------
def publish(message, title=None, tags=None, severity="ok", actions=None, click=None):
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
    if click:
        req.add_header("Click", click)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"[publish error] {e}", file=sys.stderr)
        return False

# ----------------------------------------------------------------------------
# update check  (report only — never modifies anything; update via the installer)
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

# update-notification state (shared between the listener and update threads)
_avail_ver     = None                # newest version on GitHub that is > VERSION
_dismissed_ver = None                # version the user chose to keep -> no more notices
_snooze_until  = 0.0                 # suppress update notices until this timestamp
_update_wake   = threading.Event()   # poke update_loop to recompute its timing

def _update_actions():
    # up to 3 ntfy buttons; tapping POSTs the command back to the topic
    return "; ".join([_action("Dismiss", "dismiss"),
                      _action("Remind 6h", "remind6h"),
                      _action("Remind 2d", "remind2d")])

def _announce_update(new_ver):
    # Click opens the docs/feature page; buttons let the user dismiss or snooze
    publish(t("upd_avail", h=HOSTNAME, cur=VERSION, new=new_ver),
            title=t("upd_avail_title", h=HOSTNAME), tags="arrow_up",
            click=DOCS_URL, actions=_update_actions())

def _fetch_avail():
    """Fetch the repo VERSION; cache in _avail_ver if newer than ours. None on miss/err."""
    global _avail_ver
    try:
        rv = _parse_version(_fetch_remote_script())
    except Exception as e:
        print(f"[update-check] {e}", file=sys.stderr)
        return None
    _avail_ver = rv if (rv and _newer(rv, VERSION)) else None
    return _avail_ver

def _may_notify(now):
    return bool(_avail_ver) and _avail_ver != _dismissed_ver and now >= _snooze_until

def notify_if_update():
    """Startup check: announce a newer version unless dismissed or snoozed."""
    _fetch_avail()
    if _may_notify(time.time()):
        _announce_update(_avail_ver)

def update_loop():
    """Re-check GitHub on a cadence; also fire a reminder when a snooze expires."""
    print(f"[sysmon] version-check: every {UPDATE_CHECK}s")
    last_fetch = time.time()                     # startup already fetched
    while True:
        now = time.time()
        next_fetch = last_fetch + UPDATE_CHECK
        wake_at = next_fetch
        if _avail_ver and _avail_ver != _dismissed_ver and _snooze_until > now:
            wake_at = min(wake_at, _snooze_until)     # wake to remind
        if _update_wake.wait(timeout=max(1, wake_at - now)):
            _update_wake.clear()                      # state changed -> recompute
            continue
        now = time.time()
        if now >= next_fetch:
            _fetch_avail()
            last_fetch = now
        if _may_notify(now):
            _announce_update(_avail_ver)

def check_update_cmd():
    """`checkupdate`: report whether a newer version is out. Read-only — never
    downloads or runs anything. Update by re-running the installer."""
    global _avail_ver
    try:
        rv = _parse_version(_fetch_remote_script())
    except Exception as e:
        publish(t("upd_fail", h=HOSTNAME, e=e), tags="x")
        return
    _avail_ver = rv if (rv and _newer(rv, VERSION)) else None
    if _avail_ver and _avail_ver != _dismissed_ver:
        _announce_update(_avail_ver)
    elif _avail_ver:                              # newer, but the user dismissed it
        publish(t("upd_dismissed", h=HOSTNAME, v=_avail_ver), tags="mute")
    else:
        publish(t("up_to_date", h=HOSTNAME, v=VERSION), tags="white_check_mark")

def snooze_update(seconds, label):
    """`remind*`: suppress update notices for a while, then re-announce."""
    global _snooze_until
    _snooze_until = time.time() + seconds
    _update_wake.set()
    publish(t("upd_snoozed", h=HOSTNAME, t=label), tags="alarm_clock")

def dismiss_update():
    """`dismiss`: keep the current version; stop notices about the available one."""
    global _dismissed_ver
    if _avail_ver:
        _dismissed_ver = _avail_ver
        _update_wake.set()
        publish(t("upd_dismissed", h=HOSTNAME, v=_avail_ver), tags="mute")
    else:
        publish(t("up_to_date", h=HOSTNAME, v=VERSION), tags="white_check_mark")

# ----------------------------------------------------------------------------
# command handling
# ----------------------------------------------------------------------------
def _parse_command(raw):
    """Split into (command, target_host). The command is the recognised word; any
    other bare word (or an @host token) is a target hostname. So 'pve up',
    '@pve up', 'up pve', 'up@pve' all target pve; no target => every host answers."""
    target, words = None, []
    for w in str(raw).strip().lower().split():
        if w == "@":
            continue                   # stray "@ host" -> next bare word is the target
        elif w.startswith("@"):
            target = w[1:]
        elif "@" in w:
            c, _, h = w.partition("@"); words.append(c); target = h
        else:
            words.append(w)
    cmd = ""
    for w in words:
        if w in COMMANDS:
            cmd = w
        elif target is None:
            target = w                 # bare hostname, e.g. "pve up" / "status pi4"
    return (cmd, target)

def _for_me(target):
    return target in (None, "", "all", HOSTNAME.lower(), HOSTNAME.split(".")[0].lower())

def handle_command(raw):
    cmd, target = _parse_command(raw)
    if not _for_me(target):
        return                       # addressed to a different host on the same topic
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
    elif cmd == "net":
        publish(f"{HOSTNAME} net:\n{get_net()}", tags="satellite")
    elif cmd == "hosts":
        publish(f"{HOSTNAME} · {get_ip()} · v{VERSION} · up {get_uptime()}",
                title=HOSTNAME, tags="busts_in_silhouette")
    elif cmd == "version":
        publish(t("ver", h=HOSTNAME, v=VERSION), tags="label")
    elif cmd == "checkupdate":
        check_update_cmd()
    elif cmd == "dismiss":
        dismiss_update()
    elif cmd == "remind6h":
        snooze_update(6 * 3600, "6h")
    elif cmd == "remind2d":
        snooze_update(2 * 86400, "2 days")
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
def _in_quiet():
    """True if now is inside SYSMON_QUIET (handles windows crossing midnight)."""
    if not QUIET:
        return False
    lt = time.localtime()
    cur, (a, b) = lt.tm_hour * 60 + lt.tm_min, QUIET
    return a <= cur < b if a <= b else (cur >= a or cur < b)

FLAP_STREAK = 2                       # a non-crit change must hold this many checks

def watchdog_loop():
    print(f"[sysmon] watchdog: every {INTERVAL}s"
          + (f", quiet {QUIET[0]//60:02d}:{QUIET[0]%60:02d}-{QUIET[1]//60:02d}:{QUIET[1]%60:02d}"
             if QUIET else ""))
    last, pending, streak = "ok", "ok", 0   # startup push already reported initial state
    while True:
        time.sleep(INTERVAL)
        try:
            msg, sev = build_status(full=True)
        except Exception as e:
            print(f"[watchdog] {e}", file=sys.stderr)
            continue
        if sev == last:
            pending, streak = sev, 0
            continue
        # flap protection: crit alerts at once, others must persist FLAP_STREAK checks
        if sev == "crit":
            pass
        else:
            streak = streak + 1 if sev == pending else 1
            pending = sev
            if streak < FLAP_STREAK:
                continue
        # quiet hours: hold warn (don't commit, so it alerts once the window ends)
        if sev == "warn" and _in_quiet():
            continue
        if sev == "ok":
            publish(msg, title=t("recovered", h=HOSTNAME),
                    tags="white_check_mark", severity="ok", actions=status_actions())
        else:
            publish(msg, title=t("degraded", h=HOSTNAME, sev=sev.upper()),
                    tags="warning", severity=sev, actions=status_actions())
        last, pending, streak = sev, sev, 0

# ----------------------------------------------------------------------------
# configure wizard  (edit the extra-checks lists in the systemd unit)
# ----------------------------------------------------------------------------
def _list_running(kind):
    """Return docker container names, Proxmox guest names, or systemd service names."""
    try:
        if kind == "docker":
            out = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"],
                                          stderr=subprocess.DEVNULL, timeout=5)
            return [l.strip() for l in out.decode().splitlines() if l.strip()]
        if kind == "pve":
            return [g[1] for g in _pve_guests()]          # VM/CT names
        out = subprocess.check_output(
            ["systemctl", "list-units", "--type=service", "--state=running",
             "--no-legend", "--plain"], stderr=subprocess.DEVNULL, timeout=5)
        names = []
        for l in out.decode().splitlines():
            if l.split():
                n = l.split()[0]
                names.append(n[:-8] if n.endswith(".service") else n)
        return names
    except Exception:
        return []

def _unit_get_env(path, key):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(f"Environment={key}="):
                    return line.split("=", 2)[2].strip()
    except Exception:
        pass
    return ""

def _unit_set_env(path, kv):
    """Replace (or insert before ExecStart) the given Environment=KEY=value lines."""
    with open(path) as f:
        lines = f.readlines()
    done, out = set(), []
    for line in lines:
        for k, v in kv.items():
            if line.startswith(f"Environment={k}="):
                out.append(f"Environment={k}={v}\n"); done.add(k); break
        else:
            out.append(line)
    missing = [k for k in kv if k not in done]
    if missing:
        merged = []
        for line in out:
            if line.startswith("ExecStart=") and missing:
                merged += [f"Environment={k}={kv[k]}\n" for k in missing]; missing = []
            merged.append(line)
        out = merged
    with open(path, "w") as f:
        f.writelines(out)

def _pick(kind, candidates, current):
    print(f"\n{kind}s detected:")
    if not candidates:
        print("  (none)")
    for i, name in enumerate(candidates, 1):
        mark = "*" if name in current else " "
        print(f"  [{mark}] {i:>2}) {name}")
    print("  numbers/names (comma-sep) · * = all · - = none · Enter = keep current")
    ans = input(f"{kind}s to monitor: ").strip()
    if ans == "":
        return current
    if ans == "-":
        return []
    if ans == "*":
        return list(candidates)
    chosen = []
    for tok in ans.replace(",", " ").split():
        if tok.isdigit():
            i = int(tok) - 1
            if 0 <= i < len(candidates):
                chosen.append(candidates[i])
        else:
            chosen.append(tok)
    seen, out = set(), []                     # dedupe, keep order
    for c in chosen:
        if c not in seen:
            seen.add(c); out.append(c)
    return out

def configure_wizard():
    if getattr(os, "geteuid", lambda: 0)() != 0:      # geteuid is POSIX-only
        print("Run as root:  sudo python3 sysmon.py configure"); sys.exit(1)
    if not os.path.exists(UNIT_PATH):
        print(f"No systemd unit at {UNIT_PATH}.")
        print("Install via install.sh first, or set SYSMON_CHECK_SERVICES /")
        print("SYSMON_CHECK_DOCKER yourself."); sys.exit(1)

    cur_svc = [s for s in _unit_get_env(UNIT_PATH, "SYSMON_CHECK_SERVICES").split(",") if s]
    cur_dkr = [s for s in _unit_get_env(UNIT_PATH, "SYSMON_CHECK_DOCKER").split(",") if s]
    cur_pve = [s for s in _unit_get_env(UNIT_PATH, "SYSMON_CHECK_PVE").split(",") if s]
    print("sysmon — configure extra checks (added to the 'status' report)")
    print(f"  current services:   {', '.join(cur_svc) or '(none)'}")
    print(f"  current containers: {', '.join(cur_dkr) or '(none)'}")
    pve_avail = bool(get_pve())
    if pve_avail or cur_pve:
        print(f"  current PVE guests: {', '.join(cur_pve) or '(none)'}")

    dkr = _pick("docker container", _list_running("docker"), cur_dkr)
    pve = _pick("PVE guest (VM/CT)", _list_running("pve"), cur_pve) if (pve_avail or cur_pve) else cur_pve
    svc = _pick("service", _list_running("service"), cur_svc)

    print(f"\nNew services:   {', '.join(svc) or '(none)'}")
    print(f"New containers: {', '.join(dkr) or '(none)'}")
    if pve_avail or cur_pve:
        print(f"New PVE guests: {', '.join(pve) or '(none)'}")
    if input("Save and restart sysmon? [Y/n]: ").strip().lower() not in ("", "y", "yes"):
        print("cancelled."); return
    _unit_set_env(UNIT_PATH, {"SYSMON_CHECK_SERVICES": ",".join(svc),
                              "SYSMON_CHECK_DOCKER": ",".join(dkr),
                              "SYSMON_CHECK_PVE": ",".join(pve)})
    subprocess.call(["systemctl", "daemon-reload"])
    subprocess.call(["systemctl", "restart", "sysmon.service"])
    if subprocess.call(["systemctl", "is-active", "--quiet", "sysmon.service"]) == 0:
        print("saved + restarted. Send 'status' to verify.")
    else:
        print("saved, but the service did NOT come up. Recent log:\n")
        subprocess.call(["journalctl", "-u", "sysmon.service", "-n", "20", "--no-pager"])
        print("\nFix the issue above, then: systemctl restart sysmon")
        sys.exit(1)

# ----------------------------------------------------------------------------
# doctor  (self-test: config, tools, ntfy reachability, sample report)
# ----------------------------------------------------------------------------
def doctor():
    print(f"sysmon v{VERSION}  ·  python {sys.version.split()[0]}  ·  host {HOSTNAME}")
    server = SERVER
    if os.path.exists(UNIT_PATH):
        print("\nunit config:")
        for k in ("SYSMON_TOPIC", "SYSMON_SERVER", "SYSMON_LANG", "SYSMON_INTERVAL",
                  "SYSMON_UPDATE_CHECK", "SYSMON_QUIET", "SYSMON_CHECK_SERVICES",
                  "SYSMON_CHECK_DOCKER", "SYSMON_CHECK_PVE"):
            print(f"  {k}={_unit_get_env(UNIT_PATH, k)}")
        server = _unit_get_env(UNIT_PATH, "SYSMON_SERVER") or SERVER
    print("\ntools:")
    for tool in ("systemctl", "docker", "qm", "pct", "vcgencmd", "sensors",
                 "pveversion", "journalctl"):
        print(f"  {tool:11} {'yes' if shutil.which(tool) else 'no'}")
    print(f"\nntfy {server}: ", end="", flush=True)
    try:
        r = urllib.request.urlopen(f"{server}/v1/health", timeout=5)
        print("reachable" if r.status == 200 else f"HTTP {r.status}")
    except Exception as e:
        print(f"FAIL ({e})")
    if os.path.exists(UNIT_PATH):
        rc = subprocess.call(["systemctl", "is-active", "--quiet", "sysmon.service"])
        print(f"service:     {'active' if rc == 0 else 'NOT active'}")
    print("\nsample status report:")
    print(build_status(full=True)[0])

# ----------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode in ("configure", "wizard"):
        configure_wizard(); return
    if mode == "doctor":
        doctor(); return
    if TOPIC.endswith("CHANGE_ME"):
        print(t("err_topic"), file=sys.stderr)
        sys.exit(1)
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
        if UPDATE_CHECK > 0:
            threading.Thread(target=update_loop, daemon=True).start()    # re-check version periodically
        subscribe_loop()
    else:
        print(t("usage")); sys.exit(1)

if __name__ == "__main__":
    main()
