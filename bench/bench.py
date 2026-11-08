"""Time each stage of a 60-second short. Best of 3. Run: python bench/bench.py"""
import pathlib, sys, tempfile, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pixelshorts as p


def best(fn, n=3):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return min(out)


story = p.read_story(ROOT / "examples" / "bridge.txt")
beats = p.timed(story)
seconds = sum(p.beat_frames(b) for b in beats) / p.FPS
with tempfile.TemporaryDirectory() as d:
    d = pathlib.Path(d)
    frames = best(lambda: sum(1 for _ in p.frames({"title": story["title"], "beats": beats}, 1)))
    sound = best(lambda: p.score(beats, d / "a.wav", 1))
    full = best(lambda: p.render(story, d / "a.mp4"))
    size = (d / "a.mp4").stat().st_size
print(f"story: {seconds:.1f} s, {len(beats)} beats")
print(f"draw all frames: {frames:.1f} s")
print(f"score and SFX: {sound:.1f} s")
print(f"full render to mp4: {full:.1f} s, {size / 1048576:.1f} MB")
print(f"speed: {seconds / full:.1f} times real time")
