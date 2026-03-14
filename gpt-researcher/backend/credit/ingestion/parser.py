from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from .config import load_parser_configs
from .models import StructuredDocumentRecord, StructuredMetric, StructuredObligation, StructuredSeriesPoint
from ..case_models import ExtractedBorrowerDocument, FinancialSnapshot


MONTH_PATTERN = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*[\s/-]+\d{2,4}"
PERIOD_PATTERN = re.compile(
    rf"\b({MONTH_PATTERN}|q[1-4][\s/-]*\d{{4}}|fy[\s-]*\d{{2,4}}(?:[-/]\d{{2,4}})?|"
    r"financial year[\s-]*\d{2,4}(?:[-/]\d{2,4})?|assessment year[\s-]*\d{2,4}(?:[-/]\d{2,4})?)\b",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(
    r"(?P<sign>-)?\s*(?P<currency>rs\.?|inr|usd|eur|gbp|aed|sgd|₹|\$)?\s*"
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>crore|cr|lakh|lac|million|mn|billion|bn|thousand|k|m)?",
    re.IGNORECASE,
)
PERCENT_PATTERN = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*%")


def normalize_token(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def parse_numeric_value(raw_value: str) -> float | None:
    match = NUMBER_PATTERN.search(raw_value)
    if match is None:
        return None

    number = float(match.group("number").replace(",", ""))
    unit = (match.group("unit") or "").lower()
    multiplier = 1.0
    if unit in {"crore", "cr"}:
        multiplier = 10_000_000.0
    elif unit in {"lakh", "lac"}:
        multiplier = 100_000.0
    elif unit in {"million", "mn", "m"}:
        multiplier = 1_000_000.0
    elif unit in {"billion", "bn"}:
        multiplier = 1_000_000_000.0
    elif unit in {"thousand", "k"}:
        multiplier = 1_000.0

    value = number * multiplier
    if match.group("sign"):
        value *= -1
    return value


def parse_percent_value(raw_value: str) -> float | None:
    match = PERCENT_PATTERN.search(raw_value)
    if match is not None:
        return float(match.group("number"))
    return parse_numeric_value(raw_value)


def parse_period_label(raw_value: str) -> str | None:
    match = PERIOD_PATTERN.search(raw_value)
    if match is not None:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    cleaned = raw_value.strip()
    return cleaned or None


def parse_value(raw_value: str, value_type: str) -> tuple[float | None, str | None]:
    cleaned = raw_value.strip(" :.-\t")
    if not cleaned:
        return None, None
    if value_type in {"amount", "number"}:
        return parse_numeric_value(cleaned), cleaned
    if value_type == "percent":
        return parse_percent_value(cleaned), cleaned
    if value_type == "period":
        return None, parse_period_label(cleaned)
    return None, cleaned


def flatten_table_rows(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_rows = table.get("rows")
        headers = table.get("headers")
        if isinstance(table_rows, list):
            for row in table_rows:
                if isinstance(row, dict):
                    rows.append(row)
                elif isinstance(row, list) and isinstance(headers, list):
                    rows.append(
                        {
                            str(header): row[index]
                            for index, header in enumerate(headers)
                            if index < len(row)
                        }
                    )
        else:
            scalar_row = {
                str(key): value
                for key, value in table.items()
                if key not in {"rows", "headers", "name", "title", "metadata"}
                and not isinstance(value, (list, dict))
            }
            if scalar_row:
                rows.append(scalar_row)
    return rows


def extract_text_lines(document: ExtractedBorrowerDocument) -> list[str]:
    lines = [line.strip() for line in document.extracted_text.splitlines() if line.strip()]
    for row in flatten_table_rows(document.tables):
        joined = " | ".join(f"{key}: {value}" for key, value in row.items())
        if joined:
            lines.append(joined)
    return lines


def extract_best_metric(
    document: ExtractedBorrowerDocument,
    rule: dict[str, Any],
    text_lines: list[str],
    table_rows: list[dict[str, Any]],
) -> StructuredMetric | None:
    aliases = [
        (alias, normalize_token(alias))
        for alias in sorted(rule.get("aliases", []), key=len, reverse=True)
    ]
    value_type = rule.get("value_type", "text")
    best_metric: StructuredMetric | None = None

    for row in table_rows:
        normalized_row = {normalize_token(str(key)): value for key, value in row.items()}
        for _, normalized_alias in aliases:
            if normalized_alias in normalized_row:
                numeric_value, text_value = parse_value(str(normalized_row[normalized_alias]), value_type)
                return StructuredMetric(
                    name=rule["name"],
                    value=numeric_value,
                    value_text=text_value if numeric_value is None else None,
                    unit=rule.get("unit"),
                    currency=rule.get("currency"),
                    confidence=0.9,
                    raw_value=str(normalized_row[normalized_alias]),
                    evidence=[f"table::{normalized_alias}={normalized_row[normalized_alias]}"],
                )

        label = normalize_token(str(row.get("metric") or row.get("label") or row.get("item") or ""))
        value = row.get("value") or row.get("amount") or row.get("figure")
        if label and value is not None and any(normalized_alias in label for _, normalized_alias in aliases):
            numeric_value, text_value = parse_value(str(value), value_type)
            return StructuredMetric(
                name=rule["name"],
                value=numeric_value,
                value_text=text_value if numeric_value is None else None,
                unit=rule.get("unit"),
                currency=rule.get("currency"),
                confidence=0.85,
                raw_value=str(value),
                evidence=[f"table::{label}={value}"],
            )

    for line in text_lines:
        normalized_line = normalize_token(line)
        for raw_alias, normalized_alias in aliases:
            if normalized_alias not in normalized_line:
                continue

            alias_pattern = re.escape(raw_alias).replace("\\ ", r"\s+")
            match = re.search(
                rf"{alias_pattern}\s*(?:[:=\-]|is|of|for)?\s*(?P<value>.+)$",
                line,
                re.IGNORECASE,
            )
            extracted_fragment = match.group("value") if match else line
            numeric_value, text_value = parse_value(extracted_fragment, value_type)
            if numeric_value is None and text_value is None:
                continue

            metric = StructuredMetric(
                name=rule["name"],
                value=numeric_value,
                value_text=text_value if numeric_value is None else None,
                unit=rule.get("unit"),
                currency=rule.get("currency"),
                confidence=0.72 if not document.placeholder else 0.56,
                raw_value=extracted_fragment,
                evidence=[line[:240]],
            )
            if best_metric is None or metric.confidence > best_metric.confidence:
                best_metric = metric

    return best_metric


def extract_series_points(
    document: ExtractedBorrowerDocument,
    rule: dict[str, Any],
    text_lines: list[str],
    table_rows: list[dict[str, Any]],
) -> list[StructuredSeriesPoint]:
    points: list[StructuredSeriesPoint] = []
    value_aliases = [
        (alias, normalize_token(alias))
        for alias in sorted(rule.get("value_aliases", []), key=len, reverse=True)
    ]
    label_aliases = [
        (alias, normalize_token(alias))
        for alias in sorted(rule.get("label_aliases", []), key=len, reverse=True)
    ]
    period_aliases = [normalize_token(alias) for alias in rule.get("period_aliases", [])]
    seen_keys: set[tuple[str, float]] = set()

    for row in table_rows:
        normalized_row = {normalize_token(str(key)): value for key, value in row.items()}
        period_value: str | None = None
        amount_value: float | None = None

        for key, value in normalized_row.items():
            if period_value is None and (
                key in period_aliases or key in {"period", "month", "year", "fy", "date"}
            ):
                period_value = parse_period_label(str(value))
            if amount_value is None and (
                key in {alias for _, alias in value_aliases}
                or key in {alias for _, alias in label_aliases}
                or key in {"value", "amount", "figure"}
            ):
                amount_value = parse_numeric_value(str(value))

        if period_value and amount_value is not None:
            dedupe_key = (period_value, amount_value)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            points.append(
                StructuredSeriesPoint(
                    period=period_value,
                    value=amount_value,
                    label=rule["name"],
                    confidence=0.86,
                    evidence=[
                        "table::"
                        + ", ".join(f"{key}={value}" for key, value in normalized_row.items())[:240]
                    ],
                )
            )

    for line in text_lines:
        normalized_line = normalize_token(line)
        if not any(normalized_alias in normalized_line for _, normalized_alias in label_aliases):
            continue

        period_match = PERIOD_PATTERN.search(line)
        if period_match is None:
            continue
        period = parse_period_label(period_match.group(0))
        amount: float | None = None

        for raw_alias, _ in value_aliases or label_aliases:
            alias_pattern = re.escape(raw_alias).replace("\\ ", r"\s+")
            match = re.search(
                rf"{alias_pattern}\s*(?:[:=\-]|for|of)?\s*(?P<value>.+)$",
                line,
                re.IGNORECASE,
            )
            if match:
                amount = parse_numeric_value(match.group("value"))
                if amount is not None:
                    break

        if period and amount is not None:
            dedupe_key = (period, amount)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            points.append(
                StructuredSeriesPoint(
                    period=period,
                    value=amount,
                    label=rule["name"],
                    confidence=0.68 if not document.placeholder else 0.54,
                    evidence=[line[:240]],
                )
            )

    return points


def calculate_document_match_score(
    document: ExtractedBorrowerDocument,
    config: dict[str, Any],
) -> float:
    score = 0.0
    normalized_filename = normalize_token(document.filename)
    normalized_category = normalize_token(document.category)
    normalized_text = normalize_token(document.extracted_text[:6000])

    if normalized_category in {normalize_token(value) for value in config.get("category_hints", [])}:
        score += 0.35

    filename_hits = sum(
        1 for keyword in config.get("filename_keywords", []) if normalize_token(keyword) in normalized_filename
    )
    text_hits = sum(
        1 for keyword in config.get("text_keywords", []) if normalize_token(keyword) in normalized_text
    )

    score += min(0.3, filename_hits * 0.1)
    score += min(0.4, text_hits * 0.08)
    return min(score, 1.0)


def create_obligations(record: StructuredDocumentRecord) -> list[StructuredObligation]:
    metric_map = {metric.name: metric for metric in record.metrics}
    obligations: list[StructuredObligation] = []

    if record.document_type == "sanction_letter" and "sanctioned_amount" in metric_map:
        sanctioned_amount = metric_map["sanctioned_amount"]
        obligations.append(
            StructuredObligation(
                obligation_type="sanctioned_facility",
                amount=sanctioned_amount.value,
                currency=sanctioned_amount.currency,
                status="active",
                confidence=sanctioned_amount.confidence,
                evidence=sanctioned_amount.evidence,
            )
        )

    if record.document_type == "legal_notice" and "claim_amount" in metric_map:
        claim_amount = metric_map["claim_amount"]
        obligations.append(
            StructuredObligation(
                obligation_type="claimed_outstanding",
                amount=claim_amount.value,
                currency=claim_amount.currency,
                due_date=metric_map.get("notice_date").value_text if "notice_date" in metric_map else None,
                status="disputed_or_due",
                confidence=claim_amount.confidence,
                evidence=claim_amount.evidence,
            )
        )

    return obligations


class ConfigDrivenStructuredParser:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return str(self._config["name"])

    def match_score(self, document: ExtractedBorrowerDocument) -> float:
        return calculate_document_match_score(document, self._config)

    def parse(self, document: ExtractedBorrowerDocument) -> StructuredDocumentRecord | None:
        detection_score = self.match_score(document)
        if detection_score < 0.2:
            return None

        text_lines = extract_text_lines(document)
        table_rows = flatten_table_rows(document.tables)
        metrics = [
            metric
            for rule in self._config.get("metric_rules", [])
            if (metric := extract_best_metric(document, rule, text_lines, table_rows)) is not None
        ]

        series = {
            rule["name"]: points
            for rule in self._config.get("series_rules", [])
            if (points := extract_series_points(document, rule, text_lines, table_rows))
        }

        flags: list[str] = []
        evidence: list[str] = []
        normalized_text = normalize_token(document.extracted_text)
        for rule in self._config.get("keyword_flags", []):
            if any(normalize_token(keyword) in normalized_text for keyword in rule.get("keywords", [])):
                flags.append(rule["flag"])
                evidence.append(f"flag::{rule['flag']}")

        evidence.extend(metric.evidence[0] for metric in metrics if metric.evidence)

        if not metrics and not series and not flags:
            return None

        confidence = detection_score
        confidence += min(0.36, len(metrics) * 0.08)
        confidence += min(0.22, sum(len(values) for values in series.values()) * 0.03)
        confidence += min(0.12, len(flags) * 0.04)
        if document.placeholder:
            confidence -= 0.14
        confidence = max(0.18, min(confidence, 0.96))

        assumptions = [
            "Config-driven alias and regex rules were used because exact lender and tax statement templates were not guaranteed.",
        ]
        if document.placeholder:
            assumptions.append(
                "Source extraction carried placeholder or fallback text, so structured values may be incomplete."
            )

        record = StructuredDocumentRecord(
            record_id=f"structured_{uuid4().hex[:10]}",
            source_document_id=document.document_id,
            source_filename=document.filename,
            source_category=document.category,
            document_type=str(self._config["document_type"]),
            parser_name=self.name,
            confidence=round(confidence, 4),
            metrics=metrics,
            series=series,
            flags=flags,
            evidence=list(dict.fromkeys(evidence))[:12],
            assumptions=assumptions,
        )
        record.obligations = create_obligations(record)
        return record


class StructuredDocumentInterpreter:
    def __init__(self, override_path: str | None = None) -> None:
        self._parsers = [
            ConfigDrivenStructuredParser(config) for config in load_parser_configs(override_path)
        ]

    def parse_documents(
        self,
        documents: list[ExtractedBorrowerDocument],
    ) -> list[StructuredDocumentRecord]:
        structured_documents: list[StructuredDocumentRecord] = []
        for document in documents:
            parser_matches = [(parser.match_score(document), parser) for parser in self._parsers]
            parser_matches = [match for match in parser_matches if match[0] >= 0.2]
            if not parser_matches:
                continue

            parser_matches.sort(key=lambda item: item[0], reverse=True)
            record = parser_matches[0][1].parse(document)
            if record is not None:
                structured_documents.append(record)
        return structured_documents


def _period_sort_key(period: str) -> tuple[int, int, int]:
    cleaned = period.strip().lower()
    month_match = re.search(MONTH_PATTERN, cleaned, re.IGNORECASE)
    if month_match:
        token = month_match.group(0).replace("/", " ").replace("-", " ")
        try:
            date = datetime.strptime(re.sub(r"\s+", " ", token).title(), "%b %Y")
            return (date.year, date.month, 1)
        except Exception:
            try:
                date = datetime.strptime(re.sub(r"\s+", " ", token).title(), "%B %Y")
                return (date.year, date.month, 1)
            except Exception:
                pass

    fy_match = re.search(r"fy\s*(\d{2,4})(?:[-/](\d{2,4}))?", cleaned, re.IGNORECASE)
    if fy_match:
        start_year = int(fy_match.group(1))
        if start_year < 100:
            start_year += 2000
        return (start_year, 12, 1)

    year_match = re.search(r"(\d{4})", cleaned)
    if year_match:
        return (int(year_match.group(1)), 12, 1)

    return (0, 0, 0)


def build_financial_snapshot(
    structured_documents: list[StructuredDocumentRecord],
) -> FinancialSnapshot:
    metric_candidates: dict[str, tuple[StructuredDocumentRecord, StructuredMetric]] = {}

    for record in structured_documents:
        for metric in record.metrics:
            if metric.value is None:
                continue
            current = metric_candidates.get(metric.name)
            if current is None or metric.confidence > current[1].confidence:
                metric_candidates[metric.name] = (record, metric)

    snapshot = FinancialSnapshot()
    evidence: list[dict[str, Any]] = []

    def metric_payload(metric_name: str) -> dict[str, Any]:
        if metric_name not in metric_candidates:
            return {}
        record, metric = metric_candidates[metric_name]
        evidence.append(
            {
                "document_id": record.source_document_id,
                "document_type": record.document_type,
                "metric": metric_name,
                "confidence": metric.confidence,
            }
        )
        payload = {
            "amount": metric.value,
            "currency": metric.currency,
            "source_document_id": record.source_document_id,
            "document_type": record.document_type,
            "confidence": metric.confidence,
        }
        if metric.period:
            payload["period"] = metric.period
        return payload

    annual_revenue = (
        metric_payload("revenue_from_operations")
        or metric_payload("gross_turnover")
        or metric_payload("taxable_turnover")
    )
    total_debt = metric_payload("total_debt") or metric_payload("sanctioned_amount")
    ebitda = metric_payload("ebitda")
    net_income = metric_payload("net_income") or metric_payload("net_profit")
    current_assets = metric_payload("current_assets")
    current_liabilities = metric_payload("current_liabilities")
    average_balance = metric_payload("average_monthly_balance")

    snapshot.annual_revenue = annual_revenue
    snapshot.total_debt = total_debt
    snapshot.ebitda = ebitda
    snapshot.net_income = net_income
    if annual_revenue:
        snapshot.currency = annual_revenue.get("currency")

    liquidity: dict[str, Any] = {}
    if average_balance:
        liquidity["average_monthly_balance"] = average_balance
    if current_assets and current_liabilities:
        liabilities_amount = current_liabilities.get("amount")
        assets_amount = current_assets.get("amount")
        if liabilities_amount and assets_amount is not None:
            liquidity["current_ratio_proxy"] = round(assets_amount / liabilities_amount, 4)
    snapshot.liquidity = liquidity
    snapshot.evidence = evidence

    revenue_points: list[tuple[StructuredDocumentRecord, StructuredSeriesPoint]] = []
    for record in structured_documents:
        for series_name in ("annual_revenue", "annual_turnover"):
            for point in record.series.get(series_name, []):
                revenue_points.append((record, point))

    if revenue_points:
        revenue_points.sort(key=lambda item: _period_sort_key(item[1].period))
        latest_record, latest_point = revenue_points[-1]
        snapshot.as_of_date = latest_point.period
        if not snapshot.annual_revenue:
            snapshot.annual_revenue = {
                "amount": latest_point.value,
                "currency": "INR",
                "source_document_id": latest_record.source_document_id,
                "document_type": latest_record.document_type,
                "confidence": latest_point.confidence,
                "period": latest_point.period,
            }

    return snapshot
