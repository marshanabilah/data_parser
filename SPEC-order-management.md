# Spec: Order Management

## Objective

Let the admin turn jastip order details into reviewable item records, assign a stable item ID to each saved record, and maintain item statuses. These records will later support customer lookup.

## Scope and behavior

- An authenticated admin pastes an order collection for parsing, including one or more product links, the associated DN Line, and the customer's LINE display name. The dashboard interprets the collection and proposes records; it does not require the admin to type every field individually.
- Reuse existing parsing and safe public HTTPS product extraction where practical. Show a preview before saving. The admin can correct the item name and price when extraction is missing or inaccurate. Do not invent missing values. A valid JPY price is required before confirmation; if extraction fails, the admin must enter it.
- Each order record has an item ID, order date, LINE display name, DN Line, shop, one or more product lines, shipping fee in JPY, and status. Each product line has an item name, product link, price in JPY, and positive whole-number quantity. Arrival date and box reference may be recorded when available.
- The admin can maintain a suggested JPY shipping fee for each shop. When creating a record, show the shop's suggestion if one exists; the admin can change the amount for that record without changing the shop suggestion. Shipping is one amount for the record and may be blank. The saved record retains its own shipping fee when a suggestion changes later.
- The admin can choose and save whether each shop allows multiple links per record; the saved choice applies to future orders and is editable. Initial suggested settings are single-link for Mercari, Rakuten, and Fril, and multi-link for UMS, Lashinbang, and Weverse. All product lines in one record share its item ID and status. Records for orders on different dates receive different IDs, including when their product links are the same.
- The order date defaults to the current date in WIB and is editable before saving to accommodate older orders.
- Assign one stable item ID per order record only when confirmed and saved. Do not reuse IDs. Duplicate links are permitted and flagged in the preview.
- Each saved record starts at **Purchased**. The admin can update its status to **Arrived in Japan**, **Shipping to Indonesia**, **Arrived in Indonesia**, or **Completed**, including moving backwards to correct a mistake. Each item record can have a different status.
- Store the LINE display name on each item record for now; a separate customer directory is out of scope. Display names are not assumed to be unique or permanent.
- Provide an admin list with search by item ID, LINE display name, and DN Line, plus a way to inspect a record and update its status.
- Restrict all order data and mutations in this module to authenticated admin requests. Public lookup is a separate module and will define its own limited disclosure.
- Payment verification and customer accounts are outside this module.

## Tech Stack

The current project uses Python, Starlette, and SQLite for admin sessions. Reuse existing parser and extractor modules where their contracts fit. The order storage and migration approach remain to be selected in the technical plan; preserve existing data and do not assume the Telegram Google Sheet has the new fields.

## Commands

```sh
.venv/bin/uvicorn dashboard:app_factory --factory --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

See `DASHBOARD-SETUP.md` for local admin configuration. Additional commands will be defined in the implementation plan if needed.

## Project Structure

Existing dashboard access is in `dashboard.py`; parsing and extraction are in `order_parser.py`, `order_service.py`, and `link_extractor.py`. Existing tests are in `tests/`. New storage, routes, views, and tests should have clear module boundaries, with exact paths determined in planning.

## Code Style

Follow existing Python type annotations and Ruff conventions. Keep parsing, persistence, and request handling separate. Validate input before saving and make failures visible to the admin without logging private order content or secrets.

## Testing Strategy

- Test validation, editable dates, quantities, ID stability, duplicate links, and all status values.
- Test preview and confirmation, including extraction failure and manual correction, with safe test data.
- Test authorized and unauthorized access to every order operation; verify URL-fetching protections remain effective.
- Verify persistence across application restarts and run the existing test suite to detect regressions.

## Boundaries

- **Always:** require admin authorization; validate input; preserve existing data; test changes.
- **Ask first:** migrate or modify real Google Sheets data, deploy, or change the agreed workflow materially.
- **Never:** expose private order details publicly through this module, commit secrets, or bypass URL safety checks.

## Success Criteria

1. An admin can preview, correct, and save order records with the fields above; a missing or invalid JPY product price prevents confirmation.
2. Single-link shops accept one link per record; multi-link shops support multiple product lines under one ID. Admin changes to a shop's link setting persist for future orders. Orders on different dates receive different IDs, and each product line retains its quantity.
3. New records default to Purchased, and the admin can update and retrieve each record's status, including moving it backwards.
4. Admin search finds matching records by ID, LINE display name, and DN Line.
5. Unauthenticated requests cannot read or change admin order data.
6. Saved records survive an application restart, and the existing test suite remains passing.
7. The admin can update a shop's suggested shipping fee and override it for an individual record without changing the suggestion or past records.

## Open Questions

None currently identified. Storage and migration details will be addressed during planning.
