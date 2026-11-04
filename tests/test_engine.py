import unittest
from engine import compile_rules, first_match

class RulesTest(unittest.TestCase):
    def test_match_and_scope(self):
        rules = compile_rules({"rules": [{"name":"x", "scope":"groups", "ids":["1"], "conditions":[{"kind":"image"},{"kind":"text_regex","pattern":"spam"}]}]})
        msg={"group_info":{"group_id":1},"raw_message":[{"type":"image","data":"x"},{"type":"text","data":"spam"}]}
        self.assertIsNotNone(first_match(rules,msg))
        msg["group_info"]["group_id"]=2
        self.assertIsNone(first_match(rules,msg))
    def test_sender_qq(self):
        sender = {"kind": "sender_qq", "ids": ["33", 44]}
        group = {"name":"group image", "scope":"groups", "ids":["11"],
                 "conditions":[sender, {"kind":"image"}]}
        global_rule = {"name":"all senders", "conditions":[sender]}
        rules = compile_rules({"rules":[group, global_rule]})
        info = {"group_info":{"group_id":"11"}, "user_info":{"user_id":44}}
        msg = {"platform":"qq", "message_info":info, "raw_message":[{"type":"image"}]}
        self.assertEqual(first_match(rules,msg).name, "group image")
        msg["raw_message"] = []
        self.assertEqual(first_match(rules,msg).name, "all senders")
        info.pop("group_info")
        self.assertEqual(first_match(rules,msg).name, "all senders")
        msg["platform"] = "other"
        self.assertIsNone(first_match(rules,msg))
        msg["platform"] = "qq"
        info["user_info"]["user_id"] = "11"
        self.assertIsNone(first_match(rules,msg))
        for invalid in (None, [], "44", [True], ["x"]):
            with self.assertRaises(ValueError):
                compile_rules({"rules":[{"name":"bad","conditions":[{"kind":"sender_qq","ids":invalid}]}]})

    def test_poke_notice(self):
        rules = compile_rules({"rules":[{"name":"poke","conditions":[{"kind":"poke"}]}]})
        for target in ("bot", "peer"):
            payload = {"notice_type":"notify","sub_type":"poke","target_id":target,"self_id":"bot"}
            for notice in ({"napcat_notice_payload":payload},{"napcat_notice_sub_type":"poke"}):
                self.assertIsNotNone(first_match(rules, {"message_info":{"additional_config":notice}}))
            self.assertIsNotNone(first_match(rules, {"event":payload}))
        self.assertIsNone(first_match(rules, {"raw_message":[{"type":"text","data":"戳一戳"}],
            "message_info":{"additional_config":{"napcat_notice_sub_type":"lucky_king"}}}))

    def test_invalid(self):
        for conditions in ([], [{"kind":"text_regex","pattern":"["}]):
            with self.assertRaises(ValueError):
                compile_rules({"rules":[{"name":"x","conditions":conditions}]})
    def test_nested(self):
        rules=compile_rules({"rules":[{"name":"x","conditions":[{"kind":"image"},{"kind":"forward"}]}]})
        self.assertIsNotNone(first_match(rules,{"raw_message":[{"type":"forward","data":[{"content":[{"type":"image","data":"x"}]}]}]}))

if __name__ == "__main__": unittest.main()
