"""Tests for Feishu Base/Bitable read-only tools."""

import importlib
import json
import time
from pathlib import Path

from agent.prompt_builder import PLATFORM_HINTS
from tools.registry import registry

bt = importlib.import_module("tools.feishu_bitable_tool")


BASE_URL = "https://kcn17bitaxss.feishu.cn/base/P6dGbhdMRaaD7csRCw1cR5iUnLc?table=tblsJ9hnV1u9ulX1&view=vewXxBNTOK"


def _fake_fields():
    return [
        {"field_id": "fld_task", "field_name": "Task", "type": 1, "property": {}, "is_primary": True},
        {"field_id": "fld_maison", "field_name": "Maison", "type": 1, "property": {}},
        {"field_id": "fld_group", "field_name": "分组", "type": 1, "property": {}},
        {"field_id": "fld_desc", "field_name": "任务描述", "type": 1, "property": {}},
        {"field_id": "fld_risk", "field_name": "Risk", "type": 1, "property": {}},
        {"field_id": "fld_owner", "field_name": "Owner", "type": 11, "property": {"multiple": True}},
        {"field_id": "fld_due", "field_name": "Due Date", "type": 5, "property": {"date_formatter": "yyyy/MM/dd"}},
        {"field_id": "fld_done", "field_name": "Done", "type": 7, "property": {}},
        {"field_id": "fld_status", "field_name": "Status", "type": 3, "property": {"options": [{"name": "Open"}]}},
        {"field_id": "fld_files", "field_name": "Files", "type": 17, "property": {}},
        {"field_id": "fld_formula", "field_name": "Formula", "type": 20, "property": {"formula": "1+1"}},
    ]


def _fake_records():
    return [
        {
            "record_id": "rec1",
            "fields": {
                "Task": [{"text": "Launch checklist", "type": "text"}],
                "Maison": "Celine",
                "分组": "Contexual Outreach",
                "任务描述": "Contextual Outreach",
                "Risk": None,
                "Owner": [{"id": "ou_1", "name": "Alice"}],
                "Due Date": 1780000000000,
                "Done": False,
                "Status": "Open",
                "Files": [{"file_token": "boxcnxx", "name": "brief.pdf"}],
                "Formula": 2,
            },
        },
        {
            "record_id": "rec2",
            "fields": {
                "Task": "Empty owner row",
                "Maison": "Celine",
                "分组": "Other",
                "任务描述": "Contextual Outreach",
                "Risk": "old",
                "Owner": None,
            },
        },
        {"record_id": "rec3", "fields": {"Task": "Missing optional cells", "Maison": "Celine"}},
    ]


def _install_fake_bitable(monkeypatch, *, permission_code=0, empty_records=False, update_permission_code=0, records=None):
    calls = []
    stored_records = records or _fake_records()
    bt.set_client(object())

    def fake_do_request(client, method, uri, paths=None, queries=None, body=None):
        calls.append({"method": method, "uri": uri, "paths": paths or {}, "queries": queries or [], "body": body})
        if permission_code:
            return permission_code, "forbidden", {}
        if uri == bt._RECORD_URI:
            if update_permission_code:
                return update_permission_code, "missing scope base:record:update", {}
            record_id = paths["record_id"]
            for record in stored_records:
                if record["record_id"] == record_id:
                    record.setdefault("fields", {}).update((body or {}).get("fields", {}))
                    return 0, "", {"record": record}
            return 1254040, "not found", {}
        if uri == bt._APP_URI:
            return 0, "", {"app": {"name": "Roadmap Base", "app_token": paths["app_token"]}}
        if uri == bt._TABLES_URI:
            return 0, "", {"items": [
                {"table_id": "tbl_other", "name": "Other", "revision": 1},
                {"table_id": "tblsJ9hnV1u9ulX1", "name": "Project Tracking", "revision": 2},
            ]}
        if uri == bt._VIEWS_URI:
            return 0, "", {"items": [
                {"view_id": "vew_all", "view_name": "All Tasks", "view_type": "grid"},
                {"view_id": "vewXxBNTOK", "view_name": "Project Tracking", "view_type": "grid"},
            ]}
        if uri == bt._FIELDS_URI:
            return 0, "", {"items": _fake_fields()}
        if uri == bt._RECORDS_URI:
            if empty_records:
                return 0, "", {"items": []}
            page_token = dict(queries or []).get("page_token", "")
            if page_token:
                return 0, "", {"items": stored_records[2:], "has_more": False}
            return 0, "", {"items": stored_records[:2], "has_more": True, "page_token": "next"}
        raise AssertionError(f"unexpected request {method} {uri}")

    monkeypatch.setattr(bt, "_do_request", fake_do_request)
    return calls, stored_records


def _pending_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(bt, "_pending_dir", lambda: Path(tmp_path))


