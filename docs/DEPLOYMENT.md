# Deployment guide (MongoDB Atlas M0 → Google Cloud Run → Vercel)

**None of these steps has been executed by the author of this repo.** Flags were checked against the current `gcloud run deploy` reference and Cloud Run quotas/pricing pages (Nov 2026); re-check if Google changes them. Do the steps in order: database → backend → frontend → CORS update.

## ₹0 hosting assessment (read this before section 1)

**Verdict: the Atlas → Cloud Run → Vercel design below is technically sound but cannot be guaranteed to cost ₹0, so it is NOT the recommended path under a strict ₹0 budget.** Nothing below has been deployed, and no account, billing or paid resource has been created by this project. Terms were checked on 2026-11-16 from public pages/search results; several sources disagreed, which is marked. Re-check the vendor pages before acting.

| Piece | ₹0 status | Evidence / risk |
|---|---|---|
| **Run everything on your own computer** (backend + frontend + local MongoDB) | **Genuinely ₹0. This is what every test in this repo ran on.** | No account, no billing. Commands in the README. Availability = whenever your machine is on. |
| **Google Cloud Run** | **Not safe for ₹0.** | A Cloud Run project needs a Google Cloud *billing account with a payment method* even for the free tier; the free allowance is applied per billing account. A budget alert only emails you, it does not stop spending. Charges can come from exceeding the monthly allowances (bot traffic), egress, Artifact Registry / Cloud Build storage, logs, or any min-instance setting. **Do not activate billing to follow section 2 unless you accept that risk.** |
| **MongoDB Atlas M0** | Free forever tier (512 MB, shared CPU, about 100 ops/s cap, no backups). | Whether signup asks for a card is reported inconsistently: check at signup and stop if it asks for one. M0 cannot be upgraded by accident, but the console offers upgrade buttons. |
| **Vercel Hobby** (frontend only) | Free, but **personal, non-commercial use only**. | Exceeding fair-use limits pauses features for 30 days rather than billing; commercial use requires a paid plan. Fine for a personal portfolio app. |
| **Hugging Face Spaces (Docker, free CPU)** | **Unverified / probably unavailable.** | Docs list a free 16 GB / 2 vCPU tier that sleeps when idle, but 2026 forum reports say free Docker Spaces are restricted or need a paid plan. Do not rely on it. |
| Other free backend hosts (Render, Koyeb, Fly.io, Railway…) | **Not evaluated.** | Their free tiers change often and some require a card. Check the current terms yourself; do not assume. |

**What this means for the app:** the backend needs a host that can run a Docker image with the Tesseract system package. I could not identify, with the information available, a backend host that is both card-free and hard-capped at ₹0, so **reliable public production hosting for ₹0 cannot be guaranteed.** The safest options, in order:
1. **Local-first (recommended, ₹0 guaranteed):** run backend, frontend and MongoDB on your own machine (README "Local development"). Optionally point the local backend at an Atlas M0 cluster if it signs up without a card.
2. **Hybrid:** frontend on Vercel Hobby + Atlas M0 + a backend you host yourself (for example on a PC or Raspberry Pi behind a free tunnel). Not set up or tested here.
3. **Cloud Run (below)** only if you consciously decide to attach a billing account, accepting the residual risk. Keep `--max-instances 1`, `--min-instances 0`, a budget alert, and delete old images/revisions.

**Resource facts that matter for any free host:** one Uvicorn worker; OCR runs one Tesseract process at a time. Measured on Windows: 10 slips peaked at about 183 MB and a 5-page scanned PDF at about 281 MB (Python + Tesseract). Linux/container memory is **unmeasured**. Free instances with 512 MB RAM would probably fit but this is not verified. Request limits: 5 MB per file, 20 slips per batch, 5,000 import rows.

## 0. Cost reality (read first)
- Nothing here is *guaranteed* free. A **budget alert does not stop spending**; it only emails you.
- Cloud Run request-based free tier per month (pricing page): **2 million requests, 180,000 vCPU-seconds, 360,000 GiB-seconds**, in Tier-1 regions (includes `asia-south1` Mumbai, `us-central1`, `europe-west1`…). With 1 vCPU + 2 GiB the binding limit is **360,000 GiB-s ÷ 2 GiB = 50 hours** of billed instance time per month (CPU: 180,000 s = 50 h). Billed time is while requests are being handled (plus startup), so personal use fits easily, but a bot hammering the public URL could exceed it. Egress: 1 GiB/month free (North America); more is charged.
- Things that can create charges: exceeding the allowances above; **min-instances > 0** (always billed); Artifact Registry image storage above its free amount and Cloud Build minutes above theirs (source deploys use both — check current free limits); Secret Manager beyond its free tier; any load balancer/VPC connector/NAT (do **not** add them); egress.
- Reducing risk: keep `--max-instances 1`, `--min-instances 0`, delete old container images/revisions, set a budget alert (below), watch the Cloud Run metrics page monthly.

