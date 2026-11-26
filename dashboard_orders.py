"""Validated order records for the dashboard."""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple
from urllib.parse import urlsplit


STATUSES = ("Purchased", "Arrived in Japan", "Shipping to Indonesia", "Arrived in Indonesia", "Completed")


@dataclass(frozen=True)
class ProductLine:
    name: str
    link: str
    price_jpy: int
    quantity: int

    def __post_init__(self):
        parsed = urlsplit(self.link)
        if not self.name.strip() or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("A name and public HTTPS link are required")
        if type(self.price_jpy) is not int or self.price_jpy < 0:
            raise ValueError("A nonnegative whole-yen price is required")
        if type(self.quantity) is not int or self.quantity < 1:
            raise ValueError("Quantity must be a positive integer")


@dataclass(frozen=True)
class OrderRecord:
    order_date: date
    line_name: str
    dn_line: str
    shop: str
    allows_multiple: bool
    shipping_jpy: Optional[int]
    products: Tuple[ProductLine, ...]
    status: str = "Purchased"

    def __post_init__(self):
        if not isinstance(self.order_date, date) or not all(value.strip() for value in (self.line_name, self.dn_line, self.shop)):
            raise ValueError("Date, customer, DN Line, and shop are required")
        if type(self.allows_multiple) is not bool or not self.products or (not self.allows_multiple and len(self.products) != 1):
            raise ValueError("Invalid number of product links for shop")
        if not all(isinstance(product, ProductLine) for product in self.products):
            raise ValueError("Invalid product line")
        if self.shipping_jpy is not None and (type(self.shipping_jpy) is not int or self.shipping_jpy < 0):
            raise ValueError("Shipping must be blank or a nonnegative whole-yen amount")
        if self.status not in STATUSES:
            raise ValueError("Invalid status")
