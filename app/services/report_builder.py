"""Deterministic evidence resolution and public report assembly."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from uuid import uuid4, uuid5

from app.ai.schemas import ConclusionResponse, EvidenceDecision, EvidenceVerdict
from app.models.ai import Deviation
from app.models.domain import (
    ChangeType,
    Finding,
    FindingSubject,
    Job,
    ManualReviewStatus,
    Report,
    SemanticRelation,
    Severity,
    ValidationStatus,
)
from app.services.evidence_validator import evidence_validator


class ReportBuilder:
    """Publish only drafts whose source IDs resolve to trusted clauses."""

    def build_report(
        self,
        job: Job,
        deviations: list[Deviation],
        decisions: Mapping[str, EvidenceDecision],
    ) -> Report:
        before = job.parsed_documents.get(job.documents[0].role)
        after = job.parsed_documents.get(job.documents[1].role)
        if before is None or after is None:
            raise ValueError("parsed documents are required")

        findings: list[Finding] = []
        rejected = 0
        warnings: list[str] = []
        for deviation in deviations:
            decision = decisions.get(deviation.id)
            if decision is None or decision.verdict == EvidenceVerdict.UNSUPPORTED:
                rejected += 1
                continue

            change_type = deviation.change_type
            semantic_relation = deviation.semantic_relation
            rationale = deviation.rationale
            if decision.verdict == EvidenceVerdict.INSUFFICIENT:
                change_type = ChangeType.INSUFFICIENT_EVIDENCE
                semantic_relation = SemanticRelation.NOT_APPLICABLE
                rationale = decision.reason

            before_evidence = [
                evidence_validator.build_presence_evidence(before, clause_id)
                for clause_id in deviation.before_clause_ids
            ]
            after_evidence = [
                evidence_validator.build_presence_evidence(after, clause_id)
                for clause_id in deviation.after_clause_ids
            ]

            if change_type in {ChangeType.UNIT_CREATED, ChangeType.FUNCTION_ADDED}:
                query = self._subject_name(deviation)
                absence = evidence_validator.build_absence_evidence(before, query)
                if absence.validated:
                    before_evidence.append(absence)

            evidence = [*before_evidence, *after_evidence]
            if not evidence or any(not item.validated for item in evidence):
                rejected += 1
                continue
            if not self._sources_sufficient(
                change_type, deviation, before_evidence, after_evidence
            ):
                change_type = ChangeType.INSUFFICIENT_EVIDENCE
                semantic_relation = SemanticRelation.NOT_APPLICABLE
                rationale = (
                    "Источники существуют, но их недостаточно для категоричного вывода."
                )

            manual_review = (
                ManualReviewStatus.PENDING
                if deviation.manual_review_required
                or change_type
                in {
                    ChangeType.POSSIBLE_CONFLICT,
                    ChangeType.POSSIBLE_DUPLICATE,
                    ChangeType.FUNCTION_MISSING,
                    ChangeType.INSUFFICIENT_EVIDENCE,
                }
                else ManualReviewStatus.NOT_REQUIRED
            )
            finding = Finding(
                id=str(uuid5(job.id, deviation.id)),
                change_type=change_type,
                match_id=deviation.match_id,
                semantic_relation=semantic_relation,
                subject=FindingSubject(
                    kind=self._subject_kind(change_type),
                    name=self._subject_name(deviation),
                    entity_refs=deviation.subject_refs,
                ),
                description=self._description(change_type, deviation.description),
                severity=self._safe_severity(change_type, deviation.severity),
                confidence=deviation.confidence,
                before_evidence=before_evidence,
                after_evidence=after_evidence,
                clause_numbers=list(
                    dict.fromkeys(
                        item.clause_number
                        for item in evidence
                        if item.clause_number is not None
                    )
                ),
                rationale=rationale,
                manual_review_status=manual_review,
                validation_status=ValidationStatus.VERIFIED,
                recommendation=(
                    "Требуется ручная проверка источников и распределения ответственности."
                    if manual_review == ManualReviewStatus.PENDING
                    else None
                ),
            )
            findings.append(finding)

        if rejected:
            warnings.append(
                "Один или несколько кандидатов не включены в отчет, потому что "
                "источник не подтвержден."
            )
        summary, conclusion = self._template_conclusion(findings)
        return Report(
            id=uuid4(),
            job_id=job.id,
            generated_at=datetime.now(timezone.utc),
            before_document_id=before.document.id,
            after_document_id=after.document.id,
            findings=findings,
            rejected_deviation_count=rejected,
            warnings=warnings,
            summary=summary,
            conclusion=conclusion,
        )

    def build_identical_report(self, job: Job) -> Report:
        before, after = job.documents
        return Report(
            id=uuid4(),
            job_id=job.id,
            generated_at=datetime.now(timezone.utc),
            before_document_id=before.id,
            after_document_id=after.id,
            findings=[],
            rejected_deviation_count=0,
            warnings=[],
            summary="Загруженные документы идентичны.",
            conclusion="Подтвержденные изменения не обнаружены.",
        )

    def apply_ai_conclusion(
        self, report: Report, conclusion: ConclusionResponse
    ) -> bool:
        allowed = {finding.id for finding in report.findings}
        referenced = set(conclusion.key_finding_ids)
        for recommendation in conclusion.recommendations:
            referenced.update(recommendation.based_on_finding_ids)
        if not referenced.issubset(allowed):
            return False
        report.summary = conclusion.summary.strip() or report.summary
        recommendations = [item.text.strip() for item in conclusion.recommendations]
        if recommendations:
            report.conclusion = " ".join(item for item in recommendations if item)
        return True

    @staticmethod
    def _sources_sufficient(
        change_type: ChangeType,
        deviation: Deviation,
        before_evidence: list,
        after_evidence: list,
    ) -> bool:
        both_sides = {
            ChangeType.UNIT_PRESERVED,
            ChangeType.UNIT_TRANSFORMED,
            ChangeType.FUNCTION_PRESERVED,
            ChangeType.FUNCTION_MOVED,
            ChangeType.WORDING_CHANGED,
        }
        if change_type in both_sides:
            return bool(before_evidence and after_evidence)
        if change_type == ChangeType.FUNCTION_MISSING:
            return (
                bool(before_evidence and after_evidence)
                and deviation.manual_review_required
            )
        if change_type == ChangeType.POSSIBLE_DUPLICATE:
            return len(after_evidence) >= 2 and len(deviation.subject_refs) >= 2
        if change_type == ChangeType.POSSIBLE_CONFLICT:
            return bool(after_evidence) and deviation.manual_review_required
        if change_type in {ChangeType.UNIT_CREATED, ChangeType.FUNCTION_ADDED}:
            return bool(before_evidence and after_evidence)
        return bool(before_evidence or after_evidence)

    @staticmethod
    def _template_conclusion(findings: list[Finding]) -> tuple[str, str]:
        if not findings:
            return (
                "Проверенные изменения не опубликованы.",
                "Недостаточно подтвержденных данных для содержательного заключения.",
            )
        counts = Counter(item.change_type.value for item in findings)
        rendered = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
        pending = sum(
            item.manual_review_status == ManualReviewStatus.PENDING for item in findings
        )
        return (
            f"Опубликовано проверенных выводов: {len(findings)}. {rendered}.",
            (
                f"Требуется ручная проверка: {pending}. "
                "Потенциальные риски не являются установленными фактами."
            ),
        )

    @staticmethod
    def _subject_name(deviation: Deviation) -> str:
        if ":" in deviation.description:
            return deviation.description.split(":", 1)[1].strip()
        return deviation.description

    @staticmethod
    def _subject_kind(change_type: ChangeType) -> str:
        if change_type.value.startswith("unit_"):
            return "unit"
        if change_type.value.startswith("function_"):
            return "function"
        return "risk"

    @staticmethod
    def _description(change_type: ChangeType, original: str) -> str:
        if change_type == ChangeType.POSSIBLE_CONFLICT:
            return f"Потенциальный конфликт: {ReportBuilder._tail(original)}"
        if change_type == ChangeType.POSSIBLE_DUPLICATE:
            return f"Возможное дублирование: {ReportBuilder._tail(original)}"
        if change_type == ChangeType.INSUFFICIENT_EVIDENCE:
            return f"Требуется ручная проверка: {ReportBuilder._tail(original)}"
        return original

    @staticmethod
    def _tail(value: str) -> str:
        return value.split(":", 1)[-1].strip()

    @staticmethod
    def _safe_severity(change_type: ChangeType, requested: Severity) -> Severity:
        if change_type in {ChangeType.UNIT_PRESERVED, ChangeType.FUNCTION_PRESERVED}:
            return Severity.INFO
        if change_type == ChangeType.WORDING_CHANGED:
            return (
                requested
                if requested in {Severity.INFO, Severity.LOW}
                else Severity.LOW
            )
        return requested


report_builder = ReportBuilder()
