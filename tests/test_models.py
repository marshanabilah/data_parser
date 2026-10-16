import pytest

from models import OrderDraft, OrderValidationError, SavedOrder, SubmittedLink


def test_submitted_link_normalizes_required_fields_and_overrides():
    submitted = SubmittedLink(
        dn_line="  DN-001  ",
        link="https://example.com/item/1",
        items_override="  Limited Album  ",
        nominal_override="  ¥4,500  ",
    )

    assert submitted.dn_line == "DN-001"
    assert submitted.items_override == "Limited Album"
    assert submitted.nominal_override == "¥4,500"


@pytest.mark.parametrize("dn_line", ["", "   "])
def test_submitted_link_rejects_missing_dn_line(dn_line):
    with pytest.raises(OrderValidationError, match="DN Line is required"):
        SubmittedLink(dn_line=dn_line, link="https://example.com/item/1")


@pytest.mark.parametrize(
    "link",
    [
        "",
        "http://example.com/item/1",
        "ftp://example.com/item/1",
        "https:///missing-host",
        "not a url",
    ],
)
def test_submitted_link_rejects_invalid_or_non_https_url(link):
    with pytest.raises(OrderValidationError, match="public HTTPS URL"):
        SubmittedLink(dn_line="DN-001", link=link)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("items_override", "   ", "Items override cannot be blank"),
        ("nominal_override", "   ", "Nominal override cannot be blank"),
    ],
)
def test_submitted_link_rejects_blank_overrides(field, value, message):
    values = {field: value}

    with pytest.raises(OrderValidationError, match=message):
        SubmittedLink(
            dn_line="DN-001",
            link="https://example.com/item/1",
            **values,
        )


def test_order_draft_requires_an_item_name():
    with pytest.raises(OrderValidationError, match="Items could not be determined"):
        OrderDraft(
            dn_line="DN-001",
            link="https://example.com/item/1",
            items="   ",
            nominal=None,
        )


def test_order_draft_preserves_warnings_as_immutable_values():
    draft = OrderDraft(
        dn_line="DN-001",
        link="https://example.com/item/1",
        items="Limited Album",
        nominal=None,
        warnings=("Nominal is missing", "Duplicate link"),
    )

    assert draft.warnings == ("Nominal is missing", "Duplicate link")


def test_saved_order_serializes_to_the_exact_sheet_column_order():
    order = SavedOrder(
        date="2026-10-16 14:30",
        order_no=42,
        dn_line="DN-001",
        items="Limited Album",
        link="https://example.com/item/1",
        nominal="¥4,500",
    )

    assert order.as_sheet_row() == [
        "2026-10-16 14:30",
        42,
        "DN-001",
        "Limited Album",
        "https://example.com/item/1",
        "¥4,500",
        "",
        "Ordered",
        "",
    ]


def test_saved_order_rejects_non_positive_order_number():
    with pytest.raises(OrderValidationError, match="positive integer"):
        SavedOrder(
            date="2026-10-16 14:30",
            order_no=0,
            dn_line="DN-001",
            items="Limited Album",
            link="https://example.com/item/1",
            nominal=None,
        )
