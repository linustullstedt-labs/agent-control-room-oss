"""Where the room keeps its own files, and the user's preferences (settings.json in the data folder)."""
import json
import os
from pathlib import Path

from . import system

DATA_DIR = Path(os.environ.get("AGENT_CONTROL_ROOM_DATA") or system.data_dir())
SETTINGS_FILE = DATA_DIR / "settings.json"
APPROVALS_FLAG = DATA_DIR / "approvals-on"  # read by the PermissionRequest hook; present = "Approve here" is on
ROOM_FILE = DATA_DIR / "room.json"         # port and secret key of the running room; readable by this user only


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


def write_room_file(info):
    """Write the running room's port and key so that only this user can read them (0600 on macOS/Linux; on Windows
    the data folder is already private to the user)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ROOM_FILE.with_suffix(".tmp")
    try:
        tmp.unlink()  # O_CREAT keeps an old file's permissions; start fresh so 0600 applies
    except OSError:
        pass
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(info, fh)
    tmp.replace(ROOM_FILE)


def read_room_file():
    try:
        return json.loads(ROOM_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def local_opener():
    """urllib for calls to 127.0.0.1 that never go through an HTTP proxy (corporate proxies would see the data)."""
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ignored_folders():
    """Folder names kept out of the room entirely (setting "ignored"), for private projects."""
    return {str(name).strip("/\\") for name in room_settings().get("ignored") or [] if str(name).strip("/\\")}


def is_ignored(cwd, name=None, ignored=None):
    ignored = ignored_folders() if ignored is None else ignored
    if not ignored:
        return False
    return name in ignored or any(part in ignored for part in Path(cwd or "").parts)
