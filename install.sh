#!/bin/sh
set -eu
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -x /usr/local/bin/speech-common ] || {
    echo "Install Speech_Common 0.1.0 first, then rerun this installer." >&2
    exit 1
}
if [ "$(id -u)" -ne 0 ]; then
    exec sudo -- "$SOURCE_DIR/install.sh" "$@"
fi
exec /usr/local/bin/speech-common install-app --app tts --source "$SOURCE_DIR" "$@"
