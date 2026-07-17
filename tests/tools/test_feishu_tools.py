"""Tests for feishu_doc_tool and feishu_drive_tool — registration and schema validation."""

import importlib
import json
import re
import time
import unittest

from agent.prompt_builder import PLATFORM_HINTS
from tools.registry import registry

# Trigger tool discovery so feishu tools get registered
ft = importlib.import_module("tools.feishu_doc_tool")
importlib.import_module("tools.feishu_drive_tool")


class TestFeishuToolRegistration(unittest.TestCase):
    """Verify feishu tools are registered and have valid schemas."""

    EXPECTED_TOOLS = {
        "feishu_doc_read": "feishu_doc",
        "feishu_doc_prepare_meeting_minutes": "feishu_doc",
        "feishu_doc_apply_change": "feishu_doc",
        "feishu_doc_cancel_change": "feishu_doc",
        "feishu_drive_list_comments": "feishu_drive",
        "feishu_drive_list_comment_replies": "feishu_drive",
        "feishu_drive_reply_comment": "feishu_drive",
        "feishu_drive_add_comment": "feishu_drive",
    }

    def test_all_tools_registered(self):
        for tool_name, toolset in self.EXPECTED_TOOLS.items():
            entry = registry.get_entry(tool_name)
            self.assertIsNotNone(entry, f"{tool_name} not registered")
            self.assertEqual(entry.toolset, toolset)

    def test_schemas_have_required_fields(self):
        for tool_name in self.EXPECTED_TOOLS:
            entry = registry.get_entry(tool_name)
            schema = entry.schema
            self.assertIn("name", schema)
            self.assertEqual(schema["name"], tool_name)
            self.assertIn("description", schema)
            self.assertIn("parameters", schema)
            self.assertIn("type", schema["parameters"])
            self.assertEqual(schema["parameters"]["type"], "object")

    def test_handlers_are_callable(self):
        for tool_name in self.EXPECTED_TOOLS:
            entry = registry.get_entry(tool_name)
            self.assertTrue(callable(entry.handler))

    def test_doc_read_schema_params(self):
        entry = registry.get_entry("feishu_doc_read")
        props = entry.schema["parameters"].get("properties", {})
        self.assertIn("doc_token", props)

    def test_drive_tools_require_file_token(self):
        for tool_name in self.EXPECTED_TOOLS:
            if not tool_name.startswith("feishu_drive_"):
                continue
            entry = registry.get_entry(tool_name)
            props = entry.schema["parameters"].get("properties", {})
            self.assertIn("file_token", props, f"{tool_name} missing file_token param")
            self.assertIn("file_type", props, f"{tool_name} missing file_type param")


def _text_block(block_id, text, block_type=2, parent_id="root"):
    key = {
        3: "heading1",
        4: "heading2",
        12: "bullet",
        13: "ordered",
    }.get(block_type, "text")
    return {
        "block_id": block_id,
        "parent_id": parent_id,
        "block_type": block_type,
        key: {"elements": [{"text_run": {"content": text}}]},
    }


def _meeting_blocks(extra_text=""):
    return [
        {"block_id": "root", "block_type": 1, "children": ["b1", "b2", "b3", "b4", "b5", "b6"]},
        _text_block("b1", "项目例会", 3),
        _text_block("b2", "会议主题：项目同步"),
        _text_block("b3", "日期：2026-07-15"),
        _text_block("b4", "参与人：张三、李四"),
        _text_block("b5", "Decisions\n- 采用方案 A"),
        _text_block("b6", f"Action Items\n- 完成原型", 12),
        *([_text_block("b7", extra_text)] if extra_text else []),
    ]


def _rough_prep_blocks():
    return [
        {"block_id": "root", "block_type": 1, "children": ["b1", "b2", "b3", "b4"]},
        _text_block("b1", "0520 Prep"),
        _text_block("b2", "场地确认，物料清单，嘉宾动线"),
        _text_block("b3", "Open questions: 谁负责签到台？预算是否已确认？"),
        _text_block("b4", "Next: 和设计同步海报终稿", 12),
    ]


