# Payment Slip Scanner

Upload photos/PDFs of payment slips and receipts, get a structured preview and an Excel workbook, correct mistakes, then confirm. Free and local: Tesseract OCR + PyMuPDF + Pillow. No paid API, no cloud OCR, no ML framework.

## Workflow (human verification is mandatory)
```
Upload slips (one file per request) -> OCR / PDF text -> parse fields -> Excel workbook (in memory, on demand)
   -> preview -> user corrects / verifies -> user confirms -> insert into `transactions`
```
Nothing reaches your transactions until you confirm. Confirmed slips become ordinary transactions (same schema, integer paise, `source: "slip"`, with `slip.file`, `slip.extraction`, `slip.reference` ...), so the Dashboard, Analytics, budgets, forecasts, optimiser and reports use the *one* existing financial engine. There is no separate calculation path for slips.

## Using it
1. Sidebar -> **Payment Slip Scanner**. Drag files in or choose them (JPG, JPEG, PNG, PDF; up to 20 per batch, 5 MB each, 5 pages per PDF).
2. Each file shows *Waiting -> Uploading -> Reading text (OCR) -> Read / Failed*. Files are sent one at a time.
3. Review the table. **Amber** = please check against the slip; **red** = missing; the *Review* column says why. **Edit** opens every field (and the raw text read from the slip); **Mark verified** confirms a row you have checked.
4. **Download Excel** at any time (it contains your corrections). **Confirm and import N selected** saves the ticked rows. **Cancel batch** deletes everything extracted.

Pre-ticked by default: only *successful, complete, unflagged* slips. Other rules, enforced by the server (not just the UI):

| Situation | Behaviour |
|---|---|
| Payment **failed** | never importable |
| **Pending / refunded / unclear** status | needs the "allow non-successful" tick **and** the row verified. Refunds are stored as `refund` (they reduce spending) |
| Missing date, amount, type or currency | blocked until the user fills it in (currency is never assumed) |
| Uncertain value, low OCR confidence, no merchant | must be verified (edit or "Mark verified") |
| **Possible duplicate** (same reference/amount/date as another slip in the batch or an existing transaction) | shown, never dropped silently; needs the "allow possible duplicates" tick and verification |
| Identical payment **already imported** | blocked; importing the same slips twice creates no duplicates (stable fingerprint + unique index) |
| Any blocked row in the selection | the whole import is refused with per-file reasons; nothing is saved |

## What is extracted
Date, time, amount (-> integer paise), currency, type (income / expense / transfer / refund), merchant/recipient, sender, payment method, UTR / UPI / transaction reference, bank/provider, status (successful / pending / failed / refunded / unknown), description, source file, OCR confidence, plus *missing* and *uncertain* field lists. A value that is not on the slip stays `null`; guesses are marked uncertain.
Notable rules: a bank/UPI reference (UTR) is preferred over an app transaction id; `₹` is often misread by Tesseract (`=`, `%`, `~`, dropped) and is handled; INR is concluded only from `₹ / Rs / INR` or an INR-only rail (UPI, IMPS, NEFT, RTGS), otherwise the currency stays empty; "Available balance", fees, tax etc. are never taken as the amount; a payment that says "failed" wins over boilerplate that mentions a refund; numeric dates are read day-first and a note says so.

## Storage and privacy
* Uploaded files are processed **in memory** and never written to disk or stored. (Starlette may spool uploads >1 MB to a temp file that is deleted when the request ends; a test verifies nothing is left behind.)
* Extracted data lives in a per-user MongoDB document (`slip_batches`), never in a shared file. The Excel workbook is generated in memory from *your* batch when you click download.
* Batches are deleted on cancel, when expired (default 24 h; a TTL index plus an explicit `cleanup_expired` that also runs when a new batch is created, because Cloud Run scales to zero), and the oldest unfinished batches beyond 5 per user are discarded. After import the OCR text is erased.
* Logs contain only `ok=<bool> method=<kind>`: no filenames, text, amounts, references or UPI ids (tested).
* Validation: extension + real file signature + declared MIME must agree; size, pixel and page limits; password-protected / malformed PDFs and corrupt images are rejected per file with a plain message; filenames are reduced to a safe base name; spreadsheet formula injection is neutralised in the workbook.

