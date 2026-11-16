# Real-world validation report

Run on 2026-11-16 against the local stack (FastAPI + MongoDB 8.2.1 on this machine + Vite). **Nothing was deployed** to Cloud Run, Vercel or Atlas. All data is synthetic ([docs/test-data](test-data/README.md)); no real personal or banking information was used.

## Method
1. `docs/test-data/generate_synthetic_data.py` creates a fictional HDFC-style statement (95 rows, Aug–Oct 2026) **plus a ground-truth file** (the correct type/category of every row) **and `expected_figures.json`**, computed with `Decimal` straight from the truth file — no application code involved.
2. `backend/tests/test_real_world_validation.py` imports the statement through the real API and compares the API output, the documents stored in MongoDB, the CSV/XLSX/PDF reports and every analytic with independently computed expectations (Decimal/Fraction/bisection code written inside the test).
3. `frontend/e2e/journey.spec.ts` drives the real UI through the whole journey and compares *displayed* figures with `expected_figures.json`.

## Bugs found (and fixed)
| # | Found by | Bug | Fix |
|---|---|---|---|
| 1 | Import of a standard HDFC-format file | Headers with punctuation (`Withdrawal Amt.`, `Deposit Amt.`, `Chq./Ref.No.`) were not recognised → every such statement forced manual mapping | Punctuation-insensitive header matching + more real-world header synonyms |
| 2 | Review of transfer pairing, confirmed by a dataset trap | Equal-amount debit/credit within a day were paired as "own-account transfer" if both merely mentioned NEFT/IMPS → a friend's ₹22,000 NEFT credit on rent day would have been hidden together with the rent | Both legs must be explicitly transfer-like ("transfer", "self", "own a/c"); channel words alone no longer qualify |
| 3 | Recurring detection on realistic bills | Variable bills (electricity, ±20%) were missed; groceries/fuel/cab habits were counted as "recurring cost", inflating it | Amount tolerance widened to ±40% for detection with honest evidence; each item has a `kind`; totals count bills/subscriptions and user-confirmed items only |
| 4 | Anomaly output on realistic data | Regular fuel purchases flagged as "5× typical Transport"; the ₹9,800 hospital spike and the brand-new ₹18,500 Travel category were not flagged (needed 3 prior months → impossible with 3 months of data) | Payee-normal exclusion; spike rule needs ≥2 prior months; "new category" flag |
| 5 | Phase 5 code review + test | `.xlsx` zip bomb: a tiny upload could unpack to gigabytes before the row limit applied | Declared unpacked size/entry count checked before parsing (40 MB / 200 entries) |
| 6 | Phase 5 log test | The catch-all error handler logged a traceback whose exception *message* can contain financial text | Log only exception type and location |
| 7 | Phase 5 log test **against real MongoDB** (invisible on mongomock) | PyMongo's `pymongo.command` logger writes full insert documents (bank narrations, amounts) at DEBUG | PyMongo loggers pinned to WARNING in code |
| 8 | Login attack review | Rate limit was per client IP only; `X-Forwarded-For` is spoofable behind a proxy | Added per-account login throttle (done in the previous session; test added here) |
| 9 | Journey, empty-state step | A new user with only a profile income could run a simulation that assumed ₹0 spending (savings = whole income) | Simulation now requires real history or an explicit spending figure |
| 10 | Journey, validation step | Submitting an empty budget form showed **no message** (array-level Zod error is at `errors.rows.root`) | Message now displayed |

Test-side corrections (not product bugs): wording of two expected messages, PDF text extraction helper, Playwright locators scoped to dialogs, a stale-cache assumption and a glob that did not match sub-paths. One pre-existing assertion was **changed on purpose**: `estimated_monthly_total` previously equalled the sum of all listed recurring items; bug 3 redefined it, and the test now asserts the exact hand-computed value (₹30,649.00 = rent 30,000 + Netflix 649) plus the `kind` of habitual items — stricter than before. No test was deleted or loosened.

## Results (all executed)
| Suite | Result |
|---|---|
| Backend pytest — real MongoDB 8.2.1 | **173 passed** |
| Backend pytest — mongomock | **173 passed** |
| Ruff | clean |
| Frontend Vitest | **15 passed** |
| Playwright e2e (real backend + MongoDB + UI) | **20 passed** (3 original + 17 new journey steps) |
| `tsc` + `vite build` | OK |
| Memory (scripts/measure_memory.py, re-run) | startup ≈75 MB, peak ≈175 MB, repeat passes +8.1 / +3.3 / −0.1 MB (no leak) |
| Leftover test databases in MongoDB | none |

