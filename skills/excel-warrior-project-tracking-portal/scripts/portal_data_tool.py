#!/usr/bin/env python3
"""JSON-native Project Tracking Portal tool.

Can be used directly as a CLI helper and is also imported by Hermes tool
wrappers. It edits JSON data only.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_PORTAL_ROOT = Path.home() / ".hermes" / "project_tracking_portal" / "portal"
CHANGELOG = "changelog.json"
_UNSET = object()  # merge sentinel: distinct from None, which is a valid "old" value
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB cap for uploaded images

ALIASES = {
    "pcd": "perfume_christine_dior",
    "perfume dior": "perfume_christine_dior",
    "perfume christine dior": "perfume_christine_dior",
    "beauty div": "beauty_division",
    "beauty division": "beauty_division",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _data_dir(portal_root: Path) -> Path:
    return portal_root / "data"


def _load(portal_root: Path, name: str):
    with (_data_dir(portal_root) / name).open(encoding="utf-8") as f:
        return json.load(f)


def _save(portal_root: Path, name: str, data) -> None:
    path = _data_dir(portal_root) / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _now() -> str:
    """ISO local timestamp, second precision (e.g. 2026-06-17T11:30:00)."""
    return datetime.now().isoformat(timespec="seconds")


def _looks_like_image(data: bytes) -> bool:
    """True if *data* starts with a known image magic-byte sequence."""
    if len(data) < 4:
        return False
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if data[:3] == b"\xff\xd8\xff":
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if data[:2] == b"BM":
        return True
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False


def _image_ext(data: bytes, source_path: Path) -> str:
    """Pick an extension (incl. dot) from magic bytes, else the source suffix, else .png."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".webp"
    return source_path.suffix.lower() or ".png"


@contextmanager
def _with_lock(portal_root: Path):
    """Exclusive advisory lock over all writes to data/, via flock on data/.lock.

    Held around each write action's read -> modify -> save (business JSON) ->
    append (changelog) so concurrent writers cannot lose each other's updates.
    The kernel releases the lock if the process dies, so there is no stale-lock
    cleanup. Do NOT nest (flock on a second fd in the same process deadlocks);
    only top-level write actions take it, helpers called inside must not.
    """
    lock_path = _data_dir(portal_root) / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        # close() releases the flock implicitly (POSIX); guard the explicit
        # LOCK_UN so a failure there can't skip close() and leak the fd.
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _append_changelog(portal_root: Path, entry: dict) -> None:
    """Append one entry to data/changelog.json (treat missing file as []).

    Caller MUST already hold _with_lock; this helper does not lock (would deadlock).
    """
    try:
        log = _load(portal_root, CHANGELOG)
    except FileNotFoundError:
        log = []
    log.append(entry)
    _save(portal_root, CHANGELOG, log)


