"""Build report.html and slides.html: every number comes from the real logs, and every image is embedded.

    python report/build.py

Inputs (all produced by real runs):
    logs/benchmark.jsonl          tools/benchmark.py (cache on and --no-cache games)
    logs/trace.jsonl              AKIN_TRACE=1 server run used for the screenshots
    report/img/*.png              report/screenshots.py
    report/img/traced_game_id.txt the game the screenshots and trace come from
    report/safety_test_output.txt python tests/safety_tests.py > report/safety_test_output.txt

Templates: report/report_src.html and report/slides_src.html use {{name}} placeholders and
<img src="img/x.png">. Output: report.html and slides.html in the repo root.
"""

import base64
import html
import io
import json
import re
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOGS = ROOT / "logs"
IMG = HERE / "img"

GAMES_PER_USER_MONTH = 4  # assumption, stated in the report: about one game a week


def load_jsonl(p):
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def avg(xs):
    return sum(xs) / len(xs) if xs else 0.0


def money(x, digits=4):
    return f"${x:,.{digits}f}"


def num(x):
    return f"{x:,.0f}"


def metrics():
    bench = load_jsonl(LOGS / "benchmark.jsonl")
    on = [b for b in bench if b["cache"]]
    off = [b for b in bench if not b["cache"]]
    m = {}
    for tag, rows in (("on", on), ("off", off)):
        m[f"{tag}_n"] = str(len(rows))
        if not rows:
            continue
        m[f"{tag}_rounds"] = f"{avg([r['rounds'] for r in rows]):.1f}"
        m[f"{tag}_calls"] = f"{avg([r['llm_calls'] for r in rows]):.1f}"
        for k in ("input", "cached_input", "cache_write", "output", "reasoning"):
            m[f"{tag}_{k}"] = num(avg([r["total"].get(k, 0) for r in rows]))
        tot_in = sum(r["total"]["input"] for r in rows)
        m[f"{tag}_cached_pct"] = f"{100 * sum(r['total']['cached_input'] for r in rows) / max(tot_in, 1):.0f}%"
        m[f"{tag}_cost"] = money(avg([r["total"]["cost_usd"] for r in rows]), 5)
        m[f"_{tag}_cost"] = avg([r["total"]["cost_usd"] for r in rows])
        m[f"{tag}_cost_min"] = money(min(r["total"]["cost_usd"] for r in rows), 5)
        m[f"{tag}_cost_max"] = money(max(r["total"]["cost_usd"] for r in rows), 5)
        sw = sum(r["by_agent"]["scenario_writer"]["calls"] for r in rows)
        rej = sum(r["checker_rejections"] for r in rows)
        m[f"{tag}_rejections"] = str(rej)
        m[f"{tag}_reject_rate"] = f"{100 * rej / max(sw, 1):.0f}%"
        m[f"{tag}_fallbacks"] = str(sum(r["fallbacks"] for r in rows))
        m[f"{tag}_reveal_fallbacks"] = str(sum(1 for r in rows if r["reveal_source"] != "writer"))
        m[f"{tag}_rounds_list"] = ", ".join(str(r["rounds"]) for r in rows)

    # Per-agent table (cache on)
    agents = ["scenario_writer", "checker", "prompt_writer", "reveal_writer"]
    labels = {"scenario_writer": "Scenario Writer", "checker": "Checker", "prompt_writer": "Prompt Writer",
              "reveal_writer": "Reveal Writer"}
    rows_html = []
    for a in agents:
        rs = [r["by_agent"][a] for r in on]
        if not rs:
            continue
        rows_html.append(
            f"<tr><th>{labels[a]}</th><td>{avg([x['calls'] for x in rs]):.1f}</td>"
            f"<td>{num(avg([x['input'] for x in rs]))}</td><td>{num(avg([x['cached_input'] for x in rs]))}</td>"
            f"<td>{num(avg([x['output'] for x in rs]))}</td><td>{money(avg([x['cost_usd'] for x in rs]), 5)}</td></tr>")
    m["agent_rows"] = "\n".join(rows_html)

    # Per-game table (cache on and off)
    game_rows = []
    for i, r in enumerate(on + off, 1):
        t = r["total"]
        game_rows.append(
            f"<tr><th>{i}</th><td>{'on' if r['cache'] else 'off'}</td><td>{'First date' if r['setting'] == 'first_date' else 'Friends'}</td>"
            f"<td>{r['rounds']}</td><td>{r['llm_calls']}</td><td>{num(t['input'])}</td><td>{num(t['cached_input'])}</td>"
            f"<td>{num(t['output'])}</td><td>{r['checker_rejections']}</td><td>{money(t['cost_usd'], 5)}</td></tr>")
    m["game_rows"] = "\n".join(game_rows)

    if on:
        total_cost = sum(r["total"]["cost_usd"] for r in on)
        m["checker_share"] = f"{100 * sum(r['by_agent']['checker']['cost_usd'] for r in on) / total_cost:.0f}%"
    if on and off:
        m["cache_saving_pct"] = f"{100 * (1 - m['_on_cost'] / m['_off_cost']):.0f}%"
    # Scale
    c = m.get("_on_cost", 0)
    m["games_per_month"] = str(GAMES_PER_USER_MONTH)
    m["cost_user_month"] = money(c * GAMES_PER_USER_MONTH, 4)
    m["cost_100"] = money(c * GAMES_PER_USER_MONTH * 100, 2)
    m["cost_10k"] = money(c * GAMES_PER_USER_MONTH * 10_000, 2)
    m["cost_10k_year"] = money(c * GAMES_PER_USER_MONTH * 10_000 * 12, 0)
    if off:
        co = m["_off_cost"]
        m["cost_10k_nocache"] = money(co * GAMES_PER_USER_MONTH * 10_000, 2)
    # Waits
    if on:
        m["wait_first"] = f"{avg([r['waits_s']['first_scenario'] for r in on]):.1f}"
        m["wait_reveal"] = f"{avg([x for r in on for x in r['waits_s']['reveal']]):.1f}"
        m["wait_next"] = f"{avg([x for r in on for x in r['waits_s']['next']]):.1f}"
        m["wait_end"] = f"{avg([r['waits_s']['end_screen'] for r in on]):.1f}"
    return m


