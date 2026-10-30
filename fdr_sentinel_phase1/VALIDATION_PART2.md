# Part 2 implementation and validation report

## Changes included

- Dashboard portfolio pulse no longer uses an illustrative/sample trend. It charts live maturity buckets returned by the analytics API.
- Reports & Analytics now consumes `/api/v1/analytics/overview`, with server-side status, institution, bank, and maturity-date filters.
- CSV export uses `/api/v1/analytics/export.csv`, applies the same filters, streams all matching rows, and respects institution-level visibility.
- Analytics totals, institution breakdown, maturity buckets, document counts, open reminders, and open release requests are calculated from the filtered database records visible to the signed-in user.
- A local single-process scheduler evaluates lifecycle milestones at startup and hourly. The authorised manual evaluation endpoint remains available.
- Reminder reconciliation refreshes due dates when maturity dates change, reopens applicable reminders, and marks milestones not yet due as scheduled.
- Added `PATCH /api/v1/fdrs/{id}/maturity` with role checks, institution scoping, and audit logging.
- Removed frontend reliance on caller-supplied demo role/user headers for workflow actions; the shared API client sends the signed-in bearer token and strips demo identity headers.
- Added tenant scoping to document extraction, latest extraction, field assertions, lifecycle detail, document upload, and replacement links.
- Maker-checker workflow continues to enforce independent approval and state transitions. Closing the workflow only records an offline-confirmed state; it does not move money.

## Validation results

- Backend automated tests: **23 passed**.
- Python backend compilation: **passed**.
- Frontend TSX syntax parse: **passed (0 parse diagnostics)**.
- ZIP integrity: checked after packaging.
- Frontend `npm install` / production build: **not verified**. Package installation timed out in this environment, so a full Vite/TypeScript build and browser interaction test could not be completed here.

## Known limitations

- The hourly scheduler is a local single-process demo scheduler. Production deployments with multiple workers should use a durable external scheduler/queue to avoid duplicate concurrent jobs.
- Reminders are persisted in-app only; no email/SMS provider is configured.
- Release/refund does not execute a bank transaction. It records workflow states and offline confirmation only.
- This remains a prototype. Production use requires managed SSO/secrets, private encrypted document storage, database migrations/backups, deployment hardening, and independent security review.
- Existing SQLite databases are not automatically cleaned or reseeded. The six-record sample dataset applies to a fresh database.
