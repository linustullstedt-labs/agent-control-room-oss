# PyInstaller recipe for the packaged app. Build with:  python packaging/build.py
# One folder build (fast start, which matters because Claude Code starts the app for every approval hook).
import sys
from pathlib import Path

from agent_control_room import __version__

ROOT = Path(SPECPATH).parent
ICONS = ROOT / "packaging" / "icons"
NAME = "Agent Control Room"
WINDOWS = sys.platform.startswith("win")
MAC = sys.platform == "darwin"

a = Analysis([str(ROOT / "packaging" / "launcher.py")], pathex=[str(ROOT)],
             datas=[(str(ROOT / "agent_control_room" / "web"), "agent_control_room/web")],
             hiddenimports=["agent_control_room.system.macos", "agent_control_room.system.windows",
                            "agent_control_room.system.linux"],
             excludes=["tkinter"], noarchive=False)
pyz = PYZ(a.pure)

app = EXE(pyz, a.scripts, [], exclude_binaries=True, name=NAME if not sys.platform.startswith("linux") else "agent-control-room",
          console=False, icon=str(ICONS / ("icon.ico" if WINDOWS else "icon.icns" if MAC else "icon.png")))
programs = [app]
if WINDOWS:
    # The window app has no stdout on Windows; the approval hook and the command line use this console twin.
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="agent-control-room", console=True,
              icon=str(ICONS / "icon.ico"))
    programs.append(cli)

coll = COLLECT(*programs, a.binaries, a.datas, name=NAME)

if MAC:
    BUNDLE(coll, name=NAME + ".app", icon=str(ICONS / "icon.icns"), bundle_identifier="ai.alicelabs.agent-control-room",
           info_plist={"CFBundleShortVersionString": __version__, "CFBundleVersion": __version__,
                       "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
                       "NSAppleEventsUsageDescription": "Agent Control Room raises the window of the agent you pick."})
