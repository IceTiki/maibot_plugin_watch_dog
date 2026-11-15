"""Watch Dog v0.4: first complete match wins."""
from collections import OrderedDict
from pathlib import Path
import asyncio
import importlib.util
import sys
import time

from maibot_sdk import HookHandler, MaiBotPlugin
from maibot_sdk.types import HookMode, HookOrder


def _load_sibling(stem):
    # Host loads entries by path without necessarily creating a package.
    name = __name__ + "_watch_dog_" + stem
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(stem + ".py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Watch Dog {stem}.py")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module  # Dataclasses resolve the module during loading.
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


engine = _load_sibling("engine")
settings = _load_sibling("settings")


class WatchDogPlugin(MaiBotPlugin):
    config_model = settings.WatchDogConfig
    config_reload_subscriptions = ("bot",)
    _now = staticmethod(time.monotonic)

    @classmethod
    def build_config_schema(cls, **kwargs):
        return settings.complete_rule_schema(super().build_config_schema(**kwargs))

    async def on_load(self):
        self._lock = asyncio.Lock()
        self._tasks = set()
        self._inflight = {}
        self._windows = engine.WindowTracker()
        self._recent = OrderedDict()
        self._names = ()
        self._names_until = 0.0
        data = self.config_model.model_validate(self.get_plugin_config_data()).model_dump()
        self._rules = engine.compile_rules(data)
        self.ctx.logger.info("Watch Dog v0.4.0 已加载，规则 %s 条", len(self._rules))

    async def on_unload(self):
        pending = [*self._tasks, *self._inflight.values()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._windows.clear()
        self._recent.clear()

    async def on_config_update(self, scope, config_data, version):
        self._names_until = 0.0
        if scope != "self":
            return
        try:
            data = self.config_model.model_validate(config_data).model_dump()
            rules = engine.compile_rules(data)
        except (ValueError, KeyError, TypeError) as exc:
            # Report field locations without dumping the supplied config.
            details = type(exc).__name__
            if isinstance(exc, settings.ValidationError):
                details = "; ".join(
                    ".".join(map(str, error["loc"])) + " " + error["msg"]
                    for error in exc.errors(include_input=False, include_context=False)
                )
            self.ctx.logger.error("Watch Dog 配置校验失败，保留上一份有效配置：%s", details)
            return
        async with self._lock:
            self._rules = rules
            self._windows.clear()
            self._recent.clear()
        self.ctx.logger.info("Watch Dog 规则已更新，跟进窗口已清空")

    async def _resolve_stream_id(self, message, kwargs):
        for source in (message, kwargs):
            for key in ("stream_id", "session_id", "chat_id"):
                if source.get(key):
                    return str(source[key])
        info = engine.message_info(message)
        group, user = info.get("group_info") or {}, info.get("user_info") or {}
        platform = message.get("platform") or info.get("platform") or group.get("platform") or user.get("platform")
        if not platform:
            raise ValueError("Missing platform")
        if group.get("group_id") is not None:
            kind, target = "group", {"group_id": str(group["group_id"])}
        elif user.get("user_id") is not None:
            kind, target = "private", {"user_id": str(user["user_id"])}
        else:
            raise ValueError("Missing recipient")
        additional = info.get("additional_config") or message.get("additional_config") or {}
        for key in ("account_id", "scope"):
            value = info.get(key) or message.get(key) or additional.get(key)
            if key == "account_id":
                value = value or additional.get("platform_io_account_id")
            if value:
                target[key] = str(value)
        session = await self.ctx.chat.open_session(platform=str(platform), chat_type=kind, **target)
        identity = session.get("stream_id") or session.get("session_id")
        if not identity:
            raise ValueError("Missing session identity")
        return str(identity)

    async def _reply(self, text, message, kwargs):
        async def send():
            stream = await self._resolve_stream_id(message, kwargs)
            return await self.ctx.send.text(text, stream)
        try:
            if await asyncio.wait_for(send(), 5) is False:
                self.ctx.logger.warning("Watch Dog 自动回复失败，消息 %s 保持屏蔽", message.get("message_id"))
        except Exception as exc:
            self.ctx.logger.warning("Watch Dog 自动回复失败（%s），消息 %s 保持屏蔽",
                                    type(exc).__name__, message.get("message_id"))

    async def _append_context(self, message, kwargs):
        stream = await self._resolve_stream_id(message, kwargs)
        result = await self.ctx.maisaka.context.append(
            stream, message["raw_message"], message_id=message["message_id"],
            user_info=engine.message_info(message).get("user_info") or {},
            replyable=True, source_kind="plugin:local.watch-dog",
        )
        if not isinstance(result, dict) or not result.get("success"):
            raise RuntimeError("Context append not confirmed")

    async def _bot_account(self, message):
        account = str(message.get("account_id") or "")
        if not account.isdigit():
            account = str(await asyncio.wait_for(self.ctx.config.get("bot.qq_account", ""), 1) or "")
        return account

    async def _bot_names(self):
        now = self._now()
        if now >= self._names_until:
            name, aliases = await asyncio.wait_for(asyncio.gather(
                self.ctx.config.get("bot.nickname", ""),
                self.ctx.config.get("bot.alias_names", []),
            ), 1)
            aliases = aliases if isinstance(aliases, list) else []
            self._names = tuple(x.strip() for x in [name, *aliases] if isinstance(x, str) and x.strip())
            self._names_until = now + 30
        return self._names

    async def _interaction_types(self, message, kwargs, account, wanted):
        matched = set()
        info = engine.message_info(message)
        sender = str((info.get("user_info") or {}).get("user_id") or "")
        if not sender or sender == account:
            return matched
        ordinary = engine.ordinary_message(message, account)
        if "poke_bot" in wanted and engine.poke_kind(message) == "poke_bot":
            matched.add("poke_bot")
        if not ordinary:
            return matched
        matched.add("message")
        segments = engine.outer_segments(message)
        if "at_bot" in wanted and account:
            for segment in segments:
                data = segment.get("data")
                target = data.get("target_user_id", data.get("qq")) if isinstance(data, dict) else data
                if segment.get("type") == "at" and str(target) == account:
                    matched.add("at_bot")
        if "nickname" in wanted:
            try:
                names = await self._bot_names()
                if any(name in text for text in engine.adjacent_texts(segments) for name in names):
                    matched.add("nickname")
            except Exception as exc:
                self.ctx.logger.warning("Watch Dog 昵称读取失败（%s），消息 %s",
                                        type(exc).__name__, message.get("message_id"))
        if "reply_to_bot" in wanted and account:
            for segment in segments:
                if segment.get("type") != "reply":
                    continue
                data = segment.get("data")
                data = data if isinstance(data, dict) else {"target_message_id": data}
                author = data.get("target_message_sender_id")
                if author is None and data.get("target_message_id"):
                    try:
                        async def lookup():
                            stream = await self._resolve_stream_id(message, kwargs)
                            return await self.ctx.message.get_by_id(str(data["target_message_id"]), stream_id=stream)
                        original = await asyncio.wait_for(lookup(), 1.5) or {}
                        author = (engine.message_info(original).get("user_info") or {}).get("user_id")
                    except Exception as exc:
                        self.ctx.logger.warning("Watch Dog 引用作者读取失败（%s），消息 %s",
                                                type(exc).__name__, message.get("message_id"))
                if str(author) == account:
                    matched.add("reply_to_bot")
        return matched

    @staticmethod
    def _message_key(message):
        info = engine.message_info(message)
        group = str((info.get("group_info") or {}).get("group_id") or "")
        user = str((info.get("user_info") or {}).get("user_id") or "")
        identity = message.get("message_id")
        if not identity:
            return None
        return (str(message.get("account_id") or ""), str(message.get("scope") or ""),
                group, user, str(identity))

    def _cleanup(self, now):
        self._windows.expire(now)
        while self._recent:
            key, record = next(iter(self._recent.items()))
            if record[0] > now and len(self._recent) < 10000:
                break
            self._recent.pop(key)

    async def _evaluate(self, message, kwargs):
        rules = self._rules
        kinds, texts = engine.inspect_message(message)
        account = str(message.get("account_id") or "")
        user = str((engine.message_info(message).get("user_info") or {}).get("user_id") or "")
        interactions = None
        wanted = frozenset(
            value for rule in rules for kind, values in rule.conditions
            if kind == "interaction" for value in values
        )
        for rule in rules:
            if not engine.scope_matches(rule, message) or not engine.static_matches(rule, kinds, texts):
                continue
            interaction_conditions = [value for kind, value in rule.conditions if kind == "interaction"]
            window_key = None
            active = False
            if interaction_conditions:
                try:
                    account = await self._bot_account(message)
                    if interactions is None:
                        interactions = await self._interaction_types(message, kwargs, account, wanted)
                    if rule.action == "follow_up":
                        stream = await asyncio.wait_for(self._resolve_stream_id(message, kwargs), 2)
                        if not account or not user or not message.get("message_id"):
                            raise ValueError("Missing follow-up identity")
                        window_key = (rule.index, account, stream, user)
                        active = engine.ordinary_message(message, account) and self._windows.active(
                            window_key, self._now())
                except Exception as exc:
                    self.ctx.logger.warning("Watch Dog 无法确认互动（%s），消息 %s",
                                            type(exc).__name__, message.get("message_id"))
                    continue
                if rules is not self._rules:
                    return await self._evaluate(message, kwargs)
                if not all(active or bool(types & interactions) for types in interaction_conditions):
                    continue
            self.ctx.logger.info("Watch Dog 命中规则 %s，动作 %s，消息 %s",
                                 rule.name, rule.action, message.get("message_id", "unknown"))
            if rule.action == "follow_up":
                now = self._now()
                if all(bool(types & interactions) for types in interaction_conditions):
                    self._windows.interact(window_key, now, rule.window_seconds, rule.max_active_seconds)
                else:
                    self._windows.speak(window_key, now)
                return "continue", True
            if rule.action == "native":
                return "continue", False
            if rule.action == "context_only":
                try:
                    await asyncio.wait_for(self._append_context(message, kwargs), 8)
                except Exception as exc:
                    self.ctx.logger.warning("Watch Dog 上下文追加失败（%s），消息 %s 保持拦截",
                                            type(exc).__name__, message.get("message_id"))
            elif rule.reply:
                task = asyncio.create_task(self._reply(rule.reply, message, kwargs))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            return "abort", False
        return "continue", False

    @HookHandler("chat.receive.before_process", mode=HookMode.BLOCKING, order=HookOrder.EARLY, timeout_ms=15000)
    async def filter_message(self, **kwargs):
        message = kwargs.get("message") or {}
        info = engine.message_info(message)
        if (message.get("platform") or info.get("platform")) != "qq":
            return {"action": "continue"}
        self._cleanup(self._now())
        key = self._message_key(message)
        cached = self._recent.get(key)
        if cached is not None:
            return {"action": cached[1]}
        # Different messages proceed concurrently; duplicates share one operation.
        # Only window mutations are synchronous, and config identity is rechecked
        # after asynchronous interaction lookups.
        if key is None:
            action, _ = await self._evaluate(message, kwargs)
            return {"action": action}
        if key not in self._inflight:
            self._inflight[key] = asyncio.create_task(self._process_once(message, kwargs, key))
        task = self._inflight[key]
        try:
            return await task
        finally:
            if self._inflight.get(key) is task and task.done():
                self._inflight.pop(key, None)

    async def _process_once(self, message, kwargs, key):
        action, mark = await self._evaluate(message, kwargs)
        self._recent[key] = (self._now() + 300, action, mark)
        return {"action": action}

    @HookHandler("chat.receive.after_process", mode=HookMode.BLOCKING, order=HookOrder.EARLY)
    async def mark_followup(self, **kwargs):
        message = kwargs.get("message") or {}
        record = self._recent.get(self._message_key(message))
        if record and record[0] > self._now() and record[2]:
            return {"action": "continue", "modified_kwargs": {
                **kwargs, "message": {**message, "is_mentioned": True},
            }}
        return {"action": "continue"}

    @HookHandler("send_service.after_send", mode=HookMode.OBSERVE)
    async def renew_followup(self, **kwargs):
        if not kwargs.get("sent"):
            return
        message = kwargs.get("message") or {}
        session = str(message.get("session_id") or "")
        if session:
            async with self._lock:
                self._windows.renew_sent(session, str(message.get("account_id") or ""), self._now())


def create_plugin():
    return WatchDogPlugin()
