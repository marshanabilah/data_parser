# Dashboard access: local setup

Run `.venv/bin/python dashboard_setup.py` and enter a strong password twice. Set `ADMIN_PASSWORD_HASH` to the printed value and `DASHBOARD_SECRET` to a separately generated random secret of at least 32 characters. Keep both private and outside version control. Set `DASHBOARD_INSECURE_LOCAL=1` only for local HTTP testing.

Start with `.venv/bin/uvicorn dashboard:app_factory --factory --host 127.0.0.1 --port 8000`. Visit `/login` to sign in and `/admin` for the protected placeholder. The public page is `/`.

Set `OPENROUTER_API_KEY` in the server environment to use order parsing. The dashboard uses `openrouter/free`, with no paid-model fallback. The pasted collection and product links are sent to OpenRouter for parsing, so avoid including information you do not want to share with the service. Free usage is rate-limited. In Shop settings, set each shop's link rule and optional suggested shipping fee. On Parse an order, enter a LINE name and paste one customer's collection from one shop. Review the detected shop; if it is new, choose its link rule and it will be saved on confirmation. Multi-link submissions for that shop become one record, so submit separate orders separately. Check the parsed fields and enter any missing JPY prices before confirming. Search and update statuses from Orders.

To reset the password, generate a new hash and replace `ADMIN_PASSWORD_HASH`, then restart the application. Previous sessions become invalid. The SQLite session database defaults to `dashboard.sqlite3`; set `DASHBOARD_DB` to change its path.

This is a local development foundation. Before public deployment, configure HTTPS, persistent session storage, trusted proxy handling, and protection against repeated password attempts. Do not enable the local HTTP setting in production.
