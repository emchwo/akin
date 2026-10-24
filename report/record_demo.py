"""Record a captioned demo video of one real game (real model calls, scripted clicks).

Start a tagged server first so the recording never counts as a real session:
    AKIN_LOG_TAG=video PORT=5077 python app.py
then:
    python report/record_demo.py                 # writes akin_demo.mp4 in the repo root (git-ignored)
    python report/record_demo.py --no-captions   # same run, no captions (akin_demo_no_captions.mp4)

Playwright records the browser; waits for the AI are shortened in the edit (the loader still shows briefly)
and rounds 2-5 are sped up, so the video lands at about 2-3 minutes. Needs: playwright, imageio-ffmpeg.
"""

import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
BASE = ARGS[0] if ARGS else "http://127.0.0.1:5077"
CAPTIONS = "--no-captions" not in sys.argv
ROOT = Path(__file__).resolve().parent.parent
RAW = Path(__file__).resolve().parent / "video_raw"
OUT = ROOT / ("akin_demo.mp4" if CAPTIONS else "akin_demo_no_captions.mp4")
W, H = 1280, 800

APP_H = 720          # the app plays in the top 1280x720; captions get their own strip below it
CAPTION_H = H - APP_H

WRAPPER = f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:wght@300&family=Inter:wght@400&display=swap" rel="stylesheet">
<style>
  html, body {{ margin:0; background:#F5F1EA; overflow:hidden; }}
  iframe {{ display:block; width:{W}px; height:{APP_H}px; border:0; }}
  #cap {{ height:{CAPTION_H}px; box-sizing:border-box; border-top:1px solid #CFC6B8; background:#EFE9DF;
         display:flex; align-items:center; justify-content:center; padding:0 60px; text-align:center;
         font:400 19px/1.45 Inter,"Segoe UI",Arial,sans-serif; color:#2E2A25; transition:opacity .3s; }}
</style></head><body><iframe id="app" src="{{src}}"></iframe><div id="cap"></div></body></html>"""

CAPTION_JS = """
(text) => {
  const c = document.getElementById('cap');
  c.style.opacity = text ? 1 : 0;
  if (text) c.textContent = text;
}"""

CARD_JS = """
([title, sub]) => {
  let c = document.getElementById('demo-card');
  if (!title) { if (c) c.remove(); return; }
  if (!c) { c = document.createElement('div'); c.id = 'demo-card'; document.body.appendChild(c); }
  c.style.cssText = 'position:fixed;inset:0;z-index:100;background:#F5F1EA;display:flex;flex-direction:column;' +
    'align-items:center;justify-content:center;gap:18px;text-align:center;padding:40px;';
  c.innerHTML = '';
  const t = document.createElement('div'); t.textContent = title;
  t.style.cssText = 'font:300 64px/1.1 Fraunces,Georgia,serif;color:#2E2A25;';
  const s = document.createElement('div'); s.textContent = sub;
  s.style.cssText = 'font:300 24px/1.5 Inter,Segoe UI,Arial,sans-serif;color:#7A7166;max-width:820px;';
  c.append(t, s);
}"""


class Timeline:
    """Marks stretches of the raw recording to shorten (AI waits) or speed up (repeated rounds)."""

    def __init__(self):
        self.t0 = None
        self.marks = []  # (start, end, kind)
        self._open = None

    def start(self):
        self.t0 = time.monotonic()

    def now(self):
        return time.monotonic() - self.t0

    def begin(self, kind):
        self._open = (self.now(), kind)

    def end(self):
        s, kind = self._open
        self.marks.append((s, self.now(), kind))
        self._open = None


def main():
    RAW.mkdir(exist_ok=True)
    for f in RAW.glob("*.webm"):
        f.unlink()
    tl = Timeline()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(RAW),
                                  record_video_size={"width": W, "height": H})
        top = ctx.new_page()
        tl.start()
        # The wrapper page holds the app (iframe) and, when captions are on, a caption strip below it.
        top.set_content(WRAPPER.replace("{src}", BASE + "/") if CAPTIONS else
                        WRAPPER.replace("{src}", BASE + "/").replace(f"height:{APP_H}px", f"height:{H}px"))
        top.wait_for_selector("iframe")
        page = top.query_selector("#app").content_frame()  # the app's own frame; all clicks go here
        page.wait_for_load_state()
        cap = (lambda text: top.evaluate(CAPTION_JS, text)) if CAPTIONS else (lambda text: None)
        card = lambda title, sub="": top.evaluate(CARD_JS, [title, sub])
        btn = lambda text: page.locator("button:not([disabled])", has_text=text).first
        hold = lambda s: top.wait_for_timeout(int(s * 1000))

        def wait_for(selector_or_fn, kind="wait", timeout=120000):
            tl.begin(kind)
            if callable(selector_or_fn):
                selector_or_fn(timeout)
            else:
                page.wait_for_selector(selector_or_fn, timeout=timeout)
            tl.end()

        def options_for(who):
            return lambda timeout: page.wait_for_function(
                "who => document.querySelectorAll('.option:not([disabled])').length === 2 && "
                "document.querySelector('.eyebrow').textContent.startsWith(who)", arg=who, timeout=timeout)

        def type_slow(sel, text):
            page.click(sel)
            page.fill(sel, "")
            page.type(sel, text, delay=90)

        page.wait_for_selector("#name-a")
        card("Akin", "A two-player conversation game about how each of you sees love, played on one device. "
                     "This is a real game: every scenario, question, and result is written live by AI agents.")
        hold(5)
        card(None)
        page.evaluate("startScreen()")  # replay the logo animation now that the card is gone
        hold(2.6)

        cap("Two players share one laptop. Type both first names and pick a setting: first date or friends.")
        type_slow("#name-a", "Tom")
        type_slow("#name-b", "Zendaya")
        hold(1.5)
        btn("Start").click()
        cap("The Planner picks the least-clear topic. The Scenario Writer drafts a scenario, and a Checker approves it before you see it.")
        wait_for("text=the screen is turned away")

        for rnd in range(1, 7):
            first, fast = rnd == 1, 2 <= rnd <= 5
            if fast:
                tl.begin("fast")
            if first:
                cap("Round 1. Read the scenario together.")
                hold(6)
                cap("Tom takes the laptop and turns the screen away from Zendaya.")
                hold(2.5)
            elif fast and rnd == 2:
                cap("Rounds 2 to 5 (sped up): each round goes to whichever topic is still least clear for this pair.")
                hold(2)
            elif rnd == 6:
                cap("Round 6.")
                hold(3)
            else:
                hold(2)
            btn("the screen is turned away").click()
            page.wait_for_timeout(0)
            if fast:
                options_for("Tom")(30000)
            else:
                wait_for(options_for("Tom"))
            if first:
                cap("Tom answers privately.")
                hold(3.5)
            else:
                hold(1.5)
            page.locator(".option").nth(rnd % 2).click()
            btn("I'm Zendaya").wait_for(timeout=10000)
            if first:
                cap("Hand-off. Zendaya turns the screen away from Tom. Tom can still change his answer here.")
                hold(5)
            else:
                hold(1.2)
            btn("I'm Zendaya").click()
            options_for("Zendaya")(30000)
            if first:
                cap("Zendaya answers privately, without seeing Tom's answer.")
            hold(3 if first else 1.2)
            page.locator(".option").nth(0 if rnd in (1, 3, 4) else 1).click()
            btn("Reveal answers").wait_for(timeout=10000)
            hold(2 if first else 1)
            btn("Reveal answers").click()
            if first:
                cap("Both answers are revealed at once.")
            if fast:
                page.wait_for_selector("button:has-text('Next scenario'), button:has-text('Keep going')", timeout=120000)
            else:
                wait_for("button:has-text('Next scenario'), button:has-text('Keep going')")
            if first:
                cap("The Prompt Writer turns the two answers into one question about your own lives, to talk about together.")
                hold(5)
                page.evaluate("window.scrollBy({top: 500, behavior: 'smooth'})")
                cap("Take your time talking. Meanwhile the next scenario is already being written in the background.")
                hold(7)
            elif rnd == 6:
                page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
                cap("After round 6 you choose: end now, or play up to 2 more rounds on the topics that are still close.")
                hold(6)
            else:
                hold(7)  # "talking": lets the next scenario finish in the background
            if fast:
                tl.end()
            if rnd < 6:
                btn("Next scenario").click()
                wait_for("text=the screen is turned away")

        btn("End now").click()
        cap("The Reveal Writer synthesizes each player's answers into one of eight types.")
        wait_for(".players")
        hold(3.5)
        cap("Each type's icon draws itself, then three meters and a plain summary of each side.")
        hold(5)
        for y in (520, 520, 520):
            page.evaluate(f"window.scrollBy({{top: {y}, behavior: 'smooth'}})")
            hold(1.2)
        cap("The evidence interprets what each person values and how it shapes how they connect, citing the rounds behind it.")
        hold(7)
        page.evaluate("window.scrollTo({top: document.querySelector('.clickclash').offsetTop - 120, behavior: 'smooth'})")
        cap("Where you'd click, and where you'd clash.")
        hold(6)
        page.click("#stats-toggle")
        cap("The Stats panel: tokens and cost for every agent in this game, about half a cent in total.")
        hold(6)
        page.click("#stats-close")
        for g in page.locator(".feel .toggle").all():
            g.locator("button").first.click()
            hold(0.6)
        cap("Each player says whether their type feels right, then two quick feedback questions. Nothing is saved with names.")
        hold(2)
        btn("Continue").click()
        page.wait_for_selector("text=learn something new")
        hold(2)
        page.locator(".toggle").nth(0).locator("button").first.click()
        hold(0.8)
        page.locator(".toggle").nth(1).locator("button").first.click()
        hold(0.8)
        btn("Send feedback").click()
        page.wait_for_selector("text=Keep talking.")
        cap(None)
        hold(3)
        card("akin", "github.com/emchwo/akin  ·  Emily Chow  ·  MGT 409 Final Project")
        hold(4.5)
        total = tl.now()
        ctx.close()
        browser.close()

    raw = next(RAW.glob("*.webm"))
    edit(raw, tl.marks, total)


def edit(raw, marks, total):
    """Cut the raw recording into segments: AI waits shrink to ~1.6 s, sped-up stretches play 3x."""
    segs, t = [], 0.0
    for s, e, kind in sorted(marks):
        if s > t:
            segs.append((t, s, 1.0))
        dur = e - s
        if kind == "wait":
            segs.append((s, e, max(1.0, dur / 1.6)))
        else:
            segs.append((s, e, 3.0))
        t = e
    segs.append((t, total, 1.0))
    segs = [(s, e, sp) for s, e, sp in segs if e - s > 0.05]
    parts = "".join(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=(PTS-STARTPTS)/{sp:.3f}[v{i}];"
                    for i, (s, e, sp) in enumerate(segs))
    filt = parts + "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0,fps=30[out]"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(raw), "-filter_complex", filt, "-map", "[out]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(OUT)],
                   check=True)
    final = sum((e - s) / sp for s, e, sp in segs)
    print(f"raw {total:.0f}s -> edited {final:.0f}s ({final / 60:.1f} min): {OUT}")


if __name__ == "__main__":
    main()
