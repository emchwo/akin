"""The one place that talks to the model.

Every call goes through call_llm(), which:
- uses gpt-6-luna through Portkey (no other model, not configurable on purpose),
- enforces the per-game and per-server call caps and a max-tokens limit per call,
- records input / cached input / cache-write / output tokens, tagged by agent, on the game's Usage.
"""

import json
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from safety import log_safety

APP_DIR = Path(__file__).resolve().parent

# .env lookup: the repo folder first, then the folder one level above it.
# load_dotenv never overrides a value that is already set, so the repo copy wins if both exist.
ENV_FILES = [APP_DIR / ".env", APP_DIR.parent / ".env"]
ENV_LOADED_FROM = None
for _p in ENV_FILES:
    if _p.is_file():
        load_dotenv(_p)
        ENV_LOADED_FROM = ENV_LOADED_FROM or str(_p)

MODEL = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
CACHE_KEY = "akin-v1"  # one key for all agents: they share the same static system-prompt prefix

# Spending caps.
MAX_CALLS_PER_GAME = 40
REVEAL_RESERVE = 2  # calls kept back for the final Reveal Writer (1 try + 1 retry)
MAX_CALLS_PER_SERVER = int(os.getenv("AKIN_MAX_CALLS_PER_SERVER", "600"))
MAX_TOKENS = {  # output cap per call, by agent (includes any reasoning tokens)
    "scenario_writer": 900,
    "checker": 600,
    "prompt_writer": 400,
    "reveal_writer": 1800,
}
DEFAULT_MAX_TOKENS = 800
REQUEST_TIMEOUT_S = 45

# USD per 1M tokens (OpenAI standard pricing for gpt-6-luna, MGT 409 Lecture 11).
PRICES = {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50}

AGENTS = ["scenario_writer", "checker", "prompt_writer", "reveal_writer"]


# Optional step-by-step trace of every model call (for documenting a real run). Off unless AKIN_TRACE=1.
# Player names never reach the model, so they can't appear here either.
TRACE = os.getenv("AKIN_TRACE") == "1"
NO_CACHE = os.getenv("AKIN_NO_CACHE") == "1"  # tools/benchmark.py --no-cache, to measure what caching saves
TRACE_LOG = APP_DIR / "logs" / "trace.jsonl"


def _trace(game_id, agent, user, output, u, finish):
    from safety import append_jsonl
    details = getattr(u, "prompt_tokens_details", None)
    append_jsonl(TRACE_LOG, {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "game": (game_id or "")[:8], "agent": agent,
        "input_data": user, "output": output, "finish_reason": finish,
        "tokens": {"input": getattr(u, "prompt_tokens", 0), "cached_input": getattr(details, "cached_tokens", 0) or 0,
                   "output": getattr(u, "completion_tokens", 0)},
    })


class BudgetExceeded(Exception):
    """A spending cap was hit. The game should end gracefully."""


class LLMError(Exception):
    """The model call failed or returned something unusable."""


_server_lock = threading.Lock()
_server_calls = 0
_client = None


def server_calls():
    return _server_calls


def get_client():
    global _client
    if _client is None:
        key = os.getenv("PORTKEY_API_KEY", "").strip()
        if not key:
            raise LLMError("PORTKEY_API_KEY is not set. Put it in .env in the app folder or one level above.")
        # force-refresh: skip Portkey's response cache so every logged token count is a real model call.
        _client = OpenAI(api_key=key, base_url=PORTKEY_BASE_URL, timeout=REQUEST_TIMEOUT_S, max_retries=1,
                         default_headers={"x-portkey-provider": "openai",
                                          "x-portkey-cache-force-refresh": "true"})
    return _client


