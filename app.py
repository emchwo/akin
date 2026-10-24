"""Akin: Flask backend. Run with `python app.py`, then open http://127.0.0.1:5050"""

import os
import re
import time

from flask import Flask, jsonify, render_template, request

import agents
import game as games
import llm
import results
import safety
from game_data import APP_NAME, AXES, AXIS_ORDER, SETTINGS
from llm import BudgetExceeded, ENV_LOADED_FROM, MAX_CALLS_PER_SERVER, MODEL, PRICES, server_calls
from planner import MAX_ROUNDS, MIN_ROUNDS
from results import FEEDBACK_LOG, FEELS, LEARNED
from safety import append_jsonl, clean_name

app = Flask(__name__)
app.json.sort_keys = False

BUDGET_MESSAGE = ("We've reached this game's AI limit, so we're wrapping up here. "
                  "Thanks for playing! You can still see what we learned so far.")


@app.get("/")
def index():
    return render_template("index.html", app_name=APP_NAME, model=MODEL)


@app.get("/icons")
def icons():
    return render_template("icons.html", app_name=APP_NAME)


def _game_or_404():
    data = request.get_json(silent=True) or {}
    g = games.get_game(data.get("game_id"))
    if g is None:
        return None, data, (jsonify({"error": "This game has expired. Please start a new one."}), 404)
    return g, data, None


def _budget_end(g):
    g.ended = "budget"
    if not g.rounds:
        games.log_game(g)  # nothing to finish, so log it now
    return jsonify({"ended": "budget", "message": BUDGET_MESSAGE, "rounds_played": len(g.rounds)})


@app.post("/api/start")
def start():
    data = request.get_json(silent=True) or {}
    name_a, err_a = clean_name(data.get("name_a"))
    name_b, err_b = clean_name(data.get("name_b"))
    if err_a or err_b:
        return jsonify({"errors": {"a": err_a, "b": err_b}}), 400
    setting = data.get("setting") if data.get("setting") in SETTINGS else "first_date"

    g = games.create_game({"A": name_a, "B": name_b}, setting)
    out = {"game_id": g.id, "names": g.names, "min_rounds": MIN_ROUNDS, "max_rounds": MAX_ROUNDS}
    try:
        g.current = g.make_scenario()
    except BudgetExceeded:
        return _budget_end(g)
    out["scenario"] = g.public_scenario()
    return jsonify(out)


@app.post("/api/answers")
def answers():
    g, data, err = _game_or_404()
    if err:
        return err
    picks = {"A": data.get("a"), "B": data.get("b")}
    if any(v not in ("a", "b") for v in picks.values()):
        return jsonify({"error": "Each player must pick option a or b."}), 400
    with g.lock:
        c = g.current
        if c is None:
            return jsonify({"error": "No scenario is waiting for answers."}), 409
        rnd = g.record_round(c, picks)
        g.current = None
        done, can_end, settled = g.planner.is_done(), g.planner.can_end(), g.planner.all_locked()
        g.prefetch_next()  # next scenario (and, from round 6, the reveal) are written in the background

    try:
        rnd["prompt"], rnd["prompt_source"] = agents.write_prompt(g, rnd)
    except BudgetExceeded:
        rnd["prompt"], rnd["prompt_source"] = agents.fallback_prompt(rnd), "fallback"
        g.ended = "budget"

    return jsonify({
        "round": rnd["n"],
        "answers": {p: rnd["option_" + v] for p, v in picks.items()},
        "prompt": with_names(rnd["prompt"], g.names),
        "color": AXES[rnd["axis"]]["color"],  # fills this round's progress segment (unlabeled until the end)
        "done": done,          # round 8: the game is over
        "can_end": can_end,    # round 6+: players may end now or keep going
        "settled": settled,    # every axis leans 2+ answers one way for both players
    })


@app.post("/api/undo")
def undo():
    """A player misclicked: take back the last round's answers and replay that scenario."""
    g, _, err = _game_or_404()
    if err:
        return err
    with g.lock:
        if not g.rounds or g.current is not None or g.ended:
            return jsonify({"error": "There's nothing to undo right now."}), 409
        g.undo_last()
    return jsonify({"scenario": g.public_scenario(), "rounds_played": len(g.rounds)})


