#!/usr/bin/env python3
"""One-time backfill: pull the Base's extra columns into portal JSON.

The ONLY Base->JSON flow ever (after this, mirroring is strictly JSON->Base).
Default is --dry-run: print + save a report for human review; --apply writes
JSON under the portal lock and appends a single changelog entry.

Usage (direct connection, bypassing Clash):
  env -u http_proxy -u https_proxy -u all_proxy no_proxy='*' NO_PROXY='*' \
    python3 backfill_from_base.py --portal-root ~/.hermes/project_tracking_portal/portal \
    [--apply --actor 薛亮] [--report-file backfill_report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from feishu_bitable import (DOMAIN_TO_AI_DOMAIN, FACT_TABLE, MAISON_NAME_MAP,
                            FeishuBitableClient, _cell_text, _config, status_to_fact)

AI_DOMAIN_TO_DOMAIN = {v: k for k, v in DOMAIN_TO_AI_DOMAIN.items()}


def _slugify(name):
    """项目名 -> snake_case id,跟现有 projects.json 里手写 id 的习惯一致。"""
    s = re.sub(r"[^\w]+", "_", name.strip().lower())
    return re.sub(r"_+", "_", s).strip("_")


def fact_to_status(fact_status, fact_quater, status_config):
    """FACT 的 (Status, Quater) 反推为 portal 单字段 status。

    status_to_fact 的逆:对每个状态算 (Cap(category) 或 status, quarter 或 "")
    与入参比对。命中返回该 status;空状态或无法反推返回 ""(由调用方计入异常)。
    """
    want = ((fact_status or ""), (fact_quater or ""))
    for row in status_config:
        cat = (row.get("category") or "").capitalize()
        st = cat or row.get("status")
        if (st, (row.get("quarter") or "")) == want:
            return row.get("status")
    return ""


def load_rename_map(path):
    """读一次性改名对照文件 -> {"renames": {旧名:新名}, "deletions": [旧名,...]}.

    缺文件抛 FileNotFoundError;结构不符抛 ValueError。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("renames"), dict) or not isinstance(data.get("deletions"), list):
        raise ValueError("rename-map 需含 renames(dict) 与 deletions(list)")
    return {"renames": data["renames"], "deletions": data["deletions"]}


def _key(project_name, maison_name):
    return f"{project_name}|{maison_name}"


def analyze(fact_rows, projects, maisons, records, status_config):
    """FACT 文本视图行 + portal JSON -> 回填/对账/异常 三段式报告(纯函数)。"""
    inv_maison = {v: k for k, v in MAISON_NAME_MAP.items()}   # Base 名 -> JSON 名
    maison_by_name = {m.get("name"): m for m in maisons}
    name_by_pid = {p.get("id"): p.get("name") for p in projects}
    name_by_mid = {m.get("id"): m.get("name") for m in maisons}

    report = {"backfill": {"records_meta": {}},
              "reconcile": {"json_only": [], "base_only": [], "status_mismatch": []},
              "anomalies": []}

    # Base 侧索引:(project_name, json_maison_name) -> fact row
    fact_by_key = {}
    for row in fact_rows:
        jm = inv_maison.get(row.get("Maison", ""), row.get("Maison", ""))
        fact_by_key[(row.get("Group Project", ""), jm)] = row

    # JSON 侧索引 + json_only
    json_keys = set()
    for r in records:
        pn, mn = name_by_pid.get(r.get("project_id")), name_by_mid.get(r.get("maison_id"))
        if pn is None or mn is None:
            report["anomalies"].append(f"JSON 记录引用未知 id: {r.get('project_id')}/{r.get('maison_id')}")
            continue
        json_keys.add((pn, mn))
        row = fact_by_key.get((pn, mn))
        if row is None:
            report["reconcile"]["json_only"].append([pn, MAISON_NAME_MAP.get(mn, mn)])
            continue
        # status 对账(以 JSON 为准,仅列差异):JSON status 映射后与 Base (Status, Quater) 比
        st, quarter = status_to_fact(r.get("status") or "", status_config)
        if (st, quarter or "") != (row.get("Status", ""), row.get("Quater", "")):
            base_disp = row.get("Status", "")
            if row.get("Quater"):
                base_disp += f"({row['Quater']})"
            report["reconcile"]["status_mismatch"].append(
                {"key": [pn, row.get("Maison", "")], "json": r.get("status"),
                 "base": base_disp})
        # 记录级回填:JSON 缺、Base 有
        meta = {}
        if not r.get("project_type") and row.get("Project Type"):
            meta["project_type"] = row["Project Type"]
        if not r.get("year") and row.get("Year"):
            meta["year"] = row["Year"]
        if meta:
            report["backfill"]["records_meta"][_key(r["project_id"], r["maison_id"])] = meta

    # base_only
    for (pn, mn), row in fact_by_key.items():
        if (pn, mn) not in json_keys:
            report["reconcile"]["base_only"].append([pn, row.get("Maison", "")])

    # 注:ai_project 是 name 的物化副本(恒等于项目名),不从 Base 独立回填。
    return report


