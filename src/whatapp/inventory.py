"""Inventory classification and counts; discovery remains in the shared engine."""


COUNT_DEFINITIONS = {
    "total_discovered": "Distinct application launcher file paths found, including masks and malformed/unknown-type candidates; excludes known non-Application entries.",
    "included": "Launcher file paths returned by this inventory request.",
    "eligible": "Preferred launchers with visibility status eligible.",
    "excluded": "Launchers with Hidden=true, NoDisplay=true, desktop_allowed=false, or shadowed precedence; each path counted once.",
    "shadowed": "Launchers with shadowed precedence; a subset of excluded.",
    "uncertain": "Launchers with unknown precedence or unknown visibility; may overlap excluded.",
    "omitted": "Discovered launcher candidates not returned (total_discovered minus included).",
}


def classify(entry):
    visibility, precedence = entry["visibility"], entry["precedence"]["status"]
    shadowed = precedence == "shadowed"
    return {
        "eligible": precedence == "preferred" and visibility["status"] == "eligible",
        "excluded": shadowed or visibility["hidden"] is True or visibility["no_display"] is True or visibility["desktop_allowed"] is False,
        "shadowed": shadowed,
        "uncertain": precedence == "unknown" or visibility["status"] == "unknown",
    }


def select_inventory(entries, include_excluded, scan_complete):
    candidates = [entry for entry in entries if entry["_application"]]
    counts = {key: 0 for key in COUNT_DEFINITIONS}
    counts["total_discovered"] = len(candidates)
    selected = []
    for entry in candidates:
        flags = classify(entry)
        for key, flag in flags.items():
            counts[key] += int(flag)
        if include_excluded or flags["eligible"] or flags["uncertain"]:
            selected.append(entry)
    selected.sort(key=lambda entry: (entry["display_name"] is None,
                                     (entry["display_name"] or "").casefold(),
                                     entry["desktop_entry_id"], entry["desktop_entry_path"]))
    counts["included"] = len(selected)
    counts["omitted"] = len(candidates) - len(selected)
    metadata = {
        "scope": "desktop_launchers",
        "description": "Desktop launcher inventory; not an exhaustive inventory of installed software.",
        "include_excluded": include_excluded,
        "scan_complete": scan_complete,
        "entries_with_read_or_parse_errors": sum(entry["_read_error"] is not None for entry in entries),
        "desktop_files_scanned": len(entries),
        "counts": counts,
        "count_definitions": COUNT_DEFINITIONS,
    }
    return selected, metadata
