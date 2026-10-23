"""Claude Code: `claude agents --json` (documented) for liveness and state, enriched read-only from
~/.claude/sessions/<pid>.json (entrypoint, nameSource, statusUpdatedAt, messagingSocketPath) and the transcript
(title, last prompt, last reply, pending tool). Never reads *.key files or token fields.
"""
import glob
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

from .. import system
from ..util import (HOME, FileCache, clip, drop_restatement, keep, now_ms, plain, read_fields, repo_name, subtitle_for,
                    tail_lines, within)
from .base import REPLY_STATES, Agent, executable, first_cwd

CLAUDE_PROJECTS = HOME / ".claude" / "projects"
CLAUDE_REGISTRY = HOME / ".claude" / "sessions"
CLAUDE_IDE_LOCKS = HOME / ".claude" / "ide"
CLAUDE_APP_ENTRYPOINTS = {"claude-desktop", "desktop", "local-agent"}
PERMISSION_WAITS = {"permission prompt", "sandbox request", "worker request"}
MAX_REPLY_CHARS = 20000
REPLY_HEADER = ("Your user sent this from the Agent Control Room (a local dashboard), not typed in this window. "
                "Their chat window does not show it, so start your answer with one line that restates it, "
                "beginning with \"You asked:\".\n\n")
REPLY_MARKER = "Your user sent this from the Agent Control Room"
PEER_INTRO = "Another Claude session sent a message:"
PEER_FOOTER = "\n\nThis came from another Claude session"
WINDOWS = sys.platform.startswith("win")


def peer_text(text):
    """Inbox messages arrive wrapped; return the inner text, marking ones sent from this room."""
    body = text[len(PEER_INTRO):].split(PEER_FOOTER)[0].strip()
    if body.startswith(REPLY_MARKER):
        inner = body.split("\n\n", 1)[1] if "\n\n" in body else body.split(":", 1)[-1]
        return "You, from the room: " + inner.strip()
    return "From another session: " + body


def claude_user_text(message):
    content = message.get("content")
    if isinstance(content, str) and content.startswith(PEER_INTRO):
        return peer_text(content)
    if isinstance(content, str):
        blocks = [content]
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        blocks = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    blocks = [b for b in blocks if b.strip() and not b.lstrip().startswith("<")]
    return "\n".join(blocks).strip() or None


def tool_summary(name, tool_input):
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if name == "Bash" and tool_input.get("command"):
        return tool_input["command"]
    if name == "AskUserQuestion":
        questions = tool_input.get("questions") or []
        if questions and isinstance(questions[0], dict):
            return questions[0].get("question", name)
    for key in ("file_path", "notebook_path", "path", "url", "pattern", "query", "description"):
        if tool_input.get(key):
            return "{} {}".format(name, tool_input[key])
    return name


def parse_claude_transcript(path):
    info = {"title": None, "prompt": None, "reply": None, "tool": None, "tool_name": None}
    for line in reversed(tail_lines(path)):
        if '"type"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        kind = rec.get("type")
        if kind == "ai-title":
            info["title"] = info["title"] or rec.get("aiTitle")
        message = rec.get("message") or {}
        content = message.get("content")
        is_peer = isinstance(content, str) and content.startswith(PEER_INTRO)
        if rec.get("isSidechain") or (rec.get("isMeta") and not is_peer):
            continue
        if kind == "user" and info["prompt"] is None:
            info["prompt"] = claude_user_text(message)
        elif kind == "assistant":
            for block in reversed(message.get("content") or []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and info["reply"] is None and block.get("text", "").strip():
                    info["reply"] = block["text"]
                if block.get("type") == "tool_use" and info["tool"] is None:
                    info["tool_name"] = block.get("name")
                    info["tool"] = tool_summary(block.get("name"), block.get("input"))
        if info["prompt"] and info["reply"] and info["tool"] and info["title"]:
            break
    return info


CLAUDE_TRANSCRIPTS = FileCache(parse_claude_transcript)


def claude_transcript(session_id):
    if not session_id or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", session_id):
        return {}
    matches = glob.glob(str(CLAUDE_PROJECTS / "*" / (session_id + ".jsonl")))
    return CLAUDE_TRANSCRIPTS.get(Path(matches[0])) if matches else {}


_claude_bin = {"path": None, "at": 0}


def claude_candidates():
    """Every Claude Code install, in preference order. An app started from the Dock or Start menu often has a short
    PATH, and some people only have the binary bundled with the VS Code (or Cursor) extension."""
    exe = "claude.exe" if WINDOWS else "claude"
    candidates = [shutil.which("claude"), str(HOME / ".local" / "bin" / exe), str(HOME / ".claude" / "local" / exe)]
    if not WINDOWS:
        candidates += ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    bundled = []
    for editor in (".vscode", ".cursor", ".vscode-insiders"):
        bundled += glob.glob(str(HOME / editor / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / exe))

    def version_key(path):
        m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)
    candidates += sorted(bundled, key=version_key, reverse=True)
    found = []
    for c in candidates:
        if executable(c) and os.path.realpath(c) not in map(os.path.realpath, found):
            found.append(c)
    return found


