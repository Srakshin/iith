from __future__ import annotations

import re
from statistics import mean

from .models import (
    CircularTradingFeatures,
    CreditFeatureBundle,
    DocumentQualityFeatures,
    FeatureEvidence,
    FeatureSeriesPoint,
    GstBankConsistencyFeatures,
    LiquidityLeverageFeatures,
    RevenueInflationFeatures,
    TurnoverRevenueFeatures,
)
from ..ingestion.models import StructuredDocumentRecord, StructuredMetric, StructuredSeriesPoint
from ..case_models import BorrowerCase, utc_now


def _period_sort_key(period: str) -> tuple[int, int]:
    cleaned = period.strip().lower()
    month_match = re.search(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s/-]+(\d{2,4})", cleaned)
    if month_match:
        month_lookup = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "sept": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        month_key = month_match.group(1)[:4]
        if month_key == "sept":
            month_key = "sept"
        else:
            month_key = month_key[:3]
        year = int(month_match.group(2))
        if year < 100:
            year += 2000
        return (year, month_lookup[month_key])

    quarter_match = re.search(r"q([1-4])[\s/-]*(\d{4})", cleaned)
    if quarter_match:
        return (int(quarter_match.group(2)), int(quarter_match.group(1)) * 3)

    year_match = re.search(r"(\d{4})", cleaned)
    if year_match:
        return (int(year_match.group(1)), 12)

    return (0, 0)


def _iter_metrics(
    structured_documents: list[StructuredDocumentRecord],
    metric_names: set[str],
    document_types: set[str] | None = None,
) -> list[tuple[StructuredDocumentRecord, StructuredMetric]]:
    matches: list[tuple[StructuredDocumentRecord, StructuredMetric]] = []
    for record in structured_documents:
        if document_types is not None and record.document_type not in document_types:
            continue
        for metric in record.metrics:
            if metric.name in metric_names and metric.value is not None:
                matches.append((record, metric))
    matches.sort(key=lambda item: item[1].confidence, reverse=True)
    return matches


def _iter_series(
    structured_documents: list[StructuredDocumentRecord],
    series_names: set[str],
    document_types: set[str] | None = None,
) -> list[tuple[StructuredDocumentRecord, str, StructuredSeriesPoint]]:
    matches: list[tuple[StructuredDocumentRecord, str, StructuredSeriesPoint]] = []
    for record in structured_documents:
        if document_types is not None and record.document_type not in document_types:
            continue
        for series_name, points in record.series.items():
            if series_name not in series_names:
                continue
            for point in points:
                matches.append((record, series_name, point))
    matches.sort(key=lambda item: _period_sort_key(item[2].period))
    return matches


def _build_evidence(
    record: StructuredDocumentRecord,
    message: str,
    confidence: float | None = None,
) -> FeatureEvidence:
    return FeatureEvidence(
        source_document_id=record.source_document_id,
        source_document_type=record.document_type,
        message=message,
        confidence=confidence,
    )


