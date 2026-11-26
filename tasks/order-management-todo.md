# Tasks: Order Management

## Task 1: Define record and grouping rules

Define validated records, product lines, shop link rules, JPY amounts, dates, and statuses. Design how preview grouping is corrected.

**Acceptance:**
- [ ] Single-link and multi-link rules, required product prices, optional shipping, quantities, and WIB dates have explicit validation.
- [ ] Grouping cannot silently combine different customers or dates, and ambiguous groups can be corrected.

**Verify:** Focused unit tests for validation and grouping edge cases. **Dependencies:** None. **Files:** models, grouping logic, tests. **Scope:** Medium.

## Task 2: Persist records and shop settings

Add transactional storage for shop settings, order records, product lines, and confirmation tokens.

**Acceptance:**
- [ ] IDs are stable and not reused; retrying confirmation does not duplicate records; a failed batch writes nothing.
- [ ] Shop settings persist and saved shipping values do not change when suggestions change.

**Verify:** Repository tests for restart, retries, concurrent confirmation, and rollback. **Dependencies:** Task 1. **Files:** persistence module, schema, tests. **Scope:** Medium.

## Checkpoint: Data foundation

- [ ] Focused and existing tests pass; review grouping and storage decisions.

## Task 3: Manage shop settings

Provide authenticated admin controls for each shop's link rule and suggested JPY shipping fee.

**Acceptance:**
- [ ] Admin can view and change rules and suggestions; initial named shops use the specified defaults.
- [ ] Unauthorized requests cannot read or modify settings.

**Verify:** Integration tests and manual admin workflow. **Dependencies:** Task 2. **Files:** routes, views, tests. **Scope:** Medium.

## Task 4: Parse and review orders

Adapt existing intake to create an editable preview of proposed order records and product lines.

**Acceptance:**
- [ ] Pasted collections are parsed and enriched; admin can correct missing details and grouping before saving.
- [ ] Unsafe URLs are rejected; missing or invalid required JPY prices prevent confirmation.

**Verify:** Parser and route tests for multi-link shops, extraction failures, correction, and URL safety. **Dependencies:** Tasks 1–3. **Files:** intake adapter, routes, preview view, tests. **Scope:** Medium.

## Task 5: Confirm and save preview

Connect server-validated preview confirmation to transactional persistence.

**Acceptance:**
- [ ] Confirmed records get stable IDs, Purchased status, and chosen order date and shipping values.
- [ ] Repeated or invalid confirmation cannot duplicate or partially save records.

**Verify:** Integration tests for confirmation, retry, and rollback. **Dependencies:** Tasks 2 and 4. **Files:** routes, persistence integration, tests. **Scope:** Medium.

## Checkpoint: Intake workflow

- [ ] Review a complete paste → preview → correction → confirmation flow; all tests pass.

## Task 6: Search and update status

Add authenticated list and detail views with status editing.

**Acceptance:**
- [ ] Search finds records by item ID, LINE display name, and DN Line.
- [ ] Admin can update status in either direction; unauthenticated access is rejected.

**Verify:** Integration tests and manual search/status flow. **Dependencies:** Task 5. **Files:** routes, views, tests. **Scope:** Medium.

## Task 7: Documentation and final verification

- [ ] Document local setup, shop configuration, intake, status editing, and storage limits.
- [ ] All spec success criteria and regression checks pass.

**Verify:** Full pytest suite, Ruff, and end-to-end browser checks. **Dependencies:** Tasks 3–6. **Files:** README, setup docs, tests as needed. **Scope:** Small.

## Checkpoint: Review

- [ ] Review the completed module before moving to customer lookup.
