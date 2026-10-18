"""Small GTK4 frontend. All launcher data comes from the shared inventory engine."""

from pathlib import Path
from threading import Thread

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .browser import (CATEGORIES, FOOTER, filter_launchers, inspector_rows, inspector_text,
                      inspector_title, origin, text)
from .cli import inspect

# Reverse-DNS application ID; also the basename of the .desktop file and icon in data/.
APPLICATION_ID = "io.github.boy1dr.WhatApp"

# The inspector is a read-only text view so one selection can span every field
# (mouse drag, Ctrl+A, Ctrl+C). This keeps it looking like a panel, not an entry.
INSPECTOR_CSS = "textview.inspector, textview.inspector text { background-color: transparent; }"


def label(value, *, selectable=False, style=None):
    widget = Gtk.Label(label=value, xalign=0)
    widget.set_selectable(selectable)
    widget.set_wrap(True)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        widget.add_css_class(style)
    return widget


def padded_box(spacing=10):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    for side in ("top", "bottom", "start", "end"):
        getattr(box, f"set_margin_{side}")(12)
    return box


def scroll(child):
    widget = Gtk.ScrolledWindow()
    widget.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    widget.set_vexpand(True)
    widget.set_child(child)
    return widget


class BrowserWindow(Gtk.ApplicationWindow):
    def __init__(self, application, loader=None):
        super().__init__(application=application, title="WhatApp — Launcher Browser")
        self.set_default_size(1280, 820)
        self.loader = loader or (lambda: inspect(list_launchers=True, include_excluded=True))
        self.report = None
        self.entries = []
        self.visible_entries = []
        self.selected_entry = None
        self.category = "all"
        self.closed = False
        self.loading = False
        self.connect("close-request", self.on_close)

        header = Gtk.HeaderBar()
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title.append(label("WhatApp", style="title"))
        title.append(label("Read-only desktop launcher browser", style="subtitle"))
        header.set_title_widget(title)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        self.refresh = Gtk.Button(label="Refresh")
        self.refresh.connect("clicked", lambda *_: self.load())
        header.pack_end(self.refresh)
        self.set_titlebar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        panels = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panels.set_vexpand(True)
        panels.set_position(220)
        panels.set_resize_start_child(False)
        panels.set_shrink_start_child(False)
        outer.append(panels)

        sidebar = padded_box()
        sidebar.set_size_request(190, -1)
        sidebar.append(label("LAUNCHERS", style="heading"))
        self.navigation = Gtk.ListBox()
        self.navigation.add_css_class("navigation-sidebar")
        self.nav_rows = {}
        self.nav_labels = {}
        for key, name in CATEGORIES:
            row = Gtk.ListBoxRow()
            row.category = key
            content = label(name)
            content.set_margin_top(10)
            content.set_margin_bottom(10)
            row.set_child(content)
            self.navigation.append(row)
            self.nav_rows[key] = row
            self.nav_labels[key] = content
        sidebar.append(self.navigation)
        disclaimer = label("Desktop launchers only.\nNot all installed software.\n\nCategories can overlap.", style="dim-label")
        disclaimer.set_margin_top(24)
        sidebar.append(disclaimer)
        panels.set_start_child(sidebar)

        content = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        content.set_position(410)
        content.set_shrink_start_child(False)
        content.set_shrink_end_child(False)
        panels.set_end_child(content)
        middle = padded_box()
        middle.set_size_request(300, -1)
        self.search = Gtk.SearchEntry()
        self.search.set_property("placeholder-text", "Search names, IDs and keywords")
        self.search.connect("search-changed", lambda *_: self.apply_filters())
        middle.append(self.search)
        self.list_status = label("Loading launchers…", style="dim-label")
        middle.append(self.list_status)
        self.launcher_list = Gtk.ListBox()
        self.launcher_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.launcher_list.add_css_class("boxed-list")
        self.launcher_list.connect("row-selected", self.on_selected)
        self.launcher_list.set_placeholder(label("No launchers match this filter."))
        middle.append(scroll(self.launcher_list))
        content.set_start_child(middle)

        inspector = padded_box()
        inspector.set_size_request(350, -1)
        inspector_header = Gtk.Box(spacing=12)
        heading = label("Inspector", style="title-2")
        heading.set_hexpand(True)
        inspector_header.append(heading)
        self.copy_all = Gtk.Button(label="Copy All")
        self.copy_all.set_tooltip_text("Copy every inspector field as text (or click in the inspector and press Ctrl+A, Ctrl+C)")
        self.copy_all.set_sensitive(False)
        self.copy_all.connect("clicked", self.on_copy_all)
        inspector_header.append(self.copy_all)
        self.copy_path = Gtk.Button(label="Copy Path")
        self.copy_path.set_tooltip_text("Copy only the launcher file path")
        self.copy_path.set_sensitive(False)
        self.copy_path.connect("clicked", self.on_copy_path)
        inspector_header.append(self.copy_path)
        inspector.append(inspector_header)
        self.details = Gtk.TextView()
        self.details.set_editable(False)
        self.details.set_cursor_visible(False)
        self.details.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.details.set_pixels_below_lines(2)
        self.details.set_top_margin(6)
        self.details.add_css_class("inspector")
        buffer = self.details.get_buffer()
        self.heading_tag = buffer.create_tag("heading", weight=Pango.Weight.BOLD)
        self.footer_tag = buffer.create_tag("footer", style=Pango.Style.ITALIC, scale=0.9)
        self.inspector_text = ""
        self.details.get_buffer().set_text("Select a launcher to inspect it.")
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(INSPECTOR_CSS)
        else:
            provider.load_from_data(INSPECTOR_CSS.encode())
        Gtk.StyleContext.add_provider_for_display(self.get_display(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        inspector.append(scroll(self.details))
        content.set_end_child(inspector)
        self.status = label("Reading desktop launchers…", selectable=True)
        self.status.set_margin_start(12)
        self.status.set_margin_end(12)
        self.status.set_margin_bottom(8)
        outer.append(self.status)
        self.navigation.select_row(self.nav_rows["all"])
        self.navigation.connect("row-selected", self.on_category)
        self.load()

    def on_close(self, *_):
        self.closed = True
        return False

    def load(self):
        if self.loading:
            return
        self.loading = True
        self.refresh.set_sensitive(False)
        self.spinner.start()
        self.status.set_text("Reading desktop launchers…")

        def worker():
            try:
                report = self.loader()
                GLib.idle_add(self.loaded, report, None)
            except Exception as exc:
                GLib.idle_add(self.loaded, None, str(exc))

        # Short-lived in-process work keeps dpkg queries off the GTK main thread.
        # It neither runs as a service nor writes persistent state.
        Thread(target=worker, daemon=True, name="whatapp-inventory").start()

    def loaded(self, report, error):
        if self.closed:
            return GLib.SOURCE_REMOVE
        self.loading = False
        self.spinner.stop()
        self.refresh.set_sensitive(True)
        if error:
            self.status.set_text(f"Inventory failed: {error}. Use Refresh to retry. Previous results, if any, are retained.")
            self.list_status.set_text(f"{len(self.visible_entries)} previous results" if self.report else "Inventory unavailable")
            return GLib.SOURCE_REMOVE
        self.report = report
        self.entries = report["launchers"]
        counts = report["inventory"]["counts"]
        for key, name in CATEGORIES:
            count = counts["total_discovered"] if key == "all" else counts[key]
            self.nav_labels[key].set_text(f"{name}  ({count})")
        self.apply_filters()
        summary = f"{len(self.entries)} launchers · {counts['eligible']} menu-eligible · {counts['uncertain']} uncertain"
        if not report["inventory"]["scan_complete"]:
            summary += " · Partial directory scan; counts may be incomplete"
        if report["issues"]:
            summary += "\n" + "\n".join(report["issues"])
        self.status.set_text(summary)
        return GLib.SOURCE_REMOVE

    def on_category(self, _, row):
        if row:
            self.category = row.category
            self.apply_filters()

    def icon_for(self, entry):
        icon = entry["icon"]
        image = Gtk.Image.new_from_icon_name("application-x-executable")
        image.set_pixel_size(32)
        if icon:
            if Path(icon).is_absolute() and Path(icon).is_file():
                image.set_from_file(icon)
            elif Gtk.IconTheme.get_for_display(self.get_display()).has_icon(icon):
                image.set_from_icon_name(icon)
        return image

    def apply_filters(self):
        previous = self.selected_entry["desktop_entry_path"] if self.selected_entry else None
        self.visible_entries = filter_launchers(self.entries, self.category, self.search.get_text())
        while child := self.launcher_list.get_first_child():
            self.launcher_list.remove(child)
        selected = None
        for entry in self.visible_entries:
            row = Gtk.ListBoxRow()
            row.entry = entry
            box = Gtk.Box(spacing=12)
            for side in ("top", "bottom", "start", "end"):
                getattr(box, f"set_margin_{side}")(10)
            box.append(self.icon_for(entry))
            words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            words.set_hexpand(True)
            for value, style in ((text(entry["display_name"]), "heading"),
                                 (text(entry["desktop_entry_id"]), "dim-label"),
                                 (f"{origin(entry)} · {entry['precedence']['status']} · {entry['visibility']['status']}", "dim-label")):
                line = Gtk.Label(label=value, xalign=0)
                line.set_ellipsize(Pango.EllipsizeMode.END)
                line.set_max_width_chars(45)
                line.add_css_class(style)
                words.append(line)
            box.append(words)
            row.set_child(box)
            row.set_tooltip_text(entry["desktop_entry_path"])
            self.launcher_list.append(row)
            if entry["desktop_entry_path"] == previous:
                selected = row
        self.list_status.set_text(f"{len(self.visible_entries)} shown · {dict(CATEGORIES)[self.category]}")
        self.launcher_list.select_row(selected or self.launcher_list.get_row_at_index(0))
        if not self.visible_entries:
            self.on_selected(self.launcher_list, None)

    def on_selected(self, _, row):
        self.selected_entry = row.entry if row else None
        for button, caption in ((self.copy_path, "Copy Path"), (self.copy_all, "Copy All")):
            button.set_sensitive(row is not None)
            button.set_label(caption)
        buffer = self.details.get_buffer()
        if row is None:
            self.inspector_text = ""
            buffer.set_text("Select a launcher to inspect it.")
            return
        # The buffer text is exactly inspector_text(entry), so a select-all copy and
        # Copy All produce the same thing; the tags only style titles and the footer.
        self.inspector_text = inspector_text(row.entry)
        buffer.set_text("")
        buffer.insert_with_tags(buffer.get_end_iter(), inspector_title(row.entry), self.heading_tag)
        for title, rendered in inspector_rows(row.entry):
            buffer.insert(buffer.get_end_iter(), "\n\n")
            buffer.insert_with_tags(buffer.get_end_iter(), title, self.heading_tag)
            buffer.insert(buffer.get_end_iter(), "\n" + rendered)
        buffer.insert(buffer.get_end_iter(), "\n\n")
        buffer.insert_with_tags(buffer.get_end_iter(), FOOTER, self.footer_tag)
        buffer.place_cursor(buffer.get_start_iter())

    def on_copy_path(self, *_):
        if self.selected_entry:
            self.get_clipboard().set(self.selected_entry["desktop_entry_path"])
            self.copy_path.set_label("Copied")

    def on_copy_all(self, *_):
        if self.selected_entry:
            self.get_clipboard().set(self.inspector_text)
            self.copy_all.set_label("Copied")


class WhatApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = BrowserWindow(self)
        window.present()


def run():
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        raise RuntimeError("GTK cannot connect to a graphical display. Run --gui from your desktop session.")
    return WhatApp().run(["whatapp"])
