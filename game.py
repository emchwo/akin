"""Game state, kept in server memory only. Player names live here and nowhere else."""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import agents
from game_data import AXES
from llm import Usage
from planner import Planner
import safety
from safety import LOG_DIR, append_jsonl

GAMES_LOG = LOG_DIR / "games.jsonl"
ROUND_ONLY_KEYS = {"n", "picks", "sides", "prompt", "prompt_source"}  # added when a round is answered

EXECUTOR = ThreadPoolExecutor(max_workers=8)
GAME_TTL_S = 6 * 3600

_games = {}
_games_lock = threading.Lock()


class Game:
    def __init__(self, names, setting):
        self.id = uuid.uuid4().hex
        self.names = names            # {"A": "...", "B": "..."} - shown to players only, never sent to the model
        self.setting = setting        # "first_date" | "new_friends"
        self.planner = Planner()
        self.usage = Usage()
        self.rounds = []              # finished rounds
        self.current = None           # the scenario on screen now
        self.next_future = None       # next scenario being written in the background
        self.reveal_future = None     # final reveal being written in the background (from round 6 on)
        self.checker_rejections = 0
        self.fallbacks = 0
        self.ended = None             # None | "done" | "budget"
        self.logged = False
        self.feedback_saved = False
        self.created = time.time()
        self.lock = threading.Lock()

    def previous_scenarios(self):
        out = [r["scenario"] for r in self.rounds]
        if self.current:
            out.append(self.current["scenario"])
        return out

    def make_scenario(self):
        axis = self.planner.next_axis()
        s = agents.shuffle_options(agents.next_scenario(self, axis), axis)
        s["axis"] = axis
        return s

    def prefetch_next(self):
        """While the players talk about this round, write what could come next in the background.
        From round 6 on they can end or keep going, so both the next scenario and the reveal are
        prepared, and whichever they pick shows up without a wait."""
        if not self.planner.is_done():
            self.next_future = EXECUTOR.submit(self.make_scenario)
        if self.planner.can_end():
            self.start_reveal()

    def start_reveal(self):
        if self.reveal_future is None:
            self.reveal_future = EXECUTOR.submit(agents.write_reveal, self)
        return self.reveal_future

    def record_round(self, c, picks):
        """Score one answered scenario. picks: {"A": "a"|"b", "B": "a"|"b"}. Returns the stored round."""
        sides = AXES[c["axis"]]["sides"]
        b_side = sides[1] if c["a_side"] == sides[0] else sides[0]
        side_of = {"a": c["a_side"], "b": b_side}
        chosen = {p: side_of[v] for p, v in picks.items()}
        self.planner.record(c["axis"], chosen)
        rnd = {**c, "n": self.planner.rounds_played, "picks": picks, "sides": chosen}
        self.rounds.append(rnd)
        self.next_future = self.reveal_future = None  # anything prepared earlier is now out of date
        return rnd

    def undo_last(self):
        """Take back the last round's answers (a misclick) and put its scenario back on screen.
        Only allowed before the next scenario has been shown. Work prepared in the background for the
        old answers is dropped."""
        rnd = self.rounds.pop()
        self.planner.undo(rnd["axis"], rnd["sides"])
        self.current = {k: v for k, v in rnd.items() if k not in ROUND_ONLY_KEYS}
        self.next_future = self.reveal_future = None
        return rnd

    def public_scenario(self):
        c = self.current
        return {"round": self.planner.rounds_played + 1, "scenario": c["scenario"],
                "options": {"a": c["option_a"], "b": c["option_b"]}}


def log_game(g, reveal_source=None):
    """One line per game in logs/games.jsonl. No player names, ever."""
    if g.logged:
        return
    g.logged = True
    s = g.usage.summary()
    agents_tokens = {a: {k: r[k] for k in ("calls", "input", "cached_input", "cache_write", "output", "cost_usd")}
                     for a, r in s["agents"].items()}
    record = {"source": safety.LOG_TAG} if safety.LOG_TAG else {}
    append_jsonl(GAMES_LOG, {
        **record,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": g.id[:8],
        "setting": g.setting,
        "rounds": len(g.rounds),
        "ended": g.ended or "done",
        "end_reason": "budget" if g.ended == "budget" else g.planner.end_reason(),
        "all_axes_locked": g.planner.all_locked(),
        "checker_rejections": g.checker_rejections,
        "scenario_fallbacks": g.fallbacks,
        "reveal_source": reveal_source,
        "llm_calls": s["llm_calls"],
        "tokens_by_agent": agents_tokens,
        "total": {k: s["total"][k] for k in ("input", "cached_input", "cache_write", "output", "cost_usd")},
        "seconds": round(time.time() - g.created),
    })


def create_game(names, setting):
    g = Game(names, setting)
    now = time.time()
    with _games_lock:
        for gid in [k for k, v in _games.items() if now - v.created > GAME_TTL_S]:
            del _games[gid]
        _games[g.id] = g
    return g


def get_game(game_id):
    with _games_lock:
        return _games.get(game_id or "")
