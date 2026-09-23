"""Deterministic candidate selection with AI only for ambiguous matches."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Union
from uuid import uuid5

from rapidfuzz.fuzz import ratio

from app.ai.client import AIClient, ai_client
from app.ai.prompts import matching_prompt
from app.ai.schemas import SemanticMatchResponse
from app.models.ai import (
    EntityCatalog,
    EntityType,
    Function,
    Match,
    MatchMethod,
    MatchRelation,
    OrgUnit,
    Role,
)
from app.models.domain import Clause, ParsedDocument

Entity = Union[OrgUnit, Role, Function]


@dataclass(frozen=True)
class Candidate:
    entity: Entity
    score: float
    number_match: bool
    section_match: bool
    text_similarity: float


class Matcher:
    """Match entities without treating a changed point number as a new item."""

    def __init__(self, client: AIClient | None = None) -> None:
        self.client = client or ai_client

    def match(
        self,
        before: EntityCatalog,
        after: EntityCatalog,
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ) -> list[Match]:
        matches: list[Match] = []
        for entity_type, before_items, after_items in (
            (EntityType.UNIT, before.units, after.units),
            (EntityType.ROLE, before.roles, after.roles),
            (EntityType.FUNCTION, before.functions, after.functions),
        ):
            matches.extend(
                self._match_group(
                    entity_type,
                    before_items,
                    after_items,
                    before_document,
                    after_document,
                )
            )
        return matches

    def candidates(
        self,
        before: Entity,
        after_items: Sequence[Entity],
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ) -> list[Candidate]:
        """Return at most five candidates using all mandated deterministic cues."""

        before_clauses = self._clauses(before, before_document)
        before_numbers = {item.number for item in before_clauses if item.number}
        before_sections = {
            self._section_number(item, before_document) for item in before_clauses
        } - {None}
        before_text = self._text(before)
        ranked: list[Candidate] = []
        for after in after_items:
            after_clauses = self._clauses(after, after_document)
            after_numbers = {item.number for item in after_clauses if item.number}
            after_sections = {
                self._section_number(item, after_document) for item in after_clauses
            } - {None}
            number_match = bool(before_numbers & after_numbers)
            section_match = bool(before_sections & after_sections)
            text_similarity = ratio(before_text, self._text(after)) / 100.0
            title_similarity = ratio(self._name(before), self._name(after)) / 100.0
            score = min(
                1.0,
                (0.25 if number_match else 0.0)
                + (0.15 if section_match else 0.0)
                + (0.45 * text_similarity)
                + (0.15 * title_similarity),
            )
            if score >= 0.25:
                ranked.append(
                    Candidate(
                        entity=after,
                        score=score,
                        number_match=number_match,
                        section_match=section_match,
                        text_similarity=text_similarity,
                    )
                )
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:5]

    def _match_group(
        self,
        entity_type: EntityType,
        before_items: Sequence[Entity],
        after_items: Sequence[Entity],
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ) -> list[Match]:
        results: list[Match] = []
        used_after: set[str] = set()
        for before in before_items:
            available = [item for item in after_items if item.id not in used_after]
            exact = [
                item for item in available if self._text(item) == self._text(before)
            ]
            if len(exact) == 1:
                selected = exact[0]
                used_after.add(selected.id)
                results.append(
                    self._make_match(
                        entity_type,
                        before,
                        selected,
                        MatchMethod.EXACT_TEXT,
                        MatchRelation.EQUIVALENT,
                        1.0,
                        "Текст сущности совпадает; номер пункта не использован как единственный признак.",
                    )
                )
                continue

            candidates = self.candidates(
                before, available, before_document, after_document
            )
            if not candidates:
                results.append(self._no_match(entity_type, before))
                continue
            top = candidates[0]
            margin = top.score - (candidates[1].score if len(candidates) > 1 else 0.0)
            if top.score >= 0.83 and margin >= 0.12:
                used_after.add(top.entity.id)
                results.append(
                    self._make_match(
                        entity_type,
                        before,
                        top.entity,
                        MatchMethod.FUZZY,
                        MatchRelation.UNCERTAIN,
                        top.score,
                        "Сильный единственный детерминированный кандидат; смысл проверяется на следующем этапе.",
                    )
                )
                continue

            semantic = self._semantic_match(
                before,
                candidates,
                before_document,
                after_document,
            )
            after_id = semantic.after_id
            selected = next(
                (item.entity for item in candidates if item.entity.id == after_id), None
            )
            if selected is not None:
                used_after.add(selected.id)
            results.append(
                self._make_match(
                    entity_type,
                    before,
                    selected,
                    MatchMethod.SEMANTIC,
                    semantic.relation,
                    semantic.confidence,
                    semantic.rationale,
                )
            )
        return results

    def _semantic_match(
        self,
        before: Entity,
        candidates: list[Candidate],
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ):
        payload = {
            "before": self._entity_payload(before, before_document),
            "candidates_reviewed": [
                {
                    **self._entity_payload(item.entity, after_document),
                    "deterministic_score": round(item.score, 4),
                    "same_number": item.number_match,
                    "same_section": item.section_match,
                }
                for item in candidates
            ],
        }
        response = self.client.parse(matching_prompt(payload), SemanticMatchResponse)
        allowed_after = {item.entity.id: item.entity for item in candidates}
        allowed_before_clauses = set(before.source_clause_ids)
        for match in response.matches:
            valid_after = match.after_id is None or match.after_id in allowed_after
            valid_sources = set(match.before_clause_ids).issubset(
                allowed_before_clauses
            )
            if match.after_id is not None and valid_after:
                valid_sources = valid_sources and set(match.after_clause_ids).issubset(
                    set(allowed_after[match.after_id].source_clause_ids)
                )
                valid_sources = valid_sources and bool(
                    match.before_clause_ids and match.after_clause_ids
                )
            elif match.after_id is not None:
                valid_sources = False
            if match.before_id == before.id and valid_after and valid_sources:
                return match

        # Invalid IDs never leave this module as a confident match.
        from app.ai.schemas import SemanticMatch

        return SemanticMatch(
            before_id=before.id,
            after_id=None,
            relation=MatchRelation.UNCERTAIN,
            before_clause_ids=before.source_clause_ids,
            after_clause_ids=[],
            confidence=0.0,
            rationale="AI-сопоставление отклонено из-за неизвестного идентификатора.",
        )

    def _make_match(
        self,
        entity_type: EntityType,
        before: Entity,
        after: Entity | None,
        method: MatchMethod,
        relation: MatchRelation,
        score: float,
        rationale: str,
    ) -> Match:
        target = after.id if after is not None else "none"
        return Match(
            id=f"match-{uuid5(before.document_id, before.id + ':' + target)}",
            entity_type=entity_type,
            before_id=before.id,
            after_id=after.id if after else None,
            method=method,
            relation=relation,
            score=score,
            rationale=rationale,
        )

    def _no_match(self, entity_type: EntityType, before: Entity) -> Match:
        return self._make_match(
            entity_type,
            before,
            None,
            MatchMethod.FUZZY,
            MatchRelation.NO_MATCH,
            0.0,
            "После детерминированного отбора подходящий кандидат не найден.",
        )

    @staticmethod
    def _entity_payload(entity: Entity, parsed: ParsedDocument) -> dict[str, object]:
        clause_index = {clause.id: clause for clause in parsed.clauses}
        return {
            "id": entity.id,
            "name": Matcher._name(entity),
            "text": Matcher._text(entity),
            "actor": getattr(entity, "actor_name", None),
            "clause_ids": entity.source_clause_ids,
            "clauses": [
                {
                    "clause_id": clause_id,
                    "number": clause_index[clause_id].number,
                    "text": clause_index[clause_id].raw_text,
                }
                for clause_id in entity.source_clause_ids
                if clause_id in clause_index
            ],
        }

    @staticmethod
    def _clauses(entity: Entity, parsed: ParsedDocument) -> list[Clause]:
        allowed = set(entity.source_clause_ids)
        return [clause for clause in parsed.clauses if clause.id in allowed]

    @staticmethod
    def _section_number(clause: Clause, parsed: ParsedDocument) -> str | None:
        return next(
            (
                section.number
                for section in parsed.sections
                if section.id == clause.section_id
            ),
            None,
        )

    @staticmethod
    def _name(entity: Entity) -> str:
        return (
            (entity.canonical_text if isinstance(entity, Function) else entity.name)
            .casefold()
            .strip()
        )

    @staticmethod
    def _text(entity: Entity) -> str:
        return Matcher._name(entity)


matcher = Matcher()
