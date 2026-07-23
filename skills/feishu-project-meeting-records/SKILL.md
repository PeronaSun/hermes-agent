---
name: feishu-project-meeting-records
description: "只读查找 Feishu Base 中某个项目关联的 Meeting Records，并总结最近一次会议日期和会议重点。用户询问某项目最近一次会议、会议历史、关联 Meeting Records、上次会议讲了什么，或类似‘读取 Celine Voice Tagging 项目关联的 Meeting Records’时使用。"
---

# Feishu Project Meeting Records

执行确定性的只读查询。只使用 `feishu_bitable_read_table`，不写 Base，不生成修改预览，不调用浏览器、Docx、评论或旧会议写入工具。

## 输入

从用户请求中取得：

- 项目名称，必需，例如 `Celine Voice Tagging`。
- Maison，可选；项目同名时用于消歧。
- Feishu Base URL。优先复用当前会话中已提供的同一个 Base URL；没有时只询问一次，不猜 URL 或 app token。

## 固定流程

1. 调用一次 `feishu_bitable_read_table`：
   - `base_url`: 当前 Base URL
   - `target_table_name`: `Project Tracking`
   - `maximum_rows`: `500`
2. 在返回的 `records[].fields_by_name` 中定位项目：
   - 优先精确匹配完整项目名。
   - 依次检查 `任务描述`、`分组`、`Project`、`Project Name`、`Maison Project`。
   - 比较时仅忽略首尾空格和英文大小写，不做模糊猜测。
   - 用户提供 Maison 时，同时精确匹配 `Maison`。
   - 零条匹配：报告未找到并停止。
   - 多条匹配：列出最多 5 个候选的 Maison 与项目名，让用户选择；不要继续猜。
3. 从唯一项目记录的 `Meeting Records` 关联字段提取关联 record ID。兼容字符串、对象、对象数组和 ID 数组；优先使用 `record_id`，其次 `id`。
4. 调用一次 `feishu_bitable_read_table`：
   - `base_url`: 同一个 Base URL
   - `target_table_name`: `Meeting Records`
   - `maximum_rows`: `500`
5. 只保留与项目关联的会议：
   - 首选：会议 `record_id` 出现在第 3 步的关联 ID 中。
   - 若项目侧关联字段为空，再检查 Meeting Records 中 `Project`、`Project Tracking` 或 `Projects` 的反向关联 ID 是否包含项目 `record_id`。
   - 不按会议正文中的项目名称做模糊匹配。
6. 选择最近一次会议：
   - 日期字段优先级：`Meeting Date` → `Date` → `会议日期` → `Start Time` → `Time`。
   - 正确解析 Feishu 毫秒时间戳、ISO 日期和常见中英文日期文本，再按时间降序。
   - 同日多条时，以返回的 `last_modified_time` 或 `created_time` 较新者优先；若仍相同，明确说同日有多条，不任意隐藏。
   - 所有关联会议都没有可解析日期时，不声称“最近”；报告日期缺失并列出候选标题。
7. 从最新会议中提炼重点，优先读取：
   - `AI Summary` / `Meeting Summary` / `Summary`
   - `Decisions` / `Decision`
   - `Risk` / `Risks`
   - `Latest Progress` / `最新进展记录`
   - `Action Items`
   - `Meeting Notes` / `Content` / `会议记录`
8. 只总结实际存在的字段。保留明确的人名、决定、日期、风险、数字和下一步；禁止补全缺失信息。

## 输出格式

使用简洁中文：

```text
项目：<项目名>（<Maison，仅在有值时>）
最近一次会议：<YYYY-MM-DD；可用时附标题>

会议重点：
- <关键决定或结论>
- <最新进展或风险>
- <下一步和负责人/截止日期，仅数据中存在时>
```

没有关联会议时直接回答：

`已找到项目，但 Meeting Records 关联为空，暂时没有可总结的会议记录。`

不要向用户展示 app token、内部 record ID、字段调试数据或完整原始记录，除非用户明确要求排查。

## 调用预算与停止规则

- 正常情况严格限制为两次只读工具调用：Project Tracking 一次、Meeting Records 一次。
- 权限不足、表不存在、返回空但 Base 明明有数据时，原样概括工具错误并停止。
- 不因为读取失败而改用会议写入工具、Portal 本地 JSON、浏览器抓取或飞书文档工具。
