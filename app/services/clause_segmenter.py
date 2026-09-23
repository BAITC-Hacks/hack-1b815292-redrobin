"""Document section and clause segmentation."""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol
from uuid import UUID

from app.models.domain import Clause, Section

CLAUSE_NUMBER_PATTERN = re.compile(
    r"(?<!\S)(?P<number>\d+(?:\.\d+)+(?:\.[A-Za-zА-Яа-яЁё])?)\."
    r"(?=\s*[^\d\s])"
)
TOP_LEVEL_SECTION_PATTERN = re.compile(r"^(?P<number>\d+)\.\s+\S")
LETTER_LABEL_PATTERN = re.compile(r"^(?P<label>[A-Za-zА-Яа-яЁё])\.\s*")


class BlockLike(Protocol):
    index: int
    text: str
    style_name: str | None
    numbering_metadata: dict | None


class ClauseSegmenter:
    """Turn ordered document blocks into stable sections and clauses."""

    def segment(
        self, document_id: UUID, blocks: list[BlockLike]
    ) -> tuple[list[Section], list[Clause], list[str]]:
        sections: list[Section] = []
        clauses: list[Clause] = []
        warnings: list[str] = []
        current_section: Section | None = None
        numbered_clause_count = 0

        for block in blocks:
            if not block.text or not block.text.strip():
                continue
            text = block.text
            if self._is_section_heading(text, block.style_name):
                current_section = self._make_section(
                    document_id=document_id,
                    block_index=block.index,
                    text=text,
                    style_name=block.style_name,
                )
                sections.append(current_section)
                continue

            segments = self._split_numbered_clauses(text)
            for segment_index, (start, end, number) in enumerate(segments):
                raw_text = text[start:end]
                if not raw_text.strip():
                    continue
                label_match = LETTER_LABEL_PATTERN.match(raw_text.strip())
                clause = Clause(
                    id=f"{document_id}:p{block.index}:{segment_index}",
                    document_id=document_id,
                    section_id=current_section.id if current_section else None,
                    number=number,
                    label=label_match.group("label") if label_match else None,
                    paragraph_index=block.index,
                    source_start=start,
                    source_end=end,
                    raw_text=raw_text,
                    normalized_text=self.normalize_text(raw_text),
                    style_name=block.style_name,
                    numbering_metadata=block.numbering_metadata,
                )
                clauses.append(clause)
                if number:
                    numbered_clause_count += 1
                if current_section is not None:
                    current_section.clause_ids.append(clause.id)

        if clauses and numbered_clause_count == 0:
            warnings.append(
                "Нумерация пунктов не распознана; использованы стабильные "
                "синтетические ID."
            )
        return sections, clauses, warnings

    @staticmethod
    def normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized.casefold()

    def _split_numbered_clauses(self, text: str) -> list[tuple[int, int, str | None]]:
        matches = list(CLAUSE_NUMBER_PATTERN.finditer(text))
        if not matches:
            start, end = self._trim_range(text, 0, len(text))
            return [(start, end, None)] if start < end else []

        segments: list[tuple[int, int, str | None]] = []
        first_start = matches[0].start("number")
        if text[:first_start].strip():
            start, end = self._trim_range(text, 0, first_start)
            if start < end:
                segments.append((start, end, None))

        for index, match in enumerate(matches):
            raw_start = match.start("number")
            raw_end = (
                matches[index + 1].start("number")
                if index + 1 < len(matches)
                else len(text)
            )
            start, end = self._trim_range(text, raw_start, raw_end)
            if start < end:
                segments.append((start, end, match.group("number")))
        return segments

    @staticmethod
    def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    @staticmethod
    def _is_section_heading(text: str, style_name: str | None) -> bool:
        stripped = text.strip()
        style = (style_name or "").casefold()
        if style.startswith(("heading", "заголовок")):
            return True
        return TOP_LEVEL_SECTION_PATTERN.match(stripped) is not None

    @staticmethod
    def _make_section(
        document_id: UUID, block_index: int, text: str, style_name: str | None
    ) -> Section:
        stripped = text.strip()
        numeric_match = re.match(r"^(\d+(?:\.\d+)*)\.\s*", stripped)
        number = numeric_match.group(1) if numeric_match else None
        level = len(number.split(".")) if number else 1
        return Section(
            id=f"{document_id}:s{block_index}",
            document_id=document_id,
            title=stripped,
            number=number,
            level=level,
            parent_id=None,
        )


clause_segmenter = ClauseSegmenter()