def _fragmented_action_blocks():
    return [
        {"block_id": "root", "block_type": 1, "children": ["b1", "b2", "b3"]},
        _text_block("b1", "Alice - deck; Bob - room setup; Chen - follow up vendor"),
        _text_block("b2", "No deadline yet"),
        _text_block("b3", "Need decide final guest list"),
    ]


def _unrelated_contract_blocks():
    return [
        {"block_id": "root", "block_type": 1, "children": ["b1", "b2"]},
        _text_block("b1", "本协议由甲方与乙方签署。"),
        _text_block("b2", "违约责任及法律适用条款如下。"),
    ]


def _install_fake_doc(monkeypatch, tmp_path, blocks=None, *, title="测试会议", code=0, write_error=None):
    blocks = list(blocks or _meeting_blocks())
    calls = []
    monkeypatch.setattr(ft, "get_hermes_home", lambda: tmp_path / "home")
    monkeypatch.setattr(ft, "_backup_dir", lambda: tmp_path / "backups")
    ft.set_client(object())

    def fake_do_request(client, method, uri, paths=None, queries=None, body=None):
        calls.append({"method": method, "uri": uri, "paths": paths or {}, "queries": queries or [], "body": body})
        if code != 0:
            return code, "forbidden", {}
        if uri == ft._DOCUMENT_META_URI:
            return 0, "", {"document": {"title": title, "revision": "rev1"}}
        if uri == ft._DOCUMENT_BLOCKS_URI:
            page_token = dict(queries or []).get("page_token", "")
            if page_token:
                return 0, "", {"items": blocks[2:], "has_more": False}
            if len(blocks) > 2:
                return 0, "", {"items": blocks[:2], "has_more": True, "page_token": "next"}
            return 0, "", {"items": blocks, "has_more": False}
        if uri == ft._DOCUMENT_CHILDREN_DELETE_URI:
            return 0, "", {"revision": "rev2"}
        if uri == ft._DOCUMENT_CHILDREN_URI:
            if write_error:
                return write_error, "write failed", {}
            return 0, "", {"revision": "rev3"}
        raise AssertionError(f"unexpected request {method} {uri}")

    monkeypatch.setattr(ft, "_do_request", fake_do_request)
    return calls


def _pending(monkeypatch, tmp_path, **overrides):
    monkeypatch.setattr(ft, "get_hermes_home", lambda: tmp_path / "home")
    record = {
        "change_id": "fdc_test",
        "document_id": "docabc",
        "title": "测试会议",
        "content_hash": ft._content_hash(ft.normalize_blocks_to_text(_meeting_blocks(), "docabc"), _meeting_blocks()),
        "meeting_minutes": "# 会议纪要\n\n## 会议信息\n- 会议主题: 项目同步",
        "user_id": "u1",
        "chat_id": "c1",
        "created_at": time.time(),
        "expires_at": time.time() + 1800,
    }
    record.update(overrides)
    ft._save_pending(record)
    return record


def test_parse_feishu_docx_url():
    assert ft.parse_docx_url("https://example.feishu.cn/docx/DOC123abc_-") == "DOC123abc_-"


def test_parse_feishu_docx_url_with_query_and_fragment():
    url = "https://example.feishu.cn/docx/DOC123abc_-?from=copylink#heading"
    assert ft.parse_docx_url(url) == "DOC123abc_-"


def test_invalid_url_rejected():
    with unittest.TestCase().assertRaises(ValueError):
        ft.parse_docx_url("https://example.feishu.cn/doc/DOC123")


def test_paginated_block_reading(monkeypatch, tmp_path):
    calls = _install_fake_doc(monkeypatch, tmp_path)
    blocks = ft._read_blocks(object(), "docabc")
    assert len(blocks) == len(_meeting_blocks())
    assert [c["uri"] for c in calls].count(ft._DOCUMENT_BLOCKS_URI) == 2


