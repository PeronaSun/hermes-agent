#!/usr/bin/env python3
"""单条直写:meeting_minutes_tool.add_minute/update_minute/delete_minute -> 飞书 Base 的
Meeting Minutes 表(tbl7k0o9qly9KHAr)。

2026-07-15 改版:详实正文/参会人/行动项不再在本地 JSON 里长期保留(只在 wiki 归档,
见 meeting_minutes_wiki.py),所以这里从"读 JSON 全量 diff-resync"改成"每次写操作时
拿调用方手上现成的完整数据直写一行"——不再需要批量 diff/deletion guard 那套机制,
因为每次只动一行。

2026-07-17 改版:Maison/Projects 两列从纯文本改成单向关联(SingleLink),分别指向
FACT 表同款的 DIM_Maison / DIM_AI Project(Group Project),不新增字段、不碰 FACT 表。
build_minute_fields 仍是纯函数,只吐"名字视图"(Maison 单个名字、Projects 名字列表);
名字 -> 关联记录 id 的解析放在网络层(_resolve_link_fields),跟 feishu_bitable.py 的
desired_fact_rows(纯函数,名字视图)vs _create_payload/_update_payload(网络层,解析
record id)是同一个分层套路,复用它的 DIM_TABLES/MAISON_NAME_MAP,不重复定义。

同日追加:Participants(纯文本参会人列表)改成 Owner,单向关联到 DIM_Owner(FACT 表
Owner 用的同一张词表),记的是"谁创建了这条纪要"(即调用方的 actor),不再记参会人——
参会人信息仍然完整保留在 wiki 正文里(见 meeting_minutes_wiki.py),只是不在 Bitable
这行单独开一列。Owner 解析找不到时会自动在 DIM_Owner 建一条新记录(跟 Maison/Projects
"找不到就跳过"不同)——actor 来自真实登录用户的 preferred_name,可靠度高,不像项目/
Maison 名字那样容易手滑打错,自动建词表条目风险低。

2026-07-17 再追加:用户在飞书 UI 里手动把主字段(卡片显示用)从 Minute ID 换成了
标题内容——不是简单改名,是把两个字段的名字+内容整体互换:原来的主字段 Minute ID
现在叫 Meeting Title(内容是标题文本),原来的 Title 字段现在叫 Meeting ID(内容是
原来的 id 字符串,如 "mm_20260714_chaumet")。行识别的机制完全没变(还是靠精确匹配
一个装 id 字符串的字段),只是这个字段现在叫 Meeting ID 不叫 Minute ID 了。

纯函数(render_action_items/build_minute_fields)无依赖;网络层懒加载主表
feishu_bitable 的基础设施(FeishuBitableClient/_config/_cell_text/DIM_TABLES/
MAISON_NAME_MAP),不改那个文件。
"""
from __future__ import annotations

import logging

log = logging.getLogger("meeting_minutes_bitable")

MINUTES_TABLE = "tbl7k0o9qly9KHAr"


def render_action_items(items) -> str:
    lines = []
    for it in items or []:
        owner = (it.get("owner") or "").strip()
        task = (it.get("task") or "").strip()
        due = (it.get("due") or "").strip()
        prefix = f"{owner}: " if owner else ""
        suffix = f" ({due})" if due else ""
        lines.append(f"- {prefix}{task}{suffix}")
    return "\n".join(lines)


def build_minute_fields(*, minute_id: str, maison_name: str, date: str, title: str,
                        owner: str = "", summary: str = "", project_names=None,
                        content: str = "", action_items=None, doc_url: str = "") -> dict:
    """纯函数:拼出要写进 Bitable 一行的字段 dict(名字视图)。Maison/Owner 是单个名字、
    Projects 是名字列表——都还没解析成关联记录 id,由网络层的 _resolve_link_fields
    在实际写入前解析。doc_url 为空时 Link 列留空字符串(URL 类型字段清空用空字符串,
    不是 dict)。"""
    fields = {
        "Meeting ID": minute_id,
        "Maison": maison_name,
        "Date": date,
        "Meeting Title": title,
        "Owner": owner,
        "Summary": summary,
        "Projects": list(project_names or []),
        "Content": content,
        "Action Items": render_action_items(action_items),
    }
    fields["Link"] = {"link": doc_url, "text": "查看纪要"} if doc_url else ""
    return fields


def _client(client, cfg):
    from feishu_bitable import FeishuBitableClient, _config
    if cfg is None:
        cfg = _config()
    if cfg is None:
        return None, {"ok": False, "error": "feishu 未配置 (需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN)"}
    if client is None:
        client = FeishuBitableClient(cfg)
    return client, None


