"""Core domain models shared by backend and AI services."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental contract drift."""

    model_config = ConfigDict(extra="forbid")


class DocumentRole(str, Enum):
    BEFORE = "before"
    AFTER = "after"


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    FAILED = "failed"


class JobStatus(str, Enum):
    READY = "ready"
    EXTRACTING = "extracting"
    ANALYZING = "analyzing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class ChangeType(str, Enum):
    UNIT_PRESERVED = "unit_preserved"
    UNIT_CREATED = "unit_created"
    UNIT_REMOVED = "unit_removed"
    UNIT_TRANSFORMED = "unit_transformed"
    FUNCTION_PRESERVED = "function_preserved"
    FUNCTION_ADDED = "function_added"
    FUNCTION_MISSING = "function_missing"
    FUNCTION_MOVED = "function_moved"
    WORDING_CHANGED = "wording_changed"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    POSSIBLE_CONFLICT = "possible_conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class SemanticRelation(str, Enum):
    EQUIVALENT = "equivalent"
    BROADER = "broader"
    NARROWER = "narrower"
    DIFFERENT = "different"
    NOT_APPLICABLE = "not_applicable"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ManualReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ValidationStatus(str, Enum):
    VERIFIED = "verified"
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"


class EvidenceType(str, Enum):
    PRESENCE = "presence"
    ABSENCE_CHECK = "absence_check"


class Document(StrictModel):
    id: UUID
    role: DocumentRole
    original_name: str
    safe_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    revision_label: str | None = None
    approval_date: date | None = None
    paragraph_count: int = Field(default=0, ge=0)
    status: DocumentStatus = DocumentStatus.UPLOADED


class Section(StrictModel):
    id: str
    document_id: UUID
    title: str
    number: str | None = None
    level: int = Field(default=1, ge=1)
    parent_id: str | None = None
    clause_ids: list[str] = Field(default_factory=list)


class Clause(StrictModel):
    id: str
    document_id: UUID
    section_id: str | None = None
    number: str | None = None
    label: str | None = None
    paragraph_index: int = Field(ge=0)
    source_start: int = Field(ge=0)
    source_end: int = Field(ge=0)
    raw_text: str
    normalized_text: str
    style_name: str | None = None
    numbering_metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_source_range(self) -> Clause:
        if self.source_end < self.source_start:
            raise ValueError("source_end must be greater than or equal to source_start")
        return self


class ParsedDocument(StrictModel):
    document: Document
    sections: list[Section] = Field(default_factory=list)
    clauses: list[Clause] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FindingSubject(StrictModel):
    kind: str
    name: str
    entity_refs: list[str] = Field(default_factory=list)


class Evidence(StrictModel):
    id: str
    document_id: UUID
    document_role: DocumentRole
    document_name: str | None = None
    section_title: str | None = None
    clause_id: str | None = None
    clause_number: str | None = None
    quote: str | None = None
    quote_start: int | None = Field(default=None, ge=0)
    quote_end: int | None = Field(default=None, ge=0)
    evidence_type: EvidenceType
    search_scope_clause_ids: list[str] = Field(default_factory=list)
    search_query: str | None = None
    candidate_count: int | None = Field(default=None, ge=0)
    validated: bool = False
    rejection_reason: str | None = None


class Finding(StrictModel):
    id: str
    change_type: ChangeType
    match_id: str | None = None
    semantic_relation: SemanticRelation
    subject: FindingSubject
    description: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    before_evidence: list[Evidence] = Field(default_factory=list)
    after_evidence: list[Evidence] = Field(default_factory=list)
    clause_numbers: list[str] = Field(default_factory=list)
    rationale: str
    manual_review_status: ManualReviewStatus
    validation_status: ValidationStatus
    recommendation: str | None = None


class Report(StrictModel):
    id: UUID
    job_id: UUID
    generated_at: datetime
    before_document_id: UUID
    after_document_id: UUID
    findings: list[Finding] = Field(default_factory=list)
    rejected_deviation_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    summary: str
    conclusion: str


class JobFailure(StrictModel):
    code: str
    message: str
    retryable: bool = False


class Job(StrictModel):
    id: UUID
    status: JobStatus = JobStatus.READY
    stage: str = "ready"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    expires_at: datetime
    temp_dir: Path
    documents: list[Document] = Field(default_factory=list)
    parsed_documents: dict[DocumentRole, ParsedDocument] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: JobFailure | None = None
    report: Report | None = None
