"""FINN LLM router: tiered model selection + SQLite response cache + telemetry ledger.

Tiers
  fast : bulk filing classification, structured extraction      (Gemini 2.5 Flash / gpt-4o-mini / Haiku)
  deep : multi-quarter Narrative-vs-Numbers synthesis only        (Gemini 2.5 Pro  / gpt-4o      / Sonnet)

Env flags
  LLM_PROVIDER = gemini | openai | anthropic   (default gemini)
  GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY
  FINN_MODEL_FAST / FINN_MODEL_DEEP   override model ids
  FINN_CACHE_DB  path to cache.db
"""
from __future__ import annotations

import hashlib
import re
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Type, TypeVar

from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger("finn.llm")
T = TypeVar("T", bound=BaseModel)
Tier = Literal["fast", "deep"]

DB_PATH = os.getenv("FINN_CACHE_DB", str(Path(__file__).parent / "cache.db"))
PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()

DEFAULT_MODELS = {
    "gemini": {"fast": "gemini-3.8-flash", "deep": "gemini-3.8-pro"},
    "openai": {"fast": "gpt-4o-mini", "deep": "gpt-4o"},
    "anthropic": {"fast": "claude-haiku-4-5", "deep": "claude-sonnet-4-5"},
}
# USD per 1M tokens (input, output). Edit if list prices change – cost ledger reads from here.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-pro": (1.25, 10.00),
    # TODO(verify): 3.8 rates below are carried over from 2.5 as placeholders – copy the real ones from Google's pricing page.
    "gemini-3.8-flash": (0.30, 2.50), "gemini-3.8-pro": (1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
    "claude-haiku-4-5": (1.00, 5.00), "claude-sonnet-4-5": (3.00, 15.00),
}


_DEAD: dict[str, str] = {}  # model id that 404'd this process → replacement (deep → fast)


def model_for(tier: Tier, provider: str = PROVIDER) -> str:
    m = os.getenv(f"FINN_MODEL_{tier.upper()}") or DEFAULT_MODELS[provider][tier]
    return _DEAD.get(m, m)


def cost_usd(model: str, tok_in: int, tok_out: int) -> float:
    pi, po = PRICING.get(model, (0.0, 0.0))
    return round((tok_in * pi + tok_out * po) / 1_000_000, 6)


