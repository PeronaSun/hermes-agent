from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "excel_warrior" / "__init__.py"


def _module():
    spec = importlib.util.spec_from_file_location("bagel_excel_warrior_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs


def test_registers_all_tools_without_starting_services():
    module = _module()
    ctx = FakeContext()
    module.register(ctx)
    assert set(ctx.tools) == {
        "excel_warrior_project",
        "excel_warrior_minutes",
        "excel_warrior_doc_reader",
        "excel_warrior_legacy_html",
    }
    assert all(tool["check_fn"]() for tool in ctx.tools.values())


def test_project_validate_uses_isolated_portal_data():
    module = _module()
    result = json.loads(module._project({"action": "validate"}))
    assert result["ok"] is True


def test_minutes_list_is_read_only_and_available():
    module = _module()
    result = json.loads(module._minutes({"action": "list_minutes"}))
    assert result["ok"] is True
    assert isinstance(result["minutes"], list)


def test_doc_reader_rejects_unsupported_url_without_credentials():
    module = _module()
    result = json.loads(module._doc_reader({"url": "https://example.com/not-a-doc"}))
    assert result["ok"] is False
