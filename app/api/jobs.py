"""Job upload, analysis, status, and result endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile, status

from app.errors import AppError
from app.models.api import (
    AnalyzeRequest,
    AnalyzeResponse,
    JobCreateResponse,
    JobStatusResponse,
    UploadedDocumentSummary,
)
from app.models.domain import DocumentRole, Job, JobStatus, Report
from app.services.analysis_pipeline import analysis_pipeline
from app.services.docx_parser import docx_parser
from app.services.job_service import job_service
from app.services.upload_service import upload_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    request: Request,
    before_file: Annotated[UploadFile | None, File()] = None,
    after_file: Annotated[UploadFile | None, File()] = None,
) -> JobCreateResponse:
    del request
    if before_file is None:
        raise AppError(
            400,
            "missing_file",
            "Выберите документ редакции до.",
            field="before_file",
        )
    if after_file is None:
        raise AppError(
            400,
            "missing_file",
            "Выберите документ редакции после.",
            field="after_file",
        )

    job = job_service.create_job()
    try:
        before = await upload_service.save_document(
            before_file, DocumentRole.BEFORE, job.temp_dir, "before_file"
        )
        after = await upload_service.save_document(
            after_file, DocumentRole.AFTER, job.temp_dir, "after_file"
        )
        job.documents = [before, after]
        if _order_is_reversed(job):
            job.warnings.append("Похоже, редакции загружены в обратном порядке.")
        job_service.write_artifact(job, "documents.json", job.documents)
    except Exception:
        job_service.discard_job(job.id)
        raise

    return JobCreateResponse(
        job_id=job.id,
        status=job.status,
        documents=[
            UploadedDocumentSummary(
                role=document.role,
                name=document.original_name,
                revision_label=document.revision_label,
            )
            for document in job.documents
        ],
        warnings=job.warnings,
    )


@router.post(
    "/{job_id}/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def analyze_job(
    job_id: UUID, payload: AnalyzeRequest, background_tasks: BackgroundTasks
) -> AnalyzeResponse:
    job = job_service.get_job(job_id)
    if _order_is_reversed(job) and not payload.confirm_order:
        raise AppError(
            409,
            "order_confirmation_required",
            "Похоже, редакции загружены в обратном порядке.",
        )
    job_service.begin_analysis(job_id, force_restart=payload.force_restart)
    response = AnalyzeResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
    )
    background_tasks.add_task(_run_analysis, job.id)
    return response


@router.get("/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: UUID) -> JobStatusResponse:
    job = job_service.get_job(job_id)
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        started_at=job.started_at,
        updated_at=job.updated_at,
        warnings=job.warnings,
        error=job.error,
    )


@router.get("/{job_id}/result", response_model=Report)
def get_job_result(job_id: UUID) -> Report:
    job = job_service.get_job(job_id)
    if job.report is None:
        raise AppError(
            409,
            "result_not_ready",
            "Результат анализа еще не готов.",
            retryable=job.status
            in {
                JobStatus.EXTRACTING,
                JobStatus.ANALYZING,
                JobStatus.VERIFYING,
            },
        )
    return job.report


def _run_analysis(job_id: UUID) -> None:
    job = job_service.get_job(job_id)
    try:
        for index, document in enumerate(job.documents):
            parsed = job.parsed_documents.get(document.role)
            if parsed is None or parsed.document.sha256 != document.sha256:
                parsed = docx_parser.parse(document)
                job.parsed_documents[document.role] = parsed
                job_service.write_artifact(
                    job, f"parsed-{document.role.value}.json", parsed
                )
            job_service.update_progress(
                job,
                JobStatus.EXTRACTING,
                "parse_documents",
                0.25 + (index * 0.20),
            )

        def update_ai_progress(stage: str, progress: float) -> None:
            status_value = (
                JobStatus.VERIFYING if progress >= 0.90 else JobStatus.ANALYZING
            )
            job_service.update_progress(job, status_value, stage, progress)

        run = analysis_pipeline.run(job, progress=update_ai_progress)
        for name, artifact in (
            ("entities-before.json", run.before_entities),
            ("entities-after.json", run.after_entities),
            ("matches.json", run.matches),
            ("deviations.json", run.deviations),
            ("evidence-decisions.json", run.evidence_decisions),
        ):
            if artifact is not None:
                job_service.write_artifact(job, name, artifact)
        job_service.write_artifact(job, "report.json", run.report)
        job_service.complete(job, run.report, warnings=run.report.warnings)
    except AppError as exc:
        job_service.fail(job, exc.code, exc.message, exc.retryable)
    except Exception:  # noqa: BLE001 - background tasks must end in a stable state
        job_service.fail(
            job,
            "analysis_failed",
            "Не удалось завершить обработку документов.",
            retryable=True,
        )


def _order_is_reversed(job: Job) -> bool:
    if len(job.documents) != 2:
        return False
    before, after = job.documents
    if before.revision_label is None or after.revision_label is None:
        return False
    try:
        return int(before.revision_label) > int(after.revision_label)
    except ValueError:
        return before.revision_label > after.revision_label
