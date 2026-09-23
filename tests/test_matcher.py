"""Tests for semantic matching guardrails."""

from datetime import date
from pathlib import Path
from uuid import uuid4

from app.ai.schemas import SemanticMatch, SemanticMatchResponse
from app.models.ai import (
    ActorType,
    Function,
    FunctionKind,
    MatchRelation,
)
from app.models.domain import Clause, Document, DocumentRole, ParsedDocument
from app.services.matcher import Candidate, Matcher


class FakeSemanticClient:
    def parse(self, messages, schema):  # noqa: ANN001
        return SemanticMatchResponse(
            matches=[
                SemanticMatch(
                    before_id="before-function",
                    after_id="unknown-after-id",
                    relation=MatchRelation.EQUIVALENT,
                    before_clause_ids=["before-clause"],
                    after_clause_ids=["after-clause"],
                    confidence=0.92,
                    rationale="Invalid candidate id from provider.",
                )
            ]
        )


def make_document(role: DocumentRole) -> Document:
    return Document(
        id=uuid4(),
        role=role,
        original_name=f"{role.value}.docx",
        safe_path=str(Path(f"{role.value}.docx")),
        sha256="0" * 64,
        size_bytes=1,
        approval_date=date(2026, 1, 1),
    )


def make_clause(document_id, clause_id: str, text: str) -> Clause:  # noqa: ANN001
    return Clause(
        id=clause_id,
        document_id=document_id,
        paragraph_index=0,
        source_start=0,
        source_end=len(text),
        raw_text=text,
        normalized_text=text.casefold(),
    )


def test_semantic_match_rejects_unknown_after_id_without_crashing() -> None:
    before_doc = make_document(DocumentRole.BEFORE)
    after_doc = make_document(DocumentRole.AFTER)
    before = Function(
        id="before-function",
        document_id=before_doc.id,
        actor_type=ActorType.UNIT,
        actor_id="before-unit",
        actor_name="Audit",
        kind=FunctionKind.FUNCTION,
        canonical_text="prepare annual audit plan",
        source_clause_ids=["before-clause"],
        extraction_confidence=0.9,
    )
    after = Function(
        id="valid-after-function",
        document_id=after_doc.id,
        actor_type=ActorType.UNIT,
        actor_id="after-unit",
        actor_name="Audit",
        kind=FunctionKind.FUNCTION,
        canonical_text="prepare annual audit plan",
        source_clause_ids=["after-clause"],
        extraction_confidence=0.9,
    )

    result = Matcher(FakeSemanticClient())._semantic_match(
        before,
        [
            Candidate(
                entity=after,
                score=0.7,
                number_match=False,
                section_match=False,
                text_similarity=0.7,
            )
        ],
        ParsedDocument(
            document=before_doc,
            clauses=[
                make_clause(before_doc.id, "before-clause", "prepare annual audit plan")
            ],
        ),
        ParsedDocument(
            document=after_doc,
            clauses=[
                make_clause(after_doc.id, "after-clause", "prepare annual audit plan")
            ],
        ),
    )

    assert result.before_id == "before-function"
    assert result.after_id is None
    assert result.relation == MatchRelation.UNCERTAIN
