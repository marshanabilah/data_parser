from starlette.testclient import TestClient

from bot import BotConfig, build_web_application


class Bot:
    async def set_webhook(self, **kwargs):
        self.webhook = kwargs


class TelegramApplication:
    def __init__(self):
        self.bot = Bot()
        self.updates = []
        self.started = False
        self.stopped = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def process_update(self, update):
        self.updates.append(update)


class HttpClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def config():
    return BotConfig(
        telegram_token="123456:ABCDEF",
        anthropic_api_key="test",
        allowed_user_ids=frozenset({123}),
        webhook_base_url="https://bot.example",
        webhook_secret="expected-secret",
    )


def test_health_and_webhook_secret_validation():
    telegram = TelegramApplication()
    app = build_web_application(telegram, config())

    with TestClient(app) as client:
        assert client.get("/").json() == {"status": "ok"}
        assert client.post("/telegram", json={}).status_code == 403
        assert (
            client.post(
                "/telegram",
                json={},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            ).status_code
            == 403
        )


def test_webhook_rejects_invalid_json_and_processes_valid_update():
    telegram = TelegramApplication()
    app = build_web_application(telegram, config())
    headers = {"X-Telegram-Bot-Api-Secret-Token": "expected-secret"}

    with TestClient(app) as client:
        invalid = client.post(
            "/telegram",
            content=b"not-json",
            headers={**headers, "content-type": "application/json"},
        )
        valid = client.post(
            "/telegram",
            json={"update_id": 123},
            headers=headers,
        )

    assert invalid.status_code == 400
    assert valid.status_code == 200
    assert len(telegram.updates) == 1


def test_lifespan_registers_webhook_and_closes_clients():
    telegram = TelegramApplication()
    http_client = HttpClient()
    app = build_web_application(telegram, config(), http_client)

    with TestClient(app):
        assert telegram.started is True
        assert telegram.bot.webhook["secret_token"] == "expected-secret"

    assert telegram.stopped is True
    assert http_client.closed is True
