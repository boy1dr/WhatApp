#!/bin/sh
# Per-user installation of WhatApp. Touches only your home directory.
#
# Creates a uv virtual environment on the distribution's Python (so the GUI can
# import the system GTK bindings), installs WhatApp into it, links the command
# into ~/.local/bin, and adds an application-menu launcher and icon.
#
# Environment overrides:
#   WHATAPP_PYTHON    interpreter to use            (default /usr/bin/python3)
#   WHATAPP_VENV      virtual environment location  (default ~/.venvs/whatapp)
#   WHATAPP_BIN_DIR   where the whatapp link goes   (default ~/.local/bin)
#   WHATAPP_EDITABLE  1 = editable install from this checkout (default), 0 = regular
set -eu
cd "$(dirname "$0")/.."

APP_ID=io.github.boy1dr.WhatApp
PYTHON=${WHATAPP_PYTHON:-/usr/bin/python3}
VENV=${WHATAPP_VENV:-$HOME/.venvs/whatapp}
BIN_DIR=${WHATAPP_BIN_DIR:-$HOME/.local/bin}
EDITABLE=${WHATAPP_EDITABLE:-1}
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}

# Never let uv substitute a downloaded interpreter: it would have no GTK bindings.
export UV_PYTHON_DOWNLOADS=never

fail() { printf 'install-user.sh: %s\n' "$*" >&2; exit 1; }

command -v uv >/dev/null 2>&1 || fail "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
[ -x "$PYTHON" ] || fail "$PYTHON is not an executable Python interpreter (set WHATAPP_PYTHON)"

gtk_check='
import sys
try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
except (ImportError, ValueError) as exc:
    sys.exit(f"cannot import GTK 4 bindings: {exc}")
assert sys.version_info >= (3, 12), f"Python {sys.version.split()[0]} found; WhatApp needs 3.12 or newer"
print(f"Python {sys.version.split()[0]} with GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}")
'

printf 'Checking %s ... ' "$PYTHON"
"$PYTHON" -c "$gtk_check" || fail "install the GTK bindings first (Ubuntu/Debian: sudo apt install python3-gi gir1.2-gtk-4.0)"

if [ -e "$VENV" ]; then
    printf 'Checking existing %s ... ' "$VENV"
    "$VENV/bin/python" -c "$gtk_check" 2>/dev/null \
        || fail "existing environment cannot import GTK 4; remove it (rm -rf '$VENV') or set WHATAPP_VENV"
else
    printf 'Creating %s\n' "$VENV"
    uv venv --python "$PYTHON" --system-site-packages "$VENV"
fi

printf 'Installing WhatApp into %s\n' "$VENV"
if [ "$EDITABLE" = 1 ]; then
    uv pip install --python "$VENV/bin/python" --editable .
else
    uv pip install --python "$VENV/bin/python" .
fi

mkdir -p "$BIN_DIR"
ln -sfn "$VENV/bin/whatapp" "$BIN_DIR/whatapp"
printf 'Linked %s -> %s\n' "$BIN_DIR/whatapp" "$VENV/bin/whatapp"

mkdir -p "$DATA_HOME/applications" "$DATA_HOME/icons/hicolor/scalable/apps"
sed "s|@EXEC@|$VENV/bin/whatapp|g" "data/$APP_ID.desktop" > "$DATA_HOME/applications/$APP_ID.desktop"
cp "data/icons/$APP_ID.svg" "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
printf 'Installed launcher %s\n' "$DATA_HOME/applications/$APP_ID.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$DATA_HOME/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$DATA_HOME/icons/hicolor" 2>/dev/null || true

printf 'Smoke test: whatapp --list --json ... '
"$VENV/bin/whatapp" --list --json >/dev/null && echo ok

echo
echo "WhatApp is installed. Open it from your application menu, or run: whatapp --gui"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "Note: $BIN_DIR is not on your PATH yet; log out and back in, or run $VENV/bin/whatapp directly." ;;
esac
