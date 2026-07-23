"""
Example: Hermes Agent with HTML table editor tool registered.

Requires:
  pip install openai beautifulsoup4   # openai-compatible client works with Hermes
  (or whatever client hermes-agent uses — swap the client call as needed)
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openai import OpenAI          # Hermes API is OpenAI-compatible
from html_editor_tool import html_table_editor, TOOL_DEFINITION
from html_image_tool import html_image_editor, TOOL_DEFINITION as IMAGE_TOOL_DEFINITION

# ── Client setup ──────────────────────────────────────────────────────────────
# Point to wherever your Hermes server is running
client = OpenAI(
    base_url=os.getenv("HERMES_BASE_URL", "http://localhost:8000/v1"),
    api_key=os.getenv("HERMES_API_KEY", "not-needed"),
)
MODEL = os.getenv("HERMES_MODEL", "hermes-3-llama-3.1-70b")

TOOLS = [TOOL_DEFINITION, IMAGE_TOOL_DEFINITION]

# ── Agent loop ────────────────────────────────────────────────────────────────

def run_agent(user_message: str, max_turns: int = 10) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant that can read and edit an HTML table file "
                "representing a multi-sheet Excel workbook. "
                "When asked to modify content, always read the relevant cells first to confirm "
                "what is there before making changes. Report what you changed."
            ),
        },
        {"role": "user", "content": user_message},
    ]

    for _ in range(max_turns):
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        # No tool call → final answer
        if not msg.tool_calls:
            return msg.content

        # Execute each tool call the model requested
        messages.append(msg)
        for tool_call in msg.tool_calls:
            args = json.loads(tool_call.function.arguments)
            action = args.pop("action")
            if tool_call.function.name == "html_image_editor":
                result = html_image_editor(action, **args)
            else:
                result = html_table_editor(action, **args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    return "Max turns reached without a final answer."


# ── Example usage ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example 1: Ask the agent to find and update a value
    reply = run_agent(
        "In the 'B26 Stats' sheet, find the cell that says 'Rachael JIANG' "
        "and tell me which row and column it's in."
    )
    print("Agent:", reply)

    # Example 2: Bulk update via natural language
    # reply = run_agent(
    #     "In the 'Todo' sheet, replace all occurrences of '1.' with 'Step 1.'"
    # )
    # print("Agent:", reply)
