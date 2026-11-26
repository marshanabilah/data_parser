"""JPY-to-IDR estimates for the admin calculator."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def parse_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value.strip())
        if not rate.is_finite() or rate <= 0 or rate > 1000000 or rate.as_tuple().exponent < -4:
            raise ValueError
        return rate
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError("Enter a valid rate greater than zero, with up to four decimal places.") from exc


def calculate(price: str, quantity: str, rate: str):
    if not price.isascii() or not price.isdigit() or len(price) > 9:
        raise ValueError("Enter a whole-yen item price of zero or more.")
    if not quantity.isascii() or not quantity.isdigit() or not 1 <= int(quantity) <= 10000:
        raise ValueError("Enter a quantity between 1 and 10,000.")
    amount = Decimal(int(price)) * parse_rate(rate) * int(quantity)
    items = int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    fee = 15000 * int(quantity)
    return items, fee, items + fee
