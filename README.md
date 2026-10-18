# WhatApp

**Find out what that application actually is.**

WhatApp is a read-only Linux desktop application inspector. It answers the questions that are surprisingly hard to answer from an application menu:

- Where is this application's launcher, and what command does it run?
- Where is the executable?
- Is this a user-level or system-level launcher?
- Are two identically named menu entries really the same application?
- Which Debian package, if any, owns the launcher or executable?
- Why does a launcher appear in - or disappear from - the desktop menu?

It ships as a searchable GTK 4 desktop app and a command-line tool with JSON output you can feed to scripts or an AI assistant.

> **Status:** working, read-only MVP. It installs per-user with one script (below). It has been validated on Ubuntu 24.04 with GNOME; other distributions and desktops have not been tested yet.
>
> **Scope:** WhatApp inventories **desktop launchers** (`.desktop` files), not every installed package or binary on the computer.

<!-- Screenshot: docs/screenshot.png -->

## Why WhatApp?

Linux applications arrive as Debian packages, Flatpaks, Snaps, AppImages, manually copied executables, Wine shortcuts, and more. They install launchers in different places. Multiple entries in an application menu do not always mean multiple installations - and multiple installations do not always appear as separate entries.

WhatApp makes the evidence visible instead of guessing, editing files behind your back, or trying to manage every package format.

**The idea is simple: understand first, change things elsewhere only if you decide to.**

## Features

- **Three-panel GTK interface:** browse launcher categories, select an entry, inspect its details.
- **Searchable inventory:** names, desktop-entry IDs, generic names, and keywords.
- **Launcher visibility:** distinguishes eligible, excluded, shadowed, and uncertain entries.
- **XDG precedence:** shows when one `.desktop` file overrides another, without losing sight of the shadowed copy.
- **Paths you can copy:** the `.desktop` file, the raw `Exec` command, the resolved first executable, and the icon name or path. **Copy All** (or Ctrl+A, Ctrl+C) grabs the whole inspector as text to paste into a bug report or an AI chat.
- **Package evidence:** reports `dpkg` ownership of the launcher and of the executable separately, when identifiable.
- **Honest unknowns:** never assumes `dpkg` ownership proves installation by APT, or that an application updates automatically.
- **CLI and JSON:** inspect by name, exact desktop-entry ID, or exact launcher file; list launchers; optionally run a deeper search.
- **No privileged operations:** inspection never installs, uninstalls, updates, or edits applications or launchers.

## Install (per user)

### Prerequisites

- Linux with a GTK 4 desktop session.
- The distribution's Python 3.12 or newer with PyGObject and GTK 4 bindings. On Ubuntu/Debian:

  ```bash
  sudo apt install python3 python3-gi gir1.2-gtk-4.0
  ```

- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).
- `git`.

**Why the system Python?** PyGObject is a native binding compiled against one specific interpreter. WhatApp therefore runs on the distribution's Python inside a virtual environment that can see the system site-packages, rather than on a separately downloaded Python that cannot import GTK. The install script does not install system packages for you.

### Install

```bash
git clone https://github.com/boy1dr/WhatApp.git
cd WhatApp
sh scripts/install-user.sh
```

The script:

1. checks that the system Python can import GTK 4 (and stops with a message if not);
2. creates `~/.venvs/whatapp` with `uv venv --system-site-packages` on that Python;
3. installs WhatApp into it from the clone (editable by default, so `git pull` updates the install);
4. links `~/.local/bin/whatapp` to the environment's `whatapp` command;
5. installs an application-menu launcher and icon under `~/.local/share`;
6. runs `whatapp --list --json` once as a smoke test.

Nothing is written outside your home directory and no system package is touched. Environment variables adjust the locations: `WHATAPP_PYTHON` (default `/usr/bin/python3`), `WHATAPP_VENV` (default `~/.venvs/whatapp`), `WHATAPP_BIN_DIR` (default `~/.local/bin`), and `WHATAPP_EDITABLE=0` for a regular, non-editable install.

Then open **WhatApp** from your application menu, or run:

```bash
whatapp --gui
whatapp firefox
whatapp --list
```

If `whatapp` is not found, `~/.local/bin` is probably not on your `PATH` yet; log out and back in, or use `~/.venvs/whatapp/bin/whatapp` directly.

### Uninstall

```bash
sh scripts/uninstall-user.sh        # removes the launcher, icon and ~/.local/bin link
sh scripts/uninstall-user.sh --all  # also deletes ~/.venvs/whatapp
```

## Command-line reference

