"""macOS: AppleScript for windows and keys, Notification Center, `say` and `afplay`."""
import json
import re
import shutil
import subprocess
from pathlib import Path

NAME = "macos"
__all__ = ["NAME", "data_dir", "open_url", "vscode_cli", "open_in_vscode", "notify", "list_voices", "speak_command",
           "play_command", "speak_env", "play_env", "cancel_speech", "run_in_terminal", "press_enter_in", "frontmost_app_id", "focus_app", "focus_terminal"]

VSCODE_APP_CLI = "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"


def data_dir():
    return Path.home() / "Library" / "Application Support" / "AgentControlRoom"


def osascript(script):
    proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    return proc.returncode == 0, (proc.stdout or proc.stderr).strip()


def open_url(url):
    return subprocess.run(["open", url], check=False).returncode == 0


def vscode_cli():
    return VSCODE_APP_CLI if Path(VSCODE_APP_CLI).exists() else shutil.which("code")


def open_in_vscode(folder):
    return subprocess.run(["open", "-a", "Visual Studio Code", folder], check=False).returncode == 0


def notify(title, message):
    """Plain text only, passed as AppleScript strings via JSON quoting."""
    script = "display notification {} with title {} sound name \"Glass\"".format(
        json.dumps(message, ensure_ascii=False), json.dumps(title, ensure_ascii=False))
    subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)


def list_voices():
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout or ""
    except Exception:
        return []
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"(?m)^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", out)]


def speak_command(text, lang, voice):
    return ["say"] + (["-v", voice] if voice else []) + ["--", text], None


def play_command(path):
    return ["afplay", str(path)]


def speak_env(voice):
    return {}


def cancel_speech():
    pass


def play_env(path):
    return {}


def run_in_terminal(folder, argv):
    command = "cd {} && {}".format(_sh_quote(folder), " ".join(_sh_quote(a) for a in argv))
    script = 'tell application "Terminal"\n do script {}\n activate\nend tell'.format(json.dumps(command, ensure_ascii=False))
    ok, msg = osascript(script)
    return ok, "" if ok else "Could not open Terminal: " + msg[:160]


def _sh_quote(text):
    import shlex
    return shlex.quote(text)


def frontmost_app_id():
    ok, out = osascript('tell application "System Events" to get bundle identifier of first application process whose frontmost is true')
    return out if ok and re.fullmatch(r"[A-Za-z0-9.\-]+", out or "") else None


def focus_app(app_id):
    if app_id:
        osascript('tell application id "{}" to activate'.format(app_id))


def press_enter_in(process, label):
    """Press Enter, but only if the given app is frontmost, so the key can never land in another app."""
    ok, out = osascript(
        'tell application "System Events"\n'
        ' set fp to name of first application process whose frontmost is true\n'
        ' if fp is not "{p}" then return "{l} was not in front (" & fp & ")"\n'
        ' key code 36\n return "ok"\nend tell'.format(p=process, l=label))
    if ok and out == "ok":
        return True, ""
    if "1743" in out or "not authorized to send apple events" in out.lower():
        # the "control System Events" question was declined: macOS remembers that, so open the Automation page
        open_url("x-apple.systempreferences:com.apple.preference.security?Privacy_Automation")
        return False, ("macOS blocks Agent Control Room from controlling System Events: switch it on under Automation "
                       "in the System Settings page that just opened")
    if "1002" in out or "not allowed" in out.lower() or "assistive" in out.lower():
        open_url("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        # an unsigned app gets a new identity on every rebuild, so an old grant can show as on and still not count
        return False, ("macOS needs Accessibility access for Agent Control Room: in the System Settings page that just "
                       "opened, switch it on, or if it already is, remove it with − and add it again, then try again")
    return False, out[:120]


def _host_app(pid):
    """Walk up the process tree to the .app that hosts a terminal session."""
    for _ in range(8):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        if not out:
            return None
        ppid, comm = out.split(None, 1)
        match = re.search(r"/([^/]+)\.app/Contents/MacOS/", comm)
        if match:
            return match.group(1)
        pid = ppid
    return None


def focus_terminal(pid, name):
    tty = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    app = _host_app(pid)
    if tty and tty != "??" and app == "Terminal":
        ok, msg = osascript(
            'tell application "Terminal"\n'
            ' repeat with w in windows\n  repeat with t in tabs of w\n'
            '   if tty of t is "/dev/{tty}" then\n'
            '    set selected of t to true\n    set index of w to 1\n    activate\n    return "ok"\n'
            '   end if\n  end repeat\n end repeat\nend tell'.format(tty=tty))
        return ok, "Raised the Terminal tab for {}".format(name) if ok else msg
    if tty and tty != "??" and app == "iTerm2":
        ok, msg = osascript(
            'tell application "iTerm2"\n'
            ' repeat with w in windows\n  repeat with t in tabs of w\n   repeat with s in sessions of t\n'
            '    if tty of s is "/dev/{tty}" then\n'
            '     select w\n     select t\n     activate\n     return "ok"\n'
            '    end if\n   end repeat\n  end repeat\n end repeat\nend tell'.format(tty=tty))
        return ok, "Raised the iTerm2 tab for {}".format(name) if ok else msg
    if app:
        subprocess.run(["open", "-a", app], check=False)
        return True, "Activated {}. It cannot select the exact tab yet.".format(app)
    return False, "Could not find the terminal that runs this session."
