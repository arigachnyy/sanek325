#!/usr/bin/env bash
# Cron wrapper that sends a Telegram alert on non-zero exit.
#
# Usage (from crontab):
#   /root/workspace/sanek325/bin/cron_notify.sh <project-dir> <command...>
#
# Example:
#   */10 * * * * /root/workspace/sanek325/bin/cron_notify.sh playtomic-calendar .venv/bin/python playtomic_to_calendar.py
#
# Telegram credentials are read from playtomic-booker/.env (shared bot).
# Failures post a short message to the same chat your scripts already use.

set -u

SANEK_ROOT="/root/workspace/sanek325"

project="${1:?usage: cron_notify.sh <project> <cmd...>}"
shift

cd "$SANEK_ROOT/$project" || {
    # If we can't even cd, try to alert — but we may not have creds yet.
    project_dir_missing=1
}

# Load Telegram creds from a project that's known to have them.
# (playtomic-booker/.env has TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.)
if [ -f "$SANEK_ROOT/playtomic-booker/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SANEK_ROOT/playtomic-booker/.env"
    set +a
fi

notify() {
    local msg="$1"
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -sS --max-time 10 \
            -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${msg}" \
            >/dev/null || true
    fi
}

if [ "${project_dir_missing:-0}" = "1" ]; then
    notify "[cron] $project: project directory missing"
    exit 1
fi

# Capture the last ~20 lines of stderr to include in the alert.
err_file="$(mktemp)"
trap 'rm -f "$err_file"' EXIT

"$@" 2> >(tee -a "$err_file" >&2)
rc=$?

if [ "$rc" -ne 0 ]; then
    tail="$(tail -n 20 "$err_file")"
    notify "[cron] $project exit=$rc
cmd: $*
---
$tail"
fi

exit "$rc"
