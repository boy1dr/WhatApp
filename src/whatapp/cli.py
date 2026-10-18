"""Small XDG launcher inspector. Never executes a desktop entry's command."""

import argparse
import configparser
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from .visibility import PROPERTY_KEYS, assign_precedence, interpret_visibility
from .inventory import select_inventory


class InspectionError(Exception):
    def __init__(self, message, code="invalid_arguments", exit_code=2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InspectionError(message)


def error_report(message, code, exit_code, query=None, mode=None):
    return {"schema_version": 1, "query": query, "matches": [],
            "search_mode": mode, "search_directories": [], "issues": [], "notes": [],
            "error": {"code": code, "message": message, "exit_code": exit_code}}


def application_dirs(env, issues):
    home = env.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    if not os.path.isabs(home):
        issues.append("Relative XDG_DATA_HOME ignored; using default.")
        home = str(Path.home() / ".local/share")
    bases = [home] + (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    result = []
    for base in bases:
        if not os.path.isabs(base):
            issues.append(f"Ignoring non-absolute XDG data directory: {base!r}")
            continue
        path = Path(os.path.normpath(base)) / "applications"
        if path not in result:
            result.append(path)
    return result


def unescape(value):
    return re.sub(r"\\([sntr\\])", lambda m: {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}[m[1]], value)


def keywords(value):
    """Decode a desktop string list without splitting escaped semicolons."""
    if value is None:
        return None
    # Tokenize escapes together so an escaped backslash before ';' still ends
    # an item, while '\\;' belongs to the item itself.
    parts, current = [], []
    for token in re.findall(r"\\.|[^\\]|\\$", value):
        if token == ";":
            if current:
                parts.append("".join(current))
            current = []
        else:
            current.append(";" if token == r"\;" else unescape(token))
    if current:
        parts.append("".join(current))
    return parts


def search_reasons(entry, query, deep_search=False):
    """Return explicit evidence and a small, deterministic relevance tier."""
    reasons, best = [], 99
    fields = [("display_name", [entry["display_name"]], 0),
              ("desktop_entry_id", [entry["desktop_entry_id"]], 0),
              ("generic_name", [entry["generic_name"]], 2),
              ("keywords", entry["keywords"] or [], 2)]
    if deep_search:
        fields.extend((key, [entry[key]], 4) for key in
                      ("desktop_entry_path", "exec", "executable_path"))
    for field, values, tier in fields:
        for value in values:
            if value and query.casefold() in value.casefold():
                exact = query.casefold() == value.casefold()
                reasons.append({"field": field, "match": "exact" if exact else "substring", "value": value})
                best = min(best, tier + (not exact))
    return reasons, best


# Programs that launch something else. Their dpkg owner says nothing about the
# application, which is the most common way a reader misinterprets the report.
WRAPPER_PROGRAMS = frozenset({"env", "sh", "bash", "dash", "zsh", "nohup", "wine", "wine64",
                              "flatpak", "snap", "bwrap", "firejail", "gtk-launch", "xdg-open"})


def resolve_exec(raw, env, issues, notes=None):
    if not raw:
        issues.append("Exec is missing; executable is unknown.")
        return None
    # Desktop Exec syntax is not shell syntax. Parse only the first token,
    # conservatively; arguments and field codes are never expanded or executed.
    value = unescape(raw)
    if value.startswith('"'):
        match = re.match(r'^"((?:[^"\\]|\\["`$\\])*)"(?=\s|$)', value)
        if not match:
            issues.append("Exec has unsupported or malformed executable quoting.")
            return None
        command = re.sub(r'\\(["`$\\])', r'\1', match[1])
    else:
        command = value.split()[0] if value.split() else ""
        if re.search(r'''["'\\><~|&;$*?#()`]''', command):
            issues.append("Exec executable contains unsupported unquoted characters.")
            return None
    if not command or "=" in command or re.search(r"%(?!%)", command.replace("%%", "")):
        issues.append("Exec executable is empty or contains unresolved syntax.")
        return None
    command = command.replace("%%", "%")
    if "/" in command and not os.path.isabs(command):
        issues.append("Relative executable paths are not resolved.")
        return None
    resolved = shutil.which(command, path=env.get("PATH", os.defpath))
    if resolved is None:
        issues.append("Exec executable was not found or is not executable in the current PATH.")
    else:
        resolved = os.path.abspath(resolved)
        if notes is not None:
            notes.append("Resolved only the first Exec program using the current PATH; wrappers are not traced.")
            if os.path.basename(resolved) in WRAPPER_PROGRAMS:
                notes.append(f"Resolved executable '{os.path.basename(resolved)}' is a wrapper: its package owner "
                             "describes the wrapper, not the launched application. Read the raw Exec command.")
    return resolved


def package_owners(path, issues, notes=None):
    if path is None:
        return None
    # dpkg-query treats operands as patterns: escape glob metacharacters, then
    # require an exact path in its output. Never use shell=True.
    pattern = "".join("\\" + c if c in "\\*?[]" else c for c in path)
    try:
        result = subprocess.run(
            ["dpkg-query", "--search", pattern], capture_output=True,
            text=True, timeout=5, env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        issues.append(f"Package ownership unavailable for {path}: {exc}")
        return None
    owners = set()
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            owner, separator, owned_path = line.partition(": ")
            if separator and owned_path == path and not owner.startswith("diversion "):
                owners.update(owner.split(", "))
    if not owners:
        if result.returncode not in (0, 1):
            issues.append(f"dpkg ownership query failed for {path} (exit {result.returncode}).")
        elif notes is not None:
            notes.append(f"No exact dpkg ownership identified for {path}.")
        return None
    if len(owners) > 1:
        issues.append(f"Multiple dpkg owners reported for {path}.")
    return sorted(owners)


def batch_package_owners(paths):
    """Query each unique returned path once, in bounded read-only dpkg batches.

    Each cache value contains ownership, problems, and informational notes so
    repeated executable paths get the same diagnostics as well as the same owners.
    """
    paths = list(dict.fromkeys(paths))
    cache = {}
    for start in range(0, len(paths), 128):
        batch = paths[start:start + 128]
        owners = {path: set() for path in batch}
        patterns = ["".join("\\" + c if c in "\\*?[]" else c for c in path) for path in batch]
        try:
            result = subprocess.run(["dpkg-query", "--search", *patterns],
                                    capture_output=True, text=True, timeout=5,
                                    env={**os.environ, "LC_ALL": "C"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            for path in batch:
                cache[path] = (None, [f"Package ownership unavailable for {path}: {exc}"], [])
            continue
        # Exit 1 can mean only some operands were unowned. Keep exact successes.
        if result.returncode in (0, 1):
            for line in result.stdout.splitlines():
                owner, separator, path = line.partition(": ")
                if separator and path in owners and not owner.startswith("diversion "):
                    owners[path].update(owner.split(", "))
        for path, packages in owners.items():
            problems, notes = [], []
            if result.returncode not in (0, 1):
                problems.append(f"dpkg ownership query failed for {path} (exit {result.returncode}).")
            elif not packages:
                notes.append(f"No exact dpkg ownership identified for {path}.")
            elif len(packages) > 1:
                problems.append(f"Multiple dpkg owners reported for {path}.")
            cache[path] = (sorted(packages) or None, problems, notes)
    return cache


def read_entry(path, desktop_id, env):
    issues = []
    read_error = None
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",),
                                       comment_prefixes=("#",), strict=True)
    parser.optionxform = str
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        if "Desktop Entry" not in parser:
            raise ValueError("Missing [Desktop Entry] group")
        # Do not inherit INI DEFAULT values: desktop entries have no defaults.
        data = parser._sections["Desktop Entry"]
    except (OSError, UnicodeError, configparser.Error, ValueError) as exc:
        data = {}
        issues.append(f"Cannot parse launcher: {exc}")
        code = "unreadable_target" if isinstance(exc, OSError) else "invalid_target"
        read_error = InspectionError(issues[-1], code, 3 if isinstance(exc, OSError) else 2)
    hidden = data.get("Hidden") == "true"
    if not data.get("Type") and not hidden:
        issues.append("Type is missing; application status is uncertain.")
    name = unescape(data["Name"]) if data.get("Name") else None
    if name is None and not hidden:
        issues.append("Unlocalized display name is missing.")
    properties = {key: data.get(key) for key in PROPERTY_KEYS}
    visibility = interpret_visibility(
        properties, env, issues,
        valid=not issues and data.get("Type") in (None, "Application"),
    )
    notes = []
    if data.get("DBusActivatable") == "true":
        notes.append("D-Bus activation is declared; activation is not evaluated.")
    return {
        "display_name": name, "desktop_entry_path": str(path),
        "generic_name": unescape(data["GenericName"]) if data.get("GenericName") else None,
        "keywords": keywords(data.get("Keywords")),
        "exec": data.get("Exec") or None, "executable_path": None,
        "icon": unescape(data["Icon"]) if data.get("Icon") else None,
        "desktop_entry_id": desktop_id,
        "package_ownership": {"desktop_entry": None, "executable": None},
        "installation_method": None, "issues": issues, "notes": notes,
        "desktop_entry_properties": properties, "visibility": visibility,
        "_application": data.get("Type") in (None, "Application"),
        "_optional_exec": hidden or data.get("DBusActivatable") == "true",
        "_read_error": read_error,
        "_valid_target": (data.get("Type") == "Application" and bool(name)) or hidden,
    }


def inspect(query=None, env=None, owner_lookup=package_owners, *, deep_search=False,
            desktop_id=None, desktop_file=None, list_launchers=False, include_excluded=False):
    if sum(value is not None for value in (query, desktop_id, desktop_file)) + bool(list_launchers) != 1:
        raise InspectionError("Provide exactly one query, --desktop-id, --desktop-file, or --list.")
    if include_excluded and not list_launchers:
        raise InspectionError("--include-excluded requires --list.")
    if deep_search and query is None:
        raise InspectionError("--deep-search requires a positional query.")
    if query is not None and not query.strip():
        raise InspectionError("query must not be blank")
    if desktop_id is not None and (not desktop_id.endswith(".desktop") or
                                  desktop_id == ".desktop" or "/" in desktop_id or
                                  "\x00" in desktop_id):
        raise InspectionError("--desktop-id requires a complete ID ending in .desktop, without a path.")
    env = os.environ if env is None else env
    issues, entries, seen = [], [], set()
    scan_complete = True
    directories = application_dirs(env, issues)
    target = None
    target_path = None
    if desktop_file is not None:
        if not desktop_file.strip() or "\x00" in desktop_file:
            raise InspectionError("--desktop-file requires a nonempty file path.")
        # Keep the selected path, including symlinks, rather than substituting
        # its resolved target or the preferred launcher with the same ID.
        target_path = Path(os.path.abspath(desktop_file))
        try:
            mode = target_path.stat().st_mode
        except FileNotFoundError:
            raise InspectionError(f"Requested launcher does not exist: {target_path}", "not_found", 1)
        except OSError as exc:
            raise InspectionError(f"Cannot inspect requested launcher: {exc}", "unreadable_target", 3)
        if target_path.suffix != ".desktop" or not stat.S_ISREG(mode):
            raise InspectionError("--desktop-file must target a regular .desktop file.", "invalid_target")
        target_id, target_rank = None, None
        for rank, root in enumerate(directories):
            if target_path.is_relative_to(root):
                target_id = str(target_path.relative_to(root)).replace(os.sep, "-")
                target_rank = rank
                break
        target = read_entry(target_path, target_id, env)
        if target["_read_error"]:
            raise target["_read_error"]
        if not target["_valid_target"] or not target["_application"]:
            raise InspectionError(f"Not an application launcher or Hidden mask: {target_path}", "invalid_target")
        target["_rank"] = target_rank
    for rank, root in enumerate(directories):
        def onerror(error):
            nonlocal scan_complete
            if isinstance(error, FileNotFoundError) and error.filename == str(root):
                return
            issues.append(f"Directory scan failed: {error}")
            scan_complete = False
        for directory, subdirs, files in os.walk(root, onerror=onerror, followlinks=False):
            subdirs.sort()
            for subdir in subdirs:
                if (Path(directory) / subdir).is_symlink():
                    issues.append(f"Skipping symlinked directory: {Path(directory) / subdir}")
                    scan_complete = False
            for filename in sorted(files):
                path = Path(directory) / filename
                if path.suffix != ".desktop" or str(path) in seen:
                    continue
                seen.add(str(path))
                entry_id = str(path.relative_to(root)).replace(os.sep, "-")
                entry = target if path == target_path else read_entry(path, entry_id, env)
                if entry is not None:
                    entry["_rank"] = rank
                    entries.append(entry)
                    if any(issue.startswith("Cannot parse") for issue in entry["issues"]):
                        issues.append(f"Unreadable or malformed launcher: {path}")
    if target is not None and target_path.as_posix() not in seen and target["_rank"] is not None:
        entries.append(target)
    assign_precedence(entries, scan_complete)
    if target is not None and target["_rank"] is None:
        target["precedence"] = {"status": "unknown", "directory_index": None,
                                "preferred_path": None, "candidate_paths": []}
        target["visibility"]["status"] = "unknown"
        target["visibility"]["reasons"].insert(0, "outside_xdg_directories")
        target["notes"].append("Outside configured XDG application directories; desktop-entry ID and menu precedence are unknown.")
        entries.append(target)
    inventory = None
    if list_launchers:
        entries, inventory = select_inventory(entries, include_excluded, scan_complete)
    ownership_cache = {}
    matching_ids, group_ranks = {}, {}
    for entry in entries:
        if (deep_search or list_launchers) and (entry["exec"] or not entry["_optional_exec"]):
            entry["executable_path"] = resolve_exec(entry["exec"], env, entry["issues"], entry["notes"])
        if list_launchers:
            reasons, rank = [], 0
        elif desktop_id is not None or target_path is not None:
            field = "desktop_entry_id" if desktop_id is not None else "desktop_entry_path"
            value = desktop_id if desktop_id is not None else str(target_path)
            reasons = [{"field": field, "match": "exact", "value": value}] if entry[field] == value else []
            rank = 0
        else:
            reasons, rank = search_reasons(entry, query, deep_search) if entry["_application"] else ([], 99)
        entry["match_reasons"] = reasons
        if reasons:
            entry_id = entry["desktop_entry_id"]
            matching_ids.setdefault(entry_id, []).append(entry["desktop_entry_path"])
            group_ranks[entry_id] = min(group_ranks.get(entry_id, 99), rank)
    if list_launchers and owner_lookup is package_owners:
        ownership_cache = batch_package_owners(
            path for entry in entries for path in
            (entry["desktop_entry_path"], entry["executable_path"]) if path is not None)
    # Stable sorting retains XDG precedence within each relevance tier and ID.
    entries.sort(key=lambda entry: group_ranks.get(entry["desktop_entry_id"], 99))
    matches = []
    selector_error = None
    for entry in entries:
        if target_path is not None and entry["desktop_entry_path"] != str(target_path):
            continue
        if not list_launchers and entry["desktop_entry_id"] not in matching_ids:
            continue
        problems, notes = entry["issues"], entry["notes"]
        if list_launchers:
            notes[:] = [note.replace("all copies are retained", "copies are evaluated separately") for note in notes]
        if desktop_id is not None:
            failure = entry["_read_error"]
            if failure is None and (not entry["_valid_target"] or not entry["_application"]):
                failure = InspectionError(f"Not an application launcher or Hidden mask: {entry['desktop_entry_path']}", "invalid_target")
            if failure is not None and (selector_error is None or failure.exit_code > selector_error.exit_code):
                selector_error = failure
        if target_path is not None:
            notes[:] = [note.replace("all copies are retained", "only the requested file is returned") for note in notes]
        if not entry["match_reasons"] and not list_launchers:
            entry["match_reasons"] = [{
                "field": "desktop_entry_id", "match": "related_copy",
                "value": entry["desktop_entry_id"],
                "source_paths": matching_ids[entry["desktop_entry_id"]],
            }]
        if not (deep_search or list_launchers) and (entry["exec"] or not entry["_optional_exec"]):
            entry["executable_path"] = resolve_exec(entry["exec"], env, problems, notes)
        for key, path in (("desktop_entry", entry["desktop_entry_path"]),
                          ("executable", entry["executable_path"])):
            if path:
                if path not in ownership_cache:
                    path_problems, path_notes = [], []
                    packages = (owner_lookup(path, path_problems, path_notes) if owner_lookup is package_owners
                                else owner_lookup(path, path_problems))
                    ownership_cache[path] = (packages, path_problems, path_notes)
                packages, path_problems, path_notes = ownership_cache[path]
                entry["package_ownership"][key] = packages
                problems.extend(path_problems)
                notes.extend(path_notes)
        if entry["icon"] is None:
            notes.append("Icon is missing.")
        notes.append("Original installation method is unknown; dpkg ownership does not establish APT usage.")
        for key in ("_rank", "_application", "_optional_exec", "_read_error", "_valid_target"):
            del entry[key]
        matches.append(entry)
    if list_launchers:
        return {"schema_version": 1, "query": None, "search_mode": "inventory",
                "launchers": matches, "inventory": inventory, "error": None,
                "search_directories": [str(p) for p in directories], "issues": issues,
                "notes": [inventory["description"]]}
    notes = ["Multiple launcher files matched; see precedence for each desktop-entry ID."] if len(matches) > 1 else []
    mode = "desktop_id" if desktop_id is not None else "desktop_file" if desktop_file is not None else "deep" if deep_search else "application"
    value = desktop_id if desktop_id is not None else desktop_file if desktop_file is not None else query
    error = None if matches else {"code": "not_found", "message": f"No launcher matched {value!r}.", "exit_code": 1}
    if selector_error is not None:
        error = {"code": selector_error.code, "message": str(selector_error), "exit_code": selector_error.exit_code}
    return {"schema_version": 1, "query": value, "matches": matches,
            "search_mode": mode, "error": error,
            "search_directories": [str(p) for p in directories], "issues": issues, "notes": notes}


def main(argv=None):
    import json

    argv = list(sys.argv[1:] if argv is None else argv)
    parser = ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="emit schema-versioned JSON")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show every inspector field for each launcher (default output is a compact summary)")
    parser.add_argument("--deep-search", action="store_true",
                        help="also search desktop paths, resolved executable paths, and complete Exec commands")
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--desktop-id", help="inspect all copies of an exact, case-sensitive desktop-entry ID")
    selectors.add_argument("--desktop-file", help="inspect only this exact .desktop file path")
    selectors.add_argument("--list", action="store_true", help="inventory desktop launchers, not all installed software")
    selectors.add_argument("--gui", action="store_true", help="open the read-only GTK launcher browser")
    parser.add_argument("--include-excluded", action="store_true", help="with --list, also include excluded and shadowed launchers")
    parser.add_argument("query", nargs="?", help="case-insensitive substring of application name, ID, GenericName, or Keywords")
    json_requested = "--json" in argv[:argv.index("--")] if "--" in argv else "--json" in argv
    args = None
    try:
        args = parser.parse_args(argv)
        if args.gui:
            if args.query is not None or args.json or args.deep_search or args.include_excluded:
                raise InspectionError("--gui cannot combine with a query, --json, --deep-search, or --include-excluded.")
            try:
                from .gui import run
                return run()
            except (ImportError, ValueError, RuntimeError) as exc:
                raise InspectionError(f"GUI unavailable: {exc}. Use the system Python 3.12 environment from scripts/setup-dev.sh.", "gui_unavailable", 3)
        options = {"deep_search": args.deep_search}
        if args.list:
            options["list_launchers"] = True
        if args.include_excluded:
            options["include_excluded"] = True
        if args.desktop_id is not None:
            options["desktop_id"] = args.desktop_id
        if args.desktop_file is not None:
            options["desktop_file"] = args.desktop_file
        report = inspect(args.query, **options)
    except InspectionError as exc:
        value, mode = None, None
        if args is not None:
            value = args.desktop_id or args.desktop_file or args.query
            mode = "desktop_id" if args.desktop_id is not None else "desktop_file" if args.desktop_file is not None else "application"
        report = error_report(str(exc), exc.code, exc.exit_code, value, mode)
    if json_requested:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print_human(report, verbose=bool(args and args.verbose))
    return report["error"]["exit_code"] if report.get("error") else 0


def entry():
    """Console-script entry point: main() plus quiet exit when the reader goes away."""
    try:
        code = main()
        sys.stdout.flush()
    except BrokenPipeError:
        # `whatapp ... | head` closes the pipe early. Point stdout at /dev/null so the
        # interpreter's final flush cannot raise again, then exit like other CLI tools.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        code = 0
    return code


# ANSI SGR codes, used only when stdout is a terminal (see use_colour).
BOLD, DIM, RED, GREEN, YELLOW, CYAN = "1", "2", "31", "32", "33", "36"
STATUS_COLOURS = {"eligible": GREEN, "preferred": GREEN, "available": GREEN,
                  "excluded": YELLOW, "shadowed": YELLOW, "unknown": YELLOW, "Unknown": YELLOW,
                  "missing": YELLOW, "not_set": DIM}
QUIET_VALUES = {"(not set)", "None", "None found"}


def use_colour(stream=None):
    """Colour only for a terminal, never for pipes; NO_COLOR and CLICOLOR_FORCE are honoured."""
    if os.environ.get("CLICOLOR_FORCE") or os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    stream = sys.stdout if stream is None else stream
    return hasattr(stream, "isatty") and stream.isatty()


def safe(value):
    """Escape control and other non-printable characters from launcher files.

    Newlines are kept (multi-line values are deliberate); everything else that is not
    printable, including ESC, becomes a visible \\x or \\u escape, so a launcher cannot
    inject terminal sequences or hide text.
    """
    return "".join(c if c == "\n" or c.isprintable() else
                   f"\\x{ord(c):02x}" if ord(c) < 0x100 else f"\\u{ord(c):04x}" for c in value)


def print_human(report, verbose=False):
    from .browser import FOOTER, inspector_rows, inspector_title, origin, ownership_text
    from .browser import text as plain

    colour = use_colour()

    def paint(code, value):
        return f"\033[{code}m{value}\033[0m" if colour and value else value

    def status_colour(entry):
        status = entry["visibility"]["status"]
        return GREEN if status == "eligible" else YELLOW if status in ("unknown", "excluded", "shadowed") else None

    def owner_summary(entry, key, path):
        owners = entry["package_ownership"][key]
        if owners:
            return ", ".join(owners)
        return ownership_text(entry, path).split(" (")[0].lower()

    def print_compact(entry):
        """The four things a quick look needs: where, what runs, who owns it, is it visible."""
        visibility = entry["visibility"]
        status = f"{origin(entry)}, {visibility['status']}"
        reasons = [reason for reason in visibility["reasons"] if reason != visibility["status"]]
        if reasons:
            status += " (" + ", ".join(reasons) + ")"
        identity = entry["desktop_entry_id"] or entry["desktop_entry_path"]
        print(paint(BOLD, safe(entry["display_name"] or "(no name)")) + "  "
              + paint(DIM, f"({safe(identity)})") + "  " + paint(status_colour(entry), status))
        executable = entry["executable_path"]
        executable_text = safe(executable) if executable else paint(YELLOW, "unknown")
        if executable and os.path.basename(executable) in WRAPPER_PROGRAMS:
            executable_text += "  " + paint(YELLOW, "(a wrapper: the real program is in Exec)")
        owner_text = (f"launcher {owner_summary(entry, 'desktop_entry', entry['desktop_entry_path'])}; "
                      f"executable {owner_summary(entry, 'executable', executable)}")
        rows = [("Launcher", safe(entry["desktop_entry_path"])),
                ("Exec", safe(entry["exec"]) if entry["exec"] else paint(DIM, "(not set)")),
                ("Executable", executable_text),
                ("dpkg owner", safe(owner_text))]
        if entry["precedence"]["status"] == "shadowed":
            rows.append(("Shadowed by", safe(entry["precedence"]["preferred_path"] or "unknown")))
        if entry["issues"]:
            rows.append(("Issues", paint(RED, safe("; ".join(entry["issues"])))))
        for label, value in rows:
            print("  " + paint(DIM, f"{label + ':':<13}") + value)

    def value_colour(title, rendered):
        if title == "Issues":
            return DIM if rendered == "None" else RED
        if title == "Notes" or rendered in QUIET_VALUES or rendered.startswith("Unknown ("):
            return DIM
        if rendered in STATUS_COLOURS:
            return STATUS_COLOURS[rendered]
        if title == "Visibility reasons":
            return YELLOW
        return None

    if "inventory" in report:
        print(paint(DIM, report["inventory"]["description"]))
        print(paint(BOLD, "NAME | DESKTOP ID | EXECUTABLE | STATUS"))
        for entry in report["launchers"]:
            status = f"{entry['precedence']['status']}/{entry['visibility']['status']}"
            if entry["visibility"]["reasons"]:
                status += " (" + ", ".join(entry["visibility"]["reasons"]) + ")"
            if entry["issues"]:
                status += f"; {len(entry['issues'])} issue(s)"
            if entry["visibility"]["status"] == "eligible":
                status = paint(GREEN, status)
            elif "unknown" in status or entry["issues"]:
                status = paint(YELLOW, status)
            else:
                status = paint(DIM, status)
            print(" | ".join((safe(plain(entry["display_name"])), safe(entry["desktop_entry_id"]),
                             safe(plain(entry["executable_path"])), status)))
        counts = report["inventory"]["counts"]
        print(paint(BOLD, "Counts: ") + ", ".join(f"{key}={value}" for key, value in counts.items()))
        print(paint(DIM, "Counts overlap: shadowed is part of excluded; uncertain can also be excluded."))
        complete = report["inventory"]["scan_complete"]
        print("Directory scan complete: " + paint(GREEN if complete else YELLOW, str(complete)))
    matches = report.get("matches", report.get("launchers", []) if verbose else [])
    for index, entry in enumerate(matches):
        if index:
            print()
        if not verbose:
            print_compact(entry)
            continue
        print(paint(f"{BOLD};{CYAN}", safe(inspector_title(entry))))
        reasons = [f"- {reason['field']} ({reason['match']})" for reason in entry["match_reasons"]]
        if reasons:
            print()
            print(paint(BOLD, "Matched on"))
            print(paint(DIM, safe("\n".join(reasons))))
        for title, rendered in inspector_rows(entry):
            print()
            print(paint(BOLD, title))
            shown = safe(rendered)
            code = value_colour(title, rendered)
            print(paint(code, shown) if code else shown)
    if matches and verbose:
        print()
        print(paint(DIM, FOOTER))
    elif matches:
        print()
        print(paint(DIM, f"{len(matches)} launcher{'s' if len(matches) != 1 else ''} matched. "
                         "Add -v for every field, or --json for machine-readable output."))
    if report.get("error"):
        print(f"Error: {safe(report['error']['message'])}", file=sys.stderr)
    for issue in report["issues"]:
        print(paint(RED, "Issue: ") + safe(issue))
    for note in report["notes"]:
        print(paint(DIM, "Note: " + safe(note)))
