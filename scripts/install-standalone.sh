#!/bin/sh
set -eu
python3 -m venv "${HOME}/.local/share/audioconversion/venv"
"${HOME}/.local/share/audioconversion/venv/bin/pip" install "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
mkdir -p "${HOME}/.config/audioconversion"
printf '%s\n' "Installed. Copy config/example.toml to ~/.config/audioconversion/config.toml and edit it."
