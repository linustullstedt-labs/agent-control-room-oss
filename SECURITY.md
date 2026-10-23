# Security

The room runs on your computer, can raise windows, start agents, send messages to agent sessions and answer Claude
Code permission prompts when you switch "Approve here" on. Its safety rules:

- The HTTP server listens on `127.0.0.1` only. Every request must carry the right `Host` header (blocks DNS
  rebinding), and every API call must carry the room's secret key, new for each start and stored in a file only
  you can read. That keeps out websites, other programs and other users on the same computer.
- The approval hook first checks that the program on the room's port knows the key (without sending it), so a
  program squatting on the port never sees your requests.
- Messages sent to Claude Code sessions arrive labelled as coming from another session. Claude Code does not let such
  messages approve permission prompts or change settings.
- The approval hook fails open: if the room is not running, errors out or does not answer in time, the normal
  permission dialog shows in the agent's window. The hook never approves anything by itself.
- The room never reads key or token files and never writes your OpenAI key to disk or logs.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/linustullstedt-labs/agent-control-room-oss/security/advisories/new) for
this repository. We aim to answer within a week.
