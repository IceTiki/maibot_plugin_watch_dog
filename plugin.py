import asyncio
import random
from maibot_sdk import MaiBotPlugin, PluginConfigBase, Field, HookHandler
from maibot_sdk.types import HookMode, HookOrder
import importlib.util
from pathlib import Path
import sys


def _load_engine():
    # MaiBot may load this entry by file path without adding its directory
    # to sys.path or creating a parent package. Resolve our sibling explicitly.
    module_name = __name__ + "_watch_dog_engine"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().with_name("engine.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load Watch Dog engine.py")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module  # Required by dataclasses during execution.
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


_engine = _load_engine()
compile_rules = _engine.compile_rules
first_match = _engine.first_match

class PluginSettings(PluginConfigBase):
    config_version: str = Field(default="0.1.1", description="配置版本号")
    enabled: bool = Field(default=True, description="启用消息过滤")

class WatchDogConfig(PluginConfigBase):
    group_context_only: list[dict] = Field(default_factory=list, description="按 QQ 群控制全体成员的 Planner 放行概率")
    context_only: list[dict] = Field(default_factory=list, description="按发送者 QQ 号控制 Planner 放行概率")
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    rules: list[dict] = Field(default_factory=list, description="按顺序匹配的过滤规则")

class WatchDogPlugin(MaiBotPlugin):
    config_model = WatchDogConfig

    async def on_load(self):
        self._tasks = set()
        config = self.get_plugin_config_data()
        self._rules = compile_rules(config)
        self._context_rules = _engine.compile_context_rules(config)
        self._group_rules = _engine.compile_context_rules(config, "group_context_only")

    async def on_unload(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def on_config_update(self, scope, config_data, version):
        if scope != "self":
            return
        try:
            rules = compile_rules(config_data)
            context_rules = _engine.compile_context_rules(config_data)
            group_rules = _engine.compile_context_rules(config_data, "group_context_only")
        except (ValueError, KeyError, TypeError) as exc:
            self.ctx.logger.error("Watch Dog 配置无效，保留原规则：%s", str(exc))
            return
        self._rules, self._context_rules, self._group_rules = rules, context_rules, group_rules

    async def _resolve_stream_id(self, message, kwargs):
        for source in (message, kwargs):
            for key in ("stream_id", "session_id", "chat_id"):
                if source.get(key):
                    return str(source[key])
        info = message.get("message_info") or message
        group = info.get("group_info") or {}
        user = info.get("user_info") or {}
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
            raise ValueError("Missing session identity in open_session result")
        return str(identity)

    async def _send_reply(self, text, message, kwargs):
        stream_id = await self._resolve_stream_id(message, kwargs)
        sent = await self.ctx.send.text(text, stream_id)
        if sent is False:
            self.ctx.logger.warning("Watch Dog 自动回复发送失败，消息保持屏蔽")

    async def _reply(self, text, message, kwargs):
        try:
            await asyncio.wait_for(self._send_reply(text, message, kwargs), timeout=5)
        except Exception as exc:
            self.ctx.logger.warning("Watch Dog 会话解析或自动回复失败（%s），消息保持屏蔽", type(exc).__name__)

    async def _direct_interaction(self, rule, message, kwargs):
        if rule.allow_poke and _engine.poke_kind(message) == "poke_bot":
            return True
        segments = message.get("raw_message") or []
        targets = []
        replies = []
        for segment in segments:
            kind, data = segment.get("type"), segment.get("data")
            if kind == "at" and rule.allow_at:
                targets.append(data.get("target_user_id") if isinstance(data, dict) else data)
            elif kind == "reply" and rule.allow_reply:
                replies.append(data if isinstance(data, dict) else {"target_message_id": data})
        if not targets and not replies:
            return False
        bot = str(message.get("account_id") or "")
        if not bot.isdigit():
            bot = str(await self.ctx.config.get("bot.qq_account", "") or "")
        if not bot:
            return False
        if any(str(target) == bot for target in targets):
            return True
        for reply in replies:
            author = reply.get("target_message_sender_id")
            if not author and reply.get("target_message_id"):
                stream = await self._resolve_stream_id(message, kwargs)
                original = await self.ctx.message.get_by_id(
                    str(reply["target_message_id"]), stream_id=stream
                ) or {}
                info = original.get("message_info") or original
                author = (info.get("user_info") or {}).get("user_id") or original.get("user_id")
            if str(author) == bot:
                return True
        return False

    async def _context_action(self, message, kwargs):
        info = message.get("message_info") or message
        user = info.get("user_info") or {}
        group = info.get("group_info") or {}
        rule = self._group_rules.get(str(group.get("group_id")))
        if rule is None:
            rule = self._context_rules.get(str(user.get("user_id")))
        if rule is None or message.get("platform") != "qq" or rule.probability == 1:
            return {"action": "continue"}
        try:
            direct = await asyncio.wait_for(self._direct_interaction(rule, message, kwargs), 4)
        except Exception as exc:
            self.ctx.logger.warning("Watch Dog 无法核对互动目标 (%s)，按普通消息抽样", type(exc).__name__)
            direct = False
        if direct or (rule.probability > 0 and random.random() < rule.probability):
            return {"action": "continue"}
        try:
            await asyncio.wait_for(self._append_context(message, kwargs, user), 8)
        except Exception as exc:
            self.ctx.logger.warning("Watch Dog 上下文追加失败，消息 %s：%s",
                                    message.get("message_id"), exc)
        return {"action": "abort"}

    async def _append_context(self, message, kwargs, user):
        stream = await self._resolve_stream_id(message, kwargs)
        result = await self.ctx.maisaka.context.append(
            stream, message["raw_message"], message_id=message["message_id"],
            user_info=user, replyable=True, source_kind="plugin:local.watch-dog",
        )
        if not isinstance(result, dict) or not result.get("success"):
            raise RuntimeError((result or {}).get("error", "上下文追加未确认"))

    @HookHandler("chat.receive.before_process", mode=HookMode.BLOCKING, order=HookOrder.EARLY)
    async def filter_message(self, **kwargs):
        message = kwargs.get("message") or {}
        rule = first_match(self._rules, message)
        if rule is None:
            return await self._context_action(message, kwargs)
        self.ctx.logger.info("Watch Dog 命中规则 %s，消息 %s", rule.name, message.get("message_id", "unknown"))
        if rule.reply:
            task = asyncio.create_task(self._reply(rule.reply, message, kwargs))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return {"action": "abort"}

def create_plugin():
    return WatchDogPlugin()
