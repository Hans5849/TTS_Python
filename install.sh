#!/bin/sh
set -eu

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 || { echo "sudo is required for a system installation." >&2; exit 1; }
    exec sudo -- env TTS_PROGRAM_DIR="${TTS_PROGRAM_DIR:-}" TTS_INBOX_DIR="${TTS_INBOX_DIR:-}" \
        TTS_OUTBOX_DIR="${TTS_OUTBOX_DIR:-}" "$SOURCE_DIR/install.sh" "$@"
fi

LOGIN_USER=${SUDO_USER:-root}
LOGIN_GROUP=$(id -gn "$LOGIN_USER")
PROGRAM_DIR=${TTS_PROGRAM_DIR:-/opt/TTS_Python}
INBOX_DIR=${TTS_INBOX_DIR:-/srv/tts/inbox}
OUTBOX_DIR=${TTS_OUTBOX_DIR:-/srv/tts/outbox}

if [ -t 0 ] && [ -t 1 ]; then
    printf 'Program installation path [%s]: ' "$PROGRAM_DIR" > /dev/tty
    IFS= read -r answer < /dev/tty; [ -z "$answer" ] || PROGRAM_DIR=$answer
    printf 'Inbox path [%s]: ' "$INBOX_DIR" > /dev/tty
    IFS= read -r answer < /dev/tty; [ -z "$answer" ] || INBOX_DIR=$answer
    printf 'Outbox path [%s]: ' "$OUTBOX_DIR" > /dev/tty
    IFS= read -r answer < /dev/tty; [ -z "$answer" ] || OUTBOX_DIR=$answer
fi

for value in "$PROGRAM_DIR" "$INBOX_DIR" "$OUTBOX_DIR"; do
    case "$value" in
        /*) ;;
        *) echo "All installation paths must be absolute: $value" >&2; exit 2 ;;
    esac
    case "$value" in *'"'*|*"'"*|*'
'*) echo "Quotes and newlines are not supported in installation paths." >&2; exit 2 ;; esac
done
[ "$PROGRAM_DIR" != "$SOURCE_DIR" ] || { echo "Program path must differ from the source checkout." >&2; exit 2; }

command -v python3 >/dev/null 2>&1 || { echo "python3 is required." >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
    echo "Python 3.11 or newer is required." >&2; exit 1;
}
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "python3-venv is required (for Debian/Ubuntu: apt install python3-venv)." >&2
    exit 1
fi
command -v espeak-ng >/dev/null 2>&1 || {
    echo "espeak-ng is required by the default local TTS configuration (apt install espeak-ng)." >&2; exit 1;
}
command -v ffmpeg >/dev/null 2>&1 || {
    echo "ffmpeg is required for the default MP3 output (apt install ffmpeg)." >&2; exit 1;
}

RUNTIME_ROOT=$(dirname -- "$OUTBOX_DIR")
PROCESSED_DIR=$RUNTIME_ROOT/processed
ARCHIVE_DIR=$RUNTIME_ROOT/archive
FAILED_DIR=$RUNTIME_ROOT/failed
STATE_DIR=/var/lib/audioconversion
LOG_DIR=/var/log/audioconversion

install -d -m 0755 "$PROGRAM_DIR"
(cd "$SOURCE_DIR" && tar --exclude='./.venv' --exclude='./venv' --exclude='./build' --exclude='./dist' -cf - .) |
    (cd "$PROGRAM_DIR" && tar -xf -)
python3 -m venv "$PROGRAM_DIR/venv"
"$PROGRAM_DIR/venv/bin/pip" install --upgrade pip
"$PROGRAM_DIR/venv/bin/pip" install "${PROGRAM_DIR}[openai]"

install -d -o "$LOGIN_USER" -g "$LOGIN_GROUP" "$INBOX_DIR/normal" "$INBOX_DIR/private" "$INBOX_DIR/cloud"
install -d -o "$LOGIN_USER" -g "$LOGIN_GROUP" "$OUTBOX_DIR" "$PROCESSED_DIR" "$ARCHIVE_DIR" "$FAILED_DIR"
install -d -o "$LOGIN_USER" -g "$LOGIN_GROUP" "$STATE_DIR" "$LOG_DIR"
install -d -m 0750 /etc/audioconversion
cat > /etc/audioconversion/config.toml <<EOF
[paths]
inbox = "$INBOX_DIR"
outbox = "$OUTBOX_DIR"
processed = "$PROCESSED_DIR"
archive = "$ARCHIVE_DIR"
failed = "$FAILED_DIR"
state = "$STATE_DIR"
logs = "$LOG_DIR"

[processing]
mode = "hybrid"
max_retries = 2
retry_initial_seconds = 2
retry_max_seconds = 30

[llm]
preferred = "local"
fallback = "none"
[llm.local]
provider = "ollama"
endpoint = "http://127.0.0.1:11434"
model = "MODEL_NAME"
[llm.cloud]
provider = "none"

[tts]
preferred = "cloud"
fallback = "local"
[tts.local]
provider = "espeak-ng"
voice = "default"
gpu = "auto"
idle_timeout_seconds = 600
[tts.cloud]
provider = "openai"
model = "gpt-4o-mini-tts"
voice = "cedar"

[output]
format = "mp3"
[watcher]
poll_interval_seconds = 2
stability_interval_seconds = 2
stability_checks = 3
[cache]
enabled = true
EOF
chmod 0640 /etc/audioconversion/config.toml
chown root:"$LOGIN_GROUP" /etc/audioconversion/config.toml
cat > /etc/audioconversion/install.conf <<EOF
$PROGRAM_DIR
$LOGIN_USER
$LOGIN_GROUP
EOF
chmod 0600 /etc/audioconversion/install.conf

sed -e "s|@PROGRAM_DIR@|$PROGRAM_DIR|g" -e "s|@SERVICE_USER@|$LOGIN_USER|g" \
    -e "s|@SERVICE_GROUP@|$LOGIN_GROUP|g" "$PROGRAM_DIR/systemd/audioconversion.service" \
    > /etc/systemd/system/audioconversion.service
cat > /usr/local/bin/tts <<EOF
#!/bin/sh
export AUDIOCONVERSION_CONFIG=/etc/audioconversion/config.toml
exec "$PROGRAM_DIR/venv/bin/tts" "\$@"
EOF
chmod 0755 /usr/local/bin/tts

SERVICE_MESSAGE="Systemd is not running. Start in a long-lived terminal with: tts service run"
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable --now audioconversion.service
    SERVICE_MESSAGE="Service enabled and started. Run: tts dashboard"
fi

printf '\nInstallation complete.\n  Program: %s\n  Inbox:   %s\n  Outbox:  %s\n\n%s\n' \
    "$PROGRAM_DIR" "$INBOX_DIR" "$OUTBOX_DIR" "$SERVICE_MESSAGE"
