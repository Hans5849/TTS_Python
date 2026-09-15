#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || { echo "Run as root" >&2; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
id audioconversion >/dev/null 2>&1 || useradd --system --home /var/lib/audioconversion --shell /usr/sbin/nologin audioconversion
install -d -o audioconversion -g audioconversion /opt/audioconversion /var/lib/audioconversion /var/log/audioconversion
python3 -m venv /opt/audioconversion/venv
/opt/audioconversion/venv/bin/pip install "$ROOT"
install -d -m 0750 /etc/audioconversion
test -e /etc/audioconversion/config.toml || install -m 0640 -o root -g audioconversion "$ROOT/config/example.toml" /etc/audioconversion/config.toml
install -m 0644 "$ROOT/systemd/audioconversion.service" /etc/systemd/system/audioconversion.service
systemctl daemon-reload
systemctl enable --now audioconversion.service
