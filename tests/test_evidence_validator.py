"""Tests for evidence validation and hallucination safeguards."""

from app.models.domain import DocumentRole
from app.services.docx_parser import DocxParser
from app.services.evidence_validator import EvidenceValidator
from tests.test_parser import BEFORE_PATH, source_document


def test_evidence_is_built_from_trusted_clause() -> None:
    parsed = DocxParser().parse(source_document(BEFORE_PATH, DocumentRole.BEFORE))
    clause = next(item for item in parsed.clauses if item.number == "3.4")

    evidence = EvidenceValidator().build_presence_evidence(parsed, clause.id)

    assert evidence.validated is True
    assert evidence.quote == clause.raw_text
    assert evidence.clause_number == "3.4"
    assert evidence.quote_start == clause.source_start
    assert evidence.quote_end == clause.source_end


def test_invented_quote_is_rejected() -> None:
    parsed = DocxParser().parse(source_document(BEFORE_PATH, DocumentRole.BEFORE))
    clause = next(item for item in parsed.clauses if item.number == "3.4")

    evidence = EvidenceValidator().build_presence_evidence(
        parsed, clause.id, claimed_quote="Такой цитаты в документе нет"
    )

    assert evidence.validated is False
    assert evidence.rejection_reason == "quote_not_found"


def test_unknown_clause_id_is_rejected() -> None:
    parsed = DocxParser().parse(source_document(BEFORE_PATH, DocumentRole.BEFORE))

    evidence = EvidenceValidator().build_presence_evidence(parsed, "unknown")

    assert evidence.validated is False
    assert evidence.rejection_reason == "unknown_clause_id"
