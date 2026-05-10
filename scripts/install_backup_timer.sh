#!/usr/bin/env bash
# One-shot installer: drops the systemd unit + timer files into place,
# enables and starts the timer. Idempotent — safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"

sudo install -m 0644 "$HERE/denzo-backup.service" /etc/systemd/system/denzo-backup.service
sudo install -m 0644 "$HERE/denzo-backup.timer"   /etc/systemd/system/denzo-backup.timer

sudo systemctl daemon-reload
sudo systemctl enable --now denzo-backup.timer

echo ""
echo "✓ denzo-backup.timer installed and active."
sudo systemctl list-timers denzo-backup.timer --no-pager
echo ""
echo "Next run shown above. Logs: tail -f /var/log/denzo-backup.log"
