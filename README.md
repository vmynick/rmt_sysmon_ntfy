# sysmon — ntfy.sh system monitor

One small Python script (stdlib only, **no pip dependencies**). Pushes a
system status to **ntfy.sh** and, subscribed to the same topic, answers
simple commands from anyone who knows the topic.

The command is a shareable one-liner. EN/HU response language. Message
priority scales with severity.

---

## Install — one command

```bash
curl -fsSL https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/install.sh | bash
```

It informs you first, then asks only what it needs (topic, language —
Enter accepts the default) and does everything else: checks/installs
python3, copies to `/opt/sysmon/`, creates a `systemd` service
(auto-start on boot, auto-restart), and starts it.

Fully unattended (e.g. many machines):

```bash
SYSMON_TOPIC=sysmon-aa0ca9c635659f04 SYSMON_LANG=hu \
  curl -fsSL https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/install.sh | bash
```

> The topic name **is the password** — keep it private.
> Self-hosted ntfy: `SYSMON_SERVER=https://ntfy.example.com`.

Then on your phone: **ntfy app → Subscribe**, topic = yours, server `ntfy.sh`.

---

## Use — shareable commands

From anywhere (phone ntfy "Send" button, or shell):

```bash
curl -d status https://ntfy.sh/sysmon-aa0ca9c635659f04
```

| Command  | Returns |
|----------|---------|
| `status` | full report: host, uptime, load, mem, disk, temp + extra tasks |
| `up`     | short "alive" + uptime |
| `ping`   | `pong` |
| `disk`   | disk usage |
| `mem`    | memory usage |
| `temp`   | CPU temperature (Raspberry Pi) |
| `top`    | top 5 processes by CPU |
| `help`   | command list |

Commands are **always English**. Unknown command → silently dropped (allowlist).

---

## Language

`SYSMON_LANG=en` (default) or `hu`. Only the report labels are translated;
the commands stay English so they work the same everywhere.

---

## Priority by severity

`disk` / `mem` / `temp` thresholds (and extra-task severity) set the ntfy
push priority:

| Level | Threshold | ntfy priority | Phone |
|-------|-----------|---------------|-------|
| ok    | disk<80% · mem<85% · temp<70°C | `default` | quiet |
| warn  | disk≥80% · mem≥85% · temp≥70°C | `high` ⚠️ | loud |
| crit  | disk≥92% · mem≥95% · temp≥80°C | `urgent` 🚨 | breaks through silent mode |

Thresholds live in the `TH` dict in `sysmon.py`.

---

## Extra tasks

Add your own checks in `extra_tasks()` — returns `(label, value, severity)`
tuples, appended to the `status` report; severity feeds the priority.

```python
def extra_tasks():
    results = []
    ok = subprocess.call(["systemctl","is-active","--quiet","nginx"]) == 0
    results.append(("nginx", "up" if ok else "DOWN", "ok" if ok else "crit"))
    return results
```

---

## Loop prevention

The script reads and writes the same topic, so:
1. **self-tag** — every push carries `sysmon-<host>`; incoming events with
   that tag are skipped → never answers itself.
2. **allowlist** — only the 8 known commands run; everything else dropped.
3. **dedup** — same command at most once per 4 s.
4. **rate limit** — max 8 outgoing replies / 60 s.

---

## Operate

```bash
journalctl -u sysmon -f                 # live log
sudo systemctl restart sysmon           # restart
sudo systemctl stop sysmon              # stop
sudo systemctl disable --now sysmon \
  && sudo rm /etc/systemd/system/sysmon.service /opt/sysmon/sysmon.py   # remove
```

Manual run without the service:

```bash
SYSMON_TOPIC=sysmon-... SYSMON_LANG=hu python3 sysmon.py daemon   # listen
SYSMON_TOPIC=sysmon-... python3 sysmon.py status                  # one push
python3 sysmon.py print                                           # just print
```

---

## License

MIT
