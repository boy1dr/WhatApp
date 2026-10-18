import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from whatapp.cli import application_dirs, batch_package_owners, inspect, main, package_owners, resolve_exec, use_colour


FIXTURES = Path(__file__).parent / "fixtures"


class InspectionTests(unittest.TestCase):
    def setUp(self):
        # All temporary files stay inside this development project.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.user = self.base / "user/applications"
        self.system = self.base / "system/applications"
        self.user.mkdir(parents=True)
        self.system.mkdir(parents=True)
        self.env = {"XDG_DATA_HOME": str(self.user.parent),
                    "XDG_DATA_DIRS": str(self.system.parent), "PATH": str(self.base / "bin")}

    def copy(self, fixture, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / fixture, target)

    def inspect(self, query=None, **options):
        return inspect(query, self.env, lambda path, issues: None, **options)

    def test_duplicates_nested_ids_and_fields(self):
        self.copy("editor.desktop", self.user / "vendor/editor.desktop")
        self.copy("editor.desktop", self.system / "vendor/editor.desktop")
        binary = self.base / "bin/sample-editor"
        binary.parent.mkdir()
        binary.write_text("never executed")
        binary.chmod(0o755)
        report = self.inspect("SAMPLE EDITOR")
        self.assertEqual(len(report["matches"]), 2)
        for entry in report["matches"]:
            self.assertEqual(entry["desktop_entry_id"], "vendor-editor.desktop")
            self.assertEqual(entry["exec"], "sample-editor --open %U")
            self.assertEqual(entry["executable_path"], str(binary))
            self.assertEqual(entry["icon"], "sample-editor")
            self.assertIsNone(entry["installation_method"])
            self.assertTrue(any("all copies" in note for note in entry["notes"]))

    def test_missing_hidden_and_non_application(self):
        self.copy("hidden.desktop", self.user / "hidden.desktop")
        self.copy("link.desktop", self.user / "link.desktop")
        matches = self.inspect("sample")["matches"]
        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0]["exec"])
        self.assertIsNone(matches[0]["icon"])
        self.assertTrue(matches[0]["visibility"]["hidden"])
        self.assertEqual(matches[0]["visibility"]["status"], "excluded")
        self.assertFalse(matches[0]["issues"])

    def test_broken_file_reported_and_discoverable_by_filename(self):
        self.copy("broken.desktop", self.user / "broken.desktop")
        report = self.inspect("broken")
        self.assertIsNone(report["matches"][0]["display_name"])
        self.assertTrue(report["issues"])
        self.assertTrue(self.inspect("unrelated")["issues"])

    def test_json_and_empty_result(self):
        self.copy("editor.desktop", self.user / "editor.desktop")
        for query, count in (("sample", 1), ("nonexistent", 0)):
            output = io.StringIO()
            with patch.dict(os.environ, self.env), patch("whatapp.cli.package_owners"), contextlib.redirect_stdout(output):
                # Supply deterministic ownership via inspect, avoiding host dpkg.
                with patch("whatapp.cli.inspect", side_effect=self.inspect):
                    self.assertEqual(main(["--json", query]), 0 if count else 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(len(report["matches"]), count)
            if count:
                self.assertEqual(report["matches"][0]["precedence"]["status"], "preferred")
                self.assertIn("notes", report["matches"][0])
                self.assertIn("desktop_entry_properties", report["matches"][0])

    def test_xdg_defaults_invalid_and_repeated_roots(self):
        issues = []
        dirs = application_dirs({}, issues)
        self.assertEqual(dirs[-2:], [Path("/usr/local/share/applications"), Path("/usr/share/applications")])
        env = {**self.env, "XDG_DATA_DIRS": f"relative:{self.system.parent}:{self.system.parent}"}
        dirs = application_dirs(env, issues)
        self.assertEqual(len(dirs), 2)
        self.assertTrue(issues)

    def test_quoted_executable_and_unsupported_syntax(self):
        binary = self.base / "space program"
        binary.write_text("never executed")
        binary.chmod(0o755)
        self.assertEqual(resolve_exec(f'"{binary}" %U', self.env, []), str(binary))
        for raw in (None, "absent %U", "relative/program", "'shell quoting'", '"unclosed', "%f", "VAR=value program"):
            notes = []
            self.assertIsNone(resolve_exec(raw, self.env, notes))
            self.assertTrue(notes)

    def test_shell_wrapper_is_not_executed_or_traced(self):
        with patch("whatapp.cli.shutil.which", return_value="/bin/sh"):
            notes = []
            self.assertEqual(resolve_exec('sh -c "touch /tmp/forbidden"', self.env, [], notes), "/bin/sh")
            self.assertTrue(any("wrappers" in note for note in notes))
            self.assertTrue(any("'sh' is a wrapper" in note for note in notes))
        with patch("whatapp.cli.shutil.which", return_value="/usr/bin/env"):
            notes = []
            resolve_exec("env WINEPREFIX=/home/example/.wine-claude wine app.exe", self.env, [], notes)
            self.assertTrue(any("'env' is a wrapper" in note and "raw Exec" in note for note in notes))
        with patch("whatapp.cli.shutil.which", return_value="/usr/bin/firefox"):
            notes = []
            resolve_exec("firefox %u", self.env, [], notes)
            self.assertFalse(any("is a wrapper" in note for note in notes))

    def test_user_override(self):
        for root in (self.user, self.system):
            self.copy("editor.desktop", root / "editor.desktop")
        user, system = self.inspect("editor")["matches"]
        self.assertEqual(user["precedence"]["status"], "preferred")
        self.assertEqual(system["precedence"]["status"], "shadowed")
        self.assertEqual(system["visibility"]["status"], "shadowed")
        self.assertEqual(system["precedence"]["preferred_path"], str(self.user / "editor.desktop"))

    def test_hidden_mask_included_when_only_system_name_matches(self):
        self.copy("mask.desktop", self.user / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        user, system = self.inspect("Sample Editor")["matches"]
        self.assertEqual(user["precedence"]["status"], "preferred")
        self.assertEqual(user["visibility"]["status"], "excluded")
        self.assertEqual(user["visibility"]["reasons"], ["hidden"])
        self.assertEqual(user["issues"], [])
        self.assertEqual(system["precedence"]["preferred_path"], user["desktop_entry_path"])
        self.assertEqual(system["visibility"]["status"], "shadowed")

    def test_no_display_is_informational(self):
        self.copy("nodisplay.desktop", self.user / "helper.desktop")
        entry = self.inspect("helper")["matches"][0]
        self.assertEqual(entry["desktop_entry_properties"]["NoDisplay"], "true")
        self.assertEqual(entry["precedence"]["status"], "preferred")
        self.assertEqual(entry["visibility"]["status"], "excluded")
        self.assertEqual(entry["visibility"]["reasons"], ["no_display"])
        self.assertEqual(entry["issues"], [])

    def test_desktop_restrictions_and_missing_context(self):
        self.copy("restricted.desktop", self.user / "restricted.desktop")
        for desktop, expected in (("GNOME", "eligible"), ("KDE", "excluded"),
                                  ("XFCE", "excluded"), ("KDE:GNOME", "excluded"),
                                  ("GNOME:KDE", "eligible"), ("gnome", "excluded"),
                                  ("", "unknown")):
            with self.subTest(desktop=desktop):
                self.env["XDG_CURRENT_DESKTOP"] = desktop
                entry = self.inspect("restricted")["matches"][0]
                self.assertEqual(entry["visibility"]["status"], expected)
                self.assertEqual(entry["visibility"]["only_show_in"], ["GNOME", "Unity"])
                self.assertEqual(entry["issues"], [])

    def test_not_show_in_default_allows_other_desktops(self):
        self.copy("notshow.desktop", self.user / "notshow.desktop")
        for desktop, expected in (("KDE", "excluded"), ("GNOME", "eligible"), ("", "unknown")):
            self.env["XDG_CURRENT_DESKTOP"] = desktop
            self.assertEqual(self.inspect("notshow")["matches"][0]["visibility"]["status"], expected)

    def test_try_exec_missing_and_available(self):
        self.copy("tryexec.desktop", self.user / "tryexec.desktop")
        entry = self.inspect("tryexec")["matches"][0]
        self.assertEqual(entry["visibility"]["try_exec"]["status"], "missing")
        self.assertEqual(entry["visibility"]["status"], "unknown")
        self.assertIn("try_exec_missing", entry["visibility"]["reasons"])
        self.assertEqual(entry["issues"], [])
        binary = self.base / "bin/sample-probe"
        binary.parent.mkdir()
        binary.write_text("must never execute")
        binary.chmod(0o755)
        entry = self.inspect("tryexec")["matches"][0]
        self.assertEqual(entry["visibility"]["status"], "eligible")
        self.assertEqual(entry["visibility"]["try_exec"]["executable_path"], str(binary))
        binary.chmod(0o644)
        self.assertEqual(self.inspect("tryexec")["matches"][0]["visibility"]["try_exec"]["status"], "missing")

    def test_independent_ids_are_both_preferred(self):
        self.copy("editor.desktop", self.user / "one.desktop")
        self.copy("editor.desktop", self.system / "two.desktop")
        entries = self.inspect("sample")["matches"]
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["precedence"]["status"] == "preferred" for e in entries))
        self.assertTrue(all(e["visibility"]["status"] == "eligible" for e in entries))

    def test_system_directory_order(self):
        extra = self.base / "extra/applications"
        self.copy("editor.desktop", extra / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        self.env["XDG_DATA_DIRS"] = f"{extra.parent}:{self.system.parent}"
        first, second = self.inspect("editor")["matches"]
        self.assertEqual(first["desktop_entry_path"], str(extra / "editor.desktop"))
        self.assertEqual(first["precedence"]["status"], "preferred")
        self.assertEqual(second["precedence"]["status"], "shadowed")

    def test_same_directory_id_collision_is_unknown(self):
        self.copy("editor.desktop", self.user / "vendor/editor.desktop")
        self.copy("editor.desktop", self.user / "vendor-editor.desktop")
        entries = self.inspect("editor")["matches"]
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(entry["precedence"]["status"], "unknown")
            self.assertIsNone(entry["precedence"]["preferred_path"])
            self.assertEqual(entry["visibility"]["status"], "unknown")
            self.assertTrue(entry["issues"])

    def test_invalid_visibility_properties_are_problems(self):
        self.copy("invalid-visibility.desktop", self.user / "invalid.desktop")
        entry = self.inspect("invalid")["matches"][0]
        self.assertIsNone(entry["visibility"]["hidden"])
        self.assertEqual(entry["visibility"]["status"], "unknown")
        self.assertTrue(any("boolean" in issue for issue in entry["issues"]))
        self.assertTrue(any("same desktop" in issue for issue in entry["issues"]))

    def test_malformed_preferred_file_does_not_fall_back(self):
        self.copy("broken.desktop", self.user / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        user, system = self.inspect("Sample Editor")["matches"]
        self.assertEqual(user["precedence"]["status"], "preferred")
        self.assertEqual(user["visibility"]["status"], "unknown")
        self.assertEqual(system["precedence"]["status"], "shadowed")

    def test_incomplete_scan_does_not_claim_a_winner(self):
        self.copy("editor.desktop", self.system / "editor.desktop")
        original_walk = os.walk

        def walk(root, **kwargs):
            if root == self.user:
                kwargs["onerror"](PermissionError(13, "Access denied", str(root)))
                return iter(())
            return original_walk(root, **kwargs)

        with patch("whatapp.cli.os.walk", side_effect=walk):
            report = self.inspect("editor")
        self.assertTrue(report["issues"])
        self.assertEqual(report["matches"][0]["precedence"]["status"], "unknown")
        self.assertEqual(report["matches"][0]["visibility"]["status"], "unknown")

    def test_wine_prefix_does_not_match_ordinary_search(self):
        for fixture in ("claude.desktop", "claude-native.desktop", "claude-code.desktop"):
            self.copy(fixture, self.user / fixture)
        for number in range(14):
            self.copy("wine-utility.desktop", self.user / f"utility-{number}.desktop")
        ordinary = self.inspect("CLAUDE")
        self.assertEqual(ordinary["search_mode"], "application")
        self.assertEqual({entry["desktop_entry_id"] for entry in ordinary["matches"]},
                         {"claude.desktop", "claude-native.desktop", "claude-code.desktop"})
        for entry in ordinary["matches"]:
            self.assertTrue(entry["match_reasons"])
            self.assertFalse(any(reason["field"] == "exec" for reason in entry["match_reasons"]))
            self.assertIn("precedence", entry)
            self.assertIn("visibility", entry)
        deep = self.inspect("claude", deep_search=True)
        self.assertEqual(deep["search_mode"], "deep")
        self.assertEqual(len(deep["matches"]), 17)
        self.assertEqual({entry["desktop_entry_id"] for entry in deep["matches"][:3]},
                         {entry["desktop_entry_id"] for entry in ordinary["matches"]})
        for entry in deep["matches"][3:]:
            self.assertEqual([reason["field"] for reason in entry["match_reasons"]], ["exec"])

    def test_generic_name_keywords_and_relevance(self):
        self.copy("search-metadata.desktop", self.user / "aaa.desktop")
        self.copy("claude-code.desktop", self.system / "code.desktop")
        self.copy("claude.desktop", self.system / "zzz.desktop")
        entries = self.inspect("claude")["matches"]
        self.assertEqual([entry["display_name"] for entry in entries],
                         ["Claude", "Claude Code URL Handler", "Conversation Tool"])
        self.assertEqual(entries[-1]["keywords"], ["Claude", "AI", "chat;assistant"])
        self.assertEqual(entries[-1]["match_reasons"],
                         [{"field": "keywords", "match": "exact", "value": "Claude"}])
        generic = self.inspect("assistant")["matches"][0]
        self.assertEqual({r["field"] for r in generic["match_reasons"]}, {"generic_name", "keywords"})
        self.assertEqual(generic["generic_name"], "AI Assistant")
        self.assertEqual(len(self.inspect("chat;assistant")["matches"]), 1)

    def test_paths_and_arguments_require_deep_search(self):
        self.copy("wine-utility.desktop", self.user / "utility.desktop")
        for query in (".wine-claude", "control.exe", str(self.user)):
            self.assertEqual(self.inspect(query)["matches"], [])
            self.assertEqual(len(self.inspect(query, deep_search=True)["matches"]), 1)

    def test_resolved_executable_path_is_searchable_only_in_deep_mode(self):
        self.copy("editor.desktop", self.user / "editor.desktop")
        binary = self.base / "bin/sample-editor"
        binary.parent.mkdir()
        binary.write_text("never execute")
        binary.chmod(0o755)
        self.assertEqual(self.inspect(str(binary))["matches"], [])
        entry = self.inspect(str(binary), deep_search=True)["matches"][0]
        self.assertEqual(entry["match_reasons"],
                         [{"field": "executable_path", "match": "exact", "value": str(binary)}])

    def test_mask_has_related_copy_reason(self):
        self.copy("mask.desktop", self.user / "chat.desktop")
        self.copy("claude.desktop", self.system / "chat.desktop")
        user, system = self.inspect("claude")["matches"]
        self.assertEqual(user["match_reasons"], [{
            "field": "desktop_entry_id", "match": "related_copy", "value": "chat.desktop",
            "source_paths": [str(self.system / "chat.desktop")],
        }])
        self.assertEqual(system["match_reasons"][0]["field"], "display_name")
        self.assertEqual(user["visibility"]["status"], "excluded")
        self.assertEqual(system["precedence"]["status"], "shadowed")

    def test_deep_search_cli_json(self):
        self.copy("wine-utility.desktop", self.user / "utility.desktop")
        output = io.StringIO()
        with patch("whatapp.cli.inspect", side_effect=self.inspect), contextlib.redirect_stdout(output):
            self.assertEqual(main(["--json", "--deep-search", "claude"]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["search_mode"], "deep")
        self.assertEqual(report["matches"][0]["match_reasons"][0]["field"], "exec")

    def run_cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("whatapp.cli.inspect", side_effect=self.inspect), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_exact_id_is_case_sensitive_not_substring(self):
        self.copy("claude.desktop", self.user / "claude.desktop")
        self.copy("claude-native.desktop", self.user / "other-claude.desktop")
        report = self.inspect(desktop_id="claude.desktop")
        self.assertEqual(len(report["matches"]), 1)
        self.assertEqual(report["search_mode"], "desktop_id")
        self.assertEqual(report["matches"][0]["match_reasons"],
                         [{"field": "desktop_entry_id", "match": "exact", "value": "claude.desktop"}])
        self.assertEqual(self.inspect(desktop_id="Claude.desktop")["matches"], [])
        self.assertEqual(self.inspect(desktop_id="laude.desktop")["matches"], [])

    def test_exact_id_retains_all_copies_and_hidden_mask(self):
        self.copy("mask.desktop", self.user / "vendor/editor.desktop")
        self.copy("editor.desktop", self.system / "vendor/editor.desktop")
        code, output, stderr = self.run_cli("--json", "--desktop-id", "vendor-editor.desktop")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        report = json.loads(output)
        self.assertEqual(report["schema_version"], 1)
        self.assertIsNone(report["error"])
        self.assertEqual([e["precedence"]["status"] for e in report["matches"]], ["preferred", "shadowed"])
        self.assertEqual(report["matches"][0]["visibility"]["status"], "excluded")

    def test_exact_shadowed_file_returns_only_requested_copy(self):
        self.copy("mask.desktop", self.user / "editor.desktop")
        target = self.system / "editor.desktop"
        self.copy("editor.desktop", target)
        code, output, stderr = self.run_cli("--desktop-file", str(target), "--json")
        self.assertEqual((code, stderr), (0, ""))
        report = json.loads(output)
        self.assertEqual(len(report["matches"]), 1)
        entry = report["matches"][0]
        self.assertEqual(entry["desktop_entry_path"], str(target))
        self.assertEqual(entry["precedence"]["status"], "shadowed")
        self.assertEqual(entry["precedence"]["preferred_path"], str(self.user / "editor.desktop"))
        self.assertEqual(entry["match_reasons"], [{"field": "desktop_entry_path", "match": "exact", "value": str(target)}])

    def test_missing_file_does_not_substitute_same_id(self):
        self.copy("editor.desktop", self.user / "editor.desktop")
        target = str(self.system / "editor.desktop")
        code, output, stderr = self.run_cli("--json", "--desktop-file", target)
        report = json.loads(output)
        self.assertEqual((code, stderr), (1, ""))
        self.assertEqual(report["matches"], [])
        self.assertEqual(report["error"]["code"], "not_found")
        self.assertEqual(report["query"], target)
        self.assertIn(target, report["error"]["message"])

    def test_external_and_relative_file_paths(self):
        target = self.base / "standalone.desktop"
        self.copy("editor.desktop", target)
        relative = os.path.relpath(target)
        code, output, _ = self.run_cli("--json", "--desktop-file", relative)
        self.assertEqual(code, 0)
        entry = json.loads(output)["matches"][0]
        self.assertEqual(entry["desktop_entry_path"], str(target))
        self.assertIsNone(entry["desktop_entry_id"])
        self.assertEqual(entry["precedence"]["status"], "unknown")

    def test_invalid_target_files_produce_json_errors(self):
        for fixture in ("broken.desktop", "link.desktop"):
            target = self.user / fixture
            self.copy(fixture, target)
            code, output, stderr = self.run_cli("--json", "--desktop-file", str(target))
            report = json.loads(output)
            self.assertEqual((code, stderr), (2, ""))
            self.assertEqual(report["error"]["code"], "invalid_target")
            self.assertEqual(report["matches"], [])
        for target in (self.user, FIXTURES / "../test_cli.py"):
            code, output, _ = self.run_cli("--json", "--desktop-file", str(target))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output)["error"]["code"], "invalid_target")

    def test_unreadable_target_json_error(self):
        target = self.user / "editor.desktop"
        self.copy("editor.desktop", target)
        with patch("whatapp.cli.Path.open", side_effect=PermissionError("Access denied")):
            code, output, stderr = self.run_cli("--json", "--desktop-file", str(target))
        self.assertEqual((code, stderr), (3, ""))
        self.assertEqual(json.loads(output)["error"]["code"], "unreadable_target")

    def test_incompatible_and_missing_arguments_json_errors(self):
        cases = [[], ["--desktop-id"], ["--desktop-file"],
                 ["--desktop-id", "editor.desktop", "--desktop-file", "/example.desktop"],
                 ["--desktop-id", "editor.desktop", "editor"],
                 ["--desktop-file", "/example.desktop", "editor"],
                 ["--deep-search", "--desktop-id", "editor.desktop"],
                 ["--deep-search", "--desktop-file", "/example.desktop"],
                 ["--desktop-id", "editor"], ["--desktop-id", "/editor.desktop"],
                 ["--desktop-id", ""], ["--desktop-file", ""], ["--unknown"]]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, output, stderr = self.run_cli("--json", *arguments)
                report = json.loads(output)
                self.assertEqual((code, stderr), (2, ""))
                self.assertEqual(report["schema_version"], 1)
                self.assertEqual(report["matches"], [])
                self.assertEqual(report["error"]["code"], "invalid_arguments")

    def test_human_error_and_no_match_exit_code(self):
        code, output, stderr = self.run_cli("--desktop-id", "absent.desktop")
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertIn("absent.desktop", stderr)
        code, output, stderr = self.run_cli("--json", "--desktop-id", "absent.desktop")
        self.assertEqual((code, stderr), (1, ""))
        self.assertEqual(json.loads(output)["error"]["exit_code"], 1)

    def test_invalid_exact_id_copy_retained_with_error_not_replaced(self):
        self.copy("broken.desktop", self.user / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        code, output, stderr = self.run_cli("--json", "--desktop-id", "editor.desktop")
        report = json.loads(output)
        self.assertEqual((code, stderr), (2, ""))
        self.assertEqual(report["error"]["code"], "invalid_target")
        self.assertEqual(len(report["matches"]), 2)
        self.assertEqual(report["matches"][0]["precedence"]["status"], "preferred")
        self.assertEqual(report["matches"][1]["precedence"]["status"], "shadowed")

    def inventory_fixtures(self):
        self.copy("claude.desktop", self.user / "claude.desktop")
        self.copy("tryexec.desktop", self.user / "tryexec.desktop")
        self.copy("mask.desktop", self.user / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        self.copy("nodisplay.desktop", self.user / "helper.desktop")
        self.copy("restricted.desktop", self.user / "restricted.desktop")
        self.copy("broken.desktop", self.user / "broken.desktop")
        self.copy("link.desktop", self.user / "link.desktop")
        self.env["XDG_CURRENT_DESKTOP"] = "KDE"

    def test_inventory_filtering_counts_and_uncertainty(self):
        self.inventory_fixtures()
        report = self.inspect(list_launchers=True)
        self.assertEqual({e["desktop_entry_id"] for e in report["launchers"]},
                         {"claude.desktop", "tryexec.desktop", "broken.desktop"})
        self.assertEqual(report["inventory"]["counts"], {
            "total_discovered": 7, "included": 3, "eligible": 1,
            "excluded": 4, "shadowed": 1, "uncertain": 2, "omitted": 4,
        })
        self.assertEqual(report["inventory"]["desktop_files_scanned"], 8)
        self.assertEqual(report["inventory"]["entries_with_read_or_parse_errors"], 1)
        self.assertTrue(report["inventory"]["scan_complete"])

    def test_inventory_include_excluded_preserves_paths_and_shadowing(self):
        self.inventory_fixtures()
        report = self.inspect(list_launchers=True, include_excluded=True)
        self.assertEqual(len(report["launchers"]), 7)
        self.assertEqual(report["inventory"]["counts"]["included"], 7)
        self.assertEqual(report["inventory"]["counts"]["omitted"], 0)
        copies = [e for e in report["launchers"] if e["desktop_entry_id"] == "editor.desktop"]
        self.assertEqual({e["desktop_entry_path"] for e in copies},
                         {str(self.user / "editor.desktop"), str(self.system / "editor.desktop")})
        self.assertEqual({e["precedence"]["status"] for e in copies}, {"preferred", "shadowed"})
        self.assertEqual({e["visibility"]["status"] for e in copies}, {"excluded", "shadowed"})

    def test_inventory_alphabetical_sort_and_no_grouping(self):
        self.copy("editor.desktop", self.user / "aaa.desktop")
        self.copy("claude.desktop", self.system / "zzz.desktop")
        self.copy("claude.desktop", self.user / "independent.desktop")
        self.copy("broken.desktop", self.user / "broken.desktop")
        entries = self.inspect(list_launchers=True)["launchers"]
        self.assertEqual([e["display_name"] for e in entries], ["Claude", "Claude", "Sample Editor", None])
        self.assertEqual(len({e["desktop_entry_path"] for e in entries}), 4)

    def test_empty_inventory_is_success(self):
        for arguments in (("--list", "--json"), ("--list", "--include-excluded", "--json")):
            code, output, stderr = self.run_cli(*arguments)
            report = json.loads(output)
            self.assertEqual((code, stderr), (0, ""))
            self.assertEqual(report["launchers"], [])
            self.assertIsNone(report["error"])
            self.assertTrue(all(value == 0 for value in report["inventory"]["counts"].values()))

    def test_inventory_json_and_compact_human_output(self):
        self.copy("claude.desktop", self.user / "claude.desktop")
        code, output, stderr = self.run_cli("--list", "--json")
        report = json.loads(output)
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["search_mode"], "inventory")
        self.assertEqual(report["inventory"]["scope"], "desktop_launchers")
        entry = report["launchers"][0]
        for key in ("exec", "executable_path", "package_ownership", "desktop_entry_path", "visibility", "precedence", "issues"):
            self.assertIn(key, entry)
        self.assertEqual(entry["match_reasons"], [])
        code, output, stderr = self.run_cli("--list")
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("not an exhaustive inventory of installed software", output)
        self.assertIn("Claude | claude.desktop", output)
        self.assertIn("preferred/eligible", output)
        self.assertNotIn("\x1b", output)
        self.assertLess(len(output.splitlines()), 10)

    def test_human_default_output_is_a_compact_summary(self):
        self.copy("claude.desktop", self.user / "claude.desktop")
        self.copy("mask.desktop", self.user / "editor.desktop")
        self.copy("editor.desktop", self.system / "editor.desktop")
        env = self.base / "bin/env"
        env.parent.mkdir()
        env.write_text("never executed")
        env.chmod(0o755)
        # The user/system label reads the real environment, as the CLI does. HOME is
        # pinned because the temp directory may itself sit under the real home.
        self.enterContext(patch.dict(os.environ, {**self.env, "HOME": str(self.base / "home")}))
        code, output, stderr = self.run_cli("claude")
        self.assertEqual((code, stderr), (0, ""))
        lines = output.splitlines()
        self.assertEqual(lines[0], "Claude  (claude.desktop)  User-level, eligible")
        self.assertEqual(lines[1], f"  Launcher:    {self.user / 'claude.desktop'}")
        self.assertEqual(lines[2], "  Exec:        env WINEPREFIX=/home/example/.wine-claude wine Claude.exe")
        self.assertEqual(lines[3], f"  Executable:  {env}  (a wrapper: the real program is in Exec)")
        self.assertEqual(lines[4], "  dpkg owner:  launcher unknown; executable unknown")
        self.assertEqual(lines[6], "1 launcher matched. Add -v for every field, or --json for machine-readable output.")
        self.assertNotIn("WhatApp inspector:", output)
        self.assertNotIn("Generated by WhatApp", output)
        self.assertNotIn("\x1b", output)
        self.assertLess(len(lines), 10)
        code, output, _ = self.run_cli("Sample Editor")
        self.assertIn("(no name)  (editor.desktop)  User-level, excluded (hidden)", output)
        self.assertIn("  Exec:        (not set)", output)
        self.assertIn("Sample Editor  (editor.desktop)  System-level, shadowed\n", output)
        self.assertIn(f"  Shadowed by: {self.user / 'editor.desktop'}", output)
        self.assertIn("  Issues:      Exec executable was not found", output)
        self.assertIn("2 launchers matched.", output)
        with patch.dict(os.environ, {"CLICOLOR_FORCE": "1"}):
            _, coloured, _ = self.run_cli("claude")
        self.assertIn("\x1b[1mClaude\x1b[0m", coloured)
        self.assertIn("\x1b[32mUser-level, eligible\x1b[0m", coloured)
        self.assertIn("\x1b[2mLauncher:    \x1b[0m", coloured)

    def test_verbose_output_is_inspector_blocks_with_optional_colour(self):
        self.copy("editor.desktop", self.user / "editor.desktop")
        (self.user / "odd.desktop").write_text("[Desktop Entry]\nType=Application\nName=Sample\x1b[31m Odd\x07\nExec=odd\n")
        code, output, stderr = self.run_cli("-v", "sample")
        self.assertEqual((code, stderr), (0, ""))
        self.assertIn("WhatApp inspector: editor.desktop\n", output)
        self.assertIn("Matched on\n- display_name (substring)\n", output)
        self.assertIn("Raw Exec command\nsample-editor --open %U\n", output)
        self.assertIn("TryExec\n(not set)\n", output)
        self.assertIn("Notes\n- Original installation method is unknown", output)
        self.assertIn("Issues\n- Exec executable was not found", output)
        self.assertIn("\nGenerated by WhatApp", output)
        self.assertEqual(output.count("Generated by WhatApp"), 1)
        # Control characters from launcher files are made visible, never emitted.
        self.assertIn("Sample\\x1b[31m Odd\\x07", output)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        with patch.dict(os.environ, {"CLICOLOR_FORCE": "1"}):
            _, coloured, _ = self.run_cli("--verbose", "sample")
        self.assertIn("\x1b[1mRaw Exec command\x1b[0m", coloured)
        self.assertIn("\x1b[32meligible\x1b[0m", coloured)
        self.assertIn("Sample\\x1b[31m Odd", coloured)
        with patch.dict(os.environ, {"CLICOLOR_FORCE": "1", "NO_COLOR": "1"}):
            _, forced, _ = self.run_cli("-v", "sample")
        self.assertIn("\x1b[1m", forced)  # an explicit force wins over NO_COLOR

        class Tty(io.StringIO):
            def isatty(self):
                return True

        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(use_colour(Tty()))
            self.assertFalse(use_colour(io.StringIO()))
        with patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True):
            self.assertFalse(use_colour(Tty()))
        with patch.dict(os.environ, {"TERM": "dumb"}, clear=True):
            self.assertFalse(use_colour(Tty()))

    def test_inventory_partial_scan_keeps_uncertain_entries(self):
        self.copy("editor.desktop", self.system / "editor.desktop")
        self.copy("nodisplay.desktop", self.system / "helper.desktop")
        original_walk = os.walk

        def walk(root, **kwargs):
            if root == self.user:
                kwargs["onerror"](PermissionError(13, "Access denied", str(root)))
                return iter(())
            return original_walk(root, **kwargs)

        with patch("whatapp.cli.os.walk", side_effect=walk):
            report = self.inspect(list_launchers=True)
        self.assertFalse(report["inventory"]["scan_complete"])
        self.assertTrue(report["issues"])
        counts = report["inventory"]["counts"]
        self.assertEqual((counts["included"], counts["uncertain"], counts["excluded"], counts["eligible"]), (2, 2, 1, 0))
        self.assertTrue(all(e["visibility"]["status"] == "unknown" for e in report["launchers"]))

    def test_inventory_option_conflicts(self):
        for arguments in (("--list", "query"), ("--list", "--desktop-id", "editor.desktop"),
                          ("--list", "--desktop-file", "/editor.desktop"), ("--list", "--deep-search"),
                          ("--include-excluded", "query"), ("--include-excluded",)):
            code, output, stderr = self.run_cli("--json", *arguments)
            self.assertEqual((code, stderr), (2, ""))
            self.assertEqual(json.loads(output)["error"]["code"], "invalid_arguments")

    def test_inventory_ownership_queries_cached_and_details_preserved(self):
        for root in (self.user, self.system):
            self.copy("editor.desktop", root / "editor.desktop")
        binary = self.base / "bin/sample-editor"
        binary.parent.mkdir()
        binary.write_text("never executed")
        binary.chmod(0o755)
        calls = []

        def owners(path, issues):
            calls.append(path)
            return ["example:amd64"]

        report = inspect(env=self.env, list_launchers=True, include_excluded=True, owner_lookup=owners)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls.count(str(binary)), 1)
        for entry in report["launchers"]:
            self.assertEqual(entry["package_ownership"], {"desktop_entry": ["example:amd64"], "executable": ["example:amd64"]})

    def test_inventory_batches_only_returned_paths(self):
        self.copy("editor.desktop", self.user / "editor.desktop")
        self.copy("nodisplay.desktop", self.user / "hidden.desktop")
        target = str(self.user / "editor.desktop")
        with patch("whatapp.cli.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, f"example: {target}\n", "")
            report = inspect(env=self.env, list_launchers=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["dpkg-query", "--search", target])
        self.assertEqual(report["launchers"][0]["package_ownership"]["desktop_entry"], ["example"])


class OwnershipTests(unittest.TestCase):
    @patch("whatapp.cli.subprocess.run")
    def test_batch_partial_success_dedup_and_exact_paths(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "editor:amd64, shared: /bin/editor\nother: /bin/editor-extra\n", "unowned")
        cache = batch_package_owners(["/bin/editor", "/unowned", "/bin/editor"])
        self.assertEqual(run.call_args.args[0], ["dpkg-query", "--search", "/bin/editor", "/unowned"])
        self.assertEqual(cache["/bin/editor"][0], ["editor:amd64", "shared"])
        self.assertIsNone(cache["/unowned"][0])
        self.assertTrue(cache["/unowned"][2])

    @patch("whatapp.cli.subprocess.run")
    def test_batch_timeout_and_bounded_size(self, run):
        run.side_effect = subprocess.TimeoutExpired("dpkg-query", 5)
        cache = batch_package_owners([f"/path/{i}" for i in range(129)])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(cache), 129)
        self.assertTrue(all(value[0] is None and value[1] for value in cache.values()))

    @patch("whatapp.cli.subprocess.run")
    def test_multiarch_multiple_owners_and_exact_paths(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "editor:amd64, shared: /usr/bin/editor\nother: /usr/bin/editor-extra\n", "")
        self.assertEqual(package_owners("/usr/bin/editor", []), ["editor:amd64", "shared"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("whatapp.cli.subprocess.run")
    def test_unavailable_no_owner_and_timeout(self, run):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("dpkg-query", 5)):
            run.side_effect = failure
            notes = []
            self.assertIsNone(package_owners("/example", notes))
            self.assertTrue(notes)
        run.side_effect = None
        run.return_value = subprocess.CompletedProcess([], 1, "", "no path found")
        self.assertIsNone(package_owners("/example", []))


if __name__ == "__main__":
    unittest.main()
