import asyncio
import logging
from types import SimpleNamespace

from bot import handle_order_message
from models import OrderDraft, OrderPreview


class Message:
    def __init__(self, text):
        self.text = text

    async def reply_text(self, text, **kwargs):
        return self

    async def edit_text(self, text, **kwargs):
        return None


class Service:
    async def prepare(self, message):
        return OrderPreview(
            "opaque-token",
            (
                OrderDraft(
                    dn_line="DN-SECRET",
                    items="Secret Product",
                    link="https://merchant.example/private-order",
                    nominal="JPY 9,999",
                ),
            ),
        )


def test_success_logs_do_not_include_message_urls_or_scraped_content(caplog):
    sensitive_message = "DN-SECRET https://merchant.example/private-order"
    update = SimpleNamespace(effective_message=Message(sensitive_message))
    context = SimpleNamespace(
        bot_data={"order_service": Service()},
        user_data={},
    )

    with caplog.at_level(logging.INFO):
        asyncio.run(handle_order_message(update, context))

    output = caplog.text
    assert sensitive_message not in output
    assert "merchant.example" not in output
    assert "Secret Product" not in output
    assert "JPY 9,999" not in output