class CreditFeatureEngine:
    def compute(
        self,
        case: BorrowerCase,
        structured_documents: list[StructuredDocumentRecord],
    ) -> CreditFeatureBundle:
        bundle = CreditFeatureBundle(
            computed_at=utc_now(),
            assumptions=[
                "Credit features are deterministic heuristics from parsed document signals, not a learned score.",
                "Where exact source templates were missing, parser aliases and synthetic-format assumptions were used.",
            ],
        )
        bundle.document_quality = self._document_quality(case, structured_documents)
        bundle.turnover_revenue = self._turnover_revenue(structured_documents)
        bundle.gst_bank_consistency = self._gst_bank_consistency(structured_documents)
        bundle.circular_trading = self._circular_trading(structured_documents)
        bundle.revenue_inflation = self._revenue_inflation(structured_documents, bundle.turnover_revenue)
        bundle.liquidity_leverage = self._liquidity_leverage(structured_documents)
        return bundle

    def _document_quality(
        self,
        case: BorrowerCase,
        structured_documents: list[StructuredDocumentRecord],
    ) -> DocumentQualityFeatures:
        present_types = sorted({record.document_type for record in structured_documents})
        required_types = ["gst_return", "bank_statement", "itr", "financial_statement"]
        missing_types = [doc_type for doc_type in required_types if doc_type not in present_types]
        placeholder_count = sum(1 for document in case.dossier.documents if document.placeholder)
        avg_confidence = mean([record.confidence for record in structured_documents]) if structured_documents else 0.0

        completeness_score = 1.0
        completeness_score -= min(0.6, len(missing_types) * 0.15)
        if case.dossier.documents:
            completeness_score -= min(0.35, placeholder_count / len(case.dossier.documents) * 0.35)
        completeness_score = max(0.0, min(completeness_score, 1.0))

        evidence = [
            FeatureEvidence(message=f"Parsed document types: {', '.join(present_types) or 'none'}"),
        ]
        if missing_types:
            evidence.append(FeatureEvidence(message=f"Missing core documents: {', '.join(missing_types)}"))

        return DocumentQualityFeatures(
            completeness_score=round(completeness_score, 4),
            extraction_confidence=round(avg_confidence, 4),
            supported_document_types=present_types,
            missing_core_documents=missing_types,
            placeholder_documents=placeholder_count,
            parsed_structured_documents=len(structured_documents),
            evidence=evidence,
        )

    def _turnover_revenue(
        self,
        structured_documents: list[StructuredDocumentRecord],
    ) -> TurnoverRevenueFeatures:
        reported_revenue_metrics = _iter_metrics(
            structured_documents,
            {"revenue_from_operations", "gross_turnover", "total_income"},
            {"financial_statement", "itr"},
        )
        fallback_revenue_metrics = _iter_metrics(
            structured_documents,
            {"taxable_turnover"},
            {"gst_return"},
        )
        gst_monthly = _iter_series(structured_documents, {"monthly_taxable_turnover"}, {"gst_return"})
        bank_monthly = _iter_series(structured_documents, {"monthly_credits"}, {"bank_statement"})

        result = TurnoverRevenueFeatures()
        if not reported_revenue_metrics and not fallback_revenue_metrics and not gst_monthly and not bank_monthly:
            return result

        result.available = True
        revenue_metrics = reported_revenue_metrics or fallback_revenue_metrics
        if revenue_metrics:
            result.latest_annual_revenue = round(revenue_metrics[0][1].value or 0.0, 2)
            result.evidence.append(
                _build_evidence(
                    revenue_metrics[0][0],
                    f"Reported revenue signal from {revenue_metrics[0][1].name}",
                    revenue_metrics[0][1].confidence,
                )
            )

        if gst_monthly:
            gst_values = [point.value for _, _, point in gst_monthly]
            result.annualized_gst_turnover = round(sum(gst_values) / len(gst_values) * 12, 2)
            result.monthly_turnover_series = [
                FeatureSeriesPoint(
                    period=point.period,
                    value=point.value,
                    source_document_id=record.source_document_id,
                )
                for record, _, point in gst_monthly
            ]
            result.evidence.append(
                _build_evidence(
                    gst_monthly[-1][0],
                    f"GST turnover series spans {len(gst_monthly)} periods",
                    gst_monthly[-1][2].confidence,
                )
            )

        if bank_monthly:
            bank_values = [point.value for _, _, point in bank_monthly]
            result.annualized_bank_credits = round(sum(bank_values) / len(bank_values) * 12, 2)

        trend_points = gst_monthly or bank_monthly
        if len(trend_points) >= 2:
            first_value = trend_points[0][2].value
            last_value = trend_points[-1][2].value
            if first_value:
                growth = (last_value - first_value) / first_value * 100
                result.revenue_growth_pct = round(growth, 2)
                if growth > 10:
                    result.turnover_trend = "increasing"
                elif growth < -10:
                    result.turnover_trend = "declining"
                else:
                    result.turnover_trend = "stable"
            values = [point.value for _, _, point in trend_points]
            if min(values) > 0 and max(values) / min(values) > 1.6:
                result.seasonality_flag = True

        return result

    def _gst_bank_consistency(
        self,
        structured_documents: list[StructuredDocumentRecord],
    ) -> GstBankConsistencyFeatures:
        gst_points = {
            point.period.lower(): (record, point)
            for record, _, point in _iter_series(
                structured_documents,
                {"monthly_taxable_turnover"},
                {"gst_return"},
            )
        }
        bank_points = {
            point.period.lower(): (record, point)
            for record, _, point in _iter_series(
                structured_documents,
                {"monthly_credits"},
                {"bank_statement"},
            )
        }

        overlap = sorted(set(gst_points) & set(bank_points), key=_period_sort_key)
        result = GstBankConsistencyFeatures(
            overlap_periods=[gst_points[period][1].period for period in overlap]
        )
        if not overlap:
            return result

        gaps: list[float] = []
        ratios: list[float] = []
        for period in overlap:
            gst_record, gst_point = gst_points[period]
            bank_record, bank_point = bank_points[period]
            gap_pct = abs(gst_point.value - bank_point.value) / max(gst_point.value, bank_point.value)
            ratio = gst_point.value / bank_point.value if bank_point.value else 0.0
            gaps.append(gap_pct)
            ratios.append(ratio)
            result.evidence.append(
                _build_evidence(
                    gst_record,
                    f"{gst_point.period}: GST {gst_point.value:.0f} vs bank credits {bank_point.value:.0f}",
                    gst_point.confidence,
                )
            )
            result.evidence.append(
                _build_evidence(
                    bank_record,
                    f"{bank_point.period}: bank credits used for GST consistency comparison",
                    bank_point.confidence,
                )
            )

        result.available = True
        result.average_gap_pct = round(mean(gaps) * 100, 2)
        result.max_gap_pct = round(max(gaps) * 100, 2)
        result.average_gst_to_bank_ratio = round(mean(ratios), 4)
        if result.average_gap_pct <= 15:
            result.consistency_band = "aligned"
        elif result.average_gap_pct <= 30:
            result.consistency_band = "moderate_variance"
        else:
            result.consistency_band = "high_variance"
        return result

    def _circular_trading(
        self,
        structured_documents: list[StructuredDocumentRecord],
    ) -> CircularTradingFeatures:
        metrics = {
            metric_name: _iter_metrics(structured_documents, {metric_name}, {"bank_statement"})
            for metric_name in {
                "total_credits",
                "same_day_in_out_total",
                "round_amount_credits",
                "cash_deposit_total",
                "top_counterparty_share",
                "cheque_return_count",
            }
        }
        result = CircularTradingFeatures()
        if not metrics["total_credits"]:
            return result

        total_credits_record, total_credits_metric = metrics["total_credits"][0]
        total_credits = total_credits_metric.value or 0.0
        if not total_credits:
            return result

        triggered: list[str] = []
        score_components: list[float] = []

        if metrics["same_day_in_out_total"]:
            value = (metrics["same_day_in_out_total"][0][1].value or 0.0) / total_credits
            result.same_day_in_out_ratio = round(value, 4)
            score_components.append(min(value / 0.4, 1.0))
            if value >= 0.35:
                triggered.append("same_day_in_out_ratio_high")

        if metrics["round_amount_credits"]:
            value = (metrics["round_amount_credits"][0][1].value or 0.0) / total_credits
            result.round_amount_credit_ratio = round(value, 4)
            score_components.append(min(value / 0.3, 1.0))
            if value >= 0.25:
                triggered.append("round_amount_credits_high")

        if metrics["cash_deposit_total"]:
            value = (metrics["cash_deposit_total"][0][1].value or 0.0) / total_credits
            result.cash_deposit_ratio = round(value, 4)
            score_components.append(min(value / 0.25, 1.0))
            if value >= 0.2:
                triggered.append("cash_deposit_ratio_high")

        if metrics["top_counterparty_share"]:
            value = metrics["top_counterparty_share"][0][1].value or 0.0
            result.top_counterparty_share = round(value, 2)
            score_components.append(min(value / 70.0, 1.0))
            if value >= 45:
                triggered.append("counterparty_concentration_high")

        if metrics["cheque_return_count"]:
            cheque_returns = metrics["cheque_return_count"][0][1].value or 0.0
            score_components.append(min(cheque_returns / 5.0, 1.0))
            if cheque_returns >= 3:
                triggered.append("cheque_returns_multiple")

        result.suspicion_score = round(mean(score_components) if score_components else 0.0, 4)
        result.triggered_rules = triggered
        if result.suspicion_score >= 0.65 or len(triggered) >= 3:
            result.suspicion_level = "high"
        elif result.suspicion_score >= 0.35 or len(triggered) >= 2:
            result.suspicion_level = "medium"

        result.evidence.append(
            _build_evidence(
                total_credits_record,
                f"Bank turnover base used for circular trading heuristics: {total_credits:.0f}",
                total_credits_metric.confidence,
            )
        )
        return result

    def _revenue_inflation(
        self,
        structured_documents: list[StructuredDocumentRecord],
        turnover_features: TurnoverRevenueFeatures,
    ) -> RevenueInflationFeatures:
        reported = _iter_metrics(
            structured_documents,
            {"revenue_from_operations", "gross_turnover", "total_income"},
            {"financial_statement", "itr"},
        )
        result = RevenueInflationFeatures()
        if not reported:
            return result

        result.available = True
        reported_record, reported_metric = reported[0]
        result.reported_revenue = round(reported_metric.value or 0.0, 2)
        result.evidence.append(
            _build_evidence(
                reported_record,
                f"Reported revenue from {reported_metric.name}",
                reported_metric.confidence,
            )
        )

        if turnover_features.annualized_gst_turnover:
            result.gst_reference_revenue = turnover_features.annualized_gst_turnover
            result.reported_to_gst_ratio = round(
                result.reported_revenue / turnover_features.annualized_gst_turnover,
                4,
            )
        if turnover_features.annualized_bank_credits:
            result.bank_reference_revenue = turnover_features.annualized_bank_credits
            result.reported_to_bank_ratio = round(
                result.reported_revenue / turnover_features.annualized_bank_credits,
                4,
            )

        ratios = [
            ratio
            for ratio in [result.reported_to_gst_ratio, result.reported_to_bank_ratio]
            if ratio is not None
        ]
        if not ratios:
            return result

        max_ratio = max(ratios)
        if max_ratio > 1.4:
            result.inflation_risk = "high"
        elif max_ratio > 1.2:
            result.inflation_risk = "medium"
        else:
            result.inflation_risk = "low"
        return result

    def _liquidity_leverage(
        self,
        structured_documents: list[StructuredDocumentRecord],
    ) -> LiquidityLeverageFeatures:
        result = LiquidityLeverageFeatures()

        current_assets = _iter_metrics(structured_documents, {"current_assets"}, {"financial_statement"})
        current_liabilities = _iter_metrics(structured_documents, {"current_liabilities"}, {"financial_statement"})
        average_balance = _iter_metrics(structured_documents, {"average_monthly_balance"}, {"bank_statement"})
        monthly_debits = _iter_series(structured_documents, {"monthly_debits"}, {"bank_statement"})
        total_debt = _iter_metrics(
            structured_documents,
            {"total_debt", "sanctioned_amount"},
            {"financial_statement", "sanction_letter"},
        )
        revenue = _iter_metrics(
            structured_documents,
            {"revenue_from_operations", "gross_turnover"},
            {"financial_statement", "itr"},
        )
        ebitda = _iter_metrics(structured_documents, {"ebitda"}, {"financial_statement"})

        if any([current_assets, current_liabilities, average_balance, total_debt]):
            result.available = True

        if current_assets and current_liabilities and (current_liabilities[0][1].value or 0.0):
            ratio = (current_assets[0][1].value or 0.0) / (current_liabilities[0][1].value or 1.0)
            result.current_ratio_proxy = round(ratio, 4)
            result.evidence.append(
                _build_evidence(
                    current_assets[0][0],
                    f"Current assets/current liabilities proxy = {ratio:.2f}",
                    current_assets[0][1].confidence,
                )
            )

        if average_balance:
            result.average_monthly_balance = round(average_balance[0][1].value or 0.0, 2)
            result.evidence.append(
                _build_evidence(
                    average_balance[0][0],
                    "Average monthly balance parsed from bank statement",
                    average_balance[0][1].confidence,
                )
            )

        if monthly_debits:
            debit_values = [point.value for _, _, point in monthly_debits]
            result.average_monthly_debits = round(mean(debit_values), 2)

        if total_debt:
            result.total_debt = round(total_debt[0][1].value or 0.0, 2)

        if result.total_debt and revenue and (revenue[0][1].value or 0.0):
            result.debt_to_revenue_ratio = round(
                result.total_debt / (revenue[0][1].value or 1.0),
                4,
            )

        if result.total_debt and ebitda and (ebitda[0][1].value or 0.0):
            result.debt_to_ebitda_ratio = round(
                result.total_debt / (ebitda[0][1].value or 1.0),
                4,
            )

        if result.current_ratio_proxy is not None:
            if result.current_ratio_proxy >= 1.25:
                result.liquidity_band = "healthy"
            elif result.current_ratio_proxy >= 1.0:
                result.liquidity_band = "watch"
            else:
                result.liquidity_band = "stretched"

        if result.debt_to_revenue_ratio is not None:
            if result.debt_to_revenue_ratio >= 1.0:
                result.leverage_band = "high"
            elif result.debt_to_revenue_ratio >= 0.5:
                result.leverage_band = "moderate"
            else:
                result.leverage_band = "manageable"

        return result