def _project_owner_from_fact(fact_rows, base_name):
    """项目级 owner:该项目所有 FACT 行里第一个非空 Owner;全空返回 ''。"""
    for row in fact_rows:
        if row.get("Group Project") == base_name and row.get("Owner"):
            return row["Owner"]
    return ""


def _base_domain_by_projname(fact_rows):
    """{Base Group Project 名: 反查出的 portal domain 值}。

    只收 Base "AI Domain" 非空且能经 AI_DOMAIN_TO_DOMAIN 反查回 portal 原始值的项目;
    Base 给的值不在映射表里(拼写漂移/新增域名)不静默丢——由调用方记异常、回退旧值。
    """
    out, unmapped = {}, set()
    for row in fact_rows:
        pn = row.get("Group Project", "")
        dom = row.get("AI Domain", "")
        if not pn or not dom or pn in out:
            continue
        mapped = AI_DOMAIN_TO_DOMAIN.get(dom)
        if mapped is None:
            unmapped.add((pn, dom))
        else:
            out[pn] = mapped
    return out, unmapped


def analyze_rebuild(fact_rows, projects, maisons, records, status_config,
                    rename_map, sync_date):
    """Base 为准的全量反向重建(纯函数)。

    "假装 JSON 不存在"式重建:JSON 项目改名后的有效名在 Base FACT 找不到就整个丢弃
    (不需要在 rename-map 里手动列 deletions);Base 有、JSON 没有的项目自动新建
    (id 用项目名 slug)。domain 有 Base "AI Domain" 值就用 Base 的(反查 portal 原始值),
    Base 没给值就保留 JSON 原值(Base 没信号不代表要清空);domain_category/priority/
    description 这几个 Base 没有对应字段,一律沿用旧 JSON 值(新项目留空)。

    返回 {"projects", "records", "summary", "anomalies"}:
    projects/records 是期望落盘的最终内容,summary 供 changelog/人工审,
    anomalies 收集配不上/空状态/空 owner/丢弃/新建/域名反查失败。绝不静默丢弃信息。
    """
    renames = rename_map.get("renames", {})
    deletions = set(rename_map.get("deletions", []))
    anomalies = []

    # Base maison 名 -> maison_id(与 MAISON_NAME_MAP 口径一致)
    inv_maison_name = {}
    for m in maisons:
        base_m = MAISON_NAME_MAP.get(m.get("name", ""), m.get("name", ""))
        inv_maison_name[base_m] = m.get("id")

    base_projnames = set(r.get("Group Project", "") for r in fact_rows)
    domain_by_projname, unmapped_domains = _base_domain_by_projname(fact_rows)
    for pn, dom in sorted(unmapped_domains):
        anomalies.append(f"Base 项目 {pn!r} 的 AI Domain {dom!r} 不在已知映射里,domain 保留原值")

    # ---- 期望 projects ----
    desired_projects, id_by_name = [], {}
    renamed, owner_changed, deleted_projects, dropped_not_in_base = [], [], [], []
    for p in projects:
        old = p.get("name", "")
        if old in deletions:
            deleted_projects.append(old)
            continue
        new = renames.get(old, old)
        if new not in base_projnames:
            dropped_not_in_base.append(new)
            anomalies.append(f"JSON 项目 {old!r}(有效名 {new!r})在 Base FACT 找不到,本次重建丢弃")
            continue
        owner = _project_owner_from_fact(fact_rows, new)
        if not owner:
            anomalies.append(f"项目 {new!r} 在 Base 无 Owner(留空)")
        domain = domain_by_projname.get(new, p.get("domain", ""))
        desired_projects.append({
            "id": p.get("id"),
            "name": new,
            "domain": domain,
            "domain_category": p.get("domain_category", ""),
            "priority": p.get("priority", ""),
            "owner": owner,
            "description": p.get("description", ""),
            "ai_project": new,
        })
        if new != old:
            renamed.append([old, new])
        if owner != p.get("owner", ""):
            owner_changed.append([new, p.get("owner", ""), owner])
        if new in id_by_name:
            anomalies.append(f"两个 JSON 项目撞到同一 Base 名 {new!r}")
        id_by_name[new] = p.get("id")

    # ---- Base 独有项目:自动新建(id = name 的 slug,冲突则加数字后缀)----
    used_ids = {p["id"] for p in desired_projects}
    created_projects = []
    for bn in sorted(base_projnames):
        if not bn or bn in id_by_name:
            continue
        base_id = _slugify(bn) or "project"
        new_id, n = base_id, 2
        while new_id in used_ids:
            anomalies.append(f"新建项目 {bn!r} 的 id {new_id!r} 已被占用,改用 {base_id}_{n}")
            new_id = f"{base_id}_{n}"
            n += 1
        used_ids.add(new_id)
        owner = _project_owner_from_fact(fact_rows, bn)
        if not owner:
            anomalies.append(f"新建项目 {bn!r} 在 Base 无 Owner(留空)")
        desired_projects.append({
            "id": new_id,
            "name": bn,
            "domain": domain_by_projname.get(bn, ""),
            "domain_category": "",
            "priority": "",
            "owner": owner,
            "description": "",
            "ai_project": bn,
        })
        id_by_name[bn] = new_id
        created_projects.append(bn)

    # ---- 期望 records(以 FACT 为准重建)----
    # 记录级 owner:FACT 行的 Owner 与该项目的项目级 owner 不同(同项目跨 maison 的
    # owner 本就不一致)才写进 record,与 update_record_meta / feishu_bitable 的
    # "record.owner 缺省回落 project.owner" 语义对齐;相同则不写,保持 schema 一致
    # (不写死一个到处都是的重复值)。绝不能整段丢弃——那会在下次全量正向同步时把
    # Base 上本就不同的 Owner 覆盖成清一色的项目级 owner。
    project_owner_by_id = {p["id"]: p.get("owner", "") for p in desired_projects}
    desired_records, seen_keys = [], set()
    record_owner_overrides = []
    record_maison_project_overrides = []
    for row in fact_rows:
        pn, mn = row.get("Group Project", ""), row.get("Maison", "")
        pid, mid = id_by_name.get(pn), inv_maison_name.get(mn)
        if pid is None or mid is None:
            anomalies.append(f"FACT 行无法落地: Project={pn!r} Maison={mn!r}")
            continue
        if (pid, mid) in seen_keys:
            anomalies.append(f"FACT 重复行(同一 项目×Maison): Project={pn!r} Maison={mn!r}")
            continue
        seen_keys.add((pid, mid))
        st = fact_to_status(row.get("Status", ""), row.get("Quater", ""), status_config)
        if not st:
            anomalies.append(f"FACT 行状态空/无法反推: Project={pn!r} Maison={mn!r}")
        desired_rec = {
            "project_id": pid,
            "maison_id": mid,
            "status": st,
            "remark": row.get("Remark", ""),
            "updated_at": sync_date,
            "year": row.get("Year", ""),
            "project_type": row.get("Project Type", ""),
        }
        row_owner = row.get("Owner", "")
        if row_owner and row_owner != project_owner_by_id.get(pid, ""):
            desired_rec["owner"] = row_owner
            record_owner_overrides.append([pn, mn, row_owner])
        row_maison_project = row.get("Maison Project", "")
        if row_maison_project and row_maison_project != pn:
            desired_rec["maison_project"] = row_maison_project
            record_maison_project_overrides.append([pn, mn, row_maison_project])
        desired_records.append(desired_rec)

    # ---- records diff 计数(key=(pid,mid))----
    cur = {(r.get("project_id"), r.get("maison_id")): r for r in records}
    des = {(r["project_id"], r["maison_id"]): r for r in desired_records}
    created = [k for k in des if k not in cur]
    deleted = [k for k in cur if k not in des]
    updated = [k for k in des if k in cur and any(
        (cur[k].get(f) or "") != (des[k].get(f) or "")
        for f in ("status", "year", "project_type", "remark", "owner", "maison_project"))]

    summary = {
        "renamed": renamed,
        "deleted_projects": deleted_projects,
        "dropped_not_in_base": dropped_not_in_base,
        "created_projects": created_projects,
        "owner_changed": owner_changed,
        "record_owner_overrides": record_owner_overrides,
        "record_maison_project_overrides": record_maison_project_overrides,
        "records": {"created": len(created), "updated": len(updated),
                    "deleted": len(deleted)},
    }
    return {"projects": desired_projects, "records": desired_records,
            "summary": summary, "anomalies": anomalies}


