"""Linux: xdg-open, notify-send, speech-dispatcher or espeak-ng, and whichever terminal and mp3 player exist.

Every tool is optional. When one is missing the feature says so instead of failing, and the rest keeps working.
"""
import os
import shutil
import subprocess
from pathlib import Path

NAME = "linux"
__all__ = ["NAME", "data_dir", "open_url", "vscode_cli", "open_in_vscode", "notify", "list_voices", "speak_command",
           "play_command", "speak_env", "play_env", "run_in_terminal", "press_enter_in", "frontmost_app_id",
           "focus_app", "focus_terminal"]

TERMINALS = (  # (program, args before the command); the command runs through `sh -c` so the window stays useful
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-x"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("xterm", ["-e"]),
)
PLAYERS = (("mpg123", ["-q"]), ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]), ("mpv", ["--really-quiet"]),
           ("gst-play-1.0", ["-q"]))


def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "agent-control-room"


def open_url(url):
    opener = shutil.which("xdg-open") or shutil.which("gio")
    if not opener:
        return False
    args = [opener, "open", url] if opener.endswith("gio") else [opener, url]
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def vscode_cli():
    return shutil.which("code") or shutil.which("codium")


def open_in_vscode(folder):
    cli = vscode_cli()
    if cli:
        subprocess.Popen([cli, folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    return open_url("vscode://file" + folder)


def notify(title, message):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=Agent Control Room", "--", title, message], capture_output=True, timeout=10)


def list_voices():
    """speech-dispatcher and espeak-ng pick a voice from the language, so only languages matter here."""
    return []


def speak_command(text, lang, voice):
    code = (lang or "en").split("_")[0]
    if shutil.which("spd-say"):
        return ["spd-say", "--wait", "-l", code, "--", text], None
    if shutil.which("espeak-ng"):
        return ["espeak-ng", "-v", code, "--stdin"], text
    if shutil.which("espeak"):
        return ["espeak", "-v", code, "--stdin"], text
    raise RuntimeError("No speech program found: install speech-dispatcher (spd-say) or espeak-ng")


def play_command(path):
    for program, args in PLAYERS:
        if shutil.which(program):
            return [program] + args + [str(path)]
    return None


def speak_env(voice):
    return {}


def play_env(path):
    return {}


def run_in_terminal(folder, command):
    for program, args in TERMINALS:
        if shutil.which(program):
            try:
                subprocess.Popen([program] + args + ["sh", "-c", command + "; exec \"${SHELL:-sh}\""], cwd=folder,
                                 start_new_session=True)
                return True, ""
            except OSError as exc:
                return False, "Could not open {}: {}".format(program, exc)
    return False, "No terminal program found (tried x-terminal-emulator, gnome-terminal, konsole and others)"


def press_enter_in(process, label):
    return False, "pressing Enter for you is only available on macOS"


def frontmost_app_id():
    return None


def focus_app(app_id):
    pass


def focus_terminal(pid, name):
    return False, "Raising the exact terminal window is not supported on Linux yet. Switch to it yourself."
