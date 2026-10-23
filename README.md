# Agent Control Room

One local overview of every AI coding agent session on your computer: **who needs you, for what, and since when**.

If you run several Claude Code and Codex sessions at once, in VS Code windows, terminals and the desktop apps, the
room lists them all as one row each, sorted by what needs your attention. One click takes you to the right window.
Where the agent allows it, you can reply, approve permission prompts and start new agents from the room itself.

It is an observer, not another chat client: the agents keep running where they always run.

## What it does

- **One list of every session**: Claude Code (VS Code, terminal, background sessions, Claude app) and Codex (CLI,
  VS Code, ChatGPT desktop app). Each row shows one of six states: working, needs permission, needs an answer,
  finished, stopped unexpectedly, or unknown. It never guesses "needs you".
- **Go to window**: raises the VS Code window, terminal tab or app that runs the session.
- **Reply** to a session from the room (Claude Code through its session inbox; Codex in the background).
- **Approve here** (optional, off by default): Claude Code permission prompts wait in the room for Allow or Deny.
- **Start a new agent** in any recent folder, in VS Code or a terminal, with its first task.
- **Notifications** when an agent needs you or finishes a longer turn.
- **Listen**: reads a session's reply aloud with your system's voices, each paragraph in its own language. Optionally
  with OpenAI's voice, using your own API key.
- **Private folders**: list folder names under `"ignored"` in the settings file and the room never shows, stores,
  notifies about or reads them.

## Install

Download the installer for your system from the [latest release](https://github.com/alicelabs-ai/agent-control-room-oss/releases/latest):

| System | File | First start |
|---|---|---|
| macOS 11+ (Apple Silicon or Intel) | `…-macos-arm64.dmg` / `…-macos-x64.dmg` | Drag the app to Applications. The app is not signed yet: right-click it, choose **Open**, then **Open** again. |
| Windows 10/11 | `…-windows-x64.zip` | Unzip anywhere and run `Agent Control Room.exe`. SmartScreen may warn about an unknown publisher: **More info → Run anyway**. |
| Linux (x64) | `…-linux-x64.tar.gz` | Unpack and run `Agent Control Room/agent-control-room`. Opens in your browser if no system web view is available. |

Or run it from source with Python 3.9 or newer (no dependencies needed; `pywebview` adds the app window):

```sh
git clone https://github.com/alicelabs-ai/agent-control-room-oss
cd agent-control-room-oss
python -m pip install -e ".[app]"   # or skip this and use --browser
agent-control-room                  # or: python -m agent_control_room
```

You need [Claude Code](https://claude.com/claude-code) and/or [Codex](https://github.com/openai/codex) installed and
used as usual. The room finds them on its own, including the copies bundled with their VS Code extensions.

## Command line

```text
agent-control-room                 open the room in its own window (falls back to the browser)
agent-control-room --browser       open it in the default browser
agent-control-room --server        run without opening anything (http://127.0.0.1:7788/)
agent-control-room --once          print one snapshot as JSON and exit (handy for bug reports)
agent-control-room --restart       stop a room already running on the port first
agent-control-room --install-hook [--apply]    add the Claude Code approval hook (dry run without --apply)
agent-control-room --uninstall-hook [--apply]
```

## What works where

| Feature | macOS | Windows | Linux |
|---|---|---|---|
| List Claude Code and Codex sessions | ✓ | ✓ | ✓ |
| Go to a VS Code session | ✓ | ✓ | ✓ |
| Raise the exact terminal tab | Terminal, iTerm2 | – (switch yourself) | – (switch yourself) |
| Reply to Claude Code sessions | ✓ | not yet (needs testing) | ✓ |
| Reply to Codex threads | ✓ | ✓ (background resume) | ✓ |
| Approve Claude Code prompts in the room | ✓ | ✓ | ✓ |
| Start new agents (VS Code, terminal) | ✓ | ✓ | ✓ |
| Send to a thread open in the ChatGPT app | ✓ (needs Accessibility) | – | – |
| Notifications | ✓ | ✓ | ✓ (`notify-send`) |
| Listen (system voices) | ✓ (`say`) | ✓ (Windows voices) | ✓ (`spd-say` or `espeak-ng`) |

## Settings and data

Everything stays on your computer. The room listens on `127.0.0.1` only and keeps its settings in:

- macOS: `~/Library/Application Support/AgentControlRoom/`
- Windows: `%APPDATA%\AgentControlRoom\`
- Linux: `~/.local/share/agent-control-room/` (or `$XDG_DATA_HOME`)

`settings.json` there holds your choices. To keep a private project out of the room entirely:

```json
{ "ignored": ["my-private-folder"] }
```

It never reads token or key files (`~/.claude/sessions/*.key`, `~/.codex/auth.json`, token fields in lock files).

### Network

The room makes no outbound calls, with two exceptions that only happen when you ask:

- **Check for updates** asks GitHub for the latest release.
- **Listen with "Voice: OpenAI"** sends the reply text to OpenAI's text-to-speech API with your own key, from the
  `OPENAI_API_KEY` environment variable. The voice is AI-generated, and the room says so next to the button. Audio is
  cached locally, so listening to the same reply again is free. Note that OpenAI has announced the shutdown of
  its current speech models on 2027-01-06; after that the room falls back to your system's voices.

## Contributing

Bug reports, ideas and pull requests are welcome; support for more agents, tools and platforms especially. See
[CONTRIBUTING.md](CONTRIBUTING.md) for how the code is organised and how to add an agent.

## License

[GPL-3.0-or-later](LICENSE). Copyright © Alice Labs.