class Usage:
    """Token and call counts for one game, by agent."""

    def __init__(self, max_calls=MAX_CALLS_PER_GAME):
        self.max_calls = max_calls
        self.calls = 0
        self.lock = threading.Lock()
        self.by_agent = {a: self._blank() for a in AGENTS}

    @staticmethod
    def _blank():
        return {"calls": 0, "input": 0, "cached_input": 0, "cache_write": 0, "output": 0, "reasoning": 0}

    def reserve(self, game_id, agent):
        """Count a call against both caps before it is made. Raises BudgetExceeded if either is full."""
        global _server_calls
        with self.lock:
            # The last few calls are held back for the Reveal Writer, so a game that runs out of budget
            # can still end with a real reveal instead of stopping mid-sentence.
            limit = self.max_calls if agent == "reveal_writer" else self.max_calls - REVEAL_RESERVE
            if self.calls >= limit:
                log_safety("spending_cap", f"Per-game LLM call cap reached ({self.calls}/{self.max_calls} calls, "
                           f"{REVEAL_RESERVE} held for the reveal); blocked a {agent} call.", game_id,
                           cap="per_game", limit=self.max_calls, agent=agent)
                raise BudgetExceeded("game")
            with _server_lock:
                if _server_calls >= MAX_CALLS_PER_SERVER:
                    log_safety("spending_cap", f"Per-server LLM call cap of {MAX_CALLS_PER_SERVER} reached; "
                               f"blocked a {agent} call.", game_id, cap="per_server", limit=MAX_CALLS_PER_SERVER)
                    raise BudgetExceeded("server")
                _server_calls += 1
            self.calls += 1

    def add(self, agent, usage):
        details = getattr(usage, "prompt_tokens_details", None)
        out_details = getattr(usage, "completion_tokens_details", None)
        with self.lock:
            row = self.by_agent.setdefault(agent, self._blank())
            row["calls"] += 1
            row["input"] += usage.prompt_tokens or 0
            row["cached_input"] += (getattr(details, "cached_tokens", 0) or 0) if details else 0
            row["cache_write"] += (getattr(details, "cache_write_tokens", 0) or 0) if details else 0
            row["output"] += usage.completion_tokens or 0
            row["reasoning"] += (getattr(out_details, "reasoning_tokens", 0) or 0) if out_details else 0

    @staticmethod
    def cost(row):
        uncached = max(row["input"] - row["cached_input"] - row["cache_write"], 0)
        return (uncached * PRICES["input"] + row["cached_input"] * PRICES["cached_input"]
                + row["cache_write"] * PRICES["cache_write"] + row["output"] * PRICES["output"]) / 1e6

    def summary(self):
        with self.lock:
            agents = {a: {**r, "cost_usd": round(self.cost(r), 6)} for a, r in self.by_agent.items()}
        total = self._blank()
        for r in agents.values():
            for k in total:
                total[k] += r[k]
        total["cost_usd"] = round(self.cost(total), 6)
        return {"agents": agents, "total": total, "llm_calls": self.calls, "max_calls": self.max_calls}


def call_llm(usage, game_id, agent, system, user, schema=None, effort="low"):
    """Make one model call. Returns parsed JSON (dict) when schema is given, else stripped text.

    system: the agent's static instructions (identical across calls, so the prefix caches).
    user:   this call's data only.
    """
    usage.reserve(game_id, agent)
    if NO_CACHE:  # benchmark only: a unique first line means no two prompts share a cacheable prefix
        system = f"Request {time.time_ns()}\n{system}"
    kwargs = {}
    if schema is not None:
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": agent, "strict": True, "schema": schema}}
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_completion_tokens=MAX_TOKENS.get(agent, DEFAULT_MAX_TOKENS),
            reasoning_effort=effort,
            prompt_cache_key=CACHE_KEY,
            **kwargs,
        )
    except Exception as e:  # network, auth, provider errors
        raise LLMError(f"{agent} call failed: {type(e).__name__}: {str(e)[:200]}") from e

    if resp.usage:
        usage.add(agent, resp.usage)
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    if TRACE:
        _trace(game_id, agent, user, content, resp.usage, choice.finish_reason)
    if choice.finish_reason == "length":
        log_safety("max_tokens", f"{agent} hit its {MAX_TOKENS.get(agent, DEFAULT_MAX_TOKENS)}-token output cap; "
                   "the cut-off answer was discarded.", game_id, agent=agent)
        raise LLMError(f"{agent} output was cut off at the token cap")
    if not content:
        raise LLMError(f"{agent} returned an empty answer")
    if schema is None:
        return content
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"{agent} returned invalid JSON") from e
