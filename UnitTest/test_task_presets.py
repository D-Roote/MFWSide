"""Portable preset data validation without Qt or native runtime initialization."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.taskPresets import clean_preset_name, make_task_preset, normalize_task_presets, sanitize_tasks


INTERFACE = {"option": {
    "Mode": {"cases": [{"name": "A"}, {"name": "B"}]},
    "Flags": {"type": "checkbox", "cases": [{"name": "X"}, {"name": "Y"}]},
    "Input": {"type": "input", "inputs": [
        {"name": "Account"}, {"name": "Password", "password": True}, {"name": "Count"}]},
}}
TASK = {"name": "Task", "entry": "Entry", "checked": True,
        "selected_options": {"Mode": ["B"], "Input": {"Account": "user", "Password": "secret"}}}


class TaskPresetTests(unittest.TestCase):
    def test_name_is_trimmed_and_validated(self):
        self.assertEqual(clean_preset_name("  일일 작업  "), "일일 작업")
        for invalid in (None, 42, "", "  ", "a\nb", "a\rb", "a\0b", "x" * 81):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                clean_preset_name(invalid)

    def test_make_uses_unique_ids_and_requires_tasks(self):
        first = make_task_preset("One", [TASK], INTERFACE)
        second = make_task_preset("Two", [TASK], INTERFACE)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(make_task_preset("One", [TASK], INTERFACE, "existing")["id"], "existing")
        with self.assertRaises(ValueError):
            make_task_preset("Empty", [], INTERFACE)

    def test_sanitization_removes_secrets_and_unknown_fields(self):
        task = deepcopy(TASK)
        task["selected_options"].update({"Removed": ["X"], "Input": {
            "Account": "user", "Password": "secret", "Count": 3, "Undeclared": "hidden"}})
        task["controller"] = {"address": "private"}
        result = sanitize_tasks([task], INTERFACE)[0]
        self.assertEqual(set(result), {"name", "entry", "checked", "selected_options"})
        self.assertEqual(result["selected_options"], {"Mode": ["B"], "Input": {"Account": "user"}})

    def test_choices_are_declared_unique_and_single_select_is_bounded(self):
        task = {**TASK, "selected_options": {"Mode": ["B", "A", "Removed", "A"],
                                             "Flags": ["Y", "X", "Y", "Removed"]}}
        result = sanitize_tasks([task], INTERFACE)[0]["selected_options"]
        self.assertEqual(result, {"Mode": ["A"], "Flags": ["X", "Y"]})

    def test_missing_tasks_and_duplicates_remain_ordered(self):
        records = [TASK, {"name": "Gone", "entry": "Missing", "checked": False}, TASK]
        result = sanitize_tasks(records, INTERFACE)
        self.assertEqual([task["name"] for task in result], ["Task", "Gone", "Task"])
        result[0]["selected_options"]["Mode"].append("A")
        self.assertEqual(result[2]["selected_options"]["Mode"], ["B"])
        self.assertEqual(TASK["selected_options"]["Mode"], ["B"])

    def test_invalid_task_records_are_ignored_and_checked_is_strict(self):
        records = [None, "bad", {}, {"name": 1}, {"entry": "Legacy", "checked": "false"},
                   {"name": "Valid", "checked": 1, "selected_options": {"Mode": "A", "Input": []}}]
        self.assertEqual(sanitize_tasks(records, INTERFACE), [
            {"name": "", "entry": "Legacy", "checked": False, "selected_options": {}},
            {"name": "Valid", "entry": "", "checked": False, "selected_options": {}}])
        self.assertEqual(sanitize_tasks({}, INTERFACE), [])

    def test_unsupported_and_malformed_versions_do_not_load(self):
        for data in (None, [], {}, {"version": True, "items": []}, {"version": 2, "items": []},
                     {"version": 1, "items": {}}, {"version": "1", "items": []}):
            with self.subTest(data=data):
                self.assertEqual(normalize_task_presets(data, INTERFACE), [])

    def test_loading_skips_malformed_and_duplicate_ids_and_resanitizes(self):
        valid = {"id": "one", "name": " One ", "tasks": [TASK]}
        data = {"version": 1, "items": [None, {}, valid, valid,
                {"id": "two", "name": "", "tasks": [TASK]},
                {"id": "three", "name": "Empty", "tasks": []},
                {"id": " ", "name": "Bad id", "tasks": [TASK]}]}
        result = normalize_task_presets(data, INTERFACE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "One")
        self.assertNotIn("Password", result[0]["tasks"][0]["selected_options"]["Input"])


if __name__ == "__main__":
    unittest.main()
