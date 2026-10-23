"""The platform layer and path helpers behave the same everywhere. Nothing here opens windows or runs programs."""
import ntpath
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_control_room import util  # noqa: E402
from agent_control_room.system import linux, macos, windows  # noqa: E402


class InterfaceTest(unittest.TestCase):
    def test_every_platform_offers_the_same_functions(self):
        names = set(macos.__all__)
        self.assertEqual(names, set(windows.__all__))
        self.assertEqual(names, set(linux.__all__))
        for module in (macos, windows, linux):
            missing = [n for n in module.__all__ if not hasattr(module, n)]
            self.assertEqual(missing, [], module.NAME)

    def test_data_folders(self):
        with mock.patch.dict("os.environ", {"XDG_DATA_HOME": "/x/share"}):
            self.assertEqual(linux.data_dir(), Path("/x/share/agent-control-room"))
        with mock.patch.dict("os.environ", {"APPDATA": "C:/Users/a/AppData/Roaming"}):
            self.assertEqual(windows.data_dir(), Path("C:/Users/a/AppData/Roaming") / "AgentControlRoom")
        self.assertTrue(str(macos.data_dir()).endswith("Application Support/AgentControlRoom"))

    def test_windows_speech_text_never_goes_into_the_command_line(self):
        argv, stdin = windows.speak_command('say "hi"; Remove-Item x', "en", "Microsoft Zira")
        self.assertEqual(stdin, 'say "hi"; Remove-Item x')
        self.assertNotIn("Remove-Item x", " ".join(argv))
        self.assertEqual(windows.speak_env("Microsoft Zira"), {"ACR_VOICE": "Microsoft Zira"})

    def test_mac_only_features_say_so_elsewhere(self):
        for module in (windows, linux):
            ok, why = module.press_enter_in("Code", "VS Code")
            self.assertFalse(ok)
            self.assertIn("macOS", why)


class PathTest(unittest.TestCase):
    def test_within_posix(self):
        self.assertTrue(util.within("/home/a/repo/sub", "/home/a/repo"))
        self.assertTrue(util.within("/home/a/repo", "/home/a/repo/"))
        self.assertFalse(util.within("/home/a/repo-old", "/home/a/repo"))
        self.assertFalse(util.within("", "/home/a"))

    def test_within_windows(self):
        with mock.patch.object(util, "os") as fake_os:
            fake_os.sep = "\\"
            fake_os.path = ntpath
            self.assertTrue(util.within(r"C:\Users\a\repo\sub", r"c:\users\a\repo"))
            self.assertTrue(util.within("C:/Users/a/repo", r"C:\Users\a\repo"))
            self.assertFalse(util.within(r"C:\Users\a\repo-old", r"C:\Users\a\repo"))


if __name__ == "__main__":
    unittest.main()
