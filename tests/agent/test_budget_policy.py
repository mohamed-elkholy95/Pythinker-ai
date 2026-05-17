"""BudgetPolicy: derive soft/target/hard zones from window+output_reserve."""
from __future__ import annotations

import pytest

from pythinker.agent.budget import BudgetPolicy


def test_for_model_at_272k_with_24k_output():
    p = BudgetPolicy.for_model(window=272_000, output_reserve=24_000)
    assert p.window == 272_000
    assert p.output_reserve == 24_000
    assert p.safety == 4_250
    assert p.soft == 239_500
    assert p.target == 119_750
    assert p.hard == 243_750
    assert p.target < p.soft < p.hard


def test_for_model_at_65k_default():
    p = BudgetPolicy.for_model(window=65_536, output_reserve=8_192)
    assert p.safety == 2_048
    assert p.soft == 65_536 - 8_192 - 2 * 2_048
    assert p.target == 26_624
    assert p.hard == 65_536 - 8_192 - 2_048
    assert p.target < p.soft < p.hard


def test_zone_classification():
    p = BudgetPolicy.for_model(window=272_000, output_reserve=24_000)
    assert p.classify(0) == "green"
    assert p.classify(p.target - 1) == "green"
    assert p.classify(p.target + 1) == "amber"
    assert p.classify(p.soft - 1) == "amber"
    assert p.classify(p.soft + 1) == "red"
    assert p.classify(p.hard + 1) == "critical"


def test_invalid_window_rejected():
    with pytest.raises(ValueError):
        BudgetPolicy.for_model(window=0, output_reserve=1024)


def test_output_reserve_exceeding_window_is_clamped_not_raised():
    p = BudgetPolicy.for_model(window=1_000, output_reserve=2_000)
    assert p.output_reserve <= p.window - 1_024 or p.output_reserve == 0
    assert p.soft >= 1
    assert p.hard >= 1
    assert p.target >= 0