def _backup_files(portal_root: Path, names: list[str]) -> dict[str, str]:
    """Snapshot each named data file to data/backups/<name>.bak.<stamp> before a
    destructive write. Caller MUST already hold _with_lock. Missing files are
    skipped. Returns {name: backup_path_str} for the snapshots actually made.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S_%f")
    backup_dir = _data_dir(portal_root) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    made: dict[str, str] = {}
    for name in names:
        src = _data_dir(portal_root) / name
        if src.is_file():
            dst = backup_dir / f"{name}.bak.{stamp}"
            dst.write_bytes(src.read_bytes())
            made[name] = str(dst)
    return made


def _resolve_items(items: list[dict], query: str) -> list[dict]:
    q = (query or "").strip()
    if not q:
        return []
    alias = ALIASES.get(q.lower())
    if alias:
        return [item for item in items if item.get("id") == alias]
    qn = _norm(q)
    if not qn:
        # _norm 只保留 a-z0-9,纯中文/纯符号查询会被清空成"";子串匹配 "" in anything
        # 恒真,不拦住会把最后一步的 substring fallback 误判成"命中全部记录"返回。
        return []
    exact_id = [item for item in items if _norm(item.get("id", "")) == qn]
    if exact_id:
        return exact_id
    exact_name = [item for item in items if _norm(item.get("name", "")) == qn]
    if exact_name:
        return exact_name
    return [item for item in items if qn in _norm(item.get("name", ""))]


def search(portal_root: Path, query: str, entity_type: str = "all", limit: int = 10) -> dict:
    projects = _load(portal_root, "projects.json")
    maisons = _load(portal_root, "maisons.json")
    status_config = _load(portal_root, "status_config.json")
    out = {"ok": True, "query": query, "type": entity_type, "matches": {}}
    if entity_type in ("all", "project"):
        out["matches"]["projects"] = _resolve_items(projects, query)[:limit]
    if entity_type in ("all", "maison"):
        out["matches"]["maisons"] = _resolve_items(maisons, query)[:limit]
    if entity_type in ("all", "status"):
        qn = _norm(query)
        # 同 _resolve_items:qn 为空(纯中文/纯符号查询,或空查询)时不能落进 substring
        # fallback——"" in anything 恒真会把全部状态误判成命中。
        out["matches"]["statuses"] = [] if not qn else [
            s for s in status_config
            if qn == _norm(s.get("status", "")) or qn in _norm(s.get("status", ""))
        ][:limit]
    return out


def preview(portal_root: Path, project_id: str, maison_id: str) -> dict:
    records = _load(portal_root, "project_maison_status.json")
    rec = next((r for r in records if r.get("project_id") == project_id and r.get("maison_id") == maison_id), None)
    if rec is None:
        return {"ok": False, "error": f"no record for ({project_id}, {maison_id})"}
    return {"ok": True, "record": rec}


def update_status(portal_root: Path, project_id: str, maison_id: str, status: str,
                  remark: str | None = None, updated_at: str | None = None,
                  actor: str | None = None, reason: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not reason:
        return {"ok": False, "error": "reason required: 为什么改?(传变更原因)"}
    with _with_lock(portal_root):
        projects = _load(portal_root, "projects.json")
        maisons = _load(portal_root, "maisons.json")
        records = _load(portal_root, "project_maison_status.json")
        status_config = _load(portal_root, "status_config.json")
        if project_id not in {p.get("id") for p in projects}:
            return {"ok": False, "error": f"unknown project_id: {project_id}"}
        if maison_id not in {m.get("id") for m in maisons}:
            return {"ok": False, "error": f"unknown maison_id: {maison_id}"}
        if status not in {s.get("status") for s in status_config}:
            return {"ok": False, "error": f"unknown status: {status}"}
        when = updated_at or date.today().isoformat()
        rec = next((r for r in records
                    if r.get("project_id") == project_id and r.get("maison_id") == maison_id), None)
        changes: list[dict] = []
        if rec is None:
            rec = {"project_id": project_id, "maison_id": maison_id, "status": status,
                   "remark": remark or "", "updated_at": when}
            records.append(rec)
            old = None
            created = True
            changes.append({"field": "status", "old": None, "new": status})
            # new record: an empty remark is the same as omitting it (nothing to diff)
            if remark:
                changes.append({"field": "remark", "old": None, "new": remark})
        else:
            old = dict(rec)
            old_status = rec.get("status")
            old_remark = rec.get("remark", "")
            created = False
            if old_status != status:
                changes.append({"field": "status", "old": old_status, "new": status})
            # existing record: use `is not None` so clearing a remark ("") is recorded
            if remark is not None and remark != old_remark:
                changes.append({"field": "remark", "old": old_remark, "new": remark})
            if not changes:
                # genuine no-op (same status, no remark change): touch nothing, log nothing,
                # so the data file and the audit trail stay consistent
                return {"ok": True, "created": False, "noop": True, "old": old, "new": rec}
            rec["status"] = status
            if remark is not None:
                rec["remark"] = remark
            rec["updated_at"] = when
        _save(portal_root, "project_maison_status.json", records)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "update_status",
            "entity": "record",
            "project_id": project_id,
            "maison_id": maison_id,
            "changes": changes,
            "actor": actor,
            "reason": reason,
        })
    return {"ok": True, "created": created, "old": old, "new": rec}


def update_remark(portal_root: Path, project_id: str, maison_id: str, remark: str,
                  updated_at: str | None = None, actor: str | None = None,
                  reason: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not reason:
        return {"ok": False, "error": "reason required: 为什么改?(传变更原因)"}
    with _with_lock(portal_root):
        records = _load(portal_root, "project_maison_status.json")
        rec = next((r for r in records
                    if r.get("project_id") == project_id and r.get("maison_id") == maison_id), None)
        if rec is None:
            return {"ok": False, "error": f"no record for ({project_id}, {maison_id})"}
        old = dict(rec)
        old_remark = rec.get("remark", "")
        if remark == old_remark:
            # genuine no-op: touch nothing, log nothing (keeps data and audit consistent)
            return {"ok": True, "noop": True, "old": old, "new": rec}
        rec["remark"] = remark
        rec["updated_at"] = updated_at or date.today().isoformat()
        _save(portal_root, "project_maison_status.json", records)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "update_remark",
            "entity": "record",
            "project_id": project_id,
            "maison_id": maison_id,
            "changes": [{"field": "remark", "old": old_remark, "new": remark}],
            "actor": actor,
            "reason": reason,
        })
    return {"ok": True, "old": old, "new": rec}


_VALID_YEARS = {"2026", "2027", "2028"}


def _owner_vocabulary(portal_root: Path) -> list[str]:
    """现有 owner 全集(项目级 + 记录级),去重去空,保留首见的原样大小写。

    锁内可安全调用(只走无锁 _load)。用于把用户输入的 owner 对照已有负责人归一,
    避免 'qiwen' / 'Qiwen MO' 这类别名把词表拆碎。"""
    seen: dict[str, str] = {}
    for p in _load(portal_root, "projects.json"):
        o = (p.get("owner") or "").strip()
        if o:
            seen.setdefault(o.lower(), o)
    for r in _load(portal_root, "project_maison_status.json"):
        o = (r.get("owner") or "").strip()
        if o:
            seen.setdefault(o.lower(), o)
    return sorted(seen.values())


def _resolve_owner(raw: str, vocab: list[str]) -> dict:
    """把用户输入的 owner 对照现有词表解析(用户拍板规则):

      · 大小写不敏感精确命中 → 用 canonical。
      · 唯一部分命中(输入是某个已有名字的子串,如 'qiwen' ⊂ 'Qiwen MO')→ 自动规范化。
      · 命中多个(歧义,如 'chen' 命中好几位)→ 拒绝,给候选。
      · 一个都不命中(疑似新负责人)→ 拒绝,提示需显式确认新增。

    返回 {"ok":True,"owner":canonical,"normalized_from":raw|None}
      或 {"ok":False,"reason":"ambiguous"|"unknown","candidates":[...]}"""
    raw = (raw or "").strip()
    low = raw.lower()
    exact = [v for v in vocab if v.lower() == low]
    if exact:
        return {"ok": True, "owner": exact[0],
                "normalized_from": raw if exact[0] != raw else None}
    subs = [v for v in vocab if low in v.lower()]
    if len(subs) == 1:
        return {"ok": True, "owner": subs[0], "normalized_from": raw}
    if len(subs) > 1:
        return {"ok": False, "reason": "ambiguous", "candidates": subs}
    return {"ok": False, "reason": "unknown", "candidates": vocab}


def _resolve_owner_for_write(portal_root: Path, owner, allow_new_owner: bool):
    """给写路径用:把要写入的 owner 归一。返回 (owner_value, normalized_from, error_dict)。

    owner 为 None/空 → 视作清除,直接放行 (None, None, None)。
    命中 → 返回 canonical;歧义/无命中且未 allow_new_owner → error_dict(agent 回问用户);
    allow_new_owner=True 且无命中 → 原样(strip)写入新负责人(歧义仍拒绝)。"""
    if owner is None or not str(owner).strip():
        return None, None, None
    res = _resolve_owner(owner, _owner_vocabulary(portal_root))
    if res["ok"]:
        return res["owner"], res.get("normalized_from"), None
    if allow_new_owner and res["reason"] == "unknown":
        return str(owner).strip(), None, None
    if res["reason"] == "ambiguous":
        msg = (f"owner '{owner}' 匹配到多个已有负责人,请写全名以消歧。"
               f"候选: {', '.join(res['candidates'])}")
    else:
        msg = (f"owner '{owner}' 未匹配到已有负责人。若确为新负责人,请与用户确认后带 "
               f"allow_new_owner=True 重写;现有负责人: {', '.join(res['candidates'])}")
    return None, None, {"ok": False, "error": msg,
                        "owner_reason": res["reason"],
                        "owner_candidates": res["candidates"]}


def update_record_meta(portal_root: Path, project_id: str, maison_id: str, *,
                       project_type=_UNSET, year=_UNSET, owner=_UNSET, maison_project=_UNSET,
                       maison_top3=_UNSET, allow_new_owner: bool = False,
                       updated_at: str | None = None, actor: str | None = None,
                       reason: str | None = None) -> dict:
    """记录级(per-maison)增量字段的写路径,与 update_remark 同构。

    支持 project_type / year / owner / maison_project / maison_top3。owner 记录级优先于
    项目级 owner,maison_project 记录级优先于 Group Project(= 项目名);两者都是传空
    字符串即清除记录级覆盖(回落项目默认)。maison_top3 是布尔值(是否是这个 Maison 下
    的重点前三项目),传 True/False 直接设置,没有"清空回落"的概念(Checkbox 天然有
    默认态)。不传不动。owner 会对照已有负责人词表归一(见 _resolve_owner_for_write):
    唯一命中自动规范化,新名字需 allow_new_owner=True 显式确认。maison_project 是自由
    文本,不做词表归一。
    """
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not reason:
        return {"ok": False, "error": "reason required: 为什么改?(传变更原因)"}
    if (project_type is _UNSET and year is _UNSET and owner is _UNSET
            and maison_project is _UNSET and maison_top3 is _UNSET):
        return {"ok": False,
               "error": ("nothing to update: pass project_type / year / owner / "
                        "maison_project / maison_top3")}
    if year is not _UNSET and year is not None and year not in _VALID_YEARS:
        return {"ok": False, "error": f"invalid year: {year} (need 2026/2027/2028 or null)"}
    if maison_top3 is not _UNSET and not isinstance(maison_top3, bool):
        return {"ok": False, "error": f"invalid maison_top3: {maison_top3!r} (need true/false)"}
    if project_type is not _UNSET and project_type is not None:
        project_type = project_type.strip() or None
    if maison_project is not _UNSET and maison_project is not None:
        maison_project = maison_project.strip() or None
    owner_note = None
    with _with_lock(portal_root):
        records = _load(portal_root, "project_maison_status.json")
        rec = next((r for r in records
                    if r.get("project_id") == project_id and r.get("maison_id") == maison_id), None)
        if rec is None:
            return {"ok": False, "error": f"no record for ({project_id}, {maison_id})"}
        if owner is not _UNSET:
            owner, owner_note, err = _resolve_owner_for_write(portal_root, owner, allow_new_owner)
            if err:
                return err
        old = dict(rec)
        incoming = {"project_type": project_type, "year": year, "owner": owner,
                   "maison_project": maison_project, "maison_top3": maison_top3}
        changes: list[dict] = []
        for field, value in incoming.items():
            if value is _UNSET:
                continue
            if value != rec.get(field):
                changes.append({"field": field, "old": rec.get(field), "new": value})
        if not changes:
            out = {"ok": True, "noop": True, "old": old, "new": rec}
            if owner_note:
                out["owner_normalized_from"] = owner_note
            return out
        for ch in changes:
            rec[ch["field"]] = ch["new"]
        rec["updated_at"] = updated_at or date.today().isoformat()
        _save(portal_root, "project_maison_status.json", records)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "update_record_meta",
            "entity": "record",
            "project_id": project_id,
            "maison_id": maison_id,
            "changes": changes,
            "actor": actor,
            "reason": reason,
        })
    out = {"ok": True, "old": old, "new": rec}
    if owner_note:
        out["owner_normalized_from"] = owner_note
    return out


def add_project(portal_root: Path, id: str, name: str, domain: str = "",
                domain_category: str = "", priority: str = "", owner: str = "",
                description: str = "",
                allow_new_owner: bool = False,
                actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not id:
        return {"ok": False, "error": "id required"}
    if not name:
        return {"ok": False, "error": "name required"}
    owner_note = None
    with _with_lock(portal_root):
        projects = _load(portal_root, "projects.json")
        if any(p.get("id") == id for p in projects):
            return {"ok": False, "error": f"project id already exists: {id}"}
        # 飞书 Base FACT 表以 (Project 名字, Maison 名字) 为键,不是 id;两个项目重名会
        # 让 desired_fact_rows 里后写入的那个静默覆盖前一个,其中一个项目的所有记录
        # 从此在 Base 里彻底不可见,且没有任何报错——在写入这一步就直接拒绝。
        if any(p.get("name") == name for p in projects):
            return {"ok": False,
                    "error": f"project name already exists: {name!r}(飞书 Base 按名字识别行,重名项目会互相覆盖)"}
        # owner 与 update 路径同一套词表归一(空串跳过;新名字需 allow_new_owner)
        if owner:
            owner, owner_note, err = _resolve_owner_for_write(portal_root, owner, allow_new_owner)
            if err:
                return err
            owner = owner or ""
        # ai_project 是 name 的物化副本(仪表盘的 AI Project 列),恒等于 name,不独立设置
        new_project = {"id": id, "name": name, "domain": domain,
                       "domain_category": domain_category, "priority": priority,
                       "owner": owner, "description": description,
                       "ai_project": name}
        projects.append(new_project)
        _save(portal_root, "projects.json", projects)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "add_project",
            "entity": "project",
            "project_id": id,
            "maison_id": None,
            "changes": [{"field": "create", "old": None, "new": new_project}],
            "actor": actor,
        })
    out = {"ok": True, "id": id}
    if owner_note:
        out["owner_normalized_from"] = owner_note
    return out


def add_maison(portal_root: Path, id: str, name: str, division: str = "",
               key_account: str = "", ai_champion: str = "",
               actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not id:
        return {"ok": False, "error": "id required"}
    if not name:
        return {"ok": False, "error": "name required"}
    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        if any(m.get("id") == id for m in maisons):
            return {"ok": False, "error": f"maison id already exists: {id}"}
        # 同 add_project:Base FACT/DIM 按名字识别行,重名 maison 会在镜像里互相覆盖。
        if any(m.get("name") == name for m in maisons):
            return {"ok": False,
                    "error": f"maison name already exists: {name!r}(飞书 Base 按名字识别行,重名 maison 会互相覆盖)"}
        new_maison = {"id": id, "name": name, "division": division,
                      "key_account": key_account, "ai_champion": ai_champion}
        maisons.append(new_maison)
        _save(portal_root, "maisons.json", maisons)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "add_maison",
            "entity": "maison",
            "project_id": None,
            "maison_id": id,
            "changes": [{"field": "create", "old": None, "new": new_maison}],
            "actor": actor,
        })
    return {"ok": True, "id": id}


def update_project(portal_root: Path, id: str, *, name=_UNSET, domain=_UNSET,
                   domain_category=_UNSET, priority=_UNSET, owner=_UNSET,
                   description=_UNSET, allow_new_owner: bool = False,
                   actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    owner_note = None
    with _with_lock(portal_root):
        projects = _load(portal_root, "projects.json")
        proj = next((p for p in projects if p.get("id") == id), None)
        if proj is None:
            return {"ok": False, "error": f"unknown project_id: {id}"}
        if name is not _UNSET and name and any(
                p.get("id") != id and p.get("name") == name for p in projects):
            return {"ok": False,
                    "error": f"project name already exists: {name!r}(飞书 Base 按名字识别行,重名项目会互相覆盖)"}
        if owner is not _UNSET:
            owner, owner_note, err = _resolve_owner_for_write(portal_root, owner, allow_new_owner)
            if err:
                return err
        incoming = {"name": name, "domain": domain, "domain_category": domain_category,
                    "priority": priority, "owner": owner, "description": description}
        changes: list[dict] = []
        for field, value in incoming.items():
            if value is _UNSET:
                continue  # not passed -> leave untouched
            old = proj.get(field, "")
            if value != old:
                changes.append({"field": field, "old": old, "new": value})
        # ai_project 恒等于 name:name 变 -> 同列一起改(仪表盘 AI Project 列),不独立设置
        if any(ch["field"] == "name" for ch in changes):
            new_name = next(ch["new"] for ch in changes if ch["field"] == "name")
            if proj.get("ai_project") != new_name:
                changes.append({"field": "ai_project",
                                "old": proj.get("ai_project"), "new": new_name})
        if not changes:
            out = {"ok": True, "noop": True, "id": id}
            if owner_note:
                out["owner_normalized_from"] = owner_note
            return out
        for ch in changes:
            proj[ch["field"]] = ch["new"]
        _save(portal_root, "projects.json", projects)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "update_project",
            "entity": "project",
            "project_id": id,
            "maison_id": None,
            "changes": changes,
            "actor": actor,
        })
        # 项目级 owner 变了 -> 回显影响面:多少个 maison 继承默认(会跟着变)、
        # 多少个有记录级覆盖(不受影响)。agent 必须报给用户,路由错了当场可见。
        owner_impact = None
        if any(ch["field"] == "owner" for ch in changes):
            recs = _load(portal_root, "project_maison_status.json")
            mine = [r for r in recs if r.get("project_id") == id]
            overridden = sum(1 for r in mine if r.get("owner"))
            owner_impact = {"affected_maisons": len(mine) - overridden,
                            "overridden_maisons": overridden}
    out = {"ok": True, "id": id, "changes": changes}
    if owner_note:
        out["owner_normalized_from"] = owner_note
    if owner_impact is not None:
        out.update(owner_impact)
    return out


def update_maison(portal_root: Path, id: str, *, name=_UNSET, division=_UNSET,
                  ai_champion=_UNSET, key_account=_UNSET, actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        maison = next((m for m in maisons if m.get("id") == id), None)
        if maison is None:
            return {"ok": False, "error": f"unknown maison_id: {id}"}
        if name is not _UNSET and name and any(
                m.get("id") != id and m.get("name") == name for m in maisons):
            return {"ok": False,
                    "error": f"maison name already exists: {name!r}(飞书 Base 按名字识别行,重名 maison 会互相覆盖)"}
        incoming = {"name": name, "division": division,
                    "ai_champion": ai_champion, "key_account": key_account}
        changes: list[dict] = []
        for field, value in incoming.items():
            if value is _UNSET:
                continue
            old = maison.get(field, "")
            if value != old:
                changes.append({"field": field, "old": old, "new": value})
        if not changes:
            return {"ok": True, "noop": True, "id": id}
        for ch in changes:
            maison[ch["field"]] = ch["new"]
        _save(portal_root, "maisons.json", maisons)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "update_maison",
            "entity": "maison",
            "project_id": None,
            "maison_id": id,
            "changes": changes,
            "actor": actor,
        })
    return {"ok": True, "id": id, "changes": changes}


def delete_project(portal_root: Path, id: str, *, confirm: str | None = None,
                   actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    with _with_lock(portal_root):
        projects = _load(portal_root, "projects.json")
        proj = next((p for p in projects if p.get("id") == id), None)
        if proj is None:  # unknown id BEFORE confirm flow
            return {"ok": False, "error": f"unknown project_id: {id}"}
        records = _load(portal_root, "project_maison_status.json")
        dependents = [r for r in records if r.get("project_id") == id]
        if confirm != id:
            return {"ok": False, "needs_confirm": True, "dependents": len(dependents),
                    "error": f"将删除项目 {id} 及其 {len(dependents)} 个状态格子,请传 confirm='{id}' 确认"}
        # backup -> build new content -> save business JSON -> changelog LAST
        _backup_files(portal_root, ["projects.json", "project_maison_status.json"])
        new_projects = [p for p in projects if p.get("id") != id]
        new_records = [r for r in records if r.get("project_id") != id]
        _save(portal_root, "projects.json", new_projects)
        _save(portal_root, "project_maison_status.json", new_records)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "delete_project",
            "entity": "project",
            "project_id": id,
            "maison_id": None,
            "changes": [
                {"field": "delete", "old": proj, "new": None},
                {"field": "cascade_delete_status", "old": dependents, "new": None},
            ],
            "actor": actor,
        })
    return {"ok": True, "id": id, "deleted_cells": len(dependents)}


def delete_maison(portal_root: Path, id: str, *, confirm: str | None = None,
                  actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        maison = next((m for m in maisons if m.get("id") == id), None)
        if maison is None:  # unknown id BEFORE confirm flow
            return {"ok": False, "error": f"unknown maison_id: {id}"}
        records = _load(portal_root, "project_maison_status.json")
        budget = _load(portal_root, "maison_budget.json")
        images = _load(portal_root, "images.json")
        cells = [r for r in records if r.get("maison_id") == id]
        budget_rows = [b for b in budget if b.get("maison_id") == id]
        img_row = next((r for r in images if r.get("maison_id") == id), None)
        img_refs = list(img_row.get("images", [])) if img_row else []
        if confirm != id:
            dependents = len(cells) + len(img_refs)
            return {"ok": False, "needs_confirm": True, "dependents": dependents,
                    "error": f"将删除 maison {id} 及其 {len(cells)} 个状态格子、{len(img_refs)} 张图片,请传 confirm='{id}' 确认"}
        # backup -> build new content -> save business JSON -> changelog -> unlink LAST
        _backup_files(portal_root, ["maisons.json", "project_maison_status.json",
                                    "maison_budget.json", "images.json"])
        new_maisons = [m for m in maisons if m.get("id") != id]
        new_records = [r for r in records if r.get("maison_id") != id]
        new_budget = [b for b in budget if b.get("maison_id") != id]
        new_images = [r for r in images if r.get("maison_id") != id]
        _save(portal_root, "maisons.json", new_maisons)
        _save(portal_root, "project_maison_status.json", new_records)
        _save(portal_root, "maison_budget.json", new_budget)
        _save(portal_root, "images.json", new_images)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "delete_maison",
            "entity": "maison",
            "project_id": None,
            "maison_id": id,
            "changes": [
                {"field": "delete", "old": maison, "new": None},
                {"field": "cascade_delete_status", "old": cells, "new": None},
                {"field": "cascade_delete_budget", "old": budget_rows, "new": None},
                {"field": "cascade_delete_images", "old": img_refs, "new": None},
            ],
            "actor": actor,
        })
        # orphan-protected physical unlink, AFTER business JSON is final
        files_deleted = 0
        file_delete_errors: list[str] = []
        for ref in img_refs:
            still_referenced = any(ref in r.get("images", []) for r in new_images)
            if still_referenced:
                continue
            target = portal_root / ref
            try:
                if target.is_file():
                    target.unlink()
                    files_deleted += 1
            except OSError:
                file_delete_errors.append(ref)
    return {"ok": True, "id": id, "deleted_cells": len(cells),
            "deleted_images": len(img_refs), "files_deleted": files_deleted,
            "file_delete_errors": file_delete_errors}


def add_status(portal_root: Path, status: str, *, category: str = "",
               quarter=None, stage_order=None, description: str = "",
               actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not status:
        return {"ok": False, "error": "status required"}
    with _with_lock(portal_root):
        config = _load(portal_root, "status_config.json")
        legend = _load(portal_root, "status_legend.json")
        in_config = any(s.get("status") == status for s in config)
        in_legend = any(s.get("status") == status for s in legend)
        if in_config and in_legend:
            return {"ok": False, "error": f"status already exists: {status}"}
        if in_config != in_legend:  # XOR: orphan in exactly one table
            return {"ok": False, "error": f"status_config/legend inconsistent for: {status}"}
        # 反向同步(status_to_fact/fact_to_status)靠 (Cap(category) 或 status, quarter)
        # 这对 key 在飞书 Base 的 (Status, Quater) 单选列上做双射;两个不同 status 若
        # 映到同一对 key,Base 视图上会完全无法区分,fact_to_status 只会取第一个命中的,
        # 另一个状态的所有记录反向重建时会被静默改判成前者。这里在写入前就拒绝掉。
        new_key = ((category or "").capitalize() or status, quarter or "")
        for s in config:
            existing_key = ((s.get("category") or "").capitalize() or s.get("status"),
                           s.get("quarter") or "")
            if existing_key == new_key:
                return {"ok": False,
                        "error": (f"status {status!r} 的 (category={category!r}, "
                                 f"quarter={quarter!r}) 与已有状态 {s.get('status')!r} 在飞书 "
                                 f"Base 的 (Status, Quater) 视图下无法区分——换个 category 或 quarter")}
        config_row = {"status": status, "category": category,
                      "quarter": quarter, "stage_order": stage_order}
        legend_row = {"status": status, "description": description}
        config.append(config_row)
        legend.append(legend_row)
        _save(portal_root, "status_config.json", config)
        _save(portal_root, "status_legend.json", legend)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "add_status",
            "entity": "status",
            "project_id": None,
            "maison_id": None,
            "changes": [
                {"field": "add_status_config", "old": None, "new": config_row},
                {"field": "add_status_legend", "old": None, "new": legend_row},
            ],
            "actor": actor,
        })
    return {"ok": True, "status": status}


def generate_weekly_summary(portal_root: Path, since_days: int = 7,
                            today: str | None = None) -> dict:
    """DEPRECATED — hard alias to `format_weekly_report`.

    Historically this returned raw changelog deltas (counts / by_maison /
    follow_ups) with **no finished `report`** — which tempted the agent to
    hand-roll a fabricated summary ("变更总数 N 条 / 操作人统计" etc.).
    Doc-level bans didn't stop the model because the function still handed it
    that raw payload. We now physically route any call of the old name to the
    correct deterministic report, so a stale call produces the right output
    instead of the wrong one. Do not re-add the old raw-stats body.
    """
    return format_weekly_report(portal_root, since_days=since_days, today=today)


def _fmt_remark(old: str | None, new: str | None) -> str:
    old, new = (old or ""), (new or "")
    if not old and new:
        return f"（新增）{new}"
    if old and not new:
        return "（已清空）"
    return f"{old} → {new}"


# ── 周报模板(想改版式只动这里)──
# 注:飞书消息的 md 元素不支持 ATX 标题(#/##/###),会被原样显示成字面的 "#" 字符,
# 很丑——所以这里全部用 **粗体** 当分节标题,不用 # 前缀(2026-07-09 修复)。
TPL_HEADER       = "**📊 Project Tracking 周报（{start} ~ {end}）**"
TPL_OVERVIEW     = (
    "**概览**\n"
    "- 本周变更：{changed} 个项目·品牌组合\n"
    "- 进阶：{advanced}   平级调整：{lateral}   ⚠️ 回滚：{rollback}\n"
    "- 新增项目：{new_projects}"
)
TPL_DETAIL_HEAD  = "**变更详情**"
TPL_PROJECT_HEAD = "**{project}**"
TPL_CHANGE_LINE  = "- {maison} → **{status}**{note}"
TPL_REMARK_LINE  = "- {maison}：备注 {remark}"
TPL_NEW_HEAD     = "**🆕 新增项目**"
TPL_NEW_LINE     = "- {name}（{meta}）"


def _change_note(ch: dict) -> str:
    """Trailing note for a status-change line; '' when there's no note."""
    d = ch.get("direction")
    if d == "进阶":
        note = f"（本周 {ch['old_status']} ↑ 进阶）"
    elif d == "平级":
        note = f"（本周 {ch['old_status']} → {ch['new_status']} 平级调整）"
    elif d == "回滚":
        note = f" ⚠️ 回滚（原 {ch['old_status']}）"
    elif d == "新增":
        note = "（本周新增）"
    else:
        note = ""  # unknown-status net change: status shown, no direction note
    if ch.get("remark_change"):
        note += f"· 备注：{ch['remark_change']}"
    return note


