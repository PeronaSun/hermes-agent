"""Auto-recalculation engine for LV Project Tracking HTML.

Replicates two Excel formula patterns:
1. COUNTIF + TEXTJOIN — statistics in main table (row-level G col + column headers)
2. FILTER — brand detail sheets derived from main table

Called automatically by html_editor_tool.update_cell() after each edit.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

# ── Constants ─────────────────────────────────────────────────────────────────

MAIN_SHEET = "Maison Dimension Tracking Table"

# Main table layout
_HEADER_ROWS = 7          # rows 0-6 are headers; data starts at row index 7
_STATS_ROW_TOTAL = 2      # "Total: N"
_STATS_ROW_IDEATION = 3   # "Ideation: N"
_STATS_ROW_PILOT = 4      # "Pilot: X   |   Scale: Y"
_BRAND_NAME_ROW = 1       # row with brand names

_NUM_BRANDS = 23           # LV(0) through MHD(22)

# Brand detail sheets: tab indices 5..27 map to brand offsets 0..22
_DETAIL_SHEET_START = 5
_DETAIL_SHEET_END = 27

# Detail sheet layout
_DETAIL_DATA_START_ROW = 7   # data rows begin at row index 7
_DETAIL_STATUS_COL = 3       # 0-indexed td index for status in detail left section
_DETAIL_MAISON_NAME_COL = 4  # 0-indexed td index for Maison Name column
_DETAIL_LEFT_COLS = 5        # [empty, project, owner, status, maison_name]
_DETAIL_HEADER_ROW = 6       # column header row
_DETAIL_STATS_ROW = 3        # owner + total stats row


# ── Column index helpers (handle 31 vs 30 cell rowspan offset) ────────────────

def _brand_td_index(n_cells: int, brand_offset: int) -> int:
    """Map brand_offset (0=LV .. 22=MHD) to td index in a data row."""
    base = 7 if n_cells >= 31 else 6
    return base + brand_offset


def _stats_td_index(n_cells: int) -> int:
    """Column G (row statistics) td index."""
    return 6 if n_cells >= 31 else 5


def _project_name_td_index(n_cells: int) -> int:
    """Column E (project name) td index."""
    return 4 if n_cells >= 31 else 3


def _owner_td_index(n_cells: int) -> int:
    """Column D (owner) td index."""
    return 3 if n_cells >= 31 else 2


# ── Status classification (matches Excel COUNTIF wildcards) ───────────────────

def _count_status(text: str) -> dict[str, int]:
    """Classify a status string into counting categories.

    Returns dict with keys matching counting categories.
    Note: 'col_total' and 'row_total' differ per Excel formulas:
    - col_total (column header): COUNTIF("Q*") + COUNTIF("Ideation")
    - row_total (row stats G col): counts ALL non-empty (Q* + Ideation + Done + Run)
    """
    t = text.strip()
    if not t:
        return {}
    c: dict[str, int] = {}
    # Row total counts everything non-empty
    c["row_total"] = 1
    # Column total only counts Q-prefixed and Ideation
    if t.startswith("Q") or t == "Ideation":
        c["col_total"] = 1
    if t == "Ideation":
        c["ideation"] = 1
    if "Pilot" in t:
        c["pilot"] = 1
    if "Scale" in t:
        c["scale"] = 1
    if t == "Run":
        c["run"] = 1
    if t == "Done":
        c["done"] = 1
    return c


def _format_row_stats(counts: dict[str, int]) -> str:
    """Format row-level statistics: 'Total: X   |   Ideation: X   |   ...'"""
    parts = [
        f"Total: {counts.get('row_total', 0)}",
        f"Ideation: {counts.get('ideation', 0)}",
        f"Pilot: {counts.get('pilot', 0)}",
        f"Scale: {counts.get('scale', 0)}",
        f"Run: {counts.get('run', 0)}",
        f"Done: {counts.get('done', 0)}",
    ]
    return "   |   ".join(parts)


# ── Sheet/row lookup helpers ──────────────────────────────────────────────────

def _get_main_div(soup: BeautifulSoup) -> Tag:
    return soup.select(".sheet-content")[1]


def _get_data_rows(main_div: Tag) -> list[Tag]:
    """Return all <tr> in the main table."""
    return main_div.find("table").find_all("tr")


def _get_detail_div(soup: BeautifulSoup, brand_offset: int) -> Tag | None:
    """Get the .sheet-content div for a brand detail sheet."""
    sheet_idx = _DETAIL_SHEET_START + brand_offset
    divs = soup.select(".sheet-content")
    if sheet_idx < len(divs):
        return divs[sheet_idx]
    return None


def _tab_names(soup: BeautifulSoup) -> list[str]:
    return [b.get_text(strip=True) for b in soup.select(".tab-btn")]


def _sheet_name_to_brand_offset(soup: BeautifulSoup, sheet_name: str) -> int | None:
    """If sheet_name is a brand detail sheet, return its brand_offset (0-22)."""
    tabs = _tab_names(soup)
    for i, name in enumerate(tabs):
        if name.lower().strip() == sheet_name.lower().strip():
            if _DETAIL_SHEET_START <= i <= _DETAIL_SHEET_END:
                return i - _DETAIL_SHEET_START
            return None
    return None


def _is_main_sheet(sheet_name: str) -> bool:
    return sheet_name.lower().strip() == MAIN_SHEET.lower().strip()


# ── Core recalculation functions ──────────────────────────────────────────────

def recalc_row_stats(main_div: Tag, row_idx: int) -> None:
    """Recalculate column G statistics for a single data row.

    Scans all 23 brand columns and rebuilds the stats text.
    """
    rows = _get_data_rows(main_div)
    if row_idx < 0 or row_idx >= len(rows):
        return
    row = rows[row_idx]
    cells = row.find_all(["td", "th"])
    n = len(cells)
    if n < 28:  # not a data row
        return

    # Aggregate across all brand columns
    totals: dict[str, int] = {}
    for brand_off in range(_NUM_BRANDS):
        td_idx = _brand_td_index(n, brand_off)
        if td_idx < len(cells):
            status = cells[td_idx].get_text(strip=True)
            for k, v in _count_status(status).items():
                totals[k] = totals.get(k, 0) + v

    # Write to G column
    g_idx = _stats_td_index(n)
    if g_idx < len(cells):
        cells[g_idx].clear()
        cells[g_idx].append(_format_row_stats(totals))


def recalc_column_stats(main_div: Tag, brand_offset: int) -> None:
    """Recalculate column header statistics (rows 2-4) for one brand.

    Scans all data rows for this brand column and updates:
    - Row 2: "Total: N"
    - Row 3: "Ideation: N"
    - Row 4: "Pilot: X   |   Scale: Y"
    """
    rows = _get_data_rows(main_div)
    if len(rows) < _HEADER_ROWS:
        return

    # Count statuses across all data rows for this brand
    totals: dict[str, int] = {}
    for row_idx in range(_HEADER_ROWS, len(rows)):
        cells = rows[row_idx].find_all(["td", "th"])
        n = len(cells)
        if n < 28:
            continue
        td_idx = _brand_td_index(n, brand_offset)
        if td_idx < len(cells):
            status = cells[td_idx].get_text(strip=True)
            for k, v in _count_status(status).items():
                totals[k] = totals.get(k, 0) + v

    # Find the correct td in header rows for this brand
    # Header rows always have 31 cells (no rowspan offset)
    def _set_header(row_idx: int, text: str):
        cells = rows[row_idx].find_all(["td", "th"])
        td_idx = 7 + brand_offset  # headers always 31 cells, brands at 7+
        if td_idx < len(cells):
            cells[td_idx].clear()
            cells[td_idx].append(text)

    _set_header(_STATS_ROW_TOTAL, f"Total: {totals.get('col_total', 0)}")
    _set_header(_STATS_ROW_IDEATION, f"Ideation: {totals.get('ideation', 0)}")
    _set_header(_STATS_ROW_PILOT,
                f"Pilot: {totals.get('pilot', 0)}   |   Scale: {totals.get('scale', 0)}")


def _collect_brand_projects(main_div: Tag, brand_offset: int) -> list[dict]:
    """FILTER equivalent: collect all projects where brand status is non-empty.

    Returns list of dicts with keys: main_row_idx, project_name, owner, status.
    """
    rows = _get_data_rows(main_div)
    projects = []
    for row_idx in range(_HEADER_ROWS, len(rows)):
        cells = rows[row_idx].find_all(["td", "th"])
        n = len(cells)
        if n < 28:
            continue
        td_idx = _brand_td_index(n, brand_offset)
        if td_idx >= len(cells):
            continue
        status = cells[td_idx].get_text(strip=True)
        if not status:
            continue
        pn_idx = _project_name_td_index(n)
        ow_idx = _owner_td_index(n)
        projects.append({
            "main_row_idx": row_idx,
            "project_name": cells[pn_idx].get_text(strip=True) if pn_idx < len(cells) else "",
            "owner": cells[ow_idx].get_text(strip=True) if ow_idx < len(cells) else "",
            "status": status,
        })
    return projects


def recalc_detail_sheet(soup: BeautifulSoup, main_div: Tag, brand_offset: int) -> None:
    """Regenerate a brand detail sheet's left-section data rows from main table.

    Preserves right-section cells (in-house projects) and adds data-main-row
    attributes for reverse sync.
    """
    detail_div = _get_detail_div(soup, brand_offset)
    if detail_div is None:
        return
    table = detail_div.find("table")
    if table is None:
        return
    rows = table.find_all("tr")
    if len(rows) <= _DETAIL_DATA_START_ROW:
        return

    # Collect new left-section data from main table
    projects = _collect_brand_projects(main_div, brand_offset)

    # Detect right section: columns beyond the left section (col 5+)
    right_start = None
    if _DETAIL_HEADER_ROW < len(rows):
        header_cells = rows[_DETAIL_HEADER_ROW].find_all(["td", "th"])
        for j in range(_DETAIL_LEFT_COLS, len(header_cells)):
            if header_cells[j].get_text(strip=True):
                right_start = j
                break

    # 可重建区域 = 表头下方"连续的项目行块"(project-name 列 td[1] 连续非空),
    # 到第一处空行为止。项目块之后的内容(空行、右侧 in-house 标注行、自由备注块等)
    # 不属于重建范围,必须原样保留 —— 否则会误删项目列表以下的内容。
    data_rows = []
    for _dr in rows[_DETAIL_DATA_START_ROW:]:
        _c = _dr.find_all(["td", "th"])
        if len(_c) > 1 and _c[1].get_text(strip=True):
            data_rows.append(_dr)
        else:
            break

    # Extract Maison Name values from existing data rows (keyed by project name)
    maison_names: dict[str, str] = {}
    for dr in data_rows:
        cells = dr.find_all(["td", "th"])
        if len(cells) > _DETAIL_MAISON_NAME_COL:
            pname = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            mname = cells[_DETAIL_MAISON_NAME_COL].get_text(strip=True)
            if pname and mname:
                maison_names[pname] = mname

    # Extract right-section cells, only preserving rows with actual content
    right_data: list[list[Tag]] = []
    n_cols = len(rows[_DETAIL_HEADER_ROW].find_all(["td", "th"])) if _DETAIL_HEADER_ROW < len(rows) else 8
    for dr in data_rows:
        cells = dr.find_all(["td", "th"])
        if right_start is not None and right_start < len(cells):
            right_cells = [c.extract() for c in cells[right_start:]]
            if any(cell.get_text(strip=True) for cell in right_cells):
                right_data.append(right_cells)

    # Capture style template from first data row (or header row)
    style_template = ""
    if data_rows:
        sample_cells = data_rows[0].find_all(["td", "th"])
        if sample_cells:
            style_template = sample_cells[0].get("style", "")
    if not style_template and _DETAIL_HEADER_ROW < len(rows):
        sample_cells = rows[_DETAIL_HEADER_ROW].find_all(["td", "th"])
        if len(sample_cells) > 1:
            style_template = sample_cells[1].get("style", "")

    # Remove all existing data rows
    for dr in data_rows:
        dr.decompose()

    # Build new data rows
    n_new = max(len(projects), len(right_data))
    last_tr = rows[_DETAIL_DATA_START_ROW - 1] if _DETAIL_DATA_START_ROW <= len(rows) else table
    for i in range(n_new):
        tr = soup.new_tag("tr")

        # Left section: [empty, project_name, owner, status, maison_name]
        if i < len(projects):
            p = projects[i]
            tr["data-main-row"] = str(p["main_row_idx"])
            mname = maison_names.get(p["project_name"], "")
            left_vals = ["", p["project_name"], p["owner"], p["status"], mname]
        else:
            left_vals = [""] * _DETAIL_LEFT_COLS

        # Pad to right_start if there are gap columns between left section and right section
        left_count = right_start if right_start is not None else n_cols
        while len(left_vals) < left_count:
            left_vals.append("")

        for val in left_vals:
            td = soup.new_tag("td")
            if style_template:
                td["style"] = style_template
            if val:
                td.append(val)
            tr.append(td)

        # Right section: reattach preserved cells
        if i < len(right_data):
            for rc in right_data[i]:
                tr.append(rc)
        elif right_start is not None:
            right_count = n_cols - left_count
            for _ in range(right_count):
                td = soup.new_tag("td")
                if style_template:
                    td["style"] = style_template
                tr.append(td)

        last_tr.insert_after(tr)
        last_tr = tr

    # Update detail sheet header stats (row 3: Total matches column header)
    # Excel formula: C4 = 'Maison Dimension Tracking Table'!H3 (column Total)
    if _DETAIL_STATS_ROW < len(rows):
        stats_cells = rows[_DETAIL_STATS_ROW].find_all(["td", "th"])
        if len(stats_cells) > 2:
            # Read column Total from main table header (same as recalc_column_stats)
            main_rows = _get_data_rows(main_div)
            col_total_cell = main_rows[_STATS_ROW_TOTAL].find_all(["td", "th"])[7 + brand_offset]
            col_total_text = col_total_cell.get_text(strip=True)
            stats_cells[2].clear()
            stats_cells[2].append(col_total_text)


def _reverse_sync_to_main(
    soup: BeautifulSoup, main_div: Tag,
    detail_row: Tag, new_status: str, brand_offset: int
) -> int | None:
    """Write a status change from detail sheet back to main table.

    Uses data-main-row attribute if present; falls back to matching by
    project name (detail col 2 text vs main table project name column).

    Returns the main table row index that was updated, or None if no mapping.
    """
    rows = _get_data_rows(main_div)

    # Strategy 1: use data-main-row attribute
    main_row_idx = None
    main_row_str = detail_row.get("data-main-row")
    if main_row_str is not None:
        main_row_idx = int(main_row_str)

    # Strategy 2: fallback — match by project name
    if main_row_idx is None:
        detail_cells = detail_row.find_all(["td", "th"])
        if len(detail_cells) < 2:
            return None
        project_name = detail_cells[1].get_text(strip=True)
        if not project_name:
            return None
        for ri in range(_HEADER_ROWS, len(rows)):
            mc = rows[ri].find_all(["td", "th"])
            n = len(mc)
            if n < 28:
                continue
            pn_idx = _project_name_td_index(n)
            if pn_idx < len(mc) and mc[pn_idx].get_text(strip=True) == project_name:
                main_row_idx = ri
                break

    if main_row_idx is None or main_row_idx < 0 or main_row_idx >= len(rows):
        return None

    main_cells = rows[main_row_idx].find_all(["td", "th"])
    n = len(main_cells)
    td_idx = _brand_td_index(n, brand_offset)
    if td_idx >= len(main_cells):
        return None

    main_cells[td_idx].clear()
    main_cells[td_idx].append(new_status)
    return main_row_idx


# ── Main orchestrator ─────────────────────────────────────────────────────────

def recalculate(soup: BeautifulSoup, sheet_name: str,
                row_1idx: int, col_1idx: int,
                old_value: str, new_value: str) -> bool:
    """Determine if recalculation is needed and execute it.

    Args:
        soup: parsed HTML (already modified by update_cell)
        sheet_name: name of the edited sheet
        row_1idx: 1-indexed row number
        col_1idx: 1-indexed column number
        old_value: previous cell text
        new_value: new cell text

    Returns:
        True if recalculation was performed, False if skipped.
    """
    main_div = _get_main_div(soup)

    if _is_main_sheet(sheet_name):
        row_idx = row_1idx - 1  # convert to 0-indexed
        if row_idx < _HEADER_ROWS:
            return False  # editing header rows, no recalc

        # Check if this is a brand status column
        rows = _get_data_rows(main_div)
        if row_idx >= len(rows):
            return False
        cells = rows[row_idx].find_all(["td", "th"])
        n = len(cells)
        if n < 28:
            return False

        # Determine brand_offset from col_1idx
        # col_1idx is 1-indexed logical column (accounting for colspan)
        # For brand columns: col_1idx 8-30 (31-cell) or 7-29 (30-cell)
        first_brand_col = 8 if n >= 31 else 7
        last_brand_col = first_brand_col + _NUM_BRANDS - 1
        if col_1idx < first_brand_col or col_1idx > last_brand_col:
            return False  # not a brand status column

        brand_offset = col_1idx - first_brand_col

        # Recalculate
        recalc_row_stats(main_div, row_idx)
        recalc_column_stats(main_div, brand_offset)
        recalc_detail_sheet(soup, main_div, brand_offset)
        return True

    # Check if this is a brand detail sheet
    brand_offset = _sheet_name_to_brand_offset(soup, sheet_name)
    if brand_offset is None:
        return False

    # Check if editing the status column in a data row
    # Detail sheet status is at 1-indexed col 4 (td index 3)
    if col_1idx != 4:
        return False
    row_idx = row_1idx - 1  # 0-indexed
    if row_idx < _DETAIL_DATA_START_ROW:
        return False

    # Get the detail row
    detail_div = _get_detail_div(soup, brand_offset)
    if detail_div is None:
        return False
    detail_rows = detail_div.find("table").find_all("tr")
    if row_idx >= len(detail_rows):
        return False
    detail_row = detail_rows[row_idx]

    # Reverse sync: write new status back to main table
    main_row_idx = _reverse_sync_to_main(
        soup, main_div, detail_row, new_value, brand_offset
    )
    if main_row_idx is None:
        return False

    # Recalculate everything
    recalc_row_stats(main_div, main_row_idx)
    recalc_column_stats(main_div, brand_offset)
    recalc_detail_sheet(soup, main_div, brand_offset)
    return True


# ── Full recalculation (entry-point agnostic) ─────────────────────────────────

def recalculate_all(soup: BeautifulSoup) -> dict:
    """Recalculate ALL statistics across the entire workbook.

    Entry-point agnostic — can be called after any edit method (API or raw HTML).
    Recalculates:
      - Every row's G-column statistics (row stats)
      - Every brand's column header statistics (col stats)
      - Every brand's detail sheet (sync from main table)

    Returns a summary dict with counts of what was recalculated.
    """
    main_div = _get_main_div(soup)
    rows = _get_data_rows(main_div)

    rows_recalced = 0
    brands_recalced = 0
    sheets_recalced = 0

    # 1. Recalculate every data row's G-column statistics
    for row_idx in range(_HEADER_ROWS, len(rows)):
        cells = rows[row_idx].find_all(["td", "th"])
        if len(cells) < 28:
            continue
        recalc_row_stats(main_div, row_idx)
        rows_recalced += 1

    # 2. Recalculate every brand's column header statistics + detail sheet
    for brand_offset in range(_NUM_BRANDS):
        recalc_column_stats(main_div, brand_offset)
        brands_recalced += 1

        detail_div = _get_detail_div(soup, brand_offset)
        if detail_div is not None:
            recalc_detail_sheet(soup, main_div, brand_offset)
            sheets_recalced += 1

    return {
        "rows_recalculated": rows_recalced,
        "brands_recalculated": brands_recalced,
        "detail_sheets_recalculated": sheets_recalced,
    }


# ── Detail sheet header migration ─────────────────────────────────────────────

def migrate_detail_headers(soup: BeautifulSoup) -> dict:
    """One-time migration: ensure all detail sheets have 'Maison Name' at col 4.

    Handles three cases:
    - Loro Piana: move existing Maison name data from col 5 to col 4
    - Brands with Remark at col 4: insert Maison Name before it, shift right
    - Brands with empty col 4: set header to 'Maison Name'

    Returns summary of changes.
    """
    tabs = [t.get_text(strip=True) for t in soup.select(".tab-btn")]
    contents = soup.select(".sheet-content")
    migrated = []

    for brand_offset in range(_NUM_BRANDS):
        sheet_idx = _DETAIL_SHEET_START + brand_offset
        if sheet_idx >= len(contents):
            continue
        div = contents[sheet_idx]
        table = div.find("table")
        if table is None:
            continue
        all_rows = table.find_all("tr")
        if len(all_rows) <= _DETAIL_HEADER_ROW:
            continue

        brand_name = tabs[sheet_idx] if sheet_idx < len(tabs) else f"brand_{brand_offset}"
        header_row = all_rows[_DETAIL_HEADER_ROW]
        header_cells = header_row.find_all(["td", "th"])

        if len(header_cells) <= _DETAIL_MAISON_NAME_COL:
            # Header has fewer than 5 cols — append Maison Name cell
            new_td = soup.new_tag("td")
            style = header_cells[-1].get("style", "") if header_cells else ""
            if style:
                new_td["style"] = style
            new_td.append("Maison Name")
            header_row.append(new_td)
            # Also add empty cell to non-data rows (rows 0-6) to keep column count aligned
            for r_idx in range(min(_DETAIL_DATA_START_ROW, len(all_rows))):
                if r_idx == _DETAIL_HEADER_ROW:
                    continue
                r = all_rows[r_idx]
                pad_td = soup.new_tag("td")
                if style:
                    pad_td["style"] = style
                r.append(pad_td)
            migrated.append(f"{brand_name}: appended Maison Name header (was {len(header_cells)} cols)")
            continue

        col4_text = header_cells[_DETAIL_MAISON_NAME_COL].get_text(strip=True)

        if col4_text.lower() in ("maison name", "maison_name"):
            continue  # already migrated

        # Check if Maison Name exists further right (Loro Piana case)
        old_maison_col = None
        for j in range(_DETAIL_MAISON_NAME_COL + 1, len(header_cells)):
            if header_cells[j].get_text(strip=True).lower() in ("maison name", "maison_name"):
                old_maison_col = j
                break

        if old_maison_col is not None:
            # Move Maison Name data from old column to col 4
            # Extract data from data rows first
            data_rows = all_rows[_DETAIL_DATA_START_ROW:]
            for dr in data_rows:
                cells = dr.find_all(["td", "th"])
                if len(cells) > old_maison_col:
                    mname_text = cells[old_maison_col].get_text(strip=True)
                    # Put into col 4
                    if len(cells) > _DETAIL_MAISON_NAME_COL:
                        cells[_DETAIL_MAISON_NAME_COL].clear()
                        if mname_text:
                            cells[_DETAIL_MAISON_NAME_COL].append(mname_text)
                    # Clear old column
                    cells[old_maison_col].clear()
            # Update headers
            header_cells[_DETAIL_MAISON_NAME_COL].clear()
            header_cells[_DETAIL_MAISON_NAME_COL].append("Maison Name")
            header_cells[old_maison_col].clear()
            migrated.append(f"{brand_name}: moved Maison Name from col {old_maison_col} to col 4")

        elif col4_text.lower() in ("remark", "remarks"):
            # Insert new Maison Name cell before Remark, shifting Remark right
            new_td = soup.new_tag("td")
            style = header_cells[_DETAIL_MAISON_NAME_COL].get("style", "")
            if style:
                new_td["style"] = style
            new_td.append("Maison Name")
            header_cells[_DETAIL_MAISON_NAME_COL].insert_before(new_td)
            # Insert matching cells in rows 0-6 (non-data rows) and data rows
            for r_idx, r in enumerate(all_rows):
                if r_idx == _DETAIL_HEADER_ROW:
                    continue  # already handled
                cells = r.find_all(["td", "th"])
                if len(cells) > _DETAIL_MAISON_NAME_COL:
                    empty_td = soup.new_tag("td")
                    if style:
                        empty_td["style"] = style
                    cells[_DETAIL_MAISON_NAME_COL].insert_before(empty_td)
            migrated.append(f"{brand_name}: inserted Maison Name before Remark")

        else:
            # Empty or unknown col 4 — just set the header text
            header_cells[_DETAIL_MAISON_NAME_COL].clear()
            header_cells[_DETAIL_MAISON_NAME_COL].append("Maison Name")
            migrated.append(f"{brand_name}: set col 4 header to Maison Name")

    return {"migrated": len(migrated), "details": migrated}
