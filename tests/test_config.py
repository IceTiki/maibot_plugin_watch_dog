import unittest
import tomllib
from pathlib import Path
from engine import compile_rules

class ConfigurationTests(unittest.TestCase):
    def test_example_has_required_metadata_and_top_level_rules(self):
        data = tomllib.loads((Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["plugin"]["config_version"], "0.1.1")
        self.assertEqual(len(compile_rules(data)), 2)
        self.assertNotIn("rules", data["plugin"])

    def test_nested_switch_disables_all_rules(self):
        data = {"plugin": {"enabled": False}, "rules": [{"name": "x", "conditions": [{"kind": "image"}]}]}
        self.assertEqual(compile_rules(data), ())
        data["plugin"]["enabled"] = True
        self.assertEqual(len(compile_rules(data)), 1)
