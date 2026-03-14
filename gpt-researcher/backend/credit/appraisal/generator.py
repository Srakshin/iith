from __future__ import annotations

from typing import Iterable

from ..feature_extraction.models import FeatureEvidence
from ..ingestion.models import StructuredDocumentRecord
from ..case_models import BorrowerCase, ExtractedBorrowerDocument, SecondaryResearchFinding, utc_now
from ..recommendations.models import ScoreDriver, ScoringEvidenceReference
from .models import (
    CamEvidenceReference,
    CreditAppraisalMemo,
    CreditAppraisalMemoSection,
    FiveCsOfCredit,
)
from .templates import render_credit_appraisal_memo_markdown


class CreditAppraisalMemoGenerator:
    def generate(self, case: BorrowerCase) -> CreditAppraisalMemo:
        recommendation = case.dossier.credit_recommendation
        features = case.dossier.credit_features
        if recommendation is None or features is None:
            raise ValueError("Credit recommendation and credit features must exist before generating the CAM.")

        return CreditAppraisalMemo(
            generated_at=utc_now(),
            case_id=case.case_id,
            borrower_name=case.borrower_name,
            decision=recommendation.decision,
            risk_band=recommendation.risk_band,
            overall_risk_score=recommendation.overall_risk_score,
            recommended_limit=recommendation.recommended_loan_limit.model_copy(deep=True),
            pricing=recommendation.pricing.model_copy(deep=True),
            borrower_overview=self._build_borrower_overview(case),
            five_cs=FiveCsOfCredit(
                character=self._build_character_section(case),
                capacity=self._build_capacity_section(case),
                capital=self._build_capital_section(case),
                collateral=self._build_collateral_section(case),
                conditions=self._build_conditions_section(case),
            ),
            key_financial_findings=self._build_financial_findings_section(case),
            gst_bank_reconciliation_findings=self._build_gst_bank_section(case),
            research_findings_and_flags=self._build_research_section(case),
            primary_due_diligence_notes=self._build_due_diligence_section(case),
            final_recommendation=self._build_final_recommendation_section(case),
            decision_rationale=self._build_decision_rationale_section(case),
            assumptions=self._dedupe_strings(
                [
                    *features.assumptions,
                    *recommendation.assumptions,
                    "This CAM is an evidence-backed prototype memo for demo underwriting, not a substitute for formal credit approval.",
                ]
            ),
        )

    def render_markdown(self, memo: CreditAppraisalMemo) -> str:
        return render_credit_appraisal_memo_markdown(memo)

    def _build_borrower_overview(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        features = case.dossier.credit_features
        snapshot = case.dossier.financial_snapshot
        currency = self._case_currency(case)
        requested_amount = case.dossier.credit_request.requested_amount.get("amount")
        requested_amount_text = (
            f"{currency} {float(requested_amount):,.2f}"
            if isinstance(requested_amount, (int, float))
            else "Not captured in the dossier"
        )
        purpose = case.dossier.credit_request.purpose or "Working-capital purpose not explicitly captured."
        revenue_anchor = self._amount_from_payload(snapshot.annual_revenue)
        revenue_text = self._format_currency(revenue_anchor, currency)

        summary = (
            f"{case.borrower_name} is being assessed on the basis of {len(case.uploaded_files)} uploaded files, "
            f"{len(case.dossier.documents)} ingested documents, and {len(case.dossier.structured_documents)} structured records. "
            f"The validated annual revenue anchor currently stands at {revenue_text}, while the recommendation engine "
            f"places the case in the {case.dossier.credit_recommendation.risk_band} risk band."
        )
        bullets = [
            f"Facility request: {requested_amount_text}. Purpose: {purpose}",
            f"Document coverage: completeness score {features.document_quality.completeness_score:.2f}; extraction confidence {features.document_quality.extraction_confidence:.2f}.",
            f"Structured document mix: {', '.join(features.document_quality.supported_document_types) or 'no structured document types detected yet'}.",
            f"Financial snapshot date: {snapshot.as_of_date or 'not available'}; annual revenue anchor {revenue_text}.",
        ]
        if features.document_quality.missing_core_documents:
            bullets.append(
                "Missing core documents flagged by the engine: "
                + ", ".join(features.document_quality.missing_core_documents)
                + "."
            )

        return self._section(
            title="Borrower Overview",
            assessment="Complete" if not features.document_quality.missing_core_documents else "Partial",
            summary=summary,
            bullet_points=bullets,
            evidence=[
                *self._financial_snapshot_refs(case),
                *self._feature_refs("document_quality", features.document_quality.evidence, case),
            ],
        )

    def _build_character_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        notes = case.dossier.qualitative_credit_officer_notes
        research = case.dossier.secondary_research
        structured_flags = self._structured_flags(case.dossier.structured_documents)
        research_flags = sorted(
            {
                risk_flag
                for finding in research.findings
                if finding.topic in {"promoters", "litigation", "mca_regulatory_evidence", "company"}
                for risk_flag in finding.risk_flags
            }
        )
        assessment = "Acceptable"
        if structured_flags & {"default_notice", "recovery_action"} or "litigation_signal" in research_flags:
            assessment = "Weak"
        elif notes.governance_concerns or research_flags:
            assessment = "Caution"

        management_note = notes.management_quality or "Management quality note not captured."
        governance_note = notes.governance_concerns or "No governance concern was recorded in the primary notes."
        summary = (
            f"Character is assessed as {assessment.lower()} based on management and governance observations, "
            "plus promoter, litigation, and regulatory signals surfaced during secondary research."
        )
        bullets = [
            f"Management quality: {management_note}",
            f"Governance: {governance_note}",
            "Research flags tied to character: "
            + (", ".join(flag.replace('_', ' ') for flag in research_flags) if research_flags else "none captured."),
            "Structured legal or governance flags: "
            + (", ".join(sorted(structured_flags)) if structured_flags else "none captured."),
        ]
        evidence = [
            *self._note_refs(
                [
                    ("management_quality", notes.management_quality),
                    ("governance_concerns", notes.governance_concerns),
                ]
            ),
            *self._research_finding_refs(
                [
                    finding
                    for finding in research.findings
                    if finding.topic in {"promoters", "litigation", "mca_regulatory_evidence", "company"}
                ]
            ),
            *self._driver_refs(case.dossier.credit_recommendation.top_negative_drivers),
        ]
        return self._section("Character", assessment, summary, bullets, evidence)

    def _build_capacity_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        features = case.dossier.credit_features
        notes = case.dossier.qualitative_credit_officer_notes
        turnover = features.turnover_revenue
        consistency = features.gst_bank_consistency
        circular = features.circular_trading
        liquidity = features.liquidity_leverage

        assessment = "Supportive"
        if consistency.consistency_band == "high_variance" or circular.suspicion_level == "high":
            assessment = "Weak"
        elif consistency.consistency_band in {"moderate_variance", "insufficient_data"} or circular.suspicion_level == "medium":
            assessment = "Caution"

        summary = (
            f"Capacity is assessed as {assessment.lower()} after considering turnover trend, GST-bank alignment, "
            "circular-trading heuristics, liquidity proxies, and any operating-capacity observations from primary diligence."
        )
        bullets = [
            f"Turnover trend: {turnover.turnover_trend or 'not available'} with growth of {self._format_percent(turnover.revenue_growth_pct)}.",
            f"GST-bank consistency: {consistency.consistency_band} with average gap {self._format_percent(consistency.average_gap_pct)} and max gap {self._format_percent(consistency.max_gap_pct)}.",
            f"Circular-trading suspicion: {circular.suspicion_level} at score {circular.suspicion_score:.2f}; triggers {', '.join(circular.triggered_rules) or 'none'}.",
            f"Liquidity proxy: current ratio {self._format_ratio(liquidity.current_ratio_proxy)} and average monthly debits {self._format_currency(liquidity.average_monthly_debits, self._case_currency(case))}.",
        ]
        if notes.factory_operating_capacity:
            bullets.append(f"Operating-capacity note: {notes.factory_operating_capacity}")

        evidence = [
            *self._feature_refs("turnover_revenue", turnover.evidence, case),
            *self._feature_refs("gst_bank_consistency", consistency.evidence, case),
            *self._feature_refs("circular_trading", circular.evidence, case),
            *self._feature_refs("liquidity_leverage", liquidity.evidence, case),
            *self._note_refs([("factory_operating_capacity", notes.factory_operating_capacity)]),
        ]
        return self._section("Capacity", assessment, summary, bullets, evidence)

    def _build_capital_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        features = case.dossier.credit_features
        snapshot = case.dossier.financial_snapshot
        currency = self._case_currency(case)
        annual_revenue = self._amount_from_payload(snapshot.annual_revenue)
        ebitda = self._amount_from_payload(snapshot.ebitda)
        net_income = self._amount_from_payload(snapshot.net_income)
        total_debt = self._amount_from_payload(snapshot.total_debt)
        leverage = features.liquidity_leverage
        assessment = "Supportive"
        if leverage.leverage_band == "high" or (leverage.debt_to_ebitda_ratio or 0) >= 4.0:
            assessment = "Weak"
        elif leverage.leverage_band in {"moderate", "unknown"}:
            assessment = "Caution"

        ebitda_margin = (ebitda / annual_revenue * 100.0) if annual_revenue and ebitda is not None else None
        net_margin = (net_income / annual_revenue * 100.0) if annual_revenue and net_income is not None else None
        summary = (
            f"Capital is assessed as {assessment.lower()} based on the validated revenue base, profitability measures, "
            "and leverage indicators derived from the structured financial records."
        )
        bullets = [
            f"Annual revenue anchor: {self._format_currency(annual_revenue, currency)}.",
            f"EBITDA: {self._format_currency(ebitda, currency)}; implied EBITDA margin {self._format_percent(ebitda_margin)}.",
            f"Net income: {self._format_currency(net_income, currency)}; implied net margin {self._format_percent(net_margin)}.",
            f"Total debt: {self._format_currency(total_debt, currency)}; debt/revenue {self._format_ratio(leverage.debt_to_revenue_ratio)}; debt/EBITDA {self._format_multiple(leverage.debt_to_ebitda_ratio)}.",
        ]
        evidence = [
            *self._financial_snapshot_refs(case),
            *self._feature_refs("liquidity_leverage", leverage.evidence, case),
        ]
        return self._section("Capital", assessment, summary, bullets, evidence)

    def _build_collateral_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        notes = case.dossier.qualitative_credit_officer_notes
        collateral_summary = case.dossier.credit_request.collateral_summary or "Collateral summary not captured in the credit request."
        collateral_note = notes.collateral_observations or "No collateral inspection note was captured."
        assessment = "Limited"
        lowered = collateral_note.lower()
        if any(keyword in lowered for keyword in ("installed", "tagged", "insured", "satisfactory")):
            assessment = "Supportive"
        elif any(keyword in lowered for keyword in ("pending", "issue", "concern", "irregular", "shortfall")):
            assessment = "Caution"

        summary = (
            f"Collateral support is assessed as {assessment.lower()} based on the credit-request summary and the most recent "
            "primary diligence observations on charged assets or supporting security."
        )
        bullets = [
            f"Credit-request collateral summary: {collateral_summary}",
            f"Primary diligence observation: {collateral_note}",
            "Collateral evidence is currently prototype-grade and should be supplemented with valuation, insurance, and charge perfection checks before production use.",
        ]
        evidence = self._note_refs([("collateral_observations", notes.collateral_observations)])
        return self._section("Collateral", assessment, summary, bullets, evidence)

    def _build_conditions_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        research = case.dossier.secondary_research
        request = case.dossier.credit_request
        sector_findings = [finding for finding in research.findings if finding.topic in {"sector_headwinds", "company"}]
        litigation_flags = sorted(
            {
                risk_flag
                for finding in research.findings
                if finding.topic in {"litigation", "mca_regulatory_evidence"}
                for risk_flag in finding.risk_flags
            }
        )
        assessment = "Normal"
        if litigation_flags:
            assessment = "Elevated"
        elif sector_findings:
            assessment = "Watch"

        summary = (
            f"Conditions are assessed as {assessment.lower()} after incorporating sector headwinds, borrower-specific public-web developments, "
            "litigation or regulatory references, and the stated facility purpose or tenor where available."
        )
        sector_summary = (
            "; ".join(
                finding.summary or f"{finding.topic} coverage was {finding.status}."
                for finding in sector_findings
            )
            if sector_findings
            else "Sector and company-specific external conditions were not materially evidenced."
        )
        bullets = [
            f"Sector and company conditions: {sector_summary}",
            "Litigation or regulatory flags: "
            + (", ".join(flag.replace('_', ' ') for flag in litigation_flags) if litigation_flags else "none captured."),
            f"Requested purpose: {request.purpose or 'not captured'}; tenor: {request.tenor_months or 'not captured'} months.",
        ]
        evidence = self._research_finding_refs(
            [
                finding
                for finding in research.findings
                if finding.topic in {"sector_headwinds", "company", "litigation", "mca_regulatory_evidence"}
            ]
        )
        return self._section("Conditions", assessment, summary, bullets, evidence)

    def _build_financial_findings_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        features = case.dossier.credit_features
        snapshot = case.dossier.financial_snapshot
        currency = self._case_currency(case)
        annual_revenue = self._amount_from_payload(snapshot.annual_revenue)
        ebitda = self._amount_from_payload(snapshot.ebitda)
        total_debt = self._amount_from_payload(snapshot.total_debt)
        summary = (
            "Key financial findings combine the structured financial snapshot with the feature-engine outputs so the memo can anchor "
            "the decision on validated turnover, profitability, liquidity, leverage, and document quality."
        )
        bullets = [
            f"Validated annual revenue anchor: {self._format_currency(annual_revenue, currency)} as of {snapshot.as_of_date or 'the latest available period'}.",
            f"EBITDA: {self._format_currency(ebitda, currency)}; total debt: {self._format_currency(total_debt, currency)}.",
            f"Liquidity band: {features.liquidity_leverage.liquidity_band}; current ratio proxy {self._format_ratio(features.liquidity_leverage.current_ratio_proxy)}.",
            f"Leverage band: {features.liquidity_leverage.leverage_band}; debt/EBITDA {self._format_multiple(features.liquidity_leverage.debt_to_ebitda_ratio)}.",
            f"Revenue inflation risk: {features.revenue_inflation.inflation_risk}; reported/GST ratio {self._format_ratio(features.revenue_inflation.reported_to_gst_ratio)}; reported/bank ratio {self._format_ratio(features.revenue_inflation.reported_to_bank_ratio)}.",
        ]
        evidence = [
            *self._financial_snapshot_refs(case),
            *self._feature_refs("turnover_revenue", features.turnover_revenue.evidence, case),
            *self._feature_refs("revenue_inflation", features.revenue_inflation.evidence, case),
            *self._feature_refs("liquidity_leverage", features.liquidity_leverage.evidence, case),
        ]
        return self._section("Key Financial Findings", None, summary, bullets, evidence)

    def _build_gst_bank_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        features = case.dossier.credit_features
        turnover = features.turnover_revenue
        consistency = features.gst_bank_consistency
        circular = features.circular_trading
        currency = self._case_currency(case)
        summary = (
            "GST and bank reconciliation findings highlight how closely reported turnover aligns with transactional cash flow, "
            "and whether the cash-flow shape raises circular-trading concerns."
        )
        bullets = [
            f"Annualized GST turnover: {self._format_currency(turnover.annualized_gst_turnover, currency)} versus annualized bank credits {self._format_currency(turnover.annualized_bank_credits, currency)}.",
            f"Consistency band: {consistency.consistency_band}; shared periods {len(consistency.overlap_periods)}; average gap {self._format_percent(consistency.average_gap_pct)}; max gap {self._format_percent(consistency.max_gap_pct)}.",
            f"Average GST-to-bank ratio: {self._format_ratio(consistency.average_gst_to_bank_ratio)}.",
            f"Circular-trading suspicion remains {circular.suspicion_level} with score {circular.suspicion_score:.2f}, same-day in/out ratio {self._format_ratio(circular.same_day_in_out_ratio)}, and top counterparty share {self._format_percent(circular.top_counterparty_share)}.",
        ]
        evidence = [
            *self._feature_refs("gst_bank_consistency", consistency.evidence, case),
            *self._feature_refs("circular_trading", circular.evidence, case),
            *self._feature_refs("turnover_revenue", turnover.evidence, case),
        ]
        return self._section("GST and Bank Reconciliation Findings", None, summary, bullets, evidence)

    def _build_research_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        research = case.dossier.secondary_research
        summary = (
            "Secondary research findings are organized into operating updates, sector context, and litigation or regulatory-style references "
            "so the memo can surface external flags alongside primary document analysis."
        )
        bullets: list[str] = []
        for finding in research.findings:
            descriptor = finding.summary or finding.message or f"{finding.topic} coverage was {finding.status}."
            if finding.risk_flags:
                descriptor += " Flags: " + ", ".join(flag.replace("_", " ") for flag in finding.risk_flags) + "."
            bullets.append(f"{finding.topic.replace('_', ' ').title()}: {descriptor}")
        if research.coverage_note:
            bullets.append(f"Coverage note: {research.coverage_note}")
        if not bullets:
            bullets.append("No secondary research findings were available at CAM generation time.")

        evidence = [
            *self._research_finding_refs(research.findings),
            *self._research_evidence_refs(research.evidence),
        ]
        return self._section(
            "Research Findings and Litigation or Regulatory Flags",
            research.status.title(),
            summary,
            bullets,
            evidence,
        )

    def _build_due_diligence_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        notes = case.dossier.qualitative_credit_officer_notes
        note_map = [
            ("Factory operating capacity", notes.factory_operating_capacity),
            ("Management quality", notes.management_quality),
            ("Governance concerns", notes.governance_concerns),
            ("Collateral observations", notes.collateral_observations),
            ("Site visit comments", notes.site_visit_comments),
            ("Additional comments", notes.additional_comments),
        ]
        bullets = [f"{label}: {value or 'Not captured.'}" for label, value in note_map]
        summary = (
            "Primary due-diligence notes capture the most recent on-ground and management observations that supplement the "
            "structured dossier and public-web research."
        )
        return self._section(
            "Primary Due-Diligence Notes",
            "Captured" if any(value for _, value in note_map) else "Limited",
            summary,
            bullets,
            self._note_refs(
                [
                    ("factory_operating_capacity", notes.factory_operating_capacity),
                    ("management_quality", notes.management_quality),
                    ("governance_concerns", notes.governance_concerns),
                    ("collateral_observations", notes.collateral_observations),
                    ("site_visit_comments", notes.site_visit_comments),
                    ("additional_comments", notes.additional_comments),
                ]
            ),
        )

    def _build_final_recommendation_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        recommendation = case.dossier.credit_recommendation
        assert recommendation is not None
        summary = recommendation.explanation.executive_summary
        bullets = [
            f"Decision: {recommendation.decision}. Overall risk score: {recommendation.overall_risk_score:.1f}/100 in the {recommendation.risk_band} band.",
            f"Recommended limit: {recommendation.recommended_loan_limit.currency} {recommendation.recommended_loan_limit.amount:,.2f}. Basis: {recommendation.recommended_loan_limit.basis}",
            f"Recommended risk premium: {recommendation.pricing.risk_premium_bps} bps. Pricing summary: {recommendation.pricing.summary}",
        ]
        for watchout in recommendation.explanation.watchouts:
            bullets.append(f"Watchout: {watchout}")

        evidence = [
            *self._driver_refs(recommendation.top_negative_drivers),
            *self._driver_refs(recommendation.top_positive_drivers),
        ]
        return self._section(
            "Final Recommendation",
            recommendation.decision.title(),
            summary,
            bullets,
            evidence,
        )

    def _build_decision_rationale_section(self, case: BorrowerCase) -> CreditAppraisalMemoSection:
        recommendation = case.dossier.credit_recommendation
        assert recommendation is not None
        positives = recommendation.top_positive_drivers
        negatives = recommendation.top_negative_drivers
        negative_labels = ", ".join(driver.label.lower() for driver in negatives) or "no material adverse drivers"
        positive_labels = ", ".join(driver.label.lower() for driver in positives) or "limited positive offset"
        summary = (
            f"The decision was reached because {negative_labels} outweighed {positive_labels}. "
            f"{recommendation.explanation.credit_officer_summary}"
        )
        bullets = [
            "Primary adverse drivers: "
            + (
                "; ".join(f"{driver.label}: {driver.rationale}" for driver in negatives)
                if negatives
                else "none."
            ),
            "Primary supportive drivers: "
            + (
                "; ".join(f"{driver.label}: {driver.rationale}" for driver in positives)
                if positives
                else "none."
            ),
            recommendation.explanation.judge_summary,
        ]
        evidence = self._driver_refs(recommendation.top_negative_drivers + recommendation.top_positive_drivers)
        return self._section("Decision Rationale", None, summary, bullets, evidence)

    def _financial_snapshot_refs(self, case: BorrowerCase, limit: int = 4) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for item in case.dossier.financial_snapshot.evidence[:limit]:
            document = self._document_by_id(case, item.get("document_id"))
            metric_name = item.get("metric", "financial metric").replace("_", " ")
            confidence = item.get("confidence")
            reference_parts = []
            if document is not None:
                reference_parts.append(f"Document: {document.filename}")
            if item.get("document_type"):
                reference_parts.append(f"Type: {item['document_type']}")
            if confidence is not None:
                reference_parts.append(f"Confidence: {confidence:.2f}")
            references.append(
                CamEvidenceReference(
                    label=f"{metric_name.title()} contributed to the financial snapshot.",
                    source_type="financial_snapshot",
                    reference="; ".join(reference_parts) or None,
                    source_path="dossier.financial_snapshot",
                    source_document_id=item.get("document_id"),
                    source_document_type=item.get("document_type"),
                )
            )
        return references

    def _feature_refs(
        self,
        feature_name: str,
        evidence_items: list[FeatureEvidence],
        case: BorrowerCase,
        limit: int = 4,
    ) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for item in evidence_items[:limit]:
            document = self._document_by_id(case, item.source_document_id)
            reference_parts = []
            if document is not None:
                reference_parts.append(f"Document: {document.filename}")
            if item.source_document_type:
                reference_parts.append(f"Type: {item.source_document_type}")
            if item.confidence is not None:
                reference_parts.append(f"Confidence: {item.confidence:.2f}")
            references.append(
                CamEvidenceReference(
                    label=item.message,
                    source_type="credit_feature",
                    reference="; ".join(reference_parts) or None,
                    source_path=f"dossier.credit_features.{feature_name}",
                    source_document_id=item.source_document_id,
                    source_document_type=item.source_document_type,
                )
            )
        return references

    def _research_finding_refs(
        self,
        findings: Iterable[SecondaryResearchFinding],
        limit: int = 5,
    ) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for finding in list(findings)[:limit]:
            reference_parts = []
            if finding.source_urls:
                reference_parts.append("Sources: " + ", ".join(finding.source_urls[:2]))
            if finding.risk_flags:
                reference_parts.append("Flags: " + ", ".join(finding.risk_flags))
            references.append(
                CamEvidenceReference(
                    label=f"{finding.topic.replace('_', ' ').title()}: {finding.summary or finding.message or finding.status}",
                    source_type="secondary_research",
                    reference="; ".join(reference_parts) or None,
                    source_url=finding.source_urls[0] if finding.source_urls else None,
                    source_path="dossier.secondary_research.findings[*]",
                )
            )
        return references

    def _research_evidence_refs(self, evidence_items, limit: int = 5) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for item in evidence_items[:limit]:
            reference_parts = []
            if item.source_title:
                reference_parts.append(f"Source title: {item.source_title}")
            if item.extracted_risk_flags:
                reference_parts.append("Flags: " + ", ".join(item.extracted_risk_flags))
            references.append(
                CamEvidenceReference(
                    label=item.title,
                    source_type=item.source_type,
                    reference="; ".join(reference_parts) or None,
                    source_url=item.source_url,
                    source_path="dossier.secondary_research.evidence[*]",
                )
            )
        return references

    def _note_refs(
        self,
        notes: Iterable[tuple[str, str | None]],
        limit: int = 4,
    ) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for field_name, value in list(notes)[:limit]:
            if not value:
                continue
            references.append(
                CamEvidenceReference(
                    label=value,
                    source_type="qualitative_note",
                    reference=f"Note field: {field_name.replace('_', ' ')}",
                    source_path=f"dossier.qualitative_credit_officer_notes.{field_name}",
                )
            )
        return references

    def _driver_refs(self, drivers: Iterable[ScoreDriver], limit: int = 6) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for driver in list(drivers):
            references.append(
                CamEvidenceReference(
                    label=f"{driver.label}: {driver.rationale}",
                    source_type="score_driver",
                    reference=f"Impact: {driver.impact_points:.1f} points; direction: {driver.direction}",
                    source_path=", ".join(driver.feature_refs) if driver.feature_refs else None,
                )
            )
            references.extend(self._score_evidence_refs(driver.evidence))
            if len(references) >= limit:
                break
        return references[:limit]

    def _score_evidence_refs(
        self,
        evidence_items: Iterable[ScoringEvidenceReference],
        limit: int = 3,
    ) -> list[CamEvidenceReference]:
        references: list[CamEvidenceReference] = []
        for item in list(evidence_items)[:limit]:
            source_url = item.metadata.get("source_url") if item.metadata else None
            source_title = item.metadata.get("source_title") if item.metadata else None
            reference_parts = []
            if source_title:
                reference_parts.append(f"Source title: {source_title}")
            if item.source_document_type:
                reference_parts.append(f"Document type: {item.source_document_type}")
            references.append(
                CamEvidenceReference(
                    label=item.message,
                    source_type=item.source_type,
                    reference="; ".join(reference_parts) or None,
                    source_url=source_url,
                    source_path=item.source_path,
                    source_document_id=item.source_document_id,
                    source_document_type=item.source_document_type,
                )
            )
        return references

    def _section(
        self,
        title: str,
        assessment: str | None,
        summary: str,
        bullet_points: list[str],
        evidence: list[CamEvidenceReference],
    ) -> CreditAppraisalMemoSection:
        cleaned_bullets = [bullet.strip() for bullet in bullet_points if bullet and bullet.strip()]
        return CreditAppraisalMemoSection(
            title=title,
            assessment=assessment,
            summary=summary.strip(),
            bullet_points=cleaned_bullets,
            evidence=self._dedupe_evidence(evidence),
        )

    def _dedupe_evidence(self, evidence: Iterable[CamEvidenceReference], limit: int = 6) -> list[CamEvidenceReference]:
        deduped: list[CamEvidenceReference] = []
        seen: set[tuple[str | None, ...]] = set()
        for item in evidence:
            key = (
                item.label,
                item.reference,
                item.source_url,
                item.source_path,
                item.source_document_id,
                item.source_document_type,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        return deduped

    def _document_by_id(self, case: BorrowerCase, document_id: str | None) -> ExtractedBorrowerDocument | None:
        if not document_id:
            return None
        for document in case.dossier.documents:
            if document.document_id == document_id:
                return document
        return None

    def _structured_flags(self, structured_documents: Iterable[StructuredDocumentRecord]) -> set[str]:
        return {flag for record in structured_documents for flag in record.flags}

    def _amount_from_payload(self, payload) -> float | None:
        if not payload:
            return None
        amount = payload.get("amount")
        return float(amount) if isinstance(amount, (int, float)) else None

    def _case_currency(self, case: BorrowerCase) -> str:
        recommendation = case.dossier.credit_recommendation
        return (
            case.dossier.credit_request.requested_amount.get("currency")
            or case.dossier.financial_snapshot.currency
            or (recommendation.recommended_loan_limit.currency if recommendation else None)
            or "INR"
        )

    def _format_currency(self, value: float | None, currency: str) -> str:
        if value is None:
            return "Not available"
        return f"{currency} {value:,.2f}"

    def _format_percent(self, value: float | None) -> str:
        if value is None:
            return "Not available"
        return f"{value:.2f}%"

    def _format_ratio(self, value: float | None) -> str:
        if value is None:
            return "Not available"
        return f"{value:.2f}x"

    def _format_multiple(self, value: float | None) -> str:
        return self._format_ratio(value)

    def _dedupe_strings(self, values: Iterable[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        return deduped
