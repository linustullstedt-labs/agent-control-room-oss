"""Agent Control Room: the local server behind the page (overview, jump to window, replies, approvals, Listen).

Sessions come from the agent plug-ins in agents/ (Claude Code and Codex today). Everything that differs between
macOS, Windows and Linux lives in system/. The server binds 127.0.0.1 only and never reads token fields.
"""
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, system
from .agents import AGENTS, BY_VENDOR
from .agents.claude_code import tool_summary
from .agents.vscode import open_windows
from .settings import APPROVALS_FLAG, DATA_DIR, ignored_folders, is_ignored, room_settings, save_room_settings
from .speech import SPEAKER, reply_speech
from .util import FOLDER_NAME, HOME, clip, log, now_ms, repo_name, same_path, within

WEB = Path(__file__).resolve().parent / "web"
PAGE = WEB / "index.html"
DEFAULT_PORT = 7788
POLL_SECONDS = 2.0
PLACES_TTL_SECONDS = 30
APPROVAL_TTL_SECONDS = 700  # a little longer than the hook waits
REPO_URL = "https://github.com/alicelabs-ai/agent-control-room-oss"


# ---------------------------------------------------------------- state store

class Store:
    """State transitions, for later wait-time metrics. Holds keys, states and times only, never text."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS transitions (
            key TEXT NOT NULL, vendor TEXT, client TEXT, state TEXT NOT NULL, at INTEGER NOT NULL,
            UNIQUE(key, state, at))""")
        self.db.commit()
        self.last = {}
        for key, state in self.db.execute(
                "SELECT key, state FROM transitions t WHERE at = (SELECT MAX(at) FROM transitions WHERE key = t.key)"):
            self.last[key] = state

    def record(self, sessions):
        changed = False
        for s in sessions:
            if self.last.get(s["key"]) != s["state"]:
                self.db.execute("INSERT OR IGNORE INTO transitions VALUES (?,?,?,?,?)",
                                (s["key"], s["vendor"], s["client"], s["state"], int(s["since"])))
                self.last[s["key"]] = s["state"]
                changed = True
        if changed:
            self.db.commit()


# ---------------------------------------------------------------- poller

NOTIFY_STATES = {"needs-permission": "needs permission", "needs-answer": "has a question", "dead": "stopped unexpectedly",
                 "finished": "is done"}
MIN_WORK_FOR_DONE_MS = 30_000  # skip "is done" pings for turns shorter than this
NOTIFY_COOLDOWN_SECONDS = 60   # at most one ping per session per minute


class Poller(threading.Thread):
    def __init__(self, store):
        super().__init__(daemon=True)
        self.store = store
        self.lock = threading.Lock()
        self.snapshot = {"generatedAt": 0, "sessions": [], "errors": []}
        self._prev = None
        self._sent = {}

    def notify_changes(self, sessions):
        # Compare like with like: the rows as the page shows them, waiting approvals included.
        rows = with_approvals({"sessions": sessions})["sessions"]
        prev, self._prev = self._prev, {s["key"]: (s["state"], s["since"]) for s in rows}
        if prev is None or not room_settings().get("notifications", True):
            return  # first poll after start: no pings for states that were already there
        for s in rows:
            before = prev.get(s["key"])
            if before is None or before[0] == s["state"] or s["state"] not in NOTIFY_STATES:
                continue
            if time.time() - self._sent.get(s["key"], 0) < NOTIFY_COOLDOWN_SECONDS:
                continue  # safety net against a flapping session
            if s["state"] == "finished" and (before[0] != "working" or s["since"] - before[1] < MIN_WORK_FOR_DONE_MS):
                continue
            detail = s["line"].replace("Wants to run: ", "").replace("Asks: ", "")
            self._sent[s["key"]] = time.time()
            title = "{} {}".format(s.get("repo") or s["name"], NOTIFY_STATES[s["state"]])
            if s.get("subtitle"):
                detail = s["subtitle"] + ": " + detail
            threading.Thread(target=safe_notify, args=(clip(title, 60), clip(detail, 180)), daemon=True).start()

    def poll_once(self):
        sessions, errors = [], []
        for agent in AGENTS:
            try:
                sessions.extend(agent.sessions())
            except Exception as exc:  # one broken plug-in must not blank the page
                errors.append("{}: {}".format(agent.label, clip(exc, 200)))
        ignored = ignored_folders()  # private folders: never stored, notified, shown or read aloud
        sessions = [s for s in sessions if not is_ignored(s.get("cwd"), s.get("repo"), ignored)]
        if self.store:
            self.store.record(sessions)
            self.notify_changes(sessions)
        sessions = sessions + launch_placeholders(sessions)
        with self.lock:
            self.snapshot = {"generatedAt": now_ms(), "sessions": sessions, "errors": errors}

    def run(self):
        while True:
            try:
                self.poll_once()
            except Exception as exc:  # keep polling; the page shows the last good snapshot
                log("poll failed: {}".format(clip(exc, 200)))
            time.sleep(POLL_SECONDS)

    def find(self, key):
        with self.lock:
            return next((s for s in self.snapshot["sessions"] if s["key"] == key), None)


