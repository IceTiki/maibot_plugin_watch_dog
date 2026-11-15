"""Explicit v0.3 -> v0.4 migration to the user's global interaction policy."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import tomllib


def migrate_config(old):
    if old.get("plugin", {}).get("config_version") == "0.4.0":
        return deepcopy(old)
    converted = []
    for row in old.get("rules", []):
        scope = row.get("scope", "global")
        ids = list(map(str, row.get("ids", [])))
        if scope == "global":
            groups, users = ["*"], ["*"]
        elif scope == "groups" and ids:
            groups, users = ids, ["*"]
        elif scope == "private":
            groups, users = ["private"], ids or ["*"]
        else:
            raise ValueError("旧规则包含无法迁移的 scope")
        conditions = []
        for condition in row.get("conditions", []):
            if condition["kind"] == "sender_qq":
                senders = list(map(str, condition["ids"]))
                users = senders if users == ["*"] else [u for u in users if u in senders]
                if not users:
                    raise ValueError("发送者交集为空，请人工处理该规则")
            else:
                conditions.append(deepcopy(condition))
        action = {"kind": "block"}
        if row.get("reply"):
            action["reply"] = row["reply"]
        rule = {"name": row["name"], "group": groups, "user": users,
                "condition": conditions, "action": action}
        if not row.get("enabled", True):
            rule["enabled"] = False
        converted.append(rule)
    converted.extend([
        {"name": "互动开窗及连续追问", "group": ["*"], "user": ["*"],
         "condition": [{"kind": "interaction", "types": ["nickname", "at_bot", "reply_to_bot"]}],
         "action": {"kind": "follow_up"}},
        {"name": "其余消息只入上下文", "group": ["*"], "user": ["*"],
         "condition": [], "action": {"kind": "context_only"}},
    ])
    return {"plugin": {"config_version": "0.4.0", "enabled": old.get("plugin", {}).get("enabled", True)},
            "follow_up": {"window_seconds": 60, "max_active_seconds": 300}, "rules": converted}


def toml_value(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(key + " = " + toml_value(item) for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def dump_config(data):
    lines = []
    for section in ("plugin", "follow_up"):
        lines.append("[" + section + "]")
        lines.extend(key + " = " + toml_value(value) for key, value in data[section].items())
        lines.append("")
    for rule in data["rules"]:
        lines.append("[[rules]]")
        lines.extend(key + " = " + toml_value(value) for key, value in rule.items())
        lines.append("")
    text = "\n".join(lines)
    assert tomllib.loads(text) == data
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = migrate_config(tomllib.loads(args.source.read_text(encoding="utf-8")))
    args.output.write_text(dump_config(data), encoding="utf-8")
