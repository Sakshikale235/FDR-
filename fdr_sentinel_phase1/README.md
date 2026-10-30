# FDR Sentinel X — All Four Phases + Part 1 Updates

This archive continues the previously supplied **FDR Sentinel X All Phases** project and adds Part 1: authenticated AICTE/institution login, a reduced shared demo portfolio, and document-first FDR registration with OCR candidate extraction and human confirmation. The original Phase 1–4 dashboard and workflows are retained.

## Prerequisites
- Python 3.10+
- Node.js 20+ and npm

## Run on Windows PowerShell
### Backend
```powershell
cd fdr_sentinel_phase1\backend
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:FDR_TOKEN_SECRET = "replace-with-a-long-random-secret-before-deployment"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
### Frontend
```powershell
cd fdr_sentinel_phase1\frontend
npm install
npm run dev
```
Open http://localhost:5173.

## Demo accounts
| Access | Email | Password |
|---|---|---|
| AICTE administrator | admin@aicte.demo | AICTE@123 |
| AICTE scrutiny officer | officer@aicte.demo | Officer@123 |
| AICTE approving authority | authority@aicte.demo | Authority@123 |
| AICTE auditor | auditor@aicte.demo | Auditor@123 |
| ABC Institute | accounts@abc.demo | Institute@123 |
| XYZ Institute | accounts@xyz.demo | Institute@123 |

Change demo passwords and set a strong `FDR_TOKEN_SECRET` before any deployment. The local prototype uses signed, expiring bearer sessions and PBKDF2 password hashes; this is not a substitute for production SSO, managed secrets, HTTPS, rate limiting, MFA or a security review.

## Part 1 changes included
1. **Login:** Login screen replaces the top-right demo role switch. The API derives the role and identity from a signed bearer token; caller-controlled `x-demo-role` and `x-demo-user` headers are ignored.
2. **Institution scope:** Institution users are associated with one institution. Registry listing, FDR detail, dashboard summary, analytics overview, institution list, lifecycle action/reminder lists, release-request list and audit event list are scoped for institution users. Backend checks also prevent registering for another institution.
3. **Smaller dataset:** Fresh demo databases seed 5 institutions and 6 FDR records only. The same database records feed the existing pages. Existing local databases are not automatically truncated; back them up and reset only if you want the six-record seed.
4. **Document-first registration:** New FDR begins with PDF/JPG/PNG upload. The API stores and hashes the source, runs the existing advisory extraction, then displays extracted candidates in editable fields. The user corrects missing/wrong values and confirms before the FDR and document are saved together. Extraction does not authenticate the FDR or mark bank verification confirmed.
5. **Human correction and provenance:** Existing per-record extraction/review remains available. Intake candidate values are stored as OCR field assertions; final values are confirmed by the user.

## Test and build
Backend validation from `backend/`:
```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q app tests
```
Frontend validation from `frontend/`:
```powershell
npm install
npm run build
```
The backend suite includes authentication, institution data isolation, spoofed-role-header rejection, document-first extraction/confirmation, duplicate guard, Phase 2 evidence/review, Phase 3 lifecycle/maker-checker and Phase 4 analytics. Backend validation completed with 19 passing tests and Python compile checks. npm registry access timed out in this environment, so the frontend production build remains unverified; run the two frontend commands locally before relying on the modified UI.

## Important remaining limitations
- Demo users/passwords are intentionally included for local evaluation only. No public registration or password reset flow is provided.
- Production identity provider/SSO, MFA, password rotation, rate limiting, secure cookies, CSRF strategy, secret management and independent security testing remain required.
- Institution scoping has been added to the primary registry and list/read surfaces; review every newly added endpoint before production deployment.
- Reminder automation still requires a scheduler/cron to call the existing reminder evaluation endpoint. Notifications are in-app/demo only.
- Bank confirmation, digital-signature validation and actual disbursement are not implemented. OCR, confidence, SHA-256 and quality signals are not proof of authenticity.
- Existing phases 2–4 retain their prototype limitations documented in the UI/API; this update does not claim production readiness.

## Existing project features retained
- Dashboard, institution directory, FDR registry, verification/detail drawer and audit trail
- Document versioning, SHA-256 integrity hashes, PyMuPDF text extraction and optional Tesseract OCR
- Evidence-linked candidate fields, discrepancy signals and human corrections
- Lifecycle actions, reminder milestone evaluation, replacement links and maker-checker release workflow
- Operational analytics, readiness endpoint and audit CSV export

## Part 2 — Dynamic operations and analytics

- Dashboard and Reports & Analytics now use the authenticated `/api/v1/analytics/overview` response. The illustrative activity series has been removed; the dashboard chart displays database-derived maturity buckets.
- `/api/v1/analytics/export.csv` streams all matching records visible to the authenticated account (institution users remain scoped to their own institution).
- Lifecycle reminder evaluation runs automatically once at application startup and then hourly in this local prototype. Authorised officers can also trigger an immediate evaluation. Reminder records are idempotent; when an FDR maturity date changes, existing reminder due dates are reconciled and future milestones are marked scheduled until they become due.
- `PATCH /api/v1/fdrs/{id}/maturity` changes a maturity date with role and institution checks and an audit event.
- Release/refund actions use the authenticated bearer session; client-supplied demo role/user headers are ignored. Institution users can create requests only for their own FDRs. Maker-checker and transition guards remain enforced server-side.

### Part 2 limitations

The built-in hourly scheduler is intended for a single-process local demo. For multiple production workers, use a durable external scheduler/queue so jobs are coordinated across instances. In-app reminders do not send email/SMS. Release closure records an offline-confirmed workflow state; this application does not transfer money. Production deployment still requires managed SSO/secrets, private encrypted object storage, migrations/backups, and independent security review.
