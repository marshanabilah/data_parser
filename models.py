from dataclasses import dataclass
from typing import List, Optional, Tuple, Union
from urllib.parse import urlsplit


MAX_DN_LINE_LENGTH = 200
MAX_ITEMS_LENGTH = 1_000
MAX_NOMINAL_LENGTH = 100
MAX_URL_LENGTH = 2_048


class OrderValidationError(ValueError):
    """Raised when order data cannot safely enter the intake pipeline."""


def _required_text(value: str, field: str, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise OrderValidationError(f"{field} is required")
    if len(cleaned) > maximum:
        raise OrderValidationError(f"{field} exceeds {maximum} characters")
    return cleaned


def _optional_text(
    value: Optional[str], field: str, maximum: int
) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise OrderValidationError(f"{field} cannot be blank")
    if len(cleaned) > maximum:
        raise OrderValidationError(f"{field} exceeds {maximum} characters")
    return cleaned


def _public_https_url(value: str) -> str:
    cleaned = value.strip()
    parsed = urlsplit(cleaned)
    if (
        not cleaned
        or len(cleaned) > MAX_URL_LENGTH
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OrderValidationError("Link must be a public HTTPS URL")
    return cleaned


@dataclass(frozen=True)
class SubmittedLink:
    dn_line: str
    link: str
    items_override: Optional[str] = None
    nominal_override: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dn_line",
            _required_text(self.dn_line, "DN Line", MAX_DN_LINE_LENGTH),
        )
        object.__setattr__(self, "link", _public_https_url(self.link))
        object.__setattr__(
            self,
            "items_override",
            _optional_text(
                self.items_override,
                "Items override",
                MAX_ITEMS_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "nominal_override",
            _optional_text(
                self.nominal_override,
                "Nominal override",
                MAX_NOMINAL_LENGTH,
            ),
        )


@dataclass(frozen=True)
class ExtractedProduct:
    items: Optional[str]
    nominal: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "items",
            _optional_text(self.items, "Items", MAX_ITEMS_LENGTH),
        )
        object.__setattr__(
            self,
            "nominal",
            _optional_text(self.nominal, "Nominal", MAX_NOMINAL_LENGTH),
        )


@dataclass(frozen=True)
class OrderDraft:
    dn_line: str
    link: str
    items: str
    nominal: Optional[str]
    warnings: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dn_line",
            _required_text(self.dn_line, "DN Line", MAX_DN_LINE_LENGTH),
        )
        object.__setattr__(self, "link", _public_https_url(self.link))
        try:
            items = _required_text(self.items, "Items", MAX_ITEMS_LENGTH)
        except OrderValidationError as exc:
            if str(exc) == "Items is required":
                raise OrderValidationError("Items could not be determined") from exc
            raise
        object.__setattr__(self, "items", items)
        object.__setattr__(
            self,
            "nominal",
            _optional_text(self.nominal, "Nominal", MAX_NOMINAL_LENGTH),
        )
        object.__setattr__(
            self,
            "warnings",
            tuple(warning.strip() for warning in self.warnings if warning.strip()),
        )


@dataclass(frozen=True)
class OrderPreview:
    submission_token: str
    orders: Tuple[OrderDraft, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "submission_token",
            _required_text(self.submission_token, "Submission token", 200),
        )
        if not self.orders:
            raise OrderValidationError("Preview requires at least one order")


@dataclass(frozen=True)
class SavedOrder:
    date: str
    order_no: int
    dn_line: str
    items: str
    link: str
    nominal: Optional[str]
    arrived_date: str = ""
    status: str = "Ordered"
    box: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.order_no, bool) or self.order_no < 1:
            raise OrderValidationError("Order No. must be a positive integer")
        object.__setattr__(self, "date", _required_text(self.date, "Date", 32))
        object.__setattr__(
            self,
            "dn_line",
            _required_text(self.dn_line, "DN Line", MAX_DN_LINE_LENGTH),
        )
        object.__setattr__(self, "items", _required_text(self.items, "Items", MAX_ITEMS_LENGTH))
        object.__setattr__(self, "link", _public_https_url(self.link))
        object.__setattr__(
            self,
            "nominal",
            _optional_text(self.nominal, "Nominal", MAX_NOMINAL_LENGTH),
        )

    def as_sheet_row(self) -> List[Union[str, int]]:
        return [
            self.date,
            self.order_no,
            self.dn_line,
            self.items,
            self.link,
            self.nominal or "",
            self.arrived_date,
            self.status,
            self.box,
        ]
