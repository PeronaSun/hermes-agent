---
name: excel-warrior-meeting-minutes
description: 维护按 maison 维度的会议纪要(独立于主 project-tracking 表)。详实正文归档进飞书知识库(按 maison 分目录、按时间排序的独立文档),本地只留一份轻索引,摘要+链接直写飞书 Meeting Minutes 表。用户提到"会议纪要/纪要/minutes/记一下这次会"时使用;用户贴飞书文档链接(/docx/ 或 /wiki/)、上传会议纪要文件、或粘贴一段会议记录文本要求"导入/记录这个会"时,走本 skill 的"从文档导入"流程。
---

# 会议纪要 Meeting Minutes

按 maison 维度归档会议纪要,和主项目跟踪表**互不影响**。三层各管一件事,不是互为备份:

- **飞书知识库(wiki)**——**唯一保留详实正文**的地方。固定一个知识空间,maison 是一级
  目录节点,每条纪要是目录下的一个子页面(标题带日期前缀,天然按时间排序)。参会人/
  摘要/正文/行动项全文都写在这里。
- **本地轻索引**(`portal/data/meeting_minutes.json`)——只存 `{id, maison_id, date,
  title, doc_url, wiki_node_token, created_at, updated_at}`,不重复存正文。给
  actor/锁/changelog 这套写盘纪律用,也是 `list_minutes` 查询的数据源。
- **飞书 Base 的 Meeting Minutes 表**(`tbl7k0o9qly9KHAr`)——摘要级别的可筛选索引 +
  一个跳到 wiki 原文的 `Link` 列,方便在表格里扫一眼、点链接看全文。这行是**创建时直写
  一次**,不是从 JSON 全量 diff 出来的(JSON 已经不携带足够重建这行的字段了)。

## 何时用
- 用户要记录/查询某个 maison 的会议纪要。
- 与项目状态矩阵(主表)无关——那类需求走 project-tracking-portal,别混。

## 怎么调(execute_code)

```python
import sys
# 本技能脚本 + 复用主表的飞书基础设施,两条路径都要在 sys.path
sys.path.insert(0, "/Users/perona/.hermes/hermes-agent/skills/excel-warrior-meeting-minutes/scripts")
sys.path.insert(0, "/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts")
from pathlib import Path
from meeting_minutes_tool import dispatch

PORTAL = Path.home() / ".hermes" / "project_tracking_portal" / "portal"

# 新增一条纪要(actor=当前用户名,必填;summary/project_ids 可空)
# 全部字段(participants/summary/content/action_items/project_ids)会整篇写进 wiki 页面,
# 但本地 JSON 只落 id/maison_id/date/title/doc_url 这几个轻字段——wiki 归档失败就直接
# 报错,不会留下一条指不到任何内容的"孤儿"索引。
r = dispatch(PORTAL, "add_minute",
             maison_id="louis_vuitton", date="2026-07-07",
             title="LV Q3 选品会", participants=["Mars YU", "Rachael JIANG"],
             summary="确认 Q3 选品方向与预算分配原则",
             project_ids=["product_recommendation"],  # 须真实存在于 projects.json
             content="讨论了 Q3 选品与预算分配",
             action_items=[{"owner": "Mars YU", "task": "补 Q3 预算表", "due": "2026-07-10"}],
             actor="薛亮")
print(r)   # {"ok": True, "id": "mm_20260707_louis_vuitton",
           #  "minute": {..., "doc_url": "https://.../wiki/xxx"}, "feishu": {"ok": True}}
```

其他动作:
- `update_minute(id=..., title=..., date=..., note=..., actor=...)` — **只能改索引层的
  title/date**;要追加新进展/内容变了,传 `note`(一句话说清变了什么),会在对应 wiki
  页面末尾追加一段带日期的更新记录,不整篇重写、不删旧内容。没传 note 就不碰 wiki。
- `delete_minute(id=..., actor=...)` — 只删索引行(和对应 Bitable 行),**wiki 页面本身
  不会被删**——归档语义,原文不因为索引没了就消失,用户想真删得去飞书里手动删那篇文档。
- `list_minutes(maison_id=..., date_from="2026-07-01", date_to="2026-07-31")` — 只读
  查询,返回轻索引字段(含 `doc_url`);想看正文点链接进 wiki。

## 从文档导入(自动拆纪要)

