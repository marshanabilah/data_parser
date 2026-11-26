# Implementation Plan: Order Management

## Overview

Implement `SPEC-order-management.md` behind the existing admin login. An admin pastes an order collection, reviews and corrects parsed shop-order records, then saves them with stable IDs. Each record contains one or more product lines according to the shop's saved link rule and has an independently editable status.

## Architecture and decisions

- Preserve the existing Telegram flow. Reuse its parser and safe product fetcher where suitable, adapting their contracts for customer names, shop grouping, quantities, and required JPY prices rather than assuming the current one-link-per-draft model is sufficient.
- Use transactional SQLite tables for shop settings, order records, and product lines as a proposed local-first store. Assign IDs on confirmed save and use a unique confirmation token to prevent duplicate saves. The production host must provide durable storage; multi-instance hosting needs a shared transactional store.
- Keep preview editable, validate all records again on the server at confirmation, and save a valid batch atomically. Never fetch unsafe URLs; preserve existing extraction safeguards. Missing product names or valid JPY prices require correction before saving.
- Store each record's shipping fee independently of the shop suggestion. Blank is allowed. Persist admin-managed link rules and suggested fees, including initial shop settings from the spec.
- Guard every admin operation server-side and protect state-changing requests. Public customer lookup and price estimation remain separate modules.

## Dependency order

1. Define and test record validation and grouping contracts.
2. Add transactional order and shop-setting persistence.
3. Add the admin shop-settings workflow.
4. Connect parsing and extraction to an editable preview and confirmed save.
5. Add admin search, detail, and status updates.
6. Verify end-to-end, document setup, and review.

## Risks and mitigations

- **Grouping ambiguity:** require the admin to review and correct groupings before confirmation; never silently combine records across dates or customers.
- **Parsing failures and merchant restrictions:** retain manual correction and existing safe-fetch limits; do not fabricate prices.
- **Duplicate saves:** use transactional confirmation and test retries and concurrent saves.
- **Existing data:** do not import or alter the Telegram Google Sheet without authorization; document any future migration separately.
- **Access and privacy:** test direct unauthenticated requests and avoid logging private order content.

## Open design decisions

Determine how pasted input identifies each shop and how admins split or merge proposed groups during preview. Define the exact parsed input contract and UI interactions before implementing intake; preserve the user's ability to correct ambiguous groupings. Confirm durable hosting separately before deployment.
