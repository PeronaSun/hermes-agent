---
name: excel-warrior-project-tracking-portal
description: Use when editing, validating, or reviewing the LVMH Project Tracking Portal JSON data. Handles natural-language requests to update project/maison statuses or remarks safely by resolving display names to JSON ids, modifying only portal/data/*.json, and running deterministic validation.
---

# Project Tracking Portal

Use this skill for requests that modify or validate the Project Tracking Portal data.

> **强制:如果用户要的是"整体项目进度报告"/"项目状态总览"/"现在整体什么样"这类**汇总
> 呈现**(不是改数据、不是周报),生成输出前必须先 `skill_view("feishu-report-formatting")`
> 看格式规范和 5 段模板口径,不要凭自己上次生成的印象或者聊天记录里的旧格式直接写——那边
> 踩过坑(飞书 `md` 渲染表格会破版、进度条字符选错会变乱码、Done% 和 Run+Done% 两个口径
> 混用会出错的数字),不要在这里重新踩一遍。周报(`format_weekly_report`)的输出格式规则
> 也在那边,但周报内容口径还是看本文件下面的 `format_weekly_report` 小节。

## Data Boundary

- JSON is the source of truth: `portal/data/*.json`.
- Do not edit `Project Tracking V3.html`, `Project Tracking V3.backup.html`, `portal/index.html`, `portal/src/*`, or legacy HTML tools for data changes.
- For data edits, the intended write surface is only:
  - `portal/data/projects.json`
  - `portal/data/maisons.json`
  - `portal/data/project_maison_status.json`
  - `portal/data/status_config.json`
  - `portal/data/maison_budget.json`
  - `portal/data/status_legend.json`
  - `portal/data/images.json`
- Prefer changing only the smallest required JSON file. Most user edits should touch only `project_maison_status.json`.

## Workflow

1. Read the relevant JSON files before deciding what to edit.
2. Resolve user-facing names to ids from the JSON, not from memory.
   - Maison examples: `Tiffany` -> `tiffany`; `PCD`, `Perfume Dior` -> `perfume_christine_dior`.
   - Project names may include prefixes like `【Clienteling】`; search by normalized substring.
3. If resolution returns zero matches, stop and explain what was not found.
4. If resolution returns multiple plausible matches, ask the user to choose. Do not guess.
5. Validate any requested status against `status_config.json`.
6. Apply the smallest JSON edit.
7. Run `validate(portal_root)` in the same execute_code call and confirm `ok`.
8. Report the exact project, maison, and before/after values, plus the `resync_feishu` result.

## Performance Optimization (IMPORTANT)

> **Every write now requires `actor`** (the current Feishu user, from the message's
> `[User Profile: preferred_name=...]`) and auto-writes `portal/data/changelog.json`.
> If the user is unknown, ask before writing. Never `_save` business JSON directly —
> that bypasses the changelog and the concurrency lock; always call the tool functions.

> **强制：写任何 portal 代码前，先确认有没有现成函数。** 在动手 `execute_code` 之前，
> 你必须已经看过当前的函数清单（本文件下方 import 模板，或 skill 目录里的 `CAPABILITIES.md`）。
> **只能调用清单里列出的函数。** 绝不用 `shutil`/`open`/`json` 自己手写文件拷贝或 JSON 读写
> 去实现某个能力——你要的能力清单里基本都有（增删图 = `add_image`/`remove_image`/`list_images`）。
> 只有清单里**确实没有**某能力时，才允许自己写，且必须先在输出里说明「清单里没有 X，我要自己实现」。
> （反面教训：曾因不知道 `add_image` 存在而手搓上传逻辑，造出假图 + 幽灵 changelog。）

**Use `execute_code` to combine all operations in one call.** This reduces 3-4 tool calls (search + update + validate) down to 1, cutting latency from ~6-9s to ~2-3s. 性能优化的前提是「调用清单里的现成函数」，而不是「跳过查清单自己造轮子」。

> **强制：任何改动数据后，必须在同一段 `execute_code` 末尾调用 `resync_feishu(portal_root)`
> 把改动镜像到飞书 Base，并把它的返回 `print` 出来、在回复里如实告诉用户同步结果。**
> 飞书 Base（"AI for ALL Project Tracking Copy"）的 FACT 表和看板是只读镜像，只有这一步能把
> JSON 的改动推上去；不调它，用户在飞书里看到的就是旧数据。`resync_feishu` 只读 JSON、无锁、
> 永不抛异常，失败只返回 `{"ok": false, "error": ...}`——所以即使它失败也不影响你已经写好的
> JSON，但你必须把失败如实报给用户（例如「已改 JSON，但镜像到飞书失败：…，稍后会自动补」）。
> 纯读操作（search / preview / format_weekly_report / validate 单独跑）不需要 resync。
>
> `resync_feishu` 返回里的 `manual_edits_skipped` 是一个列表：本来要被这次同步覆盖/删除
> 的行里，哪些当前显示是被人（不是我们自己的同步机器人）最后改过的，每项带
> `project`/`maison`/`modified_by`。**这些行会被直接跳过，不写、不删**——同一次同步里其它
> 行照常执行，不会因为一行冲突就卡住整次同步。列表非空时必须把具体是哪些行、谁改的转告
> 用户，让他自己判断要不要保留手动改动、还是让 agent 重新改一次把它推回去。
> （2026-07-09 更新：之前用 Base 的 `revision` 做这个检测，实测确认在飞书 UI 里直接编辑
> 一行记录并不会让 `revision` 变化——那个信号从未真正起过作用，已换成按行比对
> `last_modified_by`，这个是准的；同日再更新：从"只警告、照常覆盖"改成"直接跳过冲突行"。）
>
> `resync_feishu` 现在还会尽量**缩小 FACT 行的推送范围**:只推 changelog 里"上次同步之后"
> 记录级改动（`update_status`/`update_remark`/`update_record_meta`）涉及的那些行，不去动
> 跟期望态同样不一致、但这次没人碰过的其它行（比如被手动改过的行）。返回里
> `fact_scope_narrowed` 为 `true` 表示这次确实缩小了范围；出现改名/建删项目这类实体级
> 改动、或者第一次同步没有历史基准时，会自动退回全量推送（`false`），不用你判断，也不用管。
>
> **强制：`update_status` / `update_remark` / `update_record_meta`（改主表某一行）必须先问用户
> "为什么改"，把答案原样传 `reason=...`。** 不传会被拒绝（`{"ok": false, "error": "reason
> required: ..."}`），这是刻意的硬校验，不是可选项——reason 会原样写进 changelog，是审计
> 记录的一部分。不要自己编一个理由替用户回答；用户没说明确原因就先问，不要瞎猜。

### Template for single execute_code call:

```python
import sys, json
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from pathlib import Path
from portal_data_tool import search, update_status, update_remark, add_project, add_maison, add_image, remove_image, list_images, format_weekly_report, validate, resync_feishu
# 周报只用 format_weekly_report。generate_weekly_summary 已废弃，禁止 import / 禁止调用。

ACTOR = "<当前飞书用户的 preferred_name>"  # REQUIRED: 取自本条消息开头的 [User Profile: preferred_name=...]，必须替换成当前发消息的人，绝不要写死某个人名

portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')

# 1. Search for project (and maison if ambiguous)
sr = search(portal_root, "Project Name", entity_type="project")
print("=== Search ===")
for p in sr["matches"]["projects"]:
    print(f"  {p['id']}: {p['name']}")

if len(sr["matches"]["projects"]) != 1:
    print("\n⚠️ Multiple or no matches, need user confirmation")
else:
    project_id = sr["matches"]["projects"][0]["id"]
    
    # 2. Update status
    # ⚠️ NewStatus 带季度（Q1-Pilot ~ Q4-Scale）时先看该记录现有 year：没设或疑似跨年要反问用户，
    #    见 Pitfalls #13；确认后另调 update_record_meta(..., year=...) 写年份，update_status 不会碰 year。
    # ⚠️ reason 必须问用户"为什么改"，不要自己编。
    us = update_status(portal_root, project_id, "maison_id", "NewStatus", actor=ACTOR, reason="<用户说的原因>")
    print(f"\n=== Update status ===")
    print(f"  ok={us['ok']}, created={us.get('created')}")
    print(f"  old={us.get('old', {}).get('status') if us.get('old') else 'N/A'}")
    print(f"  new={us['new']['status']}")
    
    # 3. Update remark (if needed)
    ur = update_remark(portal_root, project_id, "maison_id", "Remark text", actor=ACTOR, reason="<用户说的原因>")
    print(f"\n=== Update remark ===")
    print(f"  ok={ur['ok']}")
    print(f"  old remark: \"{ur['old']['remark']}\"")
    print(f"  new remark: \"{ur['new']['remark']}\"")
    
    # 4. Validate
    vr = validate(portal_root)
    print(f"\n=== Validate ===")
    print(f"  ok={vr['ok']}, errors={vr['errors']}")

    # 5. 收尾：镜像到飞书 Base（改了数据就必须做，并把结果报给用户）
    mr = resync_feishu(portal_root)
    print(f"\n=== Feishu mirror ===")
    print(f"  {mr}")   # {'ok': True, 'created':.., 'updated':.., 'deleted':..} 或 {'ok': False, 'error':..}
```

### format_weekly_report — 格式化周报

```python
from pathlib import Path
import sys
sys.path.insert(0, "/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts")
import portal_data_tool as pdt

result = pdt.format_weekly_report(
    Path("/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal"),
    since_days=7,
)
print(result["report"])          # 概览 + 变更详情(按项目分组) + 新增项目（确定性 Markdown）
print(result["summary"])         # {window_start, window_end, changed, advanced, lateral, rollback, new_projects}
# 然后基于 result["summary"] / result["changes"] 自行补写「**本周要点**」(飞书不渲染 # 标题,用粗体)
```

返回 `{"ok": true, "summary": {...}, "changes": [...], "new_project_entries": [...], "report": "...Markdown..."}`。窗口是上一个完整自然周（周一~周日）。`report` 已是给用户看的成品。

**输出规则（必须严格遵守）：**

- ✅ 把 `result["report"]` **逐字原样**发给用户——不改字、不重排、不转表格、不增删段落、不改标题。
- ✅ 只允许在末尾追加一段 `**本周要点**`（2–4 条，飞书不渲染 `#` 标题，务必用粗体不用井号），内容只能来自 `result["summary"]` / `result["changes"]`，忠于事实。
- ❌ 禁止自己读 `changelog.json` 去统计「变更总数 N 条」「操作人统计」等——这些字段返回里没有，全是编造。
- ❌ 禁止自创表格、「净变化总结」「注意事项」等段落。
- ❌ 禁止把净变化为 0 的回滚项列出来——`report` 已按设计隐藏。
- 最终只有 `report` + 你追加的 `**本周要点**` 两部分。

## Creating New Projects

When a project doesn't exist in `projects.json`, it must be created before status records can be added:

```python
import sys
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from pathlib import Path
from portal_data_tool import add_project, update_status, update_record_meta, validate, resync_feishu

portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')
ACTOR = "<当前飞书用户的 preferred_name>"  # 取自消息的 [User Profile: ...]，必须替换成当前发消息的人，不要写死人名

# 1. Create the project (validated + logged to changelog)
#    owner 同样走词表归一:唯一命中自动规范化(返回带 owner_normalized_from,要转告用户);
#    全新负责人会被拒绝(owner_reason="unknown"),与用户确认后带 allow_new_owner=True 重写。
# ℹ️ ai_project 不用传——它是 name 的物化副本,恒等于项目名,由 add_project/update_project 自动写入
#    (Base 上 Group Project 列 = 项目名)。改名走 update_project(name=...) 会自动把 ai_project 一起改。
#    Base 另有一个 Maison Project 列,是记录级可覆盖的每-Maison 显示名,见下方
#    "owner 两级颗粒度" 之后的 maison_project 说明。
res = add_project(portal_root, id="new_project_id", name="New Project Name",
                  domain="Omni retail",
                  owner="Owner Name", description="", actor=ACTOR)
print(res)

# 2. Add status records for each maison (each writes a changelog entry)
for maison_id in ["maison1", "maison2"]:
    us = update_status(portal_root, "new_project_id", maison_id, "Ideation", actor=ACTOR, reason="新项目立项")
    print(f"  {maison_id}: ok={us['ok']}")
    # project_type 是记录级字段(每个 maison 可不同,例如 "LV x Group Prospective Joint Projects"),
    # 新建项目时主动问用户这个,紧跟着传一次;用户确实答不上来才留空(很多存量记录也是空的)。
    update_record_meta(portal_root, "new_project_id", maison_id, project_type="...", actor=ACTOR, reason="新项目立项")

# 3. Validate
print(validate(portal_root))

# 4. 收尾：镜像到飞书 Base（新建项目+记录都写完后做一次即可），并把结果报给用户
print("feishu:", resync_feishu(portal_root))
```

**新建项目时,项目级只问 `domain`(不要再问 domain_category,也不要问 priority)**——它现在直接对应 Base 的 `AI Domain` 维度,写完会自动同步过去(见下方 point 14)。必须是这 5 个精确取值之一(大小写敏感,原样传):

| 传给 `domain` 的值 | 同步到 Base 的 `AI Domain` |
|---|---|
| `Client development` | AI for Client Development |
| `Omni retail` | AI for Omni-Retail |
| `Marketing` | AI for Marketing & Media |
| `Operation` | AI for Operation |
| `foundation` | Foundation |

用户说的是模糊描述(比如"零售相关"/"客户那块")时,对照上表选最贴近的一个问用户确认,不要自己瞎猜精确拼写;用户给的词完全不沾边就留空(不传 domain),不要塞一个不在表里的自由文本进去——那样 portal 会正常存,但**不会**同步到 Base(见 point 14)。

`domain_category` 是历史遗留字段(31/32 存量项目有值,来自早期导入,值很乱如 `CLIENT   DEVELOPMENT`/`OMNI. RETAIL`),Base 上从来没有对应字段。**新建项目不要再问这个**;存量数据保留不用管。

`priority` 同理,**新建项目时不要问**(留空即可);用户以后想设,走 `update_project(priority=...)` 单独改。**新建项目该主动问的记录级字段是 `project_type`**(每个 maison 建记录时问一次,可以不同 maison 不同值),通过 `update_record_meta` 设置,见上面 Create Project 示例。

## Editing & Deleting Metadata, Adding Status Types

改已有项目/maison 的元数据、删行/列、加新状态类型，**一律走下列函数**，不要手搓：

```python
import sys
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from pathlib import Path
from portal_data_tool import update_project, update_maison, delete_project, delete_maison, add_status, update_record_meta, validate, resync_feishu

portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')
ACTOR = "<当前飞书用户的 preferred_name>"  # 取自 [User Profile: ...]，绝不写死

# 改元数据：只传要改的字段；"" = 清空，不传 = 不动；id 不可改
update_project(portal_root, "proj_id", owner="新负责人", priority="QW", actor=ACTOR)
update_maison(portal_root, "maison_id", division="Beauty", ai_champion="Zoe", actor=ACTOR)

# ── owner 两级颗粒度（务必判对，最容易错）──
# 判定铁律：只要用户点到任意具体 maison 名（LV/Dior/Fendi/Loewe/Celine…），
#   不管语序——「LV 的项目 X」还是「X 里的 LV」都算——owner 变更就打到那个 maison，
#   用 update_record_meta；只有完全没提任何 maison 才用 update_project（项目级）。
#   若拿不准是项目级还是某个 maison，先问用户，别猜。
#  · 项目级默认 owner —— update_project(..., owner=...)：作用于该项目所有 maison（除非某 maison 有记录级覆盖）。
#    对应「把 X 项目的负责人改成 Y」这类完全不提 maison 的说法。
#  · maison 级 owner —— update_record_meta(..., owner=...)：只改某一个 (project, maison) 格子，覆盖项目默认。
#    对应「把 X 里 LV 的负责人改成 Z」「LV 的项目 X 负责人改 Z」这类点名 maison 的说法。
#    传 owner="" 清除记录级、该 maison 回落项目默认。
update_record_meta(portal_root, "proj_id", "louis_vuitton", owner="Qiwen", actor=ACTOR, reason="<用户说的原因>")

# 项目级 owner 变更的返回里带 affected_maisons(继承默认、会跟着变的 maison 数)和
# overridden_maisons(有记录级覆盖、不受影响的数)——必须原样报给用户,让影响面可见。
# 如果用户本意只改一个 maison 而你看到 affected_maisons 是一大片,说明你选错了函数。

# ── owner 名字会自动对照现有负责人词表（别硬写生名字）──
# add_project / update_project / update_record_meta 传 owner 时，工具会拿它跟已有负责人比对：
#  · 唯一命中（含 "qiwen"→"Qiwen MO" 这类部分匹配）：自动归一，返回带 owner_normalized_from，
#    请转告用户「已归一到 Qiwen MO」。
#  · 返回 ok=False 且 owner_reason="unknown"：词表里没有 → 这是个新名字。先问用户确认是不是要
#    新建，确认后带 allow_new_owner=True 重写；不要自作主张造新 owner。
#  · 返回 ok=False 且 owner_reason="ambiguous"：命中多个 → 把 owner_candidates 列给用户选全名。

# ── maison_project:同一项目在某个 Maison 下想叫别的名字(少见,谨慎用)──
# Base 的 Maison Project 列缺省 = Group Project(项目名);只有用户明确要求
# "这个项目在 XX 下面想叫别的名字"时才用 update_record_meta(..., maison_project=...)
# 单独覆盖某一个 (project, maison)。传空串清除覆盖,回落项目名。
# **这不是打字错误纠正,是一个不常用的功能**——agent 在要设置一个跟项目名不一样的
# maison_project 时,应该先跟用户确认一下"这会让 XX 下面显示的项目名跟别的地方不一样,
# 确认吗",不要在没明确要求覆盖的情况下自作主张调用。项目改名(update_project(name=...))
# 会自动带着没被覆盖过的记录一起变(它们本来就是回落取项目名);已经被覆盖过的记录不受影响。
update_record_meta(portal_root, "proj_id", "louis_vuitton", maison_project="LV 专属叫法",
                  actor=ACTOR, reason="<用户明确要求覆盖的原因>")

# ── maison_top3:这个项目是不是某个 Maison 的重点前三项目 ──
# 布尔值,传 True/False 直接设置,没有"清空回落"的概念(不像 maison_project 那样有默认值
# 可回落)。同步到 Base 的 Maison Top 3 列(Checkbox)。不是现有 Top 12 列(那个是 Maison
# 级标签,来自 DIM_Maison,跟项目排名无关,不要混淆)。程序不强制"每个 Maison 最多 3 个"
# 这条规则,靠人工/agent 自己把关。
update_record_meta(portal_root, "proj_id", "louis_vuitton", maison_top3=True,
                  actor=ACTOR, reason="<用户说的原因>")

# 删行/列：破坏性，必须两步——先拿依赖数，再带 confirm 执行
need = delete_project(portal_root, "proj_id", actor=ACTOR)   # -> needs_confirm + dependents
# 把 need["dependents"] 告诉用户、确认后：
delete_project(portal_root, "proj_id", confirm="proj_id", actor=ACTOR)
delete_maison(portal_root, "maison_id", confirm="maison_id", actor=ACTOR)  # 级联格子/budget/图片，孤儿图保护

# 加新状态类型：写 status_config + status_legend 两表，之后 update_status 才接受
add_status(portal_root, "Paused", category="hold", stage_order=0, description="暂停中", actor=ACTOR)

print(validate(portal_root))
# 收尾：改了行/列/元数据/状态类型都要镜像到飞书 Base，并把结果报给用户
print("feishu:", resync_feishu(portal_root))
```

**删除强制确认**：`delete_*` 不带 `confirm` 只返回 `needs_confirm` + 依赖数，**绝不直接删**；必须把影响面告诉用户、得到确认后再带 `confirm=<id>` 执行。删前自动落 `data/backups/<file>.bak.<stamp>` 快照。

## Maison Gallery Images (add / list / remove)

Gallery images live in `portal/data/images.json` as `[{maison_id, images:["images/<file>", ...]}]`. Three audited functions manage them — like all writes they go through the lock + changelog. **Never `shutil.copy` files or edit `images.json`/`portal/images/` by hand** — manual copies have failed repeatedly (wrong dir: files must be in `portal/images/`, NOT `portal/data/images/`; wrong extensions: Feishu-saved PNGs often arrive named `.jpg`). Images don't mirror to Feishu Base — no resync needed.

- **`add_image(portal_root, maison_id, source_path, actor)`** — `source_path` is the **local cache path the gateway reports** when the user sends an image (the `[用户发来一张图片,已存于:...]` note) — never invent a path. Validates magic bytes (PNG/JPEG/GIF/BMP/WEBP), 20 MB cap, picks the correct extension, places the file in `portal/images/`. **Content-hash dedup**: identical bytes already in that maison → `{"ok": true, "noop": true}` — report as "已存在,未重复添加".
- **`list_images(portal_root, maison_id)`** — read-only. **Always call before removing** so the user can pick the 1-based index.
- **`remove_image(portal_root, maison_id, image, actor)`** — `image` = 1-based index or full `images/<file>` ref. **Orphan protection**: the disk file is unlinked only if no other maison references it; otherwise `{"file_deleted": false}` — report which.

**Dior ambiguity** (`christine_dior_couture` vs `perfume_christine_dior`): resolve via `search` and **ask the user** before any image write.

```python
import sys
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from pathlib import Path
from portal_data_tool import add_image, list_images, remove_image

ACTOR = "<当前飞书用户的 preferred_name>"  # 取自 [User Profile: ...]，绝不写死
portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')

r = add_image(portal_root, "tiffany", "<网关报告的本地缓存图片路径>", actor=ACTOR)
print(r)
imgs = list_images(portal_root, "tiffany")     # show imgs["images"] to the user
rm = remove_image(portal_root, "tiffany", 2, actor=ACTOR)
print(rm)
```

Native (Feishu) tool equivalents: `portal_project_add_image` / `portal_project_list_images` / `portal_project_remove_image`.

## Batch Updates

When updating multiple maisons for the same project (e.g., "push all Ideation to Q4-Pilot"), use a single `execute_code` call: filter the records **read-only** to find targets, then call `update_status` once per target — each call is locked + logged, that's the audited write path. `_load` is for **reading only**; never write with `_save` (bypasses lock + changelog):

```python
import sys
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from pathlib import Path
from portal_data_tool import _load, update_status, validate, resync_feishu

portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')
ACTOR = "<当前飞书用户的 preferred_name>"  # 取自消息的 [User Profile: ...]，必须替换成当前发消息的人，不要写死人名
records = _load(portal_root, "project_maison_status.json")

project_id = "clienteling_gen_ai_client_knowledge"
targets = [r for r in records if r['project_id'] == project_id and r['status'] == 'Ideation']
print(f"Found {len(targets)} records to update")

# Each call is locked + logged. Do NOT _save directly — that bypasses the changelog and lock.
for r in targets:
    update_status(portal_root, project_id, r['maison_id'], 'Q4-Pilot',
                  remark='Batch updated after project alignment.', actor=ACTOR,
                  reason="<用户说的原因>")

print(validate(portal_root))
# 收尾：整批改完只需镜像一次(resync 是全量,不要放进循环里)，并把结果报给用户
print("feishu:", resync_feishu(portal_root))
```

## Matching Rules

Normalize by lowercasing and removing punctuation, brackets, and whitespace differences. Match in this order:

1. Exact id match.
2. Exact normalized display-name match.
3. Alias match from `references/test-cases.json`.
4. Unique normalized substring match.

Ambiguous matches are expected for short terms like `DREAM`, `journey`, or `NPS`; ask for confirmation.

## Follow-up / Audit Queries

Common query patterns for reviewing data health:

```python
# Stale records: status + age filter
from datetime import datetime, timedelta
today = datetime.now()
cutoff = today - timedelta(days=60)
stale = [(i, r) for i, r in enumerate(records)
         if r['status'] == 'Ideation'
         and r.get('updated_at', '')
         and datetime.fromisoformat(r['updated_at']) < cutoff]

# Cross-maison summary for one project
from collections import Counter
project_records = [r for r in records if r['project_id'] == target_id]
counts = Counter(r['status'] for r in project_records)

# All records for one maison
maison_records = [r for r in records if r['maison_id'] == 'tiffany']

# Global: all Run → Done (cross-project, cross-maison).
# Use the audited write path — never mutate `records` in place + _save (that
# bypasses the changelog and lock). update_status is locked + logged per call.
for r in [r for r in records if r['status'] == 'Run']:
    update_status(portal_root, r['project_id'], r['maison_id'], 'Done', actor=ACTOR,
                  reason="<用户说的原因>")

# 收尾：整批改完镜像一次到飞书 Base（import 里记得带上 resync_feishu），并把结果报给用户
from portal_data_tool import resync_feishu
print("feishu:", resync_feishu(portal_root))
```

### Changelog reading

`portal/data/changelog.json` is an append-only audit trail. Each entry has **top-level** fields (not nested in `details`):

```python
# Read recent changelog entries
with open(portal_root / 'data' / 'changelog.json') as f:
    changelog = json.load(f)

for entry in changelog[-20:]:
    ts = entry.get('timestamp', '')        # ISO format
    actor = entry.get('actor', 'unknown')   # who made the change
    action = entry.get('action', '')        # update_status, update_remark, add_project, etc.
    project_id = entry.get('project_id')    # top-level, NOT in details
    maison_id = entry.get('maison_id')      # top-level, NOT in details
    changes = entry.get('changes', [])      # [{field, old, new}, ...]
    print(f"{ts} | {actor} | {action} | {project_id}/{maison_id}")
    for c in changes:
        print(f"  {c['field']}: {c.get('old')} → {c.get('new')}")

# Filter by actor (who made changes?)
from collections import Counter
actors = Counter(e.get('actor') for e in changelog if e.get('actor'))
print("Changes by actor:", dict(actors))

# Filter by date range
from datetime import date, timedelta
cutoff = (date.today() - timedelta(days=7)).isoformat()
recent = [e for e in changelog if e.get('timestamp', '')[:10] >= cutoff]
```

### Changelog archiving (manual, quarterly)

`changelog.json` grows forever with no rotation. `format_weekly_report` only ever reads the last ~1-2 weeks, so it's safe to archive anything before the current quarter. `archive_changelog(portal_root, today=None)` (also callable via `dispatch(portal_root, "archive_changelog")`) moves entries older than the current quarter into `data/changelog-archive/<year>-Qn.json`, one file per quarter, leaving `changelog.json` with only the current quarter's entries. Idempotent — safe to re-run, never duplicates entries in the archive files. **Manual only, no cron wired up yet** — run it yourself roughly once a quarter; it never touches Feishu (`resync_feishu` not needed after).

```python
from portal_data_tool import archive_changelog
print(archive_changelog(portal_root))
```

## Pitfalls

1. **回答用户实际问的问题,不要固定输出**: If the user asks a question, answer it — don't assume they always want a weekly report, a data dump, or any other fixed output. Execute exactly what was asked. User explicitly called out: "你不要我和你说什么，你都只会生成周报，看好问题再干".
2. **Matrix view not showing new projects**: The Matrix renders all `projects.json` entries dynamically from JSON. If a newly added project doesn't appear, it's usually browser cache or `file://` CORS blocking JS module loading. Use a local HTTP server (`python3 -m http.server`) to serve the portal.
3. **New project row position**: Projects appear in Matrix at the row matching their array index in `projects.json` (1-indexed, after header row). Appending to the end puts it at the bottom.
4. **Dior ambiguity**: "Dior" matches two maisons — `christine_dior_couture` (Fashion) and `perfume_christine_dior` (Beauty). Always ask user to confirm.
5. **Status + Remark in one call**: `update_status` accepts an optional `remark` parameter — use it to set both in one call instead of two separate calls.
6. **删除有快照，但仍无自动回放**：`delete_project`/`delete_maison` 在删除前会把受影响文件快照到 `portal/data/backups/<file>.bak.<stamp>`，changelog 的 `old` 字段也带被删行的完整对象——两者都可用于手动重建。但**自动反向回放仍未实现**；需要回滚就读快照或 changelog 手动恢复。破坏性批量操作前务必先把计划给用户看。
7. **Invalid status rejected at API level**: `update_status` returns `{"ok": false, "error": "unknown status: ..."}` if the status is not in `status_config.json`. Valid statuses: `Ideation`, `Q1-Pilot`–`Q4-Pilot`, `Q1-Scale`–`Q4-Scale`, `Run`, `Done`. 新状态类型用 `add_status()` 增加（写 config+legend 两表）后即被接受；不要直接手改 `status_config.json`。
8. **`updated_at` timestamps are migration artifacts**: All records imported from old HTML have `updated_at: "2026-06-16"`. Stale-record queries based on age won't work until real edits accumulate over time.
9. **Batch remark updates — append, don't overwrite**: When adding a remark to many records, preserve existing remarks with `" | "` separator: `new_remark = f"{old} | {suffix}" if old else suffix`.
10. **`update_status` auto-creates records**: If `(project_id, maison_id)` doesn't exist, `update_status` creates a new record (`"created": true`). This is intentional — used when adding a new brand to an existing project.
11. **`resync_feishu` does NOT clean orphaned DIM records**: it updates FACT rows and creates missing DIM records, but never deletes DIM records no longer referenced by any FACT row (e.g. renamed owners linger in DIM_Owner). Cleanup is manual — see `references/feishu-dim-cleanup.md`.
12. **Noop behavior**: When a write is called with values that already match, the tool returns `{"ok": true, "noop": true}` without writing JSON or changelog. This is correct — but **always report noop to the user** so they know no change was made (e.g., "状态已经是 Run，未做修改").
13. **`update_status` 从不碰 `year`，季度不代表年份**：`update_status` 只写 `status`/`remark`/`updated_at`，`year` 是完全独立的字段，唯一写入口是 `update_record_meta(..., year=...)`。`status_config.json` 里 `Q1-Pilot`–`Q4-Scale` 本身不带年份（"Q4-Scale" 可以是任何一年的 Q4）。所以改成任何带季度的 status 前，先看一眼该记录现有的 `year`（`search`/`preview_record` 都能拿到）：**没设 year，或者这次改动像是跨年**（例如从 Q4-XXX 推进到 Q1-XXX，或现有 year 明显是往年）——必须反问用户"这个是哪一年的 Q_-___"，拿到答案后紧跟一次 `update_record_meta(portal_root, project_id, maison_id, year="2027", actor=ACTOR, reason="...")`。不确定就问，禁止悄悄留空或照抄旧 year。
14. **`ai_project` 恒等于项目名，不是独立字段**：`ai_project` 是 `name` 的物化副本（Base 上 Group Project 列 = 项目名），由 `add_project`/`update_project` 自动写入，**不要单独传参**；改名走 `update_project(name=...)` 会自动把 `ai_project` 一起改。所以 Group Project 列不会为空（只要项目有名字）。`project_type` 才是要单独填的记录级字段（只能 `update_record_meta`，JSON 没填 `resync_feishu` 就没东西写，用户给了就设）。**AI Domain 现在会自动同步**：Base 的 `FACT.AI Domain` 是 Lookup 列（永远不能直接写），真值挂在 `DIM_AI Project.AI Domain`（一个关联到 `DIM_AI Domain` 的字段）——`resync_feishu`/`update_project` 触发的镜像会把 `projects.json` 的 `domain` 字段（经 `feishu_bitable.DOMAIN_TO_AI_DOMAIN` 映射成上面表格里的 5 个 Base 规范名之一）写进 `DIM_AI Project.AI Domain`。`domain` 为空或不在这 5 个值里 → 静默跳过，**不会清空** Base 上已有的 AI Domain（不是 bug）。所以新建/改项目时问用户 domain，就是在问这个字段，不用再让用户手动去 Base 补。

15. **`Maison Project` ≠ `Group Project`,不要混用**:Base FACT 表 2026-07-16 起把原来单一的 `Project` 列拆成两个:`Group Project`(DuplexLink,恒等于项目名,身份识别用它)和 `Maison Project`(纯文本,缺省回落项目名,可以按 maison 单独覆盖,存在 JSON 里的 `maison_project` 字段)。`update_project(name=...)` 改的是项目名(= Group Project),不会碰任何已设置的 `maison_project` 覆盖;要改某个 maison 的专属显示名,走 `update_record_meta(..., maison_project=...)`,设置前按上面的指引先跟用户确认。
16. **`Top 12` 和 `Maison Top 3` 是两个不相关的概念,别混**:`Top 12` 是 Formula 列,透传 `DIM_Maison.Top 12`(一个 Checkbox)——是"这个 Maison 是不是重点 Maison"的标签,跟具体哪个项目无关,同一个 Maison 下所有行的 `Top 12` 值都一样。`Maison Top 3`(2026-07-17 新增,JSON 字段 `maison_top3`)才是"这个项目在这个 Maison 下排不排前三"的项目级排名标记,只能通过 `update_record_meta(..., maison_top3=True/False)` 设置,程序不检查同一 Maison 下有没有超过 3 个被标记。

## References

- Domain knowledge (statistics formulas, column order, status lifecycle): `references/domain-knowledge.md`
- Test prompts and expected behavior: `references/test-cases.json`
- Write operations test cases (update_project, update_maison, delete_project, delete_maison, add_status): `references/write-operations-test-cases.md`
- Validator script: `scripts/validate_portal_data.py`
- Dry-run sample harness: `scripts/run_skill_samples.py`
- JSON-native Hermes/CLI tool core: `scripts/portal_data_tool.py`
