"""Claude Code PermissionRequest hook for the Agent Control Room.

Runs right before Claude Code would show a permission dialog.
  * "Approve here" off (no flag file) or room not running: exit at once with no output, so the normal dialog shows.
  * "Approve here" on: hand the request to the room and wait for Allow or Deny there. After WAIT_SECONDS without an
    answer, exit with no output and the normal dialog shows.

Stdlib only, fail-open: any error means "show the normal dialog". It never approves anything by itself.
"""
import json
import os
import sys
import time
import urllib.request

from ..settings import APPROVALS_FLAG as FLAG

ROOM = os.environ.get("AGENT_CONTROL_ROOM_URL", "http://127.0.0.1:7788")
WAIT_SECONDS = 600   # keep below the hook timeout set by install_hooks.py (660 s)
WINDOW_ONLY = {"AskUserQuestion", "ExitPlanMode"}


def call(method, path, body=None, timeout=5):
    req = urllib.request.Request(ROOM + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Control-Room": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main():
    if not FLAG.exists():
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") in WINDOW_ONLY:
        return 0  # questions and plan approvals need their window; they are not permissions
    try:
        ticket = call("POST", "/api/permission", {
            "session_id": event.get("session_id"),
            "cwd": event.get("cwd"),
            "tool_name": event.get("tool_name"),
            "tool_input": event.get("tool_input"),
        })
    except Exception:
        return 0  # room not running: normal dialog
    request_id = ticket.get("id")
    if not request_id:
        return 0
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline:
        try:
            answer = call("GET", "/api/permission/wait?id=" + request_id, timeout=35)
        except Exception:
            time.sleep(2)
            continue
        behavior = answer.get("behavior")
        if behavior in ("allow", "deny"):
            decision = {"behavior": behavior}
            if behavior == "deny":
                decision["message"] = "Denied from the Agent Control Room."
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))
            return 0
        if behavior == "window":
            return 0  # the user chose to answer in the window
    try:
        call("POST", "/api/permission/expire", {"id": request_id})
    except Exception:
        pass
    return 0

