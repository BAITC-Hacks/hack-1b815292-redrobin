"""Strict Structured Outputs and source-boundary tests."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.schemas import EntityExtractionResponse, ExtractedUnit
from app.models.ai import UnitType
from app.models.domain import DocumentRole
from app.services.docx_parser import DocxParser
from app.services.entity_extractor import EntityExtractor
from tests.test_parser import BEFORE_PATH, source_document


def test_ai_schemas_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExtractedUnit.model_validate(
            {
                "name": "ДИТААД",
                "short_name": "ДИТААД",
                "unit_type": "department",
                "parent_name": None,
                "source_clause_ids": ["c-1"],
                "confidence": 0.8,
                "invented": True,
            }
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_ai_schemas_bound_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ExtractedUnit(
            name="ДИТААД",
            unit_type=UnitType.DEPARTMENT,
            source_clause_ids=["c-1"],
            confidence=confidence,
        )


def test_entity_extractor_rejects_unknown_clause_id() -> None:
    parsed = DocxParser().parse(source_document(BEFORE_PATH, DocumentRole.BEFORE))

    class FakeClient:
        def parse(self, messages, schema):
            del messages, schema
            return EntityExtractionResponse(
                units=[
                    ExtractedUnit(
                        name="Несуществующий департамент",
                        unit_type=UnitType.DEPARTMENT,
                        source_clause_ids=["foreign-clause-id"],
                        confidence=0.9,
                    )
                ]
            )

    extractor = EntityExtractor(
        SimpleNamespace(parse=FakeClient().parse), max_clauses_per_request=1000
    )
    catalog = extractor.extract(parsed)

    assert catalog.units == []
    assert all(
        "foreign-clause-id" not in item.source_clause_ids
        for item in [*catalog.units, *catalog.roles, *catalog.functions]
    )
