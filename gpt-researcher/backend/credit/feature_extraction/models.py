from __future__ import annotations

from pydantic import BaseModel, Field


class FeatureEvidence(BaseModel):
    source_document_id: str | None = None
    source_document_type: str | None = None
    message: str
    confidence: float | None = None


class FeatureSeriesPoint(BaseModel):
    period: str
    value: float
    source_document_id: str | None = None


class TurnoverRevenueFeatures(BaseModel):
    available: bool = False
    latest_annual_revenue: float | None = None
    annualized_gst_turnover: float | None = None
    annualized_bank_credits: float | None = None
    revenue_growth_pct: float | None = None
    turnover_trend: str | None = None
    seasonality_flag: bool = False
    monthly_turnover_series: list[FeatureSeriesPoint] = Field(default_factory=list)
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class GstBankConsistencyFeatures(BaseModel):
    available: bool = False
    overlap_periods: list[str] = Field(default_factory=list)
    average_gap_pct: float | None = None
    max_gap_pct: float | None = None
    average_gst_to_bank_ratio: float | None = None
    consistency_band: str = "insufficient_data"
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class CircularTradingFeatures(BaseModel):
    suspicion_level: str = "low"
    suspicion_score: float = 0.0
    same_day_in_out_ratio: float | None = None
    round_amount_credit_ratio: float | None = None
    cash_deposit_ratio: float | None = None
    top_counterparty_share: float | None = None
    triggered_rules: list[str] = Field(default_factory=list)
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class RevenueInflationFeatures(BaseModel):
    available: bool = False
    reported_revenue: float | None = None
    gst_reference_revenue: float | None = None
    bank_reference_revenue: float | None = None
    reported_to_gst_ratio: float | None = None
    reported_to_bank_ratio: float | None = None
    inflation_risk: str = "insufficient_data"
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class LiquidityLeverageFeatures(BaseModel):
    available: bool = False
    current_ratio_proxy: float | None = None
    average_monthly_balance: float | None = None
    average_monthly_debits: float | None = None
    total_debt: float | None = None
    debt_to_revenue_ratio: float | None = None
    debt_to_ebitda_ratio: float | None = None
    liquidity_band: str = "unknown"
    leverage_band: str = "unknown"
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class DocumentQualityFeatures(BaseModel):
    completeness_score: float = 0.0
    extraction_confidence: float = 0.0
    supported_document_types: list[str] = Field(default_factory=list)
    missing_core_documents: list[str] = Field(default_factory=list)
    placeholder_documents: int = 0
    parsed_structured_documents: int = 0
    evidence: list[FeatureEvidence] = Field(default_factory=list)


class CreditFeatureBundle(BaseModel):
    computed_at: str
    turnover_revenue: TurnoverRevenueFeatures = Field(default_factory=TurnoverRevenueFeatures)
    gst_bank_consistency: GstBankConsistencyFeatures = Field(default_factory=GstBankConsistencyFeatures)
    circular_trading: CircularTradingFeatures = Field(default_factory=CircularTradingFeatures)
    revenue_inflation: RevenueInflationFeatures = Field(default_factory=RevenueInflationFeatures)
    liquidity_leverage: LiquidityLeverageFeatures = Field(default_factory=LiquidityLeverageFeatures)
    document_quality: DocumentQualityFeatures = Field(default_factory=DocumentQualityFeatures)
    assumptions: list[str] = Field(default_factory=list)
