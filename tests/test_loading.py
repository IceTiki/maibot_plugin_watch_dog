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
    async def verify_context():
        from unittest.mock import patch
        plugin = module.create_plugin()
        append = AsyncMock(return_value={"success": True})
        lookup = AsyncMock(return_value={"message_info": {"user_info": {"user_id": "9"}}})
        plugin.ctx = types.SimpleNamespace(
            maisaka=types.SimpleNamespace(context=types.SimpleNamespace(append=append)),
            message=types.SimpleNamespace(get_by_id=lookup), logger=Mock())
        row = {"user_ids": ["34"], "planner_pass_probability": 0.05}
        plugin._group_rules = {}
        plugin._rules = ()
        plugin._context_rules = module._engine.compile_context_rules({"context_only": [row, dict(row, planner_pass_probability=1)]})
        user = {"user_id": "34", "user_nickname": "AI"}
        msg = {"platform": "qq", "account_id": "9", "message_id": "123", "session_id": "s",
               "message_info": {"group_info": {"group_id": "12"}, "user_info": user}}
        async def check(segments, sample, expected, appended):
            append.reset_mock()
            msg["raw_message"] = segments
            with patch.object(module.random, "random", return_value=sample) as draw:
                result = await plugin.filter_message(message=msg)
                assert draw.call_count <= 1
            assert result["action"] == expected, result
            assert append.await_count == int(appended)
            if appended:
                assert append.await_args.args == ("s", segments)
                assert append.await_args.kwargs["user_info"] == user
                assert append.await_args.kwargs["message_id"] == "123"
                assert append.await_args.kwargs["replyable"] is True
        ordinary = [{"type": "text", "data": "hello"}]
        at = [{"type": "at", "data": {"target_user_id": "9"}}]
        quote = [{"type": "reply", "data": {"target_message_sender_id": "9"}}]
        await check(ordinary, 0.049, "continue", False)
        await check(ordinary, 0.05, "abort", True)
        await check(at, 0.8, "continue", False)
        await check(quote, 0.8, "continue", False)
        await check([{"type":"reply","data":{"target_message_id":"42"}}], 0.8, "continue", False)
        lookup.assert_awaited_once_with("42", stream_id="s")
        lookup.return_value = None
        await check([{"type":"reply","data":{"target_message_id":"42"}}], 0.8, "abort", True)
        msg["message_info"]["group_info"]["group_id"] = "99"
        await check(ordinary, 0.8, "abort", True)
        plugin._context_rules = module._engine.compile_context_rules({"context_only": [dict(row, group_id="12")]})
        await check(ordinary, 0.8, "abort", True)
        msg["message_info"]["group_info"] = {}
        await check(ordinary, 0.8, "abort", True)
        user["user_id"] = "35"
        await check(ordinary, 0.8, "continue", False)
        user["user_id"] = "34"
        msg["message_info"]["group_info"]["group_id"] = "12"
        async def configure(**overrides):
            await plugin.on_config_update("self", {"context_only":[dict(row, **overrides)]}, "")
        await configure(planner_pass_probability=1)
        await check(ordinary, 0.99, "continue", False)
        await configure(planner_pass_probability=0, allow_planner_on_at_bot=False)
        await check(at, 0, "abort", True)
        await check(at+quote, 0.99, "continue", False)
        await configure(planner_pass_probability=0, allow_planner_on_reply_to_bot=False)
        await check(quote, 0, "abort", True)
        await check(at+quote, 0.99, "continue", False)
        old = plugin._context_rules
        for invalid in [-1, 1.01, float("nan"), True, "0.1"]:
            await configure(planner_pass_probability=invalid)
            assert plugin._context_rules is old
        cfg = {"group_context_only":[{"group_id":"12","planner_pass_probability":0,
                 "allow_planner_on_at_bot":True,"allow_planner_on_reply_to_bot":False}],
               "context_only":[dict(row, planner_pass_probability=1)]}
        await plugin.on_config_update("self",cfg,"")
        for seg in (ordinary, quote, [{"type":"at","data":{"target_user_id":"8"}}],
                    [{"type":"text","data":"@麦麦"}], [{"type":"forward","data":[{"content":at}]}]):
            await check(seg,0,"abort",True)
        await check(at,0,"continue",False)
        await check(quote+at,0,"continue",False)
        plugin._rules = module.compile_rules({"rules":[{"name":"blocked","conditions":[{"kind":"sender_qq","ids":["34"]}]}]})
        await check(at,0,"abort",False)
        plugin._rules = ()
        user["user_id"] = "35"
        await check(ordinary,0,"abort",True)
        for group in ({}, {"group_id":"99"}):
            msg["message_info"]["group_info"] = group
            await check(ordinary,0,"continue",False)
        msg["message_info"]["group_info"] = {"group_id":"12"}
        cfg["rules"] = [{"name":"block peers","conditions":[{"kind":"poke_except_bot"}]}]
        cfg["group_context_only"][0]["allow_planner_on_poke_bot"] = True
        await plugin.on_config_update("self",cfg,"")
        payload = {"sub_type":"poke","self_id":9,"target_id":"9"}
        msg["message_info"]["additional_config"] = {"napcat_notice_payload":payload}
        await check(ordinary,0,"continue",False)
        payload["target_id"] = 8
        await check(ordinary,0,"abort",False)
        del payload["target_id"]
        await check(ordinary,0,"abort",False)
        payload["target_id"] = 9
        cfg["rules"].insert(0, {"name":"blocked","conditions":[{"kind":"sender_qq","ids":["35"]}]})
        await plugin.on_config_update("self",cfg,"")
        await check(ordinary,0,"abort",False)
        del msg["message_info"]["additional_config"]
        plugin._rules = ()
        append.side_effect = RuntimeError("failure")
        await check(ordinary, 0, "abort", True)
        plugin._rules = module.compile_rules({"rules":[{"name":"block","conditions":[{"kind":"text_regex","pattern":"hello"}]}]})
        await check(ordinary, 0, "abort", False)
    asyncio.run(verify_context())
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
