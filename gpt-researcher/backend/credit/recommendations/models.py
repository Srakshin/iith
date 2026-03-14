from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RecommendationDecision(str, Enum):
    LEND = "lend"
    REVIEW = "review"
    REJECT = "reject"


class ScoringImplementationType(str, Enum):
    RULE_BASED = "rule_based"
    MODEL_BASED = "model_based"
    HYBRID = "hybrid"


class DriverDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class ScoringEvidenceReference(BaseModel):
    source_type: str
    message: str
    source_path: str | None = None
    source_document_id: str | None = None
    source_document_type: str | None = None
    topic: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ScoreDriver(BaseModel):
    code: str
    label: str
    direction: str
    impact_points: float
    rationale: str
    feature_refs: list[str] = Field(default_factory=list)
    evidence: list[ScoringEvidenceReference] = Field(default_factory=list)


class RecommendedLoanLimit(BaseModel):
    amount: float
    currency: str = "INR"
    basis: str
    utilization_ratio_to_revenue: float | None = None


class PricingRecommendation(BaseModel):
    risk_premium_bps: int
    interest_rate_adjustment_bps: int
    summary: str


class CaseRecommendationExplanation(BaseModel):
    executive_summary: str
    judge_summary: str
    credit_officer_summary: str
    watchouts: list[str] = Field(default_factory=list)


class CreditRecommendationResult(BaseModel):
    generated_at: str
    engine_name: str
    engine_version: str
    model_type: str
    decision: str
    overall_risk_score: float
    risk_band: str
    recommended_loan_limit: RecommendedLoanLimit
    pricing: PricingRecommendation
    top_positive_drivers: list[ScoreDriver] = Field(default_factory=list)
    top_negative_drivers: list[ScoreDriver] = Field(default_factory=list)
    all_drivers: list[ScoreDriver] = Field(default_factory=list)
    explanation: CaseRecommendationExplanation
    global_explanation: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)