## 1. MongoDB Atlas (M0 free)
1. Create an Atlas account → **Create cluster → M0 (Free)**. Pick a cloud/region near your Cloud Run region (e.g. Mumbai for `asia-south1`).
2. **Database Access** → add a user (e.g. `financeai_app`) with a long random password and role **readWrite on database `financeai` only**.
3. **Network Access** → Cloud Run has no fixed egress IP, so add `0.0.0.0/0`. (Mitigation: strong password, least-privilege user. A static IP needs paid VPC + NAT, which this guide avoids.)
4. **Connect → Drivers** → copy the `mongodb+srv://…` string, replace `<password>` (URL-encode special characters). You will store it as a secret.
5. Indexes are created automatically on first startup/readiness (`app/db.py: ensure_indexes`): unique `users.email`; `transactions` on (user,date), (user,category,date), unique (user,fingerprint), (user,import); unique `budgets` (user,period); unique `recurring_payments` (user,merchant_key); TTL on staged `bank_imports`; plus per-user indexes on goals/forecasts/simulations/recommendations. M0 limits: 512 MB storage, shared CPU, ~500 connections — the app pools ≤10.

## 2. Backend on Cloud Run
Prereqs: a Google Cloud project with billing enabled (the free tier still requires a billing account), `gcloud` installed and `gcloud auth login`.
```bash
PROJECT=your-project-id
REGION=asia-south1            # a Tier-1 region; choose near Atlas
gcloud config set project $PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# secrets (never commit these)
python -c "import secrets;print(secrets.token_urlsafe(48))" | gcloud secrets create financeai-jwt --data-file=-
printf '%s' 'mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?retryWrites=true&w=majority' | gcloud secrets create financeai-mongo-uri --data-file=-
# let Cloud Run's runtime service account read them
PN=$(gcloud projects describe $PROJECT --format='value(projectNumber)')
for s in financeai-jwt financeai-mongo-uri; do
  gcloud secrets add-iam-policy-binding $s --member="serviceAccount:${PN}-compute@developer.gserviceaccount.com" --role=roles/secretmanager.secretAccessor
done

# build from backend/Dockerfile and deploy
gcloud run deploy financeai-api \
  --source backend \
  --region $REGION \
  --memory 2Gi --cpu 1 \
  --min-instances 0 --max-instances 1 \
  --concurrency 4 \
  --timeout 120 \
  --cpu-throttling \
  --allow-unauthenticated \
  --set-env-vars ENVIRONMENT=production,MONGODB_DB=financeai,CORS_ORIGINS=https://PLACEHOLDER.vercel.app \
  --set-secrets MONGODB_URI=financeai-mongo-uri:latest,JWT_SECRET=financeai-jwt:latest \
  --startup-probe=httpGet.path=/health,httpGet.port=8080,periodSeconds=5,timeoutSeconds=3,failureThreshold=12
```
What each setting does / where else it lives:
- `--cpu-throttling` = **request-based billing** (CPU only allocated during requests; the default). Console: *Edit & deploy new revision → CPU allocation → "CPU is only allocated during request processing"*.
- `--allow-unauthenticated` is required because browsers call the API directly; the app enforces its own JWT auth. Console: *Security → Allow unauthenticated invocations*.
- `--max-instances 1` also keeps the in-memory rate limiter correct. `--min-instances 0` means a cold start (a few seconds) after idle; the first login after idling is slower.
- Request limit: Cloud Run caps HTTP/1 requests at 32 MiB; the app additionally rejects uploads over 5 MB / 5,000 rows.
- The container listens on `$PORT` (8080 default) on 0.0.0.0, one Uvicorn worker, runs as a non-root user, and writes nothing persistent.
- Console-only items: billing account linkage, the budget alert (below), and viewing logs/metrics.

Smoke test (replace URL printed by the deploy command):
```bash
URL=https://financeai-api-xxxx-el.a.run.app
curl -s $URL/health            # {"status":"ok"}
curl -s $URL/ready             # {"status":"ready","database":"up"}  (503 + "down" if Atlas is unreachable)
```

### Cost controls
```bash
# budget alert (does NOT cap spend). Find your billing account id with: gcloud billing accounts list
gcloud billing budgets create --billing-account=XXXXXX-XXXXXX-XXXXXX --display-name="financeai-cap" \
  --budget-amount=1USD --threshold-rule=percent=0.5 --threshold-rule=percent=1.0
```
Also in the Console: *Billing → Budgets & alerts* (add email recipients), *Cloud Run → service → Metrics* (instance time, requests), *Artifact Registry → cloud-run-source-deploy* (delete old images), *Cloud Run → Revisions* (delete unused). For a hard stop you would need to script billing-disable from a budget Pub/Sub notification — powerful and risky (it takes the app offline); not set up here.

### Updating
Re-run the same `gcloud run deploy …` (only changed flags matter). To change CORS later: `gcloud run services update financeai-api --region $REGION --update-env-vars CORS_ORIGINS=https://your-app.vercel.app`.