def _find_record_id(client, minute_id: str) -> str | None:
    from feishu_bitable import _cell_text
    for rec in client.list_all(MINUTES_TABLE):
        if _cell_text((rec.fields or {}).get("Meeting ID")) == minute_id:
            return rec.record_id
    return None


def _lookup_dim_id(client, table_id: str, name_field: str, target_name: str) -> str | None:
    """在一张 DIM 表里按名字找第一条匹配的记录 id;找不到返回 None。"""
    from feishu_bitable import _cell_text
    for rec in client.list_all(table_id):
        if _cell_text((rec.fields or {}).get(name_field)) == target_name:
            return rec.record_id
    return None


def _lookup_or_create_dim_id(client, table_id: str, name_field: str, target_name: str) -> str:
    """跟 _lookup_dim_id 一样按名字找,找不到就建一条新的 DIM 记录并返回其 id。
    只给 Owner 用——actor 来自真实登录用户身份,自动建词表条目风险低,跟 Maison/
    Projects(自由文本、容易手滑)的"找不到就跳过"策略不同。"""
    rid = _lookup_dim_id(client, table_id, name_field, target_name)
    if rid is not None:
        return rid
    created = client.batch_create(table_id, [{name_field: target_name}])
    return created[0].record_id


def _resolve_link_fields(client, fields: dict) -> dict:
    """把 build_minute_fields 吐出的名字视图(Maison/Owner 单名字、Projects 名字列表)
    解析成关联记录 id;只处理 fields 里实际出现的键(update_minute_row 有时只传
    Title/Date,不能因为解析而凭空把 Maison/Owner/Projects 清空)。Maison/Projects
    在 DIM 里找不到的名字跳过、记 warning,不阻断整行写入(项目/Maison 名字理论上都
    已经在 FACT 正向同步时建过 DIM 记录,这里不重复"缺失自动建"那套复杂逻辑);Owner
    找不到则自动建(见 _lookup_or_create_dim_id)。"""
    from feishu_bitable import DIM_TABLES, MAISON_NAME_MAP
    out = dict(fields)
    if "Maison" in out:
        maison_tid, maison_name_field = DIM_TABLES["Maison"]
        base_name = MAISON_NAME_MAP.get(out["Maison"], out["Maison"])
        rid = _lookup_dim_id(client, maison_tid, maison_name_field, base_name) if base_name else None
        if base_name and rid is None:
            log.warning("meeting minute Maison not found in DIM_Maison: %r", base_name)
        out["Maison"] = [rid] if rid else []
    if "Projects" in out:
        ap_tid, ap_name_field = DIM_TABLES["Group Project"]
        ids = []
        for name in out["Projects"]:
            rid = _lookup_dim_id(client, ap_tid, ap_name_field, name)
            if rid is None:
                log.warning("meeting minute Project not found in DIM_AI Project: %r", name)
            else:
                ids.append(rid)
        out["Projects"] = ids
    if "Owner" in out:
        owner_tid, owner_name_field = DIM_TABLES["Owner"]
        out["Owner"] = [_lookup_or_create_dim_id(client, owner_tid, owner_name_field, out["Owner"])] \
            if out["Owner"] else []
    return out


def create_minute_row(fields: dict, *, client=None, cfg=None) -> dict:
    """Best-effort 单行创建。永不抛异常。"""
    client, err = _client(client, cfg)
    if err:
        return err
    try:
        client.batch_create(MINUTES_TABLE, [_resolve_link_fields(client, fields)])
        return {"ok": True}
    except Exception as e:
        log.warning("minute row create failed: %s", e)
        return {"ok": False, "error": str(e)}


def update_minute_row(minute_id: str, fields: dict, *, client=None, cfg=None) -> dict:
    """Best-effort 单行更新(按 Minute ID 找行);找不到就当创建。永不抛异常。"""
    client, err = _client(client, cfg)
    if err:
        return err
    try:
        resolved = _resolve_link_fields(client, fields)
        rid = _find_record_id(client, minute_id)
        if rid is None:
            client.batch_create(MINUTES_TABLE, [resolved])
        else:
            client.batch_update(MINUTES_TABLE, [(rid, resolved)])
        return {"ok": True}
    except Exception as e:
        log.warning("minute row update failed: %s", e)
        return {"ok": False, "error": str(e)}


def delete_minute_row(minute_id: str, *, client=None, cfg=None) -> dict:
    """Best-effort 单行删除;找不到就当已删除(noop)。永不抛异常。"""
    client, err = _client(client, cfg)
    if err:
        return err
    try:
        rid = _find_record_id(client, minute_id)
        if rid is not None:
            client.batch_delete(MINUTES_TABLE, [rid])
        return {"ok": True}
    except Exception as e:
        log.warning("minute row delete failed: %s", e)
        return {"ok": False, "error": str(e)}
