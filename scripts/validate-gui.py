"""Opt-in native GTK smoke test. Run inside a graphical session.

Uses the real inventory and window, including asynchronous loading, row selection,
filters, selectable labels, and a clipboard round trip. Clipboard text is restored
after validation where available. The validation window closes when finished.
"""

import json
import time

from gi.repository import GLib
from whatapp.browser import inspector_text
from whatapp.gui import WhatApp


app = WhatApp()
result = {"passed": False}
deadline = time.monotonic() + 30
previous_clipboard = None
clipboard_checked = False


def fail(exc):
    result["error"] = str(exc)
    app.quit()
    return GLib.SOURCE_REMOVE


def copied_all(clipboard, task):
    global clipboard_checked
    try:
        window = app.get_active_window()
        actual = clipboard.read_text_finish(task)
        assert actual == inspector_text(window.selected_entry), actual[:200]
        clipboard_checked = True
        result["clipboard_round_trip"] = "Copy Path and Copy All verified"
        if previous_clipboard is not None:
            clipboard.set(previous_clipboard)
        result["passed"] = True
        window.close()
        app.quit()
    except Exception as exc:
        fail(exc)


def copied_path(clipboard, task):
    try:
        window = app.get_active_window()
        actual = clipboard.read_text_finish(task)
        assert actual == window.selected_entry["desktop_entry_path"], (actual, window.selected_entry)
        window.copy_all.emit("clicked")
        clipboard.read_text_async(None, copied_all)
    except Exception as exc:
        fail(exc)


def before_copy(clipboard, task):
    global previous_clipboard
    try:
        previous_clipboard = clipboard.read_text_finish(task)
        window = app.get_active_window()
        window.copy_path.emit("clicked")
        clipboard.read_text_async(None, copied_path)
    except Exception as exc:
        fail(exc)


def validate():
    if time.monotonic() > deadline:
        return fail("GUI validation timed out")
    window = app.get_active_window()
    if not window or window.loading or not window.get_mapped():
        return GLib.SOURCE_CONTINUE
    try:
        assert window.report is not None, window.status.get_text()
        counts = window.report["inventory"]["counts"]
        result["counts"] = counts
        assert len(window.visible_entries) == counts["total_discovered"]
        for category in ("excluded", "uncertain", "shadowed", "eligible", "all"):
            window.navigation.select_row(window.nav_rows[category])
            expected = counts["total_discovered"] if category == "all" else counts[category]
            assert len(window.visible_entries) == expected, category
        result["filters"] = "all five categories verified"
        window.search.set_text("claude")
        window.apply_filters()
        assert window.visible_entries
        assert all("claude" in (e["display_name"] or "").lower() or "claude" in e["desktop_entry_id"].lower()
                   for e in window.visible_entries)
        row = next((window.launcher_list.get_row_at_index(i) for i, e in enumerate(window.visible_entries)
                    if e["desktop_entry_id"] == "claude-desktop-unofficial.desktop"), window.launcher_list.get_row_at_index(0))
        window.launcher_list.select_row(row)
        entry = row.entry
        result["selected_id"] = entry["desktop_entry_id"]
        # One read-only text view holds every field, so select-all covers the whole panel.
        buffer = window.details.get_buffer()
        shown = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        assert shown == inspector_text(entry) == window.inspector_text
        for key in ("desktop_entry_path", "desktop_entry_id"):
            assert entry[key] in shown, key
        assert not window.details.get_editable()
        result["selectable_details"] = True
        # Empty search clears the inspector and disables copying, avoiding a stale target.
        window.search.set_text("__no_such_whatapp_launcher__")
        window.apply_filters()
        assert window.selected_entry is None
        assert not window.copy_path.get_sensitive()
        assert not window.copy_all.get_sensitive()
        window.search.set_text("claude")
        window.apply_filters()
        window.launcher_list.select_row(next((window.launcher_list.get_row_at_index(i) for i, e in enumerate(window.visible_entries)
                                             if e["desktop_entry_id"] == result["selected_id"]), window.launcher_list.get_row_at_index(0)))
        window.get_clipboard().read_text_async(None, before_copy)
        return GLib.SOURCE_REMOVE
    except Exception as exc:
        return fail(exc)


GLib.timeout_add(100, validate)
GLib.timeout_add_seconds(35, lambda: fail("Clipboard validation timed out") if not clipboard_checked else GLib.SOURCE_REMOVE)
app.run(["whatapp-gui-validation"])
print(json.dumps(result, indent=2))
raise SystemExit(0 if result["passed"] else 1)