@app.post("/api/next")
def next_round():
    g, _, err = _game_or_404()
    if err:
        return err
    if g.planner.is_done():
        return jsonify({"done": True})
    if g.ended == "budget":
        return _budget_end(g)
    if g.current is None:
        try:
            fut = g.next_future
            g.current = fut.result() if fut else g.make_scenario()
            g.next_future = None
        except BudgetExceeded:
            return _budget_end(g)
    return jsonify({"scenario": g.public_scenario()})


@app.post("/api/finish")
def finish():
    g, _, err = _game_or_404()
    if err:
        return err
    if not g.rounds:
        return jsonify({"error": "No rounds were played."}), 409
    return end_payload(g)


@app.post("/api/feedback")
def feedback():
    g, data, err = _game_or_404()
    if err:
        return err
    feels = data.get("feels_right") or {}
    learned, again = data.get("learned"), data.get("play_again")
    if any(feels.get(p) not in FEELS for p in ("A", "B")) or learned not in LEARNED or again not in ("yes", "no"):
        return jsonify({"error": "Please answer every question."}), 400
    if g.feedback_saved:
        return jsonify({"ok": True, "duplicate": True})
    g.feedback_saved = True
    types = {p: g.planner.result(p)["key"] for p in ("A", "B")}
    record = {"source": safety.LOG_TAG} if safety.LOG_TAG else {}
    append_jsonl(FEEDBACK_LOG, {
        **record,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game": g.id[:8],
        "setting": g.setting,
        "rounds": len(g.rounds),
        "feels_right": [feels["A"], feels["B"]],   # by seat (first / second player), never by name
        "types": [types["A"], types["B"]],
        "learned_something": learned,
        "play_again": again,
    })
    return jsonify({"ok": True})


@app.get("/results")
def results_page():
    return render_template("results.html", app_name=APP_NAME, r=results.summarize())


def end_payload(g):
    g.ended = g.ended or "done"
    reveal, source = g.start_reveal().result()
    games.log_game(g, source)
    players = {}
    for p in ("A", "B"):
        players[p] = {**g.planner.result(p), "evidence": reveal[p]["text"], "evidence_rounds": reveal[p]["rounds"]}
    return jsonify({
        "names": g.names,
        "players": players,
        "click": {"text": with_names(reveal["click"]["text"], g.names), "rounds": reveal["click"]["rounds"]},
        "clash": {"text": with_names(reveal["clash"]["text"], g.names), "rounds": reveal["clash"]["rounds"]},
        "reveal_source": source,
        "rounds_played": len(g.rounds),
        "axes": {a: {"label": AXES[a]["label"], "sides": AXES[a]["side_labels"],
                     "color": AXES[a]["color"]} for a in AXIS_ORDER},
    })


@app.get("/api/stats")
def stats():
    g = games.get_game(request.args.get("game_id"))
    out = {"model": MODEL, "prices_per_1m": PRICES,
           "server_calls": server_calls(), "server_max_calls": MAX_CALLS_PER_SERVER}
    if g is not None:
        out.update(g.usage.summary())
        out.update({"rounds_played": len(g.rounds), "checker_rejections": g.checker_rejections,
                    "scenario_fallbacks": g.fallbacks})
    return jsonify(out)


def with_names(text, names):
    """The model only ever writes "Player A" / "Player B". Real names are swapped in here, after the model."""
    return re.sub(r"\bPlayer ([AB])\b", lambda m: names[m.group(1)], text)


if __name__ == "__main__":
    if not os.getenv("PORTKEY_API_KEY", "").strip():
        raise SystemExit("PORTKEY_API_KEY was not found. Create a .env file with PORTKEY_API_KEY=... in\n"
                         f"  {llm.ENV_FILES[0]}\nor one level up in\n  {llm.ENV_FILES[1]}\n(see .env.example).")
    port = int(os.getenv("PORT", "5050"))
    # Tag this server's log lines (e.g. AKIN_LOG_TAG=screenshots) so /results ignores non-real sessions.
    safety.LOG_TAG = os.getenv("AKIN_LOG_TAG") or None
    print(f"{APP_NAME} running at http://127.0.0.1:{port}  (model: {MODEL}; .env: {ENV_LOADED_FROM or 'not found'})")
    # No debug mode and no reloader: one process, so an edit never spawns a second server.
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
