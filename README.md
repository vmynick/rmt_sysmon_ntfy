# sysmon — ntfy.sh system monitor

One small Python script (stdlib only, **no pip dependencies**). Pushes a
system status to **ntfy.sh** and, subscribed to the same topic, answers
simple commands from anyone who knows the topic.

The command is a shareable one-liner. EN/HU response language. Message
priority scales with severity.

📄 **[One-pager / docs →](https://vmynick.github.io/rmt_sysmon_ntfy/)** (EN/HU)

---

## Install — one command

```bash
curl -fsSL https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/install.sh | bash
```

It informs you first, then asks only what it needs (topic, language —
Enter accepts the default) and does everything else: checks/installs
python3, copies to `/opt/sysmon/`, creates a `systemd` service
(auto-start on boot, auto-restart), and starts it.

**Re-running on an existing install** detects it and asks whether to
**update** (keep the current topic / language / server / interval and just
refresh the script) or do a **clean install** (pick a new topic). Either way
the service is restarted so the new code loads. This is the **only** way to
update — sysmon never updates itself from an ntfy command (that would let
anyone with the topic run code on the box).

Fully unattended (e.g. many machines):

```bash
curl -fsSL https://raw.githubusercontent.com/vmynick/rmt_sysmon_ntfy/main/install.sh \
  | SYSMON_TOPIC=sysmon-aa0ca9c635659f04 SYSMON_LANG=hu bash
```

> Put the env vars on the **`bash`** side of the pipe (as above). In
> `VAR=x curl … | bash` the `VAR` applies only to `curl`, so the script
> wouldn't see it.

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
| `up`     | alive · ip · uptime, and the name to address it by (`@name`) |
| `ping`   | `pong` |
| `disk`   | disk usage |
| `mem`    | memory usage |
| `temp`   | CPU temperature (Raspberry Pi) |
| `top`    | top 5 processes by CPU |
| `net`    | per-interface RX/TX totals |
| `hosts`  | this host's name · ip · version · uptime (roll-call) |
| `version`| running script version |
| `checkupdate` | report if a newer version is on GitHub (read-only) |
| `docs`   | push a message with **Open docs** / **GitHub** buttons |
| `help`   | command list |

Commands are **always English**. Unknown command → silently dropped (allowlist).

Status/alert notifications carry **action buttons** (Status · Top · Disk) — tap
one in the ntfy app to send that command back, no typing. `help` carries the
same buttons. Tapping a button **clears that notification** (`clear=true`).
(ntfy allows at most 3 buttons per message.)

---

## Multiple servers on one topic

Install sysmon on several machines with the **same topic** and they all listen.
By default every host answers a command (so `up` is a quick roll-call). To talk
to just one, **prefix the command with the hostname** (the `@` is optional):

```bash
curl -d "pve up"       https://ntfy.sh/mytopic   # only pve replies
curl -d "pi4 status"   https://ntfy.sh/mytopic   # only pi4 replies
curl -d "@pi4 status"  https://ntfy.sh/mytopic   # same, explicit @ form
curl -d "status"       https://ntfy.sh/mytopic   # all hosts reply
```

The host matches the machine's hostname (full or short, case-insensitive). Each
reply's title is the hostname, so you can tell them apart — send `up` once to
see every name you can use. (Prefer separate topics if you want hard isolation —
the topic is still the password.)

---

## Proxmox (PVE)

On a Proxmox node the `status` report adds a **PVE version** line, and the
installer/`configure` wizard lists the node's **VMs and CTs** so you can monitor
them — including a **Home Assistant OS** VM. Picks are stored in
`SYSMON_CHECK_PVE` (names or VMIDs) and reported like the other checks
(`running` = ok, anything else = crit). Needs to run as root on the PVE host
(default there), so `qm` / `pct` are available.

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

## Proactive alerts (watchdog)

The daemon doesn't only answer commands — every `SYSMON_INTERVAL` seconds
(default 300, `0` disables) it re-checks status and pushes **only when the
severity level changes**: a `high`/`urgent` alert on degrade, a quiet
"recovered" when it returns to ok. No spam while everything is fine.

- **Flap protection** — a non-critical change must persist two checks before it
  alerts (so `warn`↔`ok` flapping stays quiet); `crit` alerts immediately.
- **Quiet hours** — set `SYSMON_QUIET=22:00-07:00` to hold `warn` alerts during
  that window (they fire when it ends); `crit` always breaks through.

It also watches GitHub for new releases: a check on start, then every
`SYSMON_UPDATE_CHECK` seconds (default 86400 = daily, `0` disables). If a
newer version is out it pushes an **"update available"** notice — **tap it**
to open the docs and see what's new, or use the buttons:

- **Dismiss** — keep your version; no more notices for that release
- **Remind 6h** / **Remind 2d** — snooze, then notify again later

Ask any time with the **`checkupdate`** command. To actually upgrade, re-run
the installer one-liner — sysmon never self-updates from a command.

---

## Extra tasks

### 🧩 `sudo sysmon configure` — the easy way

The one command to manage what gets monitored. Run it any time:

```bash
sudo sysmon configure
```

It will:

- **list every running docker container and systemd service** on the machine
  (plus **Proxmox VMs/CTs** if it's a PVE node — e.g. your Home Assistant OS VM),
- mark your current picks, and let you **re-select** (by number or name,
  `*` = all, `-` = none, Enter = keep),
- **save** the choices into the service and **restart** it for you.

No file editing, no remembering env-var names. The same wizard also runs at the
end of the installer the first time.

Behind the scenes the picks are stored as `SYSMON_CHECK_SERVICES` /
`SYSMON_CHECK_DOCKER` / `SYSMON_CHECK_PVE` in the systemd unit, and
`extra_tasks()` checks them on every `status` — a stopped one raises the alert
priority. (Manual alternative: edit those `Environment=` lines in
`/etc/systemd/system/sysmon.service`, then `daemon-reload` + `restart`.)

### Custom checks in code

You can also edit `extra_tasks()` directly for anything beyond
services/containers. It returns
`(label, value, severity)` tuples; helpers `check_service(name)` and
`check_docker(name)` are included:

```python
def extra_tasks():
    results = []
    results.append(check_service("nginx"))         # systemd unit up?
    results.append(check_docker("homeassistant"))  # container running?
    return results
```

---

## Loop prevention

The script reads and writes the same topic, so:
1. **self-tag** — every push carries `sysmon-<host>`; incoming events with
   that tag are skipped → never answers itself.
2. **allowlist** — only the known commands run; everything else dropped.
3. **dedup** — same command at most once per 4 s.
4. **rate limit** — max 8 outgoing replies / 60 s.

---

## Operate

The installer drops a `sysmon` wrapper in `/usr/local/bin`, so:

```bash
sudo sysmon configure                   # ⭐ pick which services/containers to monitor
sudo sysmon doctor                      # self-test: config, tools, ntfy reachability
journalctl -u sysmon -f                 # live log
sudo systemctl restart sysmon           # restart
sudo systemctl stop sysmon              # stop
sudo systemctl disable --now sysmon \
  && sudo rm /etc/systemd/system/sysmon.service \
            /opt/sysmon/sysmon.py /usr/local/bin/sysmon   # remove
```

Manual run without the service (`sysmon` = `python3 /opt/sysmon/sysmon.py`):

```bash
SYSMON_TOPIC=sysmon-... SYSMON_LANG=hu python3 sysmon.py daemon   # listen
SYSMON_TOPIC=sysmon-... python3 sysmon.py status                  # one push
python3 sysmon.py print                                           # just print
sudo python3 sysmon.py configure                                  # edit checks
```

---

## License

MIT
