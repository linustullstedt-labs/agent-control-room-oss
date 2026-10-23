"""Read a reply aloud: the system's own voices by default, or (opt-in) OpenAI's voice with the user's own key.

Each paragraph is read in the language it is written in (Swedish or English for now; add words to SV_WORDS/EN_WORDS
or a new list to support more). The OpenAI voice is the room's only outbound call, made only when Listen is pressed
with "Voice: OpenAI" chosen; audio is cached per text so rereads are free.
"""
import json
import os
import re
import subprocess
import threading
import time

from . import system
from .settings import DATA_DIR
from .util import clip, log, plain

SV_WORDS = {"och", "är", "att", "det", "inte", "jag", "för", "på", "med", "som", "du", "en", "ett", "har", "kan",
            "vill", "ska", "nu", "så", "till", "från", "eller", "också", "finns", "klart", "inga", "ingen"}
EN_WORDS = {"the", "and", "is", "to", "of", "it", "not", "you", "for", "on", "with", "that", "this", "can", "will",
            "now", "have", "are", "or", "from", "also", "done", "no", "was", "be", "in", "a", "all", "got", "new", "has",
            "were", "but", "if", "as", "by", "an", "at", "we", "my", "your", "here", "there", "what", "which", "good"}


def speech_text(text, limit=280):
    """Make a reply pleasant to hear: no Markdown, code, links or long paths, cut at a sentence end."""
    text = re.sub(r"```.*?(```|$)", " ", str(text or ""), flags=re.S)
    text = re.sub(r"https?://\S+", " ", plain(text))
    text = re.sub(r"(?:~|\.{0,2})?/(?:[\w.\-]+/)+([\w.\-]*[\w\-])?", lambda m: m.group(1) or m.group(0).rstrip("/").rsplit("/", 1)[-1], text)  # paths: keep the last part
    text = re.sub(r"[|*_#>]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:end + 1] if end > limit // 3 else cut.rsplit(" ", 1)[0] + " …"


def speech_lang(text):
    """'sv' or 'en' from letters and common words; None when there is too little to tell."""
    words = re.findall(r"[a-zåäöé]+", (text or "").lower())
    sv = sum(w in SV_WORDS for w in words) + 2 * len(re.findall(r"[åäö]", (text or "").lower()))
    en = sum(w in EN_WORDS for w in words)
    if sv == en:
        return None
    return "sv" if sv > en else "en"


# Downloaded Premium or Enhanced voices win; after that the natural-sounding built-in ones.
# names on macOS (Alva, Samantha, ...) and Windows (Microsoft Hedvig, Microsoft Zira, ...)
VOICE_PREFS = {"sv": ("Alva", "Klara", "Oskar", "Microsoft Hedvig"),
               "en": ("Ava", "Zoe", "Samantha", "Daniel", "Karen", "Moira", "Microsoft Aria", "Microsoft Zira", "Microsoft David")}
VOICE_LOCALES = {"sv": ("sv_SE",), "en": ("en_US", "en_GB", "en_AU", "en_IE")}
_voices = {"list": None}


def installed_voices():
    if _voices["list"] is None:
        try:
            _voices["list"] = system.list_voices()
        except Exception:  # no voice list: the default system voice is used
            _voices["list"] = []
    return _voices["list"]


def pick_voice(lang, voices=None):
    voices = [(n.strip(), loc) for n, loc in (voices if voices is not None else installed_voices())
              if loc in VOICE_LOCALES.get(lang, ())]
    def rank(voice):
        name = voice[0]
        base = name.split(" (")[0]
        quality = 0 if "Premium" in name else 1 if "Enhanced" in name else 2
        pref = VOICE_PREFS[lang].index(base) if base in VOICE_PREFS[lang] else len(VOICE_PREFS[lang])
        return (quality, pref, VOICE_LOCALES[lang].index(voice[1]))
    ranked = sorted(voices, key=rank)
    if ranked and (rank(ranked[0])[0] < 2 or rank(ranked[0])[1] < len(VOICE_PREFS[lang])):
        return ranked[0][0]
    return None  # only novelty voices (or none listed): use the system default for the language


def reply_speech(session, limit=6000):
    """A project's longer reply as (lang, text) parts, paragraph by paragraph, each in the language it is written in.
    Code blocks, table rules and links are dropped; neighbouring paragraphs in one language become one part."""
    source = session.get("full") or session.get("line") or ""
    source = re.sub(r"```.*?(```|$)", "\n\n", str(source), flags=re.S)
    lines = []
    for line in source.split("\n"):  # tables: each row becomes a short sentence; the header and rule are skipped
        if re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*", line):
            if lines and lines[-1].startswith("\x00"):
                lines.pop()
            continue
        if re.fullmatch(r"\s*\|.*\|\s*", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
            lines.append("\x00" + ", ".join(cells) + ".")
        else:
            lines.append(line)
    source = "\n".join(l.lstrip("\x00") for l in lines)
    paragraphs = [p for p in re.split(r"\n\s*\n|\n(?=\s*(?:#|[-*] |\d+\. ))", source)
                  if p.strip() and not re.fullmatch(r"[\s|:\-]+", p)]
    whole = speech_lang(source) or "en"  # Swedish nearly always shows å, ä or ö, so unclear text is English
    parts, used, lang = [], 0, whole
    for paragraph in paragraphs:
        text = speech_text(paragraph, limit=limit)
        if not text:
            continue
        if used + len(text) > limit:
            break
        used += len(text)
        lang = speech_lang(text) or lang
        if parts and parts[-1][0] == lang:
            parts[-1] = (lang, parts[-1][1] + " " + text)
        else:
            parts.append((lang, text))
    return parts


# OpenAI voice (opt-in, setting "voice": "openai"). The one outbound call the room makes, and only when Listen is pressed.
# The key is the user's own, from the OPENAI_API_KEY environment variable; it is never written to disk or logged.
OPENAI_SPEECH = {"model": "gpt-4o-mini-tts", "voice": "marin", "response_format": "mp3",
                 "instructions": "Read this aloud like a calm, friendly colleague giving a spoken update. Natural pace. "
                                 "Read Swedish passages with a native Swedish voice and English passages in English."}
SPEECH_CACHE = DATA_DIR / "speech"


def openai_key():
    value = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not value:
        raise RuntimeError("Set the OPENAI_API_KEY environment variable to your own OpenAI API key to use the OpenAI voice")
    return value


def openai_speech_file(text):
    """The reply chunk as an mp3, from the cache when this exact text was read before (rereads are free)."""
    import hashlib
    import urllib.error
    import urllib.request
    digest = hashlib.sha256(json.dumps([OPENAI_SPEECH, text], sort_keys=True).encode()).hexdigest()[:32]
    path = SPEECH_CACHE / (digest + ".mp3")
    if path.exists() and path.stat().st_size > 0:
        return path
    body = json.dumps(dict(OPENAI_SPEECH, input=text)).encode()
    last = None
    for attempt in range(3):
        request = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body, method="POST",
                                         headers={"Authorization": "Bearer " + openai_key(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                audio = response.read()
            break
        except urllib.error.HTTPError as exc:
            last = "OpenAI answered {}".format(exc.code)
            if exc.code == 401:
                raise RuntimeError("OpenAI refused the key (401): check OPENAI_API_KEY")
            if exc.code not in (429, 500, 502, 503, 504):
                raise RuntimeError("{}: {}".format(last, clip(exc.read().decode("utf-8", "replace"), 160)))
        except (urllib.error.URLError, OSError) as exc:
            last = "Could not reach OpenAI: {}".format(clip(exc, 120))
        time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(last or "OpenAI did not answer")
    SPEECH_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(audio)
    tmp.replace(path)
    for old in SPEECH_CACHE.glob("*.mp3"):  # keep the cache small: two weeks
        try:
            if time.time() - old.stat().st_mtime > 14 * 86400:
                old.unlink()
        except OSError:
            pass
    return path


def speech_chunks(lines, first=500, rest=1500):
    """Join the reply into request-sized pieces at sentence ends; the first is short so playback starts quickly."""
    sentences = re.split(r"(?<=[.!?…])\s+", " ".join(text for _, text in lines))
    chunks, cur = [], ""
    for sentence in sentences:
        cap = first if not chunks else rest
        if cur and len(cur) + 1 + len(sentence) > cap:
            chunks.append(cur)
            cur = sentence
        else:
            cur = (cur + " " + sentence).strip()
    if cur:
        chunks.append(cur)
    return [c[:3500] for c in chunks]


class Speaker:
    """Reads a reply aloud, one piece after another: with OpenAI's voice when chosen, else with the system's voices.
    If OpenAI fails (no key, offline) the rest is read with the system's voices. A new request or stop() cuts it off."""
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.run = 0
        self.key = None
        self.active = False
        self.error = None

    @property
    def speaking(self):
        """The key of the row being read, or None."""
        with self.lock:
            return self.key if self.active else None

    def stop(self):
        with self.lock:
            self.run += 1
            self.active = False
            proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                system.cancel_speech()
            except Exception:
                pass

    def speak(self, lines, key=None, engine="system"):
        self.stop()
        with self.lock:
            run, self.key, self.active, self.error = self.run, key, True, None
        threading.Thread(target=self._play, args=(run, lines, engine), daemon=True).start()

    def _command(self, engine, lang, text):
        """(argv, stdin text, extra env) for one piece."""
        if engine == "openai":
            path = openai_speech_file(text)
            argv = system.play_command(path)
            if not argv:
                raise RuntimeError("no program to play mp3 audio was found (on Linux: install mpg123 or ffmpeg)")
            return argv, None, system.play_env(path)
        lang = lang or speech_lang(text) or "en"
        voice = pick_voice(lang)
        argv, stdin = system.speak_command(text, lang, voice)
        return argv, stdin, system.speak_env(voice)

    def _fail(self, exc):
        with self.lock:
            self.error = clip(exc, 200)
        log("/api/speak {}".format(clip(exc, 200)))

    def _play(self, run, lines, engine):
        pieces = [(None, c) for c in speech_chunks(lines)] if engine == "openai" else lines
        for lang, text in pieces:
            try:
                argv, stdin, env = self._command(engine, lang, text)
            except Exception as exc:
                self._fail(exc)
                if engine != "openai":
                    break  # no system voice either: nothing more to try
                engine = "system"  # OpenAI unavailable: finish with the system's voices and say why
                try:
                    argv, stdin, env = self._command(engine, lang, text)
                except Exception as exc2:
                    self._fail(exc2)
                    break
            with self.lock:
                if run != self.run:
                    return
                try:
                    self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 env=dict(os.environ, **env) if env else None,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except OSError as exc:
                    self.proc = None
                    failed = exc
                else:
                    failed = None
                proc = self.proc
            if failed:
                self._fail("could not start {}: {}".format(argv[0], failed))
                break
            if stdin is not None:
                try:
                    proc.communicate(stdin.encode("utf-8"))
                except (OSError, ValueError):
                    pass
            proc.wait()
        with self.lock:
            if run == self.run:
                self.proc, self.active = None, False


SPEAKER = Speaker()


