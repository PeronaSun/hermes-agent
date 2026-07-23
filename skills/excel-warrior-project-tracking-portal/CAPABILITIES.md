# Portal 函数清单（写代码前必看）

**这是 `portal_data_tool` 现有能力的权威清单。** 用 `execute_code` 操作 Project Tracking 前，
先在这里确认要用的能力**是否已有现成函数**。有就直接调，**绝不**自己用 `shutil`/`open`/`json`
手写文件拷贝或 JSON 读写去重造一遍——那会绕过加锁、changelog 审计，还极易把 schema 写歪
（已发生过：手搓图片上传造出假图 + 幽灵 changelog）。

只有当某能力**在本清单里确实没有**时，才允许自己写，且必须先在输出里说明
「清单里没有 X，我要自己实现」。

导入路径：
```python
import sys
sys.path.insert(0, '/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts')
from portal_data_tool import (
    search, preview, update_status, update_remark, update_record_meta,
    add_project, add_maison,
    add_image, remove_image, list_images,
    update_project, update_maison,
    delete_project, delete_maison,
    add_status,
    format_weekly_report, validate,
    resync_feishu, archive_changelog,
)
from pathlib import Path
portal_root = Path('/Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal')
```

所有写操作的 `actor` = 当前飞书用户的 `preferred_name`（取自消息开头 `[User Profile: ...]`），不写死人名。

**`update_status` / `update_remark` / `update_record_meta` 这三个记录级写操作，`reason`（变更原因）和 `actor` 一样是必传参数，不传直接报错——`reason` 必须问用户，不能编。**

| 能力 | 函数（全部第一参数是 `portal_root`） | 备注 |
|---|---|---|
| 搜索项目/maison | `search(portal_root, query, entity_type="all", limit=10)` | 改之前先用它把展示名解析成 id；多候选必须问用户 |
| 预览某格 | `preview(portal_root, project_id, maison_id)` | 只读 |
| 改状态 | `update_status(portal_root, project_id, maison_id, status, remark=None, actor=..., reason=...)` | 写；自动加锁 + changelog；`reason` 必传 |
| 改备注 | `update_remark(portal_root, project_id, maison_id, remark, actor=..., reason=...)` | 写；`reason` 必传 |
| 改记录级元数据(project_type/year/owner) | `update_record_meta(portal_root, project_id, maison_id, *, project_type=_UNSET, year=_UNSET, owner=_UNSET, allow_new_owner=False, actor=..., reason=...)` | 写；`reason` 必传；owner 记录级优先于项目级；传 `""` 清除记录级 owner 回落项目默认；owner 会对照已有负责人词表归一，新名字需 `allow_new_owner=True` 显式确认；**任何一个 maison 单独指定 owner，都要用这个而不是 `update_project` 的项目级 owner** |
| 新增项目 | `add_project(portal_root, id, name, domain="", ..., actor=...)` | 写 |
| 新增 maison | `add_maison(portal_root, id, name, division="", ..., actor=...)` | 写 |
| **画廊列图** | `list_images(portal_root, maison_id)` | 只读；**删图前必先调用**让用户选序号 |
| **画廊加图** | `add_image(portal_root, maison_id, source_path, actor=..., position=None)` | 写；source_path 取网关注入的『已存于』路径；内容重复自动 noop |
| **画廊删图** | `remove_image(portal_root, maison_id, image, actor=...)` | 写；`image`=1-based 序号或完整 ref；孤儿保护 |
| 周报 | **优先用 native 工具 `portal_project_weekly_report`**（无参即可）；沙箱兜底才用 `format_weekly_report(portal_root, since_days=7)` | 见 SOUL「周报生成」专节，逐字输出 |
| 校验 | `validate(portal_root)` | 改完必调 |
| 改项目元数据 | `update_project(portal_root, id, *, name=_UNSET, ..., actor=...)` | 写;id 不可改;只改传入字段，`""`=清空、不传=不动 |
| 改 maison 元数据 | `update_maison(portal_root, id, *, name=_UNSET, division=_UNSET, ai_champion=_UNSET, key_account=_UNSET, actor=...)` | 写;语义同 update_project |
| 删项目（级联） | `delete_project(portal_root, id, *, confirm=None, actor=...)` | 写;**破坏性**;先无 confirm 拿依赖数，再 `confirm=id` 执行;删前快照 |
| 删 maison（级联） | `delete_maison(portal_root, id, *, confirm=None, actor=...)` | 写;**破坏性**;级联格子/budget/图片;孤儿图保护;`confirm=id` 执行 |
| 新增状态类型 | `add_status(portal_root, status, *, category="", quarter=None, stage_order=None, description="", actor=...)` | 写;写 config+legend 两表;落地后 update_status 即接受 |
| 手动补镜像 | `resync_feishu(portal_root)` | 写操作收尾用（一般不用单独调，写函数已自动收尾）；只读 JSON 全量镜像到飞书 Base；无锁、无 changelog、永不抛异常 |
| 归档 changelog | `archive_changelog(portal_root, today=None)` | 把早于当前季度的 changelog 条目挪进 `data/changelog-archive/`；手动触发、幂等；季度第一周若要出周报应**先出周报再归档**，否则跨季度那几天的条目会被挪走导致周报漏报 |

**没有的函数（别去找/别手搓代替）：** `delete_image`（用 `remove_image`）、`generate_weekly_summary`（已废弃，用 `format_weekly_report`）、任何「直接写文件/直接 `_save`」的捷径。

图片专门流程（加/删的前置确认、Dior 歧义、孤儿保护）见 SOUL.md「图片上传 / 删除」专节。
