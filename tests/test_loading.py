from copy import deepcopy
from support import config, module, rule, standard
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import asyncio
import unittest


class HookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = module.create_plugin()
        self.append = AsyncMock(return_value={"success": True})
        self.send = AsyncMock(return_value=True)
        self.lookup = AsyncMock(return_value={"message_info": {"user_info": {"user_id": "9"}}})
        async def get(key, default=None):
            return {"bot.nickname": "麦麦", "bot.alias_names": ["小麦"], "bot.qq_account": "9"}.get(key, default)
        self.plugin._ctx = SimpleNamespace(
            logger=Mock(), config=SimpleNamespace(get=AsyncMock(side_effect=get)),
            chat=SimpleNamespace(open_session=AsyncMock(return_value={"session_id": "s"})),
            message=SimpleNamespace(get_by_id=self.lookup), send=SimpleNamespace(text=self.send),
            maisaka=SimpleNamespace(context=SimpleNamespace(append=self.append)))
        self.plugin.set_plugin_config(standard())
        await self.plugin.on_load()
        self.counter = 0

    async def asyncTearDown(self):
        await self.plugin.on_unload()

    def message(self, segments=None, group="12", user="34", **extra):
        self.counter += 1
        info = {"user_info": {"user_id": user, "user_nickname": "test"}}
        if group is not None:
            info["group_info"] = {"group_id": group}
        return {"platform": "qq", "account_id": "9", "message_id": str(self.counter),
                "session_id": "s" + str(group), "message_info": info,
                "raw_message": segments or [{"type": "text", "data": "hello"}], **extra}

    async def check(self, msg, expected, appended=False, marked=False):
        before = self.append.await_count
        result = await self.plugin.filter_message(message=msg)
        self.assertEqual(result["action"], expected)
        self.assertEqual(self.append.await_count - before, int(appended))
        after = await self.plugin.mark_followup(message=msg)
        self.assertEqual(bool(after.get("modified_kwargs", {}).get("message", {}).get("is_mentioned")), marked)

    async def configure(self, data):
        await self.plugin.on_config_update("self", data, "0.5.0")

    async def test_group_private_nickname_and_at_windows(self):
        for group in ["12", "20001", None]:
            await self.check(self.message(group=group), "abort", appended=True)
            await self.check(self.message([{"type": "at", "data": {"target_user_id": "9"}}], group=group),
                             "continue", marked=True)
            await self.check(self.message(group=group), "continue", marked=True)
            await self.check(self.message(group=group, user="35"), "abort", appended=True)
        await self.check(self.message([{"type": "text", "data": "小麦你好"}], group="new"), "continue", marked=True)

    async def test_first_complete_match_no_action_priority(self):
        msg = self.message([{"type": "at", "data": {"target_user_id": "9"}}])
        await self.configure(
            config([rule("context_only"), *standard()["rule_groups"]["main"]])
        )
        await self.check(msg, "abort", appended=True)
        await self.configure(config([rule("native"), rule("block")]))
        await self.check(self.message(), "continue")
        await self.configure(
            config([rule("block"), *standard()["rule_groups"]["main"]])
        )
        await self.check(self.message([{"type": "at", "data": {"target_user_id": "9"}}]), "abort")
        self.assertEqual(self.plugin._windows.windows, {})

    async def test_first_route_exclusive_and_group_order(self):
        data = config([])
        data["routes"] = [
            {"group": ["12"], "user": ["*"], "use": ["empty", "checks", "window"]},
            {"group": ["*"], "user": ["*"], "use": ["fallback"]},
        ]
        data["rule_groups"] = {
            "empty": [],
            "checks": [rule("block", [{"kind": "text_regex", "pattern": "^stop$"}])],
            "window": [
                rule("follow_up", [{"kind": "interaction", "types": ["at_bot"]}])
            ],
            "fallback": [rule("context_only")],
        }
        await self.configure(data)
        await self.check(
            self.message(), "continue"
        )  # A route miss must not try fallback.
        await self.check(self.message(group="13"), "abort", appended=True)
        await self.check(
            self.message([{"type": "at", "data": "9"}]), "continue", marked=True
        )
        await self.check(self.message([{"type": "text", "data": "stop"}]), "abort")
        await self.check(self.message(), "continue", marked=True)

    async def test_quote_detection_and_failure(self):
        await self.check(self.message([{"type": "reply", "data": {"target_message_sender_id": "9"}}]),
                         "continue", marked=True)
        await self.configure(standard())
        await self.check(self.message([{"type": "reply", "data": {"target_message_id": "42"}}]),
                         "continue", marked=True)
        self.lookup.assert_awaited_once()
        await self.configure(standard())
        self.lookup.side_effect = TimeoutError()
        await self.check(self.message([{"type": "reply", "data": {"target_message_id": "43"}}]), "abort", appended=True)
        await self.check(self.message([{"type": "at", "data": {"target_user_id": "8"}}]), "abort", appended=True)

    async def test_no_nested_or_notice_interaction(self):
        for segments in [
            [{"type": "forward", "data": [{"type": "at", "data": {"target_user_id": "9"}}]}],
            [{"type": "reply", "data": {"target_message_sender_id": "8", "text": "麦麦"}}],
            [{"type": "image", "data": {"description": "麦麦"}}],
        ]:
            await self.check(self.message(segments), "abort", appended=True)
        await self.check(self.message([{"type": "text", "data": "麦麦"}]), "continue", marked=True)
        msg = self.message(is_notify=True)
        msg["message_info"]["additional_config"] = {"napcat_notice_payload":
            {"sub_type": "poke", "target_id": 9, "self_id": 9}}
        await self.check(msg, "abort", appended=True)
        await self.configure(
            config(
                [
                    rule("block", [{"kind": "poke_except_bot"}]),
                    *standard()["rule_groups"]["main"],
                ]
            )
        )
        msg["message_info"]["additional_config"]["napcat_notice_payload"]["target_id"] = 8
        await self.check(msg, "abort")

    async def test_failed_context_and_duplicate_message(self):
        msg = self.message()
        self.append.side_effect = RuntimeError("failed")
        await self.check(msg, "abort", appended=True)
        await self.check(msg, "abort")
        self.append.side_effect = None
        msg = self.message()
        await asyncio.gather(self.plugin.filter_message(message=msg), self.plugin.filter_message(message=msg))
        self.assertEqual(self.append.await_count, 2)

    async def test_reply_once_even_when_send_fails(self):
        await self.configure(config([rule("block", action={"kind": "block", "reply": "fixed"})]))
        self.send.side_effect = RuntimeError("failed")
        msg = self.message()
        await self.check(msg, "abort")
        await self.check(msg, "abort")
        await asyncio.gather(*self.plugin._tasks)
        self.send.assert_awaited_once()

    async def test_disabled_and_invalid_update(self):
        await self.check(self.message([{"type": "text", "data": "麦麦"}]), "continue", marked=True)
        rules = self.plugin._routes
        invalid = standard()
        invalid["rule_groups"]["main"][0]["condition"][0]["types"] = ["unknown"]
        await self.configure(invalid)
        self.assertIs(self.plugin._routes, rules)
        self.assertTrue(self.plugin._windows.windows)
        valid = standard()
        valid["plugin"]["enabled"] = False
        await self.configure(valid)
        await self.check(self.message(), "continue")
        self.assertFalse(self.plugin._windows.windows)

    async def test_static_condition_still_required_in_window(self):
        data = standard()
        data["rule_groups"]["main"][0]["condition"].append({"kind": "image"})
        await self.configure(data)
        await self.check(self.message([{"type": "text", "data": "麦麦"}, {"type": "image"}]),
                         "continue", marked=True)
        await self.check(self.message(), "abort", appended=True)

    async def test_native_interaction_does_not_open_window(self):
        await self.configure(config([
            rule("native", [{"kind": "interaction", "types": ["at_bot"]}]),
            rule("context_only")]))
        await self.check(self.message([{"type": "at", "data": {"target_user_id": "9"}}]), "continue")
        self.assertFalse(self.plugin._windows.windows)

    async def test_send_renewal_and_cap(self):
        with patch.object(self.plugin, "_now", return_value=10):
            await self.check(self.message([{"type": "text", "data": "麦麦"}]), "continue", marked=True)
        window = next(iter(self.plugin._windows.windows.values()))
        with patch.object(self.plugin, "_now", return_value=40):
            await self.check(self.message(), "continue", marked=True)
        self.assertEqual(window.deadline, 70)
        with patch.object(self.plugin, "_now", return_value=50):
            await self.plugin.renew_followup(sent=False, message={"session_id": "s12", "account_id": "9"})
            self.assertEqual(window.deadline, 70)
            await self.plugin.renew_followup(sent=True, message={"session_id": "s12", "account_id": "9"})
        self.assertEqual(window.deadline, 110)
        with patch.object(self.plugin, "_now", return_value=110):
            await self.check(self.message(), "abort", appended=True)

    async def test_distinct_messages_do_not_block_each_other(self):
        arrived = 0
        ready = asyncio.Event()
        async def append(*args, **kwargs):
            nonlocal arrived
            arrived += 1
            if arrived == 2:
                ready.set()
            await ready.wait()
            return {"success": True}
        self.append.side_effect = append
        first, second = self.message(), self.message(user="35")
        results = await asyncio.wait_for(asyncio.gather(
            self.plugin.filter_message(message=first),
            self.plugin.filter_message(message=second)), 2)
        self.assertEqual(results, [{"action": "abort"}, {"action": "abort"}])

    async def test_config_change_during_lookup_does_not_reopen_old_window(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def lookup(*args, **kwargs):
            started.set()
            await release.wait()
            return {"message_info": {"user_info": {"user_id": "9"}}}
        self.lookup.side_effect = lookup
        message = self.message([{"type": "reply", "data": {"target_message_id": "42"}}])
        pending = asyncio.create_task(self.plugin.filter_message(message=message))
        await asyncio.wait_for(started.wait(), 1)
        await self.configure(config([rule("context_only")]))
        release.set()
        self.assertEqual(await pending, {"action": "abort"})
        self.assertFalse(self.plugin._windows.windows)
