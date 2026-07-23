#!/usr/bin/env python3
"""Validate Project Tracking Portal JSON data."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd().resolve()
    data_dir = root / "portal" / "data"
    portal_dir = root / "portal"

    required = {
        "projects": "projects.json",
        "maisons": "maisons.json",
        "records": "project_maison_status.json",
        "status_config": "status_config.json",
        "images": "images.json",
        "budget": "maison_budget.json",
        "legend": "status_legend.json",
    }

    errors: list[str] = []
    data = {}
    for key, name in required.items():
        path = data_dir / name
        if not path.exists():
            errors.append(f"missing file: {path}")
            continue
        try:
            data[key] = load_json(path)
        except Exception as exc:
            errors.append(f"invalid JSON in {path}: {exc}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    projects = data["projects"]
    maisons = data["maisons"]
    records = data["records"]
    status_config = data["status_config"]
    images = data["images"]
    budget = data["budget"]
    legend = data["legend"]

    pids = {p.get("id") for p in projects}
    mids = {m.get("id") for m in maisons}
    statuses = {s.get("status") for s in status_config}

    if len(pids) != len(projects):
        errors.append("duplicate project ids")
    if len(mids) != len(maisons):
        errors.append("duplicate maison ids")
    if len(statuses) != len(status_config):
        errors.append("duplicate statuses in status_config")

    combos = Counter((r.get("project_id"), r.get("maison_id")) for r in records)
    duplicate_combos = [k for k, n in combos.items() if n > 1]
    if duplicate_combos:
        errors.append(f"duplicate project/maison records: {len(duplicate_combos)}")

    missing_project_refs = [r for r in records if r.get("project_id") not in pids]
    missing_maison_refs = [r for r in records if r.get("maison_id") not in mids]
    invalid_statuses = [r for r in records if r.get("status") not in statuses]
    if missing_project_refs:
        errors.append(f"missing project refs: {len(missing_project_refs)}")
    if missing_maison_refs:
        errors.append(f"missing maison refs: {len(missing_maison_refs)}")
    if invalid_statuses:
        bad = sorted({r.get("status") for r in invalid_statuses})
        errors.append(f"invalid statuses: {bad}")

    budget_missing_maison = [b for b in budget if b.get("maison_id") not in mids]
    image_missing_maison = [i for i in images if i.get("maison_id") not in mids]
    if budget_missing_maison:
        errors.append(f"budget rows with unknown maison_id: {len(budget_missing_maison)}")
    if image_missing_maison:
        errors.append(f"image rows with unknown maison_id: {len(image_missing_maison)}")

    legend_statuses = {row.get("status") for row in legend}
    legend_unknown = sorted(s for s in legend_statuses if s not in statuses)
    if legend_unknown:
        errors.append(f"legend statuses not in status_config: {legend_unknown}")

    missing_images = []
    for row in images:
        for rel in row.get("images", []):
            if not (portal_dir / rel).exists():
                missing_images.append(rel)
    if missing_images:
        errors.append(f"missing image files: {missing_images[:10]}")

    print("Portal data validation")
    print(f"  projects: {len(projects)}")
    print(f"  maisons: {len(maisons)}")
    print(f"  records: {len(records)}")
    print(f"  statuses: {len(status_config)}")
    print(f"  budget rows: {len(budget)}")
    print(f"  image rows: {len(images)}")
    print(f"  referenced images: {sum(len(row.get('images', [])) for row in images)}")

    if errors:
        print("  result: FAIL")
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print("  result: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
