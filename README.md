# FinanceAI — personal finance tracking, budgeting, optimisation and simulation

React + TypeScript frontend, FastAPI + MongoDB backend. All money maths is done by the Python backend in **integer minor units** (paise for INR). No LLM, no paid API, no PyTorch/TensorFlow/LangChain/Celery/Redis.

> **Status (honest summary).** Everything marked *tested* was executed in this repository. **Nothing has been deployed.** The Docker image build, Cloud Run, MongoDB Atlas and Vercel were **not** performed or verified (no Docker, `gcloud`, Atlas or Vercel access in the build environment). **₹0 warning:** Cloud Run requires a billing account, so the Atlas → Cloud Run → Vercel path cannot be guaranteed free; read the "₹0 hosting assessment" at the top of [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) first. Running locally is the only option verified to cost nothing.

```
backend/   FastAPI app (app/), tests (tests/), memory script (scripts/), Dockerfile, requirements
frontend/  Vite + React + Tailwind + TanStack Query + RHF/Zod + Recharts, unit tests, Playwright e2e
docs/      DEPLOYMENT.md (incl. ₹0 assessment), PAYMENT_SLIPS.md, VALIDATION_REPORT.md, memory results, synthetic test data
```

## Features implemented
| Module | What exists |
|---|---|
| Auth/profile | Register, login, logout (server-side token invalidation), Argon2id hashing, JWT with expiry, password change, currency/income/savings profile, per-user data isolation |
| Import | CSV + XLSX (preamble rows, signed or debit/credit columns, Dr/Cr column), auto column detection + manual mapping, date/amount normalisation (Decimal, no floats), per-row error report, preview → confirm, fingerprint-based duplicate prevention, refund and own-account-transfer detection, limits (5 MB, 5,000 rows) |
| Transactions | Manual create/edit/delete, search, filters, sorting, pagination |
| Categories | Rule-based keywords, custom categories, learned rules from manual corrections (optionally applied to similar transactions). No ML is claimed or trained |
| Analytics | One shared set of definitions (`services/analytics.py`): income, net spending (refunds offset), transfers excluded, savings %, category split, fixed vs variable, month/year comparisons, category trends |
| Recurring | Interval/amount-consistency detection (variable bills allowed within ±40%) with confidence and evidence; bills/subscriptions vs habitual spending; confirm / edit / dismiss (user decisions survive re-detection); nothing is cancelled automatically |
| Budgets | Monthly category budgets, utilisation, overspend, history, copy |
| Goals | Multiple goals, required monthly contribution, estimated completion, emergency-fund calculator, explicit assumptions |
| Anomalies | Robust (median/MAD) large-transaction flags, category spikes, possible duplicates — worded as "review", never "fraud"; transparent health score |
| Forecast | Weighted moving average + damped trend, ± one-σ range; refuses with <3 completed months |
| Optimisation | SciPy SLSQP (validated, with deterministic fallback) over variable spending; conservative/balanced/aggressive; respects essential limits and user minimums; never changes budgets unless the user accepts and chooses to apply |
| What-if | Extra monthly saving, income change, category cuts, a scenario-only **starting savings** balance (the baseline always starts from your profile balance), illustrative return, goal/emergency-fund timing. The difference is split into starting savings, monthly changes, loan cash flow and growth. **Illustrative growth is earned only on a positive balance; a zero or negative balance is never grown.** Loans: EMI, amortisation, extra payments, and an explicit choice between an *existing* loan (EMI already in your spending; default) and a *new* loan (every repayment is deducted from projected savings), driven by the real baseline and scenario repayment schedules |
| Reports | Monthly summary, CSV (formula-injection safe), Excel, PDF — all generated in memory |
| Interface | Responsive light theme by default with an optional dark theme (saved per browser), collapsible sidebar and mobile drawer, toasts, confirmation dialogs, skeleton loaders, scrolling modals with a sticky action row, keyboard-accessible dialogs |
| **Payment Slip Scanner** | Upload up to 20 slip photos/PDFs (JPG, JPEG, PNG, PDF) per batch; local Tesseract OCR + PyMuPDF extract date, time, amount (paise), currency, type, merchant/sender, method, UTR/reference, provider, status; per-file progress; Excel workbook (Extracted Transactions / Summary / Errors / Source Text); editable preview; duplicate checks (in batch and against existing transactions); **nothing is saved until the user reviews and confirms**; confirmed slips are normal transactions in the same engine. See [docs/PAYMENT_SLIPS.md](docs/PAYMENT_SLIPS.md) |

