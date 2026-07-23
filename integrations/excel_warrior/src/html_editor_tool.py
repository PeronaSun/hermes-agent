"""
HTML Table Editor Tool — designed to be registered as a Hermes Agent tool.

Usage: register `TOOL_DEFINITION` and `html_table_editor` into your agent's tool list.
"""

from bs4 import BeautifulSoup
import json
import re

HTML_FILE_PATH = "./Project Tracking V3.html"


# ── Core implementation ────────────────────────────────────────────────────────

def _load_html(path: str) -> BeautifulSoup:
    with open(path, "r", encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "html.parser")


def _save_html(soup: BeautifulSoup, path: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(soup))


def _log_preview(user_locations=None):
    """在 _save_html 之后调用;返回 preview dict 或 None。日志失败绝不影响主编辑功能。"""
    try:
        import change_logger
        change_logger.init_db()
        return change_logger.preview_change_log(source="tool", user_locations=user_locations)
    except Exception as e:
        import sys
        print(f"[change_log warning] {e}", file=sys.stderr)
        return None


def _get_sheet_div(soup: BeautifulSoup, sheet_name: str):
    """Find the sheet <div> by matching the tab button text."""
    tabs = soup.select(".tab-btn")
    for i, tab in enumerate(tabs):
        if tab.get_text(strip=True).lower() == sheet_name.lower():
            divs = soup.select(".sheet-content")
            if i < len(divs):
                return divs[i]
    return None


def _get_all_sheets(soup: BeautifulSoup) -> list[str]:
    return [tab.get_text(strip=True) for tab in soup.select(".tab-btn")]


def read_cell(sheet_name: str, row: int, col: int) -> dict:
    """Read the current value of a cell (1-indexed)."""
    soup = _load_html(HTML_FILE_PATH)
    div = _get_sheet_div(soup, sheet_name)
    if not div:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    rows = div.select("tbody tr")
    if row < 1 or row > len(rows):
        return {"ok": False, "error": f"Row {row} out of range (1–{len(rows)})"}
    cells = rows[row - 1].find_all(["td", "th"])
    # Account for colspan — build a logical column map
    col_map = []
    for cell in cells:
        colspan = int(cell.get("colspan", 1))
        col_map.extend([cell] * colspan)
    if col < 1 or col > len(col_map):
        return {"ok": False, "error": f"Col {col} out of range (1–{len(col_map)})"}
    cell = col_map[col - 1]
    return {"ok": True, "value": cell.get_text(separator="\n"), "html": str(cell)}


def update_cell(sheet_name: str, row: int, col: int, new_value: str) -> dict:
    """Replace the text content of a specific cell (1-indexed)."""
    soup = _load_html(HTML_FILE_PATH)
    div = _get_sheet_div(soup, sheet_name)
    if not div:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    rows = div.select("tbody tr")
    if row < 1 or row > len(rows):
        return {"ok": False, "error": f"Row {row} out of range (1–{len(rows)})"}
    cells = rows[row - 1].find_all(["td", "th"])
    col_map = []
    for cell in cells:
        colspan = int(cell.get("colspan", 1))
        col_map.extend([cell] * colspan)
    if col < 1 or col > len(col_map):
        return {"ok": False, "error": f"Col {col} out of range (1–{len(col_map)})"}
    target = col_map[col - 1]
    old_value = target.get_text(separator="\n")
    target.clear()
    target.append(new_value)
    # Auto-recalculate statistics and sync detail sheets
    recalculated = False
    try:
        from recalculate import recalculate as _recalc
        recalculated = _recalc(soup, sheet_name, row, col, old_value, new_value)
    except Exception as e:
        import sys
        print(f"[recalc warning] {e}", file=sys.stderr)
    _save_html(soup, HTML_FILE_PATH)
    result = {"ok": True, "old_value": old_value, "new_value": new_value,
              "location": f"{sheet_name}!R{row}C{col}", "recalculated": recalculated}
    preview = _log_preview(user_locations={f"{sheet_name}!R{row}C{col}"})
    if preview:
        result["change_log"] = preview
    return result


