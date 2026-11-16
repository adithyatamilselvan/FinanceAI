"""Application settings, read from environment variables (never hardcode secrets)."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"  # development | production | test
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "financeai"
    mongodb_timeout_ms: int = 5000

    jwt_secret: str = "change-me-in-development-only-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # Comma separated list of exact origins, e.g. https://my-app.vercel.app
    cors_origins: str = "http://localhost:5173"

    # Import limits (protect the 2 GiB Cloud Run container and Atlas M0 storage)
    max_upload_bytes: int = 5 * 1024 * 1024
    max_import_rows: int = 5000
    max_export_rows: int = 50000
    import_preview_ttl_hours: int = 24

    # Payment-slip OCR (local Tesseract; nothing leaves the server, no paid API)
    tesseract_cmd: str = ""  # path to the tesseract binary; empty = auto-detect (PATH, then common Windows install dirs)
    ocr_lang: str = "eng"
    ocr_timeout_seconds: int = 40
    ocr_max_concurrency: int = 1  # simultaneous OCR runs per instance (memory/CPU guard for 1 vCPU)
    slip_max_file_bytes: int = 5 * 1024 * 1024
    slip_max_files_per_batch: int = 20
    slip_max_pdf_pages: int = 5
    slip_max_image_pixels: int = 25_000_000
    slip_max_active_batches_per_user: int = 5
    slip_batch_ttl_hours: int = 24
    slip_low_confidence: int = 60  # OCR confidence (0-100) below which a slip needs review
    slip_pdf_min_text_chars: int = 25  # fewer extractable characters => treat a PDF page as scanned and OCR it

    # Simple in-memory rate limiting (valid because max instances = 1)
    auth_rate_limit_per_minute: int = 10

    @field_validator("cors_origins")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.is_production and (s.jwt_secret.startswith("change-me") or len(s.jwt_secret) < 32):
        raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters in production")
    return s
