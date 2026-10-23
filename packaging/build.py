"""Build the installable app for this system into dist/:  macOS .dmg, Windows .zip, Linux .tar.gz.

  python packaging/build.py

Needs: pip install -e ".[app,build]". Builds are unsigned for now (see README: macOS shows "unidentified developer"
the first time, Windows SmartScreen may warn).
"""
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent_control_room import __version__  # noqa: E402

NAME = "Agent Control Room"
DIST = ROOT / "dist"


def run(*argv, **kw):
    print("+", " ".join(str(a) for a in argv), flush=True)
    subprocess.run([str(a) for a in argv], check=True, **kw)


def arch():
    machine = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)


def main():
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", DIST, "--workpath", ROOT / "build",
        ROOT / "packaging" / "agent-control-room.spec", cwd=ROOT)
    base = "Agent-Control-Room-{}".format(__version__)
    if sys.platform == "darwin":
        stage = ROOT / "build" / "dmg"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        run("ditto", DIST / (NAME + ".app"), stage / (NAME + ".app"))
        os.symlink("/Applications", stage / "Applications")
        out = DIST / "{}-macos-{}.dmg".format(base, arch())
        run("hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-fs", "HFS+", "-format", "UDZO", "-ov", out)
    elif sys.platform.startswith("win"):
        out = DIST / "{}-windows-{}".format(base, arch())
        shutil.make_archive(str(out), "zip", DIST, NAME)
        out = out.with_suffix(".zip")
    else:
        out = DIST / "{}-linux-{}.tar.gz".format(base, arch())
        with tarfile.open(out, "w:gz") as tar:
            tar.add(DIST / NAME, arcname=NAME)
    print("Built", out)


if __name__ == "__main__":
    main()
