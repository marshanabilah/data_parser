import asyncio

import pytest

from link_extractor import ProductFetchError, UnsafeUrlError
from models import ExtractedProduct, SavedOrder
from order_service import OrderIntakeService, OrderPreparationError


class Interpreter:
    def __init__(self, result):
        self.result = result

    def interpret(self, message, indexed_links):
        return self.result


class Extractor:
    def __init__(self, products=None, errors=None):
        self.products = products or {}
        self.errors = errors or {}
        self.calls = []

    async def extract(self, link):
        self.calls.append(link)
        error = self.errors.get(link)
        if error:
            raise error
        return self.products.get(link, ExtractedProduct(None, None))


class Repository:
    def __init__(self, duplicates=()):
        self.duplicates = frozenset(duplicates)
        self.save_calls = []
        self.saved_by_token = {}

    def find_duplicate_links(self, links):
        return self.duplicates.intersection(links)

    def save_orders(self, drafts, token):
        self.save_calls.append((drafts, token))
        if token not in self.saved_by_token:
            self.saved_by_token[token] = tuple(
                SavedOrder(
                    date="2026-10-16 14:30",
                    order_no=index,
                    dn_line=draft.dn_line,
                    items=draft.items,
                    link=draft.link,
                    nominal=draft.nominal,
                )
                for index, draft in enumerate(drafts, start=1)
            )
        return self.saved_by_token[token]


def run(awaitable):
    return asyncio.run(awaitable)


def test_prepare_builds_ordered_rows_with_overrides_and_warnings():
    one = "https://shop.example/one"
    two = "https://shop.example/two"
    interpreter = Interpreter(
        '[{"index":0,"dn_line":"DN-A"},'
        '{"index":1,"dn_line":"DN-B","items":"Manual Item",'
        '"nominal":"IDR 50,000"}]'
    )
    extractor = Extractor(
        {
            one: ExtractedProduct("Scraped One", "JPY 1,000"),
            two: ExtractedProduct("Ignored Name", "USD 20"),
        }
    )
    repository = Repository(duplicates=(one,))
    service = OrderIntakeService(
        interpreter,
        extractor,
        repository,
        token_factory=lambda: "preview-token",
    )

    preview = run(service.prepare(f"{one}\n{two}"))

    assert preview.submission_token == "preview-token"
    assert [order.items for order in preview.orders] == ["Scraped One", "Manual Item"]
    assert [order.nominal for order in preview.orders] == ["JPY 1,000", "IDR 50,000"]
    assert preview.orders[0].warnings == ("This link already exists in Orders",)
    assert preview.orders[1].warnings == ()


def test_missing_price_is_allowed_with_warning():
    link = "https://shop.example/no-price"
    service = OrderIntakeService(
        Interpreter('[{"index":0,"dn_line":"DN-A"}]'),
        Extractor({link: ExtractedProduct("Known Product", None)}),
        Repository(),
    )

    preview = run(service.prepare(link))

    assert preview.orders[0].nominal is None
    assert preview.orders[0].warnings == ("Nominal is missing",)


def test_missing_item_blocks_entire_submission():
    link = "https://shop.example/unknown"
    service = OrderIntakeService(
        Interpreter('[{"index":0,"dn_line":"DN-A"}]'),
        Extractor({link: ExtractedProduct(None, "JPY 1,000")}),
        Repository(),
    )

    with pytest.raises(OrderPreparationError, match="Items could not be determined"):
        run(service.prepare(link))


def test_blocked_page_can_use_manual_item_and_blank_nominal():
    link = "https://shop.example/blocked"
    service = OrderIntakeService(
        Interpreter('[{"index":0,"dn_line":"DN-A","items":"Manual"}]'),
        Extractor(errors={link: ProductFetchError("blocked")}),
        Repository(),
    )

    preview = run(service.prepare(link))

    assert preview.orders[0].items == "Manual"
    assert preview.orders[0].nominal is None
    assert preview.orders[0].warnings == (
        "Product page could not be read; manual item was used",
        "Nominal is missing",
    )


def test_unsafe_url_blocks_even_with_manual_overrides():
    link = "https://private.example/item"
    service = OrderIntakeService(
        Interpreter(
            '[{"index":0,"dn_line":"DN-A","items":"Manual",'
            '"nominal":"IDR 1"}]'
        ),
        Extractor(errors={link: UnsafeUrlError("private")}),
        Repository(),
    )

    with pytest.raises(OrderPreparationError, match="not a safe public page"):
        run(service.prepare(link))


def test_parser_failure_is_a_corrective_preparation_error():
    service = OrderIntakeService(Interpreter("[]"), Extractor(), Repository())

    with pytest.raises(OrderPreparationError, match="non-empty JSON array"):
        run(service.prepare("https://shop.example/item"))


def test_confirmation_reuses_submission_token_for_idempotent_retry():
    link = "https://shop.example/item"
    repository = Repository()
    service = OrderIntakeService(
        Interpreter('[{"index":0,"dn_line":"DN-A"}]'),
        Extractor({link: ExtractedProduct("Item", "USD 1")}),
        repository,
        token_factory=lambda: "stable-token",
    )
    preview = run(service.prepare(link))

    first = run(service.confirm(preview))
    second = run(service.confirm(preview))

    assert first == second
    assert [call[1] for call in repository.save_calls] == [
        "stable-token",
        "stable-token",
    ]
