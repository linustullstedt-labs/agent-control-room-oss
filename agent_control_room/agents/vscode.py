"""VS Code helpers shared by agent plug-ins whose sessions live in VS Code."""
import os
import subprocess
import time

from .. import system
from ..util import HOME, read_fields, within

IDE_LOCKS = HOME / ".claude" / "ide"  # Claude Code's IDE lock files list each open window's workspace folders


def open_windows():
    """Workspace folders of the open VS Code windows (as far as the Claude Code extension reports them)."""
    folders = []
    for lock in IDE_LOCKS.glob("*.lock"):
        for folder in read_fields(lock, ["workspaceFolders"]).get("workspaceFolders") or []:
            folder = str(folder).rstrip("/\\")
            if os.path.isdir(folder) and folder not in folders:
                folders.append(folder)
    return folders


def folder_for(cwd):
    """The open workspace folder that contains cwd (longest match), else cwd itself."""
    best = None
    for folder in open_windows():
        if within(cwd, folder) and (best is None or len(folder) > len(best)):
            best = folder
    return best or cwd


def open_folder(folder, new_window=False, settle=0.0):
    """Open or raise the window for folder. Deep links land in the frontmost window, so callers wait `settle`."""
    cli = system.vscode_cli()
    if cli:
        subprocess.run([cli] + (["-n"] if new_window else []) + [folder], check=False,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        system.open_in_vscode(folder)
    if settle:
        time.sleep(settle)
