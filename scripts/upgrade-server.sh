#!/bin/sh
set -eu
test "$(id -u)" -eq 0 || exit 1
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
/opt/audioconversion/venv/bin/pip install --upgrade "$ROOT"
systemctl restart audioconversion.service
