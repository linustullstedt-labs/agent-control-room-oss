"""Command line and app entry point.

  agent-control-room                 open the room in its own window (falls back to the browser)
  agent-control-room --browser       open the room in the default browser
  agent-control-room --server        run without any window (open http://127.0.0.1:7788/ yourself)
  agent-control-room --once          print one snapshot as JSON and exit
  agent-control-room --restart       stop a room already running on the port first
  agent-control-room --install-hook [--apply] / --uninstall-hook [--apply]
  agent-control-room --hook permission   (run by Claude Code; not for people)
"""
import argparse
import json
import sys
import threading

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent-control-room", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=7788)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--browser", action="store_true", help="open in the default browser instead of a window")
    mode.add_argument("--server", action="store_true", help="run without opening anything")
    mode.add_argument("--once", action="store_true", help="print one snapshot as JSON and exit")
    mode.add_argument("--install-hook", action="store_true", help="add the Claude Code approval hook (dry run unless --apply)")
    mode.add_argument("--uninstall-hook", action="store_true", help="remove the approval hook (dry run unless --apply)")
    mode.add_argument("--hook", choices=["permission"], help=argparse.SUPPRESS)
    parser.add_argument("--apply", action="store_true", help="with --install-hook/--uninstall-hook: write the change")
    parser.add_argument("--restart", action="store_true", help="stop a room already running on this port, then start")
    parser.add_argument("--dev", action="store_true", help="restart automatically when the code changes (from a checkout)")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = parser.parse_args(argv)

    if args.hook == "permission":  # keep this path light: Claude Code waits for it
        from .hooks import permission
        return permission.main()
    if args.install_hook or args.uninstall_hook:
        from .hooks import install
        ok, message = install.apply(uninstall=args.uninstall_hook, dry_run=not args.apply)
        print(message + ("" if args.apply or not ok else "\n\nDry run: nothing written. Add --apply to make the change."))
        return 0 if ok else 2
    if args.once:
        from .server import once
        if sys.stdout is None:
            return 2  # the windowed Windows app has no console: use agent-control-room.exe
        data = json.dumps(once(), indent=2, ensure_ascii=False) + "\n"
        out = getattr(sys.stdout, "buffer", None)
        if out:
            out.write(data.encode("utf-8"))
        else:
            sys.stdout.write(data)
        return 0

    import os
    from . import system
    from .server import Room, ask_running_room_to_quit, room_is_running, running_page_url
    if args.dev:
        os.environ["AGENT_CONTROL_ROOM_DEV"] = "1"
    url = "http://127.0.0.1:{}/".format(args.port)
    if args.restart:
        ask_running_room_to_quit(args.port)
    if room_is_running(url):
        print("The room is already running on {}.".format(url))
        return show(running_page_url(args.port), args)
    try:
        room = Room(args.port).start()
    except OSError as exc:
        print("Cannot listen on 127.0.0.1:{} ({}). Another program uses this port; try --port.".format(args.port, exc),
              file=sys.stderr)
        return 2
    if args.server:
        print("Open {} (the key in the address keeps other users out).".format(room.page_url), flush=True)
        threading.Event().wait()
        return 0
    return show(room.page_url, args, keep_serving=True, system=system)


def show(url, args, keep_serving=False, system=None):
    """Open the page: in the app window when possible, else in the browser."""
    if args.server:
        return 0
    if not args.browser:
        from . import app
        if app.available():
            try:
                app.run(url)  # returns when the window closes
                return 0
            except Exception as exc:  # e.g. Linux without GTK/Qt web libraries: use the browser instead
                print("Could not open the app window ({}); opening the browser instead.".format(exc), file=sys.stderr)
    if system is None:
        from . import system
    system.open_url(url)
    if keep_serving:
        print("Open {} in your browser. Press Ctrl+C to stop.".format(url), flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