## 3. Frontend on Vercel (Hobby/free)
1. Push the repo to GitHub (no `.env` files; `.gitignore` covers them).
2. Vercel → *Add New Project* → import the repo → **Root Directory: `frontend`** (Framework: Vite; `vercel.json` sets build, output `dist` and the SPA rewrite so refreshing `/budgets` works).
3. *Environment Variables* → `VITE_API_BASE_URL` = the Cloud Run URL (no trailing slash). Deploy.
4. Copy the production domain (e.g. `https://financeai.vercel.app`) and set it as `CORS_ORIGINS` on Cloud Run (command above). CORS allows **exact origins only**; preview deployments have different URLs and will be blocked unless you add them (comma-separated).
5. Vercel Hobby is for personal, non-commercial use.

### Payment Slip Scanner on Cloud Run (UNVERIFIED — test before relying on it)
* The Dockerfile now runs `apt-get install tesseract-ocr tesseract-ocr-eng` (about +60-100 MB image size) and the requirements add `pymupdf`, `pytesseract` and `pillow`. **This image has never been built or run by the author** (Docker was not available). `pip download` was used to confirm Linux x86-64 wheels exist for all 38 packages (no ML packages in the tree), nothing more.
* OCR runs as a Tesseract subprocess in the same container, one at a time (`OCR_MAX_CONCURRENCY=1`). On Windows, 10 slips peaked at ~183 MB and a 5-page scanned PDF at ~281 MB (Python + Tesseract), far below 2 GiB; Linux/Cloud Run figures are unmeasured. Tesseract is CPU-bound, so with 1 vCPU expect it to be slower than on a laptop: the client sends one file per request, so each request stays far below the 120 s timeout used above.
* Nothing is stored on the container filesystem; extracted data lives in MongoDB with a TTL (`SLIP_BATCH_TTL_HOURS`, default 24) and is also purged on batch creation, which matters because Cloud Run scales to zero.
* Cost note: OCR adds CPU-seconds per slip (roughly 0.3 s of CPU per slip measured locally), counted against the same free allowances; the 5 MB / 20-file limits bound the worst case per batch.
* Cloud Run request logs record request URLs; slip uploads put nothing sensitive in URLs (files travel in the body).

## 4. Verification checklist (do all, then record the results)
- [ ] `GET /health` 200 and `GET /ready` reports `database: up` from the Cloud Run URL.
- [ ] From the Vercel URL: register → log in → import a CSV → duplicates detected on re-import → dashboard figures match the file → budget/goal → forecast (needs ≥3 completed months) → optimise → simulate → PDF/CSV/Excel downloads.
- [ ] Browser devtools: no CORS errors; requests go to the Cloud Run URL, not localhost.
- [ ] Atlas: collections and indexes exist; one user cannot see another's data (log in with two accounts).
- [ ] Cloud Run → Metrics: peak memory well under 2 GiB (local measurement peaked ≈175 MB); logs contain no secrets or statement contents.
- [ ] Budget alert exists; min instances = 0, max = 1.
- [ ] **Payment Slip Scanner:** open the page on the Vercel site; the status must not say OCR is unavailable (`GET /api/v1/slips/status` → `available: true`). Upload the 10 files from `docs/test-data/payment-slips/`, check every extracted value, download the Excel, import, and confirm the figures in Dashboard. Check Cloud Run memory/CPU for the batch. Only then consider OCR verified in production.
- [ ] Wait for idle scale-to-zero, then log in again to confirm a cold start succeeds.

## 5. Environment variables
| Variable | Where | Purpose |
|---|---|---|
| `ENVIRONMENT` | Cloud Run | `production` disables `/docs`, `/debug/memory`, adds HSTS and enforces a strong `JWT_SECRET` (≥32 chars) |
| `MONGODB_URI` | Secret Manager | Atlas connection string |
| `MONGODB_DB` | Cloud Run | database name (`financeai`) |
| `JWT_SECRET` | Secret Manager | random ≥32 chars |
| `JWT_EXPIRE_MINUTES` | optional | default 60 |
| `CORS_ORIGINS` | Cloud Run | exact Vercel origin(s), comma separated |
| `MAX_UPLOAD_BYTES`, `MAX_IMPORT_ROWS`, `MAX_EXPORT_ROWS`, `AUTH_RATE_LIMIT_PER_MINUTE` | optional | limits |
| `TESSERACT_CMD`, `OCR_LANG`, `OCR_TIMEOUT_SECONDS`, `OCR_MAX_CONCURRENCY`, `SLIP_MAX_FILE_BYTES`, `SLIP_MAX_FILES_PER_BATCH`, `SLIP_MAX_PDF_PAGES`, `SLIP_BATCH_TTL_HOURS`, `SLIP_LOW_CONFIDENCE` | optional | Payment Slip Scanner (defaults in `docs/PAYMENT_SLIPS.md`; the Docker image needs none of them) |
| `VITE_API_BASE_URL` | Vercel | backend URL |
