"""Feishu Document Tool -- document workflows via Feishu/Lark API.

Provides read, preview, apply, and cancel helpers for upgraded Feishu docs.
Uses the same lazy-import + BaseRequest pattern as feishu_comment.py.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Thread-local storage for the lark client injected by feishu_comment handler.
_local = threading.local()
_global_client = None
_client_lock = threading.Lock()
_pending_lock = threading.Lock()
_PENDING_TTL_SECONDS = 30 * 60


def set_client(client):
    """Store a lark client for the current thread (called by feishu_comment)."""
    global _global_client
    _local.client = client
    with _client_lock:
        _global_client = client


def get_client():
    """Return the lark client for the current thread, or None."""
    return getattr(_local, "client", None) or _global_client


# ---------------------------------------------------------------------------
# feishu_doc_read
# ---------------------------------------------------------------------------

_RAW_CONTENT_URI = "/open-apis/docx/v1/documents/:document_id/raw_content"
_DOCUMENT_META_URI = "/open-apis/docx/v1/documents/:document_id"
_DOCUMENT_BLOCKS_URI = "/open-apis/docx/v1/documents/:document_id/blocks"
_DOCUMENT_CHILDREN_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
_DOCUMENT_CHILDREN_DELETE_URI = (
    "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
)

_DOCX_URL_RE = re.compile(r"/docx/(?P<token>[A-Za-z0-9_-]+)")
_VAGUE_CONFIRMATIONS = {"好的", "可以", "修改吧", "好", "ok", "OK", "确认", "行"}

FEISHU_DOC_READ_SCHEMA = {
    "name": "feishu_doc_read",
    "description": (
        "Read the full content of a Feishu/Lark document as plain text. "
        "For Feishu /docx/ document body workflows, use feishu_doc_prepare_meeting_minutes "
        "to preview changes and feishu_doc_apply_change only after exact confirmation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL or comment context).",
            },
        },
        "required": ["doc_token"],
    },
}

FEISHU_DOC_PREPARE_SCHEMA = {
    "name": "feishu_doc_prepare_meeting_minutes",
    "description": (
        "Read a Feishu upgraded /docx/ document URL, convert rough meeting or project-coordination "
        "material into a professional Chinese meeting-minutes preview, and create a 30-minute "
        "pending change. Does not modify the document. Use this for reading, previewing, replacing, "
        "or updating a Feishu docx document body; do not use Feishu Drive comment tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_url": {"type": "string", "description": "Feishu/Lark upgraded document URL containing /docx/."},
            "user_id": {"type": "string", "description": "Feishu user id from the inbound message source."},
            "chat_id": {"type": "string", "description": "Feishu chat id from the inbound message source."},
        },
        "required": ["doc_url", "user_id", "chat_id"],
    },
}

FEISHU_DOC_APPLY_SCHEMA = {
    "name": "feishu_doc_apply_change",
    "description": (
        "Apply a pending Feishu /docx/ document-body meeting-minutes change only after the same "
        "user sends the exact text 确认修改 CHANGE_ID in the same chat. Never use this for vague "
        "confirmations, and never substitute Drive comments for body edits."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "change_id": {"type": "string", "description": "Change ID returned by the preview."},
            "confirmation_text": {"type": "string", "description": "Original user confirmation text, e.g. 确认修改 CHANGE_ID."},
            "user_id": {"type": "string", "description": "Feishu user id from the inbound message source."},
            "chat_id": {"type": "string", "description": "Feishu chat id from the inbound message source."},
        },
        "required": ["change_id", "confirmation_text", "user_id", "chat_id"],
    },
}

FEISHU_DOC_CANCEL_SCHEMA = {
    "name": "feishu_doc_cancel_change",
    "description": "Cancel a pending Feishu /docx/ document-body meeting-minutes change.",
    "parameters": {
        "type": "object",
        "properties": {
            "change_id": {"type": "string", "description": "Change ID returned by the preview."},
            "user_id": {"type": "string", "description": "Feishu user id from the inbound message source."},
            "chat_id": {"type": "string", "description": "Feishu chat id from the inbound message source."},
        },
        "required": ["change_id", "user_id", "chat_id"],
    },
}


def _check_feishu():
    # Use ``importlib.util.find_spec`` — it checks whether ``lark_oapi``
    # is importable without actually executing its ``__init__``.
    # Executing the real import here costs ~5 seconds (the SDK eagerly
    # loads websockets, dispatcher, every api/v2 model) and this probe
    # fires at every ``hermes`` startup during tool-availability
    # evaluation.  Correctness is preserved because the actual tool
    # handler still does the real import when invoked.
    import importlib.util
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def parse_docx_url(url: str) -> str:
    """Extract a Feishu/Lark upgraded document id from a /docx/ URL."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid Feishu document URL")
    match = _DOCX_URL_RE.search(parsed.path or "")
    if not match:
        raise ValueError("Only upgraded Feishu document URLs containing /docx/ are supported")
    return match.group("token")


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

    method_name = method.upper()
    http_method = getattr(HttpMethod, method_name, None)
    if http_method is None:
        http_method = {
            "GET": HttpMethod.GET,
            "POST": HttpMethod.POST,
            "DELETE": getattr(HttpMethod, "DELETE", HttpMethod.POST),
            "PATCH": getattr(HttpMethod, "PATCH", HttpMethod.POST),
        }[method_name]

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


