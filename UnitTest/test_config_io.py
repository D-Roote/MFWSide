import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.configIO import write_json
from app.projectConfig import selected_resource


class ConfigIOTests(unittest.TestCase):
    def test_failed_replace_preserves_old_data_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            write_json(path, {"old": True})
            with patch.object(Path, "replace", side_effect=PermissionError("denied")), self.assertRaises(OSError):
                write_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(folder).iterdir()), [path])
            write_json(path, {"new": "한글"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"new": "한글"})

    def test_malformed_resource_is_a_user_facing_validation_error(self):
        for resources in (None, {}, "resource", [], ["invalid"], [None], [{}],
                          [{"path": "resource"}], [{"path": [" "]}], [{"path": [123]}]):
            with self.subTest(resources=resources), self.assertRaises(ValueError):
                selected_resource({"resource": resources})

    def test_first_resource_preserves_overlay_order_without_multi_selection(self):
        first = {"name": "Selected", "path": ["resource/base", "resource/overlay"]}
        self.assertIs(selected_resource({"resource": [first, {"path": ["other"]}]}), first)
