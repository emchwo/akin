"""Measure real token use and cost: play full games against the real model with scripted players.

    python tools/benchmark.py                 # 6 games, prompt caching on (the normal app)
    python tools/benchmark.py --games 3 --no-cache   # same, with caching defeated, to measure savings

Each game goes through the real Flask routes, so every agent, the Checker loop, prefetching, and the
reveal run exactly as in the app. Players answer at random. Half the games end at round 6 and half keep
going to round 8. Results go to logs/benchmark.jsonl (one line per game) and a summary prints at the end.
Benchmark games are tagged "source": "benchmark", so /results ignores them.
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--games", type=int, default=6)
parser.add_argument("--no-cache", action="store_true")
parser.add_argument("--seed", type=int, default=409)
args = parser.parse_args()
if args.no_cache:
    os.environ["AKIN_NO_CACHE"] = "1"  # read by llm.py at import

import game as games  # noqa: E402
import safety  # noqa: E402
from app import app  # noqa: E402
from llm import Usage  # noqa: E402
from planner import MAX_ROUNDS, MIN_ROUNDS  # noqa: E402

safety.LOG_TAG = "benchmark"
BENCH_LOG = safety.LOG_DIR / "benchmark.jsonl"
SETTINGS = ["first_date", "new_friends"]


def play(i, rng):
    c = app.test_client()
    setting = SETTINGS[i % 2]
    stop_at = MIN_ROUNDS if i % 2 == 0 else MAX_ROUNDS
    t = time.time()
    r = c.post("/api/start", json={"name_a": "Ann", "name_b": "Bea", "setting": setting}).get_json()
    waits = {"first_scenario": round(time.time() - t, 2), "reveal": [], "next": []}
    gid = r["game_id"]
    while True:
        t = time.time()
        a = c.post("/api/answers", json={"game_id": gid, "a": rng.choice("ab"), "b": rng.choice("ab")}).get_json()
        waits["reveal"].append(round(time.time() - t, 2))
        if a.get("done") or (a.get("can_end") and a["round"] >= stop_at):
            break
        time.sleep(8)  # players reading and talking: lets the background prefetch work, as in real play
        t = time.time()
        n = c.post("/api/next", json={"game_id": gid}).get_json()
        waits["next"].append(round(time.time() - t, 2))
        if n.get("ended"):
            break
    t = time.time()
    fin = c.post("/api/finish", json={"game_id": gid}).get_json()
    waits["end_screen"] = round(time.time() - t, 2)
    g = games.get_game(gid)
    s = g.usage.summary()
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "cache": not args.no_cache, "setting": setting,
        "rounds": len(g.rounds), "checker_rejections": g.checker_rejections, "fallbacks": g.fallbacks,
        "reveal_source": fin.get("reveal_source"), "llm_calls": s["llm_calls"],
        "by_agent": {k: {f: v[f] for f in ("calls", "input", "cached_input", "cache_write", "output", "cost_usd")}
                     for k, v in s["agents"].items()},
        "total": {f: s["total"][f] for f in ("input", "cached_input", "cache_write", "output", "reasoning", "cost_usd")},
        "waits_s": waits,
    }
    safety.append_jsonl(BENCH_LOG, rec)
    return rec


def main():
    rng = random.Random(args.seed)
    recs = []
    for i in range(args.games):
        rec = play(i, rng)
        recs.append(rec)
        t = rec["total"]
        print(f"game {i + 1}: {rec['setting']:<11} {rec['rounds']} rounds, {rec['llm_calls']} calls, "
              f"in {t['input']} (cached {t['cached_input']}), out {t['output']}, ${t['cost_usd']:.5f}, "
              f"{rec['checker_rejections']} rejections", flush=True)
    agg = Usage._blank()
    for r in recs:
        for k in agg:
            agg[k] += r["total"].get(k, 0)
    n = len(recs)
    print(f"\n{'cache OFF' if args.no_cache else 'cache ON'}: {n} games, avg {sum(r['rounds'] for r in recs) / n:.1f} rounds, "
          f"avg {sum(r['llm_calls'] for r in recs) / n:.1f} calls")
    print(f"avg per game: input {agg['input'] / n:.0f}, cached {agg['cached_input'] / n:.0f} "
          f"({100 * agg['cached_input'] / max(agg['input'], 1):.0f}%), output {agg['output'] / n:.0f}, "
          f"cost ${sum(r['total']['cost_usd'] for r in recs) / n:.5f}")


if __name__ == "__main__":
    main()