def _raise_api_error(action: str, code, msg):
    if code == 0:
        return
    if code == 403 or str(code) in {"1770032", "99991663"}:
        raise PermissionError(f"{action} failed: permission denied")
    if str(code) in {"99991400", "99991401", "99991402"}:
        raise RuntimeError(f"{action} failed: token expired or unauthorized")
    if str(code) in {"99991403", "99991404", "99991405"}:
        raise RuntimeError(f"{action} failed: rate limited")
    raise RuntimeError(f"{action} failed: code={code} msg={msg}")


def _extract_blocks(data: dict) -> list:
    for key in ("items", "blocks", "children"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _page_token(data: dict) -> str:
    return str(data.get("page_token") or data.get("next_page_token") or "")


def _has_more(data: dict) -> bool:
    return bool(data.get("has_more") or data.get("has_next"))


def _document_meta(client, document_id: str) -> dict:
    code, msg, data = _do_request(
        client,
        "GET",
        _DOCUMENT_META_URI,
        paths={"document_id": document_id},
    )
    _raise_api_error("Read document metadata", code, msg)
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    return document if isinstance(document, dict) else {}


def _read_blocks(client, document_id: str, *, page_size: int = 500) -> list:
    blocks = []
    page_token = ""
    for _ in range(100):
        queries = [("page_size", str(page_size))]
        if page_token:
            queries.append(("page_token", page_token))
        code, msg, data = _do_request(
            client,
            "GET",
            _DOCUMENT_BLOCKS_URI,
            paths={"document_id": document_id},
            queries=queries,
        )
        _raise_api_error("Read document blocks", code, msg)
        blocks.extend(_extract_blocks(data))
        if not _has_more(data):
            break
        page_token = _page_token(data)
        if not page_token:
            break
    return blocks


def _block_id(block: dict) -> str:
    return str(block.get("block_id") or block.get("id") or "")


def _parent_id(block: dict) -> str:
    return str(block.get("parent_id") or block.get("parent_block_id") or "")


def _children_ids(block: dict) -> list[str]:
    children = block.get("children") or block.get("children_ids") or []
    return [str(child) for child in children if str(child)]


def _root_block_id(blocks: list[dict], document_id: str) -> str:
    for block in blocks:
        if int(block.get("block_type") or 0) == 1:
            return _block_id(block) or document_id
    for block in blocks:
        if not _parent_id(block):
            return _block_id(block) or document_id
    return document_id


def _editable_body_blocks(blocks: list[dict], document_id: str) -> list[dict]:
    root = _root_block_id(blocks, document_id)
    children = []
    child_ids = set()
    for block in blocks:
        if _block_id(block) == root:
            child_ids.update(_children_ids(block))
            break
    for block in blocks:
        bid = _block_id(block)
        if bid and (bid in child_ids or _parent_id(block) == root):
            children.append(block)
    return children or [b for b in blocks if _block_id(b) != root]


def _text_from_elements(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text_from_elements(item) for item in value)
    if not isinstance(value, dict):
        return str(value)
    for key in ("content", "text"):
        if isinstance(value.get(key), str):
            return value[key]
    if isinstance(value.get("text_run"), dict):
        return _text_from_elements(value["text_run"])
    if isinstance(value.get("mention_user"), dict):
        return value["mention_user"].get("name") or value["mention_user"].get("user_id") or ""
    if isinstance(value.get("equation"), dict):
        return value["equation"].get("content") or ""
    pieces = []
    for key in ("elements", "text_elements", "children"):
        if isinstance(value.get(key), list):
            pieces.append(_text_from_elements(value[key]))
    return "".join(pieces)


def _block_text(block: dict) -> str:
    candidates = []
    for key in (
        "text", "heading1", "heading2", "heading3", "heading4", "heading5",
        "heading6", "heading7", "heading8", "heading9", "bullet", "ordered",
        "quote", "code", "table",
    ):
        if key in block:
            candidates.append(block[key])
    candidates.extend(v for k, v in block.items() if k.endswith("_block") or k.endswith("_data"))
    return " ".join(part for part in (_text_from_elements(c) for c in candidates) if part).strip()


def normalize_blocks_to_text(blocks: list[dict], document_id: str = "") -> str:
    lines = []
    counters: dict[str, int] = {}
    for block in blocks:
        block_type = int(block.get("block_type") or 0)
        if block_type == 1:
            continue
        text = _block_text(block)
        if block_type == 31:
            table_text = _normalize_table_text(block, text)
            if table_text:
                lines.append(table_text)
            continue
        if not text:
            continue
        if 3 <= block_type <= 11:
            level = min(block_type - 2, 6)
            lines.append(f"{'#' * level} {text}")
        elif block_type == 12:
            lines.append(f"- {text}")
        elif block_type == 13:
            parent = _parent_id(block) or document_id or "root"
            counters[parent] = counters.get(parent, 0) + 1
            lines.append(f"{counters[parent]}. {text}")
        else:
            lines.append(text)
    return "\n".join(lines).strip()


def _normalize_table_text(block: dict, fallback: str) -> str:
    rows = block.get("rows") or block.get("table", {}).get("rows")
    if not isinstance(rows, list):
        return fallback
    rendered = []
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else row
        if isinstance(cells, list):
            rendered.append(" | ".join(_text_from_elements(cell).strip() for cell in cells))
    return "\n".join(line for line in rendered if line) or fallback


def _content_hash(text: str, blocks: list[dict]) -> str:
    payload = json.dumps({"text": text, "blocks": blocks}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _looks_like_meeting_material(text: str) -> bool:
    """Return False only for blank or clearly unrelated source material.

    The workflow is intentionally allowed to accept rough preparation notes,
    fragmented actions, agenda drafts, and other project-coordination material.
    Ambiguous nonblank content is converted with uncertainty captured under
    Missing Information/Open Questions instead of being rejected up front.
    """
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return False

    lowered = normalized.lower()
    positive_markers = (
        "会议",
        "纪要",
        "议题",
        "决定",
        "决议",
        "行动项",
        "待办",
        "负责人",
        "参与人",
        "参会",
        "时间",
        "排期",
        "截止",
        "风险",
        "阻塞",
        "准备",
        "清单",
        "同步",
        "复盘",
        "推进",
        "项目",
        "活动",
        "agenda",
        "meeting",
        "action",
        "owner",
        "todo",
        "decision",
        "blocker",
        "deadline",
        "prep",
        "checklist",
    )
    if any(marker in lowered for marker in positive_markers):
        return True

    unrelated_markers = (
        "本协议",
        "甲方",
        "乙方",
        "违约责任",
        "合同",
        "法律适用",
        "产品亮点",
        "立即购买",
        "限时优惠",
        "品牌故事",
        "营销文案",
        "参考文献",
        "作者简介",
        "abstract",
        "references",
        "purchase now",
        "terms and conditions",
        "party a",
        "party b",
    )
    if any(marker in lowered for marker in unrelated_markers):
        return False

    return True


def _extract_field(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip(" :：-")
    return "待确认"


def _extract_list_after(headings: tuple[str, ...], text: str) -> list[str]:
    lines = text.splitlines()
    collecting = False
    items = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if collecting and items:
                break
            continue
        if any(h.lower() in stripped.lower() for h in headings):
            collecting = True
            continue
        if collecting and re.match(r"^(#{1,6}\s|[一二三四五六七八九十]+[、.]|[A-Z][A-Za-z ]+:)", stripped):
            break
        if collecting:
            cleaned = re.sub(r"^[-*•\d.\s、]+", "", stripped).strip()
            if cleaned:
                items.append(cleaned)
    return items


def generate_meeting_minutes(text: str, *, source_document: str) -> tuple[bool, str]:
    if not _looks_like_meeting_material(text):
        return False, "这篇文档看起来不是会议材料，无法在不臆造信息的情况下整理成会议纪要。"

    topic = _extract_field([r"(?:会议主题|主题)[:：]\s*(.+)", r"^#\s+(.+)$"], text)
    date = _extract_field([r"(?:日期|会议时间|时间)[:：]\s*(.+)"], text)
    participants = _extract_field([r"(?:参与人|参会人|出席人)[:：]\s*(.+)"], text)
    decisions = _extract_list_after(("决定", "决议", "decisions"), text)
    actions = _extract_list_after(("行动项", "待办", "action items", "todo"), text)
    questions = _extract_list_after(("问题", "open questions"), text)
    risks = _extract_list_after(("风险", "阻塞", "risks", "blockers"), text)

    lines = [
        "# 会议纪要",
        "",
        "## 会议信息",
        f"- 会议主题: {topic}",
        f"- 日期: {date}",
        f"- 参与人: {participants}",
        f"- 来源文档: {source_document}",
        "",
        "## Meeting Summary",
        "以下内容基于源文档中的已确认信息整理；未确认信息均标记为待确认。",
        "",
        "## Decisions",
    ]
    lines.extend(f"- {item}" for item in decisions) if decisions else lines.append("- 待确认")
    lines.extend(["", "## Action Items"])
    if actions:
        for item in actions:
            owner = _extract_field([r"(?:负责人|Owner)[:：]\s*([^，,；;\n]+)"], item)
            due = _extract_field([r"(?:截止|Due Date|deadline)[:：]\s*([^，,；;\n]+)"], item)
            status = _extract_field([r"(?:状态|Status)[:：]\s*([^，,；;\n]+)"], item)
            lines.extend([
                f"- Task: {item}",
                f"  Owner: {owner}",
                f"  Due Date: {due}",
                f"  Status: {status}",
                "  Source: 源文档行动项",
            ])
    else:
        lines.extend([
            "- Task: 待确认",
            "  Owner: 待确认",
            "  Due Date: 待确认",
            "  Status: 待确认",
            "  Source: 源文档未提供明确行动项",
        ])
    lines.extend(["", "## Open Questions"])
    lines.extend(f"- {item}" for item in questions) if questions else lines.append("- 待确认")
    lines.extend(["", "## Risks / Blockers"])
    lines.extend(f"- {item}" for item in risks) if risks else lines.append("- 待确认")
    lines.extend(["", "## Missing Information"])
    missing = []
    if topic == "待确认":
        missing.append("会议主题")
    if date == "待确认":
        missing.append("日期")
    if participants == "待确认":
        missing.append("参与人")
    if not decisions:
        missing.append("明确决策")
    if not actions:
        missing.append("明确行动项")
    lines.extend(f"- {item}" for item in missing) if missing else lines.append("- 无")
    return True, "\n".join(lines).strip()


def _pending_dir() -> Path:
    return get_hermes_home() / "pending" / "feishu_docs"


def _backup_dir() -> Path:
    return Path.home() / ".hermes" / "bagel" / "backups" / "feishu_docs"


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


def _validate_actor(record: dict, user_id: str, chat_id: str) -> str | None:
    if str(record.get("user_id") or "") != str(user_id or ""):
        return "只有创建该预览的同一位飞书用户可以确认修改。"
    if str(record.get("chat_id") or "") != str(chat_id or ""):
        return "只能在创建预览的同一个聊天中确认修改。"
    if time.time() > float(record.get("expires_at") or 0):
        return "该 Change ID 已过期，请重新生成预览。"
    return None


def _read_document_snapshot(client, document_id: str) -> dict:
    meta = _document_meta(client, document_id)
    blocks = _read_blocks(client, document_id)
    text = normalize_blocks_to_text(blocks, document_id)
    return {
        "document_id": document_id,
        "title": meta.get("title") or meta.get("document_title") or "",
        "revision": meta.get("revision") or meta.get("revision_id") or meta.get("document_revision_id"),
        "raw_blocks": blocks,
        "root_block_id": _root_block_id(blocks, document_id),
        "editable_body_blocks": _editable_body_blocks(blocks, document_id),
        "normalized_text": text,
        "content_hash": _content_hash(text, blocks),
    }


def _write_backup(record: dict, snapshot: dict) -> Path:
    backup = {
        "document ID": snapshot["document_id"],
        "document_id": snapshot["document_id"],
        "title": snapshot.get("title", ""),
        "revision": snapshot.get("revision"),
        "raw blocks": snapshot.get("raw_blocks", []),
        "raw_blocks": snapshot.get("raw_blocks", []),
        "normalized text": snapshot.get("normalized_text", ""),
        "normalized_text": snapshot.get("normalized_text", ""),
        "content hash": snapshot.get("content_hash", ""),
        "content_hash": snapshot.get("content_hash", ""),
        "requesting user ID": record.get("user_id", ""),
        "requesting_user_id": record.get("user_id", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snapshot['document_id']}-{record['change_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _text_to_feishu_blocks(text: str) -> list[dict]:
    blocks = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        block_type = 2
        content = stripped
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            block_type = 2 + min(len(heading.group(1)), 6)
            content = heading.group(2)
        elif stripped.startswith("- "):
            block_type = 12
            content = stripped[2:].strip()
        blocks.append({
            "block_type": block_type,
            "text": {"elements": [{"text_run": {"content": content}}]},
        })
    return blocks


def _replace_document_body(
    client,
    document_id: str,
    root_block_id: str,
    existing_body_blocks: list[dict],
    minutes: str,
) -> None:
    root = root_block_id or document_id
    children_ids = [_block_id(block) for block in existing_body_blocks if _block_id(block)]
    if children_ids:
        code, msg, _ = _do_request(
            client,
            "DELETE",
            _DOCUMENT_CHILDREN_DELETE_URI,
            paths={"document_id": document_id, "block_id": root},
            body={"children": children_ids},
        )
        _raise_api_error("Delete original document body blocks", code, msg)

    stamp = datetime.now(timezone.utc).isoformat()
    rendered = _text_to_feishu_blocks(
        f"{minutes}\n\n---\nUpdated by Bagel\nUpdated at: {stamp}\nSource backup created: Yes"
    )
    code, msg, _ = _do_request(
        client,
        "POST",
        _DOCUMENT_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": root},
        body={"children": rendered, "index": 0},
    )
    _raise_api_error("Create approved meeting-minutes blocks", code, msg)


def _handle_feishu_doc_read(args: dict, **kwargs) -> str:
    doc_token = (args.get("doc_token") or args.get("document_id") or "").strip()
    doc_url = (args.get("doc_url") or "").strip()
    if not doc_token and doc_url:
        try:
            doc_token = parse_docx_url(doc_url)
        except ValueError as exc:
            return tool_error(str(exc))
    if not doc_token:
        return tool_error("doc_token is required")

    client = get_client()
    if client is None:
        return tool_error("Feishu client not available (not in a Feishu comment context)")

    try:
        from lark_oapi import AccessTokenType
        from lark_oapi.core.enum import HttpMethod
        from lark_oapi.core.model.base_request import BaseRequest
    except ImportError:
        return tool_error("lark_oapi not installed")

    request = (
        BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri(_RAW_CONTENT_URI)
        .token_types({AccessTokenType.TENANT})
        .paths({"document_id": doc_token})
        .build()
    )

    # Tool handlers run synchronously in a worker thread (no running event
    # loop), so call the blocking lark client directly.
    response = client.request(request)

    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to read document: code={code} msg={msg}")

    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            body = json.loads(raw.content)
            content = body.get("data", {}).get("content", "")
            return tool_result(success=True, content=content)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: try response.data
    data = getattr(response, "data", None)
    if data:
        if isinstance(data, dict):
            content = data.get("content", "")
        else:
            content = getattr(data, "content", str(data))
        return tool_result(success=True, content=content)

    return tool_error("No content returned from document API")


def _handle_prepare_meeting_minutes(args: dict, **kwargs) -> str:
    client = get_client()
    if client is None:
        return tool_error("Feishu client not available")
    try:
        document_id = parse_docx_url(args.get("doc_url", ""))
    except ValueError as exc:
        return tool_error(str(exc))
    user_id = str(args.get("user_id") or "").strip()
    chat_id = str(args.get("chat_id") or "").strip()
    if not user_id or not chat_id:
        return tool_error("user_id and chat_id are required")

    try:
        snapshot = _read_document_snapshot(client, document_id)
        ok, preview = generate_meeting_minutes(
            snapshot["normalized_text"],
            source_document=snapshot.get("title") or document_id,
        )
        if not ok:
            return tool_result(success=False, message=preview)
        change_id = f"fdc_{uuid.uuid4().hex[:12]}"
        now = time.time()
        record = {
            "change_id": change_id,
            "document_id": document_id,
            "doc_url": args.get("doc_url", ""),
            "title": snapshot.get("title", ""),
            "revision": snapshot.get("revision"),
            "content_hash": snapshot["content_hash"],
            "meeting_minutes": preview,
            "user_id": user_id,
            "chat_id": chat_id,
            "created_at": now,
            "expires_at": now + _PENDING_TTL_SECONDS,
        }
        with _pending_lock:
            _save_pending(record)
        response = (
            "以下为预览，尚未修改原文档。\n\n"
            f"{preview}\n\n"
            f"Change ID: {change_id}\n\n"
            f"如需写回原飞书文档，请发送：\n确认修改 {change_id}"
        )
        return tool_result(success=True, change_id=change_id, preview=response, expires_in_seconds=_PENDING_TTL_SECONDS)
    except PermissionError as exc:
        return tool_error(str(exc), code="permission_denied")
    except RuntimeError as exc:
        return tool_error(str(exc))


def _handle_apply_change(args: dict, **kwargs) -> str:
    change_id = str(args.get("change_id") or "").strip()
    confirmation = str(args.get("confirmation_text") or "").strip()
    user_id = str(args.get("user_id") or "").strip()
    chat_id = str(args.get("chat_id") or "").strip()
    if not change_id:
        return tool_error("当前没有有效的修改预览，请先重新生成预览。")
    if confirmation in _VAGUE_CONFIRMATIONS or confirmation != f"确认修改 {change_id}":
        return tool_error(f"请发送明确确认：确认修改 {change_id}", code="vague_confirmation")
    record = _load_pending(change_id)
    if not record:
        return tool_error("当前没有有效的修改预览，请先重新生成预览。")
    actor_error = _validate_actor(record, user_id, chat_id)
    if actor_error:
        return tool_error(actor_error)
    client = get_client()
    if client is None:
        return tool_error("Feishu client not available")

    try:
        current = _read_document_snapshot(client, record["document_id"])
        if current["content_hash"] != record.get("content_hash"):
            return tool_error("源飞书文档在预览后发生了变化，未覆盖原文档。请重新生成预览。", code="source_changed")
        backup_path = _write_backup(record, current)
        _replace_document_body(
            client,
            record["document_id"],
            current.get("root_block_id") or record["document_id"],
            current["editable_body_blocks"],
            record["meeting_minutes"],
        )
        _delete_pending(change_id)
        return tool_result(success=True, change_id=change_id, backup_path=str(backup_path), message="已写回飞书文档。")
    except PermissionError as exc:
        return tool_error(str(exc), code="permission_denied")
    except RuntimeError as exc:
        return tool_error(str(exc))


def _handle_cancel_change(args: dict, **kwargs) -> str:
    change_id = str(args.get("change_id") or "").strip()
    user_id = str(args.get("user_id") or "").strip()
    chat_id = str(args.get("chat_id") or "").strip()
    if not change_id:
        return tool_error("change_id is required")
    record = _load_pending(change_id)
    if not record:
        return tool_result(success=True, message="该 Change ID 不存在或已取消。")
    actor_error = _validate_actor(record, user_id, chat_id)
    if actor_error:
        return tool_error(actor_error)
    _delete_pending(change_id)
    return tool_result(success=True, change_id=change_id, message="已取消该待修改请求，原文档未被修改。")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_doc_read",
    toolset="feishu_doc",
    schema=FEISHU_DOC_READ_SCHEMA,
    handler=_handle_feishu_doc_read,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Read Feishu document content",
    emoji="\U0001f4c4",
)

registry.register(
    name="feishu_doc_prepare_meeting_minutes",
    toolset="feishu_doc",
    schema=FEISHU_DOC_PREPARE_SCHEMA,
    handler=_handle_prepare_meeting_minutes,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Preview Feishu document meeting minutes",
    emoji="\U0001f4dd",
)

registry.register(
    name="feishu_doc_apply_change",
    toolset="feishu_doc",
    schema=FEISHU_DOC_APPLY_SCHEMA,
    handler=_handle_apply_change,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Apply a pending Feishu document change",
    emoji="\u2705",
)

registry.register(
    name="feishu_doc_cancel_change",
    toolset="feishu_doc",
    schema=FEISHU_DOC_CANCEL_SCHEMA,
    handler=_handle_cancel_change,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Cancel a pending Feishu document change",
    emoji="\u274c",
)
