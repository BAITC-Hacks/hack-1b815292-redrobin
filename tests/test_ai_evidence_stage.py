"""Regression tests for AI-5 omissions and deterministic evidence fallback."""

from app.ai.schemas import EvidenceCheckResponse, EvidenceVerdict
from app.models.ai import Deviation
from app.models.domain import (
    ChangeType,
    DocumentRole,
    SemanticRelation,
    Severity,
)
from app.services.docx_parser import DocxParser
from app.services.risk_analyzer import RiskAnalyzer
from tests.test_parser import AFTER_PATH, BEFORE_PATH, source_document


class EmptyEvidenceClient:
    def parse(self, messages, schema):
        del messages, schema
        return EvidenceCheckResponse(decisions=[])


def parsed_documents():
    parser = DocxParser()
    return (
        parser.parse(source_document(BEFORE_PATH, DocumentRole.BEFORE)),
        parser.parse(source_document(AFTER_PATH, DocumentRole.AFTER)),
    )


def wording_deviation(index: int, before_id: str, after_id: str) -> Deviation:
    return Deviation(
        id=f"draft-{index}",
        change_type=ChangeType.WORDING_CHANGED,
        semantic_relation=SemanticRelation.EQUIVALENT,
        subject_refs=[f"function-{index}"],
        before_clause_ids=[before_id],
        after_clause_ids=[after_id],
        description="Формулировка изменена",
        severity=Severity.INFO,
        confidence=0.99,
        rationale="Переданные пункты различаются редакционно.",
        manual_review_required=False,
    )


def test_ai5_omission_does_not_downgrade_complete_two_sided_evidence() -> None:
    before, after = parsed_documents()
    before_clause = next(item for item in before.clauses if item.number == "1.3")
    after_clause = next(item for item in after.clauses if item.number == "1.3")
    deviations = [
        wording_deviation(index, before_clause.id, after_clause.id)
        for index in range(43)
    ]

    decisions = RiskAnalyzer(EmptyEvidenceClient()).check_evidence(
        deviations, before, after
    )

    assert len(decisions) == 43
    assert {item.verdict for item in decisions.values()} == {EvidenceVerdict.SUPPORTED}
    assert all("AI-5 не вернул решение" in item.reason for item in decisions.values())


def test_ai5_omission_keeps_unproven_loss_insufficient() -> None:
    before, after = parsed_documents()
    before_clause = next(item for item in before.clauses if item.number == "9.15")
    deviation = Deviation(
        id="draft-missing",
        change_type=ChangeType.FUNCTION_MISSING,
        semantic_relation=SemanticRelation.NOT_APPLICABLE,
        subject_refs=["function-old"],
        before_clause_ids=[before_clause.id],
        after_clause_ids=[],
        description="Функция не найдена",
        severity=Severity.MEDIUM,
        confidence=0.9,
        rationale="Однозначная новая пара отсутствует.",
        manual_review_required=True,
    )

    decision = RiskAnalyzer(EmptyEvidenceClient()).check_evidence(
        [deviation], before, after
    )[deviation.id]

    assert decision.verdict == EvidenceVerdict.INSUFFICIENT
    assert decision.supporting_clause_ids == []
