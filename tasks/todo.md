# Tasks: Telegram Order Link Intake, Version 1

## Phase 1: Contracts and high-risk foundations

## Task 1: Add test tooling and typed order contracts

**Description:** Establish the offline test harness and the validated data types
shared by parsing, extraction, persistence, and Telegram presentation.

**Acceptance criteria:**

- [x] Frozen typed models represent submitted links, overrides, enriched drafts,
  preview warnings, and saved rows.
- [x] Validation rejects missing DN lines, missing item names, invalid URLs, and
  malformed nominal values without inventing defaults.
- [x] Runtime and development dependencies are separated and pinned.

**Verification:**

- [x] Tests pass: `.venv/bin/python -m pytest -q tests/test_models.py`
- [x] Lint passes: `.venv/bin/python -m ruff check models.py tests/test_models.py`
- [x] Compilation succeeds: `PYTHONPYCACHEPREFIX=/tmp/data-parser-pycache .venv/bin/python -m compileall -q models.py`

**Dependencies:** None

**Files likely touched:**

- `models.py`
- `requirements.txt`
- `requirements-dev.txt`
- `tests/test_models.py`

**Estimated scope:** Medium, 4 files

## Task 2: Parse flexible multi-link Telegram submissions

**Description:** Extract submitted HTTPS URLs deterministically, use Claude to
associate each URL with one DN line and optional per-link overrides, and validate
the returned mapping against the original message.

**Acceptance criteria:**

- [ ] Shared DN headings, per-link DN values, ordered pairing, and multiple DNs
  in one message produce the expected link drafts.
- [ ] Explicit `Items:` and `Nominal:` overrides stay attached to the intended
  link and remain authoritative.
- [ ] Missing, duplicate, unknown, or ambiguous URL/DN mappings return a clear
  unresolved result and never fabricate a pairing.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_order_parser.py`
- [ ] Lint passes: `.venv/bin/python -m ruff check order_parser.py tests/test_order_parser.py`
- [ ] Manual fixture check covers both approved examples from `SPEC.md`.

**Dependencies:** Task 1

**Files likely touched:**

- `order_parser.py`
- `models.py`
- `tests/test_order_parser.py`

**Estimated scope:** Medium, 3 files

## Task 3: Fetch and extract public product metadata safely

**Description:** Implement bounded HTTPS fetching with per-hop destination
validation and extract item names and displayed prices in the approved
precedence order.

**Acceptance criteria:**

- [ ] JSON-LD, Open Graph, heading, title, and bounded AI fallback precedence is
  deterministic and preserves original displayed currency text.
- [ ] Non-HTTPS, non-public IP, unsafe redirect, excessive redirect, oversized
  response, timeout, and non-HTML cases fail with typed errors before unsafe use.
- [ ] Requests do not forward cookies, authorization, or submitted credentials,
  and tests make no live network calls.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_link_extractor.py`
- [ ] Lint passes: `.venv/bin/python -m ruff check link_extractor.py tests/test_link_extractor.py`
- [ ] Mocked redirect tests prove every destination is revalidated.

**Dependencies:** Task 1

**Files likely touched:**

- `link_extractor.py`
- `models.py`
- `tests/test_link_extractor.py`
- `requirements.txt`

**Estimated scope:** Medium, 4 files

## Checkpoint: Foundation

- [ ] Tasks 1-3 focused tests pass together.
- [ ] The approved message examples parse correctly.
- [ ] All URL safety cases fail closed.
- [ ] No test performs a live external request.
- [ ] Human reviews the foundation before persistence work begins.

## Phase 2: Persistence and complete order pipeline

## Task 4: Implement the fixed Orders worksheet repository

**Description:** Replace active-tab sales persistence with the exact Orders
schema, durable ID reservation, duplicate-link lookup, and one-call batch append.

**Acceptance criteria:**

- [ ] The repository creates or validates `Orders` with the exact nine headers
  and never modifies existing `Sales` or unrelated tabs.
- [ ] `_BotConfig` supplies permanent monotonically increasing order-number
  ranges that are not reused after a deleted row.
- [ ] Duplicate links are reported as warnings, and one Sheets batch update
  appends the validated rows, advances the sequence, and records the submission
  token atomically with WIB timestamps and approved defaults.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_sheets.py`
- [ ] Lint passes: `.venv/bin/python -m ruff check sheets.py tests/test_sheets.py`
- [ ] Concurrent fake-repository tests receive unique, non-overlapping ID ranges.

**Dependencies:** Task 1

**Files likely touched:**

- `sheets.py`
- `models.py`
- `tests/test_sheets.py`

**Estimated scope:** Medium, 3 files

## Task 5: Build the order-intake orchestration service

**Description:** Join parsing, overrides, product extraction, required-field
validation, duplicate lookup, and confirmation persistence behind one testable
service interface.

**Acceptance criteria:**

- [ ] One service call transforms a valid multi-link message into ordered preview
  rows with missing-price and duplicate warnings.
- [ ] Missing item names, ambiguous DN mappings, and unsafe URLs block the entire
  submission before persistence.
- [ ] Confirmation is idempotent per submission token and saves the complete
  validated batch once.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_order_service.py`
- [ ] Lint passes: `.venv/bin/python -m ruff check order_service.py tests/test_order_service.py`
- [ ] Integration fixture covers valid, unresolved, duplicate, cancel, and retry cases.

