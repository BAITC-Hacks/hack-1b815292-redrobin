"""Tests for in-memory job lifecycle and TTL behavior."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.errors import AppError
from app.models.domain import Document, DocumentRole
from app.services.job_service import JobService


def make_document(role: DocumentRole, root: Path) -> Document:
    source = root / f"{role.value}.docx"
    source.touch(exist_ok=True)
    return Document(
        id=uuid4(),
        role=role,
        original_name=source.name,
        safe_path=str(source),
        sha256=role.value * 8,
        size_bytes=0,
    )


def make_service(tmp_path: Path) -> JobService:
    settings = Settings(_env_file=None, TEMP_ROOT=tmp_path, JOB_TTL_MINUTES=60)
    return JobService(settings)


def test_only_one_analysis_can_run(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    first = service.create_job()
    second = service.create_job()
    for job in (first, second):
        job.documents = [
            make_document(DocumentRole.BEFORE, job.temp_dir),
            make_document(DocumentRole.AFTER, job.temp_dir),
        ]

    service.begin_analysis(first.id)

    with pytest.raises(AppError) as already_running:
        service.begin_analysis(first.id)
    assert already_running.value.code == "already_running"

    with pytest.raises(AppError) as analysis_busy:
        service.begin_analysis(second.id)
    assert analysis_busy.value.code == "analysis_busy"
    service.clear()


def test_expired_job_returns_410_and_removes_directory(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    job = service.create_job()
    job_dir = job.temp_dir
    job.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    with pytest.raises(AppError) as expired:
        service.get_job(job.id)

    assert expired.value.status_code == 410
    assert expired.value.code == "job_expired"
    assert not job_dir.exists()