用户贴一个飞书文档链接(典型场景:开完会飞书自动生成的 AI 智能纪要文档)、上传一个
文件、或直接粘贴一段会议记录文本,说"导入纪要/把这个会记一下"——**全部字段从内容里
自动解析,不要再问用户要日期/标题/参会人**,只有真正解析不了的才开口。

### 第一步:拿到全文

贴链接:

```python
import sys
sys.path.insert(0, "/Users/perona/.hermes/hermes-agent/skills/excel-warrior-meeting-minutes/scripts")
sys.path.insert(0, "/Users/perona/.hermes/hermes-agent/skills/excel-warrior-project-tracking-portal/scripts")
from feishu_doc_reader import read_doc

r = read_doc("https://xxx.feishu.cn/docx/ApVUdHTFDoirOuxCqLacK7YBnmi")  # /wiki/ 链接也行
print(r["content"] if r["ok"] else r)   # 失败时 error 里带了给用户的提示,原样转告
```

上传文件:网关已经把文件下载缓存到本地,消息文本里会带一句
`[The user sent a document: '<文件名>'. The file is saved at: <路径>. ...]`——
从这句里取出路径,读本地 `.docx`(不碰网络,不走 read_doc):

```python
from feishu_doc_reader import read_local_docx

r = read_local_docx("<上面提示里的路径>")
print(r["content"] if r["ok"] else r)   # 失败(文件不存在/非 docx/解析出错)原样转告用户
```

- 只支持 `.docx`;其他格式(pdf/doc/图片等)如实告诉用户暂不支持,让他们转成 docx
  或贴飞书文档链接。
- 粘贴的纯文本:跳过这步,直接进第二步。
- `/minutes/`(妙记)链接 read_doc 会明确报错——按 error 里的话术让用户贴 AI 纪要
  文档(/docx/)链接。

### 第二步:解析 + 按 maison 拆

- **日期**:AI 纪要文档头部有 `Time: Jul 3, 2026 ...` 这类行,转成 `YYYY-MM-DD`;
  没有就用文中出现的会议日期;都没有才问用户。
- **标题**:文档的 Title 行;拆多条时每条标题 = `原标题 — <Maison名>`。
- **参会人**:文中的 Attendees/参会人列表;没有就留空列表,不编造。
- **按 maison 拆分(关键)**:一场会经常涉及多个 maison(比如同一份纪要里有宝格丽、
  尚美、PCD 各自的进展)。先读 `maisons.json` 拿全部 maison 的 id/name,再按内容里
  提到的品牌把内容归堆,**一个 maison 一条纪要**,行动项同理归到对应 maison 的条目里。
- **content 要详实,不要精简**:这是归档,不是标题摘要——标准是"没参会的人只看这条,
  就能完整了解这场会里关于该 maison 的一切"。原文档里该 maison 相关的**每一个细节都
  保留**:具体数字、日期、人名、推迟/变更的原因、依赖关系、风险、下一步计划,一个都
  不丢,接近原文照搬也没关系(只去掉口水话和与该 maison 无关的部分)。宁可长,不可薄;
  压缩到一两句话就是做错了。这份详实内容最终整篇写进 wiki 页面,不是给本地存的。
- **action_items 不知道的字段留空**:owner/due 从原文里解析不出来就传空字符串 `""`,
  **禁止填 "TBD"/"待定" 这类占位符**——那是脏数据,空着反而能被后续筛出来补。
- **summary(简介)**:content 详实之后,每条自动配一段 1-2 句的 summary,让人扫一眼
  知道这条讲了什么;概览就是概览,不要把 content 的细节再抄一遍。这段会同时写进 wiki
  页面开头和 Bitable 的 Summary 列。
- **project_ids(关联项目,可空)**:内容里明确对应主表某个项目的(比如"产品推荐"、
  "Smart Search"),读 `projects.json` 核对后把项目 id 填进列表,一条纪要可挂多个;
  **拿不准就留空**,不要硬猜——id 必须真实存在于 projects.json,否则写入会被拒。
- 中文别名对照:宝格丽=Bvlgari、尚美=Chaumet、LV/路易威登=Louis Vuitton 之类,
  拿不准的先用主表 search 核对;**解析不出对应哪个 maison 的内容不硬塞**——明确
  告诉用户"这部分内容(XXX)没有对应到任何 maison,要指定一个还是跳过?"
