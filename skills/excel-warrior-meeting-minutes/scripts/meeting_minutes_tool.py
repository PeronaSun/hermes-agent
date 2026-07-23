#!/usr/bin/env python3
"""隔离的会议纪要工具:按 maison 维度维护一份轻索引(meeting_minutes.json),
详实正文只归档进飞书知识库(wiki),摘要+链接直写飞书 Base 的 Meeting Minutes 表。
不 import 主表 portal_data_tool——写路径完全隔离。

2026-07-15 改版(不再是"JSON 全量 diff-resync 到 Bitable"):
- JSON 只留 {id, maison_id, date, title, doc_url, wiki_node_token, created_at, updated_at},
  不再保留 participants/content/summary/action_items/project_ids——那些只活在 wiki 页面里。
- add_minute 时先把完整内容归档进 wiki(建/复用 maison 目录节点 -> 建这条纪要的子页面 ->
  写正文),wiki 归档失败就直接报错、不落 JSON(避免留下指不到任何内容的孤儿索引)。
- Bitable 那行是 best-effort 直写(建/更新/删单行),不是从 JSON 全量 diff 出来的——JSON
  已经不携带够重建 Bitable 一行所需的全部字段了。
- update_minute 不再直接改 content/summary/participants/action_items(本地已经不存这些);
  改 title/date 是直接改索引,要追加新信息用 note 参数,在 wiki 页面末尾追加一段更新记录。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from meeting_minutes_bitable import (build_minute_fields, create_minute_row,
                                     delete_minute_row, update_minute_row)
from meeting_minutes_wiki import archive_minute, append_update_note

DEFAULT_PORTAL_ROOT = Path.home() / ".hermes" / "project_tracking_portal" / "portal"
DATA_FILE = "meeting_minutes.json"
CHANGELOG = "changelog.json"
_UNSET = object()  # merge sentinel: distinct from None


def _data_dir(portal_root: Path) -> Path:
    return Path(portal_root) / "data"


def _load(portal_root: Path, name: str):
    with (_data_dir(portal_root) / name).open(encoding="utf-8") as f:
        return json.load(f)


def _load_minutes(portal_root: Path) -> list:
    try:
        return _load(portal_root, DATA_FILE)
    except FileNotFoundError:
        return []


def _save(portal_root: Path, name: str, data) -> None:
    path = _data_dir(portal_root) / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _with_lock(portal_root: Path):
    """Exclusive advisory lock over data/ writes via flock on data/.lock.
    Do NOT nest. Kernel releases on process death; no stale cleanup."""
    lock_path = _data_dir(portal_root) / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _append_changelog(portal_root: Path, entry: dict) -> None:
    """Append one entry to changelog.json (missing file -> []). Caller holds the lock."""
    try:
        log = _load(portal_root, CHANGELOG)
    except FileNotFoundError:
        log = []
    log.append(entry)
    _save(portal_root, CHANGELOG, log)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _gen_minute_id(date_str: str, maison_id: str, existing: set) -> str:
    base = f"mm_{date_str.replace('-', '')}_{_slug(maison_id)}"
    cand, n = base, 2
    while cand in existing:
        cand = f"{base}_{n}"
        n += 1
    return cand


def _valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (ValueError, TypeError):
        return False


def _clean_project_ids(project_ids, portal_root: Path) -> tuple[list, str | None]:
    """规范 project_ids:可空;非空时必须是 list[str] 且每个 id 存在于 projects.json。
    只在非空时才读 projects.json(避免测试/极简环境必须造这个文件)。"""
    if project_ids is None:
        return [], None
    if not isinstance(project_ids, list) or not all(isinstance(p, str) for p in project_ids):
        return [], "project_ids must be a list of project id strings"
    ids = [p.strip() for p in project_ids if p.strip()]
    if not ids:
        return [], None
    try:
        known = {p.get("id") for p in _load(portal_root, "projects.json")}
    except FileNotFoundError:
        return [], "projects.json not found (cannot validate project_ids)"
    bad = [p for p in ids if p not in known]
    if bad:
        return [], f"unknown project_ids: {bad}"
    return ids, None


def _clean_action_items(items) -> tuple[list, str | None]:
    """规范 action_items:每项取 owner/task/due(字符串);task 必填。返回 (清洗后列表, 错误)。"""
    if items is None:
        return [], None
    if not isinstance(items, list):
        return [], "action_items must be a list of {owner, task, due}"
    out = []
    for it in items:
        if not isinstance(it, dict):
            return [], "each action_item must be an object {owner, task, due}"
        task = (it.get("task") or "").strip()
        if not task:
            return [], "action_item.task required"
        out.append({"owner": (it.get("owner") or "").strip(),
                    "task": task, "due": (it.get("due") or "").strip()})
    return out, None


def add_minute(portal_root: Path, *, maison_id: str, date: str, title: str = "",
               participants=None, content: str = "", summary: str = "",
               project_ids=None, action_items=None,
               actor: str | None = None) -> dict:
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if not maison_id:
        return {"ok": False, "error": "maison_id required"}
    if not _valid_date(date):
        return {"ok": False, "error": f"invalid date (need YYYY-MM-DD): {date!r}"}
    if participants is not None and not isinstance(participants, list):
        return {"ok": False, "error": "participants must be a list of names"}
    items, err = _clean_action_items(action_items)
    if err:
        return {"ok": False, "error": err}
    pids, err = _clean_project_ids(project_ids, portal_root)
    if err:
        return {"ok": False, "error": err}

    with _with_lock(portal_root):
        maisons = _load(portal_root, "maisons.json")
        maison_row = next((m for m in maisons if m.get("id") == maison_id), None)
        if maison_row is None:
            return {"ok": False, "error": f"unknown maison_id: {maison_id}"}
        maison_name = maison_row.get("name") or maison_id

        project_names = []
        if pids:
            projects = _load(portal_root, "projects.json")
            pname_by_id = {p.get("id"): p.get("name") or p.get("id") for p in projects}
            project_names = [pname_by_id.get(p, p) for p in pids]

        wiki = archive_minute(maison_name=maison_name, date=date, title=title,
                              participants=participants, summary=summary, content=content,
                              action_items=items, project_names=project_names)
        if not wiki.get("ok"):
            return {"ok": False,
                    "error": f"wiki 归档失败,未写入任何数据: {wiki.get('error')}"}

        minutes = _load_minutes(portal_root)
        mid = _gen_minute_id(date, maison_id, {m.get("id") for m in minutes})
        today = datetime.now().date().isoformat()
        rec = {"id": mid, "maison_id": maison_id, "date": date, "title": title,
               "doc_url": wiki["url"], "wiki_node_token": wiki["node_token"],
               "created_at": today, "updated_at": today}
        minutes.append(rec)
        _save(portal_root, DATA_FILE, minutes)
        _append_changelog(portal_root, {
            "timestamp": _now(), "action": "minute_add", "entity": "minute",
            "minute_id": mid, "maison_id": maison_id,
            "changes": [{"field": "create", "old": None, "new": rec}], "actor": actor})

    fields = build_minute_fields(minute_id=mid, maison_name=maison_name, date=date, title=title,
                                 owner=actor, summary=summary,
                                 project_names=project_names, content=content,
                                 action_items=items, doc_url=wiki["url"])
    feishu = create_minute_row(fields)
    return {"ok": True, "id": mid, "minute": rec, "feishu": feishu}


def update_minute(portal_root: Path, id: str, *, date=_UNSET, title=_UNSET,
                  note=_UNSET, actor: str | None = None) -> dict:
    """只改索引层的 title/date;要追加新信息(内容变了、有后续进展)用 note——
    在对应 wiki 页面末尾追加一段带日期的更新记录,不整篇重写、不动本地(本地本来就不存正文)。"""
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    if date is not _UNSET and not _valid_date(date):
        return {"ok": False, "error": f"invalid date (need YYYY-MM-DD): {date!r}"}
    note_text = None
    if note is not _UNSET:
        note_text = (note or "").strip()

    with _with_lock(portal_root):
        minutes = _load_minutes(portal_root)
        rec = next((x for x in minutes if x.get("id") == id), None)
        if rec is None:
            return {"ok": False, "error": f"unknown minute id: {id}"}
        old = dict(rec)
        incoming = {"title": title, "date": date}
        changes: list[dict] = []
        for field, value in incoming.items():
            if value is _UNSET:
                continue
            if value != rec.get(field):
                changes.append({"field": field, "old": rec.get(field), "new": value})

        if note_text:
            wiki_note = append_update_note(node_token=rec["wiki_node_token"], note=note_text,
                                           when=datetime.now().date().isoformat())
            if not wiki_note.get("ok"):
                return {"ok": False,
                        "error": f"wiki 更新记录追加失败,索引未改: {wiki_note.get('error')}"}
            changes.append({"field": "note", "old": None, "new": note_text})

        if not changes:
            return {"ok": True, "noop": True, "id": id, "minute": rec}

        for ch in changes:
            if ch["field"] != "note":
                rec[ch["field"]] = ch["new"]
        rec["updated_at"] = datetime.now().date().isoformat()
        _save(portal_root, DATA_FILE, minutes)
        _append_changelog(portal_root, {
            "timestamp": _now(), "action": "minute_update", "entity": "minute",
            "minute_id": id, "maison_id": rec.get("maison_id"),
            "changes": changes, "actor": actor})

    feishu = update_minute_row(id, {"Meeting Title": rec["title"], "Date": rec["date"]})
    return {"ok": True, "id": id, "changes": changes, "old": old, "minute": rec, "feishu": feishu}


def delete_minute(portal_root: Path, id: str, *, actor: str | None = None) -> dict:
    """只删索引行 + 对应 Bitable 行;wiki 页面本身不删(归档语义,原文不因为索引没了就消失)。"""
    if not actor:
        return {"ok": False, "error": "actor required: 谁在改?(传当前用户名)"}
    with _with_lock(portal_root):
        minutes = _load_minutes(portal_root)
        rec = next((x for x in minutes if x.get("id") == id), None)
        if rec is None:
            return {"ok": False, "error": f"unknown minute id: {id}"}
        minutes = [x for x in minutes if x.get("id") != id]
        _save(portal_root, DATA_FILE, minutes)
        _append_changelog(portal_root, {
            "timestamp": _now(), "action": "minute_delete", "entity": "minute",
            "minute_id": id, "maison_id": rec.get("maison_id"),
            "changes": [{"field": "delete", "old": rec, "new": None}], "actor": actor})

    feishu = delete_minute_row(id)
    return {"ok": True, "id": id, "deleted": rec, "feishu": feishu}


def list_minutes(portal_root: Path, maison_id: str | None = None,
                 date_from: str | None = None, date_to: str | None = None) -> dict:
    rows = _load_minutes(portal_root)
    if maison_id:
        rows = [r for r in rows if r.get("maison_id") == maison_id]
    if date_from:
        rows = [r for r in rows if (r.get("date") or "") >= date_from]
    if date_to:
        rows = [r for r in rows if (r.get("date") or "") <= date_to]
    rows = sorted(rows, key=lambda r: (r.get("date", ""), r.get("id", "")))
    return {"ok": True, "count": len(rows), "minutes": rows}


def dispatch(portal_root: Path, action: str, **kwargs) -> dict:
    actions = {
        "add_minute": add_minute,
        "update_minute": update_minute,
        "delete_minute": delete_minute,
        "list_minutes": list_minutes,
    }
    fn = actions.get(action)
    if fn is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    return fn(portal_root, **kwargs)


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