**Not implemented:** the optional AI-explanation integration (the app is fully functional without it; no AI key is used anywhere). scikit-learn is intentionally not installed: no ML classification is performed, so the dependency would only add memory.

## Key design decisions
- **Sync PyMongo + threadpool endpoints** (not async PyMongo): one shared pooled client created in the lifespan, closed on shutdown; this keeps the event loop free and lets the whole suite run on both `mongomock` and a real MongoDB.
- **No file storage.** Uploads are parsed in memory. The staged import (parsed rows, not the file) lives in MongoDB with a 24 h TTL and is deleted of its rows on commit. Starlette may spool uploads >1 MB to a temp file, which is removed when the request ends; on Cloud Run `/tmp` is in-memory (counted against the 2 GiB).
- **"Completed months" are anchored on your data**, not the calendar: an old statement still yields forecasts (a "stale" notice is shown).
- JWT is kept in `localStorage` (simple, XSS-exposed); `Content-Security-Policy`/React escaping mitigate but do not eliminate that. Logout bumps a server-side token version, so stolen tokens die on logout/password change.
- Rate limiting is in-memory (valid for max-instances = 1). Login is throttled per IP **and** per account (IP can be spoofed via `X-Forwarded-For`).

## Local development
Prereqs: Python 3.11, Node 20+, a MongoDB (local `mongod` or free Atlas M0), and — only for the Payment Slip Scanner — the free **Tesseract OCR** engine (Windows: `winget install --id UB-Mannheim.TesseractOCR -e`; Debian/Ubuntu: `sudo apt-get install tesseract-ocr tesseract-ocr-eng`; macOS: `brew install tesseract`; the Docker image installs it for you). Without Tesseract everything else works and the scanner page says OCR is unavailable.
```bash
# backend
cd backend
python -m venv .venv && .venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # set MONGODB_URI and a JWT_SECRET
uvicorn app.main:app --reload --port 8000              # docs: http://localhost:8000/docs (dev only)

# frontend (new terminal)
cd frontend && npm install && npm run dev              # http://localhost:5173 (proxies /api to :8000)
```
Dev-only memory probe: `GET /debug/memory` (not registered when `ENVIRONMENT=production`).

**Database setup:** nothing to create by hand. Point `MONGODB_URI` at any MongoDB (a local `mongod`, or an Atlas M0 cluster) and the app creates its collections and indexes on startup. Local MongoDB (Windows): install MongoDB Community Server, which runs as a service on `mongodb://localhost:27017`.

**Environment variables:** copy `backend/.env.example` to `backend/.env` and `frontend/.env.example` to `frontend/.env` (both contain placeholders only; real `.env` files are git-ignored). Generate the secret with `python -c "import secrets;print(secrets.token_urlsafe(48))"`. In production (`ENVIRONMENT=production`) the backend refuses to start if `JWT_SECRET` is missing, shorter than 32 characters or the development default. The full variable list is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#5-environment-variables).

### Run it locally, production-style (₹0)
```bash
# backend: same command shape as the Dockerfile (one worker), listening on localhost only
cd backend
ENVIRONMENT=production JWT_SECRET=<your-random-secret> MONGODB_URI=mongodb://localhost:27017 CORS_ORIGINS=http://localhost:4173 \
  uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
#   Windows PowerShell:  $env:ENVIRONMENT="production"; $env:JWT_SECRET="..."; $env:CORS_ORIGINS="http://localhost:4173"; uvicorn app.main:app --port 8000

# frontend: production build, then serve it (vite preview proxies nothing, so set the API URL at build time)
cd frontend
VITE_API_BASE_URL=http://localhost:8000 npm run build      # PowerShell: $env:VITE_API_BASE_URL="http://localhost:8000"; npm run build
npm run preview -- --port 4173                             # http://localhost:4173
```
Health checks: `curl http://127.0.0.1:8000/health` (no database) and `curl http://127.0.0.1:8000/ready` (pings MongoDB). Use `127.0.0.1`: on some Windows setups `localhost` tries IPv6 first and the server listens on IPv4.

