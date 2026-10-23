"""Where the room keeps its own files, and the user's preferences (settings.json in the data folder)."""
import json
import os
from pathlib import Path

from . import system

DATA_DIR = Path(os.environ.get("AGENT_CONTROL_ROOM_DATA") or system.data_dir())
SETTINGS_FILE = DATA_DIR / "settings.json"
APPROVALS_FLAG = DATA_DIR / "approvals-on"  # read by the PermissionRequest hook; present = "Approve here" is on


def room_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_room_settings(values):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = dict(room_settings(), **values)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)
    return merged


def ignored_folders():
    """Folder names kept out of the room entirely (setting "ignored"), for private projects."""
    return {str(name).strip("/\\") for name in room_settings().get("ignored") or [] if str(name).strip("/\\")}


def is_ignored(cwd, name=None, ignored=None):
    ignored = ignored_folders() if ignored is None else ignored
    if not ignored:
        return False
    return name in ignored or any(part in ignored for part in Path(cwd or "").parts)
