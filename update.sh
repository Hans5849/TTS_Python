#!/bin/sh
set -eu
SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Update the source checkout as its owner, never git-reset a working tree.
if [ -d "$SOURCE_DIR/.git" ]; then
    git -C "$SOURCE_DIR" pull --ff-only
fi
exec "$SOURCE_DIR/install.sh" --upgrade "$@"
