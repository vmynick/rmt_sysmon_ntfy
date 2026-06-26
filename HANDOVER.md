# HANDOVER — rmt_sysmon_ntfy

Remote system monitor: ntfy.sh push + command-response. Python stdlib only.

## Files
- `sysmon.py`       — main script (collectors, ntfy publish/subscribe, command handler)
- `install.sh`      — one-line bootstrap installer (curl | bash), systemd service
- `README.md`       — usage docs (EN)
- `onepager/index.html` — standalone one-pager doc (EN/HU toggle), terminal/phosphor theme
- `LICENSE`         — MIT
- `.gitignore`

## State / done
- EN/HU response language via `SYSMON_LANG` (commands stay English)
- Severity-based ntfy priority: ok=default, warn=high, crit=urgent
  thresholds in `TH` dict (disk/mem/temp)
- Loop prevention: self-tag, allowlist, 4s dedup, 8/60s rate limit
- One-line installer prompts only for topic + lang (Enter=default), else env-driven
- All placeholders set to vmynick/rmt_sysmon_ntfy
- Watchdog (`SYSMON_INTERVAL`, default 300, 0=off): background thread pushes
  only when severity level changes (degrade/recover) — proactive alerts
- Action buttons (ntfy `Actions` header) on status/alert pushes: Status/Top/Disk
- `version` + `update` commands: `update` fetches latest sysmon.py, compares
  `VERSION`, overwrites `__file__`, re-execs. Installer chowns `$DEST` to the
  run user so the service can self-overwrite (no root needed for update).
- `check_service(name)` / `check_docker(name)` helpers for `extra_tasks()`

## Distribution
- PUBLIC GitHub repo: https://github.com/vmynick/rmt_sysmon_ntfy (main)
- `install.sh` + `sysmon.py` fetched over raw.githubusercontent.com (no token).
  The `curl | bash` one-liner in README/doc works as-is.
- `.gitattributes` forces LF on `*.sh` / `*.py` so scripts run on Linux.

## TODO / open
- [ ] Optional: rotary/extra commands, more extra_tasks() examples

## Local test
    SYSMON_TOPIC=x SYSMON_LANG=hu python3 sysmon.py print
    SYSMON_TOPIC=x python3 sysmon.py print

## Config (env)
    SYSMON_TOPIC   required
    SYSMON_SERVER  default https://ntfy.sh
    SYSMON_LANG    en | hu  (default en)
