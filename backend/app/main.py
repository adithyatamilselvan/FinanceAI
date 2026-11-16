"""FastAPI application entry point (single Uvicorn worker; Cloud Run friendly)."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db as database
from .config import get_settings
from .routers import auth, budgets, categories, goals, imports, insights, reports, slips, transactions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("financeai")
# PyMongo logs complete command documents (inserts containing bank narrations and amounts) at DEBUG. Pin these loggers so
# that turning the root level to DEBUG can never write financial data to the logs.
for _name in ("pymongo", "pymongo.command", "pymongo.connection", "pymongo.serverSelection", "pymongo.topology"):
    logging.getLogger(_name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()  # fails fast on an unsafe production JWT secret
    database.connect()
    try:  # never crash on startup if Atlas is unreachable; /ready and DB endpoints report it
        database.ensure_indexes()
        log.info("MongoDB connected, indexes ensured")
    except PyMongoError as e:
        log.error("MongoDB unavailable at startup (%s); the API will report 503 until it recovers", type(e).__name__)
    log.info("Started in %s mode", s.environment)
    yield
    database.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="FinanceAI API", version="1.0.0", lifespan=lifespan,
                  docs_url=None if s.is_production else "/docs", redoc_url=None, openapi_url=None if s.is_production else "/openapi.json")

    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["Content-Disposition"], max_age=600)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > s.max_upload_bytes + 256 * 1024:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        # A chunked body has no Content-Length, so the size check above cannot see it and the multipart parser could spool an unbounded
        # body to temporary storage before any endpoint runs. Browsers and API clients send a length for every request this API accepts.
        if request.method in ("POST", "PUT", "PATCH") and not cl and "chunked" in request.headers.get("transfer-encoding", "").lower():
            return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
        resp = await call_next(request)
        resp.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            **({"Strict-Transport-Security": "max-age=31536000; includeSubDomains"} if s.is_production else {}),
        })
        return resp

    @app.exception_handler(database.DatabaseUnavailable)
    async def _db_unavailable(_: Request, __):
        return JSONResponse({"detail": "The database is currently unavailable. Please try again shortly."}, status_code=503)

    @app.exception_handler(PyMongoError)
    async def _mongo_error(_: Request, exc: PyMongoError):
        from pymongo.errors import AutoReconnect, ConnectionFailure, ServerSelectionTimeoutError

        log.error("MongoDB error: %s", type(exc).__name__)  # class only: never log details/credentials
        if isinstance(exc, (ServerSelectionTimeoutError, AutoReconnect, ConnectionFailure)):
            return JSONResponse({"detail": "The database is currently unavailable. Please try again shortly."}, status_code=503)
        return JSONResponse({"detail": "A database error occurred."}, status_code=500)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errs = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"detail": "Validation failed", "errors": errs}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        # Log the exception TYPE and where it happened, never its message or locals: those can contain financial text or secrets.
        import traceback

        tb = traceback.extract_tb(exc.__traceback__)
        where = f"{tb[-1].filename.split('app')[-1]}:{tb[-1].lineno} in {tb[-1].name}" if tb else "unknown"
        log.error("Unhandled %s at %s", type(exc).__name__, where)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    @app.get("/health", tags=["health"])
    async def health():
        """Liveness: no database access, no model loading."""
        return {"status": "ok"}

    @app.get("/ready", tags=["health"])
    def ready():
        """Readiness: verifies the MongoDB connection."""
        if not database.ping():
            return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
        return {"status": "ready", "database": "up"}

    if not s.is_production:
        @app.get("/debug/memory", tags=["debug"])
        def debug_memory():
            """Development-only: resident memory of this process."""
            from .util import process_rss_mb

            return {"rss_mb": process_rss_mb()}

    for r in (auth.router, transactions.router, imports.router, categories.router, budgets.router, goals.router, insights.router, reports.router, slips.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
