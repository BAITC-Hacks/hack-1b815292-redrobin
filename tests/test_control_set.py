"""Tests for the manually labelled control set."""

import json
from pathlib import Path

from app.models.domain import ChangeType, DocumentRole
from app.services.docx_parser import DocxParser
from app.services.risk_analyzer import RiskAnalyzer
from tests.test_parser import AFTER_PATH, BEFORE_PATH, source_document


def test_priority_control_set_is_complete_and_grounded() -> None:
    fixtures = json.loads(
        Path("tests/fixtures/control_set.json").read_text(encoding="utf-8")
    )
    by_id = {item["id"]: item for item in fixtures}
    assert {"C-01", "C-03", "C-11", "C-13", "C-18"}.issubset(by_id)

    before_text = BEFORE_PATH.with_suffix(BEFORE_PATH.suffix + ".txt")
    after_text = AFTER_PATH.with_suffix(AFTER_PATH.suffix + ".txt")
    # Text copies live in materials/extracted, while DOCX remains source of truth.
    before = Path("materials/extracted") / before_text.name
    after = Path("materials/extracted") / after_text.name
    before_content = before.read_text(encoding="utf-8")
    after_content = after.read_text(encoding="utf-8")
    for item in fixtures:
        assert item["expected_change_types"]
        assert item["before_marker"] in before_content
        assert item["after_marker"] in after_content

    assert "possible_duplicate" in by_id["C-18"]["forbidden_change_types"]


def test_generic_clause_fallback_covers_priority_text_changes() -> None:
    parser = DocxParser()
    before = parser.parse(source_document(BEFORE_PATH, DocumentRole.BEFORE))
    after = parser.parse(source_document(AFTER_PATH, DocumentRole.AFTER))

    deviations = RiskAnalyzer().classify_clause_fallbacks(before, after)
    before_numbers = {clause.id: clause.number for clause in before.clauses}
    types_by_number: dict[str, set[ChangeType]] = {}
    for deviation in deviations:
        for clause_id in deviation.before_clause_ids:
            number = before_numbers.get(clause_id)
            if number:
                types_by_number.setdefault(number, set()).add(deviation.change_type)

    assert types_by_number["9.15"] == {
        ChangeType.FUNCTION_MISSING,
        ChangeType.FUNCTION_ADDED,
    }
    assert ChangeType.WORDING_CHANGED in types_by_number["1.3"]
