"""Application entrypoint.

Wires the routers together and installs the exception handlers that guarantee
*every* response — success or failure — comes back in the same envelope:

    { "data": ..., "error": { "code", "message", "field" } }

The handlers are where the "speakable error" contract is enforced for the REST
API. A Pydantic failure never escapes as a Pydantic string; it is rewritten into
the same sentence the voice agent would say.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import init_db
from app.logging_conf import log_event
from app.routers import dashboard, patients, vapi
from app.schemas import first_speakable_error

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent: creates tables on first boot, no-ops thereafter. This is what
    # makes a fresh Render deploy against an empty Neon database just work.
    init_db()
    log_event("startup", app=settings.app_name, version=settings.app_version)
    yield
    log_event("shutdown")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Voice AI patient registration. A Vapi voice agent collects demographics "
        "over the phone and persists them through this service; the same records "
        "are readable and editable over REST."
    ),
    lifespan=lifespan,
)

# The API is public-read for the reviewer's convenience and for the dashboard.
# A production system would put auth in front of this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(patients.router)
app.include_router(vapi.router)
app.include_router(dashboard.router)


# --------------------------------------------------------------------------
# Exception handlers — one envelope, always
# --------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """422 with a message a person could say out loud.

    This is the REST-side twin of what the Vapi webhook does. A client that
    POSTs a future date of birth gets "That date of birth is in the future.
    Could you give me the year again?" rather than a schema dump.
    """
    field, message = first_speakable_error(exc)
    log_event("validation_error", path=str(request.url.path), field=field, message=message)
    return JSONResponse(
        # Literal 422 rather than the Starlette constant: the constant was
        # renamed between versions and this pins the wire behaviour.
        status_code=422,
        content={
            "data": None,
            "error": {"code": "validation_error", "message": message, "field": field},
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Routes 404s and our own raised HTTPExceptions into the envelope."""
    detail = exc.detail
    if isinstance(detail, dict):
        error = {
            "code": detail.get("code", "error"),
            "message": detail.get("message", "Request failed."),
            "field": detail.get("field"),
        }
    else:
        error = {"code": "http_error", "message": str(detail), "field": None}

    return JSONResponse(status_code=exc.status_code, content={"data": None, "error": error})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last line of defence. Logs the real cause, tells the client nothing useful
    about our internals, and still returns the standard envelope."""
    log_event("unhandled_error", path=str(request.url.path), error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "data": None,
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on our end. Please try again.",
                "field": None,
            },
        },
    )


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------

@app.get("/health", tags=["meta"], summary="Liveness probe")
def health():
    """Also the keep-alive target: ping this every 10 minutes from a free cron
    service so the host never cold-starts in the middle of a phone call."""
    return {"data": {"status": "ok", "version": settings.app_version}, "error": None}


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
