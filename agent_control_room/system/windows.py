"""Windows: PowerShell for toasts and speech (System.Speech), `start` for URLs, Windows Terminal or cmd for agents.

Text never goes into a PowerShell command line: it is passed through environment variables or stdin, so quotes and
other characters in an agent's reply cannot change what runs.
"""
import os
import shutil
import subprocess
from pathlib import Path

NAME = "windows"
__all__ = ["NAME", "data_dir", "open_url", "vscode_cli", "open_in_vscode", "notify", "list_voices", "speak_command",
           "play_command", "speak_env", "play_env", "run_in_terminal", "press_enter_in", "frontmost_app_id", "focus_app", "focus_terminal"]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]


def data_dir():
    return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "AgentControlRoom"


def _ps(script, env=None, timeout=15):
    return subprocess.run(POWERSHELL + [script], capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, **(env or {})), creationflags=NO_WINDOW)


def open_url(url):
    try:
        os.startfile(url)  # noqa: S606 - the system's own URL handler
        return True
    except OSError:
        return False


def vscode_cli():
    found = shutil.which("code") or shutil.which("code.cmd")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
    return str(local) if local.exists() else None


def open_in_vscode(folder):
    cli = vscode_cli()
    if cli:
        return subprocess.run([cli, folder], check=False, creationflags=NO_WINDOW).returncode == 0
    return open_url("vscode://file/" + folder.replace("\\", "/"))


TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:ACR_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:ACR_MESSAGE)) > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def notify(title, message):
    _ps(TOAST, env={"ACR_TITLE": title, "ACR_MESSAGE": message})


VOICES = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture.Name }
"""

SPEAK = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:ACR_VOICE) { try { $s.SelectVoice($env:ACR_VOICE) } catch {} }
$s.Speak([Console]::In.ReadToEnd())
"""


def list_voices():
    try:
        out = _ps(VOICES).stdout or ""
    except Exception:
        return []
    voices = []
    for line in out.splitlines():
        if "|" in line:
            name, culture = line.strip().rsplit("|", 1)
            voices.append((name, culture.replace("-", "_")))
    return voices


def speak_command(text, lang, voice):
    # the voice name travels in the environment; see Speaker, which merges ACR_VOICE into the child's env
    return POWERSHELL + [SPEAK], text


def speak_env(voice):
    return {"ACR_VOICE": voice or ""}


def play_command(path):
    """Windows Media Player's engine through PowerShell; it plays mp3 without extra software."""
    script = ("Add-Type -AssemblyName presentationCore; $p = New-Object System.Windows.Media.MediaPlayer; "
              "$p.Open([Uri]$env:ACR_FILE); $p.Play(); Start-Sleep -Milliseconds 500; "
              "while ($p.NaturalDuration.HasTimeSpan -eq $false) { Start-Sleep -Milliseconds 100 }; "
              "Start-Sleep -Seconds $p.NaturalDuration.TimeSpan.TotalSeconds")
    return POWERSHELL + [script]


def play_env(path):
    return {"ACR_FILE": str(path)}


def run_in_terminal(folder, command):
    wt = shutil.which("wt")
    try:
        if wt:
            subprocess.Popen([wt, "-d", folder, "cmd", "/k", command])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", command], cwd=folder)
        return True, ""
    except OSError as exc:
        return False, "Could not open a terminal: {}".format(exc)


def press_enter_in(process, label):
    return False, "pressing Enter for you is only available on macOS"


def frontmost_app_id():
    return None


def focus_app(app_id):
    pass


def focus_terminal(pid, name):
    return False, "Raising the exact terminal window is not supported on Windows yet. Switch to it with Alt+Tab."