def claude_binary():
    """The install that last answered `claude agents --json`, else the first one found."""
    if _claude_bin["path"] and time.time() - _claude_bin["at"] < 300 and os.path.exists(_claude_bin["path"]):
        return _claude_bin["path"]
    found = claude_candidates()
    return found[0] if found else None


def claude_agents_json():
    """Run `claude agents --json` with the first install new enough to have it. An old CLI on PATH next to a newer
    VS Code extension is common, so an "unknown option" failure moves on to the next install instead of giving up."""
    import subprocess
    cached = _claude_bin["path"] if _claude_bin["path"] and time.time() - _claude_bin["at"] < 300 else None
    candidates = claude_candidates()
    if not candidates:
        raise RuntimeError("Claude Code was not found (no claude command and no VS Code extension)")
    if cached in candidates:
        candidates.remove(cached)
        candidates.insert(0, cached)
    too_old, failure = [], None
    for binary in candidates:
        proc = subprocess.run([binary, "agents", "--json"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode == 0:
            _claude_bin.update(path=binary, at=time.time())
            return proc.stdout
        if "unknown option" in proc.stderr or "unknown command" in proc.stderr:
            too_old.append(binary)
        elif failure is None:
            failure = "claude agents --json failed ({}): {}".format(binary, clip(proc.stderr, 200))
    if failure:
        raise RuntimeError(failure)
    raise RuntimeError("Claude Code is too old to list its sessions (no `claude agents --json` in {}). "
                       "Update it: run `claude update` in a terminal, or update the Claude Code extension in VS Code, "
                       "then reopen the room.".format(", ".join(too_old)))


def inbox_usable(path):
    """The session inbox is a Unix socket on macOS and Linux. Other kinds are not used until verified."""
    if not path or not hasattr(socket, "AF_UNIX") or WINDOWS:
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    # only Claude Code's own socket folder, owned by this user
    return stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid() and (
        within(path, "/tmp/cc-socks") or within(path, os.path.join(tempfile.gettempdir(), "cc-socks")))


def send_inbox(session, text):
    """Post one message to a Claude session's inbox socket (documented in cross-session-messaging).

    Claude Code labels it as a message from another session: it can steer the session but can never
    approve a permission prompt or change settings. That boundary is intended.
    """
    path = session.get("inbox")
    if not inbox_usable(path):
        return False, "The session's inbox is gone or failed a safety check. Open its window."
    line = json.dumps({"type": "user", "message": {"role": "user", "content": REPLY_HEADER + text}}) + "\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect(path)
        sock.sendall(line.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
        try:
            answer = sock.recv(4096).decode("utf-8", "replace").strip()
        except socket.timeout:
            answer = ""
    except OSError as exc:
        return False, "Could not reach the session: {}".format(exc)
    finally:
        sock.close()
    if answer and any(w in answer.lower() for w in ("refus", "reject", "drop", "error")):
        return False, "The session refused the message: " + clip(answer, 160)
    if session.get("state") == "working":
        return True, "Queued for {}. It reads it between steps.".format(session["name"])
    return True, "Sent to {}.".format(session["name"])


class ClaudeCode(Agent):
    vendor = "claude"
    label = "Claude Code"
    launch_targets = ("window", "new-window", "terminal")

    def sessions(self):
        out = []
        for agent in json.loads(claude_agents_json() or "[]"):
            out.append(self._row(agent))
        return out

    def _row(self, agent):
        pid = agent.get("pid")
        reg = read_fields(CLAUDE_REGISTRY / "{}.json".format(pid),
                          ["entrypoint", "nameSource", "statusUpdatedAt", "messagingSocketPath"]) if pid else {}
        tr = claude_transcript(agent.get("sessionId"))
        status, waiting, bg_state = agent.get("status"), agent.get("waitingFor"), agent.get("state")

        if bg_state == "failed":
            state = "dead"
        elif status == "waiting":
            state = "needs-permission" if waiting in PERMISSION_WAITS else "needs-answer"
        elif status == "busy" or bg_state == "working":
            state = "working"
        elif status == "idle" or bg_state in ("done", "stopped"):
            state = "finished"
        else:
            state = "unknown"

        cwd = agent.get("cwd") or ""
        if agent.get("kind") == "background":
            client = "bg"
        elif reg.get("entrypoint") == "claude-vscode":
            client = "vscode"
        elif reg.get("entrypoint") == "cli":
            client = "terminal"
        elif reg.get("entrypoint") in CLAUDE_APP_ENTRYPOINTS or "Claude" in Path(cwd).parts and (
                "Application Support" in Path(cwd).parts or "AppData" in Path(cwd).parts):
            client = "claude-app"
        else:
            client = reg.get("entrypoint") or "unknown"

        name = agent.get("name") or ""
        if reg.get("nameSource") in (None, "derived") and tr.get("title"):
            name = tr["title"]

        if state == "needs-permission":
            line = "Wants to run: " + (tr.get("tool") or "a tool")
            full = tr.get("tool") or "Open its window to see the request."
        elif state == "needs-answer":
            asked = tr.get("tool") if tr.get("tool_name") == "AskUserQuestion" else tr.get("reply")
            line = "Asks: " + clip(plain(asked or waiting or "needs your input"), 200)
            full = asked or "Open its window to answer."
        elif state == "finished":
            line = clip(plain(drop_restatement(tr.get("reply"))), 200) or "Waiting for a first prompt"
            full = drop_restatement(tr.get("reply")) or ""
        elif state == "dead":
            line = "Stopped unexpectedly while: " + clip(tr.get("prompt"), 160)
            full = "The background session failed. Open it to resume."
        else:
            line = clip(tr.get("prompt"), 200) or "Working"
            full = tr.get("prompt") or ""

        repo = "Claude app" if client == "claude-app" else repo_name(cwd)
        inbox = reg.get("messagingSocketPath")
        return {
            "repo": repo,
            "subtitle": subtitle_for(clip(name, 80), repo),
            "key": "claude:" + str(agent.get("sessionId") or pid),
            "vendor": self.vendor,
            "client": client,
            "name": clip(name, 80) or "Claude session",
            "cwd": cwd,
            "state": state,
            "since": reg.get("statusUpdatedAt") or agent.get("startedAt") or now_ms(),
            "startedAt": agent.get("startedAt") or 0,
            "line": line,
            "full": keep(full),
            "task": clip(tr.get("prompt"), 600),
            "sessionId": agent.get("sessionId"),
            "pid": pid,
            "bgId": agent.get("id"),
            "inbox": inbox,
            "canReply": bool(inbox) and state in REPLY_STATES and inbox_usable(inbox),
            "source": "claude agents --json: status={} waitingFor={}{}".format(
                status, waiting, " state=" + bg_state if bg_state else ""),
        }

    def jump(self, session):
        from . import vscode
        import subprocess
        client, sid = session["client"], session.get("sessionId") or ""
        if client == "vscode":
            vscode.open_folder(vscode.folder_for(session["cwd"]), settle=0.6)
            if re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid):
                system.open_url("vscode://anthropic.claude-code/open?session=" + urllib.parse.quote(sid))
                return True, "Opened {} in VS Code".format(session["name"])
            return True, "Raised the VS Code window for {}".format(os.path.basename(session["cwd"]))
        if client == "bg" and session.get("bgId") and re.fullmatch(r"[A-Za-z0-9_-]+", session["bgId"]):
            ok, msg = system.run_in_terminal(session["cwd"] or str(HOME), [claude_binary() or "claude", "attach", session["bgId"]])
            return ok, "Attached to {} in a new terminal window".format(session["name"]) if ok else msg
        if client == "terminal" and session.get("pid"):
            return system.focus_terminal(session["pid"], session["name"])
        if client == "claude-app" and re.fullmatch(r"[0-9a-fA-F-]{8,64}", sid):
            proc = subprocess.run([claude_binary() or "claude", "--desktop", "--resume", sid], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode == 0:
                return True, "Opened {} in the Claude app".format(session["name"])
            return False, "Could not open the session in the Claude app. Pick it there."
        return False, "No way to open a {} session yet.".format(client)

    def reply(self, session, text):
        text = (text or "").strip()
        if not text:
            return False, "Type a message first."
        if len(text) > MAX_REPLY_CHARS:
            return False, "The message is too long. Keep it under {} characters.".format(MAX_REPLY_CHARS)
        if not session.get("canReply"):
            return False, "This session cannot take a message from here. Open its window."
        return send_inbox(session, text)

    def stop(self, session):
        import subprocess
        bg = session.get("bgId")
        if session.get("client") != "bg" or not bg or not re.fullmatch(r"[A-Za-z0-9_-]+", bg):
            return False, "Only background sessions can be stopped from here."
        proc = subprocess.run([claude_binary() or "claude", "stop", bg], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=20,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            return False, "claude stop failed: " + clip(proc.stderr or proc.stdout, 160)
        return True, "Stopped {}. Its conversation is kept; open it to continue.".format(session["name"])

    def recent_folders(self):
        used = {}
        for proj in CLAUDE_PROJECTS.iterdir() if CLAUDE_PROJECTS.is_dir() else []:
            files = sorted(proj.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            if files:
                cwd = first_cwd(files[0])
                if cwd:
                    used[cwd] = max(used.get(cwd, 0), files[0].stat().st_mtime)
        return used

    def _wait_for_new_session(self, folder, since_ms, seconds=12):
        """A new Claude tab in VS Code starts its session process at once; find it so the task can go to its inbox."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            time.sleep(0.75)
            try:
                for s in self.sessions():
                    if within(s.get("cwd"), folder) and (s.get("startedAt") or 0) >= since_ms - 3000 and s.get("canReply"):
                        return s
            except Exception:
                pass
        return None

    def launch(self, folder, prompt, target):
        from . import vscode
        label = os.path.basename(folder)
        if target == "terminal":
            argv = [claude_binary() or "claude"] + ([prompt] if prompt else [])
            ok, msg = system.run_in_terminal(folder, argv)
            return (True, "Started a Claude agent in {} in a terminal.".format(label)) if ok else (False, msg)
        if target not in ("window", "new-window"):
            return False, "Choose where to open the agent."
        launched_at = now_ms()
        vscode.open_folder(folder, new_window=target == "new-window", settle=1.5 if target == "new-window" else 0.7)
        if not prompt:
            system.open_url("vscode://anthropic.claude-code/open")
            return True, "New Claude tab in {}.".format(label)
        # No key presses: open an empty tab, wait for its session, and hand it the task through its inbox.
        system.open_url("vscode://anthropic.claude-code/open")
        session = self._wait_for_new_session(folder, launched_at)
        if session:
            ok, msg = send_inbox(dict(session, state="finished"), prompt)
            if ok:
                return True, ("Started a Claude agent in {} with your task. It restates the task first, because VS Code "
                              "does not show it.").format(label)
        link = "vscode://anthropic.claude-code/open?" + urllib.parse.urlencode({"prompt": prompt}, quote_via=urllib.parse.quote)
        system.open_url(link)
        time.sleep(1.2)  # let the new tab render with the task in its input
        ok, why = system.press_enter_in("Code", "VS Code")
        if ok:
            return True, "Started a Claude agent in {} with your task.".format(label)
        return False, "Opened a Claude tab in {} with your task filled in. Press Enter there.".format(label)
