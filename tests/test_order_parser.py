import json

import pytest

from order_parser import OrderParseError, extract_https_links, parse_order_message


class StubInterpreter:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def interpret(self, message, indexed_links):
        self.calls.append((message, indexed_links))
        return json.dumps(self.result)


def test_extract_https_links_preserves_occurrences_and_strips_punctuation():
    message = (
        "See (https://example.com/one), https://example.com/two. "
        "Again: https://example.com/one"
    )

    assert extract_https_links(message) == (
        "https://example.com/one",
        "https://example.com/two",
        "https://example.com/one",
    )


def test_parse_shared_dn_for_multiple_links():
    message = """DN Line: DN-001
https://example.com/one
https://example.com/two
"""
    interpreter = StubInterpreter(
        [
            {"index": 0, "dn_line": "DN-001"},
            {"index": 1, "dn_line": "DN-001"},
        ]
    )

    result = parse_order_message(message, interpreter)

    assert [(item.dn_line, item.link) for item in result] == [
        ("DN-001", "https://example.com/one"),
        ("DN-001", "https://example.com/two"),
    ]
    assert interpreter.calls[0][1] == (
        (0, "https://example.com/one"),
        (1, "https://example.com/two"),
    )


def test_parse_multiple_dns_and_per_link_overrides():
    message = """DN Line: DN-001
https://example.com/one

DN Line: DN-002
https://example.com/two
Nominal: ¥4,500
Items: Limited Edition Album
"""
    interpreter = StubInterpreter(
        [
            {"index": 1, "dn_line": "DN-002", "nominal": "¥4,500", "items": "Limited Edition Album"},
            {"index": 0, "dn_line": "DN-001"},
        ]
    )

    result = parse_order_message(message, interpreter)

    assert result[0].dn_line == "DN-001"
    assert result[1].dn_line == "DN-002"
    assert result[1].nominal_override == "¥4,500"
    assert result[1].items_override == "Limited Edition Album"


@pytest.mark.parametrize(
    "result, message",
    [
        ([{"index": 0, "dn_line": "DN-001"}], "map every submitted link"),
        (
            [
                {"index": 0, "dn_line": "DN-001"},
                {"index": 0, "dn_line": "DN-002"},
            ],
            "exactly once",
        ),
        (
            [
                {"index": 0, "dn_line": "DN-001"},
                {"index": 2, "dn_line": "DN-002"},
            ],
            "unknown link index",
        ),
        (
            [
                {"index": 0, "dn_line": ""},
                {"index": 1, "dn_line": "DN-002"},
            ],
            "DN Line is required",
        ),
    ],
)
def test_parse_rejects_missing_duplicate_unknown_or_invalid_mappings(result, message):
    interpreter = StubInterpreter(result)

    with pytest.raises(OrderParseError, match=message):
        parse_order_message(
            "https://example.com/one https://example.com/two",
            interpreter,
        )


def test_parse_rejects_unknown_model_fields():
    interpreter = StubInterpreter(
        [{"index": 0, "dn_line": "DN-001", "store": "invented"}]
    )

    with pytest.raises(OrderParseError, match="unsupported fields"):
        parse_order_message("https://example.com/one", interpreter)


@pytest.mark.parametrize("raw", ["not json", "{}", "[]"])
def test_parse_rejects_malformed_or_empty_model_output(raw):
    class RawInterpreter:
        def interpret(self, message, indexed_links):
            return raw

    with pytest.raises(OrderParseError):
        parse_order_message("https://example.com/one", RawInterpreter())


def test_parse_rejects_messages_without_https_links_before_calling_model():
    interpreter = StubInterpreter([])

    with pytest.raises(OrderParseError, match="No public HTTPS product links"):
        parse_order_message("DN Line: DN-001", interpreter)

    assert interpreter.calls == []
