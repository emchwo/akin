"""LLM agents. Each one has a static system prompt (prompts.py) and gets this call's data in the user message.

Every agent validates the model's output in code and has a hard retry limit, then a safe fallback,
so no agent can loop or crash the game.
"""

import random
import re

import prompts
from game_data import AXES, FALLBACK_SCENARIOS, SETTINGS
from llm import BudgetExceeded, LLMError, call_llm
from safety import log_safety

ALL_SIDES = [s for a in AXES.values() for s in a["sides"]]

SCENARIO_SCHEMA = {
    "type": "object",
    "properties": {
        "scenario": {"type": "string"},
        "option_a": {"type": "string"},
        "option_b": {"type": "string"},
        "a_side": {"type": "string", "enum": ALL_SIDES},
    },
    "required": ["scenario", "option_a", "option_b", "a_side"],
    "additionalProperties": False,
}

WRITER_MAX_RETRIES = 2  # so at most 3 drafts per round


# ---------- Scenario Writer ----------

def _previous_block(previous):
    if not previous:
        return "(none yet)"
    return "\n".join(f"- {s}" for s in previous)


def write_scenario(game, axis, feedback=None):
    """One Scenario Writer call. Returns a draft dict or raises LLMError / BudgetExceeded."""
    sides = AXES[axis]["sides"]
    user = (
        f"Target axis: {axis} (sides: {sides[0]} / {sides[1]})\n"
        f"Setting: {SETTINGS[game.setting]}\n"
        f"Round number: {game.planner.rounds_played + 1}\n"
        f"Scenarios already used in this game (do not repeat):\n{_previous_block(game.previous_scenarios())}\n"
    )
    if feedback:
        user += f"\nThe Checker rejected your last draft. Reason: {feedback}\nWrite a new draft that fixes this.\n"
    draft = call_llm(game.usage, game.id, "scenario_writer", prompts.SCENARIO_WRITER, user, SCENARIO_SCHEMA)
    return validate_draft(draft, axis, game.previous_scenarios())


def validate_draft(draft, axis, previous):
    """Code checks the model can't talk its way past."""
    for k in ("scenario", "option_a", "option_b", "a_side"):
        if not isinstance(draft.get(k), str) or not draft[k].strip():
            raise LLMError(f"draft is missing {k}")
    d = {k: " ".join(draft[k].split()) for k in ("scenario", "option_a", "option_b", "a_side")}
    if d["a_side"] not in AXES[axis]["sides"]:
        raise LLMError(f"a_side '{d['a_side']}' is not a side of {axis}")
    if len(d["scenario"]) > 450 or len(d["option_a"]) > 120 or len(d["option_b"]) > 120:
        raise LLMError("draft is too long")
    if d["option_a"].lower() == d["option_b"].lower():
        raise LLMError("both options are the same")
    if d["scenario"].lower() in {p.lower() for p in previous}:
        raise LLMError("draft repeats an earlier scenario")
    return d


def shuffle_options(s, axis):
    """Models tend to put the same side first, so code flips the display order half the time."""
    if random.random() < 0.5:
        sides = AXES[axis]["sides"]
        s = dict(s)
        s["option_a"], s["option_b"] = s["option_b"], s["option_a"]
        s["a_side"] = sides[1] if s["a_side"] == sides[0] else sides[0]
    return s


def fallback_scenario(game, axis):
    used = {s.lower() for s in game.previous_scenarios()}
    pool = [s for s in FALLBACK_SCENARIOS[axis] if s["scenario"].lower() not in used]
    pick = dict(random.choice(pool or FALLBACK_SCENARIOS[axis]))
    pick["source"] = "fallback"
    return pick


# ---------- Checker ----------

CHECK_FIELDS = ("pg_safe", "tests_axis", "mapping_correct", "not_obvious")
CHECKER_SCHEMA = {
    "type": "object",
    "properties": {**{k: {"type": "boolean"} for k in CHECK_FIELDS}, "reason": {"type": "string"}},
    "required": [*CHECK_FIELDS, "reason"],
    "additionalProperties": False,
}