def find_and_replace(sheet_name: str, find_text: str, replace_text: str, exact_match: bool = False) -> dict:
    """Find all cells containing find_text in a sheet and replace them."""
    soup = _load_html(HTML_FILE_PATH)
    div = _get_sheet_div(soup, sheet_name)
    if not div:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    cells = div.find_all(["td", "th"])
    matches = []
    for cell in cells:
        text = cell.get_text()
        if (exact_match and text == find_text) or (not exact_match and find_text in text):
            old = cell.get_text()
            cell.clear()
            cell.append(old.replace(find_text, replace_text) if not exact_match else replace_text)
            matches.append(old)
    if not matches:
        return {"ok": False, "error": f"'{find_text}' not found in sheet '{sheet_name}'"}
    # Auto-recalculate after replacement
    recalculated = False
    try:
        from recalculate import recalculate_all as _recalc_all
        _recalc_all(soup)
        recalculated = True
    except Exception as e:
        import sys
        print(f"[recalc warning] {e}", file=sys.stderr)
    _save_html(soup, HTML_FILE_PATH)
    result = {"ok": True, "replaced_count": len(matches), "original_values": matches, "recalculated": recalculated}
    preview = _log_preview(user_locations=None)
    if preview:
        result["change_log"] = preview
    return result


def find_cell(sheet_name: str, text: str, exact_match: bool = False) -> dict:
    """Locate cells containing `text` and return their (row, col) coordinates.

    The returned col uses the SAME logical-column scheme as read_cell/update_cell
    (colspan-expanded), so a coordinate from find_cell can be passed straight
    into update_cell without counting columns by hand.
    """
    soup = _load_html(HTML_FILE_PATH)
    div = _get_sheet_div(soup, sheet_name)
    if not div:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found. Available: {_get_all_sheets(soup)}"}
    rows = div.select("tbody tr")
    matches = []
    for r_idx, tr in enumerate(rows, start=1):
        logical_col = 1
        for cell in tr.find_all(["td", "th"]):
            colspan = int(cell.get("colspan", 1))
            cell_text = cell.get_text(separator="\n")
            hit = (cell_text == text) if exact_match else (text in cell_text)
            if hit:
                matches.append({"row": r_idx, "col": logical_col, "value": cell_text})
            logical_col += colspan
    if not matches:
        return {"ok": False, "error": f"'{text}' not found in sheet '{sheet_name}'"}
    return {"ok": True, "count": len(matches), "matches": matches}


def list_sheets() -> dict:
    """List all sheet names in the HTML file."""
    soup = _load_html(HTML_FILE_PATH)
    return {"ok": True, "sheets": _get_all_sheets(soup)}


def read_sheet_range(sheet_name: str, start_row: int, end_row: int, start_col: int, end_col: int) -> dict:
    """Read a rectangular range of cells. Returns a 2D list of values."""
    soup = _load_html(HTML_FILE_PATH)
    div = _get_sheet_div(soup, sheet_name)
    if not div:
        return {"ok": False, "error": f"Sheet '{sheet_name}' not found."}
    rows = div.select("tbody tr")
    result = []
    for r_idx in range(start_row, min(end_row + 1, len(rows) + 1)):
        cells = rows[r_idx - 1].find_all(["td", "th"])
        col_map = []
        for cell in cells:
            colspan = int(cell.get("colspan", 1))
            col_map.extend([cell] * colspan)
        row_vals = []
        for c_idx in range(start_col, min(end_col + 1, len(col_map) + 1)):
            row_vals.append(col_map[c_idx - 1].get_text(separator="\n") if c_idx <= len(col_map) else "")
        result.append(row_vals)
    return {"ok": True, "data": result, "range": f"R{start_row}C{start_col}:R{end_row}C{end_col}"}


def recalculate_all_standalone() -> dict:
    """Recalculate all statistics across the entire workbook.

    Entry-point agnostic — call after any edit (API or raw HTML).
    """
    soup = _load_html(HTML_FILE_PATH)
    from recalculate import recalculate_all as _recalc_all
    result = _recalc_all(soup)
    _save_html(soup, HTML_FILE_PATH)
    out = {"ok": True, **result}
    preview = _log_preview(user_locations=set())
    if preview:
        out["change_log"] = preview
    return out


