import array, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest, wave

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pixelshorts as p

TINY = """title: Tiny test
bridge day march: Soldiers cross the bridge in step.
bridge day shake: The deck starts to bounce.
bridge storm collapse: Then it falls.
bridge storm stand: Nobody built it that way again.
"""


def cli(*args, **kw):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    return subprocess.run([sys.executable, str(ROOT / "pixelshorts.py"), *map(str, args)], capture_output=True, encoding="utf-8", errors="replace", env=env, **kw)


class Parse(unittest.TestCase):
    def test_reads_beats_title_and_comments(self):
        s = p.parse("title: T\n# note\n\nbridge day march: Two words.\n")
        self.assertEqual(s["title"], "T")
        self.assertEqual(len(s["beats"]), 1)
        self.assertEqual(s["beats"][0]["action"], "march")

    def test_unknown_words_name_the_line(self):
        for bad, word in (("moon day march: x", "moon"), ("bridge noon march: x", "noon"), ("bridge day jump: x", "jump"), ("no colon here", "colon"), ("bridge day: x", "place light action")):
            with self.assertRaises(p.Refused) as e:
                p.parse(bad)
            self.assertIn(word, str(e.exception))

    def test_empty_story_is_refused(self):
        with self.assertRaises(p.Refused):
            p.parse("title: only a title\n")

    def test_smart_punctuation_is_kept_readable_and_other_scripts_refused(self):
        s = p.parse("bridge day stand: It’s “fine” — really…\n")
        self.assertEqual(s["beats"][0]["text"], "It's \"fine\" - really...")
        with self.assertRaises(p.Refused) as e:
            p.parse("bridge day stand: \uB2E4\uB9AC\n")
        self.assertIn("--no-captions", str(e.exception))
        self.assertEqual(p.parse("bridge day stand: \uB2E4\uB9AC\n", captions=False)["beats"][0]["text"], "\uB2E4\uB9AC")

    def test_timing_uses_the_narration_rate(self):
        s = p.parse("bridge day stand: " + " ".join(["word"] * 37) + "\n")
        self.assertAlmostEqual(p.timed(s)[0]["dur"], 10.0, places=1)
        short = p.parse("bridge day stand: Hi.\n")
        self.assertGreaterEqual(p.timed(short)[0]["dur"], p.MIN_BEAT)

    def test_voice_length_stretches_every_beat(self):
        s = p.parse("bridge day stand: one two three four\nbridge day walk: five six seven eight\n")
        beats = p.timed(s, voice=20.0)
        self.assertAlmostEqual(sum(b["dur"] for b in beats), 20.0, places=3)


class Lint(unittest.TestCase):
    def test_flags_length_and_long_hook(self):
        s = p.parse("bridge day stand: " + " ".join(["word"] * 40) + "\n")
        notes = p.lint(s)
        self.assertTrue(any("55 to 70" in n for n in notes))
        self.assertTrue(any("hook" in n.lower() for n in notes))

    def test_example_story_is_in_range(self):
        s = p.parse((ROOT / "examples" / "bridge.txt").read_text(encoding="utf-8"))
        self.assertEqual(p.lint(s), [])


