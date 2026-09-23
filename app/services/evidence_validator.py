"""Evidence identifier and exact-quote validation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import uuid4

from app.models.domain import Clause, Evidence, EvidenceType, ParsedDocument


class EvidenceValidator:
    """Resolve evidence from trusted parsed clauses rather than AI text."""

    def build_presence_evidence(
        self,
        parsed: ParsedDocument,
        clause_id: str,
        claimed_quote: str | None = None,
    ) -> Evidence:
        clause_index: dict[str, Clause] = {
            clause.id: clause for clause in parsed.clauses
        }
        clause = clause_index.get(clause_id)
        if clause is None:
            return Evidence(
                id=f"ev-{uuid4()}",
                document_id=parsed.document.id,
                document_role=parsed.document.role,
                document_name=parsed.document.original_name,
                clause_id=clause_id,
                evidence_type=EvidenceType.PRESENCE,
                validated=False,
                rejection_reason="unknown_clause_id",
            )

        quote = clause.raw_text
        if claimed_quote is not None and not self._is_exact_substring(
            claimed_quote, clause.raw_text
        ):
            return Evidence(
                id=f"ev-{uuid4()}",
                document_id=parsed.document.id,
                document_role=parsed.document.role,
                document_name=parsed.document.original_name,
                section_title=self._section_title(parsed, clause.section_id),
                clause_id=clause.id,
                clause_number=clause.number,
                quote=claimed_quote,
                evidence_type=EvidenceType.PRESENCE,
                validated=False,
                rejection_reason="quote_not_found",
            )

        return Evidence(
            id=f"ev-{uuid4()}",
            document_id=parsed.document.id,
            document_role=parsed.document.role,
            document_name=parsed.document.original_name,
            section_title=self._section_title(parsed, clause.section_id),
            clause_id=clause.id,
            clause_number=clause.number,
            quote=quote,
            quote_start=clause.source_start,
            quote_end=clause.source_end,
            evidence_type=EvidenceType.PRESENCE,
            validated=True,
        )

    def build_absence_evidence(
        self,
        parsed: ParsedDocument,
        search_query: str,
        search_scope_clause_ids: Sequence[str] | None = None,
    ) -> Evidence:
        """Record a reproducible literal absence check over trusted clauses."""

        clause_index = {clause.id: clause for clause in parsed.clauses}
        scope = list(search_scope_clause_ids or clause_index)
        if not set(scope).issubset(clause_index):
            return Evidence(
                id=f"ev-{uuid4()}",
                document_id=parsed.document.id,
                document_role=parsed.document.role,
                document_name=parsed.document.original_name,
                evidence_type=EvidenceType.ABSENCE_CHECK,
                search_scope_clause_ids=scope,
                search_query=search_query,
                validated=False,
                rejection_reason="unknown_clause_id",
            )
        normalized_query = self._normalize_spaces(search_query).casefold()
        candidates = sum(
            normalized_query
            in self._normalize_spaces(clause_index[item].raw_text).casefold()
            for item in scope
        )
        return Evidence(
            id=f"ev-{uuid4()}",
            document_id=parsed.document.id,
            document_role=parsed.document.role,
            document_name=parsed.document.original_name,
            evidence_type=EvidenceType.ABSENCE_CHECK,
            search_scope_clause_ids=scope,
            search_query=search_query,
            candidate_count=candidates,
            validated=candidates == 0,
            rejection_reason=None if candidates == 0 else "candidate_found",
        )

    @staticmethod
    def _normalize_spaces(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def _is_exact_substring(self, quote: str, source: str) -> bool:
        return self._normalize_spaces(quote) in self._normalize_spaces(source)

    @staticmethod
    def _section_title(parsed: ParsedDocument, section_id: str | None) -> str | None:
        if section_id is None:
            return None
        for section in parsed.sections:
            if section.id == section_id:
                return section.title
        return None


evidence_validator = EvidenceValidator()
