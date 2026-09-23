"""Tests for job API contracts and error responses."""

import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.domain import Report
from app.services.analysis_pipeline import AnalysisRun
from app.services.job_service import job_service
from app.services.upload_service import upload_service
from tests.test_parser import AFTER_PATH, BEFORE_PATH

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def docx_files(before: Path = BEFORE_PATH, after: Path = AFTER_PATH):
    return {
        "before_file": (before.name, before.read_bytes(), DOCX_MIME),
        "after_file": (after.name, after.read_bytes(), DOCX_MIME),
    }


@pytest.fixture
def successful_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep HTTP tests free of paid calls while exercising the job integration."""

    def run(job, progress=None):
        if progress:
            progress("semantic_matching", 0.75)
            progress("check_evidence", 0.90)
        before, after = job.documents
        report = Report(
            id=UUID("10000000-0000-0000-0000-000000000001"),
            job_id=job.id,
            generated_at=job.updated_at,
            before_document_id=before.id,
            after_document_id=after.id,
            findings=[],
            rejected_deviation_count=0,
            warnings=["Тестовый AI-клиент: выводы не сформированы."],
            summary="Документы обработаны тестовым AI-клиентом.",
            conclusion="Платные вызовы в unit-тестах отключены.",
        )
        return AnalysisRun(report=report)

    monkeypatch.setattr("app.api.jobs.analysis_pipeline.run", run)


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"].startswith("req-")


def test_full_backend_flow_with_real_documents(
    client: TestClient, successful_analysis: None
) -> None:
    upload = client.post("/api/jobs", files=docx_files())

    assert upload.status_code == 201
    created = upload.json()
    assert created["status"] == "ready"
    assert [item["revision_label"] for item in created["documents"]] == ["8", "9"]
    job_id = created["job_id"]

    pending_result = client.get(f"/api/jobs/{job_id}/result")
    assert pending_result.status_code == 409
    assert pending_result.json()["error"]["code"] == "result_not_ready"

    analyze = client.post(
        f"/api/jobs/{job_id}/analyze",
        json={"confirm_order": False, "force_restart": False},
    )
    assert analyze.status_code == 202
    assert analyze.json()["status"] == "extracting"

    status_response = client.get(f"/api/jobs/{job_id}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "completed_with_warnings"
    assert status_body["progress"] == 1.0

    result = client.get(f"/api/jobs/{job_id}/result")
    assert result.status_code == 200
    report = result.json()
    assert report["job_id"] == job_id
    assert report["findings"] == []
    assert report["warnings"]
    job = job_service.get_job(UUID(job_id))
    for artifact_name in (
        "documents.json",
        "parsed-before.json",
        "parsed-after.json",
        "report.json",
    ):
        artifact = job.temp_dir / artifact_name
        assert artifact.is_file()
        json.loads(artifact.read_text(encoding="utf-8"))


def test_unknown_job_returns_stable_error(client: TestClient) -> None:
    response = client.get("/api/jobs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "job_not_found"
    assert error["request_id"].startswith("req-")


def test_missing_file_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={"before_file": (BEFORE_PATH.name, BEFORE_PATH.read_bytes(), DOCX_MIME)},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_file"
    assert response.json()["error"]["field"] == "after_file"


def test_unsupported_extension_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={
            "before_file": ("before.txt", b"not a docx", "text/plain"),
            "after_file": (AFTER_PATH.name, AFTER_PATH.read_bytes(), DOCX_MIME),
        },
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_corrupted_docx_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        files={
            "before_file": ("before.docx", b"not a zip", DOCX_MIME),
            "after_file": (AFTER_PATH.name, AFTER_PATH.read_bytes(), DOCX_MIME),
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_docx"


def test_oversized_upload_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upload_service.settings, "max_file_mb", 1)
    response = client.post(
        "/api/jobs",
        files={
            "before_file": ("before.docx", b"x" * (1024 * 1024 + 1), DOCX_MIME),
            "after_file": (AFTER_PATH.name, AFTER_PATH.read_bytes(), DOCX_MIME),
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_reversed_order_requires_confirmation(client: TestClient) -> None:
    upload = client.post(
        "/api/jobs", files=docx_files(before=AFTER_PATH, after=BEFORE_PATH)
    )
    job_id = upload.json()["job_id"]

    response = client.post(f"/api/jobs/{job_id}/analyze", json={})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "order_confirmation_required"


def test_identical_documents_produce_empty_clean_report(client: TestClient) -> None:
    upload = client.post(
        "/api/jobs", files=docx_files(before=BEFORE_PATH, after=BEFORE_PATH)
    )
    job_id = upload.json()["job_id"]

    analyze = client.post(f"/api/jobs/{job_id}/analyze", json={})
    result = client.get(f"/api/jobs/{job_id}/result")

    assert analyze.status_code == 202
    assert result.status_code == 200
    assert result.json()["findings"] == []
    assert result.json()["warnings"] == []
    assert "идентичны" in result.json()["summary"].lower()


def test_completed_job_requires_force_restart(
    client: TestClient, successful_analysis: None
) -> None:
    upload = client.post("/api/jobs", files=docx_files())
    job_id = upload.json()["job_id"]
    first_run = client.post(f"/api/jobs/{job_id}/analyze", json={})

    without_force = client.post(f"/api/jobs/{job_id}/analyze", json={})
    with_force = client.post(
        f"/api/jobs/{job_id}/analyze", json={"force_restart": True}
    )

    assert first_run.status_code == 202
    assert without_force.status_code == 422
    assert without_force.json()["error"]["code"] == "invalid_state"
    assert with_force.status_code == 202
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 200
