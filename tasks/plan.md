# Implementation Plan: Telegram Order Link Intake, Version 1

## Overview

Replace the current sales parser with a tested order-intake pipeline. Telegram
messages become validated order drafts, public product pages supply missing item
names and prices, users review a complete preview, and confirmed batches are
written to the fixed `Orders` worksheet with permanent sequential IDs.

This plan preserves the existing Cloud Run webhook and Telegram allowlist. It
does not deploy the service or change live Google Sheets data.

## Current-State Findings

- `bot.py` combines configuration, Claude parsing, Telegram presentation,
  confirmation state, and the HTTP webhook in one module.
- The current prompt and sheet schema describe general retail sales rather than
  linked orders.
- `sheets.py` supports user-selected tabs and stores `active_tab` in
  `_BotConfig`; version 1 instead needs one `Orders` tab and sequence state.
- Synchronous Anthropic and gspread calls currently run inside async handlers and
  can block the webhook event loop.
- There is no automated test suite, lint configuration, or external-page
  fetching implementation.
- The current container copies only `bot.py` and `sheets.py`, so it must include
  the new modules.

## Dependency Graph

```text
Typed order contracts and test tooling
        |
        +--> Flexible message parser
        |
        +--> Safe public-page extractor
        |
        +--> Orders worksheet repository and ID reservation
                    |
                    v
          Order intake orchestration
                    |
                    v
       Telegram preview and confirmation flow
                    |
                    v
          Deployment and user documentation
```

## Architecture Decisions

### 1. Validated contracts at module boundaries

Use frozen dataclasses for parsed link requests, enriched order drafts, preview
rows, and saved rows. Claude, HTML, Telegram, and Google Sheets data must be
converted into these contracts before crossing module boundaries. This removes
the current unvalidated dictionary flow.

### 2. Deterministic extraction before AI

Extract URLs deterministically from the user message. Use Claude only to map the
extracted URLs to DN lines and optional overrides. Validate that every model
returned URL exactly matches one submitted URL, appears once, and has one DN
line. Reject malformed or ambiguous output rather than repairing it silently.

For product pages, use JSON-LD first, then Open Graph metadata, then headings and
the HTML title. Send only bounded, cleaned text to Claude as a final item-name
fallback. A model response never overrides explicit user data or a stronger
metadata source.

### 3. SSRF-resistant manual redirect handling

Accept only HTTPS URLs. Resolve each destination hostname and reject every
non-global address before making a request. Disable automatic redirects, inspect
and revalidate each redirect target, cap redirects at five, cap response bodies
at 2 MiB, and use a 10-second total timeout. Do not forward cookies,
authorization, or origin credentials.

DNS validation and the subsequent connection are not an atomic operation with
the standard `httpx` resolver. Tests cover the validation behavior, while the
residual DNS-rebinding window is recorded as a deployment risk. Eliminating that
window would require controlled egress or a pinned resolver/transport beyond the
approved version 1 scope.

### 4. Fixed worksheet repository

Replace active-tab functions with an `Orders` repository. It owns the exact
nine-column header, duplicate URL lookup, contiguous batch append, and the
`next_order_no` setting in hidden `_BotConfig`.

Under the application lock, read the next ID and processed-submission ledger,
then use one Google Sheets `batchUpdate` request to append the order rows, advance
the sequence, and record the submission token together. A failed request changes
neither rows nor sequence state. The application serializes confirmations with
an async lock and Cloud Run uses one instance with request concurrency `1`.

### 5. Explicit orchestration service

An order-intake service coordinates message parsing, page extraction, validation,
duplicate lookup, preview generation, and confirmation. Telegram handlers only
translate updates into service calls and render results. Blocking Anthropic and
gspread calls run through `asyncio.to_thread`; page fetching uses async `httpx`.

### 6. Pending submission identity

Each preview receives a random submission token embedded in callback data.
Pending drafts are stored per Telegram user under that token. Clicking an old
preview cannot confirm a newer submission, fixing the current single
`pending_rows` overwrite hazard.

### 7. Dependencies and verification tooling

Add `beautifulsoup4` for tolerant metadata extraction. Add a separate
`requirements-dev.txt` containing `pytest`, `pytest-asyncio`, `respx`, and
`ruff`. Pin all new packages during implementation after resolving compatible
versions against Python 3.12 and the existing runtime packages.

