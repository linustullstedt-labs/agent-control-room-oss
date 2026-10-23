"""Small helpers shared by the server and the agent plug-ins. Standard library only."""
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

HOME = Path.home()
TAIL_BYTES = 4_000_000  # how much of a transcript tail to parse
FOLDER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def now_ms():
    return int(time.time() * 1000)


def iso_to_ms(value):
    if not value:
        return None
    try:
        return int(dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def clip(text, limit=400):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def plain(text):
    """Flatten Markdown so a one-line summary reads as prose."""
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text))
    text = re.sub(r"(?m)^\s*(#+|>+|[-*] )\s*", "", text)
    return re.sub(r"\*\*|__|`", "", text)


def keep(text, limit=20000):
    """Trim without flattening, so the page can render paragraphs and lists."""
    if not text:
        return ""
    text = str(text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def drop_restatement(text):
    """Skip the 'You asked: …' line agents add when answering a message from the room."""
    if text and re.match(r"\s*You asked\b[^:\n]{0,40}:", text):
        parts = text.lstrip().split("\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return text


def tail_lines(path):
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > TAIL_BYTES:
            fh.seek(size - TAIL_BYTES)
            fh.readline()  # drop the partial first line
        data = fh.read()
    return data.decode("utf-8", "replace").splitlines()


def read_fields(path, fields):
    """Load a JSON file and keep only the named non-secret fields."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: raw.get(k) for k in fields if isinstance(raw, dict)}


class FileCache:
    """Re-parse a file only when its size or mtime changes."""

    def __init__(self, parser):
        self.parser = parser
        self.cache = {}

    def get(self, path):
        try:
            st = path.stat()
        except OSError:
            return {}
        key = (st.st_mtime_ns, st.st_size)
        hit = self.cache.get(str(path))
        if hit and hit[0] == key:
            return hit[1]
        value = self.parser(path)
        self.cache[str(path)] = (key, value)
        return value


# ---- paths: the same rules for "/" and "\" (and for C:\ vs c:\ on Windows)

def norm(path):
    return os.path.normcase(os.path.normpath(str(path))) if path else ""


def within(path, folder):
    """True when path is folder itself or somewhere inside it."""
    if not path or not folder:
        return False
    p, f = norm(path), norm(folder)
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def same_path(a, b):
    return bool(a) and bool(b) and norm(a) == norm(b)


_repo_cache = {}


def repo_name(cwd):
    """The repository a session works in: the folder holding .git above cwd, else the folder itself."""
    if not cwd:
        return "Unknown folder"
    if cwd in _repo_cache:
        return _repo_cache[cwd]
    path, name = Path(cwd), None
    while path != path.parent and not same_path(path, HOME):
        if (path / ".git").exists():
            name = path.name
            break
        path = path.parent
    name = name or (Path(cwd).name if not same_path(cwd, HOME) else "Home folder")
    _repo_cache[cwd] = name
    return name


def subtitle_for(name, repo):
    """Hide names that only repeat the folder, like the auto-generated 'agent-control-room-91'."""
    if not name or name == repo or re.fullmatch(re.escape(repo) + r"-[0-9a-z]{2,4}", name or ""):
        return ""
    return name


def log(message):
    print("[{}] {}".format(time.strftime("%H:%M:%S"), message), flush=True)
