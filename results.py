"""Summaries for the /results page: player feedback by setting, plus measured length and cost per game."""

import json

from planner import MIN_ROUNDS
from safety import LOG_DIR

FEEDBACK_LOG = LOG_DIR / "feedback.jsonl"
GAMES_LOG = LOG_DIR / "games.jsonl"
FEELS = ("yes", "kind_of", "no")
LEARNED = ("yes", "a_little", "no")
SETTING_LABELS = (("first_date", "First date"), ("new_friends", "Friends"))


def _read(path):
    """Real sessions only: entries written by tests/safety_tests.py or tools/benchmark.py are skipped."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not rec.get("source"):
            out.append(rec)
    return out


def _pct(n, d):
    return round(100 * n / d) if d else None


def _avg(xs, digits=1):
    return round(sum(xs) / len(xs), digits) if xs else None


def summarize():
    feedback = _read(FEEDBACK_LOG)
    games = _read(GAMES_LOG)
    groups = []
    for key, label in SETTING_LABELS:
        fb = [f for f in feedback if f.get("setting") == key]
        feels = [v for f in fb for v in f.get("feels_right", [])]
        learned = [f["learned_something"] for f in fb if f.get("learned_something") in LEARNED]
        gm = [g for g in games if g.get("setting") == key and g.get("rounds")]
        groups.append({
            "key": key,
            "label": label,
            "sessions": len(fb),
            "player_answers": len(feels),
            "feels": {v: _pct(sum(1 for x in feels if x == v), len(feels)) for v in FEELS},
            "learned": {v: _pct(sum(1 for x in learned if x == v), len(learned)) for v in LEARNED},
            "learned_n": len(learned),
            "again_pct": _pct(sum(1 for f in fb if f.get("play_again") == "yes"), len(fb)),
            "games_logged": len(gm),
            "avg_rounds": _avg([g["rounds"] for g in gm]),
            "ended_at_min_pct": _pct(sum(1 for g in gm if g["rounds"] == MIN_ROUNDS), len(gm)),
            "avg_minutes": _avg([g["seconds"] / 60 for g in gm if g.get("seconds")]),
            "avg_cost": _avg([g["total"]["cost_usd"] for g in gm if g.get("total")], 5),
            "avg_calls": _avg([g.get("llm_calls", 0) for g in gm]),
            "checker_rejections": sum(g.get("checker_rejections", 0) for g in gm),
        })
    return {"groups": groups, "total_sessions": len(feedback), "min_rounds": MIN_ROUNDS}
