"""Model router: try the cheapest tier that can do the job; escalate only when the answer fails a check.

    res = await llm.complete(
        "triage", prompt, system="...", min_tier="local",
        accept=lambda text: llm.extract_json(text) is not None,
    )
    res.text, res.source ("local" | "small" | "large" | "cache"), res.cost_usd

Every attempt is written to the ledger, including rejected ones, so the cost figures stay honest.
If no tier is configured or every tier fails, NoModelAvailable is raised: catch it and fall back to rules.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from . import config, ledger
from .cache import cache

ORDER = ["local", "small", "large"]


class NoModelAvailable(RuntimeError):
    """No configured tier produced an acceptable answer. Callers should fall back to rules."""


@dataclass
class LLMResult:
    text: str
    source: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    cache: str = "miss"


_down_until: dict[str, float] = {}
_reach: dict[str, tuple[bool, float]] = {}


async def _reachable(tier: config.Tier) -> bool:
    if not tier.configured or time.time() < _down_until.get(tier.name, 0.0):
        return False
    if not tier.is_local:
        return True
    ok, checked = _reach.get(tier.name, (False, 0.0))
    if time.time() - checked < 30:
        return ok
    try:
        async with httpx.AsyncClient(timeout=1.5) as http:
            ok = (await http.get(f"{tier.base_url}/models")).status_code < 500
    except httpx.HTTPError:
        ok = False
    _reach[tier.name] = (ok, time.time())
    return ok


async def status() -> dict[str, dict]:
    return {
        t.name: {"model": t.model, "provider": t.provider, "configured": t.configured, "available": await _reachable(t)}
        for t in config.TIERS
    }


async def _call(
    tier: config.Tier, system: str, prompt: str, max_tokens: int, temperature: float | None
) -> tuple[str, int, int]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(tier.timeout, connect=3.0)) as http:
        if tier.provider == "anthropic":
            body: dict = {"model": tier.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
            if system:
                body["system"] = system
            if temperature is not None:
                body["temperature"] = temperature
            r = await http.post(
                f"{tier.base_url}/v1/messages",
                json=body,
                headers={"x-api-key": tier.api_key, "anthropic-version": "2023-06-01"},
            )
            r.raise_for_status()
            data = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage") or {}
            return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": tier.model, "messages": messages, "max_tokens": max_tokens}
        if temperature is not None:
            body["temperature"] = temperature
        headers = {"Authorization": f"Bearer {tier.api_key}"} if tier.api_key else {}
        r = await http.post(f"{tier.base_url}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


async def complete(
    task: str,
    prompt: str,
    *,
    system: str = "",
    max_tokens: int = 400,
    temperature: float | None = None,
    min_tier: str = "local",
    max_tier: str = "large",
    accept: Callable[[str], bool] | None = None,
    semantic_cache: bool = False,
    run_id: str = "",
) -> LLMResult:
    hit = cache.get(task, system, prompt, semantic=semantic_cache)
    if hit and accept is not None and not accepted(accept, hit[0]):
        hit = None  # a cached answer must pass today's check too
    if hit:
        text, kind, tin, tout = hit
        ledger.record(task=task, source="cache", model=f"{kind} cache hit", tokens_in=tin, tokens_out=tout,
                      cost_usd=0.0, cache=kind, run_id=run_id)
        return LLMResult(text, "cache", f"{kind} cache hit", tin, tout, 0.0, 0.0, kind)

    ladder = ORDER[ORDER.index(min_tier): ORDER.index(max_tier) + 1]
    problems: list[str] = []
    for name in ladder:
        tier = config.tier(name)
        if not await _reachable(tier):
            continue
        started = time.perf_counter()
        try:
            text, tin, tout = await _call(tier, system, prompt, max_tokens, temperature)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as err:
            _down_until[name] = time.time() + 30  # skip this tier for 30 s instead of retrying every call
            problems.append(f"{name}: {err}")
            continue
        latency = (time.perf_counter() - started) * 1000
        tin = tin or ledger.estimate_tokens(system + prompt)
        tout = tout or ledger.estimate_tokens(text)
        cost = ledger.price(name, tin, tout)
        ok = accepted(accept, text) if accept else bool(text.strip())
        ledger.record(task=task, source=name, model=tier.model, tokens_in=tin, tokens_out=tout, cost_usd=cost,
                      latency_ms=latency, cache="miss", run_id=run_id, accepted=ok)
        if ok:
            cache.put(task, system, prompt, text, tin, tout)
            return LLMResult(text, name, tier.model, tin, tout, cost, latency)
        problems.append(f"{name}: answer failed the check")
    raise NoModelAvailable("; ".join(problems) or "no model tier is configured or reachable")


def accepted(check: Callable[[str], bool], text: str) -> bool:
    """Malformed model output is a rejected answer, never a crash of the experiment workflow."""
    try:
        return bool(check(text))
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return False


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (models like to wrap JSON in prose or code fences)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
