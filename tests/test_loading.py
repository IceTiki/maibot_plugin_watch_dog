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
    rules = module.compile_rules({"rules": [{"name": "test", "conditions": [{"kind": "image"}]}]})
    assert module.first_match(rules, {"raw_message": [{"type": "image", "data": "x"}]}).name == "test"
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", script, str(plugin)],
                cwd=directory, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