def fetch_fact_rows():
    """活连读 FACT 全表 -> 文本视图行。需要 env 配置齐全。"""
    cfg = _config()
    if cfg is None:
        raise SystemExit("feishu 未配置 (需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN)")
    client = FeishuBitableClient(cfg)
    cols = ("Maison Project", "Maison", "Status", "Quater", "Year",
            "Remark", "Owner", "Group Project", "Project Type", "AI Domain")
    rows = []
    for rec in client.list_all(FACT_TABLE):
        f = rec.fields or {}
        rows.append({c: _cell_text(f.get(c)) for c in cols})
    return rows


def apply_backfill(portal_root, report, actor):
    """报告 -> JSON 落盘(portal 锁内)+ 一条 changelog。"""
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    import portal_data_tool as pdt
    portal_root = Path(portal_root)
    with pdt._with_lock(portal_root):
        records = pdt._load(portal_root, "project_maison_status.json")
        n_rec = 0
        for key, meta in report["backfill"]["records_meta"].items():
            pid, mid = key.split("|", 1)
            rec = next((x for x in records
                        if x.get("project_id") == pid and x.get("maison_id") == mid), None)
            if rec is None:
                continue
            for field, value in meta.items():
                if not rec.get(field):
                    rec[field] = value
            n_rec += 1
        pdt._save(portal_root, "project_maison_status.json", records)
        pdt._append_changelog(portal_root, {
            "timestamp": pdt._now(),
            "action": "backfill_from_base",
            "entity": "bulk",
            "project_id": None,
            "maison_id": None,
            "changes": [{"field": "backfill",
                         "old": None,
                         "new": {"records_meta": n_rec}}],
            "actor": actor,
        })
    return {"ok": True, "records_updated": n_rec}


