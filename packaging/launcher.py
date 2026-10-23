"""Entry script for the packaged app (PyInstaller needs a plain script, not a package's __main__)."""
import sys

from agent_control_room.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