## Tests — how to run, and what was actually run
```bash
cd backend && pytest                                              # in-memory mongomock
TEST_MONGODB_URI=mongodb://localhost:27017 pytest                 # real MongoDB (throwaway db per test, dropped)
cd frontend && npm test                                           # Vitest
cd frontend && npx playwright install chromium && npx playwright test   # needs a local MongoDB (uses db financeai_e2e_run, dropped)
```
| Suite | Result (final verification run, 2026-11-16) |
|---|---|
| Backend pytest, real MongoDB 8.2.1 (local) | **312 passed, 0 failed, 0 skipped** |
| Backend pytest, mongomock | **312 passed, 0 failed, 0 skipped** |
| Ruff (`ruff check app tests`) | all checks passed |
| TypeScript (`tsc -b`) | no errors |
| Frontend Vitest | **19 passed** (money parsing/formatting, API client and upload progress, login validation) |
| `npm run build` | succeeds; main chunk ≈137 kB gzip, routes lazy-loaded, 1.0 MB total `dist` |
| Playwright e2e (real backend + MongoDB + Vite + real Tesseract) | **83 passed, 0 failed, 0 skipped**: authentication and the full finance journey; the Payment Slip Scanner (10 synthetic slips, workbook, review gates, import, duplicates, cleanup); edit-dialog isolation across slips; modal scrolling (mouse wheel, keyboard, touch) at four screen sizes; no horizontal overflow on every page at 1440, 1280, 820 and 390 px; WCAG AA text contrast in light and dark; sidebar, drawer, focus trap, toasts; what-if simulation and loan cases |
| pytest warnings | 2, both from libraries: Starlette's `httpx` TestClient deprecation, and PyJWT's short-key notice for the throwaway test secret |

**Real-world validation:** see [docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md) — synthetic dataset with ground truth in [docs/test-data](docs/test-data/README.md), 10 bugs found and fixed, and the list of figures verified against independent Python calculations.

Covered: registration/login/invalid login/expired & forged tokens/logout, protected routes, multi-user isolation, CSV/XLSX import, invalid & oversized files, row limit, 5,000-row import, duplicate prevention, categorisation & learning, manual CRUD, analytics with hand-computed expected values (incl. leap day, month ends, refunds, transfers), budgets, goals, emergency fund, recurring (monthly/weekly/annual/irregular), anomalies, forecast maths, optimiser constraints and accept/reject flow, simulation, EMI/amortisation (known values), CSV/XLSX/PDF, CORS, headers, rate limits, DB-down → 503 with no leaked details.
**Not covered by automation:** deployed Cloud Run/Atlas/Vercel behaviour, the Docker build, Atlas-specific limits, real phones and non-Chromium browsers (responsive layout, no-horizontal-overflow and text-contrast checks run in Chromium with emulated viewport sizes), screen-reader testing, and OCR on real-world receipt photos.

## Memory results (measured, not estimated)
`backend/scripts/measure_memory.py` drives the real app against a real MongoDB (full output: [docs/memory-results.txt](docs/memory-results.txt)). Windows, Python 3.11, in-process:

| Stage | RSS |
|---|---|
| Python baseline | 27 MB |
| App imported (startup, before DB) | **75 MB** |
| After 5,000-row CSV import (preview + commit) | 123 MB |
| After 5,000-row XLSX import | 132 MB |
| After analytics, forecast, anomaly, recurring | ~134 MB |
| After SciPy optimisation | 155 MB |
| After PDF report | 163 MB |
| **Peak** (incl. 10,000-row CSV/Excel exports) | **≈175 MB** |
| Leak check: 3 × 15 rounds of dashboard+forecast+optimise+PDF+list | +10.8 MB (first pass: lazy module loads), then **0.0 / −0.1 MB** |

Well under the 512 MB that crashed DocuMind-AI and the 2 GiB configured for Cloud Run. Linux container numbers will differ somewhat; re-measure with `/debug/memory` or Cloud Run's memory metric after deploying. Expected supported workload: one active user session at a time per instance (concurrency 4), statements ≤5,000 rows / 5 MB, a few hundred thousand stored transactions per Atlas M0 (512 MB storage) at most.

