# Dashboard access: local setup

Run `.venv/bin/python dashboard_setup.py` and enter a strong password twice. Set `ADMIN_PASSWORD_HASH` to the printed value and `DASHBOARD_SECRET` to a separately generated random secret of at least 32 characters. Keep both private and outside version control. Set `DASHBOARD_INSECURE_LOCAL=1` only for local HTTP testing.

Start with `.venv/bin/uvicorn dashboard:app_factory --factory --host 127.0.0.1 --port 8000`. Visit `/login` to sign in and `/admin` for the protected placeholder. The public page is `/`.

Set `ANTHROPIC_API_KEY` in the server environment to use order parsing. In Shop settings, set each shop's link rule and optional suggested shipping fee. On Parse an order, select one shop and enter one customer's collection: multi-link submissions for that shop become one record, so submit separate orders separately. Check the parsed fields and enter any missing JPY prices before confirming. Search and update statuses from Orders.

To reset the password, generate a new hash and replace `ADMIN_PASSWORD_HASH`, then restart the application. Previous sessions become invalid. The SQLite session database defaults to `dashboard.sqlite3`; set `DASHBOARD_DB` to change its path.

This is a local development foundation. Before public deployment, configure HTTPS, persistent session storage, trusted proxy handling, and protection against repeated password attempts. Do not enable the local HTTP setting in production.
