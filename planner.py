"""Planner agent (plain code, no LLM).

Keeps a running score for each player on each axis and decides what to test next and when to stop.
Score convention: +1 for an answer on the axis's first side ("one", "found", "home"), -1 for the second.

Rounds 1-6 ask every axis exactly twice, so the game can decide all three axes by round 6. After round 6
the players choose to end or keep going; rounds 7-8 go to whichever axis is still least certain.
"""

from game_data import AXES, AXIS_ORDER, SIDE_SUMMARY, TYPES

MIN_ROUNDS = 6
MAX_ROUNDS = 8
LOCK_MARGIN = 2  # an axis is "locked" for a player once they lean one way by 2+ answers
CORE_ASKS = MIN_ROUNDS // len(AXIS_ORDER)  # 2 asks per axis in rounds 1-6


class Planner:
    def __init__(self, players=("A", "B")):
        self.players = list(players)
        self.scores = {p: {a: 0 for a in AXIS_ORDER} for p in self.players}
        self.history = {p: {a: [] for a in AXIS_ORDER} for p in self.players}  # sides in answer order
        self.asked = {a: 0 for a in AXIS_ORDER}
        self.rounds_played = 0

    def record(self, axis, answers):
        """answers: {"A": side, "B": side}"""
        first_side = AXES[axis]["sides"][0]
        for p, side in answers.items():
            self.scores[p][axis] += 1 if side == first_side else -1
            self.history[p][axis].append(side)
        self.asked[axis] += 1
        self.rounds_played += 1

    def undo(self, axis, answers):
        """Reverse record() for the most recent round (used when a player misclicked)."""
        first_side = AXES[axis]["sides"][0]
        for p, side in answers.items():
            self.scores[p][axis] -= 1 if side == first_side else -1
            self.history[p][axis].pop()
        self.asked[axis] -= 1
        self.rounds_played -= 1

    def locked(self, player, axis):
        return abs(self.scores[player][axis]) >= LOCK_MARGIN

    def all_locked(self):
        return all(self.locked(p, a) for p in self.players for a in AXIS_ORDER)

    def certainty(self, axis):
        """0 (no idea about either player) to 4 (both players locked)."""
        return sum(min(abs(self.scores[p][axis]), LOCK_MARGIN) for p in self.players)

    def next_axis(self):
        """Least certainty across both players; ties go to the axis asked least, then the fixed order.
        Until round 6, only axes that haven't had their 2 asks yet are eligible."""
        pool = [a for a in AXIS_ORDER if self.asked[a] < CORE_ASKS] or AXIS_ORDER
        return min(pool, key=lambda a: (self.certainty(a), self.asked[a], AXIS_ORDER.index(a)))

    def can_end(self):
        """From round 6 on, the players may end the game."""
        return self.rounds_played >= MIN_ROUNDS

    def is_done(self):
        """The hard cap: no more rounds after round 8."""
        return self.rounds_played >= MAX_ROUNDS

    def end_reason(self):
        return "max_rounds" if self.is_done() else "players_ended"

    def side_for(self, player, axis):
        """Final side on one axis. A tied score falls back to the player's most recent answer."""
        score = self.scores[player][axis]
        sides = AXES[axis]["sides"]
        if score > 0:
            return sides[0]
        if score < 0:
            return sides[1]
        hist = self.history[player][axis]
        return hist[-1] if hist else sides[0]

    def lean(self, player, axis):
        """-1.0 .. 1.0 position for the end-screen meter (+1 = fully first side)."""
        n = len(self.history[player][axis])
        return self.scores[player][axis] / n if n else 0.0

    def result(self, player):
        key = tuple(self.side_for(player, a) for a in AXIS_ORDER)
        t = TYPES[key]
        return {
            **t,
            "sides": dict(zip(AXIS_ORDER, key)),
            "summary": [{"axis": a, "label": AXES[a]["side_labels"][s], "text": SIDE_SUMMARY[s],
                         # a tie on an axis they did answer, decided by their latest answer
                         "close": self.scores[player][a] == 0 and bool(self.history[player][a])}
                        for a, s in zip(AXIS_ORDER, key)],
            "meters": {a: round(self.lean(player, a), 3) for a in AXIS_ORDER},
            "answers_per_axis": {a: len(self.history[player][a]) for a in AXIS_ORDER},
        }

    def snapshot(self):
        return {
            "rounds_played": self.rounds_played,
            "scores": self.scores,
            "asked": self.asked,
            "locked": {p: {a: self.locked(p, a) for a in AXIS_ORDER} for p in self.players},
        }