def safe_notify(title, message):
    try:
        system.notify(title, message)
    except Exception as exc:
        log("notification failed: {}".format(clip(exc, 160)))


# ---------------------------------------------------------------- start a new agent
#
# The room opens a window and hands over the first task; the agent itself runs in VS Code or a terminal as usual.

def usable_folder(path):
    """A real project folder under the home folder: not the home folder itself, app data or hidden folders."""
    if not path or same_path(path, HOME) or not within(path, HOME) or not os.path.isdir(path):
        return False
    parts = Path(path).relative_to(HOME).parts if within(path, HOME) else ()
    return not any(p.startswith(".") for p in parts) and not ({"Library", "AppData"} & set(parts[:1])) \
        and "Documents/Codex" not in "/".join(parts)


_places = {"at": 0, "value": None}
LAUNCHES = []                  # agents started from the room, shown as placeholder rows until their session appears
LAUNCH_TTL_SECONDS = 15 * 60


def remember_launch(agent, folder, target):
    LAUNCHES.append({"agent": agent, "folder": folder, "target": target, "at": now_ms()})
    del LAUNCHES[:-20]


def launch_placeholders(sessions):
    """A row for each recent launch that has no session of its own yet, so it is never mistaken for another agent."""
    rows, kept = [], []
    for item in LAUNCHES:
        if now_ms() - item["at"] > LAUNCH_TTL_SECONDS:
            continue
        arrived = any(s["vendor"] == item["agent"] and same_path(s["cwd"], item["folder"])
                      and s.get("startedAt", s["since"]) >= item["at"] - 5000 for s in sessions)
        if arrived:
            continue
        kept.append(item)
        who = BY_VENDOR[item["agent"]].label
        rows.append({
            "key": "launch:{}:{}".format(item["agent"], item["at"]),
            "vendor": item["agent"], "client": "terminal" if item["target"] == "terminal" else "vscode",
            "name": "New {} agent in {}".format(who, os.path.basename(item["folder"])),
            "repo": repo_name(item["folder"]), "subtitle": "New {} agent".format(who),
            "cwd": item["folder"], "state": "unknown", "since": item["at"], "startedAt": item["at"],
            "line": "Starting: waiting for your first message in {}".format(who),
            "full": "This agent has no session yet. Once you send its first message in {}, this row turns into the real one.".format(who),
            "task": "", "sessionId": None, "pid": None, "canReply": False, "placeholder": True,
            "source": "Started from the room",
        })
    LAUNCHES[:] = kept
    return rows


