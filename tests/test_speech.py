"""Tests for reading a reply aloud (agent_control_room/speech.py). Nothing is spoken; no voice program runs."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent_control_room import speech as c  # noqa: E402
from agent_control_room import settings  # noqa: E402


class SpeechTextTest(unittest.TestCase):
    def test_strips_markdown_links_code_and_paths(self):
        text = c.speech_text("See **PLAN.md** at https://x.y/z and `code`.\n```\nrm -rf x\n```\nIn ~/repos/example-org/. Done.")
        self.assertNotIn("http", text)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("*", text)
        self.assertIn("example-org. Done.", text)

    def test_cuts_long_text_at_a_sentence_end(self):
        text = c.speech_text("First sentence is here. " * 30, limit=100)
        self.assertTrue(text.endswith("."))
        self.assertLessEqual(len(text), 100)


class LanguageTest(unittest.TestCase):
    def test_swedish_and_english(self):
        self.assertEqual(c.speech_lang("Supabase är uppgraderad till small, du kan merga"), "sv")
        self.assertEqual(c.speech_lang("It starts automatically when you log in after a reboot."), "en")
        self.assertIsNone(c.speech_lang("massutskick"))


class VoiceTest(unittest.TestCase):
    def test_premium_wins_then_preferred_then_none_for_novelty(self):
        self.assertEqual(c.pick_voice("sv", [("Alva", "sv_SE"), ("Alva (Premium)", "sv_SE")]), "Alva (Premium)")
        self.assertEqual(c.pick_voice("en", [("Bells", "en_US"), ("Samantha", "en_US")]), "Samantha")
        self.assertIsNone(c.pick_voice("en", [("Bells", "en_US")]))


class ReplySpeechTest(unittest.TestCase):
    def test_each_paragraph_in_its_own_language(self):
        parts = c.reply_speech({"full": "Jag har pushat ändringarna.\n\nThe tests passed and the deploy is live.\n\nVill du att jag mergar?"})
        self.assertEqual([lang for lang, _ in parts], ["sv", "en", "sv"])

    def test_tables_read_as_rows_and_code_is_skipped(self):
        parts = c.reply_speech({"full": "Ten repos got new commits:\n\n| Repo | New commits |\n|---|---|\n| billing-api | 3 |\n\n```\ngit pull\n```\nAll good."})
        self.assertEqual(parts, [("en", "Ten repos got new commits: billing-api, 3. All good.")])

    def test_falls_back_to_the_short_line_and_stops_at_the_limit(self):
        self.assertEqual(c.reply_speech({"line": "Working on the deploy"}), [("en", "Working on the deploy")])
        long = "\n\n".join("This is paragraph number {} of the reply.".format(i) for i in range(400))
        self.assertLessEqual(sum(len(t) for _, t in c.reply_speech({"full": long}, limit=500)), 500)
        self.assertEqual(c.reply_speech({}), [])


class OpenAIVoiceTest(unittest.TestCase):
    def test_chunks_start_short_and_end_at_sentences(self):
        lines = [("en", "This is one sentence of the reply. " * 60)]
        chunks = c.speech_chunks(lines, first=200, rest=600)
        self.assertLessEqual(len(chunks[0]), 200)
        self.assertTrue(all(len(x) <= 600 and x.endswith(".") for x in chunks))
        self.assertEqual(" ".join(chunks), lines[0][1].strip())

    def test_falls_back_to_system_voices_when_openai_fails(self):
        import time
        played = []
        real = (c.openai_speech_file, c.subprocess.Popen, c.system.speak_command, c._voices["list"])

        def no_openai(text):
            raise RuntimeError("Could not reach OpenAI")

        def fake_popen(cmd, **kw):
            played.append(cmd[0])
            return real[1]([sys.executable, "-c", "pass"])
        c.openai_speech_file, c.subprocess.Popen = no_openai, fake_popen
        c.system.speak_command = lambda text, lang, voice: (["speak-stub", lang], None)
        c._voices["list"] = []
        try:
            speaker = c.Speaker()
            speaker.speak([("sv", "Hej, det här är ett test.")], key="k", engine="openai")
            for _ in range(100):
                if not speaker.speaking:
                    break
                time.sleep(0.05)
        finally:
            c.openai_speech_file, c.subprocess.Popen, c.system.speak_command, c._voices["list"] = real
        self.assertEqual(played, ["speak-stub"])
        self.assertIn("Could not reach OpenAI", speaker.error)
        self.assertIsNone(speaker.speaking)

    def test_no_key_says_how_to_set_one(self):
        import os
        saved = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                c.openai_key()
        finally:
            if saved is not None:
                os.environ["OPENAI_API_KEY"] = saved


class IgnoredFolderTest(unittest.TestCase):
    def test_matches_the_folder_anywhere_in_the_path_or_by_name(self):
        hidden = {"private-notes"}
        self.assertTrue(settings.is_ignored("/Users/x/private-notes", None, hidden))
        self.assertTrue(settings.is_ignored("/Users/x/private-notes/notes", None, hidden))
        self.assertTrue(settings.is_ignored("", "private-notes", hidden))
        self.assertFalse(settings.is_ignored("/Users/x/private-notes-old", "private-notes-old", hidden))
        self.assertFalse(settings.is_ignored("/Users/x/billing-api", "billing-api", set()))


if __name__ == "__main__":
    unittest.main()
