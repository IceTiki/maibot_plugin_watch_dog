import unittest
from engine import compile_rules, first_match

class RulesTest(unittest.TestCase):
    def test_match_and_scope(self):
        rules = compile_rules({"rules": [{"name":"x", "scope":"groups", "ids":["1"], "conditions":[{"kind":"image"},{"kind":"text_regex","pattern":"spam"}]}]})
        msg={"group_info":{"group_id":1},"raw_message":[{"type":"image","data":"x"},{"type":"text","data":"spam"}]}
        self.assertIsNotNone(first_match(rules,msg))
        msg["group_info"]["group_id"]=2
        self.assertIsNone(first_match(rules,msg))
    def test_invalid(self):
        for conditions in ([], [{"kind":"text_regex","pattern":"["}]):
            with self.assertRaises(ValueError):
                compile_rules({"rules":[{"name":"x","conditions":conditions}]})
    def test_nested(self):
        rules=compile_rules({"rules":[{"name":"x","conditions":[{"kind":"image"},{"kind":"forward"}]}]})
        self.assertIsNotNone(first_match(rules,{"raw_message":[{"type":"forward","data":[{"content":[{"type":"image","data":"x"}]}]}]}))

if __name__ == "__main__": unittest.main()
