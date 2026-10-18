# Changelog

WhatApp was built in a series of milestones before it had a repository. The
entries below reconstruct that history from the development notes; the JSON
`schema_version` has stayed at `1` throughout, with fields only added.

## Unreleased

- Inspector: **Copy All** button, and the panel is now a single read-only text view
  so Ctrl+A / Ctrl+C (or a mouse drag) selects across every field. **Copy Path** is
  unchanged.
- The inspector text is self-describing for pasting elsewhere: a `WhatApp inspector:`
  title line, a read-only/interpretation footer, `(not set)` and `None found`
  distinguished from `Unknown`, and list values as `- ` items.
- New note (CLI, JSON and GUI) when the resolved executable is a wrapper such as
  `env`, `wine`, `flatpak` or `snap`: its package owner does not describe the
  launched application.
- CLI: `whatapp <query>` and the exact selectors print a compact summary per match
  (name, ID, level and visibility; launcher path, raw Exec, resolved executable with a
  wrapper flag, dpkg owners, issues). New `-v`/`--verbose` prints every inspector
  field, the same blocks as the GUI. Inline JSON is gone from human output; values
  appear as written in the file, with only non-printable characters escaped. Colour
  (bold titles, green/yellow status, red issues) when stdout is a terminal; off when
  piped or with `NO_COLOR`.
- `whatapp ... | head` no longer prints a BrokenPipeError traceback; the console
  script entry point is now `whatapp.cli:entry`.
- First public repository: single user-facing README, behaviour reference moved
  to `docs/behavior.md`, MIT licence, project metadata, and a per-user install
  script (`scripts/install-user.sh`) with an application-menu launcher and icon.
- Application ID changed from `org.whatapp.LauncherBrowser` to `io.github.boy1dr.WhatApp`.
- Machine-specific uv settings (offline mode, project-local cache, no build isolation)
  moved out of `pyproject.toml` into `scripts/setup-dev.sh`.
- Test fixtures no longer contain a developer's home path.

## Milestone 2.0 — Read-only GUI

- `whatapp --gui`: GTK 4 window with navigation, searchable launcher list, and
  inspector. Categories: All, Menu applications, Excluded, Shadowed, Uncertain.
- Selectable inspector values and a **Copy Path** button.
- Inventory loads on a worker thread; **Refresh** rereads it.
- `--gui` is exclusive with CLI selectors and output flags; missing GTK or display
  access exits 3.
- `scripts/validate-gui.py`: opt-in native smoke test.
- GTK-free presentation helpers in `browser.py`, with unit tests.

## Milestone 1.4 — Desktop launcher inventory

- `whatapp --list` and `--list --include-excluded`, with human and JSON output.
- Inventory metadata: `counts` with machine-readable definitions, `scan_complete`,
  `desktop_files_scanned`, `entries_with_read_or_parse_errors`.
- Batched read-only `dpkg-query --search` (at most 128 paths per call), with
  results reused within an invocation.
- A completed inventory request exits 0 even when empty or partial.

## Milestone 1.3 — Exact launcher selection

- `--desktop-id` inspects all copies of an exact, case-sensitive desktop-entry ID.
- `--desktop-file` inspects exactly one `.desktop` file, including shadowed files
  and files outside the XDG directories.
- Structured `error` object in JSON; errors go to stdout as one JSON report.
- No-match searches now exit 1 (previously 0); exit codes 0–3 defined.

## Milestone 1.2 — Search relevance

- Ordinary searches match only name, ID, GenericName, and Keywords; Exec and paths
  are no longer searched by default.
- `--deep-search` adds raw Exec commands, resolved executable paths, and
  desktop-entry paths, ranked after application metadata.
- `search_mode`, `generic_name`, `keywords`, and `match_reasons` added to JSON.

## Milestone 1.1 — Precedence and visibility

- XDG desktop-entry ID precedence across user and system directories, including
  `Hidden=true` masks and same-directory ID collisions.
- Visibility interpretation for `Hidden`, `NoDisplay`, `OnlyShowIn`, `NotShowIn`,
  and `TryExec` against `XDG_CURRENT_DESKTOP`; `TryExec` is never executed.
- `desktop_entry_properties`, `precedence`, `visibility`, and `notes` added to JSON.

## Milestone 1.0 — Initial inspector

- Search launchers by name, resolve the first Exec program without running it,
  report `dpkg` ownership of the launcher and executable, and emit JSON with
  `schema_version: 1`.
