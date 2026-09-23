"""Tests for the manually labelled control set."""

import json
from pathlib import Path

from tests.test_parser import AFTER_PATH, BEFORE_PATH


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
