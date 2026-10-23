"""Codex (CLI, VS Code extension, ChatGPT desktop app): observed through its rollout logs in ~/.codex/sessions.

Codex logs never show pending approvals, so this plug-in never claims "needs you": a quiet "working" thread becomes
"unknown". Messages go, in order: to the app that has the thread open (local router), else headlessly with
`codex exec resume`, else (macOS, ChatGPT app holding the thread) through the app with a guarded Enter key press.
Never reads ~/.codex/auth.json.
"""
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from .. import system
from ..settings import DATA_DIR
from ..util import HOME, FileCache, clip, iso_to_ms, keep, plain, repo_name, same_path, tail_lines
from .base import Agent, command_line, executable, first_cwd

CODEX_SESSIONS = HOME / ".codex" / "sessions"
CODEX_IPC = HOME / ".codex" / "ipc" / "ipc.sock"
CODEX_WINDOW_SECONDS = 6 * 3600   # show Codex threads touched in the last 6 hours
CODEX_STALE_SECONDS = 10 * 60     # a "working" Codex thread silent this long becomes "unknown"
CODEX_CLIENTS = {"codex_vscode": "vscode", "codex_exec": "vscode", "Codex Desktop": "desktop", "codex_work_desktop": "desktop",
                 "codex-chrome-extension-sidepanel": "chrome", "codex_cli_rs": "terminal", "codex-tui": "terminal"}
APP_TITLES = {"desktop": "ChatGPT app", "chrome": "ChatGPT in Chrome"}
MAC_APP_CLI = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
THREAD_ID = re.compile(r"[0-9a-fA-F-]{8,64}")
WINDOWS = sys.platform.startswith("win")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def codex_user_text(item):
    texts = [c.get("text", "") for c in item.get("content") or [] if isinstance(c, dict)]
    texts = [t for t in texts if t.strip() and not t.lstrip().startswith("<")]  # skip internal tags and context blocks
    text = "\n".join(texts)
    marker = "## My request:"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip() or None


