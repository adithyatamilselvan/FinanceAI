# Backend image for Google Cloud Run. Small, single Uvicorn worker, no local persistent storage.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production \
    PORT=8080

WORKDIR /srv

# Tesseract OCR engine + English model for the Payment Slip Scanner (free, runs locally; adds roughly 60-100 MB to the image).
# PyMuPDF and Pillow ship their own native libraries in their wheels, so nothing else is needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as an unprivileged user.
RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

EXPOSE 8080

# Cloud Run injects $PORT. One worker only: memory is not multiplied. Concurrency is set on the Cloud Run service (4).
# --proxy-headers lets the app see the real client IP behind Google's front end (used by the auth rate limiter).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 65 --no-server-header --no-access-log"]
