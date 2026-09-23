"""In-memory job lifecycle and temporary storage coordination."""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.config import Settings, get_settings
from app.errors import AppError
from app.models.domain import Job, JobFailure, JobStatus, Report

RUNNING_STATUSES = {
    JobStatus.EXTRACTING,
    JobStatus.ANALYZING,
    JobStatus.VERIFYING,
}


class JobService:
    """Own in-memory jobs and their isolated temporary directories."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.temp_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[UUID, Job] = {}
        self._expired: set[UUID] = set()
        self._active_job_id: UUID | None = None
        self._lock = threading.RLock()

    def create_job(self) -> Job:
        self.cleanup_expired()
        now = datetime.now(timezone.utc)
        job_id = uuid4()
        temp_dir = self.settings.temp_root / str(job_id)
        temp_dir.mkdir(parents=True, exist_ok=False)
        job = Job(
            id=job_id,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(minutes=self.settings.job_ttl_minutes),
            temp_dir=temp_dir,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get_job(self, job_id: UUID) -> Job:
        self.cleanup_expired()
        with self._lock:
            if job_id in self._expired:
                raise AppError(
                    410,
                    "job_expired",
                    "Временные файлы удалены. Загрузите документы снова.",
                )
            job = self._jobs.get(job_id)
        if job is None:
            raise AppError(404, "job_not_found", "Задание не найдено.")
        return job

    def discard_job(self, job_id: UUID) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if self._active_job_id == job_id:
                self._active_job_id = None
        if job is not None:
            shutil.rmtree(job.temp_dir, ignore_errors=True)

    def begin_analysis(self, job_id: UUID, force_restart: bool = False) -> Job:
        job = self.get_job(job_id)
        with self._lock:
            if self._active_job_id is not None and self._active_job_id != job_id:
                raise AppError(
                    409,
                    "analysis_busy",
                    "Сейчас выполняется другое задание. Повторите позже.",
                    retryable=True,
                )
            if job.status in RUNNING_STATUSES:
                raise AppError(
                    409,
                    "already_running",
                    "Анализ этого задания уже выполняется.",
                    retryable=True,
                )
            if (
                job.status
                in {
                    JobStatus.COMPLETED,
                    JobStatus.COMPLETED_WITH_WARNINGS,
                    JobStatus.FAILED,
                }
                and not force_restart
            ):
                raise AppError(
                    422,
                    "invalid_state",
                    "Для повторного запуска укажите force_restart=true.",
                )
            if len(job.documents) != 2:
                raise AppError(
                    422,
                    "invalid_state",
                    "Для анализа нужны две проверенные редакции документа.",
                )
            if force_restart:
                job.report = None
                job.error = None
            now = datetime.now(timezone.utc)
            job.status = JobStatus.EXTRACTING
            job.stage = "parse_documents"
            job.progress = 0.05
            job.started_at = now
            job.updated_at = now
            self._active_job_id = job_id
        return job

    def update_progress(
        self, job: Job, status: JobStatus, stage: str, progress: float
    ) -> None:
        with self._lock:
            job.status = status
            job.stage = stage
            job.progress = progress
            job.updated_at = datetime.now(timezone.utc)

    def complete(
        self, job: Job, report: Report, warnings: list[str] | None = None
    ) -> None:
        with self._lock:
            if warnings:
                job.warnings.extend(warnings)
            job.report = report
            job.status = (
                JobStatus.COMPLETED_WITH_WARNINGS
                if job.warnings
                else JobStatus.COMPLETED
            )
            job.stage = "completed"
            job.progress = 1.0
            job.updated_at = datetime.now(timezone.utc)
            if self._active_job_id == job.id:
                self._active_job_id = None

    def fail(self, job: Job, code: str, message: str, retryable: bool = False) -> None:
        with self._lock:
            job.status = JobStatus.FAILED
            job.stage = "failed"
            job.error = JobFailure(code=code, message=message, retryable=retryable)
            job.updated_at = datetime.now(timezone.utc)
            if self._active_job_id == job.id:
                self._active_job_id = None

    def write_artifact(self, job: Job, name: str, value: Any) -> Path:
        """Atomically store a JSON stage artifact inside the job directory."""

        target = job.temp_dir / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        payload = self._json_ready(value)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
        return target

    @classmethod
    def _json_ready(cls, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(key): cls._json_ready(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_ready(item) for item in value]
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, (UUID, Path)):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        with self._lock:
            expired_jobs = [
                job
                for job in self._jobs.values()
                if job.expires_at <= now and job.id != self._active_job_id
            ]
            for job in expired_jobs:
                self._jobs.pop(job.id, None)
                self._expired.add(job.id)
        for job in expired_jobs:
            shutil.rmtree(job.temp_dir, ignore_errors=True)
        return len(expired_jobs)

    def clear(self) -> None:
        """Remove all process jobs; used on shutdown and by isolated tests."""

        with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
            self._expired.clear()
            self._active_job_id = None
        for job in jobs:
            shutil.rmtree(job.temp_dir, ignore_errors=True)


job_service = JobService()
