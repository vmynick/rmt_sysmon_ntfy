# HANDOVER — rmt_sysmon_ntfy

Remote system monitor: ntfy.sh push + command-response. Python stdlib only.

## Files
- `sysmon.py`       — main script (collectors, ntfy publish/subscribe, command handler)
- `install.sh`      — one-line bootstrap installer (curl | bash), systemd service
- `README.md`       — usage docs (EN)
- `sysmon-doc.html` — standalone one-pager doc (EN/HU toggle), terminal/phosphor theme
- `LICENSE`         — MIT
- `.gitignore`

## State / done
- EN/HU response language via `SYSMON_LANG` (commands stay English)
- Severity-based ntfy priority: ok=default, warn=high, crit=urgent
  thresholds in `TH` dict (disk/mem/temp)
- Loop prevention: self-tag, allowlist, 4s dedup, 8/60s rate limit
- One-line installer prompts only for topic + lang (Enter=default), else env-driven
- All placeholders set to vmynick/rmt_sysmon_ntfy

## TODO / open
- [ ] Create PRIVATE GitHub repo vmynick/rmt_sysmon_ntfy and push
- [ ] Private repo => raw URL needs token. Decide distribution:
      public repo | Release asset | Gist. Then adjust install.sh fetch line
      ($REPO_RAW / sysmon.py download) + doc curl commands.
- [ ] Optional: rotary/extra commands, more extra_tasks() examples

## Push (run yourself, your credentials)
    cd rmt_sysmon_ntfy
    git init
    git add .
    git commit -m "sysmon: ntfy system monitor (push + command-response)"
    git branch -M main
    git remote add origin git@github.com:vmynick/rmt_sysmon_ntfy.git
    git push -u origin main

## Local test
    SYSMON_TOPIC=x SYSMON_LANG=hu python3 sysmon.py print
    SYSMON_TOPIC=x python3 sysmon.py print

## Config (env)
    SYSMON_TOPIC   required
    SYSMON_SERVER  default https://ntfy.sh
    SYSMON_LANG    en | hu  (default en)
