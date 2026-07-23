"""
Qwen-powered agent that edits the HTML table file via natural language.

Reuses html_editor_tool.py (the same handler ports to Hermes later — only the
registration wrapper differs). Talks to Alibaba DashScope's OpenAI-compatible
endpoint, so the tool-calling format is identical to what Hermes expects.

Run:
    python3 qwen_agent.py                      # runs the built-in demo
    python3 qwen_agent.py "your instruction"   # one-shot custom instruction
    python3 qwen_agent.py -i                    # interactive REPL
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openai import OpenAI

from html_editor_tool import html_table_editor, TOOL_DEFINITION
from html_image_tool import html_image_editor, TOOL_DEFINITION as IMAGE_TOOL_DEFINITION
import change_logger

# ── Config ────────────────────────────────────────────────────────────────────
# Reuse the same key as test_qwen_apikey.py (env var wins if set).
os.environ.setdefault("DASHSCOPE_API_KEY", "sk-b5c1660a59294788993da81acc122415")

client = OpenAI(
    api_key=os.environ["DASHSCOPE_API_KEY"],
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MODEL = os.getenv("QWEN_MODEL", "qwen3-max")

TOOLS = [TOOL_DEFINITION, IMAGE_TOOL_DEFINITION]

SYSTEM_PROMPT = (
    "你是一个负责编辑 HTML 表格文件的助手。这个 HTML 由一个多 sheet 的 Excel 转换而来。\n"
    "你有一个 html_table_editor 工具,支持以下 action:\n"
    "  - list_sheets: 列出所有 sheet 名\n"
    "  - find_cell(sheet_name,text,exact_match): 按内容定位,返回所有匹配单元格的精确 row/col 坐标\n"
    "  - read_cell(sheet_name,row,col): 读单个单元格(行列都从 1 开始)\n"
    "  - read_range(sheet_name,start_row,end_row,start_col,end_col): 读一块区域\n"
    "  - update_cell(sheet_name,row,col,new_value): 改单个单元格\n"
    "  - find_and_replace(sheet_name,find_text,replace_text,exact_match): 按文本查找替换\n\n"
    "你还有一个 html_image_editor 工具,管理某个 sheet 的图片区(图片仅展示,与文字表格分开):\n"
    "  - list_images(sheet_name): 列出该 sheet 的图片及 1-based 序号\n"
    "  - add_image(sheet_name,image_url,position): 从 URL 新增一张图(position 省略则加到末尾)\n"
    "  - replace_image(sheet_name,index,image_url): 把第 index 张换成 URL 指向的新图\n"
    "  - delete_image(sheet_name,index): 删除第 index 张\n"
    "  - reorder_images(sheet_name,new_order): new_order 是 1..N 的排列,按它重排\n\n"
    "工作原则:\n"
    "1. 定位目标单元格时,优先用 find_cell 拿到精确坐标,不要自己数行列号(尤其是有合并单元格的横向表格)。\n"
    "2. find_cell 返回的 row/col 可以直接传给 read_cell 或 update_cell。\n"
    "3. 如果 find_cell 返回多个匹配,先 read_range 看清上下文,确认改哪一个,不要误改。\n"
    "4. 如果不确定 sheet 名,先 list_sheets。\n"
    "5. 操作图片前,先用 list_images 看清当前有哪些图、序号是多少,再增删改。\n"
    "6. 完成后用中文向用户说明你具体改了哪里、从什么改成了什么。\n"
    "7. 每次修改类操作后,工具返回里会带 change_log(含 batch_id 和待写日志行)。你必须确定本次修改的 why(为什么改):若用户指令里已说明原因,直接用它;交互模式下可向用户确认;一次性指令下用一句简要原因概括本次修改。然后调用 html_table_editor 的 commit_change_log(batch_id, why) 落库。actor/maison/project/status/时间已自动填好,无需询问。\n"
    "8. 若工具返回里出现 pending_drift,说明有绕过工具的改动尚未记录,先用 commit_change_log(pending_drift 的 batch_id, why) 记录这些改动(why 用'外部直接修改,补登'之类的说明),再继续当前任务。\n"
)


def _bootstrap_change_log():
    """本地/测试运行前的准备:建库、(首次)用当前 HTML 设基线、确保 actor 有值。

    - actor: 本地没有 Hermes profile,默认给一个占位人名(可用 CHANGE_LOG_ACTOR 覆盖)。
    - baseline: 仅在尚无基线时 seed,避免首次编辑把整份文档误记成改动;已有基线则不动。
    """
    os.environ.setdefault("CHANGE_LOG_ACTOR", "本地测试用户")
    try:
        import sqlite3
        change_logger.init_db()
        conn = sqlite3.connect(change_logger.DB_PATH)
        has_baseline = conn.execute("SELECT COUNT(*) FROM baseline").fetchone()[0]
        conn.close()
        if not has_baseline:
            change_logger.seed_baseline()
    except Exception as e:
        print(f"[change_log bootstrap warning] {e}", file=sys.stderr)


# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent(user_message: str, max_turns: int = 12, verbose: bool = True) -> str:
    _bootstrap_change_log()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    for turn in range(max_turns):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0,
        )
        msg = response.choices[0].message

        # No tool call → final answer
        if not msg.tool_calls:
            return msg.content or ""

        # Append the assistant's tool-call message
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        # Execute each requested tool call
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            action = args.pop("action", None)
            if verbose:
                print(f"  [tool] {action}({json.dumps(args, ensure_ascii=False)})")
            if not action:
                result = json.dumps({"ok": False, "error": "missing 'action'"}, ensure_ascii=False)
            elif tc.function.name == "html_image_editor":
                result = html_image_editor(action, **args)
            else:
                result = html_table_editor(action, **args)
            if verbose:
                preview = result if len(result) < 300 else result[:300] + "…"
                print(f"  [result] {preview}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

    return "(达到最大轮数仍未得到最终回答)"


# ── Entrypoints ───────────────────────────────────────────────────────────────

DEMO_INSTRUCTION = (
    "在 'B26 Stats' 这个 sheet 里,Louis Vuitton 这一列的 Owner 现在是谁?"
    "请把它从 'Rachael JIANG' 改成 'Rachael JIANG (FLG Lead)'。"
)


def interactive():
    print(f"Qwen agent ready (model={MODEL}). 输入指令,空行退出。\n")
    while True:
        try:
            line = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        answer = run_agent(line)
        print(f"\nAgent> {answer}\n")


def show_log(limit: int = 20):
    """打印最近的审计日志行,方便测试时快速查看落库结果。"""
    import sqlite3
    change_logger.init_db()
    conn = sqlite3.connect(change_logger.DB_PATH)
    rows = conn.execute(
        "SELECT timestamp, actor, maison, project, status, change_type,"
        " old_value, new_value, why, is_derived, source FROM change_log"
        " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    if not rows:
        print("(change_log 为空)")
        return
    for r in reversed(rows):
        ts, actor, maison, project, status, ctype, old, new, why, derived, source = r
        print(f"[{ts}] {actor} | {maison} / {project} | {status} | {ctype}: "
              f"{old!r}→{new!r} | why={why} | derived={derived} | src={source}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--show-log":
        show_log(int(sys.argv[2]) if len(sys.argv) > 2 else 20)
    elif len(sys.argv) > 1 and sys.argv[1] in ("-i", "--interactive"):
        interactive()
    elif len(sys.argv) > 1:
        print(run_agent(" ".join(sys.argv[1:])))
    else:
        print(f"=== DEMO (model={MODEL}) ===")
        print(f"指令: {DEMO_INSTRUCTION}\n")
        print(run_agent(DEMO_INSTRUCTION))
