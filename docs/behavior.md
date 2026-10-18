# WhatApp behaviour reference

This document describes exactly what the inspection engine does and what its
output means. It is the specification the unit tests are written against. For
installation and everyday usage see the [README](../README.md); for how the
behaviour grew, see the [CHANGELOG](../CHANGELOG.md).

Commands are shown as `whatapp ...`. From a source checkout use `uv run whatapp ...`
or `PYTHONPATH=src python3 -m whatapp ...`.

## Discovery scope

WhatApp searches recursively under `$XDG_DATA_HOME/applications` (default
`~/.local/share/applications`) and each `$XDG_DATA_DIRS` entry's `applications`
directory (default `/usr/local/share:/usr/share`). Relative XDG paths are ignored
with a diagnostic. Missing directories are normal. Symlinked subdirectories are
not followed and are reported; symlinked launcher files are read.

Repeated directories are deduplicated. Same-ID launchers in different locations
are retained and flagged, including hidden entries. Precedence is computed before
query filtering, so a non-matching mask still takes precedence over the copy it
masks.

## Searching

Ordinary queries are case-insensitive substrings of the unlocalized application
name, desktop-entry ID, GenericName, or individual Keywords. Names and IDs rank
first, then GenericName/Keywords; exact matches precede substring matches within
each tier. All matching launchers are retained. Each ID's copies share its best
relevance tier, preserving directory precedence within that tier, then sorted
traversal order. Matching any copy includes all copies with that ID, even an
unnamed user mask.

Non-Application entries are excluded unless they share an ID with a matched
application. Malformed entries remain searchable by ID (or by path with deep
search) and generate scan diagnostics even if they do not match.

### Deep search

Ordinary searches do not inspect Exec, executable paths, or the desktop file's
parent directories. For example, `whatapp claude` does not match unrelated Wine
utilities merely because their commands contain `WINEPREFIX=/home/example/.wine-claude`.

`whatapp --deep-search claude` additionally searches full raw Exec commands,
resolved executable paths, and desktop-entry paths. Deep-only matches rank after
application metadata. Executable resolution still examines only the first program,
without running it or following wrappers; the raw command remains searchable even
when executable resolution fails.

Search is substring-based, without fuzzy matching or semantic inference. Results
can be broad when names, IDs, or author-supplied keywords are broad.

Search-related JSON fields:

- Report `search_mode`: `application` or `deep`.
- Match `generic_name` and `keywords`: decoded base metadata, or null when absent.
  Locale selection is not performed.
- Match `match_reasons`: objects with `field`, `match` (`exact` or `substring`),
  and the matched `value`. All matching fields are reported. Keyword reasons
  identify the individual matching keyword.
- Copies retained only for precedence context use `match: "related_copy"`,
  `field: "desktop_entry_id"`, and `source_paths` identifying the directly
  matching copies. They are not represented as direct text matches.

## Exact selectors

```sh
whatapp --json --desktop-id org.example.App.desktop
whatapp --json --desktop-file /usr/share/applications/org.example.App.desktop
```

`--desktop-id` requires an exact, case-sensitive ID including `.desktop`, without
a directory path. It returns all discovered copies of that ID, retaining their
precedence and visibility. Nested IDs use hyphens, for example
`wine-Programs-Vendor-App.desktop`.

`--desktop-file` returns only the requested regular `.desktop` file, including
shadowed files. Relative paths are made absolute from the working directory;
symlink paths are retained rather than replaced by their targets. Existing XDG
copies are scanned to determine precedence, but never substituted for the requested
file. Files outside configured XDG application directories have a null ID and
unknown precedence/visibility. Malformed, unreadable, or non-application files
produce errors; minimal `Hidden=true` masks are accepted. This is not a full
desktop-entry validator, and missing executable programs remain inspection issues.

Choose exactly one positional query, `--desktop-id`, `--desktop-file`, or `--list`.
`--deep-search` applies only to positional queries. Both selectors support human
and JSON output. `search_mode` is `desktop_id` or `desktop_file`; `query`
contains the supplied selector value. `match_reasons` identifies the exact ID/path.

## Inventory (`--list`)

`whatapp --list` inventories **desktop launchers**, not all installed software.
It uses the same XDG discovery, precedence, visibility, executable resolution,
and Debian ownership inspection as searches and exact selectors. Each distinct
launcher path remains a separate entry: no application deduplication or grouping.

By default it returns preferred, menu-eligible entries plus entries whose effective
visibility or precedence is unknown. `--include-excluded` returns all discovered
application launcher candidates, including Hidden masks, NoDisplay entries,
desktop restrictions, and shadowed copies. Known non-Application entries are not
launcher candidates; malformed or unknown-type entries remain candidates so that
uncertain data is not silently lost.

