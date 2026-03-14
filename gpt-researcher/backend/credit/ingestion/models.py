from __future__ import annotations

from pydantic import BaseModel, Field


class StructuredMetric(BaseModel):
    name: str
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    period: str | None = None
    confidence: float = 0.0
    raw_value: str | None = None
    evidence: list[str] = Field(default_factory=list)


class StructuredSeriesPoint(BaseModel):
    period: str
    value: float
    label: str | None = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class StructuredCounterparty(BaseModel):
    name: str
    role: str | None = None
    amount: float | None = None
    currency: str | None = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class StructuredObligation(BaseModel):
    obligation_type: str
    amount: float | None = None
    currency: str | None = None
    due_date: str | None = None
    status: str | None = None
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class StructuredDocumentRecord(BaseModel):
    record_id: str
    source_document_id: str
    source_filename: str
    source_category: str
    document_type: str
    parser_name: str
    parser_version: str = "phase2-rule-v1"
    confidence: float = 0.0
    metrics: list[StructuredMetric] = Field(default_factory=list)
    series: dict[str, list[StructuredSeriesPoint]] = Field(default_factory=dict)
    counterparties: list[StructuredCounterparty] = Field(default_factory=list)
    obligations: list[StructuredObligation] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
