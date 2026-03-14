from __future__ import annotations

from pydantic import BaseModel, Field

from ..recommendations.models import PricingRecommendation, RecommendedLoanLimit


class CamEvidenceReference(BaseModel):
    label: str
    source_type: str
    reference: str | None = None
    source_url: str | None = None
    source_path: str | None = None
    source_document_id: str | None = None
    source_document_type: str | None = None


class CreditAppraisalMemoSection(BaseModel):
    title: str
    summary: str
    assessment: str | None = None
    bullet_points: list[str] = Field(default_factory=list)
    evidence: list[CamEvidenceReference] = Field(default_factory=list)


class FiveCsOfCredit(BaseModel):
    character: CreditAppraisalMemoSection
    capacity: CreditAppraisalMemoSection
    capital: CreditAppraisalMemoSection
    collateral: CreditAppraisalMemoSection
    conditions: CreditAppraisalMemoSection


class CreditAppraisalMemo(BaseModel):
    generated_at: str
    memo_version: str = "phase5-cam-v1"
    case_id: str
    borrower_name: str
    decision: str
    risk_band: str
    overall_risk_score: float
    recommended_limit: RecommendedLoanLimit
    pricing: PricingRecommendation
    borrower_overview: CreditAppraisalMemoSection
    five_cs: FiveCsOfCredit
    key_financial_findings: CreditAppraisalMemoSection
    gst_bank_reconciliation_findings: CreditAppraisalMemoSection
    research_findings_and_flags: CreditAppraisalMemoSection
    primary_due_diligence_notes: CreditAppraisalMemoSection
    final_recommendation: CreditAppraisalMemoSection
    decision_rationale: CreditAppraisalMemoSection
    artifacts: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
