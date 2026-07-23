# Feishu Base DIM Table Orphan Cleanup

## When to use

After renaming an owner, deleting a project/maison, or any metadata change that
removes a name from JSON, `resync_feishu` will NOT delete the now-orphaned DIM
record from the Feishu Base. Use this procedure to clean up stale DIM entries.

## DIM table structure

| DIM Field    | Table ID               | Name Field   |
|-------------|------------------------|-------------|
| Owner       | `tbljEVC31aBpOgGY`     | Owner       |
| Maison      | `tblSKfzLE9uciUW3`     | Maison      |
| Group Project | `tblIof2SnozwVXHR`   | AI Project  |
| Project Type| `tblSdHSggWYoi2To`     | Project Type|

FACT table: `tblacI2fJ1ZbVf6I`

## Cleanup procedure

```python
import sys, json
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from feishu_bitable import _config, FeishuBitableClient, DIM_TABLES, FACT_TABLE, _cell_text

cfg = _config()
client = FeishuBitableClient(cfg)

# 1. List all DIM records for the target field (e.g. Owner)
field = "Owner"  # or "Maison", "Group Project", "Project Type"
dim_table_id, name_field = DIM_TABLES[field]
dim_records = client.list_all(dim_table_id)

print(f"All {field} records ({len(dim_records)}):")
for rec in dim_records:
    name = _cell_text((rec.fields or {}).get(name_field))
    print(f"  {rec.record_id}: '{name}'")

# 2. Collect all names still referenced by FACT rows
fact_records = client.list_all(FACT_TABLE)
referenced_names = set()
for rec in fact_records:
    f = rec.fields or {}
    val = f.get(field)
    # Link fields come back as [{"text": "...", "record_id": "..."}]
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                t = item.get("text", "")
                if t:
                    referenced_names.add(t)

print(f"\nReferenced {field} names in FACT: {sorted(referenced_names)}")

# 3. Identify orphans (DIM records not referenced by any FACT row)
orphans = []
for rec in dim_records:
    name = _cell_text((rec.fields or {}).get(name_field))
    if name and name not in referenced_names:
        orphans.append((rec.record_id, name))

print(f"\nOrphaned {field} records ({len(orphans)}):")
for rid, name in orphans:
    print(f"  {rid}: '{name}'")

# 4. Delete orphans (after confirming with user)
for rid, name in orphans:
    try:
        client.batch_delete(dim_table_id, [rid])
        print(f"✅ Deleted '{name}' ({rid})")
    except Exception as e:
        print(f"❌ Failed to delete '{name}': {e}")
```

## Important notes

- **Always list first, confirm with user before deleting.** Orphaned DIM records
  don't affect data integrity but users may want to keep some for reference.
- **Cross-check with JSON**: Compare DIM names against the current JSON data
  (`projects.json` for owners, `maisons.json` for maisons) to ensure consistency.
- **FACT link fields**: Owner/Maison/Group Project/Project Type are link fields in
  FACT, stored as `[{"text": "...", "record_id": "..."}]`. Use `_cell_text()` to
  extract the display name for comparison.
- **`resync_feishu` never deletes DIM records** — this is by design (the sync is
  additive for DIMs to avoid breaking references). Manual cleanup is the
  intended path for orphaned DIM entries.