def parse_codex_rollout(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        try:
            meta = json.loads(fh.readline()).get("payload") or {}
        except ValueError:
            return None
    info = {"meta": meta, "event": None, "event_at": None, "prompt": None, "reply": None}
    for line in reversed(tail_lines(path)):
        if '"event_msg"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        payload = rec.get("payload") or {}
        kind = payload.get("type")
        if kind in ("task_started", "task_complete", "turn_aborted") and info["event"] is None:
            info["event"], info["event_at"] = kind, iso_to_ms(rec.get("timestamp"))
            if kind == "task_complete":
                info["reply"] = payload.get("last_agent_message")
        elif kind == "user_message" and info["prompt"] is None:
            # the Codex VS Code extension logs plain user_message/agent_message events instead of item_completed
            info["prompt"] = codex_user_text({"content": [{"text": payload.get("message") or ""}]})
        elif kind == "agent_message" and info["reply"] is None and info["event"] != "task_started":
            info["reply"] = (payload.get("message") or "").strip() or None
        elif kind == "item_completed":
            item = payload.get("item") or {}
            if item.get("type") == "UserMessage" and info["prompt"] is None:
                info["prompt"] = codex_user_text(item)
            elif item.get("type") == "AgentMessage" and info["reply"] is None and info["event"] != "task_started":
                texts = [c.get("text", "") for c in item.get("content") or [] if isinstance(c, dict)]
                info["reply"] = "\n".join(texts).strip() or None
        if info["event"] and info["prompt"] and (info["reply"] or info["event"] == "task_started"):
            break
    return info


CODEX_ROLLOUTS = FileCache(parse_codex_rollout)


def rollouts():
    return glob.glob(str(CODEX_SESSIONS / "*" / "*" / "*" / "*.jsonl"))


def codex_binary():
    """The newest Codex the clients use: the ChatGPT app's own (macOS), else the CLI on PATH, else VS Code's copy."""
    if executable(MAC_APP_CLI):
        return MAC_APP_CLI
    found = shutil.which("codex")
    if found:
        return found
    exe = "codex.exe" if WINDOWS else "codex"
    bundled = glob.glob(str(HOME / ".vscode" / "extensions" / "openai.chatgpt-*" / "bin" / "*" / exe))
    bundled = [b for b in sorted(bundled, reverse=True) if executable(b)]
    return bundled[0] if bundled else None


def last_codex_prompt(path):
    try:
        return (parse_codex_rollout(Path(path)) or {}).get("prompt") or ""
    except OSError:
        return ""


def wait_for_codex_prompt(rollout, text, seconds=8):
    want = re.sub(r"\s+", " ", text)[:60]
    for _ in range(int(seconds * 2)):
        time.sleep(0.5)
        if want in re.sub(r"\s+", " ", last_codex_prompt(rollout or "")):
            return True
    return False


# ---- the Codex apps' local router (background delivery)
#
# The Codex apps (ChatGPT desktop app, VS Code extension) share a local router at ~/.codex/ipc/ipc.sock: frames are a
# 4-byte little-endian length plus JSON. A client registers with `initialize`, finds the app that has a thread loaded
# with `thread-owner-discovery`, and asks that app to run a turn with `thread-follower-start-turn` (or to steer a running
# one with `thread-follower-steer-turn`). Undocumented; read from the apps' own code. Unix socket only, so not on Windows.

IPC_VERSIONS = {"initialize": 0, "thread-owner-discovery": 1, "thread-follower-start-turn": 2, "thread-follower-steer-turn": 1}


class CodexIPC:
    def __init__(self, timeout=12):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(CODEX_IPC))
        self.me = None
        self.me = self.request("initialize", {"clientType": "agent-control-room"})["result"]["clientId"]

    def _send(self, msg):
        import struct
        data = json.dumps(msg).encode("utf-8")
        self.sock.sendall(struct.pack("<I", len(data)) + data)

    def _recv(self):
        import struct

        def exact(n):
            buf = b""
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise EOFError("Codex router closed the connection")
                buf += chunk
            return buf
        return json.loads(exact(struct.unpack("<I", exact(4))[0]))

    def request(self, method, params, target=None, timeout_ms=None):
        import uuid
        rid = str(uuid.uuid4())
        msg = {"type": "request", "requestId": rid, "version": IPC_VERSIONS.get(method, 1), "method": method, "params": params}
        if timeout_ms:
            msg["timeoutMs"] = timeout_ms  # the router waits this long for an app to claim the request (default 10 s)
        if self.me:
            msg["sourceClientId"] = self.me
        if target:
            msg["targetClientId"] = target
        self._send(msg)
        while True:
            reply = self._recv()
            if reply.get("type") == "response" and reply.get("requestId") == rid:
                return reply

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def ipc_send(thread, text, steer):
    """Deliver a message to the app that has this Codex thread loaded. Returns (delivered, reason)."""
    if WINDOWS or not hasattr(socket, "AF_UNIX") or not CODEX_IPC.exists():
        return False, "no Codex router"
    ipc = None
    try:
        ipc = CodexIPC()
        ipc.sock.settimeout(3)  # an app that has the thread answers at once; the router alone waits 10 s for nobody
        try:
            found = ipc.request("thread-owner-discovery", {"hostId": "local", "conversationId": thread}, timeout_ms=2500)
        except socket.timeout:
            return False, "no app has the thread loaded"
        ipc.sock.settimeout(15)
        owner = found.get("handledByClientId") if found.get("resultType") == "success" and found.get("result") is not False else None
        if not owner:
            return False, "no app has the thread loaded"
        user_input = [{"type": "text", "text": text, "text_elements": []}]
        if steer:
            reply = ipc.request("thread-follower-steer-turn", {"conversationId": thread, "input": user_input}, target=owner)
        else:
            reply = ipc.request("thread-follower-start-turn", {"conversationId": thread, "turnStart": {
                "request": {"threadId": thread, "input": user_input}, "context": {}}}, target=owner)
        if reply.get("resultType") != "success":
            return False, str(reply.get("error") or "the app refused")[:160]
        return True, ""
    except Exception as exc:  # any surprise: fall back to the next path
        return False, clip(exc, 160)
    finally:
        if ipc:
            ipc.close()


def _codex_log():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / "codex-exec.log"


def headless_resume(thread, cwd, text):
    """Resume a thread no app has loaded, without any window: `codex exec resume` in the thread's folder, allowed to
    write there. The app shows the new turn the next time the thread is opened."""
    binary = codex_binary()
    if not binary:
        return False, "no codex program found"
    folder = cwd if cwd and os.path.isdir(cwd) else str(HOME)
    log_path = _codex_log()
    log = open(log_path, "a", encoding="utf-8")
    log.write("\n--- {} resume {}\n".format(time.strftime("%Y-%m-%d %H:%M:%S"), thread))
    log.flush()
    start = log.tell()
    proc = subprocess.Popen([binary, "exec", "--skip-git-repo-check", "-s", "workspace-write", "-C", folder,
                             "resume", thread, text], stdout=log, stderr=log, stdin=subprocess.DEVNULL, cwd=folder,
                            start_new_session=not WINDOWS, creationflags=NO_WINDOW)
    for _ in range(12):  # a refused resume exits within a couple of seconds
        time.sleep(0.5)
        if proc.poll() is not None:
            break
    if proc.poll() is not None and proc.returncode != 0:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            fh.seek(start)
            output = fh.read()
        if "active writer" in output:
            return False, "an app has the thread open"
        return False, clip(output.strip().splitlines()[-1] if output.strip() else "codex exited with {}".format(proc.returncode), 160)
    return True, ""


