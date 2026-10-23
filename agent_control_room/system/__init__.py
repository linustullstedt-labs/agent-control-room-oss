"""Everything that differs between macOS, Windows and Linux, behind one small interface.

Each module (macos, windows, linux) provides the same functions. Callers import from here and never check the
platform themselves, so adding a platform means adding one module.

  NAME                          "macos" | "windows" | "linux"
  data_dir()                    folder for the room's settings and caches
  open_url(url)                 hand a URL (http, vscode://, codex://) to the system
  vscode_cli()                  path to VS Code's `code` command, or None
  open_in_vscode(folder)        raise or open the VS Code window for a folder
  notify(title, message)        a desktop notification
  list_voices()                 [(voice name, locale like "sv_SE")]
  speak_command(text, lang, voice)  (argv, stdin text or None) that reads text aloud and waits until done
  play_command(path)            argv that plays an mp3 and waits, or None when no player is available
  speak_env(voice) / play_env(path)  extra environment for those commands (text never goes in argv on Windows)
  cancel_speech()               silence speech a killed player may have left queued (speech-dispatcher)
  run_in_terminal(folder, argv)  (ok, message): open a terminal window running argv (never a quoted string)
  press_enter_in(process, label)    (ok, why): press Enter in an app, only if it is frontmost (macOS only)
  frontmost_app_id() / focus_app(app_id)  macOS only; None / no-op elsewhere
  focus_terminal(pid, name)     (ok, message): raise the terminal tab that runs a process
"""
import sys

if sys.platform == "darwin":
    from . import macos as _impl
elif sys.platform.startswith("win"):
    from . import windows as _impl
else:
    from . import linux as _impl

globals().update({name: getattr(_impl, name) for name in _impl.__all__})