def places():
    """Folders to offer in the launcher: open VS Code windows first, then recently used ones, then their siblings."""
    if _places["value"] and time.time() - _places["at"] < PLACES_TTL_SECONDS:
        return _places["value"]
    used = {}
    for agent in AGENTS:
        try:
            for path, mtime in agent.recent_folders().items():
                if usable_folder(path):
                    used[path] = max(used.get(path, 0), mtime)
        except Exception as exc:
            log("{} recent folders: {}".format(agent.label, clip(exc, 160)))
    windows = open_windows()
    parents = sorted({os.path.dirname(p) for p in used if not same_path(os.path.dirname(p), HOME)},
                     key=lambda d: -max(t for p, t in used.items() if os.path.dirname(p) == d))
    siblings = set()
    for parent in [d for d in parents if sum(1 for p in used if os.path.dirname(p) == d) >= 2]:
        try:
            for entry in os.scandir(parent):
                if entry.is_dir() and not entry.name.startswith(".") and entry.path not in used:
                    siblings.add(entry.path)
        except OSError:
            continue
    items = [{"path": p, "open": True, "used": used.get(p, 0)} for p in windows]
    items += [{"path": p, "open": False, "used": used[p]} for p in sorted(used, key=lambda p: -used[p]) if p not in windows]
    items += [{"path": p, "open": False, "used": 0} for p in sorted(siblings, key=str.lower) if p not in windows]

    def tilde(p):
        return "~" + p[len(str(HOME)):] if within(p, HOME) else p
    for item in items:
        item["name"] = os.path.basename(item["path"])
        item["where"] = tilde(os.path.dirname(item["path"]))
    value = {"places": items, "parents": [{"path": d, "label": tilde(d)} for d in parents],
             "agents": [{"vendor": a.vendor, "label": a.label, "targets": list(a.launch_targets)} for a in AGENTS]}
    _places.update(at=time.time(), value=value)
    return value