# ───────────────────────── SQLite (cache + ledger) ─────────────────────────
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS llm_cache(
        key TEXT PRIMARY KEY, response TEXT NOT NULL, model TEXT, created_at REAL, expires_at REAL);
    CREATE TABLE IF NOT EXISTS llm_ledger(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, task TEXT, tier TEXT, provider TEXT, model TEXT,
        tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER, cost_usd REAL, cache_hit INTEGER, ok INTEGER, error TEXT);
    CREATE INDEX IF NOT EXISTS ix_ledger_ts ON llm_ledger(ts);
    """)
    return c


def _log(task, tier, provider, model, ti, to, ms, cost, hit, ok=True, err=None) -> None:
    with _lock, _db() as c:
        c.execute("INSERT INTO llm_ledger(ts,task,tier,provider,model,tokens_in,tokens_out,latency_ms,cost_usd,cache_hit,ok,error)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (time.time(), task, tier, provider, model, ti, to, ms, cost, int(hit), int(ok), err))


def _cache_key(model: str, system: str, prompt: str, schema_name: str) -> str:
    return hashlib.sha256(json.dumps([model, system, prompt, schema_name]).encode()).hexdigest()


# ───────────────────────── Provider adapters ─────────────────────────
@dataclass
class _Raw:
    text: str
    tokens_in: int
    tokens_out: int


def _call_gemini(model: str, system: str, prompt: str, schema: Optional[Type[BaseModel]], temp: float, max_out: int) -> _Raw:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = dict(system_instruction=system or None, temperature=temp, max_output_tokens=max_out,
               response_mime_type="application/json" if schema else "text/plain")
    if schema:
        cfg["response_schema"] = schema
    if "flash" in model:  # bulk tier: no thinking budget → cheaper, faster
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    try:
        r = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(**cfg))
    except Exception as e:  # noqa: BLE001  newer model families may not accept thinking_budget → retry once without it
        if "thinking_config" in cfg and "thinking" in str(e).lower():
            cfg.pop("thinking_config")
            r = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(**cfg))
        else:
            raise
    u = r.usage_metadata
    tin = getattr(u, "prompt_token_count", 0) or 0
    # thinking tokens are billed at the output rate
    tout = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
    return _Raw(r.text or "", tin, tout)


def _call_openai(model: str, system: str, prompt: str, schema, temp: float, max_out: int) -> _Raw:
    from openai import OpenAI

    msgs = [{"role": "system", "content": system + ("\nReturn ONLY valid JSON matching this schema:\n"
            + json.dumps(schema.model_json_schema()) if schema else "")}, {"role": "user", "content": prompt}]
    r = OpenAI().chat.completions.create(model=model, messages=msgs, temperature=temp, max_tokens=max_out,
                                         **({"response_format": {"type": "json_object"}} if schema else {}))
    return _Raw(r.choices[0].message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens)


def _call_anthropic(model: str, system: str, prompt: str, schema, temp: float, max_out: int) -> _Raw:
    import anthropic

    sys_p = system + ("\nReturn ONLY valid JSON (no prose, no fences) matching this schema:\n"
                      + json.dumps(schema.model_json_schema()) if schema else "")
    r = anthropic.Anthropic().messages.create(model=model, system=sys_p, max_tokens=max_out, temperature=temp,
                                              messages=[{"role": "user", "content": prompt}])
    return _Raw("".join(b.text for b in r.content if b.type == "text"), r.usage.input_tokens, r.usage.output_tokens)


_ADAPTERS = {"gemini": _call_gemini, "openai": _call_openai, "anthropic": _call_anthropic}


def _strip_fences(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


# ───────────────────────── Error classification ─────────────────────────
_TRANSIENT = {408, 429, 500, 502, 503, 504}
_FATAL = {400, 401, 403, 404}
_T_WORDS = ("resource_exhausted", "unavailable", "deadline_exceeded", "overloaded", "timed out", "timeout", "connection", "rate limit")
_F_WORDS = ("not_found", "permission_denied", "unauthenticated", "invalid_argument", "api key", "no longer available")


def _status(e: Exception) -> Optional[int]:
    for attr in ("code", "status_code", "status"):
        v = getattr(e, attr, None)
        if isinstance(v, int) and 100 <= v <= 599:
            return v
    m = re.match(r"\s*(\d{3})\b", str(e))
    return int(m.group(1)) if m else None


def classify_error(e: Exception) -> str:
    """'fatal' (never retry: 400/401/403/404), 'transient' (429/5xx/network – back off & retry), 'retry' (bad JSON etc. – retry once)."""
    st, msg = _status(e), str(e).lower()
    if st in _FATAL:
        return "fatal"
    if st in _TRANSIENT:
        return "transient"
    if any(w in msg for w in _F_WORDS):
        return "fatal"
    if any(w in msg for w in _T_WORDS):
        return "transient"
    return "retry"


def _is_not_found(e: Exception) -> bool:
    return _status(e) == 404 or "not_found" in str(e).lower() or "no longer available" in str(e).lower()


# ───────────────────────── Public API ─────────────────────────
def complete(
    *, task: str, prompt: str, system: str = "", tier: Tier = "fast",
    schema: Optional[Type[T]] = None, ttl_s: Optional[int] = 7 * 86400,
    temperature: float = 0.1, max_output_tokens: int = 4096, retries: int = 2,
) -> T | str:
    """Route one call. Returns a validated `schema` instance (or raw str if schema is None).
    ttl_s=None disables caching. Identical (model, system, prompt, schema) ⇒ cache hit at $0 / ~1ms.
    Filings are immutable once published, so a long TTL is safe; EOD-derived prompts should pass a short ttl."""
    model = model_for(tier)
    sname = schema.__name__ if schema else "text"
    key = _cache_key(model, system, prompt, sname)
    t0 = time.perf_counter()

    if ttl_s:
        with _lock, _db() as c:
            row = c.execute("SELECT response FROM llm_cache WHERE key=? AND expires_at>?", (key, time.time())).fetchone()
        if row:
            _log(task, tier, PROVIDER, model, 0, 0, int((time.perf_counter() - t0) * 1000), 0.0, True)
            return schema.model_validate_json(row[0]) if schema else row[0]

    key_env = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[PROVIDER]
    if not os.getenv(key_env):  # fail fast: no point burning retries/backoff without credentials
        raise RuntimeError(f"{key_env} not set")
    call = _ADAPTERS[PROVIDER]
    last: Exception | None = None
    soft_fails = 0  # failures that consumed a retry (transient / malformed output)
    while True:
        try:
            raw = call(model, system, prompt, schema, temperature, max_output_tokens)
            text = _strip_fences(raw.text) if schema else raw.text
            out = schema.model_validate_json(text) if schema else text  # validation failure ⇒ 'retry' class
            ms = int((time.perf_counter() - t0) * 1000)
            _log(task, tier, PROVIDER, model, raw.tokens_in, raw.tokens_out, ms, cost_usd(model, raw.tokens_in, raw.tokens_out), False)
            if ttl_s:
                payload = out.model_dump_json() if schema else out
                with _lock, _db() as c:
                    c.execute("INSERT OR REPLACE INTO llm_cache VALUES(?,?,?,?,?)",
                              (_cache_key(model, system, prompt, sname), payload, model, time.time(), time.time() + ttl_s))
            return out
        except Exception as e:  # noqa: BLE001
            last, kind = e, classify_error(e)
            # Deep model id gone (404)? Degrade to the fast model once, remember it for the process, don't burn a retry.
            if kind == "fatal" and tier == "deep" and _is_not_found(e) and model != model_for("fast"):
                fb = model_for("fast")
                log.warning("deep model %s unavailable (%s) – falling back to %s", model, str(e)[:100], fb)
                _DEAD[model], model = fb, fb
                continue
            if kind == "fatal":
                log.error("LLM %s/%s fatal (no retry): %s", task, model, str(e)[:200])
                break
            soft_fails += 1
            log.warning("LLM %s/%s %s error %d/%d: %s", task, model, kind, soft_fails, retries + 1, str(e)[:160])
            if soft_fails > retries or (kind == "retry" and soft_fails > 1):  # malformed output: one retry only
                break
            time.sleep(1.5 * (2 ** (soft_fails - 1)) if kind == "transient" else 0.3)
    _log(task, tier, PROVIDER, model, 0, 0, int((time.perf_counter() - t0) * 1000), 0.0, False, ok=False, err=str(last)[:300])
    raise RuntimeError(f"LLM call '{task}' failed: {last}")


def get_routing_stats(since_hours: Optional[float] = None) -> dict:
    """Telemetry for the 'cost & efficiency' panel: totals, per-model, per-task, cache savings."""
    where, args = ("WHERE ts>?", [time.time() - since_hours * 3600]) if since_hours else ("", [])
    with _lock, _db() as c:
        c.row_factory = sqlite3.Row
        tot = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(cost_usd),0) cost, COALESCE(SUM(tokens_in),0) tin, "
                        f"COALESCE(SUM(tokens_out),0) tout, COALESCE(SUM(cache_hit),0) hits, "
                        f"COALESCE(AVG(CASE WHEN cache_hit=0 THEN latency_ms END),0) avg_ms FROM llm_ledger {where}", args).fetchone()
        by_model = c.execute(f"SELECT model, COUNT(*) calls, SUM(tokens_in) tokens_in, SUM(tokens_out) tokens_out, "
                             f"ROUND(SUM(cost_usd),5) cost_usd, ROUND(AVG(CASE WHEN cache_hit=0 THEN latency_ms END)) avg_latency_ms "
                             f"FROM llm_ledger {where} GROUP BY model", args).fetchall()
        by_task = c.execute(f"SELECT task, tier, COUNT(*) calls, SUM(cache_hit) cache_hits, ROUND(SUM(cost_usd),5) cost_usd "
                            f"FROM llm_ledger {where} GROUP BY task, tier ORDER BY cost_usd DESC", args).fetchall()
        # Avoided spend: each cache hit would have cost the average live call for that same task
        avg_cost = {r["task"]: r["a"] for r in c.execute(
            f"SELECT task, AVG(cost_usd) a FROM llm_ledger {where + (' AND' if where else 'WHERE')} cache_hit=0 GROUP BY task", args)}
        hits = c.execute(f"SELECT task, COUNT(*) n FROM llm_ledger {where + (' AND' if where else 'WHERE')} cache_hit=1 GROUP BY task", args).fetchall()
        fails = c.execute(f"SELECT COUNT(*) FROM llm_ledger {where + (' AND' if where else 'WHERE')} ok=0", args).fetchone()[0]
    saved = sum(avg_cost.get(h["task"], 0) * h["n"] for h in hits)
    n = tot["n"] or 0
    return {
        "provider": PROVIDER, "models": {t: model_for(t) for t in ("fast", "deep")},
        "total_calls": n, "live_calls": n - tot["hits"], "cache_hits": tot["hits"],
        "cache_hit_rate": round(tot["hits"] / n, 3) if n else 0.0, "failed_calls": fails,
        "tokens_in": tot["tin"], "tokens_out": tot["tout"], "total_cost_usd": round(tot["cost"], 5),
        "est_cost_saved_by_cache_usd": round(saved, 5), "avg_live_latency_ms": round(tot["avg_ms"]),
        "by_model": [dict(r) for r in by_model], "by_task": [dict(r) for r in by_task],
    }


def clear_expired_cache() -> int:
    with _lock, _db() as c:
        return c.execute("DELETE FROM llm_cache WHERE expires_at<?", (time.time(),)).rowcount


if __name__ == "__main__":
    import pprint
    pprint.pprint(get_routing_stats())