def trace_rows():
    gid_file = IMG / "traced_game_id.txt"
    if not gid_file.exists():
        return {"trace_rows": "<tr><td colspan=5>(trace not captured)</td></tr>", "trace_game": "?"}
    gid = gid_file.read_text().strip()
    rows = [t for t in load_jsonl(LOGS / "trace.jsonl") if t["game"] == gid]
    labels = {"scenario_writer": "Scenario Writer", "checker": "Checker", "prompt_writer": "Prompt Writer",
              "reveal_writer": "Reveal Writer"}

    def clip(s, n):
        s = " ".join(str(s).split())
        return html.escape(s if len(s) <= n else s[: n - 1] + "…")

    out = []
    for i, t in enumerate(rows, 1):
        data = t["input_data"]
        m = re.search(r"Target axis: (\w+)", data)
        rnd = re.search(r"Round number: (\d+)", data)
        what = ""
        if t["agent"] == "scenario_writer":
            what = f"round {rnd.group(1) if rnd else '?'}, axis <code>{m.group(1) if m else '?'}</code>"
            if "rejected your last draft" in data:
                what += " (rewrite after Checker feedback)"
        elif t["agent"] == "checker":
            what = f"axis <code>{m.group(1) if m else '?'}</code>"
        elif t["agent"] == "prompt_writer":
            what = "both answers" + (" (same)" if "the same option" in data else " (different)")
        else:
            what = f"{data.count('Round ')} rounds of answers"
        output = t["output"]
        if t["agent"] in ("scenario_writer", "checker", "reveal_writer"):
            try:
                o = json.loads(output)
                if t["agent"] == "scenario_writer":
                    output = f"{o['scenario']} A) {o['option_a']} B) {o['option_b']} [a_side={o['a_side']}]"
                elif t["agent"] == "checker":
                    failed = [k for k in ("pg_safe", "tests_axis", "mapping_correct", "not_obvious") if not o.get(k)]
                    output = "PASS" if not failed else f"REJECT ({', '.join(failed)}): {o.get('reason', '')}"
                else:
                    output = f"A: {o['A']['evidence']} … click/clash cards"
            except (json.JSONDecodeError, KeyError):
                pass
        tok = t["tokens"]
        out.append(f"<tr><td>{i}</td><td><b>{labels[t['agent']]}</b><br><span class=dim>{what}</span></td>"
                   f"<td>{clip(output, 120)}</td><td>{num(tok['input'])} / {num(tok['cached_input'])}</td>"
                   f"<td>{num(tok['output'])}</td></tr>")
    return {"trace_rows": "\n".join(out), "trace_game": gid, "trace_calls": str(len(rows))}


