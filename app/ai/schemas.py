"""Strict Structured Outputs contracts for the six AI stages."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from app.models.ai import ActorType, FunctionKind, MatchRelation, UnitType
from app.models.domain import ChangeType, SemanticRelation, Severity, StrictModel


class ExtractedUnit(StrictModel):
    name: str
    short_name: str | None = None
    unit_type: UnitType
    parent_name: str | None = None
    source_clause_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedRole(StrictModel):
    name: str
    unit_name: str | None = None
    reports_to: str | None = None
    source_clause_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedFunction(StrictModel):
    actor_name: str
    actor_type: ActorType
    kind: FunctionKind
    canonical_text: str
    source_clause_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class EntityExtractionResponse(StrictModel):
    units: list[ExtractedUnit] = Field(default_factory=list)
    roles: list[ExtractedRole] = Field(default_factory=list)
    functions: list[ExtractedFunction] = Field(default_factory=list)


class SemanticMatch(StrictModel):
    before_id: str
    after_id: str | None = None
    relation: MatchRelation
    before_clause_ids: list[str] = Field(default_factory=list)
    after_clause_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class SemanticMatchResponse(StrictModel):
    matches: list[SemanticMatch] = Field(default_factory=list)


class ClassifiedFinding(StrictModel):
    draft_id: str
    match_id: str | None = None
    change_type: ChangeType
    semantic_relation: SemanticRelation
    subject_refs: list[str] = Field(default_factory=list)
    before_clause_ids: list[str] = Field(default_factory=list)
    after_clause_ids: list[str] = Field(default_factory=list)
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    manual_review_required: bool


class ChangeClassificationResponse(StrictModel):
    findings: list[ClassifiedFinding] = Field(default_factory=list)


class RiskType(str, Enum):
    POSSIBLE_DUPLICATE = "possible_duplicate"
    POSSIBLE_CONFLICT = "possible_conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AnalyzedRisk(StrictModel):
    risk_type: RiskType
    subject_refs: list[str] = Field(min_length=1)
    clause_ids: list[str] = Field(min_length=1)
    overlap: str
    why_it_may_matter: str
    counter_evidence_clause_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    manual_check: str


class RiskAnalysisResponse(StrictModel):
    risks: list[AnalyzedRisk] = Field(default_factory=list)


class EvidenceVerdict(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INSUFFICIENT = "insufficient"


class EvidenceDecision(StrictModel):
    draft_id: str
    verdict: EvidenceVerdict
    supporting_clause_ids: list[str] = Field(default_factory=list)
    contradicting_clause_ids: list[str] = Field(default_factory=list)
    reason: str


class EvidenceCheckResponse(StrictModel):
    decisions: list[EvidenceDecision] = Field(default_factory=list)


class ConclusionRecommendation(StrictModel):
    text: str
    based_on_finding_ids: list[str] = Field(min_length=1)


class ConclusionResponse(StrictModel):
    summary: str
    key_finding_ids: list[str] = Field(default_factory=list)
    recommendations: list[ConclusionRecommendation] = Field(default_factory=list)