def launch(body):
    import subprocess
    prompt = str(body.get("prompt") or "").strip()[:5000]
    target = body.get("target")
    agent = BY_VENDOR.get(body.get("agent")) or AGENTS[0]
    if target not in agent.launch_targets:
        return False, "{} cannot be started that way on this computer.".format(agent.label)
    known = places()
    if body.get("newName"):
        name, parent = str(body["newName"]).strip(), body.get("parent")
        if not FOLDER_NAME.match(name):
            return False, "Use letters, digits, dots, dashes or underscores for the folder name."
        if parent not in [p["path"] for p in known["parents"]]:
            return False, "Pick one of the listed parent folders."
        folder = os.path.join(parent, name)
        if os.path.exists(folder):
            return False, "{} already exists. Pick it from the list instead.".format(name)
        os.mkdir(folder)
        try:
            subprocess.run(["git", "init", "-q", folder], check=False, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass  # no git: the folder still works, just without a repository
        _places["value"] = None
    else:
        folder = body.get("folder")
        if folder not in [p["path"] for p in known["places"]]:
            return False, "Pick a folder from the list."
    remember_launch(agent.vendor, folder, target)
    return agent.launch(folder, prompt, target)


# ---------------------------------------------------------------- approvals (PermissionRequest hook)

class Approvals:
    """Pending permission requests handed over by the hook (hooks/permission.py), answered from the page."""

    def __init__(self):
        self.cond = threading.Condition()
        self.pending = {}

    def add(self, event):
        import uuid
        req = {
            "id": uuid.uuid4().hex,
            "sessionId": event.get("session_id"),
            "cwd": event.get("cwd") or "",
            "tool": tool_summary(event.get("tool_name"), event.get("tool_input")),
            "toolName": event.get("tool_name"),
            "created": now_ms(),
            "behavior": None,
        }
        with self.cond:
            self.pending[req["id"]] = req
            self.cond.notify_all()
        return req

    def wait(self, request_id, timeout=30):
        with self.cond:
            end = time.time() + timeout
            while True:
                req = self.pending.get(request_id)
                if req is None:
                    return "window"
                if req["behavior"]:
                    self.pending.pop(request_id, None)
                    return req["behavior"]
                left = end - time.time()
                if left <= 0:
                    return None
                self.cond.wait(left)

    def decide(self, request_id, behavior):
        with self.cond:
            req = self.pending.get(request_id)
            if not req or req["behavior"]:
                return None
            req["behavior"] = behavior
            self.cond.notify_all()
            return req

    def drop(self, request_id):
        with self.cond:
            self.pending.pop(request_id, None)

    def open_requests(self):
        cutoff = now_ms() - APPROVAL_TTL_SECONDS * 1000
        with self.cond:
            for rid in [r for r, v in self.pending.items() if v["created"] < cutoff]:
                self.pending.pop(rid, None)
            return [dict(v) for v in self.pending.values() if not v["behavior"]]


APPROVALS = Approvals()


def approvals_on():
    return APPROVALS_FLAG.exists()


def set_approvals(on):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if on:
        APPROVALS_FLAG.write_text("on\n")
    elif APPROVALS_FLAG.exists():
        APPROVALS_FLAG.unlink()


def hook_installed():
    from .hooks import install
    return install.is_installed()


def with_approvals(snapshot):
    """Merge waiting approvals into the session rows at serve time, so they show up within one page refresh."""
    requests = APPROVALS.open_requests()
    if not requests:
        return snapshot
    sessions = [dict(s) for s in snapshot["sessions"]]
    for req in requests:
        row = next((s for s in sessions if s.get("sessionId") == req["sessionId"]), None)
        if row is None:
            continue
        row.update(state="needs-permission", since=req["created"], line="Wants to run: " + req["tool"],
                   full=req["tool"], approvalId=req["id"], canReply=False,
                   source="PermissionRequest hook, waiting for Allow or Deny here")
    return dict(snapshot, sessions=sessions)


# ---------------------------------------------------------------- close (hide) a row

def close_session(session):
    """Hide the row until the session changes state. The agent itself keeps running; nothing is ended."""
    closed = room_settings().get("closed", {})
    closed[session["key"]] = int(session["since"])
    save_room_settings({"closed": closed})
    return True, "Closed {}. It comes back when the agent does something new.".format(session["name"])


def without_closed(snapshot):
    closed = room_settings().get("closed", {})
    if not closed:
        return snapshot
    live = {s["key"]: int(s["since"]) for s in snapshot["sessions"]}
    still = {k: v for k, v in closed.items() if live.get(k) == v}
    if still != closed:
        save_room_settings({"closed": still})  # forget rows that moved on or are gone
    return dict(snapshot, sessions=[s for s in snapshot["sessions"] if s["key"] not in still])


# ---------------------------------------------------------------- updates (only when the user clicks)

UPDATE = {"checked": 0, "latest": None, "newer": False, "url": REPO_URL + "/releases", "error": None}


def version_tuple(text):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", str(text or ""))[:3]) or (0,)


def check_updates():
    """Ask GitHub for the latest release. The one call besides OpenAI's voice, and only when asked."""
    import urllib.request
    UPDATE.update(checked=now_ms(), error=None)
    api = REPO_URL.replace("https://github.com/", "https://api.github.com/repos/") + "/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"Accept": "application/vnd.github+json", "User-Agent": "agent-control-room"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.load(resp)
    except Exception as exc:
        UPDATE.update(error="Could not reach GitHub: {}".format(clip(exc, 120)))
        return UPDATE
    tag = release.get("tag_name") or ""
    UPDATE.update(latest=tag, url=release.get("html_url") or UPDATE["url"],
                  newer=version_tuple(tag) > version_tuple(__version__))
    return UPDATE


# ---------------------------------------------------------------- HTTP

POST_ROUTES = {"/api/jump", "/api/reply", "/api/launch", "/api/permission", "/api/permission/expire", "/api/decide",
               "/api/settings", "/api/stop", "/api/close", "/api/update/check", "/api/hook/install", "/api/hook/remove",
               "/api/speak", "/api/quit"}


def voice_setting():
    return "openai" if room_settings().get("voice") == "openai" else "system"


def snapshot_for_page(poller):
    with poller.lock:
        snap = poller.snapshot
    snap = without_closed(with_approvals(snap))
    return dict(snap, pageVersion=page_version(), version=__version__, platform=system.NAME, update=UPDATE,
                repoUrl=REPO_URL, approvals={"on": approvals_on(), "hookInstalled": hook_installed()},
                notifications=room_settings().get("notifications", True), speaking=SPEAKER.speaking,
                voice=voice_setting(), speechError=SPEAKER.error)


def make_handler(poller, port, on_quit):
    allowed_hosts = {"127.0.0.1:{}".format(port), "localhost:{}".format(port)}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def guard(self):
            # Blocks DNS-rebinding (Host check) and cross-site form posts (custom header forces a CORS preflight).
            if self.headers.get("Host") not in allowed_hosts:
                self.send(403, {"error": "forbidden host"})
                return False
            return True

        def do_GET(self):
            if not self.guard():
                return
            if self.path in ("/", "/index.html"):
                self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/icon.svg":
                self.send(200, (WEB / "icon.svg").read_bytes(), "image/svg+xml")
            elif self.path == "/api/places":
                self.send(200, places())
            elif self.path == "/api/sessions":
                self.send(200, snapshot_for_page(poller))
            elif self.path == "/api/version":
                self.send(200, {"version": __version__, "platform": system.NAME, "update": UPDATE})
            elif self.path.startswith("/api/permission/wait"):
                rid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id", [""])[0]
                self.send(200, {"behavior": APPROVALS.wait(rid)})
            else:
                self.send(404, {"error": "not found"})

        def body(self):
            try:
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}")
            except ValueError:
                return {}

        def do_POST(self):
            if not self.guard():
                return
            if self.path == "/api/client-log":
                log("[page] " + clip(json.dumps(self.body()), 600))
                self.send(200, {"ok": True})
                return
            if self.headers.get("X-Control-Room") != "1":
                self.send(403, {"error": "missing header"})
                return
            if self.path not in POST_ROUTES:
                self.send(404, {"error": "not found"})
                return
            body = self.body()
            handled = self.settings_routes(body)
            if handled is not None:
                self.send(200, handled)
                return
            self.session_routes(body)

        def settings_routes(self, body):
            """Routes that do not act on one session. Returns the answer, or None for session routes."""
            from .hooks import install
            path = self.path
            if path == "/api/quit":
                threading.Thread(target=on_quit, daemon=True).start()
                return {"ok": True}
            if path == "/api/hook/install":
                ok, message = install.apply(uninstall=False)
                if ok and body.get("enable"):
                    set_approvals(True)
                log("/api/hook/install -> {} {}".format("ok" if ok else "FAILED", clip(message, 160)))
                return {"ok": ok, "message": message}
            if path == "/api/hook/remove":
                ok, message = install.apply(uninstall=True)
                if ok:
                    set_approvals(False)
                log("/api/hook/remove -> {}".format("ok" if ok else "FAILED"))
                return {"ok": ok, "message": message}
            if path == "/api/speak":
                return self.speak(body)
            if path == "/api/update/check":
                return dict(check_updates())
            if path == "/api/permission":
                return {"id": APPROVALS.add(body)["id"]}
            if path == "/api/permission/expire":
                APPROVALS.drop(str(body.get("id")))
                return {"ok": True}
            if path == "/api/decide":
                return self.decide(body)
            if path == "/api/settings":
                if "approvals" in body:
                    set_approvals(bool(body["approvals"]))
                if "notifications" in body:
                    save_room_settings({"notifications": bool(body["notifications"])})
                if body.get("voice") in ("system", "openai"):
                    save_room_settings({"voice": body["voice"]})
                return {"ok": True, "approvals": approvals_on(), "notifications": room_settings().get("notifications", True),
                        "voice": voice_setting()}
            if path == "/api/launch":
                started = time.time()
                try:
                    ok, message = launch(body)
                except Exception as exc:
                    ok, message = False, "Failed: {}".format(clip(exc, 200))
                log("/api/launch {} {} -> {} ({:.1f}s) {}".format(body.get("agent"), body.get("target"), "ok" if ok else "FAILED",
                                                                time.time() - started, clip(message, 160)))
                return {"ok": ok, "message": message}
            return None

        def speak(self, body):
            key = str(body.get("key") or "")
            session = poller.find(key) if key else None
            if body.get("stop") or not session or SPEAKER.speaking == key:
                SPEAKER.stop()
                return {"ok": bool(session) or bool(body.get("stop")) or not key, "speaking": None,
                        "message": "" if session or not key else "That row is gone."}
            lines = reply_speech(session)
            if not lines:
                return {"ok": False, "speaking": None, "message": "There is nothing to read for this row yet."}
            SPEAKER.speak(lines, key, engine=voice_setting())
            log("/api/speak {} -> {} parts ({})".format(key[:28], len(lines), ",".join(lang for lang, _ in lines)))
            return {"ok": True, "speaking": key}

        def decide(self, body):
            behavior = body.get("behavior")
            if behavior not in ("allow", "deny", "window"):
                return {"ok": False, "message": "Unknown decision."}
            req = APPROVALS.decide(str(body.get("id")), behavior)
            if not req:
                return {"ok": False, "message": "That request is no longer waiting."}
            if behavior == "window":
                s = poller.find("claude:" + str(req["sessionId"]))
                if s:
                    BY_VENDOR["claude"].jump(s)
                return {"ok": True, "message": "The prompt is back in its window."}
            return {"ok": True, "message": "{}: {}".format("Allowed" if behavior == "allow" else "Denied", clip(req["tool"], 80))}

        def session_routes(self, body):
            session = poller.find(body.get("key"))
            if not session:
                self.send(404, {"ok": False, "message": "That session is gone. Refresh the list."})
                return
            agent = BY_VENDOR.get(session["vendor"])
            started = time.time()
            try:
                if self.path == "/api/close":
                    ok, message = close_session(session)
                elif agent is None or session.get("placeholder"):
                    ok, message = False, "This agent has not started yet."
                elif self.path == "/api/stop":
                    ok, message = agent.stop(session)
                elif self.path == "/api/reply":
                    ok, message = agent.reply(session, body.get("text"))
                else:
                    ok, message = agent.jump(session)
            except Exception as exc:
                import traceback
                traceback.print_exc()
                ok, message = False, "Failed: {}".format(clip(exc, 200))
            log("{} {} -> {} ({:.1f}s) {}".format(self.path, session.get("key", "")[:24], "ok" if ok else "FAILED",
                                                  time.time() - started, clip(message, 160)))
            self.send(200, {"ok": ok, "message": message})

    return Handler


