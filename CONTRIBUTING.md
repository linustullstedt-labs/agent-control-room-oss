# Contributing

Thanks for helping. This file explains how the room is built, how to run it while you work on it, and how to add an
agent, tool or platform.

## Run it from a checkout

```sh
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install -e ".[app,build]"
python -m agent_control_room --browser --port 7799 --dev
```

With `--dev` the room restarts itself when you save a `.py` file, and the page reloads on its own.

Tests and lint, the same as CI runs on macOS, Windows and Linux:

```sh
python -m unittest discover -s tests -v
python -m pyflakes agent_control_room tests
```

Build the installer for your own system into `dist/`:

```sh
python packaging/build.py
```

## How it is organised

```text
agent_control_room/
  __main__.py      command line and app entry point
  app.py           the desktop window (pywebview), with the browser as fallback
  server.py        polling, notifications, approvals, launching, and the HTTP API for the page (127.0.0.1 only)
  agents/          one plug-in per agent: how to list its sessions, open them, reply, start new ones
    base.py        the Agent interface and the session fields the page expects
    claude_code.py Claude Code
    codex.py       Codex (CLI, VS Code extension, ChatGPT app)
    vscode.py      shared VS Code helpers
  system/          everything that differs between macOS, Windows and Linux, behind one interface
  speech.py        Listen: text clean-up, language per paragraph, system voices, optional OpenAI voice
  settings.py      data folder and settings.json
  hooks/           the Claude Code PermissionRequest hook and its installer
  web/index.html   the whole page: plain HTML, CSS and JavaScript, no build step
packaging/         PyInstaller recipe, icons and build script
tests/             unit tests (standard library unittest)
```

Design rules that keep it trustworthy:

1. **Observer, not owner.** The room never hosts an agent in its own process. It may open a window and start an agent
   there, but the session lives in VS Code, a terminal or the agent's app as usual.
2. **Documented surfaces first.** Prefer an agent's documented commands and files (for example `claude agents --json`).
   Undocumented files may enrich a row, but must degrade to the `unknown` state when they are missing or change.
3. **Never guess "needs you".** Only show `needs-permission` or `needs-answer` when the agent says so.
4. **Never read secrets.** No key or token files, no token fields. Read named fields from JSON files, never whole files
   you then pass around.
5. **Standard library only** for the room itself. `pywebview` is optional and only draws the window.
6. **No outbound network calls** except the two the user asks for (update check, OpenAI voice). Calls to the room
   itself bypass HTTP proxies.
7. **Every API call carries the room's key** (`X-Control-Room` header). Only `/api/hello` works without it, and it
   only proves the key (HMAC of a nonce) so a client can check it talks to the real room before sending anything.
8. **Anything that edits another tool's settings** (like the hook installer) is a dry run by default, writes a
   timestamped backup first, and is safe to run twice.

## Add an agent

1. Create `agent_control_room/agents/<name>.py` with a subclass of `agents.base.Agent`:
   - `vendor` (short id) and `label` (shown to people)
   - `sessions()` returns session dicts with the fields listed in `agents/base.py`
   - optionally `jump()`, `reply()`, `stop()`, `launch()`, `recent_folders()` and `launch_targets`
2. Add an instance to `AGENTS` in `agents/__init__.py`.
3. Add tests with small fixture files that look like the agent's own (see `tests/` for the pattern). Never commit real
   transcripts.
4. Mention it in the README's "What works where" table.

The page shows any vendor; the filter buttons at the top only list Claude and Codex today, so add yours to
`web/index.html` if it should get its own filter.

## Add or improve a platform

All platform code lives in `agent_control_room/system/<platform>.py`, and every module must provide the same names
(`tests/test_platform.py` checks this). When a feature cannot work on a platform, return `(False, "<what to do
instead>")` rather than raising, so the rest of the room keeps working.

Things that especially need testing on real machines: Claude Code's session inbox on Windows, raising terminal windows
on Windows and Linux, and the app window on Linux desktops (GTK or Qt).

## Pull requests

- Keep each change focused and include a test when you fix a bug.
- Run the tests and lint above.
- Describe what you tested by hand, and on which system.
