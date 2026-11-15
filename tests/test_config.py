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
        self.assertEqual(len(Config.model_validate(data).rules), 4)

    def test_validation(self):
        for bad in [
            {"group": []}, {"user": []}, {"group": ["12*"]}, {"user": [True]},
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
        for bad in [0, -1, True, 1.5, "60"]:
            data = standard()
            data["follow_up"]["window_seconds"] = bad
            with self.assertRaises(ValueError):
                Config.model_validate(data)
        data = standard()
        data["rules"][0]["action"]["window_seconds"] = 600
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
        fields = next(s["fields"]["rules"]["item_fields"] for s in schema["sections"].values() if "rules" in s["fields"])
        self.assertNotIn("action", fields)
        self.assertEqual(fields["condition"]["item_type"], "object")
        self.assertTrue(fields["condition"]["item_fields"]["types"]["multiple"])
        self.assertEqual(fields["user"]["item_type"], "string")
        data = Config.model_validate(standard()).model_dump()
        old_action = deepcopy(data["rules"][0]["action"])
        # ListFieldEditor spreads existing row data before replacing a field.
        data["rules"][0] = {**data["rules"][0], "name": "edited"}
        self.assertEqual(Config.model_validate(data).rules[0].action.model_dump(), old_action)
