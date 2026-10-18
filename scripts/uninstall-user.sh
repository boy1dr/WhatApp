#!/bin/sh
# Remove a per-user WhatApp installation made by scripts/install-user.sh.
#
#   sh scripts/uninstall-user.sh         removes the launcher, icon and ~/.local/bin link
#   sh scripts/uninstall-user.sh --all   also deletes the virtual environment
#
# Honours the same WHATAPP_VENV and WHATAPP_BIN_DIR overrides as the installer.
set -eu

APP_ID=io.github.boy1dr.WhatApp
VENV=${WHATAPP_VENV:-$HOME/.venvs/whatapp}
BIN_DIR=${WHATAPP_BIN_DIR:-$HOME/.local/bin}
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

remove() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        rm -f "$1"
        printf 'Removed %s\n' "$1"
    fi
}

remove "$DATA_HOME/applications/$APP_ID.desktop"
remove "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
remove "$BIN_DIR/whatapp"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$DATA_HOME/applications" || true

if [ "${1:-}" = "--all" ]; then
    if [ -d "$VENV" ]; then
        rm -rf "$VENV"
        printf 'Removed %s\n' "$VENV"
    fi
else
    [ -d "$VENV" ] && printf 'Kept %s (pass --all to delete it)\n' "$VENV"
fi
echo "WhatApp uninstalled."
