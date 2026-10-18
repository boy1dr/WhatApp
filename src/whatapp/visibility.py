"""Interpret launcher precedence and menu eligibility without launching anything."""

import os
import re
import shutil


PROPERTY_KEYS = ("Hidden", "NoDisplay", "OnlyShowIn", "NotShowIn", "TryExec")


def interpret_visibility(properties, env, issues, *, valid=True):
    """Absent booleans default to false; malformed values remain unknown.

    Eligibility describes these desktop-entry gates, not a desktop's final menu.
    TryExec failure permits desktops to omit an entry, but does not require it.
    """
    uncertain = not valid

    def boolean(key):
        nonlocal uncertain
        value = properties[key]
        if value is None:
            return False if valid else None
        if value not in ("true", "false"):
            issues.append(f"Invalid {key} boolean: {value!r}")
            uncertain = True
            return None
        return value == "true"

    def desktop_list(key):
        nonlocal uncertain
        value = properties[key]
        if value is None:
            return None
        # Split only unescaped semicolons; desktop names are case-sensitive.
        items = re.split(r"(?<!\\);", value)
        if items and items[-1] == "":
            items.pop()
        if any(not item or "\\" in item for item in items):
            issues.append(f"Invalid or unsupported {key} list: {value!r}")
            uncertain = True
            return None
        return items

    hidden, no_display = boolean("Hidden"), boolean("NoDisplay")
    only, excluded = desktop_list("OnlyShowIn"), desktop_list("NotShowIn")
    if set(only or []) & set(excluded or []):
        issues.append("OnlyShowIn and NotShowIn contain the same desktop name.")
        uncertain = True
    desktops = [name for name in env.get("XDG_CURRENT_DESKTOP", "").split(":") if name]
    allowed = None
    restricted = properties["OnlyShowIn"] is not None or properties["NotShowIn"] is not None
    if not restricted:
        allowed = True
    elif desktops and not uncertain:
        allowed = only is None
        for desktop in desktops:
            if desktop in (only or []):
                allowed = True
                break
            if desktop in (excluded or []):
                allowed = False
                break

    raw_try = properties["TryExec"]
    try_path = None
    try_status = "not_set"
    if raw_try is not None:
        # TryExec is one string/path, not an Exec command with arguments.
        command = re.sub(r"\\([sntr\\])", lambda m: {
            "s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"
        }[m[1]], raw_try)
        if not command or ("/" in command and not os.path.isabs(command)):
            try_status = "unknown"
            issues.append("TryExec is empty or uses an unsupported relative path.")
        else:
            try_path = shutil.which(command, path=env.get("PATH", os.defpath))
            try_status = "available" if try_path else "missing"
            if try_path:
                try_path = os.path.abspath(try_path)

    reasons = []
    if hidden is True:
        reasons.append("hidden")
    if no_display is True:
        reasons.append("no_display")
    if allowed is False:
        reasons.append("desktop_restricted")
    if reasons:
        status = "excluded"
    else:
        if uncertain:
            reasons.append("invalid_entry_or_properties")
        if allowed is None:
            reasons.append("desktop_context_unknown")
        if try_status in ("missing", "unknown"):
            reasons.append("try_exec_" + try_status)
        status = "unknown" if reasons else "eligible"
    if try_status == "missing" and "try_exec_missing" not in reasons:
        reasons.append("try_exec_missing")
    return {
        "hidden": hidden, "no_display": no_display,
        "only_show_in": only, "not_show_in": excluded,
        "current_desktops": desktops or None, "desktop_allowed": allowed,
        "try_exec": {"value": raw_try, "status": try_status, "executable_path": try_path},
        "status": status, "reasons": reasons,
    }


def assign_precedence(entries, scan_complete):
    """Group before query filtering, so a nonmatching mask still takes precedence."""
    by_id = {}
    for entry in entries:
        by_id.setdefault(entry["desktop_entry_id"], []).append(entry)
    for group in by_id.values():
        first_rank = min(entry["_rank"] for entry in group)
        candidates = [entry["desktop_entry_path"] for entry in group if entry["_rank"] == first_rank]
        selected = candidates[0] if len(candidates) == 1 and scan_complete else None
        for entry in group:
            status = "unknown"
            if scan_complete and entry["_rank"] > first_rank:
                status = "shadowed"
            elif selected:
                status = "preferred"
            entry["precedence"] = {
                "status": status, "directory_index": entry["_rank"],
                "preferred_path": selected, "candidate_paths": candidates,
            }
            if status == "shadowed":
                entry["visibility"]["status"] = "shadowed"
                entry["visibility"]["reasons"].insert(0, "shadowed")
            elif status == "unknown":
                entry["visibility"]["status"] = "unknown"
                entry["visibility"]["reasons"].insert(0, "precedence_unknown")
                entry["issues"].append("Precedence is uncertain: same-directory ID collision or incomplete scan.")
            if len(group) > 1:
                entry["notes"].append("Multiple files share this desktop-entry ID; all copies are retained.")
    return by_id