def apply_rebuild(portal_root, report, actor):
    """把 analyze_rebuild 的期望内容落盘:portal 锁内备份->写两个 JSON->一条 changelog。"""
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    import portal_data_tool as pdt
    portal_root = Path(portal_root)
    backup_dir = portal_root / "data" / ".bak" / datetime.now().strftime("%Y%m%dT%H%M%S")
    with pdt._with_lock(portal_root):
        backup_dir.mkdir(parents=True, exist_ok=True)
        for name in ("projects.json", "project_maison_status.json"):
            (backup_dir / name).write_text(
                json.dumps(pdt._load(portal_root, name), ensure_ascii=False, indent=2),
                encoding="utf-8")
        pdt._save(portal_root, "projects.json", report["projects"])
        pdt._save(portal_root, "project_maison_status.json", report["records"])
        pdt._append_changelog(portal_root, {
            "timestamp": pdt._now(),
            "action": "reverse_reconcile",
            "entity": "bulk",
            "project_id": None,
            "maison_id": None,
            "changes": [{"field": "reverse_reconcile", "old": None,
                         "new": report["summary"]}],
            "actor": actor,
        })
    return {"ok": True, "backup": str(backup_dir), "summary": report["summary"]}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portal-root", required=True)
    ap.add_argument("--apply", action="store_true", help="落盘(默认只出报告)")
    ap.add_argument("--actor", default="", help="--apply 时必填")
    ap.add_argument("--report-file", default="backfill_report.json")
    ap.add_argument("--mode", choices=["backfill", "rebuild"], default="backfill",
                    help="backfill=只补空(旧行为);rebuild=Base 为准全量反向重建")
    ap.add_argument("--rename-map", default="", help="rebuild 必填:改名对照文件路径")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="rebuild 落盘的显式闸门;缺省即使 --apply 也只出报告")
    args = ap.parse_args(argv[1:])

    root = Path(args.portal_root).expanduser()
    load = lambda n: json.loads((root / "data" / n).read_text(encoding="utf-8"))  # noqa: E731
    fact_rows = fetch_fact_rows()

    if args.mode == "rebuild":
        if not args.rename_map:
            raise SystemExit("--mode rebuild 需要 --rename-map <path>")
        rename_map = load_rename_map(args.rename_map)
        report = analyze_rebuild(fact_rows, load("projects.json"), load("maisons.json"),
                                 load("project_maison_status.json"),
                                 load("status_config.json"), rename_map,
                                 date.today().isoformat())
        Path(args.report_file).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        s = report["summary"]
        print(f"[rebuild] 改名×{len(s['renamed'])} 显式删除×{len(s['deleted_projects'])} "
              f"Base找不到而丢弃×{len(s['dropped_not_in_base'])} Base独有自动新建×{len(s['created_projects'])} "
              f"owner变×{len(s['owner_changed'])} 记录级owner覆盖×{len(s['record_owner_overrides'])} | "
              f"记录 新建{s['records']['created']}/"
              f"更新{s['records']['updated']}/删除{s['records']['deleted']} | "
              f"异常×{len(report['anomalies'])}")
        print(f"完整报告 -> {args.report_file}")
        if not (args.apply and args.force_rebuild):
            print("dry-run(未落盘)。审报告后加 --apply --force-rebuild --actor <你的名字> 落盘。")
            return 0
        result = apply_rebuild(root, report, args.actor)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    report = analyze(fact_rows, load("projects.json"), load("maisons.json"),
                     load("project_maison_status.json"), load("status_config.json"))
    Path(args.report_file).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    b, rc = report["backfill"], report["reconcile"]
    print(f"回填候选: 记录 meta ×{len(b['records_meta'])}")
    print(f"对账: json_only ×{len(rc['json_only'])}, base_only ×{len(rc['base_only'])}, "
          f"status_mismatch ×{len(rc['status_mismatch'])}")
    print(f"异常 ×{len(report['anomalies'])};完整报告 -> {args.report_file}")
    if not args.apply:
        print("dry-run(未落盘)。人工审阅报告后加 --apply --actor <你的名字> 执行。")
        return 0
    result = apply_backfill(root, report, args.actor)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