## Independent calculation verification (what was compared)
Each item below was recomputed independently and compared with the **API**, the **MongoDB documents** and (for the first five, the budget, the recurring total and EMI) the **UI text**:
- Import: all 95 rows × (date, paise, debit/credit direction, type, category) in 8 bank layouts (HDFC, ICICI, SBI, signed, Dr/Cr, semicolon + cp1252, manual mapping, XLSX with preamble); stored types are `int` paise, tz-aware UTC midnight, unique fingerprints; staged rows removed after commit.
- Dashboard income / gross / refunds / net expense / net cash flow / savings % (2 dp) / category split / fixed vs variable, for each of Aug, Sep, Oct; month-over-month and yearly figures; refund offset (Sep Shopping = 899 + 1,740 − 1,299); transfers excluded.
- Budget (budget, actual, remaining, utilisation %, overspent set), goal (remaining, months by 30.4375-day rule, required contribution, completion date, progress %), emergency fund (essential average, target, gap, months to goal, coverage).
- Recurring: eight must-detect payees with independent median amounts and a ₹34,256.00 monthly total; nine must-not-detect payees.
- Anomalies: expected large items, spikes (incl. exact previous average and % increase), duplicate pair.
- Forecast: weighted moving average, sample standard deviation range, cumulative savings and spread, with ≤1–2 paise rounding tolerance; refusal with 2 months.
- Optimiser: independently derived floors/caps/targets for 3 scenarios, constraint satisfaction, reconciliation, and cost within 1% of an independent bisection (water-filling) optimum.
- Simulation: exact integer series (with and without 9% illustrative return, cut/income rounding half-up); EMI vs closed form (exact) and vs bisection (≤1 paise) for 5 loans incl. fractional rates and 360-month tenure; amortisation reconciles to zero.
- Reports: CSV and XLSX sums by type equal ground truth; PDF text (decoded) contains the independently expected October figures (Rs 85,000.00, Rs 1,56,962.25, −Rs 71,962.25, −84.66%).

## Security and integrity (all tested)
Cross-user access by ID for 16 operations on 10 resource types → all 404 with no side effects; every protected GET/POST requires auth; forged/`alg:none`/HS512/expired/no-exp/ghost-user/NoSQL-in-claims/wrong-scheme tokens rejected; password change and logout revoke tokens; NoSQL-operator payloads and mass assignment rejected (422); Argon2id hashes (salted, never in responses or any collection); stored user fields whitelisted — no bank credentials or payment data anywhere; upload matrix (extension/content mismatch, binary, zip bomb, path-like filenames never touch the filesystem); CSV formula injection neutralised; server logs contain no passwords, tokens, secrets, narrations or amounts; error responses leak no internals; production disables `/docs`, `/openapi.json`, `/debug/memory`, enforces a strong JWT secret and sends HSTS.

## Remaining known limitations
- **Query strings reach request logs.** Search text and filters travel in GET query strings; the app's own logs do not record them (access log disabled in the Docker image), but Cloud Run's platform request logs record full URLs. Treat the search box as potentially logged by the platform; a future change could move search to a POST body.
- Categorisation is keyword-based; unusual narrations land in "Other" until corrected (the dataset's 100 % accuracy partly reflects keywords tuned to Indian narrations; real statements will have misses).
- Transfer pairing is deliberately conservative: a transfer whose narration has no "transfer/self/own a/c" wording stays income/expense (user can retype). Credit-card bill payments are treated as transfers (so card purchases must be imported to be counted).
- Recurring detection needs ≥3 occurrences (≥2 for annual). Single-occurrence items such as the flight can only be caught by the "new category" flag; Travel itself is not "recurring".
- Large-transaction detection needs ≥6 transactions in a category; with 3 months of data the spike rule is the main safety net.
- Forecasts with 3 months are labelled low confidence and have no trend term (needs ≥4 months).
- Rate limiting is per instance (fine at max-instances 1).
- Not verified: Docker build, Cloud Run, Atlas, Vercel, mobile layout, accessibility audit.