| Command | Purpose |
| --- | --- |
| `whatapp --gui` | Open the graphical launcher browser |
| `whatapp firefox` | Search launchers by name, ID, generic name, or keywords; compact summary per match |
| `whatapp -v firefox` | The same, with every inspector field (what the GUI's Copy All gives you) |
| `whatapp --json firefox` | Machine-readable search results |
| `whatapp --deep-search firefox` | Also search executable paths and full launch commands |
| `whatapp --list` | List preferred, menu-eligible launchers and uncertain entries |
| `whatapp --list --include-excluded` | Also show excluded and shadowed launchers |
| `whatapp --list --json` | Structured inventory data and counts |
| `whatapp --desktop-id org.gnome.Calculator.desktop` | Inspect an exact desktop-entry ID, including shadowed copies |
| `whatapp --desktop-file /usr/share/applications/example.desktop` | Inspect one exact `.desktop` file, even if shadowed |

The exact selectors also work with `--json`. Unlike a name search, they never silently select a similarly named application.

By default each match is a short block — name, ID, user/system level and visibility on one line, then the launcher path, the raw `Exec`, the resolved executable (flagged when it is a wrapper such as `env` or `wine`), the dpkg owners, and any issues. `-v` / `--verbose` prints every inspector field instead, the same text the GUI's Copy All produces. Output is coloured when stdout is a terminal and plain when piped (`NO_COLOR=1` turns colour off, `CLICOLOR_FORCE=1` turns it on). Values are shown exactly as they appear in the launcher file, with only control characters escaped.

### JSON for scripts and AI assistants

The JSON output is meant to be useful without screen scraping. Search results carry a `schema_version` (currently `1`) and structured matches; inventory output has a `launchers` array, inventory metadata, and counts.

Useful fields include launcher and executable paths, package ownership, installation-method uncertainty, visibility, precedence, notes, issues, and search match reasons. Unknown information is represented explicitly instead of guessed.

```bash
whatapp --json --desktop-id org.gnome.Calculator.desktop
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | Successful inspection (or a completed inventory, even an empty one) |
| `1` | No match, or the requested file does not exist |
| `2` | Invalid arguments or invalid target |
| `3` | Requested target could not be read, or the GUI cannot start |

A successful scan can still contain informational notes or issues (for example, an incomplete scan); check the JSON fields rather than treating exit code `0` as proof that every detail was resolved.

The full field-by-field description of the output, precedence rules, visibility rules, and inventory counts is in [`docs/behavior.md`](docs/behavior.md).

## Understanding the results

- **Launcher vs application:** a `.desktop` file is an instruction for the desktop environment, not necessarily a separate installed program.
- **User-level vs system-level:** launchers commonly live under `~/.local/share/applications/` or `/usr/share/applications/`. User-level entries override system entries through XDG precedence.
- **Excluded vs broken:** `NoDisplay=true`, `Hidden=true`, desktop restrictions, and `TryExec` affect visibility in different ways. An excluded entry is not automatically broken.
- **Package owner vs installer:** a file owned by a Debian package does **not** establish whether it came from an Ubuntu repository, a vendor repository, or a downloaded `.deb`.
- **Resolved executable vs final program:** for wrapper commands such as `env ... wine ...`, WhatApp resolves `/usr/bin/env`, **not** the underlying Windows executable, and says so in a note (the owner of `env` is `coreutils`, which tells you nothing about the app). Read the raw `Exec` command too.

WhatApp reports eligibility according to desktop-entry rules; it cannot guarantee exactly how every desktop menu presents an entry.

## What WhatApp deliberately does not do

- Remove duplicate applications or launchers
- Edit `.desktop` files or icons
- Install, update, or uninstall packages
- Determine with certainty how every program was originally installed or updated
- Enumerate every package, binary, AppImage, Flatpak, or Snap that lacks a discoverable launcher
- Modify your desktop environment's menus

This is a deliberate product boundary, not a missing cleanup button. WhatApp gives you information you can review, copy, and use with tools you trust. Third parties can build desktop integrations (right-click actions and the like) on the exact-ID and exact-file CLI selectors.

## Development

The CLI and the unit tests are standard-library only; GTK is imported only by `--gui`. To work from a checkout:

```bash
sh scripts/setup-dev.sh                              # creates .venv on the system Python, offline
uv run python -m unittest discover -s tests -v       # 57 tests, no GTK or display needed
uv run whatapp --gui
uv run python scripts/validate-gui.py                # opt-in smoke test that opens a real window
```

`scripts/setup-dev.sh` creates `.venv` with `--system-site-packages` so the GUI can import the distribution's GTK bindings, and keeps uv's cache inside the project (`.uv-cache/`). It installs nothing system-wide and downloads nothing. Without it, a plain `uv run` would create an environment that cannot see GTK.

You can also skip the environment entirely for the CLI and tests:

```bash
PYTHONPATH=src python3 -m whatapp --json firefox
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The CLI and GUI share one inspection engine; the GTK front end (`gui.py`) is a viewer over the data from `cli.py`, with GTK-free presentation helpers in `browser.py`. Keep it that way: no second scanner in the GUI.

- Behaviour and JSON schema: [`docs/behavior.md`](docs/behavior.md)
- History: [`CHANGELOG.md`](CHANGELOG.md)

## Philosophy

**WhatApp helps you understand what's installed on your Linux desktop, where it lives, and how it launches - without changing your system.**

When Linux gets confusing, sometimes the most useful button isn't **Fix**. It's **Show me what's actually going on.**

## License

[MIT](LICENSE).
