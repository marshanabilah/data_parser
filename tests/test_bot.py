import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop, CommandHandler

from bot import (
    BotConfig,
    build_preview_pages,
    build_telegram_application,
    enforce_access,
    handle_order_callback,
    handle_order_message,
)
from models import OrderDraft, OrderPreview, SavedOrder
from order_service import OrderPreparationError


class EditableMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class Message:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        reply = EditableMessage()
        self.replies.append((text, kwargs, reply))
        return reply


class Query:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class Service:
    def __init__(self, preview=None, error=None):
        self.preview = preview
        self.error = error
        self.prepare_calls = []
        self.confirm_calls = []

    async def prepare(self, message):
        self.prepare_calls.append(message)
        if self.error:
            raise self.error
        return self.preview

    async def confirm(self, preview):
        self.confirm_calls.append(preview)
        return (
            SavedOrder(
                date="2026-10-16 14:30",
                order_no=7,
                dn_line=preview.orders[0].dn_line,
                items=preview.orders[0].items,
                link=preview.orders[0].link,
                nominal=preview.orders[0].nominal,
            ),
        )


def preview(token="token-1", item="Album"):
    return OrderPreview(
        token,
        (
            OrderDraft(
                dn_line="DN-001",
                items=item,
                link="https://example.com/item",
                nominal=None,
                warnings=("Nominal is missing",),
            ),
        ),
    )


def context(service=None, pending=None, allowed=(123,)):
    return SimpleNamespace(
        bot_data={
            "allowed_user_ids": frozenset(allowed),
            "order_service": service,
        },
        user_data={"pending_orders": pending} if pending is not None else {},
    )


def run(awaitable):
    return asyncio.run(awaitable)


def test_unauthorized_update_is_stopped_before_functional_handlers():
    message = Message()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=999),
        effective_message=message,
        callback_query=None,
    )

    with pytest.raises(ApplicationHandlerStop):
        run(enforce_access(update, context()))

    assert "999" in message.replies[0][0]


def test_preview_lists_every_sheet_field_and_escapes_untrusted_text():
    pages = build_preview_pages(preview(item="Album <script>"))

    rendered = "\n".join(pages)
    for label in (
        "Date:",
        "Order No.:",
        "DN Line:",
        "Items:",
        "Link:",
        "Nominal:",
        "Arrived Date:",
        "Status:",
        "Box:",
    ):
        assert label in rendered
    assert "Album &lt;script&gt;" in rendered
    assert "Album <script>" not in rendered


def test_message_prepares_tokenized_preview_with_only_confirm_and_cancel():
    order_preview = preview()
    service = Service(preview=order_preview)
    message = Message("DN Line: DN-001\nhttps://example.com/item")
    update = SimpleNamespace(effective_message=message)
    ctx = context(service=service)

    run(handle_order_message(update, ctx))

    assert ctx.user_data["pending_orders"]["token-1"] == order_preview
    _, edit_kwargs = message.replies[0][2].edits[0]
    buttons = edit_kwargs["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Confirm", "Cancel"]
    assert all("token-1" in button.callback_data for button in buttons)


def test_preparation_error_gives_correction_without_internal_details():
    service = Service(error=OrderPreparationError("add an Items: override"))
    message = Message("https://example.com/item")

    run(handle_order_message(SimpleNamespace(effective_message=message), context(service)))

    assert "add an Items: override" in message.replies[0][2].edits[0][0]


def test_confirm_saves_matching_preview_and_removes_it_after_success():
    order_preview = preview()
    service = Service(preview=order_preview)
    query = Query("order:confirm:token-1")
    ctx = context(service, pending={"token-1": order_preview})

    run(handle_order_callback(SimpleNamespace(callback_query=query), ctx))

    assert service.confirm_calls == [order_preview]
    assert ctx.user_data["pending_orders"] == {}
    assert "Order numbers: 7" in query.edits[0][0]


def test_cancel_and_stale_callbacks_never_save():
    order_preview = preview()
    service = Service(preview=order_preview)
    cancel = Query("order:cancel:token-1")
    ctx = context(service, pending={"token-1": order_preview})

    run(handle_order_callback(SimpleNamespace(callback_query=cancel), ctx))

    stale = Query("order:confirm:token-1")
    run(handle_order_callback(SimpleNamespace(callback_query=stale), ctx))
    assert service.confirm_calls == []
    assert "expired" in stale.edits[0][0]


def test_only_start_and_help_commands_are_registered():
    config = BotConfig(
        telegram_token="123456:ABCDEF",
        anthropic_api_key="test",
        allowed_user_ids=frozenset({123}),
        webhook_secret="secret",
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        application = build_telegram_application(config, Service(preview=preview()))
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    commands = {
        command
        for handlers in application.handlers.values()
        for handler in handlers
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }

    assert commands == {"start", "help"}