def test_block_to_text_normalization():
    blocks = [
        {"block_id": "root", "block_type": 1, "children": ["h", "b", "o"]},
        _text_block("h", "标题", 3),
        _text_block("b", "要点", 12),
        _text_block("o", "步骤", 13),
    ]
    text = ft.normalize_blocks_to_text(blocks, "docabc")
    assert "# 标题" in text
    assert "- 要点" in text
    assert "1. 步骤" in text


def test_headings_lists_and_simple_tables():
    table = {"block_id": "t", "block_type": 31, "rows": [[{"text": "A"}, {"text": "B"}], [{"text": "1"}, {"text": "2"}]]}
    text = ft.normalize_blocks_to_text([_text_block("h", "标题", 4), _text_block("b", "项", 12), table], "docabc")
    assert "## 标题" in text
    assert "- 项" in text
    assert "A | B" in text


def test_missing_owner():
    ok, minutes = ft.generate_meeting_minutes("会议主题：同步\n行动项\n- 完成原型 截止：周五", source_document="doc")
    assert ok
    assert "Owner: 待确认" in minutes


def test_missing_due_date():
    ok, minutes = ft.generate_meeting_minutes("会议主题：同步\n行动项\n- 完成原型 负责人：张三", source_document="doc")
    assert ok
    assert "Due Date: 待确认" in minutes


def test_preview_generation(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path)
    out = json.loads(ft._handle_prepare_meeting_minutes({
        "doc_url": "https://x.feishu.cn/docx/docabc",
        "user_id": "u1",
        "chat_id": "c1",
    }))
    assert out["success"] is True
    assert re.match(r"^fdc_[0-9a-f]{12}$", out["change_id"])
    assert "以下为预览，尚未修改原文档。" in out["preview"]
    assert f"确认修改 {out['change_id']}" in out["preview"]


def test_rough_meeting_preparation_notes_are_accepted(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, blocks=_rough_prep_blocks(), title="0520 Meeting")
    out = json.loads(ft._handle_prepare_meeting_minutes({
        "doc_url": "https://x.feishu.cn/docx/docabc",
        "user_id": "u1",
        "chat_id": "c1",
    }))
    assert out["success"] is True
    assert "Change ID:" in out["preview"]
    assert "## Missing Information" in out["preview"]


def test_fragmented_action_items_are_accepted(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, blocks=_fragmented_action_blocks())
    out = json.loads(ft._handle_prepare_meeting_minutes({
        "doc_url": "https://x.feishu.cn/docx/docabc",
        "user_id": "u1",
        "chat_id": "c1",
    }))
    assert out["success"] is True
    assert "以下为预览，尚未修改原文档。" in out["preview"]


def test_genuinely_unrelated_documents_are_rejected(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, blocks=_unrelated_contract_blocks())
    out = json.loads(ft._handle_prepare_meeting_minutes({
        "doc_url": "https://x.feishu.cn/docx/docabc",
        "user_id": "u1",
        "chat_id": "c1",
    }))
    assert out["success"] is False
    assert "不是会议材料" in out["message"]


def test_vague_confirmation_rejection(monkeypatch, tmp_path):
    _pending(monkeypatch, tmp_path)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "好的", "user_id": "u1", "chat_id": "c1"}))
    assert out["code"] == "vague_confirmation"


def test_direct_write_without_pending_change_gets_preview_required_message(monkeypatch, tmp_path):
    monkeypatch.setattr(ft, "get_hermes_home", lambda: tmp_path / "home")
    out = json.loads(ft._handle_apply_change({
        "change_id": "",
        "confirmation_text": "帮我直接改到 doc 里面",
        "user_id": "u1",
        "chat_id": "c1",
    }))
    assert out["error"] == "当前没有有效的修改预览，请先重新生成预览。"


