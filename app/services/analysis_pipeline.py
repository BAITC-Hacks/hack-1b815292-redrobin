"""Orchestration for the six-stage source-grounded AI analysis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from app.ai.client import AIClient, ai_client
from app.ai.prompts import conclusion_prompt
from app.ai.schemas import ConclusionResponse, EvidenceDecision
from app.config import get_settings
from app.errors import AppError
from app.models.ai import Deviation, EntityCatalog, Match
from app.models.domain import Job, Report
from app.services.entity_extractor import EntityExtractor
from app.services.matcher import Matcher
from app.services.report_builder import ReportBuilder, report_builder
from app.services.risk_analyzer import RiskAnalyzer

ProgressCallback = Callable[[str, float], None]


@dataclass
class AnalysisRun:
    report: Report
    before_entities: EntityCatalog | None = None
    after_entities: EntityCatalog | None = None
    matches: list[Match] | None = None
    deviations: list[Deviation] | None = None
    evidence_decisions: dict[str, EvidenceDecision] | None = None


class AnalysisPipeline:
    """Run AI-1 through AI-6 while preserving each validated stage artifact."""

    def __init__(
        self,
        client: AIClient | None = None,
        builder: ReportBuilder | None = None,
    ) -> None:
        self.client = client or ai_client
        self.extractor = EntityExtractor(self.client)
        self.matcher = Matcher(self.client)
        self.risks = RiskAnalyzer(self.client)
        self.builder = builder or report_builder

    def run(
        self,
        job: Job,
        progress: ProgressCallback | None = None,
    ) -> AnalysisRun:
        started = monotonic()
        timeout = get_settings().analysis_timeout_seconds

        def ensure_time() -> None:
            if monotonic() - started >= timeout:
                raise AppError(
                    503,
                    "ai_unavailable",
                    (
                        "Сервис анализа временно недоступен. "
                        "Извлеченные пункты сохранены."
                    ),
                    retryable=True,
                )

        before = job.parsed_documents.get(job.documents[0].role)
        after = job.parsed_documents.get(job.documents[1].role)
        if before is None or after is None:
            raise ValueError("documents must be parsed before AI analysis")
        if before.document.sha256 == after.document.sha256:
            return AnalysisRun(report=self.builder.build_identical_report(job))

        self._progress(progress, "extract_entities_before", 0.50)
        before_entities = self.extractor.extract(before)
        ensure_time()
        self._progress(progress, "extract_entities_after", 0.61)
        after_entities = self.extractor.extract(after)
        ensure_time()

        self._progress(progress, "semantic_matching", 0.72)
        matches = self.matcher.match(before_entities, after_entities, before, after)
        ensure_time()

        self._progress(progress, "classify_changes", 0.80)
        deviations = self.risks.classify_changes(
            before_entities,
            after_entities,
            matches,
            before,
            after,
        )
        deviations.extend(self.risks.analyze_risks(after_entities, after))
        ensure_time()

        self._progress(progress, "check_evidence", 0.90)
        decisions = (
            self.risks.check_evidence(deviations, before, after) if deviations else {}
        )
        report = self.builder.build_report(job, deviations, decisions)

        if report.findings:
            try:
                conclusion = self.client.parse(
                    conclusion_prompt(
                        {
                            "published_findings": [
                                {
                                    "finding_id": item.id,
                                    "change_type": item.change_type.value,
                                    "description": item.description,
                                    "rationale": item.rationale,
                                    "manual_review_status": (
                                        item.manual_review_status.value
                                    ),
                                }
                                for item in report.findings
                            ]
                        }
                    ),
                    ConclusionResponse,
                )
                if not self.builder.apply_ai_conclusion(report, conclusion):
                    report.warnings.append(
                        "AI-заключение заменено шаблонным из-за неизвестного finding_id."
                    )
            except AppError:
                # AI-6 has a documented deterministic fallback. Earlier stage
                # failures still propagate and fail the job.
                report.warnings.append(
                    "Краткое заключение сформировано по шаблону; AI-6 недоступен."
                )

        return AnalysisRun(
            report=report,
            before_entities=before_entities,
            after_entities=after_entities,
            matches=matches,
            deviations=deviations,
            evidence_decisions=decisions,
        )

    @staticmethod
    def _progress(callback: ProgressCallback | None, stage: str, value: float) -> None:
        if callback is not None:
            callback(stage, value)


analysis_pipeline = AnalysisPipeline()