## API
`/health` (no DB), `/ready` (pings MongoDB, 503 if down), everything else under `/api/v1` — auth, profile, transactions, imports, categories, budgets, goals (+emergency-fund), recurring, analytics, forecasts, optimization, simulations, reports. Interactive docs at `/docs` outside production. Money is always integer minor units in JSON.

## Payment Slip Scanner: status
Verified here (Windows, real Tesseract 5.4, real MongoDB 8.2): 71 backend tests (formats, digital + scanned PDFs, 10-file batch matching ground truth field-by-field, missing/low-confidence/failed/pending/refund slips, duplicates in-batch / against existing transactions / on re-import, workbook structure and values, isolation between users, validation, limits, cleanup, no sensitive data in logs), 3 frontend unit tests and 9 Playwright tests through the real UI (10 slips uploaded, Excel downloaded and opened, corrections, human-review gates, import, Dashboard/Analytics/Reports figures, re-upload creates no duplicates, cancel, refresh, edge cases). Measured: 10 slips ≈ 3 s and ≈ 183 MB peak (Python + Tesseract); worst allowed PDF (5 scanned A4 pages) ≈ 6.4 s and ≈ 281 MB.
**Not verified:** OCR inside the Linux Docker image / on Cloud Run (no Docker here; the Dockerfile installs `tesseract-ocr` but the image was never built or run), real photographs of real receipts (all test slips are synthetic renders; severely degraded copies lose accuracy but are flagged for review), handwriting and non-English slips. **Do not assume OCR works on Cloud Run until you have deployed and run the checklist in docs/DEPLOYMENT.md.**

## Production-mode check (executed locally)
With only `requirements.txt` installed in a clean virtualenv (40 packages incl. transitive; no dev or ML packages), the backend was started in `ENVIRONMENT=production` like the Dockerfile `CMD`: it refuses to start with the default JWT secret; `/health` and `/ready` pass; `/docs`, `/openapi.json` and `/debug/memory` return 404; HSTS, nosniff, frame-deny and CSP headers are sent; CORS admits only the configured origin; register, login and logout work; Tesseract OCR is reported available; no secret or password appears in the logs. `npm run build` output was served and checked (SPA routes fall back to `index.html`; no `localhost` URL is baked in). `npm audit`: 0 vulnerabilities. `pip-audit` on the installed Python packages: no vulnerable *application* dependency (only the venv's own `setuptools` build tool, which is not in `requirements.txt`).

## Known limitations
- Cloud Run / Atlas / Vercel / Docker: **unverified** (see above). `requirements.txt` installs in a clean Windows venv and has Linux x86-64 CPython 3.11 wheels, but the Docker build and Linux runtime were never run. This is **not a git repository**; put it under version control before publishing.
- Hosting at ₹0 cannot be guaranteed (see the assessment in docs/DEPLOYMENT.md). Vercel Hobby is personal, non-commercial use only.
- The JWT lives in `localStorage` (XSS-exposed by design; React escaping and the API's strict headers mitigate it). Sessions last 60 minutes by default.
- Chunked (length-less) request bodies are refused with 411; every browser/API client used here sends a length.
- Categorisation is keyword-based (tuned for Indian bank narrations); unusual merchants land in "Other" until corrected.
- Transfer pairing is heuristic (equal amount ±1 day, transfer-like wording); users can retype any transaction.
- Forecasts/optimiser are simple statistics on your own history, not predictions or advice. Health score is educational, not a credit score.
- Multi-currency accounts are not converted; the profile currency is a label for all amounts.
- Atlas must allow `0.0.0.0/0` for Cloud Run (no static egress IP without paid networking); protect with a strong DB password and least-privilege user.
- Rate limiter and caches are per instance (fine at max-instances 1; use a shared store before scaling out).
- Slip OCR accuracy on real-world photos is unknown; there is no automatic deskew and no OpenCV; one OCR run at a time per instance, so a 20-slip batch on 1 vCPU takes on the order of a minute (sent file-by-file, each request well under the timeout).
- Large uploads are bounded by Cloud Run's 32 MiB HTTP/1 request limit and the app's 5 MB check; `.xlsx` files are also refused if they unpack beyond 40 MB.
- Search text and filters are sent as URL query strings; Cloud Run's platform request logs record URLs (the app's own logs do not). See the validation report.
- Recurring-payment totals count bills/subscriptions and payments you confirm; habitual spending (groceries, fuel, cabs) is listed for review but excluded until confirmed.