def _render_report(summary: dict, changes: list[dict],
                   new_project_entries: list[dict]) -> str:
    """Deterministic Markdown: 概览 + 变更详情(按项目分组) + 新增项目. No 本周要点."""
    lines = [
        TPL_HEADER.format(start=summary["window_start"], end=summary["window_end"]),
        "",
        TPL_OVERVIEW.format(**summary),
    ]
    if changes:
        lines += ["", TPL_DETAIL_HEAD]
        by_project: dict[str, list[dict]] = {}
        for ch in changes:
            by_project.setdefault(ch["project"], []).append(ch)
        for project in sorted(by_project):
            lines += ["", TPL_PROJECT_HEAD.format(project=project)]
            for ch in sorted(by_project[project], key=lambda c: c["maison"]):
                if ch["new_status"] is not None:
                    lines.append(TPL_CHANGE_LINE.format(
                        maison=ch["maison"], status=ch["new_status"], note=_change_note(ch)))
                else:
                    lines.append(TPL_REMARK_LINE.format(
                        maison=ch["maison"], remark=ch["remark_change"] or ""))
    if new_project_entries:
        lines += ["", TPL_NEW_HEAD]
        for e in new_project_entries:
            meta = e.get("meta", "")
            lines.append(TPL_NEW_LINE.format(name=e["name"], meta=meta) if meta
                         else f"- {e['name']}")
    return "\n".join(lines)


