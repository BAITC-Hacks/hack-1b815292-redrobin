"""Section-scoped organization, role, and function extraction."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid5

from app.ai.client import AIClient, ai_client
from app.ai.prompts import extraction_prompt
from app.ai.schemas import EntityExtractionResponse
from app.models.ai import EntityCatalog, Function, OrgUnit, Role
from app.models.domain import Clause, ParsedDocument, Section

MAX_CLAUSES_PER_REQUEST = 25
NAME_PATTERN = re.compile(
    r"(?:Департамент|Блок|Центр|Директор|Руководитель|Главный аудитор)"
    r"[^.;:]{0,100}",
    re.IGNORECASE,
)


class EntityExtractor:
    """Call AI once per bounded section chunk and reject foreign source IDs."""

    def __init__(
        self,
        client: AIClient | None = None,
        *,
        max_clauses_per_request: int = MAX_CLAUSES_PER_REQUEST,
    ) -> None:
        self.client = client or ai_client
        self.max_clauses_per_request = max_clauses_per_request
        client_settings = getattr(self.client, "settings", None)
        self.parallelism = getattr(client_settings, "ai_parallelism", 1)

    def extract(self, parsed: ParsedDocument) -> EntityCatalog:
        clauses_by_id = {clause.id: clause for clause in parsed.clauses}
        section_by_id = {section.id: section for section in parsed.sections}
        grouped: dict[str | None, list[Clause]] = defaultdict(list)
        for clause in parsed.clauses:
            grouped[clause.section_id].append(clause)

        requests: list[tuple[dict[str, object], set[str]]] = []
        for section_id, section_clauses in grouped.items():
            section = section_by_id.get(section_id)
            for chunk in self._chunks(section_clauses):
                requests.append(
                    (
                        self._payload(parsed, section, chunk),
                        {item.id for item in chunk},
                    )
                )

        def execute(
            request: tuple[dict[str, object], set[str]],
        ) -> EntityExtractionResponse:
            payload, allowed_ids = request
            response = self.client.parse(
                extraction_prompt(payload), EntityExtractionResponse
            )
            self._validate_clause_ids(response, allowed_ids)
            return response

        with ThreadPoolExecutor(max_workers=self.parallelism) as executor:
            responses = list(executor.map(execute, requests))

        return self._catalog(parsed, responses, clauses_by_id)

    def _chunks(self, clauses: list[Clause]) -> Iterable[list[Clause]]:
        for start in range(0, len(clauses), self.max_clauses_per_request):
            yield clauses[start : start + self.max_clauses_per_request]

    @staticmethod
    def _payload(
        parsed: ParsedDocument,
        section: Section | None,
        clauses: list[Clause],
    ) -> dict[str, object]:
        known_names: list[str] = []
        for clause in clauses:
            known_names.extend(
                match.group(0).strip()
                for match in NAME_PATTERN.finditer(clause.raw_text)
            )
        return {
            "document_role": parsed.document.role.value,
            "section": {
                "id": section.id if section else None,
                "title": section.title if section else "Без раздела",
                "number": section.number if section else None,
            },
            "deterministic_name_candidates": list(dict.fromkeys(known_names))[:50],
            "clauses": [
                {
                    "clause_id": clause.id,
                    "number": clause.number,
                    "text": clause.raw_text,
                }
                for clause in clauses
            ],
        }

    @staticmethod
    def _validate_clause_ids(
        response: EntityExtractionResponse, allowed_ids: set[str]
    ) -> int:
        """Reject individual entities that cite anything outside the request."""

        rejected = 0

        def keep(item: object) -> bool:
            nonlocal rejected
            source_ids = set(item.source_clause_ids)
            valid = bool(source_ids) and source_ids.issubset(allowed_ids)
            if not valid:
                rejected += 1
            return valid

        response.units = [item for item in response.units if keep(item)]
        response.roles = [item for item in response.roles if keep(item)]
        response.functions = [item for item in response.functions if keep(item)]
        return rejected

    @staticmethod
    def _catalog(
        parsed: ParsedDocument,
        responses: list[EntityExtractionResponse],
        clauses_by_id: dict[str, Clause],
    ) -> EntityCatalog:
        del clauses_by_id  # IDs were checked per request; kept explicit by contract.
        units_by_name: dict[str, object] = {}
        roles_by_name: dict[str, object] = {}
        functions_by_key: dict[tuple[str, str, str], object] = {}

        for response in responses:
            for unit in response.units:
                EntityExtractor._merge(units_by_name, unit.name.casefold(), unit)
            for role in response.roles:
                EntityExtractor._merge(roles_by_name, role.name.casefold(), role)
            for function in response.functions:
                key = (
                    function.actor_name.casefold(),
                    function.kind.value,
                    function.canonical_text.casefold(),
                )
                EntityExtractor._merge(functions_by_key, key, function)

        document_id = parsed.document.id
        unit_ids = {
            name: f"unit-{uuid5(document_id, 'unit:' + name)}" for name in units_by_name
        }
        role_ids = {
            name: f"role-{uuid5(document_id, 'role:' + name)}" for name in roles_by_name
        }

        units = [
            OrgUnit(
                id=unit_ids[name],
                document_id=document_id,
                name=item.name,
                short_name=item.short_name,
                unit_type=item.unit_type,
                parent_unit_id=unit_ids.get((item.parent_name or "").casefold()),
                source_clause_ids=item.source_clause_ids,
                extraction_confidence=item.confidence,
            )
            for name, item in units_by_name.items()
        ]
        roles = [
            Role(
                id=role_ids[name],
                document_id=document_id,
                name=item.name,
                unit_id=unit_ids.get((item.unit_name or "").casefold()),
                reports_to_role_id=role_ids.get((item.reports_to or "").casefold()),
                source_clause_ids=item.source_clause_ids,
                extraction_confidence=item.confidence,
            )
            for name, item in roles_by_name.items()
        ]
        functions = []
        for key, item in functions_by_key.items():
            actor_name = item.actor_name.casefold()
            actor_id = role_ids.get(actor_name) or unit_ids.get(actor_name)
            if actor_id is None:
                actor_id = f"actor-{uuid5(document_id, 'actor:' + actor_name)}"
            functions.append(
                Function(
                    id=f"function-{uuid5(document_id, ':'.join(key))}",
                    document_id=document_id,
                    actor_type=item.actor_type,
                    actor_id=actor_id,
                    actor_name=item.actor_name,
                    kind=item.kind,
                    canonical_text=item.canonical_text,
                    source_clause_ids=item.source_clause_ids,
                    extraction_confidence=item.confidence,
                )
            )
        return EntityCatalog(
            document_id=document_id,
            units=units,
            roles=roles,
            functions=functions,
        )

    @staticmethod
    def _merge(target: dict, key: object, item: object) -> None:
        existing = target.get(key)
        if existing is None:
            target[key] = item
            return
        existing.source_clause_ids = list(
            dict.fromkeys([*existing.source_clause_ids, *item.source_clause_ids])
        )
        existing.confidence = max(existing.confidence, item.confidence)


entity_extractor = EntityExtractor()
