import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from whatapp.browser import FOOTER, filter_launchers, inspector_fields, inspector_rows, inspector_text, origin, text
from whatapp.cli import inspect, main


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.env = {"HOME": str(root / "home"), "XDG_DATA_HOME": str(root / "home/data"),
                    "XDG_DATA_DIRS": str(root / "system"), "PATH": ""}
        fixtures = Path(__file__).parent / "fixtures"
        for base, filename, fixture in (
            (root / "home/data", "claude.desktop", "claude.desktop"),
            (root / "system", "wine.desktop", "wine-utility.desktop"),
            (root / "system", "claude.desktop", "claude.desktop"),
            (root / "system", "helper.desktop", "nodisplay.desktop"),
            (root / "system", "tryexec.desktop", "tryexec.desktop"),
        ):
            folder = base / "applications"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixtures / fixture, folder / filename)
        self.report = inspect(env=self.env, list_launchers=True, include_excluded=True,
                              owner_lookup=lambda path, issues: None)
        self.entries = self.report["launchers"]

    def test_categories_match_inventory_counts(self):
        self.assertEqual(len(filter_launchers(self.entries)), 5)
        for category in ("eligible", "excluded", "shadowed", "uncertain"):
            self.assertEqual(len(filter_launchers(self.entries, category)),
                             self.report["inventory"]["counts"][category])

    def test_search_does_not_use_wine_prefix_and_preserves_copies(self):
        results = filter_launchers(self.entries, query="CLAUDE")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(e["display_name"] == "Claude" for e in results))
        self.assertEqual(len(filter_launchers(self.entries, "excluded", "claude")), 1)
        self.assertEqual(filter_launchers(self.entries, query="no such app"), [])

    def test_origin_uses_location_not_package_ownership(self):
        for entry in self.entries:
            expected = "User-level" if Path(entry["desktop_entry_path"]).is_relative_to(self.env["HOME"]) else "System-level"
            self.assertEqual(origin(entry, self.env), expected)

    def test_details_preserve_copyable_raw_fields_and_unknowns(self):
        entry = next(e for e in self.entries if e["desktop_entry_id"] == "wine.desktop")
        fields = dict(inspector_fields(entry))
        self.assertEqual(fields["Launcher file path"], entry["desktop_entry_path"])
        self.assertEqual(fields["Raw Exec command"], entry["exec"])
        self.assertEqual(fields["Resolved executable path"], entry["executable_path"])
        self.assertIsNone(fields["Launcher package owner (dpkg)"])
        self.assertIsNone(fields["Original installation method"])
        self.assertEqual(text(None), "Unknown")
        self.assertEqual(text([]), "None")

    def test_inspector_text_is_self_describing_for_copy_all(self):
        entry = next(e for e in self.entries if e["desktop_entry_id"] == "wine.desktop")
        rendered = inspector_text(entry)
        blocks = rendered.split("\n\n")
        self.assertEqual(len(blocks), len(inspector_fields(entry)) + 2)
        self.assertEqual(blocks[0], "WhatApp inspector: wine.desktop")
        self.assertEqual(blocks[1], "Display name\nWine Control Panel")
        self.assertEqual(blocks[-1], FOOTER)
        self.assertIn("not necessarily the application", FOOTER)
        self.assertIn(f"Launcher file path\n{entry['desktop_entry_path']}", rendered)
        self.assertFalse(rendered.endswith("\n"))

    def test_inspector_rows_distinguish_not_set_none_found_and_unknown(self):
        rows = dict(inspector_rows(next(e for e in self.entries if e["desktop_entry_id"] == "wine.desktop")))
        # Keys absent from the file are facts, not unknowns.
        self.assertEqual(rows["TryExec"], "(not set)")
        self.assertEqual(rows["OnlyShowIn"], "(not set)")
        self.assertEqual(rows["Icon name or path"], "(not set)")
        self.assertEqual(rows["TryExec status"], "not_set")
        # The mocked lookup gave no answer and no 'none identified' note, so ownership is unknown.
        self.assertEqual(rows["Launcher package owner (dpkg)"], "Unknown")
        # PATH is empty in this fixture, so env cannot be resolved and nothing was looked up.
        self.assertTrue(rows["Executable package owner (dpkg)"].startswith("Unknown"))
        self.assertEqual(rows["Original installation method"], "Unknown")
        self.assertEqual(rows["Visibility reasons"], "None")
        self.assertTrue(all(line.startswith("- ") for line in rows["Notes"].splitlines()))
        entry = dict(self.entries[0], notes=[f"No exact dpkg ownership identified for {self.entries[0]['desktop_entry_path']}."])
        self.assertEqual(dict(inspector_rows(entry))["Launcher package owner (dpkg)"], "None found")
        unnamed = dict(self.entries[0], display_name=None, visibility=dict(self.entries[0]["visibility"], current_desktops=None))
        rows = dict(inspector_rows(unnamed))
        self.assertEqual(rows["Display name"], "(not set)")
        self.assertEqual(rows["Current desktops"], "(not set)")

    def test_gui_rejects_cli_combinations_without_importing_gtk(self):
        for args in (["--gui", "query"], ["--gui", "--list"],
                     ["--gui", "--desktop-id", "test.desktop"],
                     ["--gui", "--deep-search"], ["--gui", "--include-excluded"]):
            with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--json", *args]), 2)
                self.assertEqual(json.loads(output.getvalue())["error"]["code"], "invalid_arguments")
