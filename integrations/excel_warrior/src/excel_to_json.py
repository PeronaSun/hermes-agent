"""One-shot Excel -> portal/data/*.json importer.

Run once; Excel retires afterward. See
docs/superpowers/specs/2026-06-16-json-source-portal-migration-design.md
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def assign_ids(names: list[str]) -> list[tuple[str, str]]:
    """Map display names to unique slug ids, suffixing duplicates with _2, _3..."""
    seen: dict[str, int] = {}
    out: list[tuple[str, str]] = []
    for name in names:
        base = slugify(name)
        seen[base] = seen.get(base, 0) + 1
        sid = base if seen[base] == 1 else f"{base}_{seen[base]}"
        out.append((name, sid))
    return out


def _col_to_index(ref: str) -> int:
    """'C5' -> 2 (0-indexed column)."""
    letters = re.match(r"[A-Z]+", ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_workbook_grids(xlsx_path: str) -> dict[str, list[list[str]]]:
    """Return {sheet_name: grid}. grid[r][c] is cell text, padded to rectangle."""
    with zipfile.ZipFile(xlsx_path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS_MAIN}}}t")))
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target")
                for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        grids: dict[str, list[list[str]]] = {}
        for s in wb.find(f"{{{NS_MAIN}}}sheets"):
            name = s.get("name")
            target = "xl/" + rels[s.get(f"{{{NS_REL}}}id")]
            ws = ET.fromstring(z.read(target))
            sparse: dict[int, dict[int, str]] = {}
            max_c = -1
            for fallback_ri, row in enumerate(ws.find(f"{{{NS_MAIN}}}sheetData")):
                r_attr = row.get("r")
                ri = int(r_attr) - 1 if r_attr is not None else fallback_ri
                rowmap: dict[int, str] = {}
                for c in row:
                    ci = _col_to_index(c.get("r"))
                    v = c.find(f"{{{NS_MAIN}}}v")
                    if v is None:
                        continue
                    val = shared[int(v.text)] if c.get("t") == "s" else (v.text or "")
                    rowmap[ci] = (val or "").strip()
                    max_c = max(max_c, ci)
                sparse[ri] = rowmap
            n_rows = (max(sparse) + 1) if sparse else 0
            width = max_c + 1
            grid = [[sparse.get(r, {}).get(c, "") for c in range(width)]
                    for r in range(n_rows)]
            grids[name] = grid
        return grids


MAISON_FIRST_COL = 7
MAISON_LAST_COL = 29
DIV_ROW, NAME_ROW, CHAMP_ROW, KA_ROW = 0, 1, 5, 6


def _forward_fill(cells: list[str]) -> list[str]:
    out, last = [], ""
    for v in cells:
        last = v if v else last
        out.append(last)
    return out


def parse_maisons(main_grid: list[list[str]],
                  first_col: int = MAISON_FIRST_COL,
                  last_col: int = MAISON_LAST_COL) -> list[dict]:
    cols = range(first_col, last_col + 1)
    names = [main_grid[NAME_ROW][c] for c in cols]
    divisions = _forward_fill([main_grid[DIV_ROW][c] for c in cols])
    champs = [main_grid[CHAMP_ROW][c] for c in cols]
    kas = [main_grid[KA_ROW][c] for c in cols]
    name_ids = dict(assign_ids(names))
    out = []
    for name, div, champ, ka in zip(names, divisions, champs, kas):
        if not name:
            continue
        out.append({"id": name_ids[name], "name": name, "division": div,
                    "ai_champion": champ, "key_account": ka})
    return out


# Source sheets / B26 headers sometimes name a maison differently from the
# canonical name in the main tracking grid. Map those aliases here.
MAISON_NAME_ALIASES = {
    "Perfume Dior": "Perfume Christine Dior",
    "Beauty Div": "Beauty Division",
}


def _canonical_maison_name(raw: str) -> str:
    """Normalize a maison name from a secondary source: drop any parenthetical
    note ('Beauty Division (Givenchy, ...)' -> 'Beauty Division') and apply aliases."""
    base = raw.split("(")[0].strip()
    return MAISON_NAME_ALIASES.get(raw, MAISON_NAME_ALIASES.get(base, base))


def resolve_maison_id(raw: str, name_to_mid: dict) -> str | None:
    """Resolve a raw maison name (from a detail sheet / B26 header / image sheet)
    to a maison id, tolerating aliases and parenthetical notes. None if unresolved."""
    if raw in name_to_mid:
        return name_to_mid[raw]
    return name_to_mid.get(_canonical_maison_name(raw))


DATA_START_ROW = 7
COL_DOMAIN_CAT, COL_DOMAIN, COL_PRIORITY, COL_OWNER, COL_NAME = 0, 1, 2, 3, 4


def parse_projects(main_grid: list[list[str]],
                   data_start_row: int = DATA_START_ROW) -> list[dict]:
    rows = main_grid[data_start_row:]
    cats = _forward_fill([r[COL_DOMAIN_CAT] for r in rows])
    names = [r[COL_NAME] for r in rows]
    name_ids = dict(assign_ids([n for n in names if n]))
    out = []
    for r, cat in zip(rows, cats):
        name = r[COL_NAME]
        if not name:
            continue
        out.append({"id": name_ids[name], "name": name,
                    "domain": r[COL_DOMAIN], "domain_category": cat,
                    "priority": r[COL_PRIORITY], "owner": r[COL_OWNER],
                    "description": ""})
    return out


def parse_status_records(main_grid, projects, maisons,
                         first_col=MAISON_FIRST_COL, last_col=MAISON_LAST_COL,
                         data_start_row=DATA_START_ROW, updated_at="") -> list[dict]:
    maison_by_col = {}
    for c in range(first_col, last_col + 1):
        nm = main_grid[NAME_ROW][c]
        if nm:
            maison_by_col[c] = nm
    name_to_mid = {m["name"]: m["id"] for m in maisons}
    name_to_pid = {p["name"]: p["id"] for p in projects}
    out = []
    for r in main_grid[data_start_row:]:
        pname = r[COL_NAME]
        pid = name_to_pid.get(pname)
        if not pid:
            continue
        for c, mname in maison_by_col.items():
            status = r[c]
            mid = name_to_mid.get(mname)
            if status and mid:
                out.append({"project_id": pid, "maison_id": mid, "status": status,
                            "remark": "", "updated_at": updated_at})
    return out


def _find_header_cols(grid, headers: list[str]) -> tuple[int, dict[str, int]]:
    """Return (header_row_index, {header_lower: col}). Matches first row containing all headers."""
    want = [h.lower() for h in headers]
    for ri, row in enumerate(grid):
        low = [c.lower() for c in row]
        if all(h in low for h in want):
            return ri, {h: low.index(h) for h in want}
    raise ValueError(f"headers {headers} not found")


def merge_remarks(records, detail_grid, maison_id, projects) -> list[tuple]:
    """Mutate records in place to attach remarks for this maison.
    Returns list of (maison_id, project_name, remark) that matched no record."""
    name_to_pid = {p["name"]: p["id"] for p in projects}
    rec_by_key = {(r["project_id"], r["maison_id"]): r for r in records}
    hdr_row, cols = _find_header_cols(detail_grid, ["projects", "status", "remarks"])
    pcol, rcol = cols["projects"], cols["remarks"]
    unmatched = []
    for row in detail_grid[hdr_row + 1:]:
        pname = row[pcol] if pcol < len(row) else ""
        remark = row[rcol] if rcol < len(row) else ""
        if not pname or not remark:
            continue
        pid = name_to_pid.get(pname)
        rec = rec_by_key.get((pid, maison_id)) if pid else None
        if rec is not None:
            rec["remark"] = remark
        else:
            unmatched.append((maison_id, pname, remark))
    return unmatched


def build_status_config() -> list[dict]:
    cfg = [{"status": "Ideation", "category": "ideation", "quarter": None, "stage_order": 1}]
    for q in ("Q1", "Q2", "Q3", "Q4"):
        cfg.append({"status": f"{q}-Pilot", "category": "pilot", "quarter": q, "stage_order": 2})
    for q in ("Q1", "Q2", "Q3", "Q4"):
        cfg.append({"status": f"{q}-Scale", "category": "scale", "quarter": q, "stage_order": 3})
    cfg.append({"status": "Run", "category": "run", "quarter": None, "stage_order": 4})
    cfg.append({"status": "Done", "category": "done", "quarter": None, "stage_order": 5})
    return cfg


def unknown_statuses(seen: list[str], config: list[dict]) -> set[str]:
    known = {c["status"] for c in config}
    return {s for s in seen if s and s not in known}


def parse_budget(b26_grid, maisons, maison_header_row: int = 0,
                 maison_first_col: int = 2, metric_col: int = 1) -> list[dict]:
    name_to_mid = {m["name"]: m["id"] for m in maisons}
    header = b26_grid[maison_header_row]
    col_to_mid = {}
    for c in range(maison_first_col, len(header)):
        mid = resolve_maison_id(header[c], name_to_mid)
        if mid:
            col_to_mid[c] = mid
    notes_by_mid: dict[str, list[dict]] = {mid: [] for mid in col_to_mid.values()}
    for row in b26_grid[maison_header_row + 1:]:
        metric = row[metric_col] if metric_col < len(row) else ""
        if not metric:
            continue
        for c, mid in col_to_mid.items():
            val = row[c] if c < len(row) else ""
            if val:
                notes_by_mid[mid].append({"key": metric, "value": val})
    return [{"maison_id": mid, "notes": notes}
            for mid, notes in notes_by_mid.items() if notes]


def parse_legend(desc_grid, config) -> list[dict]:
    known = {c["status"] for c in config}
    out, seen = [], set()
    for row in desc_grid:
        for ci, cell in enumerate(row):
            if cell in known and cell not in seen:
                desc = next((row[k] for k in range(ci + 1, len(row)) if row[k]), "")
                out.append({"status": cell, "description": desc})
                seen.add(cell)
    return out


def images_json_from_galleries(galleries: dict[int, list[str]],
                               sheet_names: dict[int, str],
                               maisons: list[dict]) -> list[dict]:
    name_to_mid = {m["name"]: m["id"] for m in maisons}
    out = []
    for idx, paths in galleries.items():
        mid = resolve_maison_id(sheet_names.get(idx, ""), name_to_mid)
        if mid and paths:
            out.append({"maison_id": mid, "images": paths})
    return out


def count_exported_images(galleries: dict) -> int:
    return sum(len(v) for v in galleries.values())


def extract_images(xlsx_path: str, images_dir: str,
                   maisons: list[dict]) -> tuple[list[dict], int]:
    """Export PNGs to images_dir and return (images.json rows keyed by maison_id,
    count of PNGs actually exported)."""
    import build_html as bh  # reuse proven anchor extraction
    sheet_names = bh.parse_workbook_sheets(xlsx_path)
    anchors = bh.extract_image_anchors(xlsx_path)
    galleries = bh.export_images(xlsx_path, anchors, sheet_names, images_dir)
    sheet_names_by_idx = {i: n for i, n in enumerate(sheet_names)}
    rows = images_json_from_galleries(galleries, sheet_names_by_idx, maisons)
    return rows, count_exported_images(galleries)


def validate_import(projects, maisons, records, config,
                    unknown_status: set, unmatched_remarks: list,
                    images_orphaned: int = 0) -> dict:
    pids = {p["id"] for p in projects}
    mids = {m["id"] for m in maisons}
    known_status = {c["status"] for c in config}
    combo = Counter((r["project_id"], r["maison_id"]) for r in records)
    report = {
        "total_records": len(records),
        "total_projects": len(projects),
        "total_maisons": len(maisons),
        "missing_project_refs": sum(1 for r in records if r["project_id"] not in pids),
        "missing_maison_refs": sum(1 for r in records if r["maison_id"] not in mids),
        "invalid_statuses": sum(1 for r in records if r["status"] not in known_status),
        "duplicate_combos": sum(1 for k, n in combo.items() if n > 1),
        "empty_owner_projects": sum(1 for p in projects if not p.get("owner")),
        "unknown_status_values": sorted(unknown_status),
        "unmatched_remarks": len(unmatched_remarks),
        "images_orphaned": images_orphaned,
    }
    report["clean"] = (report["missing_project_refs"] == 0 and report["missing_maison_refs"] == 0
                       and report["invalid_statuses"] == 0 and report["duplicate_combos"] == 0
                       and not report["unknown_status_values"] and report["images_orphaned"] == 0)
    return report


DATE = "2026-06-16"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
XLSX_PATH = os.path.join(ROOT, "source_data ", "Project Tracking V3.xlsx")  # trailing space in dir
PORTAL_DIR = os.path.join(ROOT, "portal")
DATA_DIR = os.path.join(PORTAL_DIR, "data")
IMAGES_DIR = os.path.join(PORTAL_DIR, "images")

MAIN_SHEET = "Maison Dimension Tracking Table"
B26_SHEET = "B26 Stats"
DESC_SHEET = "Description"
DETAIL_SHEETS = [
    "Louis Vuitton", "Christine Dior Couture", "Loro Piana", "Fendi", "Celine",
    "Loewe", "Givenchy", "Kenzo", "Berluti", "Marc Jocobs", "Rimowa", "Tiffany",
    "Bvlgari", "Chaumet", "Fred", "Hublot", "TAG Heuer", "Zenith", "Perfume Dior",
    "Guerlain", "Beauty Div", "Sephora", "MHD",
]


def _atomic_write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _portal_relative_images(images: list[dict]) -> list[dict]:
    """Rewrite image paths to portal-root-relative 'images/<filename>' so the
    front-end (index.html served from portal/) can load them regardless of the
    absolute directory the PNGs were exported to."""
    out = []
    for row in images:
        out.append({"maison_id": row["maison_id"],
                    "images": [posixpath.join("images", os.path.basename(p))
                               for p in row["images"]]})
    return out


def build_dataset(grids: dict[str, list[list[str]]], xlsx_path: str,
                  images_dir: str = IMAGES_DIR) -> dict:
    main = grids[MAIN_SHEET]
    maisons = parse_maisons(main)
    projects = parse_projects(main)
    config = build_status_config()
    records = parse_status_records(main, projects, maisons, updated_at=DATE)

    name_to_mid = {m["name"]: m["id"] for m in maisons}
    unmatched_remarks = []
    for sheet in DETAIL_SHEETS:
        if sheet not in grids:
            continue
        mid = resolve_maison_id(sheet, name_to_mid)
        if not mid:
            continue
        try:
            unmatched_remarks += merge_remarks(records, grids[sheet], mid, projects)
        except ValueError:
            pass  # detail sheet without a Remarks header -> nothing to merge

    if B26_SHEET in grids:
        b26 = grids[B26_SHEET]
        hdr = next((i for i, row in enumerate(b26) if len(row) > 1 and row[1] == "Maison"), 0)
        budget = parse_budget(b26, maisons, maison_header_row=hdr)
    else:
        budget = []
    legend = parse_legend(grids[DESC_SHEET], config) if DESC_SHEET in grids else []
    raw_images, exported_count = extract_images(xlsx_path, images_dir, maisons)
    images = _portal_relative_images(raw_images)
    referenced_count = sum(len(r["images"]) for r in images)
    images_orphaned = exported_count - referenced_count

    seen_status = [r["status"] for r in records]
    unknown = unknown_statuses(seen_status, config)
    report = validate_import(projects, maisons, records, config, unknown, unmatched_remarks,
                             images_orphaned=images_orphaned)
    return {"projects": projects, "maisons": maisons,
            "project_maison_status": records, "status_config": config,
            "maison_budget": budget, "status_legend": legend, "images": images,
            "report": report, "unmatched_remarks": unmatched_remarks}


def main():
    grids = read_workbook_grids(XLSX_PATH)
    ds = build_dataset(grids, XLSX_PATH)
    for key in ("projects", "maisons", "project_maison_status", "status_config",
                "maison_budget", "status_legend", "images"):
        _atomic_write_json(os.path.join(DATA_DIR, f"{key}.json"), ds[key])
    lines = [f"{k}: {v}" for k, v in ds["report"].items()]
    lines.append("")
    lines.append("Unmatched remarks (maison_id, project_name, remark):")
    lines += [f"  {t}" for t in ds["unmatched_remarks"]]
    with open(os.path.join(DATA_DIR, "import_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("Import report:")
    print("\n".join(f"  {k}: {v}" for k, v in ds["report"].items()))
    if not ds["report"]["clean"]:
        print("\n⚠️  Report not clean — review portal/data/import_report.txt")


if __name__ == "__main__":
    main()
