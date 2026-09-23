"""Conservative change classification and risk candidate analysis."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations
from typing import Union
from uuid import UUID, uuid5

from rapidfuzz.fuzz import ratio

from app.ai.client import AIClient, ai_client
from app.ai.prompts import classification_prompt, evidence_prompt, risk_prompt
from app.ai.schemas import (
    ChangeClassificationResponse,
    EvidenceCheckResponse,
    EvidenceDecision,
    EvidenceVerdict,
    RiskAnalysisResponse,
    RiskType,
)
from app.models.ai import (
    Deviation,
    EntityCatalog,
    EntityType,
    Function,
    Match,
    MatchRelation,
    OrgUnit,
    Role,
)
from app.models.domain import ChangeType, ParsedDocument, SemanticRelation, Severity

Entity = Union[OrgUnit, Role, Function]
CLASSIFICATION_BATCH_SIZE = 20
EVIDENCE_BATCH_SIZE = 25
MAX_RISK_CANDIDATES = 24


class RiskAnalyzer:
    """Create cautious drafts; deterministic validation still happens later."""

    def __init__(self, client: AIClient | None = None) -> None:
        self.client = client or ai_client

    def classify_changes(
        self,
        before_catalog: EntityCatalog,
        after_catalog: EntityCatalog,
        matches: list[Match],
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ) -> list[Deviation]:
        before_entities = self._entity_index(before_catalog)
        after_entities = self._entity_index(after_catalog)
        deviations: list[Deviation] = []
        ai_inputs: list[dict[str, object]] = []
        matched_after = {match.after_id for match in matches if match.after_id}

        for match in matches:
            before = before_entities[match.before_id]
            after = after_entities.get(match.after_id or "")
            if match.entity_type == EntityType.UNIT:
                if after is not None and match.relation == MatchRelation.EQUIVALENT:
                    deviations.append(
                        self._draft(
                            before.document_id,
                            match,
                            ChangeType.UNIT_PRESERVED,
                            SemanticRelation.EQUIVALENT,
                            [before.id, after.id],
                            before.source_clause_ids,
                            after.source_clause_ids,
                            before.name,
                            Severity.INFO,
                            min(match.score, 1.0),
                            "Подразделение найдено в обеих редакциях.",
                            False,
                        )
                    )
                elif after is None:
                    deviations.append(self._insufficient_for_unmatched(before, match))
                else:
                    ai_inputs.append(
                        self._classification_input(
                            match, before, after, before_document, after_document
                        )
                    )
                continue

            if match.entity_type == EntityType.FUNCTION:
                if after is None:
                    deviations.append(self._insufficient_for_unmatched(before, match))
                elif match.relation == MatchRelation.EQUIVALENT:
                    if self._numbers(before, before_document) != self._numbers(
                        after, after_document
                    ):
                        deviations.append(
                            self._draft(
                                before.document_id,
                                match,
                                ChangeType.FUNCTION_PRESERVED,
                                SemanticRelation.EQUIVALENT,
                                [before.id, after.id],
                                before.source_clause_ids,
                                after.source_clause_ids,
                                before.canonical_text,
                                Severity.INFO,
                                match.score,
                                "Функция сохранена, хотя номер источника изменился.",
                                False,
                            )
                        )
                else:
                    ai_inputs.append(
                        self._classification_input(
                            match, before, after, before_document, after_document
                        )
                    )
                continue

            if after is None:
                deviations.append(self._insufficient_for_unmatched(before, match))

        for after in [*after_catalog.units, *after_catalog.functions]:
            if after.id in matched_after:
                continue
            draft_id = self._draft_id(after.document_id, "added:" + after.id)
            ai_inputs.append(
                {
                    "draft_id": draft_id,
                    "match_id": None,
                    "kind": "unmatched_after",
                    "before": None,
                    "after": self._entity_payload(after, after_document),
                    "allowed_change_types": [
                        ChangeType.UNIT_CREATED.value
                        if isinstance(after, OrgUnit)
                        else ChangeType.FUNCTION_ADDED.value,
                        ChangeType.INSUFFICIENT_EVIDENCE.value,
                    ],
                }
            )

        deviations.extend(
            self._classify_with_ai(
                ai_inputs,
                before_catalog.document_id,
                before_document,
                after_document,
            )
        )
        return self._deduplicate(deviations)

    def analyze_risks(
        self,
        catalog: EntityCatalog,
        parsed: ParsedDocument,
    ) -> list[Deviation]:
        clause_index = {clause.id: clause for clause in parsed.clauses}
        candidates: list[dict[str, object]] = []

        scored_pairs = []
        for first, second in combinations(catalog.functions, 2):
            if first.actor_id == second.actor_id:
                continue
            similarity = ratio(first.canonical_text, second.canonical_text) / 100.0
            if similarity < 0.86:
                continue
            hierarchical = self._hierarchical(first, second, catalog)
            scored_pairs.append((similarity, first, second, hierarchical))
        for similarity, first, second, hierarchical in sorted(
            scored_pairs, key=lambda item: item[0], reverse=True
        )[:MAX_RISK_CANDIDATES]:
            if hierarchical:
                continue
            candidates.append(
                {
                    "candidate_type": "duplicate",
                    "subject_refs": [first.id, second.id],
                    "independent_actors": [first.actor_id, second.actor_id],
                    "hierarchical_relation": False,
                    "similarity": round(similarity, 4),
                    "functions": [
                        self._entity_payload(first, parsed),
                        self._entity_payload(second, parsed),
                    ],
                }
            )

        conflict_clauses = [
            clause
            for clause in parsed.clauses
            if "конфликт" in clause.normalized_text
            and "потенциаль" in clause.normalized_text
        ]
        for clause in conflict_clauses[:8]:
            linked = [
                function.id
                for function in catalog.functions
                if clause.id in function.source_clause_ids
            ]
            candidates.append(
                {
                    "candidate_type": "conflict",
                    "subject_refs": linked or [f"risk:{clause.id}"],
                    "direct_document_wording": True,
                    "clauses": [
                        {
                            "clause_id": clause.id,
                            "number": clause.number,
                            "text": clause.raw_text,
                        }
                    ],
                }
            )

        if not candidates:
            return []
        response = self.client.parse(
            risk_prompt({"risk_candidates": candidates}), RiskAnalysisResponse
        )
        allowed_clause_ids = set(clause_index)
        allowed_subject_refs = {
            ref for candidate in candidates for ref in candidate["subject_refs"]
        }
        functions = {function.id: function for function in catalog.functions}
        deviations: list[Deviation] = []
        for risk in response.risks:
            if not set(risk.clause_ids).issubset(allowed_clause_ids):
                continue
            if not set(risk.counter_evidence_clause_ids).issubset(allowed_clause_ids):
                continue
            if not set(risk.subject_refs).issubset(allowed_subject_refs):
                continue
            risk_type = risk.risk_type
            if risk_type == RiskType.POSSIBLE_DUPLICATE:
                linked = [functions.get(ref) for ref in risk.subject_refs]
                linked = [item for item in linked if item is not None]
                valid_duplicate = (
                    len(linked) >= 2
                    and len({item.actor_id for item in linked}) >= 2
                    and all(
                        set(item.source_clause_ids) & set(risk.clause_ids)
                        for item in linked[:2]
                    )
                    and not self._hierarchical(linked[0], linked[1], catalog)
                )
                if not valid_duplicate:
                    risk_type = RiskType.INSUFFICIENT_EVIDENCE
            change_type = ChangeType(risk_type.value)
            deviations.append(
                Deviation(
                    id=self._draft_id(
                        catalog.document_id,
                        "risk:" + ":".join(sorted(risk.subject_refs)),
                    ),
                    change_type=change_type,
                    semantic_relation=SemanticRelation.NOT_APPLICABLE,
                    subject_refs=risk.subject_refs,
                    before_clause_ids=[],
                    after_clause_ids=risk.clause_ids,
                    description=self._description(
                        change_type, " / ".join(risk.subject_refs)
                    ),
                    severity=(
                        Severity.HIGH
                        if change_type == ChangeType.POSSIBLE_CONFLICT
                        else Severity.MEDIUM
                    ),
                    confidence=risk.confidence,
                    rationale=(
                        f"{risk.overlap} {risk.why_it_may_matter} "
                        f"Ручная проверка: {risk.manual_check}"
                    ).strip(),
                    manual_review_required=True,
                )
            )
        return deviations

    def check_evidence(
        self,
        deviations: list[Deviation],
        before: ParsedDocument,
        after: ParsedDocument,
    ) -> dict[str, EvidenceDecision]:
        allowed_ids = {clause.id for clause in [*before.clauses, *after.clauses]}
        decisions: dict[str, EvidenceDecision] = {}
        for batch in self._batches(deviations, EVIDENCE_BATCH_SIZE):
            payload = {
                "drafts": [item.model_dump(mode="json") for item in batch],
                "clauses": self._referenced_clauses(batch, before, after),
            }
            response = self.client.parse(
                evidence_prompt(payload), EvidenceCheckResponse
            )
            batch_ids = {item.id for item in batch}
            for decision in response.decisions:
                if decision.draft_id not in batch_ids:
                    continue
                if not set(decision.supporting_clause_ids).issubset(allowed_ids):
                    continue
                if not set(decision.contradicting_clause_ids).issubset(allowed_ids):
                    continue
                decisions[decision.draft_id] = decision
        for deviation in deviations:
            if deviation.id not in decisions:
                decisions[deviation.id] = EvidenceDecision(
                    draft_id=deviation.id,
                    verdict=EvidenceVerdict.INSUFFICIENT,
                    supporting_clause_ids=[
                        *deviation.before_clause_ids,
                        *deviation.after_clause_ids,
                    ],
                    contradicting_clause_ids=[],
                    reason="AI-5 не вернул проверяемое решение для черновика.",
                )
        return decisions

    def _classify_with_ai(
        self,
        inputs: list[dict[str, object]],
        namespace: UUID,
        before: ParsedDocument,
        after: ParsedDocument,
    ) -> list[Deviation]:
        allowed_clause_ids = {clause.id for clause in [*before.clauses, *after.clauses]}
        deviations: list[Deviation] = []
        for batch in self._batches(inputs, CLASSIFICATION_BATCH_SIZE):
            response = self.client.parse(
                classification_prompt({"change_candidates": batch}),
                ChangeClassificationResponse,
            )
            allowed_drafts = {str(item["draft_id"]): item for item in batch}
            for finding in response.findings:
                source = allowed_drafts.get(finding.draft_id)
                if source is None:
                    continue
                source_refs = {
                    str(ref)
                    for side in (source.get("before"), source.get("after"))
                    if side
                    for ref in side.get("clause_ids", [])
                }
                returned_refs = {*finding.before_clause_ids, *finding.after_clause_ids}
                if not returned_refs.issubset(source_refs & allowed_clause_ids):
                    continue
                change_type = finding.change_type
                if change_type in {
                    ChangeType.FUNCTION_MISSING,
                    ChangeType.POSSIBLE_DUPLICATE,
                    ChangeType.POSSIBLE_CONFLICT,
                } and (not returned_refs or not finding.rationale.strip()):
                    change_type = ChangeType.INSUFFICIENT_EVIDENCE
                subject_name = self._candidate_subject_name(source)
                deviations.append(
                    Deviation(
                        id=finding.draft_id,
                        match_id=finding.match_id,
                        change_type=change_type,
                        semantic_relation=finding.semantic_relation,
                        subject_refs=finding.subject_refs,
                        before_clause_ids=finding.before_clause_ids,
                        after_clause_ids=finding.after_clause_ids,
                        description=self._description(change_type, subject_name),
                        severity=finding.severity,
                        confidence=finding.confidence,
                        rationale=finding.rationale,
                        manual_review_required=finding.manual_review_required,
                    )
                )
        del namespace
        return deviations

    def _classification_input(
        self,
        match: Match,
        before: Entity,
        after: Entity,
        before_document: ParsedDocument,
        after_document: ParsedDocument,
    ) -> dict[str, object]:
        return {
            "draft_id": self._draft_id(before.document_id, "change:" + match.id),
            "match_id": match.id,
            "match_relation": match.relation.value,
            "before": self._entity_payload(before, before_document),
            "after": self._entity_payload(after, after_document),
        }

    def _insufficient_for_unmatched(self, entity: Entity, match: Match) -> Deviation:
        return self._draft(
            entity.document_id,
            match,
            ChangeType.INSUFFICIENT_EVIDENCE,
            SemanticRelation.NOT_APPLICABLE,
            [entity.id],
            entity.source_clause_ids,
            [],
            self._entity_name(entity),
            Severity.MEDIUM,
            max(0.0, min(match.score, 0.5)),
            (
                "Однозначная пара не найдена. Это не доказывает потерю функции "
                "или роли после реорганизации."
            ),
            True,
        )

    def _draft(
        self,
        namespace: UUID,
        match: Match,
        change_type: ChangeType,
        relation: SemanticRelation,
        subjects: list[str],
        before_ids: list[str],
        after_ids: list[str],
        subject_name: str,
        severity: Severity,
        confidence: float,
        rationale: str,
        manual_review: bool,
    ) -> Deviation:
        return Deviation(
            id=self._draft_id(namespace, change_type.value + ":" + match.id),
            match_id=match.id,
            change_type=change_type,
            semantic_relation=relation,
            subject_refs=subjects,
            before_clause_ids=before_ids,
            after_clause_ids=after_ids,
            description=self._description(change_type, subject_name),
            severity=severity,
            confidence=confidence,
            rationale=rationale,
            manual_review_required=manual_review,
        )

    @staticmethod
    def _description(change_type: ChangeType, subject: str) -> str:
        labels = {
            ChangeType.UNIT_PRESERVED: "Подразделение сохранено",
            ChangeType.UNIT_CREATED: "Подразделение создано",
            ChangeType.UNIT_REMOVED: "Подразделение не найдено",
            ChangeType.UNIT_TRANSFORMED: "Структура подразделения изменена",
            ChangeType.FUNCTION_PRESERVED: "Функция сохранена",
            ChangeType.FUNCTION_ADDED: "Функция формально добавлена",
            ChangeType.FUNCTION_MISSING: "Часть прежней обязанности не найдена",
            ChangeType.FUNCTION_MOVED: "Функция перенесена",
            ChangeType.WORDING_CHANGED: "Формулировка изменена без изменения смысла",
            ChangeType.POSSIBLE_DUPLICATE: "Возможное дублирование",
            ChangeType.POSSIBLE_CONFLICT: "Потенциальный конфликт",
            ChangeType.INSUFFICIENT_EVIDENCE: "Недостаточно доказательств",
        }
        return f"{labels[change_type]}: {subject}".strip()

    @staticmethod
    def _entity_index(catalog: EntityCatalog) -> dict[str, Entity]:
        return {
            item.id: item
            for item in [*catalog.units, *catalog.roles, *catalog.functions]
        }

    @staticmethod
    def _entity_name(entity: Entity) -> str:
        return entity.canonical_text if isinstance(entity, Function) else entity.name

    @staticmethod
    def _entity_payload(entity: Entity, parsed: ParsedDocument) -> dict[str, object]:
        clause_index = {clause.id: clause for clause in parsed.clauses}
        return {
            "id": entity.id,
            "entity_type": (
                "function"
                if isinstance(entity, Function)
                else "unit"
                if isinstance(entity, OrgUnit)
                else "role"
            ),
            "name": RiskAnalyzer._entity_name(entity),
            "actor": getattr(entity, "actor_name", None),
            "actor_id": getattr(entity, "actor_id", None),
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
    def _numbers(entity: Entity, parsed: ParsedDocument) -> set[str | None]:
        allowed = set(entity.source_clause_ids)
        return {clause.number for clause in parsed.clauses if clause.id in allowed}

    @staticmethod
    def _hierarchical(
        first: Function, second: Function, catalog: EntityCatalog
    ) -> bool:
        if "block" in {first.actor_type.value, second.actor_type.value}:
            return True
        units = {unit.id: unit for unit in catalog.units}
        roles = {role.id: role for role in catalog.roles}
        first_role = roles.get(first.actor_id)
        second_role = roles.get(second.actor_id)
        first_unit = first_role.unit_id if first_role else first.actor_id
        second_unit = second_role.unit_id if second_role else second.actor_id
        if first_unit == second_unit:
            return True
        return (
            units.get(first_unit) is not None
            and units[first_unit].parent_unit_id == second_unit
        ) or (
            units.get(second_unit) is not None
            and units[second_unit].parent_unit_id == first_unit
        )

    @staticmethod
    def _candidate_subject_name(candidate: dict[str, object]) -> str:
        side = candidate.get("after") or candidate.get("before") or {}
        return str(side.get("name", "изменение"))

    @staticmethod
    def _draft_id(namespace: UUID, key: str) -> str:
        return f"draft-{uuid5(namespace, key)}"

    @staticmethod
    def _batches(items: list, size: int) -> Iterable[list]:
        for start in range(0, len(items), size):
            yield items[start : start + size]

    @staticmethod
    def _referenced_clauses(
        batch: list[Deviation],
        before: ParsedDocument,
        after: ParsedDocument,
    ) -> list[dict[str, object]]:
        ids = {
            clause_id
            for item in batch
            for clause_id in [*item.before_clause_ids, *item.after_clause_ids]
        }
        return [
            {
                "clause_id": clause.id,
                "document_role": parsed.document.role.value,
                "number": clause.number,
                "text": clause.raw_text,
            }
            for parsed in (before, after)
            for clause in parsed.clauses
            if clause.id in ids
        ]

    @staticmethod
    def _deduplicate(deviations: list[Deviation]) -> list[Deviation]:
        unique: dict[tuple, Deviation] = {}
        for item in deviations:
            key = (
                item.change_type,
                tuple(sorted(item.before_clause_ids)),
                tuple(sorted(item.after_clause_ids)),
                tuple(sorted(item.subject_refs)),
            )
            current = unique.get(key)
            if current is None or item.confidence > current.confidence:
                unique[key] = item
        return list(unique.values())


risk_analyzer = RiskAnalyzer()
