"""Server-rendered UX smoke tests."""

from fastapi.testclient import TestClient

from app.main import app
from app.services.job_service import job_service
from app.services.report_builder import report_builder
from tests.test_api import docx_files


def test_upload_and_status_pages_expose_required_controls() -> None:
    with TestClient(app) as client:
        upload = client.get("/")
        assert upload.status_code == 200
        assert "Редакция до" in upload.text
        assert "Редакция после" in upload.text
        assert "Только обезличенные документы" in upload.text
        assert "swap-files" in upload.text

        status = client.get("/status/00000000-0000-0000-0000-000000000001")
        assert status.status_code == 200
        assert "Извлекаем разделы и пункты" in status.text
        assert "Проверка источников" in status.text
        assert "retry-analysis" in status.text


def test_result_page_shows_report_sections_and_filters() -> None:
    with TestClient(app) as client:
        created = client.post("/api/jobs", files=docx_files()).json()
        from uuid import UUID

        job = job_service.get_job(UUID(created["job_id"]))
        job.report = report_builder.build_identical_report(job)
        response = client.get(f"/result/{job.id}")

        assert response.status_code == 200
        assert "Проверенный отчёт" in response.text
        assert "change-filter" in response.text
        assert "severity-filter" in response.text
        assert "Проверка источника" not in response.text  # no empty fake cards