def test_parse_base_url():
    parsed = bt.parse_base_url(BASE_URL)
    assert parsed == {
        "app_token": "P6dGbhdMRaaD7csRCw1cR5iUnLc",
        "table_id": "tblsJ9hnV1u9ulX1",
        "view_id": "vewXxBNTOK",
    }


def test_tools_registered():
    for name in (
        "feishu_bitable_get_app",
        "feishu_bitable_list_tables",
        "feishu_bitable_list_views",
        "feishu_bitable_list_fields",
        "feishu_bitable_list_records",
        "feishu_bitable_read_table",
    ):
        entry = registry.get_entry(name)
        assert entry is not None
        assert entry.toolset == "feishu_bitable"


def test_matching_project_tracking_by_table_name(monkeypatch):
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL, target_table_name="project tracking", maximum_rows=1)
    assert result["table_id"] == "tblsJ9hnV1u9ulX1"
    assert result["table_name"] == "Project Tracking"


def test_matching_view_by_name(monkeypatch):
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL, target_view_name="project tracking", maximum_rows=1)
    assert result["view_id"] == "vewXxBNTOK"
    assert result["view_name"] == "Project Tracking"


def test_listing_fields(monkeypatch):
    _install_fake_bitable(monkeypatch)
    fields = bt.bitable_list_fields("app", "tbl")
    assert fields[0]["field_id"] == "fld_task"
    assert fields[0]["field_name"] == "Task"
    assert fields[0]["is_primary"] is True
    assert fields[-1]["is_computed"] is True
    assert fields[-1]["is_writable"] is False


def test_reading_all_records_with_pagination(monkeypatch):
    calls, _ = _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL)
    assert result["total_record_count"] == 3
    assert [c["uri"] for c in calls].count(bt._RECORDS_URI) == 2


def test_preserving_empty_cells(monkeypatch):
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL)
    rec2 = result["records"][1]
    rec3 = result["records"][2]
    assert rec2["fields_by_name"]["Owner"] is None
    assert rec2["field_states"]["Owner"] == "null"
    assert rec3["fields_by_name"]["Owner"] is None
    assert rec3["field_states"]["Owner"] == "empty"


def test_complex_field_values_preserved(monkeypatch):
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL)
    rec1 = result["records"][0]
    assert isinstance(rec1["fields_by_name"]["Task"], list)
    assert rec1["fields_by_name"]["Owner"][0]["id"] == "ou_1"
    assert rec1["fields_by_name"]["Files"][0]["file_token"] == "boxcnxx"
    assert rec1["fields_by_id"]["fld_status"] == "Open"


def test_permission_denied(monkeypatch):
    _install_fake_bitable(monkeypatch, permission_code=403)
    out = json.loads(bt._handler(bt._read_table_handler)({"base_url": BASE_URL}))
    assert out["code"] == "permission_denied"
    assert "Base 未共享" in out["error"]


def test_advanced_permission_empty_result_warning(monkeypatch):
    _install_fake_bitable(monkeypatch, empty_records=True)
    result = bt.bitable_read_table(BASE_URL)
    assert result["total_record_count"] == 0
    assert result["inaccessible_or_unsupported_fields"]
    assert "高级权限" in result["inaccessible_or_unsupported_fields"][0]["message"]


def test_live_gateway_client_injection(monkeypatch):
    from plugins.platforms.feishu.adapter import FeishuAdapter

    bt.set_client(None)
    live_client = object()
    adapter = object.__new__(FeishuAdapter)
    adapter._client = live_client
    adapter._inject_feishu_doc_client()
    assert bt.get_client() is live_client


def test_no_fallback_to_generic_file_reading():
    hint = PLATFORM_HINTS["feishu"]
    assert "feishu_bitable_read_table" in hint
    assert "Do not use read_file or browser scraping" in hint
    entry = registry.get_entry("feishu_bitable_read_table")
    assert "Do not use browser scraping, generic read_file" in entry.schema["description"]


def test_read_only_behavior(monkeypatch):
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_read_table(BASE_URL, maximum_rows=1)
    assert result["read_only"] is True


def test_prepare_record_update_unique_match(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    calls, _ = _install_fake_bitable(monkeypatch)
    result = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_name="Project Tracking",
        matching_conditions=[
            {"field": "Maison", "operator": "contains", "value": "Celine"},
            {"field": "分组", "operator": "equals", "value": "Contexual Outreach"},
            {"field": "任务描述", "operator": "equals", "value": "Contextual Outreach"},
        ],
        target_field="Risk",
        proposed_new_value="test成功",
    )
    assert result["success"] is True
    assert result["change_id"].startswith("fbu_")
    assert result["record_id"] == "rec1"
    assert result["old_value"] is None
    assert result["write_performed"] is False
    assert not [call for call in calls if call["method"] == "PUT"]


