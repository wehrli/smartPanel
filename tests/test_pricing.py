from smartpanel.claude_stats.pricing import estimate_cost, price_for_model


def test_matches_family_by_substring():
    assert price_for_model("claude-opus-4-7").input == 15.00
    assert price_for_model("claude-sonnet-4-6").input == 3.00
    assert price_for_model("claude-haiku-4-5-20251001").input == 1.00


def test_unknown_model_uses_default():
    # Pessimistic fallback => Opus rates.
    assert price_for_model("gpt-4").input == 15.00
    assert price_for_model("").input == 15.00


def test_cost_combines_all_token_classes():
    # 1M of each on Opus: 15 + 75 + 18.75 + 1.50 = 110.25
    cost = estimate_cost(
        "claude-opus-4-7",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert abs(cost - 110.25) < 0.01