# Cheap code-level screen that runs before the Checker. The Checker is the real PG judge; this just
# catches the obvious cases for free.
BLOCKED_TERMS = re.compile(
    r"\b(sex\w*|nude|naked|drunk|hammered|tipsy|booze|weed|cocaine|drugs?|murder\w*|blood\w*|guns?|knife|"
    r"suicide|cheat(?:ed|ing)\s+on|affair|hook(?:ing)?\s*up|bedroom|strip\s*club|religio\w*|politic\w*|"
    r"church|election)\b",
    re.IGNORECASE)


def content_screen(draft):
    text = " ".join(draft[k] for k in ("scenario", "option_a", "option_b"))
    hits = sorted({m.group(0).lower() for m in BLOCKED_TERMS.finditer(text)})
    return hits


def check_scenario(game, axis, draft):
    """Checker call. Returns (passed, failed_checks, reason). A Checker failure counts as a rejection (fail closed)."""
    sides = AXES[axis]["sides"]
    other = sides[1] if draft["a_side"] == sides[0] else sides[0]
    user = (
        f"Target axis: {axis} (sides: {sides[0]} / {sides[1]})\n"
        f"Setting: {SETTINGS[game.setting]}\n"
        f"Draft scenario: {draft['scenario']}\n"
        f"option_a (should lean {draft['a_side']}): {draft['option_a']}\n"
        f"option_b (should lean {other}): {draft['option_b']}\n"
    )
    try:
        verdict = call_llm(game.usage, game.id, "checker", prompts.CHECKER, user, CHECKER_SCHEMA)
    except LLMError as e:
        return False, ["checker_error"], f"Checker could not review the draft ({e}). Write a simpler, clearly PG draft."
    failed = [k for k in CHECK_FIELDS if verdict.get(k) is not True]
    reason = " ".join(str(verdict.get("reason", "")).split())[:300] or "no reason given"
    return not failed, failed, reason


def next_scenario(game, axis):
    """Scenario Writer -> code screen -> Checker, with at most 2 rewrites, then a safe built-in fallback."""
    feedback = None
    for attempt in range(WRITER_MAX_RETRIES + 1):
        try:
            draft = write_scenario(game, axis, feedback)
        except BudgetExceeded:
            raise
        except LLMError as e:
            feedback = f"Your draft could not be used: {e}."
            continue

        hits = content_screen(draft)
        if hits:
            game.checker_rejections += 1
            feedback = f"Not PG for a first date: it mentions {', '.join(hits)}. Remove that topic entirely."
            log_safety("content_filter", "Code screen blocked a draft scenario before the Checker.",
                       game.id, axis=axis, attempt=attempt + 1, terms=hits, draft=draft["scenario"])
            continue

        passed, failed, reason = check_scenario(game, axis, draft)
        if passed:
            draft["source"] = "writer"
            draft["attempts"] = attempt + 1
            return draft
        game.checker_rejections += 1
        feedback = reason
        log_safety("checker", f"Checker rejected a draft scenario (failed: {', '.join(failed)}).",
                   game.id, axis=axis, attempt=attempt + 1, failed=failed, reason=reason,
                   draft=draft["scenario"], option_a=draft["option_a"], option_b=draft["option_b"])
    log_safety("retry_limit", f"Scenario Writer failed {WRITER_MAX_RETRIES + 1} times; used a built-in "
               "fallback scenario.", game.id, axis=axis, last_error=feedback)
    game.fallbacks += 1
    fb = fallback_scenario(game, axis)
    fb["attempts"] = WRITER_MAX_RETRIES + 1
    return fb


# ---------- Prompt Writer ----------

PROMPT_MAX_RETRIES = 1
BANNED_WORDS = re.compile(r"\b(match\w*|split\w*)\b", re.IGNORECASE)


def fallback_prompt(rnd):
    if rnd["picks"]["A"] == rnd["picks"]["B"]:
        said = rnd["option_" + rnd["picks"]["A"]].rstrip(".!?")
        return f"You both said “{said}.” What's a time in your life when that instinct paid off?"
    return "One of you went each way on this one. Take turns making your case, then say what would change your mind."