def start_headless(folder, prompt):
    """Start a new Codex thread in folder with prompt as its first message, without any window or key press.
    Returns the thread id once Codex reports it (the turn keeps running in the background), or None."""
    import queue
    binary = codex_binary()
    if not binary:
        return None
    log = open(_codex_log(), "a", encoding="utf-8")
    log.write("\n--- {} new thread\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
    log.flush()
    proc = subprocess.Popen([binary, "exec", "--json", "--skip-git-repo-check", "-s", "workspace-write", "-C", folder, prompt],
                            stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            cwd=folder, start_new_session=not WINDOWS, creationflags=NO_WINDOW)
    found = queue.Queue()

    def drain():  # read Codex's event stream: report the thread id, keep the rest in the log
        for line in proc.stdout:
            log.write(line)
            if '"thread.started"' in line:
                try:
                    found.put(json.loads(line).get("thread_id"))
                except ValueError:
                    pass
        log.flush()
        found.put(None)
    threading.Thread(target=drain, daemon=True).start()
    try:
        return found.get(timeout=30)
    except Exception:
        return None


def _started_ms(path, meta):
    stamp = iso_to_ms(meta.get("timestamp"))
    if stamp:
        return stamp
    st = path.stat()
    return int(getattr(st, "st_birthtime", st.st_ctime if WINDOWS else st.st_mtime) * 1000)


class Codex(Agent):
    vendor = "codex"
    label = "Codex"
    launch_targets = ("window", "new-window", "terminal") + (("app",) if system.NAME == "macos" else ())

    def __init__(self):
        self.launched = {}  # thread id -> client, for threads the room started (they report originator codex_exec)

    def sessions(self):
        cutoff = time.time() - CODEX_WINDOW_SECONDS
        can_resume = bool(codex_binary())
        out = []
        for name in rollouts():
            path = Path(name)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue
            info = CODEX_ROLLOUTS.get(path)
            if not info or info["meta"].get("thread_source") not in (None, "user"):
                continue
            meta = info["meta"]
            if info["event"] == "task_started":
                state = "unknown" if time.time() - mtime > CODEX_STALE_SECONDS else "working"
            elif info["event"] in ("task_complete", "turn_aborted"):
                state = "finished"
            else:
                state = "unknown"
            if state == "working":
                line = clip(info["prompt"], 200) or "Working"
            elif state == "finished":
                line = clip(plain(info["reply"]), 200) or ("Interrupted" if info["event"] == "turn_aborted" else "Finished")
            else:
                line = "No recent signal. It may be working, done, or waiting on an approval."
            cwd = meta.get("cwd") or ""
            client = self.launched.get(meta.get("id")) or CODEX_CLIENTS.get(meta.get("originator"), meta.get("originator") or "unknown")
            repo = APP_TITLES.get(client) or repo_name(cwd)
            first_line = (info["prompt"] or "").strip().split("\n")[0]
            thread = meta.get("id")
            out.append({
                "repo": repo,
                "subtitle": clip(plain(first_line), 70),
                "key": "codex:" + str(thread or path.stem),
                "vendor": self.vendor,
                "client": client,
                "name": (os.path.basename(cwd) if cwd and not same_path(cwd, HOME) else clip(info["prompt"], 60)) or "Codex thread",
                "cwd": cwd,
                "state": state,
                "since": info["event_at"] or int(mtime * 1000),
                "startedAt": _started_ms(path, meta),
                "line": line,
                "full": keep(info["reply"] if state == "finished" else info["prompt"]),
                "task": clip(info["prompt"], 600),
                "sessionId": thread,
                "pid": None,
                "canReply": (client in ("desktop", "vscode") and state in ("finished", "working", "unknown")
                             and bool(thread) and (can_resume or CODEX_IPC.exists())),
                "rollout": str(path),
                "source": "Codex rollout log (observer-only): last event {}".format(info["event"] or "none"),
            })
        return out

    def jump(self, session):
        from . import vscode
        client, thread = session["client"], session.get("sessionId") or ""
        if client == "vscode":
            folder = vscode.folder_for(session["cwd"])
            vscode.open_folder(folder, settle=0.6)  # the link lands in the frontmost window
            if THREAD_ID.fullmatch(thread):
                system.open_url("vscode://openai.chatgpt/local/" + thread)
                return True, "Opened the Codex thread in {}".format(os.path.basename(folder))
            return True, "Raised the VS Code window for {}".format(os.path.basename(folder))
        if client == "desktop":
            if THREAD_ID.fullmatch(thread) and system.open_url("codex://threads/" + thread):
                return True, "Opened the thread in the ChatGPT app"
            return False, "Could not open the ChatGPT app. Pick the thread there."
        return False, "No way to open a Codex {} thread yet.".format(client)

    def reply(self, session, text):
        """Send the user's own message (no room header) and confirm it reached the thread's log."""
        text = (text or "").strip()
        thread = session.get("sessionId") or ""
        if not text:
            return False, "Type a message first."
        if not session.get("canReply"):
            return False, "This Codex thread cannot take a message from here. Open its window."
        if not THREAD_ID.fullmatch(thread):
            return False, "This thread cannot be opened from here."
        label = session.get("subtitle") or session["name"]
        delivered, _ = ipc_send(thread, text[:20000], steer=session["state"] == "working")
        if delivered:
            if wait_for_codex_prompt(session.get("rollout"), text):
                return True, "Sent to {} in the background.".format(label)
            return True, "Handed to the Codex app in the background; the thread has not shown it yet."
        started, why = headless_resume(thread, session.get("cwd"), text[:20000])
        if started:
            if wait_for_codex_prompt(session.get("rollout"), text, seconds=15):
                return True, "Sent to {} in the background. Open the thread to follow it live.".format(label)
            return False, ("Codex started in the background but your message has not shown up in the thread. "
                           "Check the thread before sending again.")
        if session.get("client") != "desktop" or system.NAME != "macos":
            return False, "Could not send to this Codex thread ({}). Reply in its window.".format(why)
        return self._through_mac_app(session, thread, text, label)

    def _through_mac_app(self, session, thread, text, label):
        """The ChatGPT app holds the thread (writer lock) and has no background API: open the thread with the message
        filled in, press Enter only while the app is in front, then hand focus back."""
        link = "codex://threads/{}?{}".format(thread, urllib.parse.urlencode({"prompt": text[:5000]}, quote_via=urllib.parse.quote))
        previous = system.frontmost_app_id()
        system.open_url(link)
        ok, why = False, ""
        for _ in range(12):  # press Enter as soon as the app is in front, at most ~1.2 s
            time.sleep(0.1)
            if system.frontmost_app_id() == "com.openai.codex":
                time.sleep(0.35)  # let the thread render the text in its box
                ok, why = system.press_enter_in("ChatGPT", "The ChatGPT app")
                break
        else:
            ok, why = system.press_enter_in("ChatGPT", "The ChatGPT app")
        if previous and previous != "com.openai.codex":
            time.sleep(0.15)
            system.focus_app(previous)
        if not ok:
            return False, "Opened the thread with your message filled in, but could not press Enter: {}. Press Enter there.".format(why)
        if wait_for_codex_prompt(session.get("rollout"), text, seconds=8):
            return True, "Sent to {} through the ChatGPT app (it had the thread open).".format(label)
        return False, "Pressed Enter in the ChatGPT app, but the message has not shown up in the thread. Check the app."

    def recent_folders(self):
        used, cutoff = {}, time.time() - 30 * 86400
        for name in rollouts():
            try:
                mtime = os.path.getmtime(name)
            except OSError:
                continue
            if mtime > cutoff:
                cwd = first_cwd(name)
                if cwd:
                    used[cwd] = max(used.get(cwd, 0), mtime)
        return used

    def launch(self, folder, prompt, target):
        from . import vscode
        label = os.path.basename(folder)
        if target == "terminal":
            ok, msg = system.run_in_terminal(folder, command_line([codex_binary() or "codex"] + ([prompt] if prompt else [])))
            return (True, "Started a Codex agent in {} in a terminal.".format(label)) if ok else (False, msg)
        if target == "app":
            query = {"path": folder}
            if prompt:
                query["prompt"] = prompt
            system.open_url("codex://threads/new?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote))
            if not prompt:
                return True, "Opened a new Codex thread for {} in the ChatGPT app.".format(label)
            time.sleep(1.5)
            ok, why = system.press_enter_in("ChatGPT", "The ChatGPT app")
            if ok:
                return True, "Started a Codex agent in {} in the ChatGPT app with your task.".format(label)
            return False, "Opened the ChatGPT app with your task filled in. Press Enter there."
        if target not in ("window", "new-window"):
            return False, "Choose where to open the agent."
        # A window of its own, so no existing chat in that window can take the task.
        vscode.open_folder(folder, new_window=True, settle=1.5)
        if not prompt:
            system.open_url("vscode://openai.chatgpt/")
            return True, "Opened Codex in {}.".format(label)
        # The Codex extension takes no task from outside, so Codex itself starts the thread headlessly in this folder;
        # the room tracks it and "Go to window" opens it in VS Code.
        thread = start_headless(folder, prompt)
        if thread:
            self.launched[thread] = "vscode"
            return True, ("Started a Codex agent in {} with your task. It works in the background: when the room shows "
                          "it done, Go to window opens the thread in VS Code.").format(label)
        return False, "Could not start Codex in {}. See codex-exec.log in the room's data folder.".format(label)
