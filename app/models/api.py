"""API request, response, and error models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.models.domain import DocumentRole, JobFailure, JobStatus, StrictModel


class ErrorDetail(StrictModel):
    code: str
    message: str
    field: str | None = None
    request_id: str
    retryable: bool = False


class ErrorResponse(StrictModel):
    error: ErrorDetail


class UploadedDocumentSummary(StrictModel):
    role: DocumentRole
    name: str
    revision_label: str | None = None


class JobCreateResponse(StrictModel):
    job_id: UUID
    status: JobStatus
    documents: list[UploadedDocumentSummary]
    warnings: list[str] = Field(default_factory=list)


class AnalyzeRequest(StrictModel):
    confirm_order: bool = False
    force_restart: bool = False


class AnalyzeResponse(StrictModel):
    job_id: UUID
    status: JobStatus
    stage: str
    progress: float = Field(ge=0.0, le=1.0)


class JobStatusResponse(StrictModel):
    job_id: UUID
    status: JobStatus
    stage: str
    progress: float = Field(ge=0.0, le=1.0)
    started_at: datetime | None = None
    updated_at: datetime
    warnings: list[str] = Field(default_factory=list)
    error: JobFailure | None = None
