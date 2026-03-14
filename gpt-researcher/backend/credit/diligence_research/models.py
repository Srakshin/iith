from __future__ import annotations

from pydantic import BaseModel, Field


class SecondaryResearchJob(BaseModel):
    borrower_name: str
    legal_name: str | None = None
    industry: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    query_domains: list[str] = Field(default_factory=list)


class ResearchExtractionEvidence(BaseModel):
    topic: str
    title: str
    summary: str
    source_url: str | None = None
    source_title: str | None = None
    source_type: str | None = None
    confidence: str | None = None
    observed_at: str | None = None
    risk_flags: list[str] = Field(default_factory=list)


class ResearchExtractionFinding(BaseModel):
    topic: str
    status: str = "available"
    summary: str | None = None
    evidence: list[ResearchExtractionEvidence] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    message: str | None = None


class ResearchExtractionPayload(BaseModel):
    executive_summary: str | None = None
    findings: list[ResearchExtractionFinding] = Field(default_factory=list)
