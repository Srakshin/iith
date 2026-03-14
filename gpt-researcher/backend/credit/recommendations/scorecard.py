from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..feature_extraction.models import FeatureEvidence
from ..ingestion.models import StructuredDocumentRecord
from ..case_models import BorrowerCase, QualitativeCreditOfficerNotes, utc_now
from .models import (
    CaseRecommendationExplanation,
    CreditRecommendationResult,
    DriverDirection,
    PricingRecommendation,
    RecommendationDecision,
    RecommendedLoanLimit,
    ScoreDriver,
    ScoringEvidenceReference,
    ScoringImplementationType,
)


@dataclass(frozen=True)
class _NoteSignal:
    direction: str
    impact_points: float
    rationale: str


class DeterministicScorecardModel:
    model_name = "transparent_recommendation_engine"
    model_version = "phase4-scorecard-v1"
    implementation_type = ScoringImplementationType.RULE_BASED.value

    def score(self, case: BorrowerCase) -> CreditRecommendationResult:
        if case.dossier.credit_features is None:
            raise ValueError("Credit features must be computed before scoring.")

        drivers: list[ScoreDriver] = []
        drivers.extend(self._document_quality_drivers(case))
        drivers.extend(self._feature_drivers(case))
        drivers.extend(self._structured_flag_drivers(case))
        drivers.extend(self._research_drivers(case))
        drivers.extend(self._qualitative_note_drivers(case.dossier.qualitative_credit_officer_notes))

        risk_score = self._compute_risk_score(drivers)
        structured_flags = self._structured_flags(case.dossier.structured_documents)
        decision = self._decision(risk_score, structured_flags)
        risk_band = self._risk_band(risk_score)

        positive_drivers = sorted(
            [driver for driver in drivers if driver.direction == DriverDirection.POSITIVE.value],
            key=lambda driver: driver.impact_points,
            reverse=True,
        )
        negative_drivers = sorted(
            [driver for driver in drivers if driver.direction == DriverDirection.NEGATIVE.value],
            key=lambda driver: driver.impact_points,
            reverse=True,
        )

        loan_limit = self._recommended_loan_limit(case, risk_score, decision)
        pricing = self._pricing_recommendation(risk_score, decision)

        return CreditRecommendationResult(
            generated_at=utc_now(),
            engine_name=self.model_name,
            engine_version=self.model_version,
            model_type=self.implementation_type,
            decision=decision.value,
            overall_risk_score=risk_score,
            risk_band=risk_band,
            recommended_loan_limit=loan_limit,
            pricing=pricing,
            top_positive_drivers=positive_drivers[:3],
            top_negative_drivers=negative_drivers[:3],
            all_drivers=sorted(drivers, key=lambda driver: driver.impact_points, reverse=True),
            explanation=self._explanation(
                decision,
                risk_score,
                loan_limit,
                pricing,
                positive_drivers[:2],
                negative_drivers[:3],
            ),
            assumptions=[
                "No labeled credit-decision training dataset was found in the repo, so the recommendation layer uses a deterministic explainable scorecard.",
                "Risk score ranges from 0 to 100, where higher values indicate higher credit risk.",
                "Loan limit uses conservative revenue and turnover anchors and is not a substitute for formal underwriting or collateral valuation.",
                "The scoring engine is intentionally modular so an interpret EBM can replace this scorecard later without changing the API contract.",
            ],
        )

    def _document_quality_drivers(self, case: BorrowerCase) -> list[ScoreDriver]:
        features = case.dossier.credit_features.document_quality
        drivers: list[ScoreDriver] = []

        if features.completeness_score >= 0.85 and not features.missing_core_documents:
            drivers.append(
                self._driver(
                    code="document_completeness_strong",
                    label="Core document coverage is strong",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=8.0,
                    rationale=(
                        f"Completeness score is {features.completeness_score:.2f} with all core financial "
                        "document types available."
                    ),
                    feature_refs=["dossier.credit_features.document_quality.completeness_score"],
                    evidence=self._feature_evidence("document_quality", features.evidence),
                )
            )
        elif features.missing_core_documents or features.placeholder_documents:
            missing = ", ".join(features.missing_core_documents) or "placeholder-backed records"
            drivers.append(
                self._driver(
                    code="document_completeness_weak",
                    label="Document coverage needs review",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=10.0,
                    rationale=f"Document quality is constrained by missing or weak inputs: {missing}.",
                    feature_refs=[
                        "dossier.credit_features.document_quality.missing_core_documents",
                        "dossier.credit_features.document_quality.placeholder_documents",
                    ],
                    evidence=self._feature_evidence("document_quality", features.evidence),
                )
            )

        if features.extraction_confidence >= 0.85:
            drivers.append(
                self._driver(
                    code="extraction_confidence_high",
                    label="Extraction confidence is high",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=3.0,
                    rationale=(
                        f"Average structured extraction confidence is {features.extraction_confidence:.2f}."
                    ),
                    feature_refs=["dossier.credit_features.document_quality.extraction_confidence"],
                    evidence=self._feature_evidence("document_quality", features.evidence),
                )
            )
        elif features.extraction_confidence and features.extraction_confidence < 0.65:
            drivers.append(
                self._driver(
                    code="extraction_confidence_low",
                    label="Extraction confidence is low",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=5.0,
                    rationale=(
                        f"Average structured extraction confidence is only {features.extraction_confidence:.2f}."
                    ),
                    feature_refs=["dossier.credit_features.document_quality.extraction_confidence"],
                    evidence=self._feature_evidence("document_quality", features.evidence),
                )
            )

        return drivers

    def _feature_drivers(self, case: BorrowerCase) -> list[ScoreDriver]:
        features = case.dossier.credit_features
        drivers: list[ScoreDriver] = []

        turnover = features.turnover_revenue
        if turnover.turnover_trend == "increasing":
            drivers.append(
                self._driver(
                    code="turnover_increasing",
                    label="Revenue trend is improving",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=7.0,
                    rationale=(
                        f"Turnover trend is increasing with {turnover.revenue_growth_pct or 0:.2f}% growth "
                        "across the observed series."
                    ),
                    feature_refs=[
                        "dossier.credit_features.turnover_revenue.turnover_trend",
                        "dossier.credit_features.turnover_revenue.revenue_growth_pct",
                    ],
                    evidence=self._feature_evidence("turnover_revenue", turnover.evidence),
                )
            )
        elif turnover.turnover_trend == "declining":
            drivers.append(
                self._driver(
                    code="turnover_declining",
                    label="Revenue trend is weakening",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=9.0,
                    rationale=(
                        f"Turnover trend is declining with {turnover.revenue_growth_pct or 0:.2f}% change."
                    ),
                    feature_refs=[
                        "dossier.credit_features.turnover_revenue.turnover_trend",
                        "dossier.credit_features.turnover_revenue.revenue_growth_pct",
                    ],
                    evidence=self._feature_evidence("turnover_revenue", turnover.evidence),
                )
            )

        if turnover.seasonality_flag:
            drivers.append(
                self._driver(
                    code="turnover_seasonality",
                    label="Turnover volatility is elevated",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=3.0,
                    rationale="Monthly turnover series shows material volatility that needs explanation.",
                    feature_refs=["dossier.credit_features.turnover_revenue.seasonality_flag"],
                    evidence=self._feature_evidence("turnover_revenue", turnover.evidence),
                )
            )

        consistency = features.gst_bank_consistency
        if consistency.consistency_band == "aligned":
            drivers.append(
                self._driver(
                    code="gst_bank_aligned",
                    label="GST and bank turnover are aligned",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=10.0,
                    rationale=(
                        f"Average GST-bank gap is {consistency.average_gap_pct or 0:.2f}% across "
                        f"{len(consistency.overlap_periods)} shared periods."
                    ),
                    feature_refs=[
                        "dossier.credit_features.gst_bank_consistency.consistency_band",
                        "dossier.credit_features.gst_bank_consistency.average_gap_pct",
                    ],
                    evidence=self._feature_evidence("gst_bank_consistency", consistency.evidence),
                )
            )
        elif consistency.consistency_band == "moderate_variance":
            drivers.append(
                self._driver(
                    code="gst_bank_variance_moderate",
                    label="GST and bank turnover show variance",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=8.0,
                    rationale=(
                        f"Average GST-bank gap is {consistency.average_gap_pct or 0:.2f}%, which needs explanation."
                    ),
                    feature_refs=[
                        "dossier.credit_features.gst_bank_consistency.consistency_band",
                        "dossier.credit_features.gst_bank_consistency.average_gap_pct",
                    ],
                    evidence=self._feature_evidence("gst_bank_consistency", consistency.evidence),
                )
            )
        elif consistency.consistency_band == "high_variance":
            drivers.append(
                self._driver(
                    code="gst_bank_variance_high",
                    label="GST and bank turnover diverge materially",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=16.0,
                    rationale=(
                        f"Average GST-bank gap is {consistency.average_gap_pct or 0:.2f}% with "
                        f"a max gap of {consistency.max_gap_pct or 0:.2f}%."
                    ),
                    feature_refs=[
                        "dossier.credit_features.gst_bank_consistency.average_gap_pct",
                        "dossier.credit_features.gst_bank_consistency.max_gap_pct",
                    ],
                    evidence=self._feature_evidence("gst_bank_consistency", consistency.evidence),
                )
            )

        circular = features.circular_trading
        if circular.suspicion_level == "high":
            rules = ", ".join(circular.triggered_rules) or "multiple circularity heuristics"
            drivers.append(
                self._driver(
                    code="circular_trading_high",
                    label="Circular trading heuristics are severe",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=20.0,
                    rationale=(
                        f"Circular trading suspicion score is {circular.suspicion_score:.2f} with triggers: {rules}."
                    ),
                    feature_refs=[
                        "dossier.credit_features.circular_trading.suspicion_score",
                        "dossier.credit_features.circular_trading.triggered_rules",
                    ],
                    evidence=self._feature_evidence("circular_trading", circular.evidence),
                )
            )
        elif circular.suspicion_level == "medium":
            drivers.append(
                self._driver(
                    code="circular_trading_medium",
                    label="Circular trading heuristics are elevated",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=12.0,
                    rationale=(
                        f"Circular trading suspicion score is {circular.suspicion_score:.2f} and requires review."
                    ),
                    feature_refs=[
                        "dossier.credit_features.circular_trading.suspicion_score",
                        "dossier.credit_features.circular_trading.triggered_rules",
                    ],
                    evidence=self._feature_evidence("circular_trading", circular.evidence),
                )
            )
        else:
            drivers.append(
                self._driver(
                    code="circular_trading_low",
                    label="Circular trading heuristics are limited",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=4.0,
                    rationale=(
                        f"Circular trading suspicion level is {circular.suspicion_level} "
                        f"with score {circular.suspicion_score:.2f}."
                    ),
                    feature_refs=[
                        "dossier.credit_features.circular_trading.suspicion_level",
                        "dossier.credit_features.circular_trading.suspicion_score",
                    ],
                    evidence=self._feature_evidence("circular_trading", circular.evidence),
                )
            )

        inflation = features.revenue_inflation
        if inflation.inflation_risk == "low":
            drivers.append(
                self._driver(
                    code="revenue_inflation_low",
                    label="Reported revenue is broadly supported",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=6.0,
                    rationale=(
                        f"Revenue inflation risk is low with reported/GST ratio "
                        f"{inflation.reported_to_gst_ratio or 0:.2f} and reported/bank ratio "
                        f"{inflation.reported_to_bank_ratio or 0:.2f}."
                    ),
                    feature_refs=[
                        "dossier.credit_features.revenue_inflation.inflation_risk",
                        "dossier.credit_features.revenue_inflation.reported_to_gst_ratio",
                        "dossier.credit_features.revenue_inflation.reported_to_bank_ratio",
                    ],
                    evidence=self._feature_evidence("revenue_inflation", inflation.evidence),
                )
            )
        elif inflation.inflation_risk == "medium":
            drivers.append(
                self._driver(
                    code="revenue_inflation_medium",
                    label="Reported revenue needs reconciliation",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=8.0,
                    rationale=(
                        f"Revenue inflation risk is medium with reported/GST ratio "
                        f"{inflation.reported_to_gst_ratio or 0:.2f}."
                    ),
                    feature_refs=[
                        "dossier.credit_features.revenue_inflation.inflation_risk",
                        "dossier.credit_features.revenue_inflation.reported_to_gst_ratio",
                    ],
                    evidence=self._feature_evidence("revenue_inflation", inflation.evidence),
                )
            )
        elif inflation.inflation_risk == "high":
            drivers.append(
                self._driver(
                    code="revenue_inflation_high",
                    label="Reported revenue appears overstated",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=16.0,
                    rationale=(
                        f"Revenue inflation risk is high with reported/GST ratio "
                        f"{inflation.reported_to_gst_ratio or 0:.2f} and reported/bank ratio "
                        f"{inflation.reported_to_bank_ratio or 0:.2f}."
                    ),
                    feature_refs=[
                        "dossier.credit_features.revenue_inflation.reported_to_gst_ratio",
                        "dossier.credit_features.revenue_inflation.reported_to_bank_ratio",
                    ],
                    evidence=self._feature_evidence("revenue_inflation", inflation.evidence),
                )
            )

        liquidity = features.liquidity_leverage
        if liquidity.liquidity_band == "healthy":
            drivers.append(
                self._driver(
                    code="liquidity_healthy",
                    label="Liquidity proxy is healthy",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=8.0,
                    rationale=f"Current ratio proxy is {liquidity.current_ratio_proxy or 0:.2f}.",
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.current_ratio_proxy",
                        "dossier.credit_features.liquidity_leverage.liquidity_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )
        elif liquidity.liquidity_band == "watch":
            drivers.append(
                self._driver(
                    code="liquidity_watch",
                    label="Liquidity needs monitoring",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=5.0,
                    rationale=(
                        f"Current ratio proxy is {liquidity.current_ratio_proxy or 0:.2f}, which is near the watch band."
                    ),
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.current_ratio_proxy",
                        "dossier.credit_features.liquidity_leverage.liquidity_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )
        elif liquidity.liquidity_band == "stretched":
            drivers.append(
                self._driver(
                    code="liquidity_stretched",
                    label="Liquidity is stretched",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=12.0,
                    rationale=(
                        f"Current ratio proxy is {liquidity.current_ratio_proxy or 0:.2f}, below comfort levels."
                    ),
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.current_ratio_proxy",
                        "dossier.credit_features.liquidity_leverage.liquidity_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )

        if liquidity.leverage_band == "manageable":
            drivers.append(
                self._driver(
                    code="leverage_manageable",
                    label="Debt burden is manageable on revenue",
                    direction=DriverDirection.POSITIVE.value,
                    impact_points=5.0,
                    rationale=f"Debt-to-revenue ratio is {liquidity.debt_to_revenue_ratio or 0:.2f}.",
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.debt_to_revenue_ratio",
                        "dossier.credit_features.liquidity_leverage.leverage_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )
        elif liquidity.leverage_band == "moderate":
            drivers.append(
                self._driver(
                    code="leverage_moderate",
                    label="Leverage is moderate",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=6.0,
                    rationale=f"Debt-to-revenue ratio is {liquidity.debt_to_revenue_ratio or 0:.2f}.",
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.debt_to_revenue_ratio",
                        "dossier.credit_features.liquidity_leverage.leverage_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )
        elif liquidity.leverage_band == "high":
            drivers.append(
                self._driver(
                    code="leverage_high",
                    label="Leverage is high",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=12.0,
                    rationale=f"Debt-to-revenue ratio is {liquidity.debt_to_revenue_ratio or 0:.2f}.",
                    feature_refs=[
                        "dossier.credit_features.liquidity_leverage.debt_to_revenue_ratio",
                        "dossier.credit_features.liquidity_leverage.leverage_band",
                    ],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )

        if liquidity.debt_to_ebitda_ratio and liquidity.debt_to_ebitda_ratio >= 4.0:
            drivers.append(
                self._driver(
                    code="debt_to_ebitda_high",
                    label="Debt to EBITDA is elevated",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=6.0,
                    rationale=f"Debt-to-EBITDA ratio is {liquidity.debt_to_ebitda_ratio:.2f}x.",
                    feature_refs=["dossier.credit_features.liquidity_leverage.debt_to_ebitda_ratio"],
                    evidence=self._feature_evidence("liquidity_leverage", liquidity.evidence),
                )
            )

        return drivers

    def _structured_flag_drivers(self, case: BorrowerCase) -> list[ScoreDriver]:
        impact_map = {
            "default_notice": ("Default notice captured", 24.0),
            "recovery_action": ("Recovery action signal captured", 20.0),
            "auditor_qualification": ("Auditor qualification captured", 12.0),
            "bank_stress_signal": ("Bank stress signal captured", 10.0),
            "related_party_signal": ("Related-party signal captured", 6.0),
            "tight_covenant_signal": ("Tight covenant signal captured", 4.0),
        }
        drivers: list[ScoreDriver] = []
        for record in case.dossier.structured_documents:
            for flag in record.flags:
                config = impact_map.get(flag)
                if config is None:
                    continue
                label, impact = config
                drivers.append(
                    self._driver(
                        code=f"structured_flag_{flag}",
                        label=label,
                        direction=DriverDirection.NEGATIVE.value,
                        impact_points=impact,
                        rationale=(
                            f"{record.document_type} produced structured flag `{flag}` from "
                            f"{record.source_filename}."
                        ),
                        feature_refs=["dossier.structured_documents[*].flags"],
                        evidence=[
                            ScoringEvidenceReference(
                                source_type="structured_flag",
                                source_path="dossier.structured_documents[*].flags",
                                source_document_id=record.source_document_id,
                                source_document_type=record.document_type,
                                message=f"{record.document_type} flagged `{flag}` in {record.source_filename}.",
                            )
                        ],
                    )
                )
        return drivers

    def _research_drivers(self, case: BorrowerCase) -> list[ScoreDriver]:
        impact_map = {
            "litigation_signal": 10.0,
            "execution_delay": 5.0,
            "margin_pressure": 6.0,
            "regulatory_action": 12.0,
            "fraud_signal": 20.0,
        }
        drivers: list[ScoreDriver] = []
        research = case.dossier.secondary_research
        if research.status == "unavailable":
            drivers.append(
                self._driver(
                    code="research_unavailable",
                    label="Secondary research coverage is unavailable",
                    direction=DriverDirection.NEGATIVE.value,
                    impact_points=4.0,
                    rationale=research.message or "External diligence could not be completed in this environment.",
                    feature_refs=["dossier.secondary_research.status"],
                    evidence=[
                        ScoringEvidenceReference(
                            source_type="secondary_research",
                            source_path="dossier.secondary_research.status",
                            message=research.coverage_note or research.message or "Secondary research unavailable.",
                        )
                    ],
                )
            )
            return drivers

        seen_flags: set[tuple[str, str]] = set()
        for finding in research.findings:
            for risk_flag in finding.risk_flags:
                key = (finding.topic, risk_flag)
                if key in seen_flags:
                    continue
                seen_flags.add(key)
                impact = impact_map.get(risk_flag, 5.0)
                evidence = [
                    ScoringEvidenceReference(
                        source_type="secondary_research",
                        source_path="dossier.secondary_research.findings[*]",
                        topic=finding.topic,
                        message=finding.summary or f"Secondary research flagged `{risk_flag}`.",
                    )
                ]
                for item in finding.evidence:
                    evidence.append(
                        ScoringEvidenceReference(
                            source_type="secondary_research_evidence",
                            source_path="dossier.secondary_research.evidence[*]",
                            topic=finding.topic,
                            message=item.title,
                            metadata={
                                "source_url": item.source_url,
                                "source_title": item.source_title,
                            },
                        )
                    )
                drivers.append(
                    self._driver(
                        code=f"research_flag_{finding.topic}_{risk_flag}",
                        label=f"Secondary research flagged {risk_flag.replace('_', ' ')}",
                        direction=DriverDirection.NEGATIVE.value,
                        impact_points=impact,
                        rationale=f"Secondary research under {finding.topic} reported `{risk_flag}`.",
                        feature_refs=[
                            "dossier.secondary_research.findings[*].risk_flags",
                            "dossier.secondary_research.findings[*].summary",
                        ],
                        evidence=evidence,
                    )
                )
        return drivers

    def _qualitative_note_drivers(
        self,
        notes: QualitativeCreditOfficerNotes,
    ) -> list[ScoreDriver]:
        note_map = {
            "factory_operating_capacity": notes.factory_operating_capacity,
            "management_quality": notes.management_quality,
            "governance_concerns": notes.governance_concerns,
            "collateral_observations": notes.collateral_observations,
            "site_visit_comments": notes.site_visit_comments,
            "additional_comments": notes.additional_comments,
        }
        drivers: list[ScoreDriver] = []
        for field_name, note in note_map.items():
            if not note:
                continue
            signal = self._classify_note(field_name, note)
            if signal is None:
                continue
            drivers.append(
                self._driver(
                    code=f"qualitative_{field_name}",
                    label=field_name.replace("_", " ").title(),
                    direction=signal.direction,
                    impact_points=signal.impact_points,
                    rationale=signal.rationale,
                    feature_refs=[f"dossier.qualitative_credit_officer_notes.{field_name}"],
                    evidence=[
                        ScoringEvidenceReference(
                            source_type="qualitative_note",
                            source_path=f"dossier.qualitative_credit_officer_notes.{field_name}",
                            topic=field_name,
                            message=note,
                        )
                    ],
                )
            )
        return drivers

    def _classify_note(self, field_name: str, note: str) -> _NoteSignal | None:
        lowered = note.lower()
        negative_keywords = (
            "delay",
            "weak",
            "concern",
            "issue",
            "irregular",
            "pending",
            "underutilized",
            "idle",
            "shortfall",
            "stress",
            "dispute",
            "repair",
            "lower than",
        )
        positive_keywords = (
            "capable",
            "responsive",
            "experienced",
            "strong",
            "installed",
            "tagged",
            "insured",
            "satisfactory",
            "supportive",
            "good",
        )

        if field_name == "governance_concerns":
            return _NoteSignal(
                direction=DriverDirection.NEGATIVE.value,
                impact_points=10.0,
                rationale="Primary diligence notes record governance concerns that require mitigation before lending.",
            )

        if any(keyword in lowered for keyword in negative_keywords):
            impact = 7.0 if field_name in {"factory_operating_capacity", "site_visit_comments"} else 5.0
            return _NoteSignal(
                direction=DriverDirection.NEGATIVE.value,
                impact_points=impact,
                rationale=(
                    f"Primary diligence note on {field_name.replace('_', ' ')} contains an adverse observation."
                ),
            )

        if any(keyword in lowered for keyword in positive_keywords):
            return _NoteSignal(
                direction=DriverDirection.POSITIVE.value,
                impact_points=4.0,
                rationale=f"Primary diligence note on {field_name.replace('_', ' ')} is supportive.",
            )

        return None

    def _compute_risk_score(self, drivers: Iterable[ScoreDriver]) -> float:
        risk_score = 50.0
        for driver in drivers:
            if driver.direction == DriverDirection.NEGATIVE.value:
                risk_score += driver.impact_points
            else:
                risk_score -= driver.impact_points
        return round(max(0.0, min(risk_score, 100.0)), 1)

    def _decision(
        self,
        risk_score: float,
        structured_flags: set[str],
    ) -> RecommendationDecision:
        if structured_flags & {"default_notice", "recovery_action"}:
            return RecommendationDecision.REJECT
        if risk_score >= 72:
            return RecommendationDecision.REJECT
        if risk_score >= 45:
            return RecommendationDecision.REVIEW
        return RecommendationDecision.LEND

    def _risk_band(self, risk_score: float) -> str:
        if risk_score < 30:
            return "low"
        if risk_score < 45:
            return "guarded"
        if risk_score < 60:
            return "moderate"
        if risk_score < 75:
            return "high"
        return "severe"

    def _recommended_loan_limit(
        self,
        case: BorrowerCase,
        risk_score: float,
        decision: RecommendationDecision,
    ) -> RecommendedLoanLimit:
        features = case.dossier.credit_features
        annual_revenue_candidates = [
            value
            for value in [
                features.revenue_inflation.reported_revenue,
                features.revenue_inflation.gst_reference_revenue,
                features.revenue_inflation.bank_reference_revenue,
                case.dossier.financial_snapshot.annual_revenue.get("amount"),
            ]
            if value
        ]
        currency = (
            case.dossier.credit_request.requested_amount.get("currency")
            or case.dossier.financial_snapshot.currency
            or "INR"
        )
        conservative_revenue_anchor = min(annual_revenue_candidates) if annual_revenue_candidates else 0.0
        turnover_capacity = (
            (features.liquidity_leverage.average_monthly_debits or 0.0) * 2.0
            if features.liquidity_leverage.average_monthly_debits
            else 0.0
        )

        base_limit_candidates = [value for value in [conservative_revenue_anchor * 0.18, turnover_capacity] if value]
        requested_amount = case.dossier.credit_request.requested_amount.get("amount")
        if isinstance(requested_amount, (int, float)) and requested_amount > 0:
            base_limit_candidates.append(float(requested_amount))

        base_limit = min(base_limit_candidates) if base_limit_candidates else 0.0
        if decision == RecommendationDecision.LEND:
            multiplier = 0.95 if risk_score < 25 else 0.85
        elif decision == RecommendationDecision.REVIEW:
            multiplier = 0.55
        else:
            multiplier = 0.0

        recommended_amount = round(base_limit * multiplier, 2)
        utilization = (
            round(recommended_amount / conservative_revenue_anchor, 4)
            if conservative_revenue_anchor
            else None
        )
        basis_parts = []
        if conservative_revenue_anchor:
            basis_parts.append(
                f"18% of conservative validated annual revenue anchor {conservative_revenue_anchor:,.0f}"
            )
        if turnover_capacity:
            basis_parts.append(
                f"2x average monthly debits {features.liquidity_leverage.average_monthly_debits:,.0f}"
            )
        if isinstance(requested_amount, (int, float)) and requested_amount > 0:
            basis_parts.append(f"capped against requested amount {requested_amount:,.0f}")
        if not basis_parts:
            basis_parts.append("no reliable turnover anchor was available")

        return RecommendedLoanLimit(
            amount=recommended_amount,
            currency=currency,
            basis=(
                f"{'; '.join(basis_parts)}; decision multiplier {multiplier:.2f} from risk score {risk_score:.1f}."
            ),
            utilization_ratio_to_revenue=utilization,
        )

    def _pricing_recommendation(
        self,
        risk_score: float,
        decision: RecommendationDecision,
    ) -> PricingRecommendation:
        if decision == RecommendationDecision.LEND:
            premium = 125 if risk_score < 30 else 200
        elif decision == RecommendationDecision.REVIEW:
            premium = 350 if risk_score < 60 else 500
        else:
            premium = 700

        return PricingRecommendation(
            risk_premium_bps=premium,
            interest_rate_adjustment_bps=premium,
            summary=(
                f"Apply a {premium} bps pricing premium over the base lending grid for a "
                f"{decision.value} recommendation with risk score {risk_score:.1f}."
            ),
        )

    def _explanation(
        self,
        decision: RecommendationDecision,
        risk_score: float,
        loan_limit: RecommendedLoanLimit,
        pricing: PricingRecommendation,
        positive_drivers: list[ScoreDriver],
        negative_drivers: list[ScoreDriver],
    ) -> CaseRecommendationExplanation:
        positive_summary = "; ".join(
            f"{driver.label.lower()}: {driver.rationale}" for driver in positive_drivers
        ) or "limited positive support was available."
        negative_summary = "; ".join(
            f"{driver.label.lower()}: {driver.rationale}" for driver in negative_drivers
        ) or "no material adverse drivers were captured."

        return CaseRecommendationExplanation(
            executive_summary=(
                f"Recommendation: {decision.value}. Overall risk score is {risk_score:.1f}/100. "
                f"Recommended limit is {loan_limit.currency} {loan_limit.amount:,.2f} and pricing premium is "
                f"{pricing.risk_premium_bps} bps."
            ),
            judge_summary=(
                "This recommendation is produced by a deterministic scorecard that cites extracted financial "
                f"features, structured risk flags, external research, and primary diligence notes. Key positive "
                f"drivers were {positive_summary} Key negative drivers were {negative_summary}"
            ),
            credit_officer_summary=(
                f"The scorecard recommends `{decision.value}` because {negative_summary} "
                f"Offsetting support came from {positive_summary}"
            ),
            watchouts=[driver.rationale for driver in negative_drivers[:3]],
        )

    def _structured_flags(self, structured_documents: list[StructuredDocumentRecord]) -> set[str]:
        return {flag for record in structured_documents for flag in record.flags}

    def _feature_evidence(
        self,
        feature_name: str,
        evidence_items: list[FeatureEvidence],
    ) -> list[ScoringEvidenceReference]:
        references: list[ScoringEvidenceReference] = []
        for item in evidence_items[:4]:
            references.append(
                ScoringEvidenceReference(
                    source_type="credit_feature",
                    source_path=f"dossier.credit_features.{feature_name}",
                    source_document_id=item.source_document_id,
                    source_document_type=item.source_document_type,
                    message=item.message,
                    metadata={"confidence": item.confidence},
                )
            )
        return references

    def _driver(
        self,
        code: str,
        label: str,
        direction: str,
        impact_points: float,
        rationale: str,
        feature_refs: list[str],
        evidence: list[ScoringEvidenceReference],
    ) -> ScoreDriver:
        return ScoreDriver(
            code=code,
            label=label,
            direction=direction,
            impact_points=round(impact_points, 2),
            rationale=rationale,
            feature_refs=feature_refs,
            evidence=evidence,
        )
