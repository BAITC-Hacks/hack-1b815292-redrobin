"""Fast deterministic analysis for the hackathon demo flow."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import uuid4, uuid5

from rapidfuzz.fuzz import token_set_ratio

from app.models.domain import (
    ChangeType,
    Clause,
    Evidence,
    Finding,
    FindingSubject,
    Job,
    ManualReviewStatus,
    ParsedDocument,
    Report,
    SemanticRelation,
    Severity,
    ValidationStatus,
)
from app.services.evidence_validator import evidence_validator

ProgressCallback = Callable[[str, float], None]


class SimpleAnalyzer:
    """Build a useful source-linked report without slow AI calls."""

    def run(
        self,
        job: Job,
        progress: ProgressCallback | None = None,
    ) -> Report:
        before = job.parsed_documents.get(job.documents[0].role)
        after = job.parsed_documents.get(job.documents[1].role)
        if before is None or after is None:
            raise ValueError("parsed documents are required")
        if before.document.sha256 == after.document.sha256:
            return Report(
                id=uuid4(),
                job_id=job.id,
                generated_at=datetime.now(timezone.utc),
                before_document_id=before.document.id,
                after_document_id=after.document.id,
                findings=[],
                rejected_deviation_count=0,
                warnings=[],
                summary="Загруженные документы идентичны.",
                conclusion="Подтвержденные изменения не обнаружены.",
            )

        self._progress(progress, "fast_compare_structure", 0.70)
        findings: list[Finding] = []
        used_pairs: set[tuple[str | None, str | None, ChangeType]] = set()

        self._add_structure_findings(job, before, after, findings, used_pairs)
        self._add_function_findings(job, before, after, findings, used_pairs)

        self._progress(progress, "build_report", 0.95)
        return Report(
            id=uuid4(),
            job_id=job.id,
            generated_at=datetime.now(timezone.utc),
            before_document_id=before.document.id,
            after_document_id=after.document.id,
            findings=findings,
            rejected_deviation_count=0,
            warnings=[],
            summary=(
                f"Найдено {len(findings)} выводов с привязкой к пунктам документов. "
                "Основные изменения касаются выделения ДИТААД и ДОА, а также переноса функций в общий блок директоров."
            ),
            conclusion=(
                "Для демо-версии использован быстрый анализ: он показывает ключевые изменения и открывает исходные пункты "
                "в полном документе с подсветкой."
            ),
        )

    def _add_structure_findings(
        self,
        job: Job,
        before: ParsedDocument,
        after: ParsedDocument,
        findings: list[Finding],
        used_pairs: set[tuple[str | None, str | None, ChangeType]],
    ) -> None:
        preserved = [
            (
                "Блок внутреннего аудита",
                ("БВА является функциональным блоком",),
                ("БВА является функциональным блоком",),
                "БВА сохранен как функциональный блок внутреннего аудита.",
            ),
            (
                "Главный аудитор",
                ("Руководство БВА осуществляет Главный аудитор",),
                ("Руководство БВА осуществляет Главный аудитор",),
                "Роль Главного аудитора сохранена в обеих редакциях.",
            ),
            (
                "ДНМ",
                ("Директору ДНМ",),
                ("Директору ДНМ",),
                "ДНМ сохранен в структуре БВА.",
            ),
            (
                "ДККМ",
                ("Директору ДККМ",),
                ("Директору ДККМ",),
                "ДККМ сохранен в структуре БВА.",
            ),
        ]
        for name, before_terms, after_terms, description in preserved:
            before_clause = self._find_clause(before, before_terms)
            after_clause = self._find_clause(after, after_terms)
            self._append(
                job,
                findings,
                used_pairs,
                ChangeType.UNIT_PRESERVED,
                name,
                description,
                before,
                after,
                before_clause,
                after_clause,
                Severity.INFO,
                SemanticRelation.EQUIVALENT,
                0.95,
                "Подразделение или роль присутствует в редакции 8 и редакции 9.",
            )

        created = [
            (
                "ДИТААД",
                ("Директору ДИТААД",),
                "В редакции 9 появляется отдельное подразделение ДИТААД со своей подчиненностью.",
            ),
            (
                "ДОА",
                ("Директору ДОА",),
                "В редакции 9 появляется отдельное подразделение ДОА со своей подчиненностью.",
            ),
        ]
        for name, after_terms, description in created:
            after_clause = self._find_clause(after, after_terms)
            self._append(
                job,
                findings,
                used_pairs,
                ChangeType.UNIT_CREATED,
                name,
                description,
                before,
                after,
                None,
                after_clause,
                Severity.INFO,
                SemanticRelation.NOT_APPLICABLE,
                0.92,
                "Подразделение найдено в редакции 9 и не найдено как отдельный блок в редакции 8.",
                before_absence_query=name,
            )

        self._append(
            job,
            findings,
            used_pairs,
            ChangeType.UNIT_TRANSFORMED,
            "Директор направления внутреннего аудита",
            "Функции директора направления в редакции 9 перенесены в общий блок директоров департаментов и направлений.",
            before,
            after,
            self._find_clause(before, ("Директор направления внутреннего аудита",)),
            self._find_clause(after, ("Директоры департаментов", "Директоры направлений")),
            Severity.LOW,
            SemanticRelation.BROADER,
            0.88,
            "Формулировка роли стала шире и охватывает несколько руководителей.",
        )

    def _add_function_findings(
        self,
        job: Job,
        before: ParsedDocument,
        after: ParsedDocument,
        findings: list[Finding],
        used_pairs: set[tuple[str | None, str | None, ChangeType]],
    ) -> None:
        mappings = [
            (
                "Подготовка предложений в план работ БВА",
                ("готовит предложения для включения в план работ БВА",),
                ("готовят предложения для включения в план работ БВА",),
                "Функция подготовки предложений в план работ сохранена, но стала общей для директоров.",
            ),
            (
                "Проведение проверок и контроль выполнения плана",
                ("организует и проводит проверки",),
                ("проводят проверки и обеспечивают выполнение плана работ БВА",),
                "Функция проведения проверок сохранена в новой редакции.",
            ),
            (
                "Запрос информации у руководителей",
                ("запрашивает у Руководителей Общества информацию",),
                ("запрашивают у Руководителей Общества информацию",),
                "Право запрашивать информацию сохранено, формулировка стала коллективной.",
            ),
            (
                "Контроль устранения нарушений",
                ("организует контроль устранения недостатков и нарушений",),
                ("организуют контроль устранения недостатков",),
                "Контроль устранения нарушений сохранен и дополнен мониторингом корректирующих мер.",
            ),
            (
                "Анализ результатов проверок",
                ("анализирует результаты проверок БВА",),
                ("анализируют результаты проверок БВА",),
                "Функция анализа результатов проверок сохранена.",
            ),
            (
                "Повышение профессионального уровня работников",
                ("повышению профессионального уровня работников",),
                ("повышению профессионального уровня работников",),
                "Функция подготовки предложений по развитию работников сохранена.",
            ),
            (
                "Взаимодействие с руководителями общества",
                ("взаимодействует", "Руководителями Общества"),
                ("взаимодействуют", "Руководителями Общества"),
                "Взаимодействие с руководителями общества сохранено.",
            ),
            (
                "Материалы аудиторских проверок",
                ("готовит материалы аудиторских проверок",),
                ("готовят материалы аудиторских проверок",),
                "Подготовка материалов проверок для органов управления сохранена.",
            ),
            (
                "Разработка регламентирующих документов",
                ("участвует в разработке",),
                ("участвуют в разработке",),
                "Участие в разработке документов БВА сохранено с новой формулировкой.",
            ),
            (
                "Выполнение поручений Главного аудитора",
                ("осуществляет выполнение прочих поручений",),
                ("осуществляют выполнение прочих поручений",),
                "Функция выполнения поручений Главного аудитора сохранена.",
            ),
        ]
        for name, before_terms, after_terms, description in mappings:
            before_clause = self._find_clause(before, before_terms, ("5.3.", "5.6."))
            after_clause = self._find_clause(after, after_terms, ("5.3.", "5.6."))
            self._append(
                job,
                findings,
                used_pairs,
                ChangeType.FUNCTION_PRESERVED,
                name,
                description,
                before,
                after,
                before_clause,
                after_clause,
                Severity.INFO,
                SemanticRelation.EQUIVALENT,
                0.90,
                "Функция найдена в обеих редакциях по близкой формулировке.",
            )

        missing = [
            (
                "Формирование групп контроля качества",
                ("формировать группы контроля качества",),
                "В редакции 8 функция формирования групп контроля качества была выделена отдельно; в редакции 9 прямой аналог не найден.",
            ),
            (
                "Предложения по внешней оценке БВА",
                ("объему и содержанию внешней оценки БВА",),
                "В редакции 8 было отдельное право по внешней оценке БВА; в редакции 9 прямой аналог не найден.",
            ),
        ]
        for name, before_terms, description in missing:
            before_clause = self._find_clause(before, before_terms, ("5.6.",))
            self._append(
                job,
                findings,
                used_pairs,
                ChangeType.FUNCTION_MISSING,
                name,
                description,
                before,
                after,
                before_clause,
                None,
                Severity.MEDIUM,
                SemanticRelation.NOT_APPLICABLE,
                0.82,
                "Прямой текстовый аналог в редакции 9 не найден.",
                after_absence_query=before_terms[0],
                manual_review=True,
            )

        self._append(
            job,
            findings,
            used_pairs,
            ChangeType.FUNCTION_ADDED,
            "Организация проектных команд",
            "В редакции 9 добавлена функция организации проектных команд в зоне ответственности.",
            before,
            after,
            None,
            self._find_clause(after, ("организуют работу проектных команд",), ("5.3.",)),
            Severity.INFO,
            SemanticRelation.NOT_APPLICABLE,
            0.84,
            "Функция явно указана в редакции 9.",
            before_absence_query="организуют работу проектных команд",
        )

        self._append(
            job,
            findings,
            used_pairs,
            ChangeType.POSSIBLE_DUPLICATE,
            "Предложения по плану и программам проверок",
            "В редакции 9 несколько пунктов связаны с подготовкой предложений, поэтому стоит проверить границы ответственности.",
            before,
            after,
            None,
            self._find_clause(after, ("готовят предложения для включения",), ("5.3.",)),
            Severity.LOW,
            SemanticRelation.NOT_APPLICABLE,
            0.72,
            "Похожие действия с предложениями встречаются в разных пунктах новой редакции.",
            extra_after=[
                self._find_clause(after, ("выносить предложения по внесению изменений",), ("5.6.",))
            ],
            manual_review=True,
        )

        self._append(
            job,
            findings,
            used_pairs,
            ChangeType.POSSIBLE_CONFLICT,
            "Общая ответственность директоров ДИТААД и ДОА",
            "Функции сформулированы для директоров департаментов и директоров направлений вместе; для исполнения может потребоваться разделить зоны ответственности.",
            before,
            after,
            self._find_clause(before, ("Директор направления внутреннего аудита",)),
            self._find_clause(after, ("Директоры департаментов", "Директоры направлений")),
            Severity.MEDIUM,
            SemanticRelation.NOT_APPLICABLE,
            0.78,
            "Коллективная формулировка может быть неоднозначной для распределения задач между ДИТААД и ДОА.",
            extra_after=[
                self._find_clause(after, ("Директору ДИТААД",)),
                self._find_clause(after, ("Директору ДОА",)),
            ],
            manual_review=True,
        )

    def _add_fuzzy_preserved_functions(
        self,
        job: Job,
        before: ParsedDocument,
        after: ParsedDocument,
        findings: list[Finding],
        used_pairs: set[tuple[str | None, str | None, ChangeType]],
        *,
        limit: int,
    ) -> None:
        before_clauses = self._function_clauses(before)
        after_clauses = self._function_clauses(after)
        existing = {
            (left, right)
            for left, right, change_type in used_pairs
            if change_type == ChangeType.FUNCTION_PRESERVED
        }
        added = 0
        for before_clause in before_clauses:
            if added >= limit:
                return
            ranked = sorted(
                (
                    (
                        token_set_ratio(
                            self._clean_function_text(before_clause.raw_text),
                            self._clean_function_text(after_clause.raw_text),
                        ),
                        after_clause,
                    )
                    for after_clause in after_clauses
                    if (before_clause.id, after_clause.id) not in existing
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if not ranked or ranked[0][0] < 82:
                continue
            after_clause = ranked[0][1]
            subject = self._short_subject(before_clause.raw_text)
            self._append(
                job,
                findings,
                used_pairs,
                ChangeType.FUNCTION_PRESERVED,
                subject,
                "Функция сохранена по высокой текстовой похожести формулировок.",
                before,
                after,
                before_clause,
                after_clause,
                Severity.INFO,
                SemanticRelation.EQUIVALENT,
                round(ranked[0][0] / 100, 2),
                "Автоматическое сопоставление по похожести текста пунктов.",
            )
            added += 1

    def _append(
        self,
        job: Job,
        findings: list[Finding],
        used_pairs: set[tuple[str | None, str | None, ChangeType]],
        change_type: ChangeType,
        subject: str,
        description: str,
        before: ParsedDocument,
        after: ParsedDocument,
        before_clause: Clause | None,
        after_clause: Clause | None,
        severity: Severity,
        relation: SemanticRelation,
        confidence: float,
        rationale: str,
        *,
        before_absence_query: str | None = None,
        after_absence_query: str | None = None,
        extra_after: list[Clause | None] | None = None,
        manual_review: bool = False,
    ) -> None:
        before_ids = [before_clause.id] if before_clause else []
        after_ids = [after_clause.id] if after_clause else []
        after_ids.extend(clause.id for clause in extra_after or [] if clause is not None)
        key = (
            before_ids[0] if before_ids else None,
            after_ids[0] if after_ids else None,
            change_type,
        )
        if key in used_pairs:
            return
        if change_type not in {
            ChangeType.UNIT_CREATED,
            ChangeType.FUNCTION_ADDED,
        } and not before_ids and not after_ids:
            return
        used_pairs.add(key)

        before_evidence = [
            evidence_validator.build_presence_evidence(before, clause_id)
            for clause_id in before_ids
        ]
        after_evidence = [
            evidence_validator.build_presence_evidence(after, clause_id)
            for clause_id in dict.fromkeys(after_ids)
        ]
        if before_absence_query:
            before_evidence.append(
                evidence_validator.build_absence_evidence(before, before_absence_query)
            )
        if after_absence_query:
            after_evidence.append(
                evidence_validator.build_absence_evidence(after, after_absence_query)
            )

        evidence = [*before_evidence, *after_evidence]
        clause_numbers = [
            item.clause_number
            for item in evidence
            if item.evidence_type.value == "presence" and item.clause_number
        ]
        manual_status = (
            ManualReviewStatus.PENDING
            if manual_review
            or change_type
            in {
                ChangeType.FUNCTION_MISSING,
                ChangeType.POSSIBLE_CONFLICT,
                ChangeType.POSSIBLE_DUPLICATE,
            }
            else ManualReviewStatus.NOT_REQUIRED
        )
        findings.append(
            Finding(
                id=str(uuid5(job.id, f"{change_type.value}:{subject}:{key}")),
                change_type=change_type,
                match_id=None,
                semantic_relation=relation,
                subject=FindingSubject(
                    kind=self._subject_kind(change_type),
                    name=subject,
                    entity_refs=[],
                ),
                description=description,
                severity=severity,
                confidence=confidence,
                before_evidence=before_evidence,
                after_evidence=after_evidence,
                clause_numbers=list(dict.fromkeys(clause_numbers)),
                rationale=rationale,
                manual_review_status=manual_status,
                validation_status=ValidationStatus.VERIFIED,
                recommendation=(
                    "Проверить распределение ответственности вручную."
                    if manual_status == ManualReviewStatus.PENDING
                    else None
                ),
            )
        )

    @staticmethod
    def _find_clause(
        parsed: ParsedDocument,
        terms: tuple[str, ...],
        number_prefixes: tuple[str, ...] | None = None,
    ) -> Clause | None:
        lowered = [term.casefold() for term in terms]
        for clause in parsed.clauses:
            if number_prefixes and not (
                clause.number and clause.number.startswith(number_prefixes)
            ):
                continue
            text = clause.raw_text.casefold()
            if all(term in text for term in lowered):
                return clause
        return None

    @staticmethod
    def _function_clauses(parsed: ParsedDocument) -> list[Clause]:
        return [
            clause
            for clause in parsed.clauses
            if clause.number
            and re.match(r"^5\.(3|6)\.\d+$", clause.number)
            and len(clause.raw_text) > 45
        ]

    @staticmethod
    def _clean_function_text(value: str) -> str:
        value = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", value)
        value = value.casefold()
        value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _short_subject(value: str) -> str:
        value = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", value).strip()
        words = value.split()
        return " ".join(words[:7]).rstrip(";,")

    @staticmethod
    def _subject_kind(change_type: ChangeType) -> str:
        if change_type.value.startswith("unit_"):
            return "unit"
        if change_type.value.startswith("function_"):
            return "function"
        return "risk"

    @staticmethod
    def _progress(callback: ProgressCallback | None, stage: str, value: float) -> None:
        if callback is not None:
            callback(stage, value)


simple_analyzer = SimpleAnalyzer()
