#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || exit 1
systemctl disable --now audioconversion.service 2>/dev/null || true
rm -f /etc/systemd/system/audioconversion.service
rm -rf /opt/audioconversion
systemctl daemon-reload
echo "Configuration and runtime data were intentionally retained."