## Implementation Phases

### Phase 1: Contracts and high-risk foundations

1. Establish test tooling and typed order contracts.
2. Build and test flexible DN/link message parsing.
3. Build and test safe public-page extraction.

Checkpoint: contracts, parser, and extractor pass focused offline tests.

### Phase 2: Persistence and complete order pipeline

4. Replace tab-based sheet access with the fixed Orders repository.
5. Build the order-intake orchestration service.
6. Replace the sales Telegram flow with tokenized order preview and confirmation.

Checkpoint: an offline integration test covers message to preview to confirmed
rows, including cancellation and authorization.

### Phase 3: Operational hardening and handoff

7. Harden webhook execution, logs, error handling, and container packaging.
8. Rewrite setup, deployment, and usage documentation for the order workflow.
9. Run the full quality and security verification matrix.

Checkpoint: tests, lint, compilation, container build, and documented manual
smoke checks pass before deployment is considered.

Detailed acceptance criteria, verification commands, dependencies, and file
scope are recorded in `tasks/todo.md`.

## Verification Checkpoints

### Foundation checkpoint after Tasks 1-3

- All unit tests pass without network access.
- Parser ambiguity and override precedence match `SPEC.md`.
- URL validation rejects private and unsafe destinations before fetching.
- Page extraction precedence and limits are covered by fixtures.

### Pipeline checkpoint after Tasks 4-6

- Fake-sheet integration creates the exact nine-column rows.
- Concurrent in-process confirmations receive unique ID ranges.
- Multi-link preview, duplicate warnings, confirm, cancel, stale callbacks, and
  unauthorized access are tested.
- No tab-management handler remains registered.

### Release-readiness checkpoint after Tasks 7-9

- `pytest`, Ruff, and compile checks pass.
- The Docker image builds with every required source module.
- The README matches the implemented message format and fixed worksheet.
- The diff contains no credentials or captured merchant content.
- A controlled manual smoke-test checklist is ready for the owner; deployment
  itself requires explicit approval and credentials.

## Parallelization

After Task 1 fixes the shared contracts, Tasks 2 and 3 are technically
independent. The remaining tasks are sequential because they share the order
contracts, persistence interface, orchestration behavior, and Telegram callback
contract. No parallel-agent work is required for this small repository.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Merchant HTML varies or changes | High | Metadata precedence, fixture coverage, manual overrides, no site guarantee |
| Merchant blocks server requests | Medium | Clear unsupported result; require `Items:` and optionally `Nominal:` |
| SSRF through DNS or redirects | High | HTTPS-only validation, global-IP checks per hop, manual redirects, strict limits |
| DNS rebinding between validation and connection | Medium | Record residual risk; use controlled egress/pinned transport only in later scope |
| Claude maps a DN to the wrong link | High | Constrain prompt, validate URL set and cardinality, show full preview, block ambiguity |
| Duplicate order numbers | High | Persistent reservation, in-process lock, one instance, concurrency `1` |
| Rolling revisions overlap | Medium | Deploy during a quiet window; a cross-instance transactional store requires separate approval |
| Google write succeeds but response is lost | Medium | Store the submission token in `_BotConfig` in the same Sheets batch update and return the prior result on retry |
| Telegram preview exceeds message length | Medium | Paginate or reject oversized submissions with a clear maximum established during implementation |
| Existing `Sales` data is altered | High | Create/use only `Orders`; do not migrate, delete, or rewrite existing tabs |
| Logs expose order data | High | Log event IDs, counts, domains, and error classes only; never raw messages, URLs, page text, or credentials |

## Rollout and Rollback

1. Run all tests against fakes and local fixtures.
2. Use a separate test spreadsheet for the manual smoke test.
3. Deploy a new Cloud Run revision with concurrency `1` and no production
   traffic, then validate its health endpoint.
4. Register the webhook and perform one controlled order submission.
5. Inspect the resulting `Orders` row and `_BotConfig` sequence value.
6. Route production traffic only after the owner approves the smoke result.

Rollback routes traffic to the previous Cloud Run revision. The new `Orders` tab
and `_BotConfig` sequence entry are retained because deleting sheet data is not
part of rollback. Any test rows are removed manually only with explicit owner
approval.

## Open Questions

None blocking planning. Implementation must not start until this plan and the
task list are approved.
