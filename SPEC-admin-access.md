# Spec: Admin Access

## Objective

Protect dashboard administration for one admin while allowing visitors to use the customer-facing estimator and item lookup without an account.

## Scope

- Provide one admin login and a way to sign out.
- Require an authenticated admin session for administrative pages and their operations, including order management and exchange-rate changes.
- Keep customer-facing estimator and lookup pages accessible without login. Those modules define what information they expose.
- Reject incorrect credentials without granting access. Do not expose credentials in client code, logs, or the repository.
- Expire sessions and require login again; the expiry duration and credential setup and recovery procedure remain open questions.
- Authentication must be checked on the server for protected operations, not just by hiding interface controls.

## Tech Stack

Use the existing Python and Starlette dependencies. Store opaque, random session identifiers as hashes in a SQLite database, with server-side expiry and revocation. The admin supplies a password hash and a separate application secret through environment configuration; neither belongs in source control. Run behind HTTPS in production.

## Commands

Existing project checks, from the repository root:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

Development command: `.venv/bin/uvicorn dashboard:app_factory --factory --host 127.0.0.1 --port 8000`.

## Project Structure

The current Python modules and `tests/` directory remain in place. Locations for dashboard routes, session handling, templates or frontend code, and their tests will be determined in planning.

## Code Style

Follow existing Python conventions, type annotations, and Ruff rules. Keep access checks explicit and central rather than duplicating them in each handler. For example, an authenticated server-side handler should obtain the admin identity from a validated session before changing protected data.

## Testing Strategy

- Test valid and invalid login, logout, and session expiry.
- Verify unauthenticated requests cannot read protected admin data or perform protected changes, including direct requests that bypass the interface.
- Verify public customer pages remain accessible without a session.
- Test that credentials and session secrets are not included in application logs or responses.

## Boundaries

- **Always:** validate authentication server-side; protect secrets; run relevant tests.
- **Ask first:** decisions that materially change account recovery, access for additional admins, or production deployment.
- **Never:** commit credentials; expose admin data to unauthenticated visitors; rely on hidden buttons as access control.

## Success Criteria

1. The configured single admin can sign in and access protected dashboard operations.
2. Incorrect credentials and missing or expired sessions cannot access protected operations.
3. Signing out invalidates the session for subsequent protected requests.
4. A visitor without a session can access the customer estimator and lookup when those modules are available.
5. Credentials and session secrets are absent from source control, responses, and logs.

## Open Questions

- The admin will generate a new password hash locally and replace the configured hash to set or recover the password. Changing it must revoke existing sessions.
- Sessions expire eight hours after login. Logging out revokes the current session.
- SQLite storage must be persistent across restarts; production deployment must provide a persistent volume or replace it with a suitable shared store. Deployment details remain open.
