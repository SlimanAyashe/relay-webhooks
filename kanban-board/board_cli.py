#!/usr/bin/env python3
"""Live-updates kanban-board/index.html so a page refresh always shows current
ticket status. The embedded #board-seed JSON block in index.html is the single
source of truth; localStorage in the browser only wins when its rev is newer
(i.e. never, since only this script bumps rev).

Usage:
  python board_cli.py seed <backlog-import.json>   # one-time: load backlog items
  python board_cli.py list                          # show current board state
  python board_cli.py to-todo <id>                   # backlog -> To Do
  python board_cli.py start <id>                     # backlog/To Do -> In Progress
  python board_cli.py done <id>                      # -> Done
  python board_cli.py block <id> <reason...>          # -> Blocked (creates column)
"""

import json
import re
import sys
from pathlib import Path

HTML_PATH = Path(__file__).parent / "index.html"
SEED_RE = re.compile(
    r'(<script type="application/json" id="board-seed">\n)(.*?)(\n</script>)',
    re.DOTALL,
)


def load_state():
    html = HTML_PATH.read_text(encoding="utf-8")
    m = SEED_RE.search(html)
    if not m:
        raise SystemExit("board-seed script block not found in index.html")
    return json.loads(m.group(2)), html


def save_state(state, html):
    state["rev"] = state.get("rev", 0) + 1
    new_seed = json.dumps(state, separators=(",", ":"))
    new_html = SEED_RE.sub(lambda m: m.group(1) + new_seed + m.group(3), html, count=1)
    HTML_PATH.write_text(new_html, encoding="utf-8")
    print(f"rev {state['rev']} saved.")


def find_task_location(state, item_id):
    """Return ('backlog', idx) or (col_id, idx) for whichever list currently holds item_id."""
    for i, item in enumerate(state["backlog"]):
        if item["id"] == item_id:
            return "backlog", i
    for col_id in state["columns"]:
        ids = state["columnData"][col_id]["taskIds"]
        if item_id in ids:
            return col_id, ids.index(item_id)
    # also allow matching by backlog-id even after it became a task-<n> id
    task_id = "task-" + item_id.removeprefix("backlog-")
    for col_id in state["columns"]:
        ids = state["columnData"][col_id]["taskIds"]
        if task_id in ids:
            return col_id, ids.index(task_id)
    return None, None


def get_task_record(state, item_id):
    """Get the task dict for item_id, whether it's still in backlog or already a task."""
    for item in state["backlog"]:
        if item["id"] == item_id:
            return item, "backlog"
    if item_id in state["tasks"]:
        return state["tasks"][item_id], "task"
    task_id = "task-" + item_id.removeprefix("backlog-")
    if task_id in state["tasks"]:
        return state["tasks"][task_id], "task"
    return None, None


def move_to_column(state, item_id, target_col):
    loc, idx = find_task_location(state, item_id)
    if loc is None:
        raise SystemExit(f"id not found: {item_id}")

    if loc == "backlog":
        item = state["backlog"].pop(idx)
        task_id = "task-" + item["id"].removeprefix("backlog-")
        state["tasks"][task_id] = {
            "id": task_id,
            "title": item["title"],
            "description": item.get("description", ""),
            "priority": item.get("priority", ""),
            "createdAt": item.get("createdAt", 0),
        }
    else:
        task_id = state["columnData"][loc]["taskIds"].pop(idx)

    if target_col not in state["columnData"]:
        state["columnData"][target_col] = {"title": target_col, "taskIds": []}
        if target_col not in state["columns"]:
            state["columns"].insert(2, target_col)  # before Done by default

    state["columnData"][target_col]["taskIds"].append(task_id)
    return task_id


def ensure_column(state, col_id, title):
    if col_id not in state["columnData"]:
        state["columnData"][col_id] = {"title": title, "taskIds": []}
    if col_id not in state["columns"]:
        state["columns"].append(col_id)


def cmd_seed(argv):
    backlog_path = Path(argv[0])
    data = json.loads(backlog_path.read_text(encoding="utf-8"))
    state, html = load_state()
    state["backlog"] = data["backlog"]
    save_state(state, html)
    print(f"Seeded {len(data['backlog'])} backlog items.")


def cmd_list(argv):
    state, _ = load_state()
    print(f"rev {state.get('rev', 0)}")
    for col_id in state["columns"]:
        col = state["columnData"][col_id]
        print(f"\n== {col['title']} ({len(col['taskIds'])}) ==")
        for tid in col["taskIds"]:
            t = state["tasks"].get(tid, {})
            print(f"  [{tid}] {t.get('title')}")
    print(f"\n== Backlog ({len(state['backlog'])}) ==")
    for item in state["backlog"][:10]:
        print(f"  [{item['id']}] {item['title']}")
    if len(state["backlog"]) > 10:
        print(f"  ... and {len(state['backlog']) - 10} more")


def cmd_to_todo(argv):
    state, html = load_state()
    task_id = move_to_column(state, argv[0], "col-todo")
    save_state(state, html)
    print(f"{argv[0]} -> To Do ({task_id})")


def cmd_start(argv):
    state, html = load_state()
    task_id = move_to_column(state, argv[0], "col-doing")
    save_state(state, html)
    print(f"{argv[0]} -> In Progress ({task_id})")


def cmd_done(argv):
    state, html = load_state()
    task_id = move_to_column(state, argv[0], "col-done")
    save_state(state, html)
    print(f"{argv[0]} -> Done ({task_id})")


def cmd_block(argv):
    item_id = argv[0]
    reason = " ".join(argv[1:]) or "blocked"
    state, html = load_state()
    ensure_column(state, "col-blocked", "Blocked")
    task_id = move_to_column(state, item_id, "col-blocked")
    rec, _ = get_task_record(state, task_id)
    if rec is None:
        rec = state["tasks"].get(task_id)
    if rec is not None:
        rec["description"] = (rec.get("description", "") + f"\n\nBLOCKED: {reason}").strip()
    save_state(state, html)
    print(f"{item_id} -> Blocked ({task_id}): {reason}")


COMMANDS = {
    "seed": cmd_seed,
    "list": cmd_list,
    "to-todo": cmd_to_todo,
    "start": cmd_start,
    "done": cmd_done,
    "block": cmd_block,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
