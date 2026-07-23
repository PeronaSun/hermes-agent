#!/usr/bin/env python3
"""归档会议纪要正文到飞书知识库(Wiki):这是"详实正文"唯一保留的地方——
JSON 只留轻索引(id/maison/date/title/doc_url),Bitable 只留摘要+链接,
参会人/详细内容/行动项的完整文本只写进这里的 docx 页面。

结构:固定知识空间(SPACE_ID)下,固定根节点(ROOT_NODE_TOKEN)挂 maison 目录节点,
每个 maison 目录下按"日期 · 标题"建子页面(标题带日期前缀,列表天然按时间排序)。

凭证/HTTP 基础设施(tenant token、境内直连、_get_json)全部复用 feishu_doc_reader,
不重复造轮子。永不抛异常,失败一律返回 {"ok": False, "error"}。
"""
from __future__ import annotations

from feishu_doc_reader import _BASE, _credentials, _get_json, _no_proxy, _tenant_token

# 用户在 2026-07-15 提供的知识空间;"Homepage" 是这个空间的根节点,maison 目录都挂它下面。
SPACE_ID = "7662684470754954223"
ROOT_NODE_TOKEN = "UqMVwN4RaiZ0fjkcG3ycV4e0nJf"


def _list_children(token: str, parent_node_token: str) -> list[dict]:
    items: list[dict] = []
    page_token = ""
    while True:
        url = (f"{_BASE}/wiki/v2/spaces/{SPACE_ID}/nodes"
               f"?parent_node_token={parent_node_token}&page_size=50")
        if page_token:
            url += f"&page_token={page_token}"
        r = _get_json(url, token=token)
        if r.get("code") != 0:
            raise RuntimeError(f"list wiki nodes failed: {r.get('msg')}")
        data = r.get("data") or {}
        items.extend(data.get("items") or [])
        if not data.get("has_more"):
            return items
        page_token = data.get("page_token") or ""


def _create_node(token: str, parent_node_token: str, title: str) -> dict:
    body = {"obj_type": "docx", "node_type": "origin",
            "parent_node_token": parent_node_token, "title": title}
    r = _get_json(f"{_BASE}/wiki/v2/spaces/{SPACE_ID}/nodes", token=token, payload=body)
    if r.get("code") != 0:
        raise RuntimeError(f"create wiki node failed: {r.get('msg')}")
    return r["data"]["node"]


def _ensure_maison_node(token: str, maison_name: str) -> dict:
    """maison 目录节点:标题精确匹配已存在就复用,否则新建。"""
    for node in _list_children(token, ROOT_NODE_TOKEN):
        if node.get("title") == maison_name:
            return node
    return _create_node(token, ROOT_NODE_TOKEN, maison_name)


def _text_block(text: str) -> dict:
    return {"block_type": 2,
            "text": {"elements": [{"text_run": {"content": text, "text_element_style": {}}}]}}


def _child_block_count(token: str, obj_token: str) -> int:
    """根 block 的直接子 block 数(用于算追加时的 index)。第一项是根 block 自己,不算。"""
    r = _get_json(f"{_BASE}/docx/v1/documents/{obj_token}/blocks?page_size=50", token=token)
    if r.get("code") != 0:
        raise RuntimeError(f"list blocks failed: {r.get('msg')}")
    return max(len(r.get("data", {}).get("items") or []) - 1, 0)


def _write_blocks(token: str, obj_token: str, blocks: list[dict], *, index: int) -> None:
    if not blocks:
        return
    body = {"index": index, "children": blocks}
    r = _get_json(f"{_BASE}/docx/v1/documents/{obj_token}/blocks/{obj_token}/children",
                  token=token, payload=body)
    if r.get("code") != 0:
        raise RuntimeError(f"write blocks failed: {r.get('msg')}")


def render_action_item_lines(items) -> list[str]:
    lines = []
    for it in items or []:
        owner = (it.get("owner") or "").strip()
        task = (it.get("task") or "").strip()
        due = (it.get("due") or "").strip()
        prefix = f"{owner}: " if owner else ""
        suffix = f" ({due})" if due else ""
        lines.append(f"- {prefix}{task}{suffix}")
    return lines


def _build_minute_blocks(*, maison_name: str, date: str, participants, summary: str,
                         content: str, action_items, project_names) -> list[dict]:
    blocks = []
    header = f"Maison: {maison_name} | Date: {date}"
    if participants:
        header += " | 参会人: " + "、".join(participants)
    blocks.append(_text_block(header))
    if project_names:
        blocks.append(_text_block("关联项目: " + "、".join(project_names)))
    if summary:
        blocks.append(_text_block("摘要: " + summary))
    for para in (content or "").split("\n"):
        if para.strip():
            blocks.append(_text_block(para))
    action_lines = render_action_item_lines(action_items)
    if action_lines:
        blocks.append(_text_block("行动项:"))
        blocks.extend(_text_block(line) for line in action_lines)
    return blocks


def archive_minute(*, maison_name: str, date: str, title: str, participants=None,
                   summary: str = "", content: str = "", action_items=None,
                   project_names=None) -> dict:
    """建/复用 maison 目录节点 -> 建这条纪要的子页面(标题带日期前缀,天然按时间排序)
    -> 写正文。永不抛异常。成功返回 {"ok": True, "node_token", "url"}。"""
    creds = _credentials()
    if creds is None:
        return {"ok": False, "error": "feishu 未配置(需 FEISHU_APP_ID/FEISHU_APP_SECRET)"}
    try:
        with _no_proxy():
            token = _tenant_token(creds)
            maison_node = _ensure_maison_node(token, maison_name)
            page_title = f"{date} · {title}" if title else date
            minute_node = _create_node(token, maison_node["node_token"], page_title)
            blocks = _build_minute_blocks(
                maison_name=maison_name, date=date, participants=participants,
                summary=summary, content=content, action_items=action_items,
                project_names=project_names)
            _write_blocks(token, minute_node["obj_token"], blocks, index=0)
        return {"ok": True, "node_token": minute_node["node_token"], "url": minute_node["url"]}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # 网络等意外情况也不抛
        return {"ok": False, "error": f"unexpected: {e}"}


def append_update_note(*, node_token: str, note: str, when: str) -> dict:
    """update_minute 用:在已有页面末尾追加一段更新记录,不整篇重写、不删旧内容。"""
    creds = _credentials()
    if creds is None:
        return {"ok": False, "error": "feishu 未配置(需 FEISHU_APP_ID/FEISHU_APP_SECRET)"}
    try:
        with _no_proxy():
            token = _tenant_token(creds)
            r = _get_json(f"{_BASE}/wiki/v2/spaces/get_node?token={node_token}", token=token)
            if r.get("code") != 0:
                raise RuntimeError(f"resolve node failed: {r.get('msg')}")
            obj_token = r["data"]["node"]["obj_token"]
            idx = _child_block_count(token, obj_token)
            _write_blocks(token, obj_token, [_text_block(f"[更新 {when}] {note}")], index=idx)
        return {"ok": True}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"unexpected: {e}"}
