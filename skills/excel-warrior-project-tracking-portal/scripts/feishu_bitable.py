#!/usr/bin/env python3
"""One-way mirror: portal JSON -> Feishu Bitable star schema (FACT + DIMs).

Pure desired-state building + diff, a thin lark-oapi Bitable v1 client, and a
best-effort sync orchestrator. Imported lazily by portal_data_tool.dispatch
after mirror-affecting writes. lark_oapi is imported lazily inside the client
so pure functions and most tests need neither the SDK nor the network.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("feishu_bitable")

# --- Base 固有结构(非机密) ---
# 2026-07-09 迁移正式版 Base(https://lvmh-digital.feishu.cn/base/QVx4bZtfGaDh3OszJBhcNhiinHf)。
# 旧测试 Base 的 table_id 留作记录,不再使用:
#   FACT_TABLE = "tblacI2fJ1ZbVf6I"
#   Maison="tblSKfzLE9uciUW3" Owner="tbljEVC31aBpOgGY"
#   AI Project="tblIof2SnozwVXHR" Project Type="tblSdHSggWYoi2To"
#   AI_DOMAIN_TABLE = "tblhn92WLrDh4yoy"
FACT_TABLE = "tblxDlGc8IJa4Q4p"
DIM_TABLES = {          # FACT 关联列 -> (DIM 表 id, DIM 名字字段)
    "Maison": ("tblKpfd7PI90CLlt", "Maison"),
    "Owner": ("tblF4cZs4lfHgvzf", "Owner"),
    "Group Project": ("tblNuEQh61TvMZnP", "AI Project"),
    "Project Type": ("tblSZfUA8TpwpX0r", "Project Type"),
}
LINK_FIELDS = tuple(DIM_TABLES)

# JSON maison 名 -> Base DIM_Maison 名(仅两处命名出入;Marc Jocobs 无对应,原样透传)
MAISON_NAME_MAP = {          # JSON maison 名 -> 新 Base maison 名(仅列不同名者)
    "Perfume Christine Dior": "Parfume Christine Dior",   # 新 Base 拼写带 a(Base 为准)
}

# diff 与 update 覆盖的全部列;公式/Lookup 列(Top 12/Date/AI Domain)绝不出现
_COMPARED_FIELDS = ("Maison Project", "Maison", "Status", "Quater", "Year",
                    "Remark", "Owner", "Group Project", "Project Type", "Maison Top 3")

# Checkbox 类型列:desired_fact_rows/_COMPARED_FIELDS 的文本视图里存 "True"/"False"
# (跟 _cell_text 读回真实 Checkbox 值时的 str(bool) 输出对齐,方便直接文本比较);
# 真正要发给 API 的是原生 bool,只在这两个 payload 构造函数里转换一次。
CHECKBOX_FIELDS = ("Maison Top 3",)

# DIM_Maison 的静态属性列(FACT 不带,只在 DIM 行上维护):JSON maisons.json 字段 -> Base 列名
MAISON_ATTR_FIELDS = {"division": "Division", "ai_champion": "AI Champion",
                      "key_account": "Dimension Owner"}

# 域名维度:portal projects.json 的 domain 值 -> 新 Base DIM_AI Domain 规范名。
# 只列有映射者;空 domain / 未知值不写(不清空,与 desired_maison_attrs 口径一致)。
DOMAIN_TO_AI_DOMAIN = {
    "Client development": "AI for Client Development",
    "Omni retail": "AI for Omni-Retail",
    "Marketing": "AI for Marketing & Media",
    "Operation": "AI for Operation",
    "foundation": "Foundation",
}
AI_DOMAIN_TABLE = "tblKM2ZzqYFQmVGt"   # DIM_AI Domain(域名维度表)
# FACT 上的 AI Domain 是 Lookup(只读),真值在 DIM_AI Project.AI Domain(关联 DIM_AI Domain)。
# DIM_AI Domain 的名字列与 DIM_AI Project 的关联列同名,共用此常量。
AI_DOMAIN_FIELD = "AI Domain"


def status_to_fact(status, status_config):
    """portal 单字段 status -> (FACT Status 单选, Quater 单选或 None)。

    status_config 自带 category/quarter,查表即映射;未知 status 原样透传。
    """
    row = next((s for s in status_config if s.get("status") == status), None)
    if row is None:
        return (status, None)
    cat = (row.get("category") or "").capitalize()
    return (cat or status, row.get("quarter"))


def desired_fact_rows(projects, maisons, records, status_config):
    """JSON -> 期望的 FACT 行:{(project_name, base_maison_name): fields}。

    关联列的值此时还是名字(由 sync 层解析为 record_id);空值键不输出。
    """
    proj_by_id = {p.get("id"): p for p in projects}
    maison_by_id = {m.get("id"): m for m in maisons}
    out = {}
    for r in records:
        p = proj_by_id.get(r.get("project_id"))
        m = maison_by_id.get(r.get("maison_id"))
        if p is None or m is None:
            continue
        pname = p.get("name", "")
        base_maison = MAISON_NAME_MAP.get(m.get("name", ""), m.get("name", ""))
        st, quarter = status_to_fact(r.get("status") or "", status_config)
        fields = {"Maison Project": r.get("maison_project") or pname, "Maison": base_maison}
        if st:
            fields["Status"] = st
        if quarter:
            fields["Quater"] = quarter
        if r.get("year"):
            fields["Year"] = r["year"]
        if r.get("remark"):
            fields["Remark"] = r["remark"]
        # owner 记录级优先(per-maison),没有则回落项目级默认
        owner = r.get("owner") or p.get("owner")
        if owner:
            fields["Owner"] = owner
        # Group Project 列 = 项目名(恒等于 name,项目身份的唯一来源)
        if p.get("name"):
            fields["Group Project"] = p["name"]
        if r.get("project_type"):
            fields["Project Type"] = r["project_type"]
        # Checkbox 天然有默认态(未标 = False),不是"缺省不出现"那类字段,无条件写
        fields["Maison Top 3"] = "True" if r.get("maison_top3") else "False"
        out[(pname, base_maison)] = fields
    return out


def desired_maison_attrs(maisons):
    """JSON maisons -> {base_maison_name: {Division/AI Champion/Dimension Owner 已设的}}。

    名字按 MAISON_NAME_MAP 转换,与 desired_fact_rows 的 Maison 链接值口径一致;
    未设的属性不输出(空值键不代表"清空",由调用方与现状对比后再决定)。
    """
    out = {}
    for m in maisons:
        base_name = MAISON_NAME_MAP.get(m.get("name", ""), m.get("name", ""))
        fields = {base: m[json_key] for json_key, base in MAISON_ATTR_FIELDS.items()
                  if m.get(json_key)}
        out[base_name] = fields
    return out


def desired_ai_project_domains(projects):
    """{Base AI Project 名: Base AI Domain 规范名}。

    AI Project 名 = 项目名(与 desired_fact_rows 的 "Group Project" 列口径一致)。
    仅含 domain 能经 DOMAIN_TO_AI_DOMAIN 映射到规范名的项目;空 domain / 未知值不出现
    (空值不代表清空——不会去动 Base 上已有的 AI Domain)。
    """
    out = {}
    for p in projects:
        name = p.get("name")
        label = DOMAIN_TO_AI_DOMAIN.get((p.get("domain") or "").strip())
        if name and label:
            out[name] = label
    return out


def _cell_text(v):
    """Bitable 读回的字段值 -> 文本。关联/富文本是 [{text,...}];其余原样字符串化。"""
    if isinstance(v, list):
        return "|".join(str((x.get("text") if isinstance(x, dict) else x) or "") for x in v)
    return "" if v is None else str(v)


def diff_rows(desired, current):
    """期望 vs 现状(均为文本视图)-> (creates, updates, deletes)。

    creates: list[fields];updates: list[(record_id, fields)];deletes: list[record_id]。
    比较口径:逐列比文本,两侧均空("":None:缺键)视为相等。
    """
    creates, updates, deletes = [], [], []
    for key, want in desired.items():
        if key not in current:
            creates.append(want)
            continue
        record_id, have = current[key]
        if any((want.get(f) or "") != (have.get(f) or "") for f in _COMPARED_FIELDS):
            updates.append((record_id, want))
    for key, (record_id, _have) in current.items():
        if key not in desired:
            deletes.append(record_id)
    return creates, updates, deletes


def deletion_guard_tripped(n_deletes, n_current):
    """防 JSON 损坏/误读触发大清空:要删的行数超过 max(5, 现状 50%) 即中止。"""
    return n_deletes > max(5, n_current * 0.5)


_ENV_KEYS = {
    "app_id": "FEISHU_APP_ID",
    "app_secret": "FEISHU_APP_SECRET",
    "app_token": "FEISHU_BITABLE_APP_TOKEN",
}

# secret 兜底文件:execute_code sandbox 不继承 gateway plist 注入的 env,
# 所以 os.environ 里可能没有;从这个 git 外的 0600 文件补齐(与 plist 同源)。
_ENV_FILE = Path.home() / ".hermes" / ".env"


def _read_env_file(path: Path | None = None) -> dict:
    """极简 dotenv 解析:KEY=VALUE 逐行,忽略空行/注释,去掉两侧引号。
    文件不存在/读不了返回 {}。仅作 os.environ 的兜底,不覆盖已有 env。
    path 默认在调用时解析模块级 _ENV_FILE(便于测试 monkeypatch)。"""
    if path is None:
        path = _ENV_FILE
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def _config():
    """Feishu settings: env 优先,再用 ~/.hermes/.env 兜底(sandbox 不继承注入 env)。
    三项任一仍缺则返回 None。"""
    file_env = None
    cfg = {}
    for k, env in _ENV_KEYS.items():
        val = os.environ.get(env)
        if not val:
            if file_env is None:
                file_env = _read_env_file()
            val = file_env.get(env)
        cfg[k] = val
    if not all(cfg.values()):
        return None
    return cfg


class FeishuBitableClient:
    """Thin wrapper over lark-oapi typed Bitable v1. lark_oapi imported lazily
    so pure functions and mocked tests need neither the SDK nor the network."""

    _CHUNK = 500

    def __init__(self, cfg):
        self._cfg = cfg
        import lark_oapi as lark
        self._client = (lark.Client.builder()
                        .app_id(cfg["app_id"])
                        .app_secret(cfg["app_secret"])
                        .log_level(lark.LogLevel.WARNING)
                        .build())

    def _check(self, resp, what):
        if not resp.success():
            raise RuntimeError(f"feishu {what} failed: code={resp.code} msg={resp.msg}")
        return resp

    def list_all(self, table_id, automatic_fields=False):
        from lark_oapi.api.bitable.v1 import ListAppTableRecordRequest
        items, page = [], None
        while True:
            b = (ListAppTableRecordRequest.builder()
                 .app_token(self._cfg["app_token"]).table_id(table_id).page_size(500))
            if automatic_fields:
                b = b.automatic_fields(True)
            if page:
                b = b.page_token(page)
            resp = self._check(self._client.bitable.v1.app_table_record.list(b.build()),
                               f"list {table_id}")
            items.extend(resp.data.items or [])
            if not resp.data.has_more:
                return items
            page = resp.data.page_token

    def batch_create(self, table_id, fields_list):
        from lark_oapi.api.bitable.v1 import (AppTableRecord,
                                              BatchCreateAppTableRecordRequest,
                                              BatchCreateAppTableRecordRequestBody)
        created = []
        for i in range(0, len(fields_list), self._CHUNK):
            chunk = [AppTableRecord.builder().fields(f).build()
                     for f in fields_list[i:i + self._CHUNK]]
            req = (BatchCreateAppTableRecordRequest.builder()
                   .app_token(self._cfg["app_token"]).table_id(table_id)
                   .request_body(BatchCreateAppTableRecordRequestBody.builder()
                                 .records(chunk).build())
                   .build())
            resp = self._check(self._client.bitable.v1.app_table_record.batch_create(req),
                               f"batch_create {table_id}")
            created.extend(resp.data.records or [])
        return created

    def batch_update(self, table_id, updates):
        from lark_oapi.api.bitable.v1 import (AppTableRecord,
                                              BatchUpdateAppTableRecordRequest,
                                              BatchUpdateAppTableRecordRequestBody)
        for i in range(0, len(updates), self._CHUNK):
            chunk = [AppTableRecord.builder().record_id(rid).fields(f).build()
                     for rid, f in updates[i:i + self._CHUNK]]
            req = (BatchUpdateAppTableRecordRequest.builder()
                   .app_token(self._cfg["app_token"]).table_id(table_id)
                   .request_body(BatchUpdateAppTableRecordRequestBody.builder()
                                 .records(chunk).build())
                   .build())
            self._check(self._client.bitable.v1.app_table_record.batch_update(req),
                        f"batch_update {table_id}")

    def batch_delete(self, table_id, record_ids):
        from lark_oapi.api.bitable.v1 import (BatchDeleteAppTableRecordRequest,
                                              BatchDeleteAppTableRecordRequestBody)
        for i in range(0, len(record_ids), self._CHUNK):
            req = (BatchDeleteAppTableRecordRequest.builder()
                   .app_token(self._cfg["app_token"]).table_id(table_id)
                   .request_body(BatchDeleteAppTableRecordRequestBody.builder()
                                 .records(list(record_ids[i:i + self._CHUNK])).build())
                   .build())
            self._check(self._client.bitable.v1.app_table_record.batch_delete(req),
                        f"batch_delete {table_id}")


def _read_json(portal_root, name):
    with (Path(portal_root) / "data" / name).open(encoding="utf-8") as f:
        return json.load(f)


_SYNC_STATE_FILE = ".feishu_sync_state.json"


def _read_sync_state(portal_root):
    path = Path(portal_root) / "data" / _SYNC_STATE_FILE
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_sync_state(portal_root, state):
    path = Path(portal_root) / "data" / _SYNC_STATE_FILE
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# 自建应用通过 API 写入 Base 时,飞书记录的 last_modified_by 是这个固定的机器人身份
# (显示名"The Turk")。2026-07-09 实测确认:Base 的 app-level revision 不会因为在飞书
# UI 里直接编辑一行记录而变化(只反映 schema/结构层面的改动),之前用它做"保底"检测
# 从未真正起过作用——换成按行比对 last_modified_by 才是准的。若这个自建应用的凭证被
# 重新创建,这个 id 需要跟着更新(可查一次 sync_to_feishu 自己写完的任意一行确认)。
SYNC_BOT_USER_ID = "ou_7abbdd5d715a621f5a945a92e73a59d5"


def _read_changelog(portal_root):
    try:
        return _read_json(portal_root, "changelog.json")
    except FileNotFoundError:
        return []


# 只有这三个"记录级"动作才会缩小 FACT 推送范围;任何别的改动(改名/建删项目等实体级
# 操作,或者不认识的新 action)一律退回全量——宁可多推,不可漏推。
_RECORD_SCOPED_ACTIONS = {"update_status", "update_remark", "update_record_meta"}


def _scoped_fact_keys(log, last_synced_count, projects, maisons):
    """changelog 全量 + 上次同步记的条数 -> 这次要推送的 FACT 行键集合(纯函数)。

    返回 None 表示不缩小范围,照常全量推:首次同步(没有基准)、changelog 被归档/清空
    导致条数比记录的还少(历史对不上,没法安全判断)、或者新增条目里出现记录级三个
    动作之外的任何改动(改名/建删项目这类影响一整个实体的操作,不是"这一行"能概括的,
    直接退回全量更安全)。"""
    if last_synced_count is None or last_synced_count > len(log):
        return None
    name_by_pid = {p.get("id"): p.get("name") for p in projects}
    name_by_mid = {m.get("id"): MAISON_NAME_MAP.get(m.get("name", ""), m.get("name", ""))
                  for m in maisons}
    keys = set()
    for e in log[last_synced_count:]:
        if e.get("action") not in _RECORD_SCOPED_ACTIONS:
            return None
        pname = name_by_pid.get(e.get("project_id"))
        mname = name_by_mid.get(e.get("maison_id"))
        if pname is None or mname is None:
            return None
        keys.add((pname, mname))
    return keys


# update 时对缺省列写入的清空值:文本/单选 -> "",关联 -> []
# 注意:单选字段传 None 不会清空——lark_oapi 序列化时会丢弃 None 值键,PATCH 请求里
# 该字段直接消失,飞书侧保留旧值不变;必须显式传 "" 才会真正清空(实测验证过)。
_CLEAR_VALUES = {"Maison Project": "", "Remark": "", "Status": "", "Quater": "", "Year": "",
                 "Maison": [], "Owner": [], "Group Project": [], "Project Type": [],
                 "Maison Top 3": "False"}


def _create_payload(fields, dim_maps):
    """期望行(名字视图)-> create 用 API payload:只含已设键,关联名解析为 [record_id],
    Checkbox 文本("True"/"False")解析为原生 bool。"""
    out = {}
    for k, v in fields.items():
        if k in LINK_FIELDS:
            rid = dim_maps[k].get(v)
            if rid:
                out[k] = [rid]
        elif k in CHECKBOX_FIELDS:
            out[k] = v == "True"
        else:
            out[k] = v
    return out


def _update_payload(fields, dim_maps):
    """期望行 -> update 用 API payload:覆盖全部比较列,缺省列写清空值,防旧值残留;
    Checkbox 文本("True"/"False"/缺省清空值)解析为原生 bool。"""
    out = {}
    for k in _COMPARED_FIELDS:
        v = fields.get(k)
        if k in LINK_FIELDS:
            rid = dim_maps[k].get(v) if v else None
            out[k] = [rid] if rid else []
        elif k in CHECKBOX_FIELDS:
            text = v if v is not None else _CLEAR_VALUES[k]
            out[k] = text == "True"
        else:
            out[k] = v if v is not None else _CLEAR_VALUES[k]
    return out


def sync_to_feishu(portal_root):
    """Best-effort one-way mirror into the Bitable FACT table. Never raises."""
    cfg = _config()
    if cfg is None:
        return {"ok": False,
                "error": "feishu 未配置 (需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN)"}
    progress: dict = {}   # 部分失败时已完成的批次计数,随错误返回(spec §5)
    try:
        # changelog 必须先于业务 JSON 读:sync 全程不加锁,若在两次读之间插入一次并发
        # 写,changelog 计数一旦"数到"某条 entry,后读的业务 JSON 就保证已经落盘那条
        # entry 对应的数据(写者在同一把锁内先存数据、后追加 changelog)。反过来(先读
        # 数据后读 changelog)会出现"计数已包含某次改动,但数据快照还没包含"——那条
        # 改动会被误标记为已同步,下次范围收窄直接漏推,且不会自愈。
        changelog = _read_changelog(portal_root)
        projects = _read_json(portal_root, "projects.json")
        maisons = _read_json(portal_root, "maisons.json")
        records = _read_json(portal_root, "project_maison_status.json")
        status_config = _read_json(portal_root, "status_config.json")
        desired = desired_fact_rows(projects, maisons, records, status_config)
        client = FeishuBitableClient(cfg)

        sync_state = _read_sync_state(portal_root)

        # 0) 缩小 FACT 推送范围(方案 B):只推 changelog 里"上次同步之后"记录级改动
        #    涉及的行,不去动同样跟期望态不一致、但这次没人碰过的其它行——避免顺手覆盖
        #    别人手动改的数据。DIM 对齐/Maison 属性/AI Domain 关联这几步不缩小,维持全量。
        scope_keys = _scoped_fact_keys(
            changelog, sync_state.get("last_synced_changelog_count"), projects, maisons)
        # 上次同步里因为「行被人手动改过」被跳过的键,即便这次的 changelog 增量范围
        # 已经把它们排除在外,也要并回本次 scope——否则一旦某行被跳过,它对应的
        # changelog 条目照样计入 last_synced_changelog_count,从此这行永远不会再被
        # diff/推送,manual_edits_skipped 也不会再提起它,问题被永久悄悄吞掉。持续
        # 并入 scope,直到该行不再是人工最后修改(冲突解除)才会跌出 manual_edits_skipped。
        pending_conflicts = {tuple(k) for k in sync_state.get("pending_manual_conflict_keys", [])}
        if scope_keys is not None and pending_conflicts:
            scope_keys = scope_keys | pending_conflicts

        # 1) DIM 对齐:名字 -> record_id;期望里出现而 DIM 缺失的值先建
        #    Maison 额外维护 Division/AI Champion/Dimension Owner 三个静态属性列(JSON 已有数据但
        #    FACT 不带),新建时随名字一起写入,已存在的行如果属性漂移(比如 AI Champion 换人)则补 update。
        maison_attrs = desired_maison_attrs(maisons)
        dim_maps = {}
        maison_attr_updates = []
        for field, (tid, name_field) in DIM_TABLES.items():
            existing = {}
            existing_records = client.list_all(tid)
            for rec in existing_records:
                nm = _cell_text((rec.fields or {}).get(name_field))
                if nm:
                    existing.setdefault(nm, rec.record_id)
            wanted = {f[field] for f in desired.values() if f.get(field)}
            missing = sorted(wanted - set(existing))
            if missing:
                create_fields = []
                for nm in missing:
                    payload = {name_field: nm}
                    if field == "Maison":
                        payload.update(maison_attrs.get(nm, {}))
                    create_fields.append(payload)
                for rec in client.batch_create(tid, create_fields):
                    existing[_cell_text((rec.fields or {}).get(name_field))] = rec.record_id
            dim_maps[field] = existing

            if field == "Maison":
                attr_cols = tuple(MAISON_ATTR_FIELDS.values())
                for rec in existing_records:
                    nm = _cell_text((rec.fields or {}).get(name_field))
                    if not nm or nm not in wanted:
                        continue
                    want = maison_attrs.get(nm, {})
                    have = {c: _cell_text((rec.fields or {}).get(c)) for c in attr_cols}
                    if any((want.get(c) or "") != (have.get(c) or "") for c in attr_cols):
                        maison_attr_updates.append(
                            (rec.record_id, {c: want.get(c, "") for c in attr_cols}))

        if maison_attr_updates:
            client.batch_update(DIM_TABLES["Maison"][0], maison_attr_updates)
            progress["maison_dim_updated"] = len(maison_attr_updates)

        # 1b) DIM_AI Project 的 AI Domain 关联列:FACT 上 AI Domain 是 Lookup(只读),
        #     真值在 DIM_AI Project 上,来自 projects.json 的 domain 经 DOMAIN_TO_AI_DOMAIN
        #     映射到 DIM_AI Domain 规范名。空/未知 domain 不写(不清空 Base 已有值)。
        want_dom = desired_ai_project_domains(projects)
        if want_dom:
            ap_tid = DIM_TABLES["Group Project"][0]
            dom_ids = {}
            for rec in client.list_all(AI_DOMAIN_TABLE):
                nm = _cell_text((rec.fields or {}).get(AI_DOMAIN_FIELD))
                if nm:
                    dom_ids.setdefault(nm, rec.record_id)
            missing_domains = sorted(set(want_dom.values()) - set(dom_ids))
            if missing_domains:
                created = client.batch_create(
                    AI_DOMAIN_TABLE, [{AI_DOMAIN_FIELD: nm} for nm in missing_domains])
                for rec in created:
                    dom_ids[_cell_text((rec.fields or {}).get(AI_DOMAIN_FIELD))] = rec.record_id
                progress["ai_domain_created"] = len(missing_domains)
            ai_domain_updates = []
            for rec in client.list_all(ap_tid):   # 含本轮新建的空壳行
                label = want_dom.get(_cell_text((rec.fields or {}).get("AI Project")))
                rid = dom_ids.get(label) if label else None
                if rid and _cell_text((rec.fields or {}).get(AI_DOMAIN_FIELD)) != label:
                    ai_domain_updates.append((rec.record_id, {AI_DOMAIN_FIELD: [rid]}))
            if ai_domain_updates:
                client.batch_update(ap_tid, ai_domain_updates)
                progress["ai_project_domain_updated"] = len(ai_domain_updates)

        # 2) FACT 现状(文本视图)。带 automatic_fields 拿 last_modified_by,用来判断
        #    某一行是不是被人手动碰过(不是我们自己同步机器人写的)。
        current = {}
        modifier_by_rid = {}
        for rec in client.list_all(FACT_TABLE, automatic_fields=True):
            f = rec.fields or {}
            key = (_cell_text(f.get("Group Project")), _cell_text(f.get("Maison")))
            current[key] = (rec.record_id,
                            {c: _cell_text(f.get(c)) for c in _COMPARED_FIELDS})
            modifier = getattr(rec, "last_modified_by", None)
            if modifier is not None and modifier.id and modifier.id != SYNC_BOT_USER_ID:
                modifier_by_rid[rec.record_id] = modifier.name or modifier.id

        # 3) diff + 护栏 + batch 执行(progress 记录已完成步骤,部分失败时随错误返回)
        key_by_rid = {rid: key for key, (rid, _have) in current.items()}
        creates, updates, deletes = diff_rows(desired, current)
        if scope_keys is not None:
            creates = [f for f in creates if (f["Group Project"], f["Maison"]) in scope_keys]
            updates = [(rid, f) for rid, f in updates if (f["Group Project"], f["Maison"]) in scope_keys]
            deletes = [rid for rid in deletes if key_by_rid.get(rid) in scope_keys]
        if deletion_guard_tripped(len(deletes), len(current)):
            msg = f"deletion guard tripped: would delete {len(deletes)} of {len(current)} rows"
            log.warning("feishu bitable sync aborted: %s", msg)
            return {"ok": False, "error": msg}

        # 保底信号(按行,不是按 Base 整体):即将覆盖/删除的行里,哪些当前显示是被人
        # (不是我们自己)最后改的——直接跳过这一行,不写、不删,只报给用户去核实,
        # 不去猜怎么合并;其它行照常同步,不因为一行冲突就卡住整次同步。
        manual_edits_skipped = []
        safe_updates = []
        for rid, f in updates:
            if rid in modifier_by_rid:
                manual_edits_skipped.append(
                    {"project": f["Group Project"], "maison": f["Maison"],
                     "modified_by": modifier_by_rid[rid]})
            else:
                safe_updates.append((rid, f))
        safe_deletes = []
        for rid in deletes:
            if rid in modifier_by_rid:
                key = key_by_rid.get(rid, ("", ""))
                manual_edits_skipped.append(
                    {"project": key[0], "maison": key[1],
                     "modified_by": modifier_by_rid[rid]})
            else:
                safe_deletes.append(rid)
        updates, deletes = safe_updates, safe_deletes

        if creates:
            client.batch_create(FACT_TABLE, [_create_payload(f, dim_maps) for f in creates])
            progress["created"] = len(creates)
        if updates:
            client.batch_update(FACT_TABLE,
                                [(rid, _update_payload(f, dim_maps)) for rid, f in updates])
            progress["updated"] = len(updates)
        if deletes:
            client.batch_delete(FACT_TABLE, deletes)
            progress["deleted"] = len(deletes)

        sync_state_out = {"last_synced_changelog_count": len(changelog)}
        if manual_edits_skipped:
            sync_state_out["pending_manual_conflict_keys"] = [
                [e["project"], e["maison"]] for e in manual_edits_skipped]
        _write_sync_state(portal_root, sync_state_out)

        return {"ok": True, "created": len(creates),
                "updated": len(updates), "deleted": len(deletes),
                "maison_dim_updated": progress.get("maison_dim_updated", 0),
                "ai_project_domain_updated": progress.get("ai_project_domain_updated", 0),
                "ai_domain_created": progress.get("ai_domain_created", 0),
                "manual_edits_skipped": manual_edits_skipped,
                "fact_scope_narrowed": scope_keys is not None}
    except Exception as e:  # best-effort: never break the caller
        log.warning("feishu bitable sync failed: %s", e)
        return {"ok": False, "error": str(e), **progress}