def page_version():
    try:
        return int(PAGE.stat().st_mtime_ns // 1_000_000)
    except OSError:
        return 0


# ---------------------------------------------------------------- start and stop

def room_is_running(url):
    """True when the port is held by an Agent Control Room, not some other program."""
    import urllib.request
    try:
        with urllib.request.urlopen(url + "api/version", timeout=2) as resp:
            return "version" in json.load(resp)
    except Exception:
        return False


def ask_running_room_to_quit(port):
    """Stop the room already on this port by asking it, which works the same on every system."""
    import urllib.request
    url = "http://127.0.0.1:{}/".format(port)
    if not room_is_running(url):
        return False
    req = urllib.request.Request(url + "api/quit", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json", "X-Control-Room": "1"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
    for _ in range(50):
        if not room_is_running(url):
            return True
        time.sleep(0.1)
    return False


def watch_and_reload(paths):
    """Developer convenience: restart in place when the code changes (not in packaged apps). Waits while an
    approval is pending, and keeps the running version when the new code does not compile."""
    def stamp():
        return {p: p.stat().st_mtime_ns for p in paths if p.exists()}
    start = stamp()
    while True:
        time.sleep(1)
        now = stamp()
        if now == start or APPROVALS.open_requests():
            continue
        time.sleep(0.5)  # let an editor finish writing
        try:
            for p in paths:
                compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except SyntaxError as exc:
            log("Code changed but does not compile ({}); keeping the running version.".format(exc))
            start = stamp()
            continue
        log("Code changed; restarting the room.")
        # rerun as a module so the package imports still resolve (sys.argv[0] is __main__.py)
        os.execv(sys.executable, [sys.executable, "-m", "agent_control_room"] + sys.argv[1:])


class Room:
    """The running room: poller plus HTTP server on 127.0.0.1."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.url = "http://127.0.0.1:{}/".format(port)
        self.poller = Poller(Store(DATA_DIR / "control-room.sqlite3"))
        self.httpd = None

    def start(self):
        """Bind and start serving in the background. Raises OSError when the port is taken."""
        self.poller.poll_once()
        self.poller.start()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.poller, self.port, self.stop))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if not getattr(sys, "frozen", False):
            code = sorted(Path(__file__).resolve().parent.rglob("*.py"))
            threading.Thread(target=watch_and_reload, args=(code,), daemon=True).start()
        log("Agent Control Room {} on {}".format(__version__, self.url))
        return self

    def stop(self):
        time.sleep(0.3)  # let the answer to /api/quit go out first
        SPEAKER.stop()
        if self.httpd:
            self.httpd.shutdown()
        os._exit(0)


def once():
    """One snapshot as JSON, for debugging and for plug-in authors."""
    poller = Poller(store=None)
    poller.poll_once()
    return poller.snapshot
