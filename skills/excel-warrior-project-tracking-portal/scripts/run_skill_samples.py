#!/usr/bin/env python3
"""Dry-run the Project Tracking Portal skill sample cases on copied data."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ALIASES = {
    "pcd": "perfume_christine_dior",
    "perfume dior": "perfume_christine_dior",
    "perfume christine dior": "perfume_christine_dior",
}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def load(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save(path: Path, data) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def prepare_workspace(repo: Path) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    dst = Path(tmp.name) / "portal"
    shutil.copytree(repo / "portal" / "data", dst / "data")
    shutil.copytree(repo / "portal" / "images", dst / "images")
    return tmp


def resolve(items, query: str):
    alias = ALIASES.get(query.lower().strip())
    if alias:
        return [item for item in items if item.get("id") == alias]
    qn = norm(query)
    exact_id = [item for item in items if norm(item.get("id", "")) == qn]
    if exact_id:
        return exact_id
    exact_name = [item for item in items if norm(item.get("name", "")) == qn]
    if exact_name:
        return exact_name
    return [item for item in items if qn and qn in norm(item.get("name", ""))]


def file_snapshot(data_dir: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(data_dir.glob("*.json"))}


def changed_files(before: dict[str, str], data_dir: Path) -> list[str]:
    after = file_snapshot(data_dir)
    return [name for name in sorted(after) if after[name] != before.get(name)]


def update_status(data_dir: Path, project_query: str, maison_query: str, status: str, remark=None):
    projects = load(data_dir / "projects.json")
    maisons = load(data_dir / "maisons.json")
    records = load(data_dir / "project_maison_status.json")
    statuses = {row["status"] for row in load(data_dir / "status_config.json")}

    project_matches = resolve(projects, project_query)
    maison_matches = resolve(maisons, maison_query)
    if len(project_matches) != 1:
        return {"ok": False, "reason": f"project matches={len(project_matches)}"}
    if len(maison_matches) != 1:
        return {"ok": False, "reason": f"maison matches={len(maison_matches)}"}
    if status not in statuses:
        return {"ok": False, "reason": f"invalid status: {status}"}

    pid = project_matches[0]["id"]
    mid = maison_matches[0]["id"]
    rec = next((r for r in records if r["project_id"] == pid and r["maison_id"] == mid), None)
    if rec is None:
        records.append({"project_id": pid, "maison_id": mid, "status": status,
                        "remark": remark or "", "updated_at": "2026-06-16"})
    else:
        rec["status"] = status
        if remark is not None:
            rec["remark"] = remark
        rec["updated_at"] = "2026-06-16"
    save(data_dir / "project_maison_status.json", records)
    return {"ok": True, "project_id": pid, "maison_id": mid}


def update_remark(data_dir: Path, project_query: str, maison_query: str, remark: str):
    projects = load(data_dir / "projects.json")
    maisons = load(data_dir / "maisons.json")
    records = load(data_dir / "project_maison_status.json")
    project_matches = resolve(projects, project_query)
    maison_matches = resolve(maisons, maison_query)
    if len(project_matches) != 1:
        return {"ok": False, "reason": f"project matches={len(project_matches)}"}
    if len(maison_matches) != 1:
        return {"ok": False, "reason": f"maison matches={len(maison_matches)}"}
    pid = project_matches[0]["id"]
    mid = maison_matches[0]["id"]
    rec = next((r for r in records if r["project_id"] == pid and r["maison_id"] == mid), None)
    if rec is None:
        return {"ok": False, "reason": "record missing"}
    rec["remark"] = remark
    rec["updated_at"] = "2026-06-16"
    save(data_dir / "project_maison_status.json", records)
    return {"ok": True, "project_id": pid, "maison_id": mid}


def validate(repo: Path, root: Path) -> None:
    script = repo / "skills" / "project-tracking-portal" / "scripts" / "validate_portal_data.py"
    result = subprocess.run([sys.executable, str(script), str(root)], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)


def run_case(repo: Path, case_id: str) -> None:
    with prepare_workspace(repo) as tmp:
        root = Path(tmp)
        data_dir = root / "portal" / "data"
        before = file_snapshot(data_dir)

        if case_id == "status-clear-project-and-maison":
            result = update_status(data_dir, "Gen AI Client Knowledge", "Tiffany", "Q3-Pilot")
            assert result["ok"], result
            assert changed_files(before, data_dir) == ["project_maison_status.json"]
            validate(repo, root)
        elif case_id == "remark-only":
            result = update_remark(data_dir, "Enrich project scaling", "Guerlain",
                                   "Strong interest by HQ - dry run")
            assert result["ok"], result
            assert changed_files(before, data_dir) == ["project_maison_status.json"]
            validate(repo, root)
        elif case_id == "maison-alias-pcd":
            result = update_status(data_dir, "Enrich project scaling", "PCD", "Run")
            assert result["ok"], result
            assert result["maison_id"] == "perfume_christine_dior"
            assert changed_files(before, data_dir) == ["project_maison_status.json"]
            validate(repo, root)
        elif case_id == "invalid-status":
            result = update_status(data_dir, "Gen AI Client Knowledge", "Tiffany", "In Progress")
            assert not result["ok"], result
            assert changed_files(before, data_dir) == []
        elif case_id == "missing-project":
            result = update_status(data_dir, "Magic Unicorn Project", "Tiffany", "Run")
            assert not result["ok"], result
            assert changed_files(before, data_dir) == []
        elif case_id == "ambiguous-project":
            projects = load(data_dir / "projects.json")
            maisons = load(data_dir / "maisons.json")
            assert len(resolve(projects, "DREAM")) >= 1
            assert len(resolve(maisons, "")) == 0
            assert changed_files(before, data_dir) == []
        else:
            raise AssertionError(f"unknown case: {case_id}")
        print(f"OK {case_id}")


def main(argv: list[str]) -> int:
    repo = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd().resolve()
    cases = [
        "status-clear-project-and-maison",
        "remark-only",
        "maison-alias-pcd",
        "ambiguous-project",
        "invalid-status",
        "missing-project",
    ]
    for case_id in cases:
        run_case(repo, case_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
