"""The HTTP server and the hook installer, with a fake agent and a throwaway data folder and Claude settings file."""
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

TMP = tempfile.mkdtemp(prefix="acr-test-")
os.environ["AGENT_CONTROL_ROOM_DATA"] = TMP  # before the package reads it
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_control_room import agents, server, settings  # noqa: E402
from agent_control_room.agents.base import Agent  # noqa: E402
from agent_control_room.hooks import install  # noqa: E402

ROW = {"key": "fake:1", "vendor": "fake", "client": "terminal", "name": "Fake session", "repo": "demo", "subtitle": "",
       "cwd": "/tmp/demo", "state": "finished", "since": 1, "startedAt": 1, "line": "Done.", "full": "All done.",
       "task": "do it", "canReply": True, "source": "test"}


class FakeAgent(Agent):
    vendor = "fake"
    label = "Fake"

    def __init__(self):
        self.replies = []

    def sessions(self):
        return [dict(ROW)]

    def reply(self, session, text):
        self.replies.append(text)
        return True, "Sent."

    def jump(self, session):
        return True, "Raised."


class BrokenAgent(Agent):
    vendor = "broken"
    label = "Broken"

    def sessions(self):
        raise RuntimeError("cannot read its files")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakeAgent()
        data = Path(TMP)
        cls.patches = [mock.patch.object(settings, "DATA_DIR", data),  # never the user's real settings
                       mock.patch.object(settings, "SETTINGS_FILE", data / "settings.json"),
                       mock.patch.object(server, "DATA_DIR", data),
                       mock.patch.object(server, "APPROVALS_FLAG", data / "approvals-on"),
                       mock.patch.object(settings, "ROOM_FILE", data / "room.json"),
                       mock.patch.object(server, "AGENTS", [cls.fake, BrokenAgent()]),
                       mock.patch.dict(server.BY_VENDOR, {"fake": cls.fake}),
                       mock.patch.object(agents, "AGENTS", [cls.fake])]
        for p in cls.patches:
            p.start()
        cls.port = free_port()
        with mock.patch.object(server.sys, "frozen", True, create=True):  # no code watcher in tests
            cls.room = server.Room(cls.port).start()
        cls.url = "http://127.0.0.1:{}/".format(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.room.httpd.shutdown()
        for p in cls.patches:
            p.stop()

    def get(self, path, key=True):
        req = urllib.request.Request(self.url + path, headers={"X-Control-Room": self.room.token} if key else {})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def post(self, path, body, header=True, host=None):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Control-Room"] = self.room.token if header is True else header
        if host:
            headers["Host"] = host
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_sessions_and_a_broken_plugin_does_not_blank_the_page(self):
        snap = self.get("api/sessions")
        self.assertEqual([s["key"] for s in snap["sessions"]], ["fake:1"])
        self.assertTrue(any("Broken: cannot read its files" in e for e in snap["errors"]))
        self.assertEqual(snap["version"], server.__version__)

    def test_reply_goes_to_the_right_plugin(self):
        code, out = self.post("api/reply", {"key": "fake:1", "text": "hello"})
        self.assertEqual((code, out["ok"]), (200, True))
        self.assertIn("hello", self.fake.replies)

    def test_guards(self):
        self.assertEqual(self.post("api/reply", {"key": "fake:1", "text": "x"}, header=False)[0], 403)
        self.assertEqual(self.post("api/reply", {"key": "fake:1", "text": "x"}, host="evil.example")[0], 403)
        self.assertEqual(self.post("api/nothing", {})[0], 404)
        self.assertEqual(self.post("api/jump", {"key": "gone:1"})[0], 404)

    def test_close_hides_the_row_until_it_changes(self):
        self.post("api/close", {"key": "fake:1"})
        self.assertEqual(self.get("api/sessions")["sessions"], [])
        server.save_room_settings({"closed": {}})
        self.assertEqual(len(self.get("api/sessions")["sessions"]), 1)

    def test_every_api_call_needs_the_key(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("api/sessions", key=False)
        self.assertEqual(ctx.exception.code, 403)
        self.assertEqual(self.post("api/reply", {"key": "fake:1", "text": "x"}, header="1")[0], 403)
        self.assertEqual(self.post("api/client-log", {"kind": "x"}, header=False)[0], 403)

    def test_hello_proves_the_key_without_sending_it(self):
        answer = self.get("api/hello?n=abc", key=False)
        self.assertEqual(answer["proof"], server.proof(self.room.token, "abc"))
        self.assertNotIn(self.room.token, json.dumps(answer))

    def test_room_file_is_private_and_the_hook_trusts_only_the_real_room(self):
        from agent_control_room.hooks import permission
        info = settings.read_room_file()
        self.assertEqual((info["port"], info["token"]), (self.port, self.room.token))
        if os.name == "posix":
            self.assertEqual(os.stat(settings.ROOM_FILE).st_mode & 0o777, 0o600)
        self.assertTrue(permission.find_room())
        settings.write_room_file({"port": self.port, "token": "not-the-key"})
        try:
            self.assertFalse(permission.find_room())
        finally:
            settings.write_room_file({"port": self.port, "token": self.room.token})

    def test_approval_waits_only_for_a_session_the_room_shows(self):
        self.assertIsNone(self.post("api/permission", {"session_id": "unknown", "tool_name": "Bash"})[1]["id"])
        server.APPROVALS.pending.clear()
        ROW_ID = "s-1"
        with mock.patch.dict(ROW, {"sessionId": ROW_ID}):
            self.room.poller.poll_once()
            rid = self.post("api/permission", {"session_id": ROW_ID, "tool_name": "Bash", "tool_input": {"command": "ls"}})[1]["id"]
            self.assertTrue(rid)
            row = self.get("api/sessions")["sessions"][0]
            self.assertEqual((row["state"], row["approvalId"]), ("needs-permission", rid))
        server.APPROVALS.pending.clear()
        self.room.poller.poll_once()

    def test_page_is_served(self):
        with urllib.request.urlopen(self.url, timeout=5) as r:
            self.assertIn(b"Agent Control Room", r.read())


class HookInstallTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="acr-claude-"))
        self.settings = self.dir / "settings.json"
        self.settings.write_text(json.dumps({"model": "x", "hooks": {"PermissionRequest": [
            {"matcher": "*", "hooks": [{"type": "command", "command": "other-tool"}]}]}}))
        self.patch = mock.patch.object(install, "SETTINGS", self.settings)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_install_is_idempotent_keeps_other_hooks_and_backs_up(self):
        self.assertTrue(install.apply()[0])
        self.assertTrue(install.is_installed())
        once = self.settings.read_text()
        ok, message = install.apply()
        self.assertIn("Nothing to change", message)
        self.assertEqual(self.settings.read_text(), once)
        groups = json.loads(once)["hooks"]["PermissionRequest"]
        self.assertEqual(len(groups), 2)
        self.assertTrue(list(self.dir.glob("settings.json.bak-*")))

    def test_uninstall_leaves_other_hooks(self):
        install.apply()
        install.apply(uninstall=True)
        groups = json.loads(self.settings.read_text())["hooks"]["PermissionRequest"]
        self.assertEqual([g["hooks"][0]["command"] for g in groups], ["other-tool"])

    def test_dry_run_writes_nothing(self):
        before = self.settings.read_text()
        ok, message = install.apply(dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(self.settings.read_text(), before)


if __name__ == "__main__":
    unittest.main()