def format_weekly_report(
    portal_root: Path,
    since_days: int = 7,
    today: str | None = None,
) -> dict:
    """Status-focused weekly report from the changelog (read-only, no lock).

    Window = previous complete ISO week (Mon-Sun) relative to `today`.
    Returns structured facts (summary/changes/new_project_entries) plus a
    deterministic Markdown `report`. The agent appends 「本周要点」 itself.
    """
    today_d = date.fromisoformat(today) if today else date.today()
    this_monday = today_d - timedelta(days=today_d.weekday())
    window_end = this_monday - timedelta(days=1)
    window_start = window_end - timedelta(days=since_days - 1)

    try:
        log = _load(portal_root, CHANGELOG)
    except FileNotFoundError:
        log = []
    projects = _load(portal_root, "projects.json")
    maisons = _load(portal_root, "maisons.json")
    status_config = _load(portal_root, "status_config.json")

    order_of = {s["status"]: s.get("stage_order") for s in status_config}
    proj_name = {p["id"]: p.get("name", p["id"]) for p in projects}
    proj_meta = {
        p["id"]: " / ".join(filter(None, [p.get("domain", ""), p.get("owner", "")]))
        for p in projects
    }
    maison_name = {m["id"]: m.get("name", m["id"]) for m in maisons}

    def _in_window(e: dict) -> bool:
        try:
            d = date.fromisoformat(e.get("timestamp", "")[:10])
        except (ValueError, TypeError):
            return False
        return window_start <= d <= window_end

    recent = sorted([e for e in log if _in_window(e)], key=lambda e: e.get("timestamp", ""))

    # New projects added this window (dedup by id, preserve first-seen order)
    new_project_entries: list[dict] = []
    seen_pids: set = set()
    for e in recent:
        if e.get("action") != "add_project":
            continue
        pid = e.get("project_id")
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        new_project_entries.append({
            "project_id": pid,
            "name": proj_name.get(pid, pid),
            "meta": proj_meta.get(pid, ""),
        })

    # Merge per (project_id, maison_id): earliest old, latest new, latest actor.
    # _UNSET (not None) marks "not yet captured" — None is a valid earliest old.
    delta: dict[tuple, dict] = {}
    for entry in recent:
        if entry.get("action") not in ("update_status", "update_remark"):
            continue
        key = (entry.get("project_id"), entry.get("maison_id"))
        rec = delta.setdefault(key, {"old_status": _UNSET, "new_status": _UNSET,
                                     "old_remark": _UNSET, "new_remark": _UNSET,
                                     "actor": None})
        touched = False
        for ch in entry.get("changes", []):
            f = ch.get("field")
            if f == "status":
                if rec["old_status"] is _UNSET:
                    rec["old_status"] = ch.get("old")
                rec["new_status"] = ch.get("new")
                touched = True
            elif f == "remark":
                if rec["old_remark"] is _UNSET:
                    rec["old_remark"] = ch.get("old")
                rec["new_remark"] = ch.get("new")
                touched = True
        if touched:
            rec["actor"] = entry.get("actor")

    changes: list[dict] = []
    for (pid, mid), rec in delta.items():
        status_touched = rec["new_status"] is not _UNSET
        remark_touched = rec["new_remark"] is not _UNSET
        old_status = None if rec["old_status"] is _UNSET else rec["old_status"]
        new_status = None if rec["new_status"] is _UNSET else rec["new_status"]
        old_remark = None if rec["old_remark"] is _UNSET else rec["old_remark"]
        new_remark = None if rec["new_remark"] is _UNSET else rec["new_remark"]

        net_status = status_touched and new_status is not None and old_status != new_status
        net_remark = remark_touched and (old_remark or "") != (new_remark or "")
        if not (net_status or net_remark):
            continue  # 净值没变就别出现

        if net_status:
            if old_status is None:
                direction = "新增"
            elif order_of.get(old_status) is None or order_of.get(new_status) is None:
                direction = None  # unknown status: degrade, can't classify
            else:
                o, n = order_of[old_status], order_of[new_status]
                direction = "进阶" if n > o else ("平级" if n == o else "回滚")
            ch_old, ch_new = old_status, new_status
        else:
            direction = None
            ch_old, ch_new = None, None  # remark-only: no status line

        changes.append({
            "project_id": pid,
            "project": proj_name.get(pid, pid),
            "maison_id": mid,
            "maison": maison_name.get(mid, mid),
            "old_status": ch_old,
            "new_status": ch_new,
            "direction": direction,
            "remark_change": _fmt_remark(old_remark, new_remark) if net_remark else None,
            "actor": rec["actor"],
        })

    changes.sort(key=lambda c: (c["project"], c["maison"]))

    summary = {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "changed": len(changes),
        "advanced": sum(1 for c in changes if c["direction"] == "进阶"),
        "lateral": sum(1 for c in changes if c["direction"] == "平级"),
        "rollback": sum(1 for c in changes if c["direction"] == "回滚"),
        "new_projects": len(new_project_entries),
    }
    report = _render_report(summary, changes, new_project_entries)
    return {"ok": True, "summary": summary, "changes": changes,
            "new_project_entries": new_project_entries, "report": report}