- 纯内部讨论、与任何 maison 无关的整场会:如实说没有可归档的 maison 维度内容,
  问用户是否要挂在某个 maison 下,不要自己猜一个。

### 第三步:落库 + 汇报

所有条目解析齐了就直接批量 `add_minute` 写入(actor=当前用户),**不需要用户确认**;
写完汇报:拆成了几条、每条的 maison/日期/标题、以及每条的 `doc_url`(方便用户直接点开
核对 wiki 归档效果)。某一条如果 wiki 归档失败,那一条会直接返回 `ok: False`,如实告诉
用户哪条失败了、要不要重试,不要吞掉这个错误当成部分成功。

## wiki 归档结构

固定知识空间 `SPACE_ID = "7662684470754954223"`,根节点 `ROOT_NODE_TOKEN`(标题
"Homepage")下按 maison 建一级目录节点(标题=maison 中文/英文名,精确匹配已存在就复用,
不会重复建)。每条纪要是目录下的子页面,标题 = `<date> · <title>`(没 title 就只有日期),
列表天然按日期字符串排序=时间顺序。正文结构固定几个段落:Maison/Date/参会人 一行、
关联项目(有才写)、摘要(有才写)、正文(按空行拆段落写)、行动项(有才写,`- owner:
task (due)` 逐行)。这套逻辑在 `meeting_minutes_wiki.py`,`archive_minute()` 建页面,
`append_update_note()` 给 `update_minute(note=...)` 用,在页面末尾追加一段
`[更新 <日期>] <note>`。

## 数据形状

**本地轻索引**(`meeting_minutes.json`)一条:
`{id, maison_id, date(YYYY-MM-DD), title, doc_url(wiki 页面链接),
wiki_node_token, created_at, updated_at}`。**不含** participants/content/summary/
project_ids/action_items——那些只在 wiki 页面正文里,想看得点 `doc_url`。

> 2026-07-15 前写入的 3 条旧记录(`mm_20260703_bvlgari/chaumet/perfume_christine_dior`)
> 还是旧 schema(本地存了完整 content/summary/participants/project_ids,没有
> doc_url/wiki_node_token),尚未迁移进 wiki。读到这几条时 `doc_url` 字段是空的,如实
> 告诉用户"这条还是旧格式,正文在本地/Bitable 里,还没归档进 wiki",不要假装有链接。

## 坑 / 约束
1. **actor 必填**,否则写动作直接报错。
2. **maison_id 必须是 maisons.json 里已存在的 id**(不是 maison 名字);不确定先用主表的
   search 或读 maisons.json 核对。
3. **一条纪要挂一个 maison**。一场会涉及多个 maison → 记多条,别硬塞。
4. **id 是系统生成的**(`mm_<日期>_<maison>`),不要自己造;改内容用 update_minute 传 id。
5. **maison_id 不可改**:记错了就 delete 再 add(wiki 页面会孤立在旧 maison 目录下,
   知道就好,不影响新纪要正常归档)。
6. **wiki 归档是硬性前提**:`add_minute` 会先把内容写进 wiki,失败就直接返回
   `{"ok": False}`、本地 JSON 不落盘——不会出现一条有索引但打不开原文的"孤儿"记录。
7. **Bitable 那行是 best-effort**:失败只是 `{"ok": False, ...}`,不影响 wiki/JSON 已经
   落盘的内容,每次写后把返回里的 `feishu` 字段报给用户。
8. **delete_minute 不删 wiki 页面**,只删索引 + Bitable 行——真要销毁原文得去飞书手动删。

> 本 Skill 已迁移到 Bagel runtime；文中的路径均指向当前 runtime 内的隔离能力层。
> 同一套约定(见 `deployments/hermes/2026-06-29-portal-write-completion/deploy.sh` 的
> `copy_fix`,部署时把这个前缀整体替换成 prod 实际 `$HERMES`/home)。⚠ 但该 deploy.sh
> 目前**不部署 meeting-minutes**——只处理 `skills/project-tracking-portal/`。这份
> SKILL.md 和 `scripts/meeting_minutes_tool.py` / `meeting_minutes_bitable.py` /
> `meeting_minutes_wiki.py` 现在还是手动拷贝到
> `$HERMES/skills/productivity/meeting-minutes/`,拷贝时要手动做同样的路径替换
> 不要把旧开发机绝对路径复制到调用代码中。