def safety_output():
    p = HERE / "safety_test_output.txt"
    if not p.exists():
        return {"safety_output": "(run the safety tests first)", "safety_held": "?"}
    txt = p.read_text(encoding="utf-8")
    held = re.search(r"(\d+)/(\d+) defenses held", txt)

    def mark(s):
        s = html.escape(s)
        return s.replace("[HELD]", "<span class=held>[HELD]</span>").replace("[FAILED]", "<span class=failed>[FAILED]</span>")

    # Excerpt: each attack category with its tally and one sample result; the full output is linked in the report.
    sections, cur = [], None
    for line in txt.splitlines():
        if re.match(r"^\d+\. ", line):
            cur = {"title": line.strip(), "results": []}
            sections.append(cur)
        elif cur is not None and line.strip().startswith("["):
            cur["results"].append(line.strip())
    lines = []
    for sec in sections:
        n_held = sum(1 for r in sec["results"] if r.startswith("[HELD]"))
        lines.append(f"<b>{html.escape(sec['title'])}</b>  ({n_held} of {len(sec['results'])} held)")
        if sec["results"]:
            sample = sec["results"][0]
            lines.append("   " + mark(sample if len(sample) <= 150 else sample[:149] + "…"))
    if held:
        lines.append(f"\n<b>{html.escape(held.group(0))}</b>")
    return {"safety_output": "\n".join(lines), "safety_held": held.group(0) if held else "?",
            "safety_held_words": f"{held.group(1)} out of {held.group(2)} defenses held" if held else "?"}


def safety_log_excerpt():
    rows = load_jsonl(LOGS / "safety.jsonl")
    picks, seen = [], set()
    for r in rows:
        if r.get("source") == "safety_tests" and r["defense"] not in seen:
            seen.add(r["defense"])
            picks.append(r)
    return {"safety_log": "\n".join(f"<tr><td><code>{html.escape(r['defense'])}</code></td><td>{html.escape(r['caught'])}</td></tr>"
                                    for r in picks)}


def embed_images(s, max_w):
    def repl(m):
        name = m.group(1)
        p = IMG / name
        if not p.exists():
            return m.group(0)
        im = Image.open(p).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
        return 'src="data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode() + '"'
    return re.sub(r'src="img/([\w.-]+\.png)"', repl, s)


def fill(src, values):
    def repl(m):
        k = m.group(1)
        if k not in values:
            raise KeyError(f"missing value for {{{{{k}}}}}")
        return str(values[k])
    return re.sub(r"\{\{(\w+)\}\}", repl, src)


def snapshot_data():
    """logs/ is git-ignored, so keep a copy of the measured runs the report cites inside the repo."""
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    gid = (IMG / "traced_game_id.txt").read_text().strip() if (IMG / "traced_game_id.txt").exists() else None
    (data / "benchmark.jsonl").write_text((LOGS / "benchmark.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    trace = [json.dumps(t, ensure_ascii=False) for t in load_jsonl(LOGS / "trace.jsonl") if t["game"] == gid]
    (data / "trace_traced_game.jsonl").write_text("\n".join(trace) + "\n", encoding="utf-8")


def main():
    snapshot_data()
    values = {}
    values.update(metrics())
    values.update(trace_rows())
    values.update(safety_output())
    values.update(safety_log_excerpt())
    values = {k: v for k, v in values.items() if not k.startswith("_")}
    for name, max_w in (("report", 1500), ("slides", 1700)):
        src = HERE / f"{name}_src.html"
        if not src.exists():
            continue
        out = embed_images(fill(src.read_text(encoding="utf-8"), values), max_w)
        (ROOT / f"{name}.html").write_text(out, encoding="utf-8")
        print(f"wrote {name}.html ({len(out) / 1e6:.1f} MB)")
    print(json.dumps({k: v for k, v in values.items() if len(str(v)) < 40}, indent=1))


if __name__ == "__main__":
    main()
