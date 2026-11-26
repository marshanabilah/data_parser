import pytest

from pricing import calculate, parse_rate


def test_calculate_per_item_fee_and_quantity():
    assert calculate("1000", "2", "110") == (220000, 30000, 250000)


def test_fractional_rate_rounds_total_item_cost_to_nearest_rupiah():
    assert calculate("1", "1", "110.5") == (111, 15000, 15111)


@pytest.mark.parametrize("rate", ["", "0", "-1", "NaN", "Infinity", "1.12345"])
def test_reject_invalid_rate(rate):
    with pytest.raises(ValueError):
        parse_rate(rate)


@pytest.mark.parametrize("price,quantity", [("-1", "1"), ("1.5", "1"), ("100", "0"), ("100", "-1")])
def test_reject_invalid_estimate_inputs(price, quantity):
    with pytest.raises(ValueError):
        calculate(price, quantity, "110")