def test_wrong_user_confirmation_rejection(monkeypatch, tmp_path):
    _pending(monkeypatch, tmp_path)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u2", "chat_id": "c1"}))
    assert "同一位飞书用户" in out["error"]


def test_wrong_chat_confirmation_rejection(monkeypatch, tmp_path):
    _pending(monkeypatch, tmp_path)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u1", "chat_id": "c2"}))
    assert "同一个聊天" in out["error"]


def test_expired_pending_change(monkeypatch, tmp_path):
    _pending(monkeypatch, tmp_path, expires_at=time.time() - 1)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u1", "chat_id": "c1"}))
    assert "已过期" in out["error"]


def test_source_document_changed_after_preview(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, blocks=_meeting_blocks("新内容"))
    _pending(monkeypatch, tmp_path, content_hash="old")
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u1", "chat_id": "c1"}))
    assert out["code"] == "source_changed"


def test_backup_creation(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path)
    record = _pending(monkeypatch, tmp_path)
    snapshot = ft._read_document_snapshot(object(), "docabc")
    path = ft._write_backup(record, snapshot)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["document_id"] == "docabc"
    assert data["requesting_user_id"] == "u1"


def test_explicit_confirmation_invokes_feishu_doc_apply_change(monkeypatch, tmp_path):
    calls = _install_fake_doc(monkeypatch, tmp_path)
    _pending(monkeypatch, tmp_path)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u1", "chat_id": "c1"}))
    assert out["success"] is True
    assert any(c["uri"] == ft._DOCUMENT_CHILDREN_DELETE_URI for c in calls)
    create = [c for c in calls if c["uri"] == ft._DOCUMENT_CHILDREN_URI][-1]
    assert "Updated by Bagel" in json.dumps(create["body"], ensure_ascii=False)


def test_no_legacy_feishu_drive_add_comment_fallback_for_docx_body_work():
    drive_entry = registry.get_entry("feishu_drive_add_comment")
    assert drive_entry is not None
    drive_description = drive_entry.schema["description"]
    assert "Do not use this to read, preview, replace, update, or modify the body" in drive_description
    hint = PLATFORM_HINTS["feishu"]
    assert "use only feishu_doc_* tools" in hint
    assert "Do not use feishu_drive_add_comment" in hint


def test_tools_can_obtain_client_through_live_gateway_dependency_path(monkeypatch):
    from plugins.platforms.feishu.adapter import FeishuAdapter

    ft.set_client(None)
    live_client = object()
    adapter = object.__new__(FeishuAdapter)
    adapter._client = live_client
    adapter._inject_feishu_doc_client()
    assert ft.get_client() is live_client


def test_permission_failure(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, code=403)
    out = json.loads(ft._handle_prepare_meeting_minutes({"doc_url": "https://x.feishu.cn/docx/docabc", "user_id": "u1", "chat_id": "c1"}))
    assert out["code"] == "permission_denied"


def test_partial_write_failure(monkeypatch, tmp_path):
    _install_fake_doc(monkeypatch, tmp_path, write_error=500)
    _pending(monkeypatch, tmp_path)
    out = json.loads(ft._handle_apply_change({"change_id": "fdc_test", "confirmation_text": "确认修改 fdc_test", "user_id": "u1", "chat_id": "c1"}))
    assert "Create approved meeting-minutes blocks failed" in out["error"]
    assert list((tmp_path / "backups").glob("*.json"))


def test_no_secret_leakage_in_logs(monkeypatch, tmp_path, caplog):
    secret = "app_secret_should_not_appear"
    _install_fake_doc(monkeypatch, tmp_path, blocks=_meeting_blocks(secret))
    with caplog.at_level("INFO"):
        ft._handle_prepare_meeting_minutes({"doc_url": "https://x.feishu.cn/docx/docabc", "user_id": "u1", "chat_id": "c1"})
    assert secret not in caplog.text


if __name__ == "__main__":
    unittest.main()
