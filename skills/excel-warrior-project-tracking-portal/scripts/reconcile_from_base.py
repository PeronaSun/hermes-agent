#!/usr/bin/env python3
"""One-time reconcile: align portal JSON to the Base's current data.

User decision (2026-07-03): 以 Base 为准 — for the pre-mirror reconcile the
Base wins. This is a one-time Base->JSON flow (same class as backfill), run
once before the mirror is switched on so the first JSON->Base sync is not
destructive. Three buckets:

  json_only       -> delete the JSON record (Base doesn't have it). If a
                     project loses ALL its records AND its name is absent
                     from Base, drop the project entry too (cascade). The
                     data decides junk-vs-real, not a hardcoded list.
  base_only       -> add the record from Base (create the project entry if
                     missing). Skip junk rows (blank project or maison name)
                     and rows whose Base status can't map to portal vocab.
  status_mismatch -> overwrite the JSON status with Base's (converted to
                     portal vocab). If Base status is blank, keep JSON + flag
                     (a blank cannot be represented and reads as a Base gap).

Default is --dry-run: print the full mutation plan for review. --apply writes
JSON under the portal lock and appends ONE changelog entry.

Usage (direct connection, bypassing Clash):
  env -u http_proxy -u https_proxy -u all_proxy no_proxy='*' NO_PROXY='*' \
    python3 reconcile_from_base.py --portal-root ~/.hermes/project_tracking_portal/portal \
    [--apply --actor 薛亮] [--report-file reconcile_report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from feishu_bitable import (FACT_TABLE, MAISON_NAME_MAP, FeishuBitableClient,
                            _cell_text, _config, status_to_fact)

_VALID_YEARS = {"2026", "2027", "2028"}


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "x"


def _unique_slug(name: str, taken: set[str]) -> str:
    base = _slugify(name)
    slug, i = base, 2
    while slug in taken:
        slug = f"{base}_{i}"
        i += 1
    return slug


def _base_to_status(status_config):
    """(Status_capitalized, Quater or "") -> portal status string."""
    inv = {}
    for e in status_config:
        inv[(e["category"].capitalize(), e.get("quarter") or "")] = e["status"]
    return inv


def build_plan(fact_rows, projects, maisons, records, status_config):
    """FACT 文本行 + portal JSON -> 变更计划(纯函数,不写盘)。"""
    inv_maison = {v: k for k, v in MAISON_NAME_MAP.items()}     # Base 名 -> JSON 名
    base_to_status = _base_to_status(status_config)
    name_by_pid = {p["id"]: p["name"] for p in projects}
    name_by_mid = {m["id"]: m["name"] for m in maisons}
    id_by_pname = {p["name"]: p["id"] for p in projects}
    id_by_mname = {m["name"]: m["id"] for m in maisons}

    fact_by_key = {}
    fact_project_names = set()
    for row in fact_rows:
        pn = row.get("Group Project", "")
        if pn:
            fact_project_names.add(pn)
        jm = inv_maison.get(row.get("Maison", ""), row.get("Maison", ""))
        if pn and jm:
            fact_by_key[(pn, jm)] = row

    plan = {"deletes": [], "delete_projects": [], "adds": [], "status_updates": [],
            "skipped": [], "anomalies": []}

    json_keys = set()
    for r in records:
        pn, mn = name_by_pid.get(r["project_id"]), name_by_mid.get(r["maison_id"])
        if pn is None or mn is None:
            plan["anomalies"].append(
                f"JSON 记录引用未知 id: {r['project_id']}/{r['maison_id']}")
            continue
        json_keys.add((pn, mn))

    # bucket 1: json_only -> delete records
    for r in records:
        pn, mn = name_by_pid.get(r["project_id"]), name_by_mid.get(r["maison_id"])
        if pn is None or mn is None:
            continue
        if (pn, mn) not in fact_by_key:
            plan["deletes"].append({"project_id": r["project_id"], "maison_id": r["maison_id"],
                                    "project": pn, "maison": mn, "status": r.get("status")})

    # cascade: a project all of whose JSON records are being deleted AND that
    # does not exist on Base -> drop the project entry too
    deleted_keys = {(d["project_id"], d["maison_id"]) for d in plan["deletes"]}
    by_project = defaultdict(list)
    for r in records:
        by_project[r["project_id"]].append((r["project_id"], r["maison_id"]))
    for pid, keys in by_project.items():
        if all(k in deleted_keys for k in keys) and name_by_pid.get(pid) not in fact_project_names:
            plan["delete_projects"].append({"project_id": pid, "name": name_by_pid.get(pid)})

    # bucket 2: base_only -> add
    for (pn, jm), row in fact_by_key.items():
        if (pn, jm) in json_keys:
            continue
        if not pn or not jm:
            plan["skipped"].append({"reason": "blank key", "project": pn, "maison": jm})
            continue
        st = base_to_status.get((row.get("Status", ""), row.get("Quater", "") or ""))
        if st is None:
            plan["skipped"].append({
                "reason": f"unmappable base status {row.get('Status')!r}/{row.get('Quater')!r}",
                "project": pn, "maison": jm})
            continue
        plan["adds"].append({
            "project": pn, "maison": jm,
            "project_exists": pn in id_by_pname,
            "maison_id": id_by_mname.get(jm),
            "status": st,
            "remark": row.get("Remark", ""),
            "owner": row.get("Owner", ""),
            "project_type": row.get("Project Type", ""),
            "year": row.get("Year", ""),
        })

    # bucket 3: status_mismatch -> overwrite JSON status with Base's
    for r in records:
        pn, mn = name_by_pid.get(r["project_id"]), name_by_mid.get(r["maison_id"])
        if pn is None or mn is None:
            continue
        row = fact_by_key.get((pn, mn))
        if row is None:
            continue                                     # json_only, handled above
        cur = r.get("status") or ""
        cur_st, cur_q = status_to_fact(cur, status_config)
        base_pair = (row.get("Status", ""), row.get("Quater", "") or "")
        if (cur_st, cur_q or "") == base_pair:
            continue                                     # already agrees
        if not row.get("Status"):
            plan["anomalies"].append(f"Base status 空,保留 JSON: {pn}/{mn} = {cur!r}")
            continue
        new_status = base_to_status.get(base_pair)
        if new_status is None:
            plan["anomalies"].append(f"Base status 无法映射 {base_pair}: {pn}/{mn}")
            continue
        if new_status != cur:
            plan["status_updates"].append({
                "project_id": r["project_id"], "maison_id": r["maison_id"],
                "project": pn, "maison": mn, "old": cur, "new": new_status})
    return plan


def apply_reconcile(portal_root, plan, actor):
    """计划 -> JSON 落盘(portal 锁内)+ 一条 changelog。"""
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    import portal_data_tool as pdt
    portal_root = Path(portal_root)
    today = date.today().isoformat()
    with pdt._with_lock(portal_root):
        projects = pdt._load(portal_root, "projects.json")
        maisons = pdt._load(portal_root, "maisons.json")
        records = pdt._load(portal_root, "project_maison_status.json")

        # 3) status overwrite
        n_status = 0
        for u in plan["status_updates"]:
            rec = next((r for r in records
                        if r["project_id"] == u["project_id"] and r["maison_id"] == u["maison_id"]),
                       None)
            if rec is not None and rec.get("status") != u["new"]:
                rec["status"] = u["new"]
                rec["updated_at"] = today
                n_status += 1

        # 1) delete json_only records
        del_keys = {(d["project_id"], d["maison_id"]) for d in plan["deletes"]}
        before = len(records)
        records = [r for r in records if (r["project_id"], r["maison_id"]) not in del_keys]
        n_del = before - len(records)

        # 1b) drop emptied+absent projects
        del_pids = {d["project_id"] for d in plan["delete_projects"]}
        projects = [p for p in projects if p["id"] not in del_pids]
        n_delp = len(del_pids)

        # 2) add base_only records (+ new project entries as needed)
        taken = {p["id"] for p in projects}
        id_by_pname = {p["name"]: p["id"] for p in projects}
        n_add = n_newproj = 0
        for a in plan["adds"]:
            pid = id_by_pname.get(a["project"])
            if pid is None:
                pid = _unique_slug(a["project"], taken)
                taken.add(pid)
                id_by_pname[a["project"]] = pid
                projects.append({
                    "id": pid, "name": a["project"], "domain": "", "domain_category": "",
                    "priority": "", "owner": a["owner"], "description": "",
                    "ai_project": a["project"]})  # ai_project 恒等于 name
                n_newproj += 1
            rec = {"project_id": pid, "maison_id": a["maison_id"], "status": a["status"],
                   "remark": a["remark"] or "", "updated_at": today}
            if a["project_type"]:
                rec["project_type"] = a["project_type"]
            if a["year"] in _VALID_YEARS:
                rec["year"] = a["year"]
            records.append(rec)
            n_add += 1

        pdt._save(portal_root, "projects.json", projects)
        pdt._save(portal_root, "project_maison_status.json", records)
        pdt._append_changelog(portal_root, {
            "timestamp": pdt._now(),
            "action": "reconcile_from_base",
            "entity": "bulk",
            "project_id": None,
            "maison_id": None,
            "changes": [{"field": "reconcile", "old": None, "new": {
                "status_updated": n_status, "records_deleted": n_del,
                "projects_deleted": n_delp, "records_added": n_add,
                "projects_added": n_newproj}}],
            "actor": actor,
        })
    return {"ok": True, "status_updated": n_status, "records_deleted": n_del,
            "projects_deleted": n_delp, "records_added": n_add, "projects_added": n_newproj}


def fetch_fact_rows():
    cfg = _config()
    if cfg is None:
        raise SystemExit("feishu 未配置 (需 FEISHU_APP_ID/SECRET/BITABLE_APP_TOKEN)")
    client = FeishuBitableClient(cfg)
    cols = ("Maison Project", "Maison", "Status", "Quater", "Year",
            "Remark", "Owner", "Group Project", "Project Type")
    return [{c: _cell_text((rec.fields or {}).get(c)) for c in cols}
            for rec in client.list_all(FACT_TABLE)]


def _print_plan(plan):
    def line(x):
        print("   ", x)
    print(f"\n▶ status 覆盖 (以 Base 为准) ×{len(plan['status_updates'])}")
    for u in plan["status_updates"]:
        line(f"{u['project']} / {u['maison']}: {u['old']} -> {u['new']}")
    print(f"\n▶ 删除记录 (json_only, Base 无) ×{len(plan['deletes'])}")
    for d in plan["deletes"]:
        line(f"{d['project']} / {d['maison']}  (status={d['status']})")
    print(f"\n▶ 连带删除项目 (全部记录 json_only 且 Base 无此项目) ×{len(plan['delete_projects'])}")
    for d in plan["delete_projects"]:
        line(f"{d['name']}  (id={d['project_id']})")
    print(f"\n▶ 新增记录 (base_only, 以 Base 为准) ×{len(plan['adds'])}")
    for a in plan["adds"]:
        tag = "" if a["project_exists"] else "  [+新建项目]"
        line(f"{a['project']} / {a['maison']}: {a['status']}"
             f"  owner={a['owner']!r} type={a['project_type']!r} year={a['year']!r}{tag}")
    print(f"\n▶ 跳过 (垃圾/无法映射) ×{len(plan['skipped'])}")
    for s in plan["skipped"]:
        line(f"{s['project']!r} / {s['maison']!r}  ({s['reason']})")
    print(f"\n▶ 需人看 anomalies ×{len(plan['anomalies'])}")
    for a in plan["anomalies"]:
        line(a)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portal-root", required=True)
    ap.add_argument("--apply", action="store_true", help="落盘(默认只出计划)")
    ap.add_argument("--actor", default="", help="--apply 时必填")
    ap.add_argument("--report-file", default="reconcile_report.json")
    args = ap.parse_args(argv[1:])

    root = Path(args.portal_root).expanduser()
    load = lambda n: json.loads((root / "data" / n).read_text(encoding="utf-8"))  # noqa: E731
    fact_rows = fetch_fact_rows()
    plan = build_plan(fact_rows, load("projects.json"), load("maisons.json"),
                      load("project_maison_status.json"), load("status_config.json"))
    Path(args.report_file).write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_plan(plan)
    print(f"\n完整计划 -> {args.report_file}")
    if not args.apply:
        print("dry-run(未落盘)。审阅后加 --apply --actor <你的名字> 执行。")
        return 0
    result = apply_reconcile(root, plan, args.actor)
    print("\n" + json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
