"""DOCX upload validation and persistence."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings, get_settings
from app.errors import AppError
from app.models.domain import Document, DocumentRole

DOCX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
    "application/zip",
    "",
}
REQUIRED_DOCX_PARTS = {"[Content_Types].xml", "word/document.xml"}
REVISION_PATTERN = re.compile(
    r"(?:редакц(?:ия|ии)|revision|rev)[^0-9]{0,10}(?:no|№)?\s*(\d+)",
    re.IGNORECASE,
)


class UploadService:
    """Validate and persist untrusted DOCX uploads without extracting them."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def save_document(
        self,
        upload: UploadFile,
        role: DocumentRole,
        job_dir: Path,
        field_name: str,
    ) -> Document:
        original_name = self._clean_display_name(upload.filename)
        if not original_name:
            raise AppError(
                400,
                "missing_file",
                "Выберите обе редакции документа.",
                field=field_name,
            )
        if Path(original_name).suffix.lower() != ".docx":
            raise AppError(
                415,
                "unsupported_media_type",
                "Поддерживаются только файлы DOCX.",
                field=field_name,
            )
        content_type = (upload.content_type or "").lower()
        if content_type not in DOCX_CONTENT_TYPES:
            raise AppError(
                415,
                "unsupported_media_type",
                "Поддерживаются только файлы DOCX.",
                field=field_name,
            )

        destination = job_dir / f"{role.value}.docx"
        digest = hashlib.sha256()
        size = 0
        max_bytes = self.settings.max_file_mb * 1024 * 1024
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise AppError(
                            413,
                            "file_too_large",
                            "Размер файла не должен превышать "
                            f"{self.settings.max_file_mb} МБ.",
                            field=field_name,
                        )
                    digest.update(chunk)
                    output.write(chunk)
            self._validate_docx_container(destination, field_name)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        return Document(
            id=uuid4(),
            role=role,
            original_name=original_name,
            safe_path=str(destination),
            sha256=digest.hexdigest(),
            size_bytes=size,
            revision_label=self._extract_revision_label(original_name),
        )

    def _validate_docx_container(self, file_path: Path, field_name: str) -> None:
        try:
            if not zipfile.is_zipfile(file_path):
                raise zipfile.BadZipFile
            with zipfile.ZipFile(file_path) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if not REQUIRED_DOCX_PARTS.issubset(names):
                    raise zipfile.BadZipFile
                if len(entries) > self.settings.max_zip_entries:
                    raise AppError(
                        422,
                        "invalid_docx",
                        "DOCX содержит слишком много внутренних частей.",
                        field=field_name,
                    )
                uncompressed_size = sum(entry.file_size for entry in entries)
                limit = self.settings.max_uncompressed_mb * 1024 * 1024
                if uncompressed_size > limit:
                    raise AppError(
                        422,
                        "invalid_docx",
                        "Распакованный DOCX превышает допустимый размер.",
                        field=field_name,
                    )
                if any(entry.flag_bits & 0x1 for entry in entries):
                    raise AppError(
                        422,
                        "invalid_docx",
                        "Зашифрованный DOCX не поддерживается.",
                        field=field_name,
                    )
        except AppError:
            raise
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise AppError(
                422,
                "invalid_docx",
                "Файл поврежден или не является DOCX.",
                field=field_name,
            ) from exc

    @staticmethod
    def _clean_display_name(filename: str | None) -> str:
        if not filename:
            return ""
        name = Path(filename.replace("\\", "/")).name
        name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
        return name[:255]

    @staticmethod
    def _extract_revision_label(filename: str) -> str | None:
        match = REVISION_PATTERN.search(Path(filename).stem)
        return match.group(1) if match else None


upload_service = UploadService()
