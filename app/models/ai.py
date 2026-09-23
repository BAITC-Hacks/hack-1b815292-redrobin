"""Internal, source-linked records used between AI pipeline stages.

These records mirror section 9 of the technical specification.  They are not
API alternatives to the core domain models: a :class:`Deviation` is always a
draft and only ``ReportBuilder`` may turn it into a public ``Finding``.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import Field

from app.models.domain import ChangeType, SemanticRelation, Severity, StrictModel


class UnitType(str, Enum):
    BLOCK = "block"
    DEPARTMENT = "department"
    CENTER = "center"
    OTHER = "other"


class ActorType(str, Enum):
    UNIT = "unit"
    ROLE = "role"
    BLOCK = "block"


class FunctionKind(str, Enum):
    TASK = "task"
    FUNCTION = "function"
    RIGHT = "right"
    DUTY = "duty"
    RESTRICTION = "restriction"


class EntityType(str, Enum):
    UNIT = "unit"
    ROLE = "role"
    FUNCTION = "function"


class MatchMethod(str, Enum):
    EXACT_NUMBER = "exact_number"
    EXACT_TEXT = "exact_text"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"
    MANUAL = "manual"


class MatchRelation(str, Enum):
    EQUIVALENT = "equivalent"
    RENAMED = "renamed"
    NARROWER = "narrower"
    BROADER = "broader"
    MOVED = "moved"
    DIFFERENT = "different"
    NO_MATCH = "no_match"
    UNCERTAIN = "uncertain"


class OrgUnit(StrictModel):
    id: str
    document_id: UUID
    name: str
    short_name: str | None = None
    unit_type: UnitType
    parent_unit_id: str | None = None
    source_clause_ids: list[str] = Field(min_length=1)
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class Role(StrictModel):
    id: str
    document_id: UUID
    name: str
    unit_id: str | None = None
    reports_to_role_id: str | None = None
    source_clause_ids: list[str] = Field(min_length=1)
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class Function(StrictModel):
    id: str
    document_id: UUID
    actor_type: ActorType
    actor_id: str
    actor_name: str
    kind: FunctionKind
    canonical_text: str
    source_clause_ids: list[str] = Field(min_length=1)
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class EntityCatalog(StrictModel):
    document_id: UUID
    units: list[OrgUnit] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    functions: list[Function] = Field(default_factory=list)


class Match(StrictModel):
    id: str
    entity_type: EntityType
    before_id: str
    after_id: str | None = None
    method: MatchMethod
    relation: MatchRelation
    score: float = Field(ge=0.0, le=1.0)
    rationale: str


class Deviation(StrictModel):
    id: str
    match_id: str | None = None
    change_type: ChangeType
    semantic_relation: SemanticRelation
    subject_refs: list[str] = Field(default_factory=list)
    before_clause_ids: list[str] = Field(default_factory=list)
    after_clause_ids: list[str] = Field(default_factory=list)
    description: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    manual_review_required: bool