def test_prepare_record_update_zero_matches(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Maison": "Nope"},
        target_field="Risk",
        proposed_new_value="test成功",
    )
    assert result["success"] is False
    assert result["candidate_record_ids"] == []
    assert not list(Path(tmp_path).glob("*.json"))


def test_prepare_record_update_multiple_matches(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    result = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions=[{"field": "Maison", "operator": "contains", "value": "Celine"}],
        target_field="Risk",
        proposed_new_value="test成功",
    )
    assert result["success"] is False
    assert result["candidate_record_ids"] == ["rec1", "rec2", "rec3"]
    assert not list(Path(tmp_path).glob("*.json"))


def test_preview_does_not_write(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    calls, _ = _install_fake_bitable(monkeypatch)
    bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Task": "Empty owner row"},
        target_field="Risk",
        proposed_new_value="new",
    )
    assert all(call["method"] != "PUT" for call in calls)


def test_successful_confirmed_text_update_and_verification(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    calls, records = _install_fake_bitable(monkeypatch)
    preview = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Task": "Empty owner row"},
        target_field="Risk",
        proposed_new_value="test成功",
    )
    result = bt.bitable_apply_record_update(
        change_id=preview["change_id"],
        confirmation_text=f"确认修改 {preview['change_id']}",
    )
    assert result["success"] is True
    assert result["record_id"] == "rec2"
    assert result["old_value"] == "old"
    assert result["final_saved_value"] == "test成功"
    assert records[1]["fields"]["Risk"] == "test成功"
    put_calls = [call for call in calls if call["method"] == "PUT"]
    assert len(put_calls) == 1
    assert put_calls[0]["body"] == {"fields": {"Risk": "test成功"}}


def test_apply_invalid_change_id(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    out = json.loads(bt._handler(bt._apply_record_update_handler)({
        "change_id": "fbu_missing",
        "confirmation_text": "确认修改 fbu_missing",
    }))
    assert "当前没有有效的修改预览" in out["error"]


def test_apply_expired_change_id(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    change_id = "fbu_expired"
    bt._save_pending({
        "change_id": change_id,
        "app_token": "app",
        "table_id": "tbl",
        "record_id": "rec",
        "field_name": "Risk",
        "old_value": "old",
        "proposed_new_value": "new",
        "expires_at": time.time() - 1,
    })
    out = json.loads(bt._handler(bt._apply_record_update_handler)({
        "change_id": change_id,
        "confirmation_text": f"确认修改 {change_id}",
    }))
    assert "已过期" in out["error"]


def test_revised_preview_invalidates_previous_change_id(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    first = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Task": "Empty owner row"},
        target_field="Risk",
        proposed_new_value="first",
    )
    second = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Task": "Empty owner row"},
        target_field="Risk",
        proposed_new_value="second",
    )
    assert first["change_id"] in second["invalidated_change_ids"]
    assert bt._load_pending(first["change_id"]) is None
    assert bt._load_pending(second["change_id"]) is not None


def test_permission_failure_includes_missing_scope(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch, update_permission_code=403)
    preview = bt.bitable_prepare_record_update(
        base_url=BASE_URL,
        table_id="tblsJ9hnV1u9ulX1",
        matching_conditions={"Task": "Empty owner row"},
        target_field="Risk",
        proposed_new_value="test成功",
    )
    out = json.loads(bt._handler(bt._apply_record_update_handler)({
        "change_id": preview["change_id"],
        "confirmation_text": f"确认修改 {preview['change_id']}",
    }))
    assert out["code"] == "permission_denied"
    assert "base:record:update" in out["error"]


def test_unsupported_field_type(monkeypatch, tmp_path):
    _pending_in_tmp(monkeypatch, tmp_path)
    _install_fake_bitable(monkeypatch)
    out = json.loads(bt._handler(bt._prepare_record_update_handler)({
        "base_url": BASE_URL,
        "table_id": "tblsJ9hnV1u9ulX1",
        "matching_conditions": {"Task": "Empty owner row"},
        "target_field": "Owner",
        "proposed_new_value": "Bob",
    }))
    assert out["code"] == "unsupported_field_type"
    assert "仅支持 Text 字段" in out["error"]


def test_no_fallback_to_comments_or_docs_for_bitable_writes():
    hint = PLATFORM_HINTS["feishu"]
    assert "feishu_bitable_prepare_record_update" in hint
    assert "feishu_bitable_apply_record_update" in hint
    assert "Never substitute Feishu comments, Feishu Docs tools" in hint


def test_update_tools_registered():
    for name in ("feishu_bitable_prepare_record_update", "feishu_bitable_apply_record_update"):
        entry = registry.get_entry(name)
        assert entry is not None
        assert entry.toolset == "feishu_bitable"
