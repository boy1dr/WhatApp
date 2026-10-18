#!/bin/sh
# Development environment for working from a checkout.
#
# Creates ./.venv on the distribution's Python with --system-site-packages so the
# GUI can import the system GTK bindings, then installs WhatApp into it. It uses
# only what is already on the machine: no downloads, no system changes. If a
# prerequisite is missing it stops and says so.
#
# These are the machine-local uv settings that used to live in pyproject.toml.
# They apply to this script only; `uv run` afterwards reuses the .venv it made.
set -eu
cd "$(dirname "$0")/.."

PYTHON=${WHATAPP_PYTHON:-/usr/bin/python3}
export PYTHONDONTWRITEBYTECODE=1
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PYTHON_DOWNLOADS=never
export UV_OFFLINE=true
export UV_NO_BUILD_ISOLATION=1   # build with the system setuptools/wheel instead of downloading them

"$PYTHON" - <<'PY'
import sys
import gi
import setuptools
import wheel
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
assert sys.version_info >= (3, 12), f"Python {sys.version.split()[0]} found; WhatApp needs 3.12 or newer"
print('System Python', sys.version.split()[0], 'with GTK', Gtk.get_major_version(), Gtk.get_minor_version(), 'available')
PY

if [ -e .venv ]; then
    if ! .venv/bin/python - <<'PY'
import sys
try:
    import gi
    assert sys.version_info >= (3, 12)
    assert sys.base_prefix == '/usr'
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gtk
except (ImportError, ValueError, AssertionError):
    sys.exit(1)
PY
    then
        mkdir -p _to_delete
        backup=$(mktemp -d "$PWD/_to_delete/venv.XXXXXX")
        mv .venv "$backup/venv"
        echo "Previous environment cannot use GTK; moved to $backup/venv (delete it when convenient)"
    fi
fi
if [ ! -e .venv ]; then
    uv venv --python "$PYTHON" --system-site-packages .venv
fi
uv sync
.venv/bin/python -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print("WhatApp development environment ready")'
