"""Register the migrated excel_warrior capability layer with Bagel.

The plugin deliberately does not start a service or gateway.  It delegates to
the audited source modules copied under ``integrations/excel_warrior`` and is
available only when that isolated data tree is present.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
LAYER_ROOT = RUNTIME_ROOT / "integrations" / "excel_warrior"
PORTAL_ROOT = LAYER_ROOT / "portal"
PROJECT_SCRIPTS = RUNTIME_ROOT / "skills" / "excel-warrior-project-tracking-portal" / "scripts"
MINUTES_SCRIPTS = RUNTIME_ROOT / "skills" / "excel-warrior-meeting-minutes" / "scripts"
LEGACY_SRC = LAYER_ROOT / "src"


def _available() -> bool:
    return (PORTAL_ROOT / "data" / "projects.json").is_file()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _project(args: dict, **_kwargs) -> str:
    sys.path.insert(0, str(PROJECT_SCRIPTS))
    try:
        from portal_data_tool import dispatch
        action = str(args.get("action") or "")
        payload = {k: v for k, v in args.items() if k != "action"}
        return _json(dispatch(PORTAL_ROOT, action, **payload))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})
    finally:
        if sys.path and sys.path[0] == str(PROJECT_SCRIPTS):
            sys.path.pop(0)


def _minutes(args: dict, **_kwargs) -> str:
    for directory in (MINUTES_SCRIPTS, PROJECT_SCRIPTS):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    try:
        from meeting_minutes_tool import dispatch
        action = str(args.get("action") or "")
        payload = {k: v for k, v in args.items() if k != "action"}
        return _json(dispatch(PORTAL_ROOT, action, **payload))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


def _doc_reader(args: dict, **_kwargs) -> str:
    if str(MINUTES_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(MINUTES_SCRIPTS))
    try:
        from feishu_doc_reader import read_doc, read_local_docx
        if args.get("local_path"):
            return _json(read_local_docx(str(args["local_path"])))
        return _json(read_doc(str(args.get("url") or "")))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


def _legacy_html(args: dict, **_kwargs) -> str:
    if str(LEGACY_SRC) not in sys.path:
        sys.path.insert(0, str(LEGACY_SRC))
    try:
        action = str(args.get("action") or "")
        payload = {k: v for k, v in args.items() if k != "action"}
        if action.startswith("image_"):
            from html_image_tool import html_image_editor
            return str(html_image_editor(action.removeprefix("image_"), **payload))
        from html_editor_tool import html_table_editor
        return str(html_table_editor(action, **payload))
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})


PROJECT_SCHEMA = {
    "name": "excel_warrior_project",
    "description": "Search, preview, validate, report, or auditably update migrated Project Tracking Portal data. Writes require actor and record-level writes require reason.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "preview", "update_status", "update_remark", "update_record_meta", "add_project", "add_maison", "update_project", "update_maison", "delete_project", "delete_maison", "add_status", "format_weekly_report", "validate", "resync_feishu", "archive_changelog", "add_image", "remove_image", "list_images"]},
            "query": {"type": "string"}, "entity_type": {"type": "string"}, "limit": {"type": "integer"},
            "project_id": {"type": "string"}, "maison_id": {"type": "string"}, "id": {"type": "string"},
            "name": {"type": "string"}, "status": {"type": "string"}, "remark": {"type": "string"},
            "reason": {"type": "string"}, "actor": {"type": "string"}, "confirm": {"type": "string"},
            "owner": {"type": "string"}, "project_type": {"type": "string"}, "year": {"type": "string"},
            "domain": {"type": "string"}, "priority": {"type": "string"}, "description": {"type": "string"},
            "source_path": {"type": "string"}, "image": {"type": "string"}, "position": {"type": "integer"},
            "since_days": {"type": "integer"}
        },
        "required": ["action"]
    }
}

MINUTES_SCHEMA = {
    "name": "excel_warrior_minutes",
    "description": "Manage the migrated maison-scoped Meeting Minutes wiki archive and local lightweight index. For writes, first use Bagel's deterministic Feishu meeting preview/confirm workflow; do not call add/update/delete before user confirmation.",
    "parameters": {
        "type": "object", "properties": {
            "action": {"type": "string", "enum": ["add_minute", "update_minute", "delete_minute", "list_minutes"]},
            "id": {"type": "string"}, "maison_id": {"type": "string"}, "date": {"type": "string"},
            "date_from": {"type": "string"}, "date_to": {"type": "string"}, "title": {"type": "string"},
            "participants": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"},
            "content": {"type": "string"}, "project_ids": {"type": "array", "items": {"type": "string"}},
            "action_items": {"type": "array", "items": {"type": "object"}}, "note": {"type": "string"},
            "actor": {"type": "string"}
        }, "required": ["action"]
    }
}

DOC_SCHEMA = {
    "name": "excel_warrior_doc_reader",
    "description": "Read a Feishu doc/wiki URL or a locally cached DOCX for meeting import, preserving Bagel's existing Feishu Docx write tools.",
    "parameters": {"type": "object", "properties": {"url": {"type": "string"}, "local_path": {"type": "string"}}}
}

LEGACY_SCHEMA = {
    "name": "excel_warrior_legacy_html",
    "description": "Compatibility access to the audited legacy Excel-exported HTML table and gallery editor. Prefer excel_warrior_project for current JSON data.",
    "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "sheet_name": {"type": "string"}, "row": {"type": "integer"}, "col": {"type": "integer"}, "new_value": {"type": "string"}, "find_text": {"type": "string"}, "replace_text": {"type": "string"}, "index": {"type": "integer"}, "image_url": {"type": "string"}}, "required": ["action"]}
}


def register(ctx) -> None:
    for name, schema, handler, emoji in (
        ("excel_warrior_project", PROJECT_SCHEMA, _project, "📊"),
        ("excel_warrior_minutes", MINUTES_SCHEMA, _minutes, "📝"),
        ("excel_warrior_doc_reader", DOC_SCHEMA, _doc_reader, "📄"),
        ("excel_warrior_legacy_html", LEGACY_SCHEMA, _legacy_html, "🧮"),
    ):
        ctx.register_tool(name=name, toolset="excel_warrior", schema=schema, handler=handler, check_fn=_available, emoji=emoji)
