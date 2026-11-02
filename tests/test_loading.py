"""Reproduce the host loading an entrypoint from outside the plugin directory."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LoadingTests(unittest.TestCase):
    def test_file_entrypoint_without_plugin_on_search_path(self):
        plugin = Path(__file__).resolve().parents[1] / "plugin.py"
        script = r"""
import importlib.util
import sys
import types
sdk = types.ModuleType("maibot_sdk")
sdk.MaiBotPlugin = type("MaiBotPlugin", (), {})
sdk.PluginConfigBase = type("PluginConfigBase", (), {})
sdk.Field = lambda **kwargs: None
sdk.HookHandler = lambda *args, **kwargs: lambda func: func
sdk_types = types.ModuleType("maibot_sdk.types")
sdk_types.HookMode = types.SimpleNamespace(BLOCKING="blocking")
sdk_types.HookOrder = types.SimpleNamespace(EARLY="early")
sys.modules["maibot_sdk"] = sdk
sys.modules["maibot_sdk.types"] = sdk_types
# An unrelated global engine must never satisfy the sibling import.
sys.modules["engine"] = types.ModuleType("engine")
for name in ("host_entry", "host.plugins.watch_dog"):
    spec = importlib.util.spec_from_file_location(name, sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.create_plugin().__class__ is module.WatchDogPlugin
    import asyncio
    from unittest.mock import AsyncMock, Mock
    async def verify_reply():
        plugin = module.create_plugin()
        plugin.ctx = types.SimpleNamespace(chat=types.SimpleNamespace(open_session=AsyncMock(return_value={"stream_id":"resolved"})), send=types.SimpleNamespace(text=AsyncMock(return_value=True)), logger=Mock())
        plugin._tasks = set()
        plugin._rules = module.compile_rules({"rules":[{"name":"sl","reply":"NOT SNOWLUME! ","conditions":[{"kind":"text_regex","pattern":"^#sl$"}]}]})
        message = {"platform":"qq","group_info":{"group_id":"123"},"additional_config":{"platform_io_account_id":"bot","scope":"gateway"},"raw_message":[{"type":"text","data":"#sl"}]}
        assert await plugin.filter_message(message=message) == {"action":"abort"}
        await asyncio.gather(*plugin._tasks)
        plugin.ctx.chat.open_session.assert_awaited_once_with(platform="qq",chat_type="group",group_id="123",account_id="bot",scope="gateway")
        plugin.ctx.send.text.assert_awaited_once_with("NOT SNOWLUME! ","resolved")
        plugin.ctx.chat.open_session.reset_mock()
        await plugin._reply("x", {"session_id":"known"}, {})
        plugin.ctx.chat.open_session.assert_not_awaited()
        await plugin._reply("x", {"message_info":{"platform":"qq","user_info":{"user_id":"456"}}}, {})
        plugin.ctx.chat.open_session.assert_awaited_once_with(platform="qq",chat_type="private",user_id="456")
        plugin.ctx.chat.open_session.side_effect = RuntimeError("failed")
        assert await plugin.filter_message(message=message) == {"action":"abort"}
        await asyncio.gather(*plugin._tasks)
        plugin.ctx.logger.warning.assert_called_once()
    asyncio.run(verify_reply())
    rules = module.compile_rules({"rules": [{"name": "test", "conditions": [{"kind": "image"}]}]})
    assert module.first_match(rules, {"raw_message": [{"type": "image", "data": "x"}]}).name == "test"
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", script, str(plugin)],
                cwd=directory, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
