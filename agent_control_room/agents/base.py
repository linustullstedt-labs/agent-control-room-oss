"""The interface every agent plug-in implements. To add an agent, subclass Agent and register it in agents/__init__.py.

A session is a plain dict the page renders. Required keys:
  key        unique and stable, "<vendor>:<id>"
  vendor     the plug-in's `vendor`
  client     where it runs: "vscode", "terminal", "bg", "desktop", ... (the plug-in decides what jump() does with it)
  name, repo, subtitle, cwd
  state      one of STATES; never guess "needs-permission"/"needs-answer" (use "unknown" when unsure)
  since      ms timestamp of the current state, startedAt
  line       one-line summary, full (longer Markdown text), task (last prompt)
  canReply   True only when reply() can deliver a message right now
  source     where the state came from, shown under "Show details"
Optional: sessionId, pid, and anything the plug-in itself needs later (inbox paths, thread ids, ...).
"""
import json
import os
import sys

STATES = ("working", "needs-permission", "needs-answer", "finished", "dead", "unknown")
REPLY_STATES = {"finished", "working"}  # a pending prompt or dialog must be answered in the window


class Agent:
    vendor = "agent"            # short id used in keys and the page's filter
    label = "Agent"             # shown to people
    launch_targets = ()         # any of "window", "new-window", "terminal", "app"

    def sessions(self):
        """Every live session, as session dicts. Raise with a helpful message when the agent cannot be read."""
        return []

    def jump(self, session):
        """Bring the session's window to the front. Returns (ok, message)."""
        return False, "{} cannot open this session yet.".format(self.label)

    def reply(self, session, text):
        """Deliver a message the user typed in the room. Returns (ok, message)."""
        return False, "{} cannot take a message from here. Open its window.".format(self.label)

    def stop(self, session):
        return False, "Only background sessions can be stopped from here."

    def launch(self, folder, prompt, target):
        """Start a new agent in folder with an optional first prompt. Returns (ok, message)."""
        return False, "Starting {} from the room is not supported yet.".format(self.label)

    def recent_folders(self):
        """{folder: last used (epoch seconds)}, so the launcher can offer folders the user works in."""
        return {}


def first_cwd(path):
    """The working folder recorded near the top of a transcript or rollout log."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(30):
                line = fh.readline()
                if not line:
                    break
                if '"cwd"' in line:
                    rec = json.loads(line)
                    cwd = rec.get("cwd") or (rec.get("payload") or {}).get("cwd")
                    if cwd:
                        return cwd
    except (OSError, ValueError):
        pass
    return None


def executable(path):
    return bool(path) and os.path.isfile(path) and (sys.platform.startswith("win") or os.access(path, os.X_OK))