## Install Tesseract (required for real OCR)
| Where | How |
|---|---|
| **Windows (development)** | `winget install --id UB-Mannheim.TesseractOCR -e` (or the installer from https://github.com/UB-Mannheim/tesseract/wiki). The app finds `C:\Program Files\Tesseract-OCR\tesseract.exe` automatically; otherwise set `TESSERACT_CMD`. Verify: `tesseract --version` |
| **Debian / Ubuntu** | `sudo apt-get install -y tesseract-ocr tesseract-ocr-eng` |
| **macOS** | `brew install tesseract` |
| **Docker / Cloud Run** | Already in `backend/Dockerfile` (`apt-get install tesseract-ocr tesseract-ocr-eng`). Adds roughly 60-100 MB to the image |
Python side: `pip install -r backend/requirements.txt` (adds `pymupdf==1.26.7`, `pytesseract`, `pillow`). **Do not upgrade PyMuPDF to 2.x**: it pulls in `onnxruntime`, `networkx` and other heavy packages. OpenCV is deliberately not used (Pillow preprocessing is enough and keeps the image small). Other languages: install the extra `tesseract-ocr-<lang>` package and set `OCR_LANG` (e.g. `eng+hin`).

If Tesseract is missing, the page shows a clear banner, uploads are disabled and the rest of FinanceAI works normally.

## Settings (environment variables)
`TESSERACT_CMD` (path, auto-detected if empty), `OCR_LANG=eng`, `OCR_TIMEOUT_SECONDS=40`, `OCR_MAX_CONCURRENCY=1`, `SLIP_MAX_FILE_BYTES=5242880`, `SLIP_MAX_FILES_PER_BATCH=20`, `SLIP_MAX_PDF_PAGES=5`, `SLIP_MAX_IMAGE_PIXELS=25000000`, `SLIP_MAX_ACTIVE_BATCHES_PER_USER=5`, `SLIP_BATCH_TTL_HOURS=24`, `SLIP_LOW_CONFIDENCE=60`, `SLIP_PDF_MIN_TEXT_CHARS=25`.

## API (all under `/api/v1/slips`, authenticated, scoped to the caller)
`GET /status` · `POST /batches` · `POST /batches/{id}/files` (one file) · `GET /batches/{id}` · `PATCH /batches/{id}/items/{item}` · `GET /batches/{id}/items/{item}/text` · `GET /batches/{id}/workbook.xlsx` · `POST /batches/{id}/commit` · `DELETE /batches/{id}`.

## Measured performance (Windows, local; Linux/Cloud Run not measured)
See [slip-memory-results.txt](slip-memory-results.txt). 10 slips: ~3 s, peak ~183 MB (Python + Tesseract). Worst allowed input, a 5-page scanned A4 PDF: ~6.4 s, peak ~281 MB. One OCR run at a time per instance (`OCR_MAX_CONCURRENCY=1`) bounds memory and CPU.

## Accuracy: what was and was not verified
* The 10 acceptance slips (PNG, JPG, JPEG, digital PDF, scanned PDF; UPI app, bank transfer, card, cash, failed, pending, refund layouts): **all 110 field values (10 slips x 11 fields) match the ground truth** with real Tesseract OCR.
* Degraded copies of those slips (`scripts` experiment, not a promise): JPEG quality 35, 55 % scale and dim+blur all stayed exact; a 2-degree rotation on a grey background lost 2 fields out of 9 slips; a severe "phone photo" simulation (50 % size + JPEG 50 + rotation) was exact for 5 of 9 slips. In every degraded case a wrong or missing date/amount/type/currency came out **flagged for review**, never silently importable (this is a test).
* **Not verified:** real photographs of real receipts, handwriting, non-English slips, crumpled/low-light photos, real bank/UPI app layouts beyond the fictional ones here, other fonts, and the Linux/Docker/Cloud Run environment (no Docker was available). Expect more misses on real photos; that is why every row goes through human review. There is no automatic deskew (Pillow only).
* Synthetic slips use real brand names as plain text only to exercise keyword/category logic; no real account, UTR or person is involved.
