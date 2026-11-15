from support import Config, config, engine, rule
import unittest


class MatchingTests(unittest.TestCase):
    def test_scope_and_conditions(self):
        r = engine.compile_rules(Config.model_validate(config([
            rule("block", [{"kind": "image"}, {"kind": "text_regex", "pattern": "spam"}],
                 group=["12"], user=["34"])
        ])).model_dump())[0]
        msg = {"group_info": {"group_id": 12}, "user_info": {"user_id": 34},
               "raw_message": [{"type": "image"}, {"type": "text", "data": "spam"}]}
        self.assertTrue(engine.scope_matches(r, msg))
        self.assertTrue(engine.static_matches(r, *engine.inspect_message(msg)))
        msg["user_info"]["user_id"] = "35"
        self.assertFalse(engine.scope_matches(r, msg))
        msg["raw_message"] = [{"type": "image", "data": "spam"}]
        self.assertFalse(engine.static_matches(r, *engine.inspect_message(msg)))

    def test_nested_forward_and_no_cross_node_text(self):
        msg = {"raw_message": [{"type": "forward", "data": {"nodes": [
            {"type": "node", "data": {"content": [{"type": "text", "data": "hel"}]}},
            {"type": "node", "data": {"content": [{"type": "text", "data": "lo"}, {"type": "image"}]}}
        ]}}]}
        kinds, texts = engine.inspect_message(msg)
        self.assertIn("image", kinds)
        self.assertIn("forward", kinds)
        self.assertNotIn("hello", texts)
        msg["raw_message"] = [{"type": "text", "data": "hel"}, {"type": "text", "data": {"text": "lo"}}]
        self.assertEqual(engine.inspect_message(msg)[1], ["hello"])

    def test_poke_structured_only(self):
        for target, expected in [(9, "poke_bot"), (8, "poke_except_bot"), (None, "poke_except_bot")]:
            payload = {"sub_type": "poke", "self_id": 9, "target_id": target}
            for msg in [{"event": payload}, {"message_info": {"additional_config": {"napcat_notice_payload": payload}}}]:
                self.assertEqual(engine.poke_kind(msg), expected)
        self.assertIsNone(engine.poke_kind({"raw_message": [{"type": "text", "data": "戳一戳"}]}))

    def test_windows_expiry_renewal_and_isolation(self):
        tracker = engine.WindowTracker()
        key = (0, "9", "session", "34")
        tracker.interact(key, 0, 60, 300)
        tracker.speak(key, 30)
        self.assertEqual(tracker.windows[key].deadline, 60)
        tracker.renew_sent("elsewhere", "9", 40)
        self.assertEqual(tracker.windows[key].deadline, 60)
        tracker.renew_sent("session", "8", 40)
        self.assertEqual(tracker.windows[key].deadline, 60)
        tracker.renew_sent("session", "9", 40)
        self.assertEqual(tracker.windows[key].deadline, 100)
        for now in (90, 140, 190, 240, 290):
            tracker.interact(key, now, 60, 300)
        self.assertEqual(tracker.windows[key].deadline, 300)
        self.assertFalse(tracker.active(key, 300))
        tracker.interact(key, 301, 60, 300)
        self.assertEqual(tracker.windows[key].cap_at, 601)