# ── Dispatcher (called by Hermes Agent) ───────────────────────────────────────

def html_table_editor(action: str, **kwargs) -> str:
    """
    Entry point for the Hermes agent tool call.
    Returns a JSON string.
    """
    drift = None
    try:
        import change_logger
        change_logger.init_db()
        drift = change_logger.ensure_logged()
    except Exception:
        drift = None
    dispatch = {
        "list_sheets": lambda: list_sheets(),
        "find_cell": lambda: find_cell(kwargs["sheet_name"], kwargs["text"], kwargs.get("exact_match", False)),
        "read_cell": lambda: read_cell(kwargs["sheet_name"], kwargs["row"], kwargs["col"]),
        "update_cell": lambda: update_cell(kwargs["sheet_name"], kwargs["row"], kwargs["col"], kwargs["new_value"]),
        "find_and_replace": lambda: find_and_replace(
            kwargs["sheet_name"], kwargs["find_text"], kwargs["replace_text"],
            kwargs.get("exact_match", False)
        ),
        "read_range": lambda: read_sheet_range(
            kwargs["sheet_name"], kwargs["start_row"], kwargs["end_row"],
            kwargs["start_col"], kwargs["end_col"]
        ),
        "recalculate_all": lambda: recalculate_all_standalone(),
        "commit_change_log": lambda: {"ok": True, **__import__("change_logger").commit_change_log(
            kwargs["batch_id"], kwargs.get("why"))},
    }
    if action not in dispatch:
        result = {"ok": False, "error": f"Unknown action '{action}'. Available: {list(dispatch.keys())}"}
    else:
        try:
            result = dispatch[action]()
        except KeyError as e:
            result = {"ok": False, "error": f"Missing required parameter: {e}"}
        except Exception as e:
            result = {"ok": False, "error": str(e)}
    if drift and isinstance(result, dict):
        result["pending_drift"] = drift
    return json.dumps(result, ensure_ascii=False, indent=2)


# ── Tool definition schema (paste into your Hermes agent config) ───────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "html_table_editor",
        "description": (
            "Read or modify cells in a multi-sheet HTML table file. "
            "Use list_sheets to discover sheet names. To locate a value, prefer find_cell "
            "(returns exact row/col coordinates by content) over counting columns yourself. "
            "Then use read_cell to confirm, and update_cell to change."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_sheets", "find_cell", "read_cell", "update_cell", "find_and_replace", "read_range", "recalculate_all", "commit_change_log"],
                    "description": "The operation to perform."
                },
                "text": {
                    "type": "string",
                    "description": "Text to locate (find_cell). Returns row/col coordinates of every matching cell."
                },
                "sheet_name": {
                    "type": "string",
                    "description": "Sheet tab name (case-insensitive). Required for all actions except list_sheets."
                },
                "row": {"type": "integer", "description": "1-indexed row number."},
                "col": {"type": "integer", "description": "1-indexed column number."},
                "new_value": {"type": "string", "description": "New text to write into the cell."},
                "find_text": {"type": "string", "description": "Text to search for (find_and_replace)."},
                "replace_text": {"type": "string", "description": "Text to substitute in (find_and_replace)."},
                "exact_match": {"type": "boolean", "description": "If true, only replace exact full-cell matches."},
                "start_row": {"type": "integer"},
                "end_row": {"type": "integer"},
                "start_col": {"type": "integer"},
                "end_col": {"type": "integer"},
                "batch_id": {"type": "string", "description": "preview 返回的 batch_id;commit_change_log 用它落库。"},
                "why": {"type": "string", "description": "本次修改的原因(commit_change_log 必填,用于审计日志的 why 字段)。"}
            },
            "required": ["action"]
        }
    }
}


# ── Quick test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(html_table_editor("list_sheets"))
    print(html_table_editor("read_cell", sheet_name="Description", row=5, col=3))
