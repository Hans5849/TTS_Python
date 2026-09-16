#!/bin/sh
# Runs only on disposable CI hosts. It does not contact OpenAI or download a model.
set -eu
cd "$(dirname "$0")/.."
sudo sh install.sh --yes --user runner --no-start
sudo -u runner sh -c 'printf "protected input\n" > /srv/tts/inbox/normal/keep.txt'
sudo -u runner sh -c 'printf "protected output\n" > /srv/tts/outbox/keep.txt'
sudo sh install.sh --fresh --yes --no-start
sudo sh uninstall.sh --dry-run
sudo sh uninstall.sh --apply
test "$(cat /srv/tts/inbox/normal/keep.txt)" = 'protected input'
test "$(cat /srv/tts/outbox/keep.txt)" = 'protected output'
test ! -e /usr/local/bin/tts
test -x /usr/local/bin/speech-common
sudo uninstall-speech --app all --apply --purge-shared
test -f /srv/tts/outbox/keep.txt