def _quarter_start(d: date) -> date:
    return date(d.year, ((d.month - 1) // 3) * 3 + 1, 1)


def _quarter_label(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def archive_changelog(portal_root: Path, today: str | None = None) -> dict:
    """把早于当前季度的 changelog 条目按季度挪进 data/changelog-archive/<year>-Qn.json,
    changelog.json 只留当前季度起的记录。手动触发(dispatch/CLI),不接自动调度——量级还小,
    先留好接口,以后要挂 cron 直接调这个 action 即可。

    重复执行安全:归档文件按条目内容去重,不会重复堆叠。changelog 从不镜像到飞书,
    这里不接 resync_feishu。"""
    today_d = date.fromisoformat(today) if today else date.today()
    cutoff = _quarter_start(today_d)

    with _with_lock(portal_root):
        try:
            log = _load(portal_root, CHANGELOG)
        except FileNotFoundError:
            return {"ok": True, "archived": 0, "kept": 0, "archive_files": []}

        def _entry_date(e):
            try:
                return date.fromisoformat(e.get("timestamp", "")[:10])
            except (ValueError, TypeError):
                return None

        kept, by_quarter = [], {}
        for e in log:
            d = _entry_date(e)
            if d is not None and d < cutoff:
                by_quarter.setdefault(_quarter_label(d), []).append(e)
            else:
                kept.append(e)

        archive_files = []
        if by_quarter:
            archive_dir = _data_dir(portal_root) / "changelog-archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            for label, entries in sorted(by_quarter.items()):
                path = archive_dir / f"{label}.json"
                existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
                merged = existing + [e for e in entries if e not in existing]
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(path)
                archive_files.append(f"data/changelog-archive/{label}.json")

            _backup_files(portal_root, [CHANGELOG])
            _save(portal_root, CHANGELOG, kept)

    archived = sum(len(v) for v in by_quarter.values())
    return {"ok": True, "archived": archived, "kept": len(kept), "archive_files": archive_files}


def validate(portal_root: Path) -> dict:
    data_dir = _data_dir(portal_root)
    required = {
        "projects": "projects.json",
        "maisons": "maisons.json",
        "records": "project_maison_status.json",
        "status_config": "status_config.json",
        "images": "images.json",
        "budget": "maison_budget.json",
        "legend": "status_legend.json",
    }
    data = {key: _load(portal_root, name) for key, name in required.items()}
    errors = []
    projects, maisons, records = data["projects"], data["maisons"], data["records"]
    status_config, images = data["status_config"], data["images"]
    budget, legend = data["budget"], data["legend"]
    pids = {p.get("id") for p in projects}
    mids = {m.get("id") for m in maisons}
    statuses = {s.get("status") for s in status_config}
    if len(pids) != len(projects):
        errors.append("duplicate project ids")
    if len(mids) != len(maisons):
        errors.append("duplicate maison ids")
    # 名字也必须唯一:飞书镜像用 (项目名, maison名) 做 FACT 行 key,重名会静默合并/互删行
    dup_pnames = [n for n, c in Counter(p.get("name", "") for p in projects).items() if n and c > 1]
    if dup_pnames:
        errors.append(f"duplicate project names (mirror key!): {dup_pnames}")
    dup_mnames = [n for n, c in Counter(m.get("name", "") for m in maisons).items() if n and c > 1]
    if dup_mnames:
        errors.append(f"duplicate maison names (mirror key!): {dup_mnames}")
    combos = Counter((r.get("project_id"), r.get("maison_id")) for r in records)
    dupes = [k for k, n in combos.items() if n > 1]
    if dupes:
        errors.append(f"duplicate project/maison records: {len(dupes)}")
    missing_project_refs = sum(1 for r in records if r.get("project_id") not in pids)
    missing_maison_refs = sum(1 for r in records if r.get("maison_id") not in mids)
    invalid_statuses = sorted({r.get("status") for r in records if r.get("status") not in statuses})
    if missing_project_refs:
        errors.append(f"missing project refs: {missing_project_refs}")
    if missing_maison_refs:
        errors.append(f"missing maison refs: {missing_maison_refs}")
    if invalid_statuses:
        errors.append(f"invalid statuses: {invalid_statuses}")
    if any(b.get("maison_id") not in mids for b in budget):
        errors.append("budget rows with unknown maison_id")
    if any(i.get("maison_id") not in mids for i in images):
        errors.append("image rows with unknown maison_id")
    legend_unknown = sorted({row.get("status") for row in legend if row.get("status") not in statuses})
    if legend_unknown:
        errors.append(f"legend statuses not in status_config: {legend_unknown}")
    missing_images = [
        rel for row in images for rel in row.get("images", [])
        if not (portal_root / rel).exists()
    ]
    if missing_images:
        errors.append(f"missing image files: {missing_images[:10]}")
    return {
        "ok": not errors,
        "errors": errors,
        "counts": {
            "projects": len(projects),
            "maisons": len(maisons),
            "records": len(records),
            "statuses": len(status_config),
            "budget_rows": len(budget),
            "image_rows": len(images),
            "referenced_images": sum(len(row.get("images", [])) for row in images),
        },
        "data_dir": str(data_dir),
    }


def remove_image(portal_root: Path, maison_id: str, image, actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        if maison_id not in {m.get("id") for m in maisons}:
            return {"ok": False, "error": f"unknown maison_id: {maison_id}"}
        images = _load(portal_root, "images.json")
        row = next((r for r in images if r.get("maison_id") == maison_id), None)
        refs = row.get("images", []) if row else []
        ref = None
        if isinstance(image, int) or (isinstance(image, str) and image.isdigit()):
            idx = int(image)
            if 1 <= idx <= len(refs):
                ref = refs[idx - 1]
        elif isinstance(image, str) and image in refs:
            ref = image
        if ref is None:
            return {"ok": False, "error": f"image not found in {maison_id}: {image!r}"}
        refs.remove(ref)
        if not refs:
            images = [r for r in images if r.get("maison_id") != maison_id]
        _save(portal_root, "images.json", images)
        still_referenced = any(ref in r.get("images", []) for r in images)
        file_deleted = False
        file_delete_error = False
        if not still_referenced:
            target = portal_root / ref
            try:
                if target.is_file():
                    target.unlink()
                    file_deleted = True
            except OSError:
                # 与 delete_maison 的物理删除对齐:JSON 引用已经改完是既成事实,不能
                # 因为磁盘 unlink 失败就让异常冒出去、跳过下面的 changelog 写入。
                file_delete_error = True
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "remove_image",
            "entity": "image",
            "project_id": None,
            "maison_id": maison_id,
            "changes": [{"field": "remove", "old": ref, "new": None}],
            "actor": actor,
        })
    out = {"ok": True, "maison_id": maison_id, "removed": ref, "file_deleted": file_deleted}
    if file_delete_error:
        out["file_delete_error"] = True
    return out


def add_image(portal_root: Path, maison_id: str, source_path: str,
              actor: str | None = None, position: int | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    src = Path(source_path)
    if not src.is_file():
        return {"ok": False, "error": f"source_path not found: {source_path}"}
    data = src.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        return {"ok": False, "error": f"image too large: {len(data)} bytes (max {MAX_IMAGE_BYTES})"}
    if not _looks_like_image(data):
        return {"ok": False, "error": "source_path is not a recognized image"}
    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        if maison_id not in {m.get("id") for m in maisons}:
            return {"ok": False, "error": f"unknown maison_id: {maison_id}"}
        images = _load(portal_root, "images.json")
        row = next((r for r in images if r.get("maison_id") == maison_id), None)
        new_hash = hashlib.sha256(data).hexdigest()
        if row:
            for ref in row.get("images", []):
                existing = portal_root / ref
                if existing.is_file() and hashlib.sha256(existing.read_bytes()).hexdigest() == new_hash:
                    return {"ok": True, "noop": True, "maison_id": maison_id, "image": ref}
        ext = _image_ext(data, src)
        img_dir = portal_root / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S_%f")
        base = f"{maison_id}_upload_{stamp}"
        filename = f"{base}{ext}"
        n = 1
        while (img_dir / filename).exists():
            filename = f"{base}_{n}{ext}"
            n += 1
        (img_dir / filename).write_bytes(data)
        ref = f"images/{filename}"
        if row is None:
            row = {"maison_id": maison_id, "images": []}
            images.append(row)
        imgs = row["images"]
        if position is not None and 1 <= position <= len(imgs) + 1:
            imgs.insert(position - 1, ref)
        else:
            imgs.append(ref)
        _save(portal_root, "images.json", images)
        _append_changelog(portal_root, {
            "timestamp": _now(),
            "action": "add_image",
            "entity": "image",
            "project_id": None,
            "maison_id": maison_id,
            "changes": [{"field": "add", "old": None, "new": ref}],
            "actor": actor,
        })
    return {"ok": True, "maison_id": maison_id, "image": ref}


def list_images(portal_root: Path, maison_id: str) -> dict:
    """Maison gallery contents (read-only, no lock)."""
    maisons = _load(portal_root, "maisons.json")
    if maison_id not in {m.get("id") for m in maisons}:
        return {"ok": False, "error": f"unknown maison_id: {maison_id}"}
    images = _load(portal_root, "images.json")
    row = next((r for r in images if r.get("maison_id") == maison_id), None)
    refs = row.get("images", []) if row else []
    return {"ok": True, "maison_id": maison_id, "count": len(refs),
            "images": [{"index": i + 1, "ref": ref} for i, ref in enumerate(refs)]}


MIRROR_AFFECTING_ACTIONS = {
    "update_status",       # 改格子值
    "add_project",         # 加行 + 行标签
    "add_maison",          # 加列 + 列表头
    "update_project",      # 可能改行标签 / owner / ai_project
    "update_maison",       # 可能改列表头
    "delete_project",      # 删行
    "delete_maison",       # 删列
    "update_remark",       # FACT 有 Remark 列
    "update_record_meta",  # project_type / year
}


def _mirror_impl(portal_root: Path) -> dict:
    """Do the real Feishu render. Isolated so tests can inject failures."""
    from feishu_bitable import sync_to_feishu
    return sync_to_feishu(portal_root)


def _resync_feishu(portal_root: Path) -> dict:
    """Force a full re-render to the Feishu Bitable FACT table. Reads only; no JSON write,
    no lock, no changelog."""
    try:
        return _mirror_impl(portal_root)
    except Exception as e:
        return {"ok": False, "error": f"feishu module unavailable: {e}"}


def resync_feishu(portal_root: Path) -> dict:
    """收尾镜像(公开,给 execute_code 调):把当前 JSON 全量镜像到飞书 Base 的 FACT 表。

    任何数据写操作后,都必须在同一段代码末尾调用它并把返回 print 给用户,确认改动已
    同步到飞书 Base。返回 {"ok": bool, "created": n, "updated": n, "deleted": n} 或
    {"ok": False, "error": ...}。只读 JSON、无锁、无 changelog,永不抛异常。

    境内 open.feishu.cn 需直连,这里临时清掉 http(s)/all_proxy 并置 no_proxy='*' 再
    还原——sandbox 里若继承了 Clash 代理也能连上;无代理时无副作用。"""
    _proxy_keys = ("http_proxy", "https_proxy", "all_proxy",
                   "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    _saved = {k: os.environ.get(k) for k in _proxy_keys + ("no_proxy", "NO_PROXY")}
    try:
        for k in _proxy_keys:
            os.environ.pop(k, None)
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        return _resync_feishu(portal_root)
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def dispatch(portal_root: Path, action: str, **kwargs) -> dict:
    actions = {
        "search": search,
        "preview": preview,
        "update_status": update_status,
        "update_remark": update_remark,
        "add_project": add_project,
        "add_maison": add_maison,
        "update_project": update_project,
        "update_maison": update_maison,
        "delete_project": delete_project,
        "delete_maison": delete_maison,
        "add_status": add_status,
        "generate_weekly_summary": generate_weekly_summary,
        "format_weekly_report": format_weekly_report,
        "validate": validate,
        "add_image": add_image,
        "remove_image": remove_image,
        "list_images": list_images,
        "update_record_meta": update_record_meta,
        "resync_feishu": resync_feishu,
        "archive_changelog": archive_changelog,
    }
    fn = actions.get(action)
    if fn is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    result = fn(portal_root, **kwargs)
    # 单向镜像:矩阵相关写成功且非 no-op 后刷飞书(锁已释放)。结果附在返回里
    # (result["feishu"]),native tool 路径也能如实向用户上报同步成败——与
    # execute_code 路径的显式 resync_feishu 口径一致。resync_feishu 永不抛异常。
    if (action in MIRROR_AFFECTING_ACTIONS
            and result.get("ok") and not result.get("noop")):
        result["feishu"] = resync_feishu(portal_root)
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", help="JSON payload with action and args")
    parser.add_argument("--portal-root", default=str(DEFAULT_PORTAL_ROOT))
    args = parser.parse_args(argv[1:])
    payload = json.loads(args.payload)
    action = payload.pop("action")
    result = dispatch(Path(args.portal_root), action, **payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