Both modes sort case-insensitively by display name, using desktop-entry ID and exact
path as deterministic tie-breakers; missing names sort last. Human output has one
line per launcher with its name, ID, resolved executable (or unknown), and status.
JSON uses `launchers` instead of the search/selector `matches` array; each element
contains the same inspection fields, including exact paths and ownership.
`match_reasons` is empty because inventory does not perform text matching.

Inventory JSON has `schema_version: 1`, `search_mode: "inventory"`, `query: null`,
`search_directories`, `issues`, `notes`, and `error`. Its `inventory` metadata
includes scope, filter choice, `counts`, machine-readable count definitions,
`scan_complete`, `desktop_files_scanned`, and `entries_with_read_or_parse_errors`.
`scan_complete` describes directory traversal only; parsing/reading failures are
counted separately and reported in issues. Counts cover observed files only, so an
incomplete scan cannot give an authoritative system total.

| Count | Definition |
| --- | --- |
| `total_discovered` | Distinct application launcher candidate paths, before inventory filtering. |
| `included` | Number of entries in `launchers` for this request. |
| `eligible` | Preferred entries with eligible visibility. |
| `excluded` | Entries with Hidden=true, NoDisplay=true, desktop_allowed=false, or shadowed precedence; each path counted once. |
| `shadowed` | Entries with shadowed precedence; a subset of excluded. |
| `uncertain` | Entries with unknown precedence or visibility; may overlap excluded. |
| `omitted` | total_discovered minus included. |

The categories are not all additive. During an incomplete scan, for example, a
NoDisplay entry can be both excluded by its properties and uncertain in precedence.
Uncertain entries are retained by default even in that case. In normal complete
scans, known exclusions are omitted unless `--include-excluded` is supplied.
`desktop_files_scanned` also includes known non-Application files, unlike
`total_discovered`.

Missing TryExec programs remain uncertain because desktop implementations may omit
them. Menu eligibility does not guarantee that an app can launch or appears in a
particular menu. Missing Exec programs and package ownership do not change inventory
visibility classification; these remain diagnostics in the inspection data.

`--list` is mutually exclusive with queries and exact selectors, and cannot combine
with `--deep-search`. `--include-excluded` requires `--list`. A completed inventory
request exits 0, including empty or partial inventories; inspect metadata and issues
for completeness. Invalid combinations exit 2 using the standard error format.

Only returned entries undergo executable/ownership enrichment. Inventory batches
read-only `dpkg-query --search` operands in groups of at most 128 unique paths and
reuses each result within the invocation. Shared executable paths are queried once;
results and diagnostics are still attached to each launcher separately. Partial
dpkg results retain exact successful ownership matches. No persistent cache is used.

## Precedence and visibility

Each match carries these fields in addition to the basic inspection fields:

- `desktop_entry_properties`: raw strings for `Hidden`, `NoDisplay`, `OnlyShowIn`,
  `NotShowIn`, and `TryExec`; absent keys are null.
- `precedence`: `status` is `preferred`, `shadowed`, or `unknown`.
  `directory_index` is the zero-based index in `search_directories`.
  `preferred_path` identifies the highest-priority copy, even a Hidden mask;
  `candidate_paths` lists the highest-priority candidates. An ambiguous winner
  leaves `preferred_path` null. User data comes first, then XDG_DATA_DIRS order.
- `visibility`: interpreted booleans (`hidden`, `no_display`), desktop lists
  (`only_show_in`, `not_show_in`), `current_desktops`, `desktop_allowed`, and
  a `try_exec` object containing its value, status, and executable path.
  Absent booleans default to false for valid entries; malformed booleans are null.
  `status` is `eligible`, `excluded`, `shadowed`, or `unknown`, with machine-readable
  `reasons`. This describes menu eligibility under these gates, not launchability
  or a guarantee that a particular desktop menu displays the entry.
- `notes` at report and match level: ordinary observations, separate from `issues`
  (malformed data, failed inspection, or unresolved precedence). Hidden, NoDisplay,
  desktop restrictions, and missing optional icons are not errors.

`Hidden=true` on the preferred file masks all lower-priority copies without falling
back. `NoDisplay=true` excludes an otherwise preferred entry from menus. Desktop
names are case-sensitive and considered in `XDG_CURRENT_DESKTOP` colon-list order.
Restrictions without desktop context yield unknown visibility. Missing TryExec
programs have `try_exec.status: "missing"`; visibility is unknown because the spec
allows, but does not require, a desktop to omit them. TryExec is never executed.

