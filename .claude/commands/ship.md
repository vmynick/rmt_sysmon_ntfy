---
description: Sync all docs to the current code's features, then commit & push to GitHub
---

Bring every doc in this repo in line with what the code actually does right now,
then ship it. Work autonomously; only stop to ask if something is genuinely
ambiguous.

## 1. Read the source of truth
- `sysmon.py`: the `COMMANDS` allowlist, every `handle_command` branch, the
  `VERSION` constant, env vars (`SYSMON_*`), helpers (`check_service`,
  `check_docker`, action-button helpers), the watchdog, and any new behaviour.
- `install.sh`: env vars wired into the systemd unit, prompts, side effects
  (groups, chown, etc.).

## 2. Reconcile the docs against that
Update these so they list **every** command and describe **every** feature the
code has — add what's missing, fix what's stale, remove what no longer exists:
- `README.md` — command table, feature sections, env/config. **English only.**
- `HANDOVER.md` — "State / done" bullets and file list.
- `docs/index.html` — the one-pager. It is **bilingual EN/HU**: every prose
  string must exist as both `<span class="l-en">…</span>` and
  `<span class="l-hu">…</span>` (or paired `.l-en` / `.l-hu` block elements).
  Shared command/code blocks stay single. Keep the terminal/phosphor styling.

Rules: README and all code comments stay **English**. Only the one-pager is
bilingual. Do not invent features the code doesn't have.

## 3. Version
If `sysmon.py` behaviour changed since the last commit (new/changed command or
feature), bump `VERSION` (semver: feature → minor, fix → patch). Keep the README
/ one-pager command tables in sync with the bump.

## 4. Verify
- `python -m py_compile sysmon.py`
- `bash -n install.sh`
Fix anything that fails before continuing.

## 5. Ship
- `git add -A`
- Commit with a clear message summarising the doc/feature sync (and version bump
  if any). End the message with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- `git push origin main` (we work on `main` for this repo).
- Confirm `git status -sb` shows the tree clean and in sync.

## 6. Report
List what you changed (per file), the new VERSION if bumped, and the pushed
commit hash. If nothing was out of sync, say so and skip the commit.
