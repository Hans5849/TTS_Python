#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || exit 1
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl disable --now audioconversion.service 2>/dev/null || true
fi
rm -f /etc/systemd/system/audioconversion.service
if [ -r /etc/audioconversion/install.conf ]; then
    PROGRAM_DIR=$(sed -n '1p' /etc/audioconversion/install.conf)
    case "$PROGRAM_DIR" in /opt/*) rm -rf -- "$PROGRAM_DIR" ;; esac
fi
rm -f /usr/local/bin/tts
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi
echo "Configuration and runtime data were intentionally retained."
