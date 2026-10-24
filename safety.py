"""Safety helpers: the safety log and the player-name filter.

Every defense that triggers writes one line to logs/safety.jsonl. Player names are never written there;
for a rejected name we log only why it was rejected and its length.
"""

import json
import re
import threading
import time
import unicodedata
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
SAFETY_LOG = LOG_DIR / "safety.jsonl"
_lock = threading.Lock()

MAX_NAME_LEN = 20
MAX_NAME_WORDS = 2
CODE_CHARS = re.compile(r"[<>{}\[\]`$\\|;=#@*/%^~]")

# Words that read like instructions to a model rather than a first name.
INJECTION_WORDS = {
    "ignore", "instruction", "instructions", "prompt", "system", "assistant", "developer", "override",
    "disregard", "jailbreak", "previous", "above", "forget", "pretend", "roleplay", "admin", "sudo",
    "execute", "command", "reveal", "bypass", "model", "gpt", "openai", "token", "tokens",
}


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock, open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


LOG_TAG = None  # tests/safety_tests.py sets this to "safety_tests" so its entries are easy to tell apart


def log_safety(defense, caught, game_id=None, **details):
    """defense: short id (e.g. "name_filter"); caught: one plain sentence of what it caught."""
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "defense": defense, "caught": caught}
    if LOG_TAG:
        record["source"] = LOG_TAG
    if game_id:
        record["game"] = game_id[:8]
    record.update(details)
    append_jsonl(SAFETY_LOG, record)
    return record


def clean_name(raw, game_id=None):
    """Return (clean_name, error). error is a friendly message when the name can't be used.

    Keeps letters (any language), spaces, hyphens, apostrophes, and periods. Everything else is stripped.
    Names are only ever shown back to players; they are never sent to the model.
    """
    raw = "" if raw is None else str(raw)
    text = unicodedata.normalize("NFKC", raw)

    code_chars = sorted(set(CODE_CHARS.findall(text)))
    if code_chars:
        log_safety("name_filter", "Name contained code or markup characters and was rejected.", game_id,
                   characters=code_chars, input_length=len(raw))
        return None, "Please type just your first name, using letters."

    kept = "".join(ch for ch in text if ch.isalpha() or ch in " -'.")
    kept = re.sub(r"\s+", " ", kept)
    kept = re.sub(r"(?<![^\W\d_])['\-]|['\-](?![^\W\d_])", "", kept)  # ' and - only between letters
    kept = re.sub(r"(?<![^\W\d_])\.", "", kept)                         # . only right after a letter
    kept = re.sub(r"\s+", " ", kept).strip()
    stripped = len(text) - len(kept)

    if not kept:
        log_safety("name_filter", "Name had no usable letters and was rejected.", game_id,
                   input_length=len(raw))
        return None, "Please type a first name using letters."

    words = {w.lower().strip("-'.") for w in kept.split()}
    hits = sorted(words & INJECTION_WORDS)
    if hits:
        log_safety("name_filter", "Name looked like an instruction to the AI and was rejected.", game_id,
                   matched_words=hits, input_length=len(raw))
        return None, "That doesn't look like a first name. Please type just your first name."

    parts = kept.split()
    trimmed = " ".join(parts[:MAX_NAME_WORDS])[:MAX_NAME_LEN].strip(" -'.")
    if stripped > 0 or trimmed != kept:
        log_safety("name_filter", "Name was cleaned: non-name characters stripped or length/word limit applied.",
                   game_id, chars_removed=max(stripped, 0), input_length=len(raw),
                   output_length=len(trimmed))
    return trimmed, None
