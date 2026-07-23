"""审计日志(change_log)端到端实测脚本 —— 走真实 qwen agent。

完全隔离:把真实的 'Project Tracking V3.html' 复制到临时文件,并使用临时 DB,
所以你的工作文件和真实 data/change_log.db 都不会被动到。

每个场景:
  1. 把自然语言指令交给真实 qwen agent(会真的调 DashScope)。
  2. 捕获 agent 的工具调用过程([tool]/[result])。
  3. 对比该场景前后 change_log / pending_batch 的变化,作为权威结果。
最后把整个过程写入 docs/change-log-test-log.md。

Run:
    python3 agents/run_change_log_tests.py
"""
import contextlib
import datetime
import io
import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import html_editor_tool as het
import change_logger as cl

# ── 隔离环境 ───────────────────────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix="changelog_test_")
REAL_HTML = os.path.join(ROOT, "Project Tracking V3.html")
TEST_HTML = os.path.join(TMP, "report.html")
shutil.copyfile(REAL_HTML, TEST_HTML)

het.HTML_FILE_PATH = TEST_HTML            # 所有表格/图片工具改这个临时副本
cl.DB_PATH = os.path.join(TMP, "change_log.db")   # 审计日志写临时 DB
os.environ["CHANGE_LOG_ACTOR"] = "测试-小陈"        # 本地无 Hermes profile,用占位人名

import qwen_agent  # 在打补丁后导入;工具函数在调用时才读全局路径,所以顺序无碍


def _db():
    return sqlite3.connect(cl.DB_PATH)


def _counts():
    conn = _db()
    try:
        cl_n = conn.execute("SELECT COUNT(*) FROM change_log").fetchone()[0]
        pend = conn.execute("SELECT COUNT(*) FROM pending_batch").fetchone()[0]
    finally:
        conn.close()
    return cl_n, pend


def _new_log_rows(since_id):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id, actor, maison, project, status, change_type, old_value,"
            " new_value, why, is_derived, source FROM change_log WHERE id > ?"
            " ORDER BY id", (since_id,)).fetchall()
        max_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM change_log").fetchone()[0]
    finally:
        conn.close()
    return rows, max_id


def _fmt_rows(rows):
    out = []
    for r in rows:
        _id, actor, maison, project, status, ctype, old, new, why, derived, source = r
        out.append(
            f"  - {actor} | {maison} / {project} | status={status} | {ctype}: "
            f"{old!r}→{new!r} | why={why!r} | derived={derived} | src={source}")
    return "\n".join(out) if out else "  (无新增日志行)"


LOG = []   # markdown 行


def section(title):
    LOG.append(f"\n## {title}\n")
    print(f"\n===== {title} =====")


def note(text):
    LOG.append(text + "\n")
    print(text)


def run_scenario(idx, title, instruction, pre_hook=None):
    section(f"场景 {idx}:{title}")
    if pre_hook:
        pre_hook()
    _, max_id = _new_log_rows(0)
    cl_before, pend_before = _counts()
    note(f"**指令**:{instruction}\n")
    note(f"_(运行前:change_log={cl_before} 行,pending_batch={pend_before})_\n")

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            answer = qwen_agent.run_agent(instruction, verbose=True)
    except Exception as e:
        answer = f"(运行异常:{type(e).__name__}: {e})"
    trace = buf.getvalue().strip()

    note("**Agent 工具调用过程**:")
    LOG.append("```\n" + (trace or "(无工具调用)") + "\n```\n")
    print(trace)
    note(f"\n**Agent 最终回复**:{answer}\n")

    rows, _ = _new_log_rows(max_id)
    cl_after, pend_after = _counts()
    note(f"**本场景新增审计日志**(change_log {cl_before}→{cl_after},pending {pend_before}→{pend_after}):")
    LOG.append(_fmt_rows(rows) + "\n")
    print(_fmt_rows(rows))


def main():
    # 初始化:用临时副本设基线(已有内容不算改动)
    cl.init_db()
    cl.seed_baseline()
    note(f"# 审计日志(change_log)实测记录\n")
    note(f"- 时间:{datetime.datetime.now().isoformat(timespec='seconds')}")
    note(f"- 模型:{qwen_agent.MODEL}")
    note(f"- 隔离环境:HTML={TEST_HTML} ,DB={cl.DB_PATH}")
    note(f"- actor(本地占位):{os.environ['CHANGE_LOG_ACTOR']}")
    note(f"- 说明:已用临时 HTML 副本 seed_baseline,真实文件未被改动。\n")

    # 场景 1:普通单格修改 + agent 自动 commit
    run_scenario(
        1, "单格修改 + 自动落日志",
        "在 Givenchy 这个 sheet 里,把项目 'China AI omni landscape' 的 status "
        "从 'Ideation' 改成 'Pilot'。原因:已确认进入试点阶段。改完后记得记录这次修改。",
    )

    # 场景 2:find_and_replace
    run_scenario(
        2, "查找替换 + 落日志",
        "在 Givenchy 这个 sheet 里,把项目 owner 'Qiwen MO' 改成 'Qiwen MO (代理)'。"
        "原因:负责人临时代理。改完记录修改。",
    )

    # 场景 3:drift 兜底 —— 绕过工具直接改文件,再让 agent 做事
    def bypass_edit():
        with open(TEST_HTML, "r", encoding="utf-8") as f:
            c = f.read()
        # 直接把 Chenlong WANG 改成 Chenlong WANG (Lead),模拟 sandbox/手动改
        c2 = c.replace("Chenlong WANG", "Chenlong WANG (Lead)", 1)
        with open(TEST_HTML, "w", encoding="utf-8") as f:
            f.write(c2)
        note("_(已绕过工具,直接修改临时 HTML:Chenlong WANG → Chenlong WANG (Lead))_\n")

    run_scenario(
        3, "drift 安全网(绕过工具的改动被兜回)",
        "列出 Givenchy 这个 sheet 当前所有项目和它们的状态。",
        pre_hook=bypass_edit,
    )

    # 汇总
    section("最终 change_log 全量")
    conn = _db()
    try:
        allrows = conn.execute(
            "SELECT id, actor, maison, project, status, change_type, old_value,"
            " new_value, why, is_derived, source FROM change_log ORDER BY id").fetchall()
    finally:
        conn.close()
    LOG.append(_fmt_rows([r[0:] for r in allrows]) + "\n")
    print(_fmt_rows([r[0:] for r in allrows]))

    out_path = os.path.join(ROOT, "docs", "change-log-test-log.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(LOG))
    print(f"\n>>> 测试记录已写入 {out_path}")
    print(f">>> 临时环境保留在 {TMP}(测试用,可删)")


if __name__ == "__main__":
    main()
