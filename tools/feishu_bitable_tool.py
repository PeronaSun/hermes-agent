"""Feishu Bitable/Base tools -- read and safely update Base tables via Feishu/Lark API."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_local = threading.local()
_global_client = None
_client_lock = threading.Lock()
_pending_lock = threading.Lock()
_PENDING_TTL_SECONDS = 30 * 60
_READ_SCOPE_HINT = "base:record:retrieve"
_WRITE_SCOPE_HINT = "base:record:update"

_BASE_URL_RE = re.compile(r"/base/(?P<app_token>[A-Za-z0-9_-]+)")

_APP_URI = "/open-apis/bitable/v1/apps/:app_token"
_TABLES_URI = "/open-apis/bitable/v1/apps/:app_token/tables"
_VIEWS_URI = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/views"
_FIELDS_URI = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
_RECORDS_URI = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
_RECORD_URI = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"


def set_client(client):
    """Store the live lark SDK client for Bitable tools."""
    global _global_client
    _local.client = client
    with _client_lock:
        _global_client = client


def get_client():
    """Return the thread-local or process-global lark SDK client."""
    return getattr(_local, "client", None) or _global_client


def _check_feishu():
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def parse_base_url(base_url: str) -> dict:
    """Extract app_token, table_id and view_id from a Feishu Base URL."""
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("无效的飞书 Base URL")
    match = _BASE_URL_RE.search(parsed.path or "")
    if not match:
        raise ValueError("仅支持包含 /base/ 的飞书 Base / Bitable URL")
    query = parse_qs(parsed.query or "")
    return {
        "app_token": match.group("app_token"),
        "table_id": (query.get("table") or [""])[0],
        "view_id": (query.get("view") or [""])[0],
    }


def _response_data(response) -> dict:
    data = {}
    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            body = json.loads(raw.content)
            data = body.get("data", {})
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    if not data:
        resp_data = getattr(response, "data", None)
        if isinstance(resp_data, dict):
            data = resp_data
        elif resp_data and hasattr(resp_data, "__dict__"):
            data = vars(resp_data)
    return data if isinstance(data, dict) else {}


def _do_request(client, method, uri, paths=None, queries=None, body=None):
    """Build and execute a lark BaseRequest, returning (code, msg, data)."""
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    http_method = getattr(HttpMethod, method.upper(), None)
    if http_method is None:
        http_method = {
            "GET": HttpMethod.GET,
            "POST": HttpMethod.POST,
            "PUT": HttpMethod.PUT,
            "PATCH": getattr(HttpMethod, "PATCH", HttpMethod.PUT),
            "DELETE": HttpMethod.DELETE,
        }[method.upper()]

    builder = (
        BaseRequest.builder()
        .http_method(http_method)
        .uri(uri)
        .token_types({AccessTokenType.TENANT})
    )
    if paths:
        builder = builder.paths(paths)
    if queries:
        builder = builder.queries(queries)
    if body is not None:
        builder = builder.body(body)
    response = client.request(builder.build())
    return getattr(response, "code", None), getattr(response, "msg", ""), _response_data(response)


def _missing_scope(msg: str, data: dict | None = None, fallback: str = "") -> str:
    haystack = [str(msg or "")]
    if isinstance(data, dict):
        haystack.append(json.dumps(data, ensure_ascii=False))
    pattern = re.compile(r"(?:base|bitable):[A-Za-z0-9:_-]+")
    for text in haystack:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return fallback


def _raise_api_error(action: str, code, msg, *, empty_permission_hint: bool = False, data=None, required_scope: str = ""):
    if code == 0:
        return
    code_s = str(code)
    if code == 403 or code_s in {"1254003", "1254030", "1254031", "99991663", "99991672"}:
        scope = _missing_scope(msg, data, required_scope)
        suffix = f"缺少权限范围：{scope}。" if scope else "缺少多维表格权限。"
        raise PermissionError(f"{action}失败：Base 未共享给当前应用，或{suffix}")
    if code_s in {"99991400", "99991401", "99991402"}:
        raise RuntimeError(f"{action}失败：Feishu 客户端凭证或 tenant token 不可用，请重启网关后重试。")
    if code_s in {"99991403", "99991404", "99991405"}:
        raise RuntimeError(f"{action}失败：触发飞书 API 限流，请稍后重试。")
    if empty_permission_hint:
        raise PermissionError(f"{action}失败：可能受高级权限限制，接口未返回可见数据。")
    raise RuntimeError(f"{action}失败：code={code} msg={msg}")


def _items(data: dict, *keys: str) -> list:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _has_more(data: dict) -> bool:
    return bool(data.get("has_more") or data.get("has_next"))


def _next_page_token(data: dict) -> str:
    return str(data.get("page_token") or data.get("next_page_token") or "")


def _paginate(client, uri: str, *, paths: dict, base_queries=None, page_size=500, limit=None, action="分页读取") -> list:
    all_items = []
    page_token = ""
    while True:
        queries = list(base_queries or [])
        queries.append(("page_size", str(min(max(int(page_size or 500), 1), 500))))
        if page_token:
            queries.append(("page_token", page_token))
        code, msg, data = _do_request(client, "GET", uri, paths=paths, queries=queries)
        _raise_api_error(action, code, msg, data=data, required_scope=_READ_SCOPE_HINT)
        page_items = _items(data, "items", "records", "tables", "views", "fields")
        all_items.extend(page_items)
        if limit and len(all_items) >= int(limit):
            return all_items[: int(limit)]
        if not _has_more(data):
            return all_items
        page_token = _next_page_token(data)
        if not page_token:
            raise RuntimeError(f"{action}失败：分页响应缺少 page_token。")


def _normalize_app(data: dict, app_token: str) -> dict:
    app = data.get("app") if isinstance(data.get("app"), dict) else data
    return {
        "app_token": app_token,
        "app_name": app.get("name") or app.get("app_name") or "",
        "metadata": app,
        "tables": _items(data, "tables", "items"),
    }


def _normalize_table(table: dict) -> dict:
    return {
        "table_id": str(table.get("table_id") or table.get("id") or ""),
        "table_name": str(table.get("name") or table.get("table_name") or ""),
        "revision": table.get("revision") or table.get("rev"),
        "raw": table,
    }


def _normalize_view(view: dict) -> dict:
    return {
        "view_id": str(view.get("view_id") or view.get("id") or ""),
        "view_name": str(view.get("view_name") or view.get("name") or ""),
        "view_type": view.get("view_type") or view.get("type"),
        "raw": view,
    }


def _normalize_field(field: dict) -> dict:
    property_value = field.get("property") if isinstance(field.get("property"), dict) else {}
    field_type = field.get("type") or field.get("field_type")
    return {
        "field_id": str(field.get("field_id") or field.get("id") or ""),
        "field_name": str(field.get("field_name") or field.get("name") or ""),
        "field_type": field_type,
        "property": property_value,
        "is_primary": bool(field.get("is_primary") or field.get("primary")),
        "is_computed": bool(property_value.get("formula") or property_value.get("lookup") or property_value.get("auto_number")),
        "is_writable": not bool(property_value.get("formula") or property_value.get("lookup") or field.get("is_readonly")),
        "raw": field,
    }


def _value_state(fields: dict, field_name: str) -> str:
    if field_name not in fields:
        return "empty"
    return "null" if fields.get(field_name) is None else "present"


def _normalize_record(record: dict, fields_schema: list[dict]) -> dict:
    raw_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    by_name = {}
    by_id = {}
    states = {}
    for field in fields_schema:
        field_id = field.get("field_id") or ""
        field_name = field.get("field_name") or field_id
        value = raw_fields.get(field_name)
        by_name[field_name] = value if field_name in raw_fields else None
        if field_id:
            by_id[field_id] = value if field_name in raw_fields else None
        states[field_name] = _value_state(raw_fields, field_name)
    for key, value in raw_fields.items():
        if key not in by_name:
            by_name[key] = value
            states[key] = "present" if value is not None else "null"
    return {
        "record_id": str(record.get("record_id") or record.get("id") or ""),
        "fields_by_name": by_name,
        "fields_by_id": by_id,
        "field_states": states,
        "raw_fields": raw_fields,
        "raw": record,
    }


def _match_by_name(items: list[dict], name_key: str, target_name: str, not_found_label: str) -> dict:
    target = str(target_name or "").strip().lower()
    for item in items:
        if str(item.get(name_key) or "").strip().lower() == target:
            return item
    raise LookupError(f"{not_found_label}未找到：{target_name}")


def _find_field(fields: list[dict], field_name_or_id: str) -> dict:
    target = str(field_name_or_id or "").strip()
    if not target:
        raise ValueError("需要提供 target_field。")
    for field in fields:
        if str(field.get("field_id") or "") == target:
            return field
    target_lower = target.lower()
    for field in fields:
        if str(field.get("field_name") or "").strip().lower() == target_lower:
            return field
    raise LookupError(f"字段未找到：{field_name_or_id}")


def _is_text_field(field: dict) -> bool:
    field_type = field.get("field_type")
    return field_type in {1, "1", "text", "Text", "多行文本", "单行文本"}


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("name") or item.get("email") or item.get("value") or ""))
            else:
                parts.append(_cell_text(item))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or value)
    return str(value)


def _normalize_conditions(matching_conditions) -> list[dict]:
    if isinstance(matching_conditions, list):
        normalized = []
        for condition in matching_conditions:
            if not isinstance(condition, dict):
                raise ValueError("matching_conditions 列表中的每一项都必须是对象。")
            field = condition.get("field") or condition.get("field_name") or condition.get("field_id")
            operator = condition.get("operator") or condition.get("op") or "equals"
            value = condition.get("value")
            if not field:
                raise ValueError("每个匹配条件都必须包含 field。")
            normalized.append({"field": str(field), "operator": str(operator), "value": value})
        return normalized
    if isinstance(matching_conditions, dict):
        normalized = []
        for field, value in matching_conditions.items():
            operator = "equals"
            expected = value
            if isinstance(value, dict) and len(value) == 1:
                operator, expected = next(iter(value.items()))
            normalized.append({"field": str(field), "operator": str(operator), "value": expected})
        return normalized
    raise ValueError("matching_conditions 必须是对象或对象列表。")


def _condition_matches(record: dict, condition: dict) -> bool:
    fields = record.get("fields_by_name", {})
    field = condition["field"]
    if field in fields:
        value = fields.get(field)
    else:
        value = record.get("fields_by_id", {}).get(field)
    expected = condition.get("value")
    operator = str(condition.get("operator") or "equals").strip().lower()
    if operator in {"equals", "eq", "=", "等于"}:
        return value == expected or _cell_text(value).strip() == _cell_text(expected).strip()
    if operator in {"contains", "包含"}:
        return _cell_text(expected).strip() in _cell_text(value)
    raise ValueError(f"不支持的匹配操作符：{condition.get('operator')}")


def _record_matches(record: dict, conditions: list[dict]) -> bool:
    return all(_condition_matches(record, condition) for condition in conditions)


def _values_equivalent(saved_value, expected_value) -> bool:
    return saved_value == expected_value or _cell_text(saved_value) == str(expected_value)


def _pending_dir() -> Path:
    return get_hermes_home() / "pending" / "feishu_bitable"


def _pending_path(change_id: str) -> Path:
    return _pending_dir() / f"{change_id}.json"


def _save_pending(record: dict) -> None:
    path = _pending_path(record["change_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_pending(change_id: str) -> dict | None:
    path = _pending_path(change_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _delete_pending(change_id: str) -> None:
    try:
        _pending_path(change_id).unlink()
    except FileNotFoundError:
        pass


def _invalidate_related_pending(new_record: dict) -> list[str]:
    removed = []
    directory = _pending_dir()
    if not directory.exists():
        return removed
    for path in directory.glob("fbu_*.json"):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        same_target = all(
            str(existing.get(key) or "") == str(new_record.get(key) or "")
            for key in ("app_token", "table_id", "record_id", "field_name")
        )
        same_actor = (
            not existing.get("user_id")
            or not new_record.get("user_id")
            or str(existing.get("user_id")) == str(new_record.get("user_id"))
        )
        if same_target and same_actor:
            removed.append(str(existing.get("change_id") or path.stem))
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    return removed


def _validate_pending_actor(record: dict, user_id: str = "", chat_id: str = "") -> str | None:
    if record.get("user_id") and str(record.get("user_id")) != str(user_id or ""):
        return "只有创建该预览的同一位飞书用户可以确认修改。"
    if record.get("chat_id") and str(record.get("chat_id")) != str(chat_id or ""):
        return "只能在创建预览的同一个聊天中确认修改。"
    if time.time() > float(record.get("expires_at") or 0):
        return "该 Change ID 已过期，请重新生成预览。"
    return None


def _client_or_error():
    client = get_client()
    if client is None:
        raise RuntimeError("Feishu 客户端不可用，请确认网关已启动并完成 Feishu 初始化。")
    return client


def _app_token_from_args(args: dict) -> str:
    app_token = str(args.get("app_token") or "").strip()
    if app_token:
        return app_token
    base_url = str(args.get("base_url") or "").strip()
    if base_url:
        return parse_base_url(base_url)["app_token"]
    raise ValueError("需要提供 base_url 或 app_token。")


def bitable_get_app(base_url: str = "", app_token: str = "") -> dict:
    resolved_token = app_token or parse_base_url(base_url)["app_token"]
    client = _client_or_error()
    code, msg, data = _do_request(client, "GET", _APP_URI, paths={"app_token": resolved_token})
    _raise_api_error("读取 Base 元数据", code, msg, data=data, required_scope="bitable:app:readonly")
    return _normalize_app(data, resolved_token)


def bitable_list_tables(app_token: str) -> list[dict]:
    client = _client_or_error()
    tables = _paginate(client, _TABLES_URI, paths={"app_token": app_token}, page_size=100, action="列出 Base 数据表")
    return [_normalize_table(table) for table in tables]


def bitable_list_views(app_token: str, table_id: str) -> list[dict]:
    client = _client_or_error()
    views = _paginate(
        client,
        _VIEWS_URI,
        paths={"app_token": app_token, "table_id": table_id},
        page_size=100,
        action="列出数据表视图",
    )
    return [_normalize_view(view) for view in views]


def bitable_list_fields(app_token: str, table_id: str, view_id: str = "") -> list[dict]:
    client = _client_or_error()
    queries = [("view_id", view_id)] if view_id else []
    fields = _paginate(
        client,
        _FIELDS_URI,
        paths={"app_token": app_token, "table_id": table_id},
        base_queries=queries,
        page_size=100,
        action="列出字段",
    )
    return [_normalize_field(field) for field in fields]


def bitable_list_records(
    app_token: str,
    table_id: str,
    *,
    view_id: str = "",
    filter_value=None,
    page_size: int = 500,
    page_token: str = "",
    limit: int | None = None,
    fields_schema: list[dict] | None = None,
) -> dict:
    client = _client_or_error()
    queries = [("user_id_type", "open_id")]
    if view_id:
        queries.append(("view_id", view_id))
    if filter_value:
        queries.append(("filter", json.dumps(filter_value, ensure_ascii=False) if not isinstance(filter_value, str) else filter_value))
    if page_token:
        queries.append(("page_token", page_token))

    if page_token:
        code, msg, data = _do_request(
            client,
            "GET",
            _RECORDS_URI,
            paths={"app_token": app_token, "table_id": table_id},
            queries=[*queries, ("page_size", str(min(max(int(page_size or 500), 1), 500)))],
        )
        _raise_api_error("读取记录", code, msg, data=data, required_scope=_READ_SCOPE_HINT)
        records = _items(data, "items", "records")
    else:
        records = _paginate(
            client,
            _RECORDS_URI,
            paths={"app_token": app_token, "table_id": table_id},
            base_queries=queries,
            page_size=page_size,
            limit=limit,
            action="读取记录",
        )
        data = {"has_more": False}

    schema = fields_schema or bitable_list_fields(app_token, table_id, view_id)
    normalized = [_normalize_record(record, schema) for record in records]
    return {
        "records": normalized,
        "total_record_count": len(normalized),
        "has_more": _has_more(data),
        "next_page_token": _next_page_token(data),
    }


def bitable_read_table(
    base_url: str,
    *,
    target_table_name: str = "",
    target_view_name: str = "",
    maximum_rows: int | None = None,
) -> dict:
    parsed = parse_base_url(base_url)
    app_token = parsed["app_token"]
    table_id = parsed["table_id"]
    view_id = parsed["view_id"]
    app = bitable_get_app(app_token=app_token)
    table_name = ""
    view_name = ""
    inaccessible = []

    tables = []
    if target_table_name or not table_id:
        tables = bitable_list_tables(app_token)
        table = _match_by_name(tables, "table_name", target_table_name, "数据表") if target_table_name else (tables[0] if tables else None)
        if table is None:
            raise LookupError("数据表未找到：Base 中没有可用数据表。")
        table_id = table["table_id"]
        table_name = table["table_name"]
    else:
        tables = bitable_list_tables(app_token)
        table = next((item for item in tables if item.get("table_id") == table_id), None)
        table_name = (table or {}).get("table_name", "")

    views = []
    if target_view_name or view_id:
        views = bitable_list_views(app_token, table_id)
        if target_view_name:
            view = _match_by_name(views, "view_name", target_view_name, "视图")
            view_id = view["view_id"]
            view_name = view["view_name"]
        elif view_id:
            view = next((item for item in views if item.get("view_id") == view_id), None)
            view_name = (view or {}).get("view_name", "")

    fields = bitable_list_fields(app_token, table_id, view_id)
    if not fields:
        raise PermissionError("字段列表为空：可能缺少多维表格字段读取权限，或受高级权限限制。")
    records_result = bitable_list_records(
        app_token,
        table_id,
        view_id=view_id,
        page_size=500,
        limit=maximum_rows,
        fields_schema=fields,
    )
    records = records_result["records"]
    if not records and maximum_rows is None:
        inaccessible.append({
            "reason": "empty_records",
            "message": "记录列表为空；如果 Base 中确认有数据，可能是高级权限或应用授权范围限制。",
        })

    return {
        "source_base_url": base_url,
        "app_token": app_token,
        "app_name": app.get("app_name", ""),
        "app_metadata": app.get("metadata", {}),
        "table_id": table_id,
        "table_name": table_name,
        "view_id": view_id,
        "view_name": view_name,
        "field_schema": fields,
        "total_record_count": records_result["total_record_count"],
        "records": records,
        "inaccessible_or_unsupported_fields": inaccessible,
        "read_only": True,
    }


def _resolve_table_for_update(
    *,
    base_url: str = "",
    app_token: str = "",
    table_id: str = "",
    table_name: str = "",
    view_id: str = "",
    view_name: str = "",
) -> dict:
    parsed = parse_base_url(base_url) if base_url else {}
    resolved_app_token = app_token or parsed.get("app_token", "")
    resolved_table_id = table_id or parsed.get("table_id", "")
    resolved_view_id = view_id or parsed.get("view_id", "")
    if not resolved_app_token:
        raise ValueError("需要提供 base_url 或 app_token。")

    tables = bitable_list_tables(resolved_app_token)
    resolved_table_name = ""
    if table_name:
        table = _match_by_name(tables, "table_name", table_name, "数据表")
        resolved_table_id = table["table_id"]
        resolved_table_name = table["table_name"]
    elif resolved_table_id:
        table = next((item for item in tables if item.get("table_id") == resolved_table_id), None)
        if table:
            resolved_table_name = table.get("table_name", "")
    else:
        raise ValueError("需要提供 table_name 或 table_id。")

    resolved_view_name = ""
    if view_name or resolved_view_id:
        views = bitable_list_views(resolved_app_token, resolved_table_id)
        if view_name:
            view = _match_by_name(views, "view_name", view_name, "视图")
            resolved_view_id = view["view_id"]
            resolved_view_name = view["view_name"]
        else:
            view = next((item for item in views if item.get("view_id") == resolved_view_id), None)
            resolved_view_name = (view or {}).get("view_name", "")

    return {
        "app_token": resolved_app_token,
        "table_id": resolved_table_id,
        "table_name": resolved_table_name,
        "view_id": resolved_view_id,
        "view_name": resolved_view_name,
    }


def bitable_prepare_record_update(
    *,
    base_url: str = "",
    app_token: str = "",
    table_id: str = "",
    table_name: str = "",
    view_id: str = "",
    view_name: str = "",
    matching_conditions=None,
    target_field: str = "",
    proposed_new_value=None,
    user_id: str = "",
    chat_id: str = "",
) -> dict:
    resolved = _resolve_table_for_update(
        base_url=base_url,
        app_token=app_token,
        table_id=table_id,
        table_name=table_name,
        view_id=view_id,
        view_name=view_name,
    )
    conditions = _normalize_conditions(matching_conditions)
    fields = bitable_list_fields(resolved["app_token"], resolved["table_id"], resolved["view_id"])
    target = _find_field(fields, target_field)
    if not target.get("is_writable", True):
        raise ValueError(f"字段不可写：{target.get('field_name') or target_field}")
    if not _is_text_field(target):
        raise TypeError(f"暂不支持更新该字段类型：{target.get('field_name')} ({target.get('field_type')})。当前仅支持 Text 字段。")

    records_result = bitable_list_records(
        resolved["app_token"],
        resolved["table_id"],
        view_id=resolved["view_id"],
        page_size=500,
        fields_schema=fields,
    )
    matches = [record for record in records_result["records"] if _record_matches(record, conditions)]
    if not matches:
        return {
            "success": False,
            "message": "没有找到符合匹配条件的记录，未生成 Change ID，未写入任何数据。",
            "matching_conditions": conditions,
            "candidate_record_ids": [],
        }
    if len(matches) > 1:
        return {
            "success": False,
            "message": "匹配到多条记录，未生成 Change ID，未写入任何数据。请增加更明确的匹配条件。",
            "matching_conditions": conditions,
            "candidate_record_ids": [record["record_id"] for record in matches],
        }

    record = matches[0]
    field_name = target["field_name"]
    old_value = record.get("fields_by_name", {}).get(field_name)
    change_id = f"fbu_{uuid.uuid4().hex[:12]}"
    now = time.time()
    pending = {
        "change_id": change_id,
        "app_token": resolved["app_token"],
        "base_url": base_url,
        "table_id": resolved["table_id"],
        "table_name": resolved["table_name"],
        "view_id": resolved["view_id"],
        "view_name": resolved["view_name"],
        "record_id": record["record_id"],
        "matching_conditions": conditions,
        "field_id": target.get("field_id", ""),
        "field_name": field_name,
        "field_type": target.get("field_type"),
        "old_value": old_value,
        "proposed_new_value": proposed_new_value,
        "user_id": str(user_id or ""),
        "chat_id": str(chat_id or ""),
        "created_at": now,
        "expires_at": now + _PENDING_TTL_SECONDS,
    }
    with _pending_lock:
        invalidated = _invalidate_related_pending(pending)
        _save_pending(pending)
    return {
        "success": True,
        "message": "以下为预览，尚未修改原多维表格。请发送：确认修改 " + change_id,
        "change_id": change_id,
        "expires_in_seconds": _PENDING_TTL_SECONDS,
        "invalidated_change_ids": invalidated,
        "table_name": resolved["table_name"],
        "table_id": resolved["table_id"],
        "record_id": record["record_id"],
        "matching_conditions": conditions,
        "field_name": field_name,
        "old_value": old_value,
        "proposed_new_value": proposed_new_value,
        "write_performed": False,
    }


def _update_record_field(app_token: str, table_id: str, record_id: str, field_name: str, value) -> None:
    client = _client_or_error()
    code, msg, data = _do_request(
        client,
        "PUT",
        _RECORD_URI,
        paths={"app_token": app_token, "table_id": table_id, "record_id": record_id},
        queries=[("user_id_type", "open_id")],
        body={"fields": {field_name: value}},
    )
    _raise_api_error("更新记录", code, msg, data=data, required_scope=_WRITE_SCOPE_HINT)


def _read_record_from_table(record: dict) -> dict | None:
    fields = bitable_list_fields(record["app_token"], record["table_id"], record.get("view_id", ""))
    records = bitable_list_records(
        record["app_token"],
        record["table_id"],
        view_id=record.get("view_id", ""),
        page_size=500,
        fields_schema=fields,
    )["records"]
    return next((item for item in records if item.get("record_id") == record.get("record_id")), None)


def bitable_apply_record_update(
    *,
    change_id: str,
    confirmation_text: str = "",
    user_id: str = "",
    chat_id: str = "",
) -> dict:
    if not change_id:
        raise ValueError("当前没有有效的修改预览，请先重新生成预览。")
    expected_confirmation = f"确认修改 {change_id}"
    if str(confirmation_text or "").strip() != expected_confirmation:
        raise ValueError(f"请发送明确确认：{expected_confirmation}")

    pending = _load_pending(change_id)
    if not pending:
        raise LookupError("当前没有有效的修改预览，请先重新生成预览。")
    actor_error = _validate_pending_actor(pending, user_id, chat_id)
    if actor_error:
        raise ValueError(actor_error)

    before = _read_record_from_table(pending)
    if before is None:
        raise LookupError("目标记录不存在或当前应用无权读取，未写入任何数据。")
    current_old_value = before.get("fields_by_name", {}).get(pending["field_name"])
    if not _values_equivalent(current_old_value, pending.get("old_value")):
        raise RuntimeError("目标单元格在预览后发生变化，未写入任何数据。请重新生成预览。")

    _update_record_field(
        pending["app_token"],
        pending["table_id"],
        pending["record_id"],
        pending["field_name"],
        pending.get("proposed_new_value"),
    )
    after = _read_record_from_table(pending)
    if after is None:
        raise RuntimeError("写入后无法重新读取目标记录，无法确认保存结果。")
    final_value = after.get("fields_by_name", {}).get(pending["field_name"])
    verified = _values_equivalent(final_value, pending.get("proposed_new_value"))
    if not verified:
        raise RuntimeError("写入后校验失败：飞书返回的最终值与预期不一致。")
    _delete_pending(change_id)
    return {
        "success": True,
        "table_id": pending["table_id"],
        "record_id": pending["record_id"],
        "field_name": pending["field_name"],
        "old_value": pending.get("old_value"),
        "final_saved_value": final_value,
        "verified": True,
    }


FEISHU_BITABLE_GET_APP_SCHEMA = {
    "name": "feishu_bitable_get_app",
    "description": "Read Feishu Base/Bitable app metadata from a /base/ URL or app_token. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Feishu Base URL containing /base/."},
            "app_token": {"type": "string", "description": "Bitable app token."},
        },
    },
}

FEISHU_BITABLE_LIST_TABLES_SCHEMA = {
    "name": "feishu_bitable_list_tables",
    "description": "List tables in a Feishu Base/Bitable app. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"app_token": {"type": "string"}},
        "required": ["app_token"],
    },
}

FEISHU_BITABLE_LIST_VIEWS_SCHEMA = {
    "name": "feishu_bitable_list_views",
    "description": "List views in a Feishu Base/Bitable table. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"app_token": {"type": "string"}, "table_id": {"type": "string"}},
        "required": ["app_token", "table_id"],
    },
}

FEISHU_BITABLE_LIST_FIELDS_SCHEMA = {
    "name": "feishu_bitable_list_fields",
    "description": "List every field/column definition in a Feishu Base/Bitable table or view. Read-only.",
    "parameters": {
        "type": "object",
        "properties": {"app_token": {"type": "string"}, "table_id": {"type": "string"}, "view_id": {"type": "string"}},
        "required": ["app_token", "table_id"],
    },
}

FEISHU_BITABLE_LIST_RECORDS_SCHEMA = {
    "name": "feishu_bitable_list_records",
    "description": (
        "List Feishu Base/Bitable records with pagination, preserving field types and raw complex values. "
        "Use feishu_bitable_read_table for normal user requests like reading every cell, summarizing a task table, "
        "or finding overdue tasks. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {"type": "string"},
            "table_id": {"type": "string"},
            "view_id": {"type": "string"},
            "filter": {"description": "Optional Feishu filter expression or structured filter object."},
            "page_size": {"type": "integer", "default": 500},
            "page_token": {"type": "string"},
            "limit": {"type": "integer", "description": "Optional maximum number of records to return."},
        },
        "required": ["app_token", "table_id"],
    },
}

FEISHU_BITABLE_READ_TABLE_SCHEMA = {
    "name": "feishu_bitable_read_table",
    "description": (
        "High-level read-only tool for Feishu /base/ URLs. Use this when the user asks to read Project Tracking, "
        "read every cell, summarize a task table, find overdue tasks, or inspect Base/Bitable data. "
        "Do not use browser scraping, generic read_file, or docx tools for /base/ URLs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Feishu Base URL containing /base/."},
            "target_table_name": {"type": "string", "description": "Optional table name, e.g. Project Tracking."},
            "target_view_name": {"type": "string", "description": "Optional view name."},
            "maximum_rows": {"type": "integer", "description": "Optional maximum rows to retrieve."},
        },
        "required": ["base_url"],
    },
}

FEISHU_BITABLE_PREPARE_RECORD_UPDATE_SCHEMA = {
    "name": "feishu_bitable_prepare_record_update",
    "description": (
        "Preview a safe Feishu Base/Bitable single-cell update. Re-reads live records, requires exactly one "
        "matching record, supports Text fields first, and returns a Change ID. Does not write."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Feishu Base URL containing /base/."},
            "app_token": {"type": "string"},
            "table_id": {"type": "string"},
            "table_name": {"type": "string"},
            "view_id": {"type": "string"},
            "view_name": {"type": "string"},
            "matching_conditions": {
                "description": (
                    "Explicit record matching conditions. Use a dict for equals, or a list like "
                    "[{\"field\":\"Maison\",\"operator\":\"contains\",\"value\":\"Celine\"}]."
                )
            },
            "target_field": {"type": "string", "description": "Target field name or field_id."},
            "proposed_new_value": {"description": "New Text value to preview."},
            "user_id": {"type": "string", "description": "Optional Feishu user id for confirmation binding."},
            "chat_id": {"type": "string", "description": "Optional Feishu chat id for confirmation binding."},
        },
        "required": ["matching_conditions", "target_field", "proposed_new_value"],
    },
}

FEISHU_BITABLE_APPLY_RECORD_UPDATE_SCHEMA = {
    "name": "feishu_bitable_apply_record_update",
    "description": (
        "Apply a pending Feishu Base/Bitable single-cell update only after exact confirmation text "
        "'确认修改 <Change ID>'. Uses the official record update API, re-reads, and verifies the saved value."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "change_id": {"type": "string", "description": "Change ID returned by feishu_bitable_prepare_record_update."},
            "confirmation_text": {"type": "string", "description": "Must exactly equal 确认修改 <Change ID>."},
            "user_id": {"type": "string", "description": "Optional Feishu user id for confirmation binding."},
            "chat_id": {"type": "string", "description": "Optional Feishu chat id for confirmation binding."},
        },
        "required": ["change_id", "confirmation_text"],
    },
}


def _handler(fn):
    def wrapped(args: dict, **kwargs) -> str:
        try:
            return tool_result(fn(args))
        except LookupError as exc:
            return tool_error(str(exc), code="not_found")
        except PermissionError as exc:
            return tool_error(str(exc), code="permission_denied")
        except TypeError as exc:
            return tool_error(str(exc), code="unsupported_field_type")
        except ValueError as exc:
            return tool_error(str(exc))
        except RuntimeError as exc:
            return tool_error(str(exc))

    return wrapped


def _get_app_handler(args: dict) -> dict:
    return bitable_get_app(base_url=str(args.get("base_url") or ""), app_token=str(args.get("app_token") or ""))


def _list_tables_handler(args: dict) -> dict:
    return {"tables": bitable_list_tables(str(args.get("app_token") or ""))}


def _list_views_handler(args: dict) -> dict:
    return {"views": bitable_list_views(str(args.get("app_token") or ""), str(args.get("table_id") or ""))}


def _list_fields_handler(args: dict) -> dict:
    return {
        "fields": bitable_list_fields(
            str(args.get("app_token") or ""),
            str(args.get("table_id") or ""),
            str(args.get("view_id") or ""),
        )
    }


def _list_records_handler(args: dict) -> dict:
    return bitable_list_records(
        str(args.get("app_token") or ""),
        str(args.get("table_id") or ""),
        view_id=str(args.get("view_id") or ""),
        filter_value=args.get("filter"),
        page_size=int(args.get("page_size") or 500),
        page_token=str(args.get("page_token") or ""),
        limit=args.get("limit"),
    )


def _read_table_handler(args: dict) -> dict:
    return bitable_read_table(
        str(args.get("base_url") or ""),
        target_table_name=str(args.get("target_table_name") or ""),
        target_view_name=str(args.get("target_view_name") or ""),
        maximum_rows=args.get("maximum_rows"),
    )


def _prepare_record_update_handler(args: dict) -> dict:
    return bitable_prepare_record_update(
        base_url=str(args.get("base_url") or ""),
        app_token=str(args.get("app_token") or ""),
        table_id=str(args.get("table_id") or ""),
        table_name=str(args.get("table_name") or ""),
        view_id=str(args.get("view_id") or ""),
        view_name=str(args.get("view_name") or ""),
        matching_conditions=args.get("matching_conditions"),
        target_field=str(args.get("target_field") or ""),
        proposed_new_value=args.get("proposed_new_value"),
        user_id=str(args.get("user_id") or ""),
        chat_id=str(args.get("chat_id") or ""),
    )


def _apply_record_update_handler(args: dict) -> dict:
    return bitable_apply_record_update(
        change_id=str(args.get("change_id") or ""),
        confirmation_text=str(args.get("confirmation_text") or ""),
        user_id=str(args.get("user_id") or ""),
        chat_id=str(args.get("chat_id") or ""),
    )


for _name, _schema, _handler_fn, _description, _emoji in (
    ("feishu_bitable_get_app", FEISHU_BITABLE_GET_APP_SCHEMA, _get_app_handler, "Read Feishu Base metadata", "\U0001f5c3"),
    ("feishu_bitable_list_tables", FEISHU_BITABLE_LIST_TABLES_SCHEMA, _list_tables_handler, "List Feishu Base tables", "\U0001f5c2"),
    ("feishu_bitable_list_views", FEISHU_BITABLE_LIST_VIEWS_SCHEMA, _list_views_handler, "List Feishu Base views", "\U0001f441"),
    ("feishu_bitable_list_fields", FEISHU_BITABLE_LIST_FIELDS_SCHEMA, _list_fields_handler, "List Feishu Base fields", "\U0001f4cb"),
    ("feishu_bitable_list_records", FEISHU_BITABLE_LIST_RECORDS_SCHEMA, _list_records_handler, "List Feishu Base records", "\U0001f4ca"),
    ("feishu_bitable_read_table", FEISHU_BITABLE_READ_TABLE_SCHEMA, _read_table_handler, "Read Feishu Base table", "\U0001f4d6"),
    (
        "feishu_bitable_prepare_record_update",
        FEISHU_BITABLE_PREPARE_RECORD_UPDATE_SCHEMA,
        _prepare_record_update_handler,
        "Preview Feishu Base record update",
        "\u270f\ufe0f",
    ),
    (
        "feishu_bitable_apply_record_update",
        FEISHU_BITABLE_APPLY_RECORD_UPDATE_SCHEMA,
        _apply_record_update_handler,
        "Apply Feishu Base record update",
        "\u2705",
    ),
):
    registry.register(
        name=_name,
        toolset="feishu_bitable",
        schema=_schema,
        handler=_handler(_handler_fn),
        check_fn=_check_feishu,
        requires_env=[],
        is_async=False,
        description=_description,
        emoji=_emoji,
    )
