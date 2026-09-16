#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || { echo "sudo is required." >&2; exit 1; }
    exec sudo -- "$0" "$@"
fi
[ -r /etc/audioconversion/install.conf ] || { echo "Audioconversion is not installed; run ./install.sh first." >&2; exit 1; }
PROGRAM_DIR=$(sed -n '1p' /etc/audioconversion/install.conf)
SERVICE_USER=$(sed -n '2p' /etc/audioconversion/install.conf)
SERVICE_GROUP=$(sed -n '3p' /etc/audioconversion/install.conf)
[ -d "$PROGRAM_DIR" ] || { echo "Installed program directory is missing: $PROGRAM_DIR" >&2; exit 1; }

WAS_ACTIVE=0
if systemctl is-active --quiet audioconversion.service; then
    WAS_ACTIVE=1
    systemctl stop audioconversion.service
fi
restore_service() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ "$WAS_ACTIVE" -eq 1 ] && ! systemctl is-active --quiet audioconversion.service; then
        echo "Restoring previously active service..." >&2
        systemctl start audioconversion.service || true
    fi
    exit "$result"
}
trap restore_service EXIT HUP INT TERM

if [ -d "$PROGRAM_DIR/.git" ]; then
    git -C "$PROGRAM_DIR" pull --ff-only
else
    SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    if [ "$SOURCE_DIR" != "$PROGRAM_DIR" ]; then
        (cd "$SOURCE_DIR" && tar --exclude='./.venv' --exclude='./venv' --exclude='./build' --exclude='./dist' -cf - .) |
        (cd "$PROGRAM_DIR" && tar -xf -)
    fi
fi
"$PROGRAM_DIR/venv/bin/pip" install --upgrade "${PROGRAM_DIR}[openai]"
sed -e "s|@PROGRAM_DIR@|$PROGRAM_DIR|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
    -e "s|@SERVICE_GROUP@|$SERVICE_GROUP|g" "$PROGRAM_DIR/systemd/audioconversion.service" \
    > /etc/systemd/system/audioconversion.service
systemctl daemon-reload
if [ "$WAS_ACTIVE" -eq 1 ]; then
    systemctl start audioconversion.service
fi
trap - EXIT HUP INT TERM
echo "Update complete. Configuration and runtime data were preserved. Run: tts dashboard"
