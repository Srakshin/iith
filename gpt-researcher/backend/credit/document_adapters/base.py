from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


AMOUNT_PATTERN = re.compile(
    r"(?:USD|INR|Rs\.?|₹|\$)\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:USD|INR)"
)
DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4})\b",
    re.IGNORECASE,
)
PERCENT_PATTERN = re.compile(r"\b\d+(?:\.\d+)?%")
IDENTIFIER_PATTERN = re.compile(r"\b[A-Z0-9]{6,20}\b")


@dataclass
class ExtractionPayload:
    adapter: str
    placeholder: bool
    extracted_text: str
    extracted_fields: dict[str, Any] = field(default_factory=dict)
    tables: list[dict[str, Any]] = field(default_factory=list)
    pages: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseDocumentAdapter:
    name = "base"

    async def extract(self, file_path: Path) -> ExtractionPayload:
        raise NotImplementedError


def unique_matches(pattern: re.Pattern[str], text: str, limit: int = 10) -> list[str]:
    matches: list[str] = []
    for match in pattern.findall(text):
        if match not in matches:
            matches.append(match)
        if len(matches) >= limit:
            break
    return matches


def build_text_signals(text: str) -> dict[str, Any]:
    normalized = " ".join(text.split())
    return {
        "text_excerpt": normalized[:500],
        "character_count": len(text),
        "line_count": len(text.splitlines()),
        "candidate_amounts": unique_matches(AMOUNT_PATTERN, normalized),
        "candidate_dates": unique_matches(DATE_PATTERN, normalized),
        "candidate_percentages": unique_matches(PERCENT_PATTERN, normalized),
        "candidate_identifiers": unique_matches(IDENTIFIER_PATTERN, normalized),
    }


def read_plaintext_fallback(file_path: Path, max_chars: int = 12000) -> str:
    if file_path.suffix.lower() in {".txt", ".md", ".csv", ".json", ".html", ".xml"}:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
        except Exception:
            return ""
    return ""
