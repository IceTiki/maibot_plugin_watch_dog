import asyncio
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

class WatchDogConfig(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用消息过滤")
    rules: list[dict] = Field(default_factory=list, description="按顺序匹配的过滤规则")

class WatchDogPlugin(MaiBotPlugin):
    config_model = WatchDogConfig

    async def on_load(self):
        self._tasks = set()
        self._rules = compile_rules(self.get_plugin_config_data())

    async def on_unload(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def on_config_update(self, scope, config_data, version):
        if scope != "self":
            return
        try:
            rules = compile_rules(config_data)
        except (ValueError, KeyError, TypeError) as exc:
            self.ctx.logger.error("Watch Dog 配置无效，保留原规则：%s", type(exc).__name__)
            return
        self._rules = rules

    async def _reply(self, text, stream_id):
        try:
            await asyncio.wait_for(self.ctx.send.text(text, stream_id), timeout=5)
        except Exception:
            self.ctx.logger.warning("Watch Dog 自动回复失败，消息保持屏蔽")

    @HookHandler("chat.receive.before_process", mode=HookMode.BLOCKING, order=HookOrder.EARLY)
    async def filter_message(self, **kwargs):
        message = kwargs.get("message") or {}
        rule = first_match(self._rules, message)
        if rule is None:
            return {"action": "continue"}
        self.ctx.logger.info("Watch Dog 命中规则 %s，消息 %s", rule.name, message.get("message_id", "unknown"))
        stream_id = message.get("stream_id") or message.get("chat_id") or kwargs.get("stream_id")
        if rule.reply and stream_id:
            task = asyncio.create_task(self._reply(rule.reply, stream_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif rule.reply:
            self.ctx.logger.warning("Watch Dog 缺少会话 ID，已屏蔽但无法自动回复")
        return {"action": "abort"}

def create_plugin():
    return WatchDogPlugin()
