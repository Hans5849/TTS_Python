#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$0" "$@"
fi
[ -x /usr/local/bin/speech-common ] || {
    echo "Install Speech_Common first to inventory and safely remove this application." >&2
    exit 1
}
exec /usr/local/bin/speech-common uninstall --app tts "$@"