def validate_prompt(text):
    text = " ".join(text.strip().strip('"“”').split())
    if len(text) < 15 or len(text) > 320:
        raise LLMError("prompt length out of range")
    if BANNED_WORDS.search(text):
        raise LLMError("prompt used a banned word (match/split)")
    if any(ch in text for ch in "{}<>"):
        raise LLMError("prompt contains markup characters")
    return text


def write_prompt(game, rnd):
    """Prompt Writer: one conversation prompt for this scenario and these two answers."""
    pa, pb = rnd["picks"]["A"], rnd["picks"]["B"]
    user = (
        f"Setting: {SETTINGS[game.setting]}\n"
        f"Scenario: {rnd['scenario']}\n"
        f"Option A: {rnd['option_a']}\n"
        f"Option B: {rnd['option_b']}\n"
        f"Player A chose option {pa.upper()}: {rnd['option_' + pa]}\n"
        f"Player B chose option {pb.upper()}: {rnd['option_' + pb]}\n"
        f"They chose {'the same option' if pa == pb else 'different options'}.\n"
    )
    # Earlier prompts (model-written text, so never any player names) let the writer vary its style.
    used = [r["prompt"] for r in game.rounds if r.get("prompt")]
    if used:
        user += "Prompts already used in this game (vary from these):\n" + "\n".join(f"- {p}" for p in used) + "\n"
    for attempt in range(PROMPT_MAX_RETRIES + 1):
        try:
            text = call_llm(game.usage, game.id, "prompt_writer", prompts.PROMPT_WRITER, user)
            return validate_prompt(text), "writer"
        except BudgetExceeded:
            raise
        except LLMError as e:
            last = str(e)
    log_safety("retry_limit", f"Prompt Writer failed {PROMPT_MAX_RETRIES + 1} times; used a built-in prompt.",
               game.id, last_error=last)
    return fallback_prompt(rnd), "fallback"


# ---------- Reveal Writer ----------

REVEAL_MAX_RETRIES = 1
_cited = {"type": "object", "properties": {"text": {"type": "string"},
                                           "rounds": {"type": "array", "items": {"type": "integer"}}},
          "required": ["text", "rounds"], "additionalProperties": False}
_evidence = {"type": "object", "properties": {"evidence": {"type": "string"},
                                              "rounds": {"type": "array", "items": {"type": "integer"}}},
             "required": ["evidence", "rounds"], "additionalProperties": False}
REVEAL_SCHEMA = {
    "type": "object",
    "properties": {"A": _evidence, "B": _evidence, "click": _cited, "clash": _cited},
    "required": ["A", "B", "click", "clash"],
    "additionalProperties": False,
}
ROUND_MENTION = re.compile(r"\brounds?\s+(\d+)(?:\s*(?:,|and|&|-|to)\s*(\d+))*", re.IGNORECASE)


def _mentioned_rounds(text):
    """Every number written as 'round 3', 'rounds 2 and 5', 'rounds 1, 4', etc. in the text."""
    nums = set()
    for m in ROUND_MENTION.finditer(text):
        nums.update(int(n) for n in re.findall(r"\d+", m.group(0)))
    return nums


def reveal_input(game):
    lines = [f"Setting: {SETTINGS[game.setting]}", "", "Rounds played:"]
    for r in game.rounds:
        sides = AXES[r["axis"]]["sides"]
        b_side = sides[1] if r["a_side"] == sides[0] else sides[0]
        lines += [
            f"Round {r['n']} (axis {r['axis']}): {r['scenario']}",
            f"  Option A ({r['a_side']}): {r['option_a']}",
            f"  Option B ({b_side}): {r['option_b']}",
            f"  Player A chose option {r['picks']['A'].upper()} ({r['sides']['A']}).",
            f"  Player B chose option {r['picks']['B'].upper()} ({r['sides']['B']}).",
        ]
    lines += ["", "Final types (already decided; do not change them):"]
    for p in ("A", "B"):
        res = game.planner.result(p)
        lean = ", ".join(f"{a}: {res['sides'][a]}" for a in res["sides"])
        lines.append(f"  Player {p}: {res['name']} ({lean})")
        # How strongly they leaned, so the writer can spot an axis where the player wavered.
        for a, side in res["sides"].items():
            hist = game.planner.history[p][a]
            n_side = sum(1 for s in hist if s == side)
            label = "strong" if n_side - (len(hist) - n_side) >= 2 else ("slight" if n_side > len(hist) - n_side else "even, tie broken by latest answer")
            lines.append(f"    {a}: {n_side} of {len(hist)} answers leaned {side} ({label})")
    lines.append(f"\nValid round numbers you may cite: {', '.join(str(r['n']) for r in game.rounds)}")
    return "\n".join(lines)