Same-ID paths within the highest-priority applications directory (for example
`vendor/editor.desktop` and `vendor-editor.desktop`) have undefined selection and
are reported as unknown. Incomplete directory scans conservatively make precedence
unknown. Malformed preferred files are not silently replaced by lower-priority ones;
their visibility is uncertain. A definite Hidden/NoDisplay exclusion can still be
reported when another visibility property is invalid, with the problem in `issues`.

Rules follow the [Desktop Entry keys](https://specifications.freedesktop.org/desktop-entry/latest/recognized-keys.html)
and [desktop-entry ID precedence](https://specifications.freedesktop.org/desktop-entry/latest/file-naming.html).

## JSON schema and exit codes

Search and selector JSON has `schema_version: 1`, `query`, `search_mode`,
`matches`, `search_directories`, `issues`, `notes`, and `error`. Each match
includes `display_name`, `desktop_entry_path`, `exec`, `executable_path`, `icon`,
`desktop_entry_id`, `generic_name`, `keywords`, `package_ownership`,
`installation_method`, `desktop_entry_properties`, `precedence`, `visibility`,
`match_reasons`, `issues`, and `notes`. Unknown scalar values and unknown ownership
are `null`. Identified ownership is an array of package names, separately keyed by
`desktop_entry` and `executable`. An empty `matches` array means no matches;
check `issues` for incomplete scans.

| Code | Meaning |
| --- | --- |
| 0 | At least one match inspected, a completed inventory request (even empty), or `--help`; inspect `issues` for unresolved fields or incomplete scans. |
| 1 | No match, or the exact requested file does not exist. |
| 2 | Invalid/missing/conflicting arguments, invalid target format, malformed selected file, or non-application target. |
| 3 | Requested target cannot be read or accessed, or the GUI cannot start (no GTK or no display). |

`error` is null on success, otherwise an object with `code`, `message`, and
`exit_code`. Errors produce one JSON report on stdout and no error text on stderr.
Human-readable errors go to stderr. Argument errors have null query/mode when
parsing cannot determine them. `--help` is ordinary help text. Missing/invalid
exact files return an empty matches array. Exact-ID inspection retains every copy
even when one is malformed or unreadable, but exits 2 or 3 and includes an error;
it never silently promotes a lower-priority copy.

### Human-readable output

Without `--json`, a search or exact selector prints a compact block per match:

```
<Display name>  (<desktop-entry ID>)  <User-level|System-level>, <visibility> (<reasons>)
  Launcher:    <desktop file path>
  Exec:        <raw Exec, as written in the file>
  Executable:  <resolved first program>  [(a wrapper: the real program is in Exec)]
  dpkg owner:  launcher <packages|none found|unknown>; executable <...>
  Shadowed by: <preferred path>          (only for shadowed copies)
  Issues:      <issues joined by "; ">   (only when there are any)
```

followed by `N launchers matched. Add -v for every field, or --json ...`. The
visibility word is the entry's `visibility.status`; reasons equal to the status
itself are not repeated. Precedence is implied: `shadowed` appears as the visibility
status with a `Shadowed by:` line, and `preferred` is the default.

`-v` / `--verbose` prints every field instead, as the same blocks the GUI inspector
shows (see *The GUI* below): a `WhatApp inspector: <id>` title, a `Matched on` block
listing the fields and match kinds, one block per field, and the footer once after the
last match. With `--list`, `-v` appends these blocks for every listed launcher after
the table. `--list` on its own prints a one-line-per-launcher table followed by
counts. Report-level issues and notes come last.

When the reader closes the pipe early (`whatapp ... | head`), the command exits 0
without a traceback.

Values are printed as they are in the launcher file (no quoting, no JSON escaping),
except that control and other non-printable characters are shown as `\xNN` / `\uNNNN`
escapes, so a launcher cannot inject terminal sequences or hide text.

Colour is used only when stdout is a terminal: bold field titles, cyan match titles,
green for `eligible`/`preferred`/`available`, yellow for `excluded`/`shadowed`/
`unknown`/`missing`, red for issues, dim for notes, `(not set)`, `None`, `None found`
and the footer. Colour never carries information that the text does not. Piped
output has no escape codes; `NO_COLOR` or `TERM=dumb` turns colour off and
`CLICOLOR_FORCE=1` (or `FORCE_COLOR`) turns it on regardless.

## The GUI

`whatapp --gui` opens a window with three resizable panels: navigation, a searchable
alphabetical launcher list, and a scrollable inspector. Sidebar counts describe the
full loaded inventory; search narrows the selected category without changing those
global counts.

- **All launchers** includes every inventory candidate, even excluded and shadowed
  files. **Menu applications** uses the engine's eligible classification.
- **Excluded**, **Shadowed**, and **Uncertain** use the overlapping inventory
  categories defined above. In particular, Shadowed is a subset of Excluded.
  Uncertain entries remain browsable.
- Search uses the same name/ID/GenericName/Keywords matching as the CLI, without
  searching Wine-prefix values or arbitrary Exec arguments.
- The list shows the name, the resolved theme or file icon when available
  (otherwise a generic icon), desktop-entry ID, visibility/precedence, and location level.
- The inspector is one read-only text view, so a selection can span every field:
  drag with the mouse, or click in the inspector and press Ctrl+A then Ctrl+C.
  **Copy All** copies the same text (`inspector_text()` in `browser.py`). **Copy Path**
  copies only the exact selected desktop file path, including a shadowed path.
  Filtering to no results clears the selection and disables both buttons.
- The copied text is written to be pasted somewhere else without the window, for
  example into a bug report or an AI chat. It starts with `WhatApp inspector: <id>`,
  has one field per block (title line, value below, blank line between fields), and
  ends with a footer stating that WhatApp is read-only, that a launcher is not
  necessarily the application, and that dpkg ownership does not establish how
  something was installed. Null values are rendered precisely: `(not set)` for a key
  absent from the file or environment (Icon, TryExec, OnlyShowIn, NotShowIn, Current
  desktops, ...), `None found` when dpkg answered that no package owns the path,
  and `Unknown` only for what WhatApp could not determine. List values are one
  `- item` per line; an empty list is `None`.
- User-level/system-level describes file location, not installation provenance:
  the XDG user applications directory or a path under the home directory is
  user-level. Other discovered paths are system-level. No APT provenance is inferred.
- Launcher and executable package owners are shown separately. Missing information
  is **Unknown**. Resolution identifies only the first Exec program; a Wine launcher
  may resolve to `env`, whose package is not the wrapped app's package.
- **Refresh** rereads the inventory. A short-lived worker thread handles inspection
  while GTK remains responsive; UI updates happen on the GTK thread. There is no
  daemon, background service, file watching, or stored GUI state. Partial scans and
  failures are shown, with previous results retained on refresh failure. Category
  counts are snapshots, not live guarantees.

GUI code is in `gui.py`; GTK-independent presentation helpers are in `browser.py`.
Both use the shared inspection and classification functions. The GUI does not
launch selected apps, edit files, install/remove packages, or add discovery backends.
`--gui` is exclusive with CLI selectors and output flags. Missing GTK or display
access produces a clear error with exit code 3.

`scripts/validate-gui.py` is an opt-in native smoke test for a desktop session. It
opens a real window, loads the real inventory, verifies the category filters and a
selection, checks that the inspector text matches `inspector_text()`, verifies Copy
Path and Copy All via clipboard round trips, then closes. It temporarily changes clipboard text and restores previous text where
available (non-text clipboard formats are not preserved). It is intentionally
separate from the display-independent unit suite.

## Limitations

- Uses the base `Name`, not locale selection. The CLI reports the icon name or path
  as written; only the GUI performs icon-theme lookup, for display.
- Conservatively resolves only the first Exec token against the current process PATH.
  When that program is a known wrapper (`env`, `sh`, `bash`, `wine`, `flatpak`, `snap`,
  `bwrap`, `firejail`, `gtk-launch`, `xdg-open`, ...), a note says so, because the
  wrapper's package owner (for example `coreutils` for `env`) says nothing about the
  launched application. This may differ from a desktop session's PATH. It never launches applications,
  expands arguments, evaluates shell expressions, or traces wrappers such as env,
  shells, Flatpak, or Snap. Relative executable paths remain unresolved. It is not
  a full desktop-entry validator.
- Evaluates visibility against the current environment only; D-Bus activation,
  desktop actions, menu layout rules, and desktop-specific integrations are out of
  scope. Unusual escaped desktop-name lists are reported as unsupported.
- Runs only `dpkg-query --search` for exact reported launcher and executable paths.
  Ownership can be unknown for local files, generated launchers, symlinks, or
  non-Debian systems. Symlink targets and usr-merge aliases are not traced.
  A package association does **not** establish APT usage or original installation
  method; `installation_method` always remains null.
- No sudo, package management operations, or desktop-file edits. The inspector
  does not write files. Fixtures and mocked ownership tests do not depend on host apps.
