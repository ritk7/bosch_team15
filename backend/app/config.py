"""Every tunable in one place. Override any value with an environment variable or backend/.env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
CACHE_DIR = ROOT / ".cache"
CACHE_DIR.mkdir(exist_ok=True)


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _is_local(url: str) -> bool:
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"))


@dataclass(frozen=True)
class Tier:
    name: str  # local | small | large
    provider: str  # "anthropic" or "openai" (any OpenAI-compatible server: Ollama, vLLM, Groq, OpenAI)
    model: str
    base_url: str
    api_key: str
    usd_in_per_m: float  # USD per million input tokens
    usd_out_per_m: float  # USD per million output tokens
    timeout: float

    @property
    def is_local(self) -> bool:
        return self.provider == "openai" and _is_local(self.base_url)

    @property
    def configured(self) -> bool:
        return bool(self.model and self.base_url) and (self.is_local or bool(self.api_key))


PROVIDER_BASE = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}


def _tier(name: str, provider: str, model: str, base: str, usd_in: float, usd_out: float, timeout: float) -> Tier:
    p = name.upper()
    chosen = env(f"{p}_PROVIDER", provider)
    same = chosen == provider  # defaults only apply when the provider wasn't changed
    default_key = env("ANTHROPIC_API_KEY") if chosen == "anthropic" else env("OPENAI_API_KEY")
    return Tier(
        name=name,
        provider=chosen,
        model=env(f"{p}_MODEL", model if same else ""),
        base_url=env(f"{p}_BASE_URL", base if same else PROVIDER_BASE.get(chosen, "")).rstrip("/"),
        api_key=env(f"{p}_API_KEY", default_key),
        usd_in_per_m=float(env(f"{p}_USD_IN", str(usd_in) if same else "0")),
        usd_out_per_m=float(env(f"{p}_USD_OUT", str(usd_out) if same else "0")),
        timeout=float(env(f"{p}_TIMEOUT", str(timeout))),
    )


# Cheapest first. Prices are list prices per million tokens (platform.claude.com/docs/en/about-claude/pricing,
# checked Oct 2026). Re-check before the pitch, and set *_USD_IN/_OUT if you swap models.
TIERS: list[Tier] = [
    _tier("local", "openai", "qwen2.5:3b", "http://localhost:11434/v1", 0.0, 0.0, 45),  # Ollama on this laptop
    _tier("small", "anthropic", "claude-haiku-5-5", PROVIDER_BASE["anthropic"], 0.10, 0.50, 60),
    _tier("large", "anthropic", "claude-sonnet-5-5", PROVIDER_BASE["anthropic"], 2.00, 10.00, 90),
]

# "What would this cost if every step went to one big model?" The pitch compares against this tier.
BASELINE_TIER = env("BASELINE_TIER", "large")

# Rupees per US dollar. Set this to today's rate before quoting rupee figures.
USD_INR = float(env("USD_INR", "88"))


def tier(name: str) -> Tier:
    for t in TIERS:
        if t.name == name:
            return t
    raise KeyError(f"Unknown tier '{name}'. Use one of: {', '.join(t.name for t in TIERS)}")
