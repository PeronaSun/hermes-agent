# Write Operations Test Cases (Batch 2)

> Tests for `update_project` / `update_maison` / `delete_project` / `delete_maison` / `add_status`.
> Complements the first-batch test cases in `references/test-cases.json`.

## Test Data Convention

All test objects use `zz_` prefix. **Only delete `zz_`-prefixed objects** — deletes are hard and irreversible (though snapshots are taken).

## Setup — Create Ephemeral Test Data

| # | Prompt | Expected |
|---|--------|----------|
| S1 | 新增项目 id=zz_test_proj_2026, name=ZZ 测试项目, domain=Retail, owner=你的名字 | `add_project`; changelog +1 (create); actor=you |
| S2 | 给 zz_test_proj_2026 的 Louis Vuitton 设成 Q3-Pilot, 备注 setup cell | `update_status` (created=true) + remark |
| S3 | 新增品牌 id=zz_test_maison_2026, name=ZZ 测试品牌 | `add_maison`; changelog +1 |
| S4 | 给 zz_test_proj_2026 的 ZZ 测试品牌 设成 Ideation | Creates a status cell for later cascade testing |

## H. update_project — Partial Metadata Update

| # | Prompt | Expected |
|---|--------|----------|
| H1 | 改 zz_test_proj_2026 owner → 张三 | Only `owner` field changes; changelog `changes` has only owner |
| H2 | 改 domain → Beauty, priority → P1 | Two fields in one changelog entry; name/owner/description untouched |
| H3 | 清空 description | Empty string `""` = clear field; changelog records old→"" |
| H4 | 再改 owner → 张三 (same value) | **no-op**: no changelog entry, reply "无变化" |
| H5 | 改 id → zz_new_id | **Rejected**: id is a foreign key, cannot be changed |
| H6 | 改不存在的项目 | `unknown project_id` error, no write |

**Key rules:**
- Partial update: only pass fields to change; omit = don't touch; `""` = clear
- `id` is immutable (it's a foreign key across status records)
- Same-value = no-op, no changelog pollution

## I. update_maison — Symmetric to update_project

| # | Prompt | Expected |
|---|--------|----------|
| I1 | 改 ZZ 测试品牌 division → Beauty Division | Only division changes |
| I2 | 改 ai_champion → 李四, key_account → 王五 | Two fields, one changelog entry |
| I3 | 改 division → same value | no-op |

Maison editable fields: `name`, `division`, `ai_champion`, `key_account`.

## J. delete_project — Two-Step Confirmation Required

| # | Prompt | Expected |
|---|--------|----------|
| J1 | 删不存在的项目 foo_bar_nope | `unknown project_id`, **no confirmation flow** |
| J2 | 删 zz_test_proj_2026 | **Don't delete yet**: report N cells to cascade, ask for confirmation |
| J3 | 确认删除 | Now delete: projects row + all status cells; changelog +1 with full old-value snapshot; `data/backups/*.bak.<timestamp>` created |
| J4 | 查 zz_test_proj_2026 | Not found, zero residual cells |

**Critical:** First call without `confirm` only reports dependencies. Must get explicit user confirmation, then call again with `confirm=<project_id>`.

## K. delete_maison — Cascade + Orphan Image Protection

| # | Prompt | Expected |
|---|--------|----------|
| K1 | (Optional) Add image to test maison gallery | `add_image` for cascade testing |
| K2 | 删 zz_test_maison_2026 | Don't delete: report N cells + M images, ask confirmation |
| K3 | 确认删除 | Delete: maisons/cells/budget/images rows; **orphan protection** for shared image files; returns `deleted_cells`/`deleted_images`/`files_deleted`/`file_delete_errors` |
| K4 | 搜 ZZ 测试品牌 | Not found |

**Orphan protection:** Physical image files are only deleted if no other maison references them. `file_deleted: false` = shared image, reference dropped only.

## L. add_status — Dual-Table Write

| # | Prompt | Expected |
|---|--------|----------|
| L1 | 新增状态 Run (already exists) | Rejected: `status already exists` |
| L2 | 新增 ZZ-Hold, 类别 pilot, 描述 暂缓中 | Both `status_config` AND `status_legend` get new row; changelog +1 |
| L3 | Set a cell to ZZ-Hold | `update_status` **accepts** the new status immediately |

**Key:** `add_status` writes to both `status_config.json` and `status_legend.json`. After that, `update_status` accepts the new status value.

## M. Cross-Cutting Edge Cases

| # | Expected |
|---|----------|
| M1 | `actor` in changelog is always the Feishu user's name, never the OS username |
| M2 | Ambiguous names (especially Dior: couture vs perfume) → **ask user first**, never guess |

## Terminal Verification Commands

```bash
DATA=~/.hermes/project_tracking_portal/portal/data

# Data integrity
python3 ~/.hermes/skills/productivity/project-tracking-portal/scripts/validate_portal_data.py ~/.hermes/project_tracking_portal

# Recent audit entries
python3 -c "import json;[print(e['timestamp'],e['actor'],e['action'],e.get('project_id'),e.get('maison_id'),[c['field'] for c in e.get('changes',[])]) for e in json.load(open('$DATA/changelog.json'))[-15:]]"

# Check backup snapshots exist
ls -la $DATA/backups/ 2>/dev/null || echo "(no backups yet)"

# Verify clean deletion (replace zz_test_proj_2026)
python3 -c "import json;d='$DATA';print('projects has?', any(r['id']=='zz_test_proj_2026' for r in json.load(open(d+'/projects.json'))));print('residual cells:', sum(1 for r in json.load(open(d+'/project_maison_status.json')) if r['project_id']=='zz_test_proj_2026'))"
```