class Frames(unittest.TestCase):
    def setUp(self):
        self.beats = p.timed(p.parse(TINY))

    def test_frames_have_the_right_size_and_count(self):
        frames = list(p.frames({"title": "T", "beats": self.beats}, seed=1))
        self.assertEqual(len(frames), sum(p.beat_frames(b) for b in self.beats))
        self.assertTrue(all(len(f) == p.W * p.H * 3 for f in frames[:5]))

    def test_same_seed_same_frames_different_seed_differs(self):
        story = {"title": "T", "beats": self.beats}
        a, b, c = [list(p.frames(story, seed=s)) for s in (1, 1, 2)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_collapse_removes_the_hero_for_later_beats(self):
        # Same place, light and zoom, so any difference comes from the hero being gone.
        beats = [{"place": "bridge", "light": "day", "action": a, "text": "x", "dur": d} for a, d in (("stand", 1.0), ("collapse", 3.0), ("stand", 1.0))]
        frames = list(p.frames({"title": "", "beats": beats}, seed=1, captions=False))
        before, after = frames[6], frames[-1]
        count = sum(1 for i in range(0, len(after), 3) if before[i:i + 3] != after[i:i + 3])
        self.assertGreater(count, 2000)

    def test_every_place_light_action_renders(self):
        for place in p.PLACES:
            for light in p.LIGHTS:
                story = {"title": "", "beats": [{"place": place, "light": light, "action": "stand", "text": "x", "dur": 0.2}]}
                self.assertTrue(list(p.frames(story, seed=1)))
        for action in p.ACTIONS:
            story = {"title": "", "beats": [{"place": "town", "light": "day", "action": action, "text": "x", "dur": 0.4}]}
            self.assertTrue(list(p.frames(story, seed=1)), action)

    def test_captions_wrap_inside_the_frame(self):
        text = " ".join(["Internationalization"] * 60) + " " + "x" * 80
        story = {"title": "", "beats": [{"place": "town", "light": "day", "action": "stand", "text": text, "dur": 16.0}]}
        self.assertTrue(list(p.frames(story, seed=1)))
        for line in p.wrap(text):
            self.assertLessEqual(len(line), p.CAP_CHARS)

    def test_png_writer_makes_a_real_png(self):
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "a.png"
            p.write_png(out, bytes(p.W * p.H * 3))
            self.assertEqual(out.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class Audio(unittest.TestCase):
    def test_score_has_sound_and_exact_length_and_no_clipping(self):
        beats = p.timed(p.parse(TINY))
        with tempfile.TemporaryDirectory() as d:
            out = pathlib.Path(d) / "a.wav"
            p.score(beats, out, seed=1)
            with wave.open(str(out)) as w:
                self.assertAlmostEqual(w.getnframes() / w.getframerate(), sum(p.beat_frames(b) for b in beats) / p.FPS, delta=0.01)
                data = w.readframes(w.getnframes())
        samples = array.array("h", data)
        self.assertGreater(max(samples), 3000)
        self.assertLess(max(map(abs, samples)), 32760)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = pathlib.Path(self.tmp.name)
        self.story = self.d / "s.txt"
        self.story.write_text(TINY, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, path):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)], capture_output=True, text=True)
        return json.loads(r.stdout)["streams"]

    def test_render_makes_a_vertical_video_with_sound_and_qa_passes(self):
        out = self.d / "a b.mp4"
        r = cli("render", self.story, "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        streams = self.probe(out)
        video = [s for s in streams if s["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertTrue([s for s in streams if s["codec_type"] == "audio"])
        q = cli("qa", out)
        self.assertEqual(q.returncode, 0, q.stdout + q.stderr)
        self.assertIn("LUFS", q.stdout)

    def test_voice_track_is_mixed_and_sets_the_length(self):
        voice = self.d / "v.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=440:d=9", str(voice)], check=True)
        out = self.d / "a.mp4"
        r = cli("render", self.story, "-o", out, "--voice", voice, "--no-captions")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
        self.assertAlmostEqual(float(d), 9.0, delta=0.5)

    def test_2k_export(self):
        out = self.d / "k.mp4"
        self.assertEqual(cli("render", self.story, "-o", out, "--2k").returncode, 0)
        video = [s for s in self.probe(out) if s["codec_type"] == "video"][0]
        self.assertEqual((video["width"], video["height"]), (1440, 2560))

    def test_qa_catches_a_black_and_frozen_video(self):
        out = self.d / "black.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=270x480:d=8", str(out)], check=True)
        r = cli("qa", out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("black", r.stdout)
        self.assertIn("frozen", r.stdout)

    def test_stills_write_one_png_per_beat(self):
        r = cli("stills", self.story, "-o", self.d / "st")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(list((self.d / "st").glob("*.png"))), 4)

    def test_bad_input_exits_2_with_no_traceback_and_no_output(self):
        out = self.d / "o.mp4"
        for args in (("render", self.d / "missing.txt", "-o", out), ("render", self.story, "-o", self.story), ("render", self.story, "-o", out, "--voice", self.d / "none.wav"), ("qa", self.d / "none.mp4")):
            r = cli(*args)
            self.assertEqual(r.returncode, 2, args)
            self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(out.exists())
        bad = self.d / "bad.txt"
        bad.write_bytes(b"\xff\xfe\x00bad")
        self.assertEqual(cli("check", bad).returncode, 2)

    def test_voice_that_is_not_audio_leaves_no_partial_output(self):
        fake = self.d / "v.wav"
        fake.write_text("not audio", encoding="utf-8")
        out = self.d / "o.mp4"
        r = cli("render", self.story, "-o", out, "--voice", fake)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(out.exists())

    def test_check_prints_plan(self):
        r = cli("check", ROOT / "examples" / "bridge.txt")
        self.assertEqual(r.returncode, 0)
        self.assertIn("beats", r.stdout)


if __name__ == "__main__":
    unittest.main()
