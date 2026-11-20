"""Ordered matching and in-memory follow-up windows."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Rule:
    index: tuple[str, int]
    name: str
    conditions: tuple
    action: str
    reply: str
    window_seconds: int
    max_active_seconds: int


@dataclass(frozen=True)
class Route:
    groups: tuple[str, ...]
    users: tuple[str, ...]
    rules: tuple[Rule, ...]


def compile_routes(data: dict) -> tuple[Route, ...]:
    """Compile groups once and resolve route references in declaration order."""
    if not data["plugin"]["enabled"]:
        return ()
    groups = {}
    for group, rows in data["rule_groups"].items():
        compiled = []
        for i, row in enumerate(rows, 1):
            if not row["enabled"]:
                continue
            action = row["action"]
            conditions = []
            for condition in row["condition"]:
                kind = condition["kind"]
                value = None
                if kind == "text_regex":
                    value = re.compile(condition["pattern"])
                elif kind == "interaction":
                    value = frozenset(condition["types"])
                conditions.append((kind, value))
            compiled.append(
                Rule(
                    (group, i),
                    f"{group}/{row['name'] or i}",
                    tuple(conditions),
                    action["kind"],
                    action["reply"],
                    action["window_seconds"] or 60,
                    action["max_active_seconds"] or 300,
                )
            )
        groups[group] = compiled
    return tuple(
        Route(
            tuple(route["group"]),
            tuple(route["user"]),
            tuple(rule for key in route["use"] for rule in groups[key]),
        )
        for route in data["routes"]
    )


def select_rules(routes: tuple[Route, ...], message: dict) -> tuple[Rule, ...]:
    """Select exactly one route even if its rule chain is empty or has no match."""
    return next((route.rules for route in routes if scope_matches(route, message)), ())


def message_info(message):
    return message.get("message_info") or message


def poke_kind(message):
    info = message_info(message)
    notice = info.get("additional_config") or {}
    payload = notice.get("napcat_notice_payload")
    event = message.get("event")
    if notice.get("napcat_notice_sub_type") == "poke" or (
        isinstance(payload, dict) and payload.get("sub_type") == "poke"
    ):
        record = payload if isinstance(payload, dict) else {}
    elif isinstance(event, dict) and event.get("sub_type", event.get("type")) == "poke":
        record = event
    else:
        return None
    target = str(record.get("target_id") or "")
    account = str(record.get("self_id") or message.get("account_id") or "")
    return "poke_bot" if target.isascii() and target.isdigit() and target == account else "poke_except_bot"


def outer_segments(message):
    raw = message.get("raw_message", message.get("message_segment", []))
    def walk(value, depth=0):
        if depth > 64:
            return
        if isinstance(value, list):
            for segment in value:
                yield from walk(segment, depth + 1)
        elif isinstance(value, dict):
            if value.get("type") == "seglist":
                yield from walk(value.get("data", []), depth + 1)
            else:
                yield value
    return list(walk(raw))


def text_value(segment):
    data = segment.get("data", segment.get("content", ""))
    if isinstance(data, dict):
        return str(data.get("text", ""))
    return data if isinstance(data, str) else ""


def adjacent_texts(segments):
    texts, run = [], []
    for segment in segments:
        if segment.get("type") == "text":
            run.append(text_value(segment))
        elif run:
            texts.append("".join(run))
            run = []
    if run:
        texts.append("".join(run))
    return texts


def inspect_message(message):
    kinds, texts = set(), []
    def walk(value, depth=0):
        if depth > 64:
            return
        if isinstance(value, list):
            segments = [v for v in value if isinstance(v, dict)]
            texts.extend(adjacent_texts(segments))
            for segment in segments:
                if segment.get("type") != "text":
                    walk(segment, depth + 1)
        elif isinstance(value, dict):
            kind = value.get("type")
            if kind == "image":
                kinds.add("image")
            elif kind in ("forward", "node"):
                kinds.add("forward")
                walk(value.get("data", value.get("content")), depth + 1)
            elif kind == "text":
                texts.append(text_value(value))
            elif kind == "seglist":
                walk(value.get("data", []), depth + 1)
            elif kind != "reply":
                for key in ("content", "message", "nodes"):
                    if key in value:
                        walk(value[key], depth + 1)
    walk(message.get("raw_message", message.get("message_segment", [])))
    poke = poke_kind(message)
    if poke:
        kinds.update(("poke", poke))
    return kinds, texts


def ordinary_message(message, account):
    info = message_info(message)
    user = str((info.get("user_info") or {}).get("user_id") or "")
    notice = info.get("additional_config") or {}
    return bool(user) and user != account and not (
        message.get("is_notify") or poke_kind(message) or notice.get("napcat_notice_type")
    )


def scope_matches(rule, message):
    info = message_info(message)
    group = (info.get("group_info") or {}).get("group_id")
    group_key = str(group) if group is not None and str(group) else "private"
    user = str((info.get("user_info") or {}).get("user_id") or "")
    return ("*" in rule.groups or group_key in rule.groups) and (
        "*" in rule.users or user in rule.users
    )


def static_matches(rule, kinds, texts):
    for kind, value in rule.conditions:
        if kind == "text_regex" and not any(value.search(text) for text in texts):
            return False
        if kind not in ("text_regex", "interaction") and kind not in kinds:
            return False
    return True


@dataclass
class Window:
    deadline: float
    cap_at: float
    last_spoke_at: float
    seconds: int


class WindowTracker:
    """Per-rule/account/session/user windows; renewal follows gsskk's MIT logic."""

    def __init__(self):
        self.windows = {}

    def expire(self, now):
        for key, window in list(self.windows.items()):
            if window.deadline <= now:
                del self.windows[key]

    def active(self, key, now):
        window = self.windows.get(key)
        return window is not None and window.deadline > now

    def interact(self, key, now, seconds, maximum):
        if self.active(key, now):
            window = self.windows[key]
            window.deadline = min(now + seconds, window.cap_at)
            window.last_spoke_at = now
        else:
            self.windows[key] = Window(now + seconds, now + maximum, now, seconds)

    def speak(self, key, now):
        if self.active(key, now):
            self.windows[key].last_spoke_at = now

    def renew_sent(self, session, account, now):
        self.expire(now)
        for (_, bot, stream, _), window in self.windows.items():
            if stream == session and (not account or account == bot) and now - window.last_spoke_at <= window.seconds:
                window.deadline = min(now + window.seconds, window.cap_at)

    def clear(self):
        self.windows.clear()