def validate_reveal(data, valid_rounds):
    """Each piece of evidence must cite rounds that exist, and any round named in the text must exist."""
    problems = []
    out = {}
    for key, text_field in (("A", "evidence"), ("B", "evidence"), ("click", "text"), ("clash", "text")):
        item = data.get(key) or {}
        text = " ".join(str(item.get(text_field, "")).split())
        rounds = item.get("rounds") or []
        if not (30 <= len(text) <= 700):
            problems.append(f"{key}: text length {len(text)} is out of range")
        if not rounds:
            problems.append(f"{key}: cites no rounds")
        bad = sorted({r for r in rounds if r not in valid_rounds} | (_mentioned_rounds(text) - valid_rounds))
        if bad:
            problems.append(f"{key}: cites round(s) {bad} that were not played")
        if BANNED_WORDS.search(text):
            problems.append(f"{key}: uses a banned word (match/split)")
        if any(ch in text for ch in "{}<>"):
            problems.append(f"{key}: contains markup characters")
        out[key] = {"text": text, "rounds": sorted(set(rounds))}
    return out, problems


def fallback_reveal(game):
    """Plain-code reveal built only from the real answers. Used if the Reveal Writer can't pass validation."""
    w = "round"
    out = {}
    for p in ("A", "B"):
        res = game.planner.result(p)
        bits, cited = [], []
        for axis, side in res["sides"].items():
            hits = [r for r in game.rounds if r["axis"] == axis and r["sides"][p] == side]
            if hits:
                r = hits[0]
                cited.append(r["n"])
                bits.append(f"in {w} {r['n']} you chose “{r['option_' + r['picks'][p]].rstrip('.!?')}”")
        text = ("Your answers point this way: " + "; ".join(bits) + ".") if bits else "Your answers point this way."
        out[p] = {"text": text[0].upper() + text[1:], "rounds": sorted(cited)}
    same = [r for r in game.rounds if r["picks"]["A"] == r["picks"]["B"]]
    diff = [r for r in game.rounds if r["picks"]["A"] != r["picks"]["B"]]
    first = game.rounds[0]
    c = same[0] if same else first
    out["click"] = {"text": f"In {w} {c['n']} you were on the same page, which is a good place to start a longer conversation."
                    if same else f"You both took {w} {c['n']} seriously, and that care is a good place to start.",
                    "rounds": [c["n"]]}
    d = diff[0] if diff else first
    out["clash"] = {"text": f"{w.capitalize()} {d['n']} pulled you in different directions. Expect friendly debates about that one."
                    if diff else f"You agreed so often that the first real disagreement might surprise you. Start with {w} {d['n']}.",
                    "rounds": [d["n"]]}
    return out


def write_reveal(game):
    """Reveal Writer with code validation, one retry, then the plain-code fallback. Returns (reveal, source)."""
    valid = {r["n"] for r in game.rounds}
    base = reveal_input(game)
    user = base
    for attempt in range(REVEAL_MAX_RETRIES + 1):
        try:
            data = call_llm(game.usage, game.id, "reveal_writer", prompts.REVEAL_WRITER, user, REVEAL_SCHEMA)
        except BudgetExceeded:
            break
        except LLMError as e:
            problems = [str(e)]
        else:
            out, problems = validate_reveal(data, valid)
            if not problems:
                return out, "writer"
            log_safety("evidence_check", "Reveal Writer output failed validation: " + "; ".join(problems),
                       game.id, attempt=attempt + 1)
        user = base + "\n\nYour previous answer was rejected: " + "; ".join(problems) + ". Fix it."
    log_safety("retry_limit", "Reveal Writer could not produce valid evidence; used plain-code evidence "
               "built from the real answers.", game.id)
    return fallback_reveal(game), "fallback"
