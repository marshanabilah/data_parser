# Implementation Plan: Admin Access

Implement `SPEC-admin-access.md`: one admin signs in and out, administrative operations require a validated session, and customer pages remain public. Existing parsing and extraction may be reused by the later order-management module.

## Approach

- Choose a Python web framework and session mechanism, then document dependencies and commands.
- Configure an admin password hash and session secret outside source control. Define setup, recovery, and session lifetime before implementation.
- Enforce authentication server-side for every protected operation. Use secure cookies and protection against forged state-changing requests.
- Verify the access boundary with minimal public and protected routes before later modules add their pages.

## Order and risks

Design → login and protected access → logout and expiry → public boundary and documentation. Missing credentials must fail closed; direct requests must not bypass access checks. Keep the existing bot and tests working. Production deployment is outside this module.

## Open decisions

Use the existing Starlette stack, hashed opaque sessions in SQLite, an eight-hour lifetime, and an environment-configured password hash. Password recovery replaces that hash and revokes sessions. SQLite requires persistent storage; a multi-instance deployment would require a shared store. See `SPEC-admin-access.md` for the decisions.
