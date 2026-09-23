"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.jobs import router as jobs_router
from app.config import get_settings
from app.errors import AppError
from app.models.api import ErrorDetail, ErrorResponse
from app.services.job_service import job_service


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    del application
    settings = get_settings()
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    yield
    job_service.clear()


app = FastAPI(title="RedRobin", version="0.1.0", lifespan=lifespan)
app.include_router(jobs_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req-{uuid4()}"
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            field=exc.field,
            request_id=_request_id(request),
            retryable=exc.retryable,
        )
    )
    return JSONResponse(
        status_code=exc.status_code, content=payload.model_dump(mode="json")
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del exc
    payload = ErrorResponse(
        error=ErrorDetail(
            code="validation_error",
            message="Проверьте формат и обязательные поля запроса.",
            request_id=_request_id(request),
            retryable=False,
        )
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return a minimal process health signal without invoking AI."""

    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def upload_page(request: Request):
    return templates.TemplateResponse(request=request, name="upload.html")


@app.get("/status/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def status_page(request: Request, job_id: str):
    return templates.TemplateResponse(
        request=request,
        name="status.html",
        context={"job_id": job_id},
    )


@app.get("/result/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def result_page(request: Request, job_id: str):
    from uuid import UUID

    try:
        parsed_id = UUID(job_id)
    except ValueError:
        return RedirectResponse(url="/", status_code=303)
    job = job_service.get_job(parsed_id)
    if job.report is None:
        return RedirectResponse(url=f"/status/{job_id}", status_code=303)
    counts: dict[str, int] = {}
    for finding in job.report.findings:
        key = finding.change_type.value
        counts[key] = counts.get(key, 0) + 1
    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={
            "report": job.report,
            "job_id": job_id,
            "counts": counts,
            "change_types": sorted(counts),
            "severities": sorted(
                {finding.severity.value for finding in job.report.findings}
            ),
        },
    )


@app.get("/ready", tags=["system"])
def ready() -> dict[str, object]:
    settings = get_settings()
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    return {
        "status": "ready",
        "temp_storage": settings.temp_root.is_dir(),
        "ai_configured": bool(settings.openai_api_key and settings.openai_model),
    }


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", f"req-{uuid4()}")
