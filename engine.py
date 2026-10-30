"""Pure message matching, independent of MaiBot."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Rule:
    name: str
    scope: str
    ids: tuple
    conditions: tuple
    reply: str


def compile_rules(data):
    result = []
    for item in data.get("rules", []):
        if not item.get("enabled", True):
            continue
        name = item.get("name", "")
        scope = item.get("scope", "global")
        ids = tuple(str(x) for x in item.get("ids", []))
        conditions = []
        if not name or scope not in ("global", "groups", "private"):
            raise ValueError("Invalid rule name or scope")
        if scope == "groups" and not ids:
            raise ValueError(f"{name}: groups requires ids")
        for c in item.get("conditions", []):
            kind = c["kind"]
            if kind not in ("image", "forward", "poke", "text_regex"):
                raise ValueError(f"{name}: unknown condition")
            try:
                pattern = re.compile(c["pattern"]) if kind == "text_regex" else None
            except re.error as exc:
                raise ValueError(f"{name}: invalid regex") from exc
            conditions.append((kind, pattern))
        if not conditions:
            raise ValueError(f"{name}: empty conditions")
        result.append(Rule(name, scope, ids, tuple(conditions), item.get("reply", "")))
    return tuple(result) if data.get("plugin", {}).get("enabled", data.get("enabled", True)) else ()


def inspect_message(message):
    kinds, texts = set(), []
    def walk(value, depth=0):
        if depth > 64:
            return
        if isinstance(value, list):
            run = []
            for v in value:
                if isinstance(v, dict) and v.get("type") == "text":
                    run.append(str(v.get("data", v.get("content", ""))))
                else:
                    if run:
                        texts.append("".join(run)); run = []
                    walk(v, depth + 1)
            if run:
                texts.append("".join(run))
        elif isinstance(value, dict):
            kind = value.get("type")
            if kind == "image":
                kinds.add("image")
            elif kind in ("forward", "node"):
                kinds.add("forward")
                walk(value.get("data", value.get("content")), depth + 1)
            elif kind == "text":
                texts.append(str(value.get("data", value.get("content", ""))))
            elif kind == "seglist":
                walk(value.get("data", []), depth + 1)
            else:
                for key in ("content", "message", "nodes"):
                    if key in value:
                        walk(value[key], depth + 1)
    walk(message.get("raw_message", message.get("message_segment", [])))
    event = message.get("event", {})
    if isinstance(event, dict) and event.get("sub_type", event.get("type")) == "poke":
        target, bot = event.get("target_id"), event.get("self_id")
        if target is not None and bot is not None and str(target) == str(bot):
            kinds.add("poke")
    return kinds, texts


def first_match(rules, message):
    info = message.get("message_info", message)
    group = (info.get("group_info") or {}).get("group_id")
    user = (info.get("user_info") or {}).get("user_id")
    kinds, texts = inspect_message(message)
    for r in rules:
        if r.scope == "groups" and (group is None or str(group) not in r.ids):
            continue
        if r.scope == "private" and (group is not None or (r.ids and str(user) not in r.ids)):
            continue
        if all(any(pattern.search(t) for t in texts) if kind == "text_regex" else kind in kinds for kind, pattern in r.conditions):
            return r
    return None
