"""Use the installed SDK when available; local tests stub only its host shell."""
from pathlib import Path
import importlib.util
import sys
import types
from pydantic import BaseModel, Field

try:
    import maibot_sdk
    REAL_SDK = True
except ImportError:
    REAL_SDK = False
    sdk = types.ModuleType("maibot_sdk")
    class MaiBotPlugin:
        @property
        def ctx(self):
            return self._ctx
        def set_plugin_config(self, data):
            self._plugin_config_data = data
        def get_plugin_config_data(self):
            return self._plugin_config_data
    sdk.MaiBotPlugin = MaiBotPlugin
    sdk.PluginConfigBase = BaseModel
    sdk.Field = Field
    sdk.HookHandler = lambda *args, **kwargs: lambda function: function
    sdk_types = types.ModuleType("maibot_sdk.types")
    sdk_types.HookMode = types.SimpleNamespace(BLOCKING="blocking", OBSERVE="observe")
    sdk_types.HookOrder = types.SimpleNamespace(EARLY="early")
    sys.modules["maibot_sdk"] = sdk
    sys.modules["maibot_sdk.types"] = sdk_types

spec = importlib.util.spec_from_file_location("watch_dog_test_entry", Path(__file__).resolve().parents[1] / "plugin.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
engine = module.engine
Config = module.settings.WatchDogConfig


def rule(kind, conditions=None, **kwargs):
    return {
        "name": kind,
        "condition": conditions or [],
        "action": {"kind": kind},
        **kwargs,
    }


def route(use, group=None, user=None):
    return {"group": group or ["*"], "user": user or ["*"], "use": use}


def config(rows):
    return {
        "plugin": {"config_version": "0.5.0", "enabled": True},
        "routes": [route(["main"])],
        "rule_groups": {"main": rows},
    }


def standard():
    return config([
        rule("follow_up", [{"kind": "interaction", "types": ["nickname", "at_bot", "reply_to_bot"]}]),
        rule("context_only"),
    ])
