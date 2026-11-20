from copy import deepcopy
from pathlib import Path
from support import Config, REAL_SDK, config, module, rule, standard
import tomllib
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from migrate import dump_config, migrate_config


class ConfigurationTests(unittest.TestCase):
    def test_example(self):
        data = tomllib.loads((Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf8"))
        self.assertEqual(len(Config.model_validate(data).routes), 5)

    def test_validation(self):
        for bad in [
            {"condition": [{"kind": "text_regex", "pattern": "["}]},
            {"condition": [{"kind": "interaction", "types": []}]},
            {"action": {"kind": "unknown"}},
            {"action": {"kind": "native", "reply": "x"}},
            {"action": {"kind": "follow_up"}},
            {"action": {"kind": "context_only", "window_seconds": 5}},
            {"planner_pass_probability": 0.1},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Config.model_validate(config([rule("block", **bad)]))
        for patch in [
            {"group": []},
            {"user": []},
            {"group": ["12*"]},
            {"user": [True]},
            {"use": ["typo"]},
            {"use": []},
        ]:
            data = standard()
            data["routes"][0].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                Config.model_validate(data)
        for seconds in [-1, True, 1.5, "60", 600]:
            data = standard()
            data["rule_groups"]["main"][0]["action"]["window_seconds"] = seconds
            with self.assertRaises(ValueError):
                Config.model_validate(data)

    def test_old_config_rejected_and_explicit_migration(self):
        old = {"plugin": {"config_version": "0.1.1"}, "rules": [
            {"name": "sender", "scope": "global", "conditions": [{"kind": "sender_qq", "ids": ["34"]}]},
            {"name": "regex", "conditions": [{"kind": "text_regex", "pattern": r"^x\\s$"}], "reply": "a\nb"},
        ], "group_context_only": [{"group_id": "12", "planner_pass_probability": 0}]}
        original = deepcopy(old)
        with self.assertRaises(ValueError):
            Config.model_validate(old)
        result = migrate_config(old)
        with self.assertRaises(ValueError):
            Config.model_validate(result)
        self.assertEqual(result["rules"][0]["user"], ["34"])
        self.assertEqual(result["rules"][1]["condition"], old["rules"][1]["conditions"])
        self.assertEqual(result["rules"][1]["action"]["reply"], "a\nb")
        self.assertEqual(tomllib.loads(dump_config(result)), result)
        self.assertEqual(migrate_config(result), result)
        self.assertEqual(old, original)

    @unittest.skipUnless(REAL_SDK, "Validate real SDK schema in staging container")
    def test_schema_and_editor_roundtrip(self):
        schema = module.WatchDogPlugin.build_config_schema()
        fields = next(
            s["fields"] for s in schema["sections"].values() if "routes" in s["fields"]
        )
        self.assertNotIn("rule_groups", fields)
        for k in ("group", "user", "use"):
            self.assertEqual(fields["routes"]["item_fields"][k]["item_type"], "string")
