# src/change_logger.py
"""HTML 修改审计日志:diff 即真相来源 + 两阶段 preview/commit + drift 自愈。

设计文档: docs/superpowers/specs/2026-06-15-html-change-log-design.md
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))  # so html_editor_tool importable

import html_editor_tool as _het

# DB 放项目根 data/(运行态数据,不进 src/)。测试用 monkeypatch 覆盖。
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "change_log.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS change_log (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              batch_id    TEXT,
              timestamp   TEXT,
              actor       TEXT,
              detected_by TEXT,
              maison      TEXT,
              project     TEXT,
              status      TEXT,
              sheet       TEXT,
              location    TEXT,
              change_type TEXT,
              old_value   TEXT,
              new_value   TEXT,
              why         TEXT,
              is_derived  INTEGER,
              source      TEXT
            );
            CREATE TABLE IF NOT EXISTS baseline (
              id            INTEGER PRIMARY KEY CHECK (id = 1),
              file_hash     TEXT,
              cell_map_json TEXT,
              updated_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS pending_batch (
              batch_id    TEXT,
              rows_json   TEXT,
              file_hash   TEXT,
              cell_map_json TEXT,
              created_at  TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def file_hash() -> str:
    with open(_het.HTML_FILE_PATH, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def snapshot() -> dict:
    """把当前 HTML 拍平成 {cells, images}。列号=逻辑列(colspan 展开,1-based)。"""
    soup = _het._load_html(_het.HTML_FILE_PATH)
    sheets = _het._get_all_sheets(soup)
    divs = soup.select(".sheet-content")
    cells: dict[str, str] = {}
    images: dict[str, list[str]] = {}
    for i, name in enumerate(sheets):
        if i >= len(divs):
            break
        div = divs[i]
        for r_idx, tr in enumerate(div.select("tbody tr"), start=1):
            logical_col = 1
            for cell in tr.find_all(["td", "th"]):
                colspan = int(cell.get("colspan", 1))
                text = cell.get_text(separator="\n")
                if text.strip():
                    cells[f"{name}|{r_idx}|{logical_col}"] = text
                logical_col += colspan
        gallery = div.find("div", class_="image-gallery")
        imgs = gallery.find_all("img", class_="gallery-img") if gallery else []
        if imgs:
            images[name] = [im.get("src") for im in imgs]
    return {"cells": cells, "images": images}


def _loc(sheet: str, row, col) -> str:
    return f"{sheet}!R{row}C{col}"


def _diff_cells(base_cells: dict, cur_cells: dict) -> list[dict]:
    out = []
    for key in set(base_cells) | set(cur_cells):
        old = base_cells.get(key)
        new = cur_cells.get(key)
        if old == new:
            continue
        sheet, row, col = key.rsplit("|", 2)
        if old is None:
            ctype = "cell_add"
        elif new is None:
            ctype = "cell_delete"
        else:
            ctype = "cell_update"
        out.append({"sheet": sheet, "row": int(row), "col": int(col),
                    "location": _loc(sheet, row, col), "change_type": ctype,
                    "old_value": old, "new_value": new})
    return out


def _diff_images(base_imgs: dict, cur_imgs: dict) -> list[dict]:
    out = []
    for sheet in set(base_imgs) | set(cur_imgs):
        old = base_imgs.get(sheet, [])
        new = cur_imgs.get(sheet, [])
        if old == new:
            continue
        # 同长且同集合但顺序不同 → reorder
        if len(old) == len(new) and sorted(old) == sorted(new):
            out.append({"sheet": sheet, "row": None, "col": None,
                        "location": f"{sheet}!images", "change_type": "image_reorder",
                        "old_value": json.dumps(old, ensure_ascii=False),
                        "new_value": json.dumps(new, ensure_ascii=False)})
            continue
        # 同长且仅个别位置不同 → 这些位置算 replace
        if len(old) == len(new):
            for i, (o, n) in enumerate(zip(old, new), start=1):
                if o != n:
                    out.append({"sheet": sheet, "row": None, "col": None,
                                "location": f"{sheet}!image#{i}", "change_type": "image_replace",
                                "old_value": o, "new_value": n})
            continue
        # 不同长 → 用多重集合(计数)差,逐个出现报 add / delete,避免重复 src 漏记
        old_counts = Counter(old)
        new_counts = Counter(new)
        for src in (new_counts - old_counts).elements():
            out.append({"sheet": sheet, "row": None, "col": None,
                        "location": f"{sheet}!image", "change_type": "image_add",
                        "old_value": None, "new_value": src})
        for src in (old_counts - new_counts).elements():
            out.append({"sheet": sheet, "row": None, "col": None,
                        "location": f"{sheet}!image", "change_type": "image_delete",
                        "old_value": src, "new_value": None})
    return out


def diff(base: dict, cur: dict) -> list[dict]:
    return _diff_cells(base.get("cells", {}), cur.get("cells", {})) + \
           _diff_images(base.get("images", {}), cur.get("images", {}))


MAIN_SHEET = "Maison Dimension Tracking Table"
_BRAND_NAME_ROW = 2          # 逻辑行:品牌名所在行(主表)
_MAIN_PROJECT_COL = 5        # 主表 project 逻辑列
_DETAIL_PROJECT_COL = 2      # detail 表 project 逻辑列
_DETAIL_STATUS_COL = 4       # detail 表 status 逻辑列


def _cell(cells: dict, sheet: str, row: int, col: int) -> str:
    return cells.get(f"{sheet}|{row}|{col}", "").strip()


def _infer(sheet: str, row, cells: dict) -> dict:
    """推断 maison/project/status。row=None(图片)→ project/status=UNKNOWN。绝不抛错。"""
    if row is None:
        return {"maison": sheet, "project": "UNKNOWN", "status": "UNKNOWN"}
    if sheet == MAIN_SHEET:
        return {
            "maison": "UNKNOWN",
            "project": _cell(cells, sheet, row, _MAIN_PROJECT_COL) or "UNKNOWN",
            "status": "UNKNOWN",
        }
    # detail 表
    return {
        "maison": sheet,
        "project": _cell(cells, sheet, row, _DETAIL_PROJECT_COL) or "UNKNOWN",
        "status": _cell(cells, sheet, row, _DETAIL_STATUS_COL) or "UNKNOWN",
    }


_PROFILE_ROOT = os.path.expanduser("~/.hermes/user_profiles")
# profile.json 中显示名的候选键(实现期确认后可收敛)
_PROFILE_NAME_KEYS = ("name", "display_name", "user_name", "nickname")


def _session_user_id() -> str | None:
    # 软导入 Hermes 的 session env;本仓库无该模块则回退到环境变量
    try:
        from hermes.session import get_session_env  # type: ignore
        uid = get_session_env("HERMES_SESSION_USER_ID")
        if uid:
            return uid
    except Exception:
        pass
    return os.environ.get("HERMES_SESSION_USER_ID") or None


def _name_from_profile(uid: str) -> str | None:
    path = os.path.join(_PROFILE_ROOT, uid, "profile.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    for k in _PROFILE_NAME_KEYS:
        if data.get(k):
            return str(data[k])
    return None


def _get_actor() -> str:
    uid = _session_user_id()
    if uid:
        name = _name_from_profile(uid)
        if name:
            return name
    return os.environ.get("CHANGE_LOG_ACTOR") or "UNKNOWN"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_baseline_map(conn) -> dict:
    row = conn.execute("SELECT cell_map_json FROM baseline WHERE id=1").fetchone()
    if row and row[0]:
        return json.loads(row[0])
    return {"cells": {}, "images": {}}


def preview_change_log(source: str = "tool", derived_locations: set | None = None,
                       user_locations: set | None = None) -> dict:
    conn = _connect()
    try:
        base = _load_baseline_map(conn)
        cur = snapshot()
        changes = diff(base, cur)
        if not changes:
            return {"batch_id": None, "rows": [], "committed": True}
        who = _get_actor()
        actor = "UNKNOWN" if source == "drift" else who
        ts = _now()
        batch_id = str(uuid.uuid4())
        rows = []
        for ch in changes:
            info = _infer(ch["sheet"], ch["row"], cur["cells"])
            if user_locations is not None:
                is_derived = 0 if ch["location"] in user_locations else 1
            else:
                is_derived = 1 if ch["location"] in (derived_locations or set()) else 0
            rows.append({
                "batch_id": batch_id, "timestamp": ts,
                "actor": actor, "detected_by": who,
                "maison": info["maison"], "project": info["project"],
                "status": info["status"], "sheet": ch["sheet"],
                "location": ch["location"], "change_type": ch["change_type"],
                "old_value": ch["old_value"], "new_value": ch["new_value"],
                "why": None,
                "is_derived": is_derived,
                "source": source,
            })
        conn.execute(
            "INSERT INTO pending_batch(batch_id, rows_json, file_hash, cell_map_json, created_at)"
            " VALUES (?,?,?,?,?)",
            (batch_id, json.dumps(rows, ensure_ascii=False), file_hash(),
             json.dumps(cur, ensure_ascii=False), ts),
        )
        conn.commit()
        return {"batch_id": batch_id, "rows": rows, "committed": False}
    finally:
        conn.close()


def discard_pending(batch_id: str) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM pending_batch WHERE batch_id=?", (batch_id,))
        conn.commit()
    finally:
        conn.close()


_LOG_COLS = ("batch_id", "timestamp", "actor", "detected_by", "maison", "project",
             "status", "sheet", "location", "change_type", "old_value", "new_value",
             "why", "is_derived", "source")


def _insert_log_rows(conn, rows: list[dict]) -> None:
    placeholders = ",".join(["?"] * len(_LOG_COLS))
    conn.executemany(
        f"INSERT INTO change_log ({','.join(_LOG_COLS)}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in _LOG_COLS) for r in rows],
    )


def commit_change_log(batch_id: str, why: str | None) -> dict:
    conn = _connect()
    try:
        pend = conn.execute(
            "SELECT rows_json, file_hash, cell_map_json FROM pending_batch WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        if not pend:
            raise ValueError(f"unknown batch_id {batch_id!r}")
        rows = json.loads(pend[0])
        for r in rows:
            r["why"] = why
        conn.execute("BEGIN")
        _insert_log_rows(conn, rows)
        conn.execute(
            "INSERT INTO baseline(id, file_hash, cell_map_json, updated_at) VALUES (1,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET file_hash=excluded.file_hash,"
            " cell_map_json=excluded.cell_map_json, updated_at=excluded.updated_at",
            (pend[1], pend[2], _now()),
        )
        conn.execute("DELETE FROM pending_batch WHERE batch_id=?", (batch_id,))
        conn.commit()
        return {"committed": True, "count": len(rows)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_baseline() -> dict:
    """把当前 HTML 状态记为 baseline,但不产生任何 change_log 行。
    部署时一次性调用,避免首次编辑把整份文档当成改动记录下来。"""
    conn = _connect()
    try:
        cur = snapshot()
        fh = file_hash()
        conn.execute(
            "INSERT INTO baseline(id, file_hash, cell_map_json, updated_at) VALUES (1,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET file_hash=excluded.file_hash,"
            " cell_map_json=excluded.cell_map_json, updated_at=excluded.updated_at",
            (fh, json.dumps(cur, ensure_ascii=False), _now()),
        )
        conn.commit()
        return {"ok": True, "file_hash": fh}
    finally:
        conn.close()


def ensure_logged():
    """入口安全网:有未记录漂移则返回 drift preview,否则 None。
    只有当某个 pending 批次正好对应当前文件状态(已 staged 待 commit)时才抑制;
    陈旧 pending(对应旧状态)不再屏蔽新漂移。"""
    cur_hash = file_hash()
    conn = _connect()
    try:
        base = conn.execute("SELECT file_hash FROM baseline WHERE id=1").fetchone()
        pending_hashes = [r[0] for r in conn.execute(
            "SELECT file_hash FROM pending_batch").fetchall()]
    finally:
        conn.close()
    base_hash = base[0] if base else None
    if base_hash == cur_hash:
        return None                       # 无漂移
    if cur_hash in pending_hashes:
        return None                       # 当前状态已被某个 pending 捕获,等待 commit
    return preview_change_log(source="drift")
