from __future__ import annotations

from dataclasses import dataclass

# Prices are USD per 1,000,000 tokens. Update when Anthropic changes pricing.
# These reflect public Anthropic API pricing as of early 2026.
@dataclass(frozen=True, slots=True)
class ModelPrice:
    input: float
    output: float
    cache_write: float
    cache_read: float


MODEL_PRICES: dict[str, ModelPrice] = {
    # Opus family
    "opus": ModelPrice(input=15.00, output=75.00, cache_write=18.75, cache_read=1.50),
    # Sonnet family
    "sonnet": ModelPrice(input=3.00, output=15.00, cache_write=3.75, cache_read=0.30),
    # Haiku family
    "haiku": ModelPrice(input=1.00, output=5.00, cache_write=1.25, cache_read=0.10),
}

# Default applied to unknown models — pessimistic (Opus rates).
DEFAULT_PRICE_KEY = "opus"


def price_for_model(model_id: str) -> ModelPrice:
    """Match a Claude model ID like 'claude-opus-4-7' to a price tier by family substring."""
    low = (model_id or "").lower()
    for key, price in MODEL_PRICES.items():
        if key in low:
            return price
    return MODEL_PRICES[DEFAULT_PRICE_KEY]


def output_cost(model_id: str, tokens: float) -> float:
    """Cost of emitting `tokens` output tokens on this model."""
    return tokens * price_for_model(model_id).output / 1_000_000.0


def cache_read_cost(model_id: str, tokens: float) -> float:
    """Cost of re-reading `tokens` prompt tokens from the cache.

    Used to price the *echo*: everything the assistant says is re-sent as
    prompt on every later turn of the same session.
    """
    return tokens * price_for_model(model_id).cache_read / 1_000_000.0


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int,
                  cache_write_tokens: int, cache_read_tokens: int) -> float:
    p = price_for_model(model_id)
    per_token = 1_000_000.0
    return (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_write_tokens * p.cache_write
        + cache_read_tokens * p.cache_read
    ) / per_token
