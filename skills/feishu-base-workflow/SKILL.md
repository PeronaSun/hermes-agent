---
name: feishu-base-workflow
description: "处理用户提供的 Maison、Project 和 Meeting Notes，将内容一次性写入 Feishu Base 的 Project Tracking、Action Items 和 Meeting Records。该 Skill 禁止使用旧的单字段更新 workflow。"
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [feishu, bitable, base, meeting, project-tracking]
---

# Feishu Base Workflow

Use this skill whenever a Feishu user provides Maison, Project, and Meeting Notes for a project meeting update.

This is a deterministic workflow. Do not search for alternative tools, views, skills, or manual workarounds. The purpose of this skill is to prevent slow exploratory loops.

## Required Tool Path

Normal meeting workflow must use only:

1. `feishu_bitable_prepare_project_meeting_write`
2. `feishu_bitable_apply_project_meeting_write`

Do not use these legacy single-cell tools unless the user explicitly asks to edit one existing cell:

- `feishu_bitable_prepare_record_update`
- `feishu_bitable_apply_record_update`

Do not use these older meeting tools for the Project Tracking + Meeting Records + Action Items workflow:

- `feishu_bitable_prepare_meeting_write`
- `feishu_bitable_apply_meeting_write`

Never call `feishu_bitable_read_table`, `feishu_bitable_list_records`, `feishu_bitable_list_fields`, or `feishu_bitable_list_views` manually to prepare this meeting write. The high-level prepare tool performs the required reads internally.

## Stop Rules

If required input is missing, ask for it and stop. Do not call a Feishu tool.

Required input:

- Maison
- Project
- Meeting Notes
- Feishu Base URL or configured app token

If `feishu_bitable_prepare_project_meeting_write` returns an error, show that error in concise Chinese and stop. Do not try another Feishu tool in the same turn.

If `feishu_bitable_apply_project_meeting_write` returns an error, show that error in concise Chinese and stop. Do not try single-cell updates, Docs tools, comments, local files, browser automation, terminal commands, or another preview in the same turn.

If the error says the view ID is wrong, ask the user for the correct Base URL/view or retry only by omitting `project_view_id`; do not guess a view ID.

If the error says a field is not writable, report the field name and stop unless the high-level tool explicitly returned a successful preview with that field listed as skipped.

## Standard Flow

1. Identify Maison, Project, and Meeting Notes.
2. Find the unique matching Project Tracking record.
3. Append Risk and 最新进展记录; never overwrite existing content.
4. Create one new Meeting Records row.
5. Create one new Action Items row for every user-supplied or clearly extracted task.
6. Link Project and Source Meeting relationships.
7. Generate one unified preview.
8. Ask only: `是否确认写入飞书？请回复：确认`
9. After the user replies `确认`, `同意`, `可以`, `写入`, or `确认写入`, call `feishu_bitable_apply_project_meeting_write`.
10. Repeated confirmations must not create duplicate records or append duplicate text.

Tool budget: one high-level prepare call for preview, one high-level apply call for confirmation. More tool calls are a bug unless the user explicitly asks to read/debug the Base.

## Input Rules

Support these labels:

- Maison / 品牌
- Project / 项目 / 分组 / Project Type
- Meeting Notes / 会议记录 / 会议内容
- Risk / 风险
- 最新进展记录 / Latest Progress
- Action Items / 待办事项

If the user explicitly provides Risk, 最新进展记录, or Action Items, use exactly those values. Extract from Meeting Notes only when the user did not provide them.

Never invent tasks, owners, dates, status, priority, risks, or progress.

## Matching Rules

Project Tracking must match:

1. Maison exact match.
2. 分组 exact match with Project.
3. Existing non-empty records beat blank records.
4. Never create a Project Tracking row.

If the matched Project Tracking primary field `任务描述` is empty, set it to `{Maison} {Project}` inside the unified write.

## Confirmation Rules

Never show Change ID, Change Set ID, `fbu_`, or `fbm_` in normal chat.

The user only confirms with one plain message:

`确认`

or:

`同意`, `可以`, `写入`, `确认写入`

If no pending preview exists, reply:

`当前没有待确认的修改，请先发送会议内容或重新生成预览。`

If already applied, reply:

`本次会议内容已写入飞书，无需重复执行。`
