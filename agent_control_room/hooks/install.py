"""Add or remove the Agent Control Room PermissionRequest hook in ~/.claude/settings.json.

Dry run by default from the command line: prints the change and writes nothing. Applying first copies the settings
file to settings.json.bak-<timestamp> next to it. Running it twice changes nothing the second time.

  agent-control-room --install-hook            # show what would change
  agent-control-room --install-hook --apply    # install
  agent-control-room --uninstall-hook --apply

The hook does nothing until "Approve here" is switched on in the room; with it off, every prompt shows as before.
"""
import json
import shutil
import sys
import time
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
MARKER = "--hook permission"
HOOK_TIMEOUT = 660  # seconds; the hook itself gives up after 600 and lets the normal dialog show


def hook_command():
    """How Claude Code should start the hook: the packaged app itself, or this Python with the package."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        # Windows: the window app has no stdout, so the hook runs the console build that ships next to it
        cli = exe.with_name("agent-control-room.exe")
        return "{} {}".format(json.dumps(str(cli if sys.platform.startswith("win") and cli.exists() else exe)), MARKER)
    return "{} -m agent_control_room {}".format(json.dumps(sys.executable), MARKER)


def ours(group):
    return any(MARKER in str(h.get("command", "")) for h in group.get("hooks", []))


def updated(settings, uninstall):
    new = json.loads(json.dumps(settings))
    hooks = new.setdefault("hooks", {})
    groups = [g for g in hooks.get("PermissionRequest", []) if not ours(g)]
    if not uninstall:
        groups.append({"matcher": "*", "hooks": [{"type": "command", "command": hook_command(), "timeout": HOOK_TIMEOUT}]})
    if groups:
        hooks["PermissionRequest"] = groups
    else:
        hooks.pop("PermissionRequest", None)
    if not hooks:
        new.pop("hooks")
    return new


def read_settings():
    if not SETTINGS.exists():
        raise ValueError("No {} found. Start Claude Code once, then try again.".format(SETTINGS))
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("{} is not valid JSON ({}). Fix it first; nothing was changed.".format(SETTINGS, exc))


def is_installed():
    try:
        current = read_settings()
    except ValueError:
        return False
    return any(ours(g) for g in (current.get("hooks") or {}).get("PermissionRequest") or [])


def apply(uninstall=False, dry_run=False):
    """(ok, message). Writes a timestamped backup before changing anything."""
    try:
        current = read_settings()
    except ValueError as exc:
        return False, str(exc)
    new = updated(current, uninstall)
    if new == current:
        return True, "Nothing to change: the hook is already {}.".format("absent" if uninstall else "installed")
    if dry_run:
        return True, "Would set PermissionRequest hooks to:\n" + json.dumps(new.get("hooks", {}).get("PermissionRequest", []), indent=2)
    backup = SETTINGS.with_name("settings.json.bak-" + time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(SETTINGS, backup)
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SETTINGS)
    if uninstall:
        return True, "Removed. Permission prompts show in their own window. Backup: {}".format(backup)
    return True, "Installed. A backup of your Claude settings is at {}".format(backup)