**Dependencies:** Tasks 2, 3, and 4

**Files likely touched:**

- `order_service.py`
- `models.py`
- `tests/test_order_service.py`

**Estimated scope:** Medium, 3 files

## Task 6: Replace sales handlers with order preview and confirmation

**Description:** Connect the order service to Telegram, remove tab management,
render safe paginated previews, and bind callback actions to unique submission
tokens.

**Acceptance criteria:**

- [ ] `/start` and `/help` document link, DN, and override usage; sales and tab
  commands are no longer registered.
- [ ] Every preview displays all proposed fields, warnings, and only Confirm and
  Cancel actions; stale callbacks cannot confirm a different submission.
- [ ] Access control runs before parsing or fetching, and user-facing failures
  provide corrective steps without internal exception text.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_bot.py`
- [ ] Lint passes: `.venv/bin/python -m ruff check bot.py tests/test_bot.py`
- [ ] Integration test covers unauthorized, preview, confirm, cancel, and stale callback updates.

**Dependencies:** Task 5

**Files likely touched:**

- `bot.py`
- `order_service.py`
- `tests/test_bot.py`

**Estimated scope:** Medium, 3 files

## Checkpoint: Order pipeline

- [ ] Full offline test suite passes.
- [ ] A fake-sheet end-to-end test writes exact schema rows for multiple links.
- [ ] Validation failures and cancellation write no order rows.
- [ ] Concurrent confirmations cannot duplicate order numbers in one running instance.
- [ ] Human reviews the end-to-end behavior before operational hardening.

## Phase 3: Operational hardening and handoff

## Task 7: Harden runtime execution and container packaging

**Description:** Ensure blocking SDK calls do not block the webhook loop, protect
logs and errors, include every module in the image, and enforce Cloud Run's
single-concurrency sequence constraint.

**Acceptance criteria:**

- [ ] Anthropic and gspread operations run off the event loop while HTTP fetching
  remains async and bounded.
- [ ] Logs contain no raw Telegram messages, submitted URLs, scraped content,
  credentials, or model responses.
- [ ] The container includes all runtime modules, starts on `$PORT`, and the
  documented deploy command uses maximum instances `1` and concurrency `1`.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q tests/test_webhook.py tests/test_logging.py`
- [ ] Compilation succeeds: `.venv/bin/python -m compileall -q bot.py models.py order_parser.py link_extractor.py order_service.py sheets.py`
- [ ] Container builds: `docker build -t telegram-order-bot:local .`

**Dependencies:** Task 6

**Files likely touched:**

- `bot.py`
- `Dockerfile`
- `tests/test_webhook.py`
- `tests/test_logging.py`

**Estimated scope:** Medium, 4 files

## Task 8: Update order workflow and deployment documentation

**Description:** Replace the sales-oriented README with the implemented order
schema, flexible input examples, manual fallback behavior, safety limits, and
Cloud Run deployment instructions.

**Acceptance criteria:**

- [ ] README examples cover one shared DN, multiple DNs, multiple links, and
  manual `Items:` and `Nominal:` overrides.
- [ ] Sheet schema, defaults, duplicate warnings, unsupported-page behavior, and
  manual fulfillment updates match `SPEC.md` and the implementation.
- [ ] Setup and deployment commands include the fixed worksheet, allowlist,
  webhook secret, maximum instances `1`, and concurrency `1`.

**Verification:**

- [ ] Documentation commands are checked against actual filenames and CLI flags.
- [ ] `rg -n "sales|settab|currenttab|listtabs" README.md` returns no stale feature documentation except explicit migration notes.
- [ ] Secret placeholders contain no real credential values.

**Dependencies:** Task 7

**Files likely touched:**

- `README.md`
- `.env.example`

**Estimated scope:** Medium, 3 files

## Task 9: Run final quality, security, and smoke verification

**Description:** Execute the complete offline verification matrix, inspect the
diff for secrets and scope drift, and prepare a controlled manual smoke-test
checklist without deploying.

**Acceptance criteria:**

- [ ] Every success criterion in `SPEC.md` maps to a passing automated test or a
  documented manual check.
- [ ] Tests, lint, compilation, and container build pass from a clean environment.
- [ ] Security review finds no credential leak, unsafe URL path, raw order log,
  or live external dependency in automated tests.

**Verification:**

- [ ] Tests pass: `.venv/bin/python -m pytest -q`
- [ ] Lint passes: `.venv/bin/python -m ruff check .`
- [ ] Compilation succeeds: `.venv/bin/python -m compileall -q bot.py models.py order_parser.py link_extractor.py order_service.py sheets.py`
- [ ] Container builds: `docker build -t telegram-order-bot:local .`
- [ ] Diff passes: `git diff --check`

**Dependencies:** Task 8

**Files likely touched:**

- `tests/`
- `README.md`
- `tasks/todo.md`

**Estimated scope:** Medium, 3 areas

## Checkpoint: Ready for implementation review

- [ ] All specification success criteria are accounted for.
- [ ] Every task and checkpoint is complete.
- [ ] No credentials or merchant page captures are tracked.
- [ ] Manual smoke-test steps use a test spreadsheet.
- [ ] Deployment remains a separate, explicitly authorized action.
