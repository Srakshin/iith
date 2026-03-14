from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_PARSER_CONFIGS: list[dict[str, Any]] = [
    {
        "name": "gst_returns",
        "document_type": "gst_return",
        "category_hints": ["gst_return", "tax_document"],
        "filename_keywords": ["gst", "gstr", "tax"],
        "text_keywords": [
            "gstin",
            "gstr-1",
            "gstr 1",
            "gstr-3b",
            "outward taxable supplies",
            "taxable turnover",
            "gross turnover",
        ],
        "metric_rules": [
            {"name": "gstin", "aliases": ["gstin"], "value_type": "text"},
            {
                "name": "return_period",
                "aliases": ["return period", "filing period", "tax period"],
                "value_type": "text",
            },
            {
                "name": "taxable_turnover",
                "aliases": [
                    "taxable turnover",
                    "gross turnover",
                    "outward taxable supplies",
                    "taxable value",
                ],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "output_tax",
                "aliases": ["output tax", "gst payable", "igst", "cgst", "sgst"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "nil_rated_supplies",
                "aliases": ["nil rated supplies", "exempt supplies"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "filing_delay_days",
                "aliases": ["delay days", "days delayed", "filing delay"],
                "value_type": "number",
            },
        ],
        "series_rules": [
            {
                "name": "monthly_taxable_turnover",
                "label_aliases": ["turnover", "sales", "outward supplies", "taxable turnover"],
                "period_aliases": ["month", "period"],
                "value_aliases": ["turnover", "sales", "taxable turnover", "outward supplies"],
                "value_type": "amount",
                "currency": "INR",
            }
        ],
        "keyword_flags": [
            {
                "flag": "gst_filing_irregularity",
                "keywords": ["not filed", "filing pending", "late fee", "delay in filing"],
            }
        ],
    },
    {
        "name": "bank_statements",
        "document_type": "bank_statement",
        "category_hints": ["bank_statement"],
        "filename_keywords": ["bank", "statement", "account statement", "cc account"],
        "text_keywords": [
            "opening balance",
            "closing balance",
            "total credits",
            "total debits",
            "avg monthly balance",
        ],
        "metric_rules": [
            {"name": "bank_name", "aliases": ["bank name"], "value_type": "text"},
            {
                "name": "account_number",
                "aliases": ["account number", "a/c no", "account no"],
                "value_type": "text",
            },
            {
                "name": "statement_period",
                "aliases": ["statement period", "period covered", "date range"],
                "value_type": "text",
            },
            {
                "name": "total_credits",
                "aliases": ["total credits", "credit turnover", "total deposits"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "total_debits",
                "aliases": ["total debits", "debit turnover", "total withdrawals"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "average_monthly_balance",
                "aliases": ["avg monthly balance", "average monthly balance", "average balance"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "cash_deposit_total",
                "aliases": ["cash deposits", "cash deposit total", "cash credits"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "same_day_in_out_total",
                "aliases": ["same day in-out", "same day in out", "same day reversals"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "round_amount_credits",
                "aliases": ["round amount credits", "round figure credits"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "top_counterparty_share",
                "aliases": ["top counterparty share", "largest counterparty share"],
                "value_type": "percent",
            },
            {
                "name": "cheque_return_count",
                "aliases": ["cheque returns", "return count", "bounced cheques"],
                "value_type": "number",
            },
        ],
        "series_rules": [
            {
                "name": "monthly_credits",
                "label_aliases": ["credits", "credit turnover", "deposits"],
                "period_aliases": ["month", "period"],
                "value_aliases": ["credits", "deposits"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "monthly_debits",
                "label_aliases": ["debits", "debit turnover", "withdrawals"],
                "period_aliases": ["month", "period"],
                "value_aliases": ["debits", "withdrawals"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "monthly_closing_balance",
                "label_aliases": ["closing balance", "balance"],
                "period_aliases": ["month", "period"],
                "value_aliases": ["closing balance", "balance"],
                "value_type": "amount",
                "currency": "INR",
            },
        ],
        "keyword_flags": [
            {
                "flag": "bank_stress_signal",
                "keywords": ["overdrawn", "cheque return", "insufficient funds", "dp shortfall"],
            }
        ],
    },
    {
        "name": "itr_documents",
        "document_type": "itr",
        "category_hints": ["tax_document"],
        "filename_keywords": ["itr", "income tax return"],
        "text_keywords": ["assessment year", "gross total income", "turnover", "business income"],
        "metric_rules": [
            {
                "name": "assessment_year",
                "aliases": ["assessment year", "ay"],
                "value_type": "text",
            },
            {
                "name": "gross_turnover",
                "aliases": ["gross turnover", "turnover", "sales turnover"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "total_income",
                "aliases": ["total income", "gross total income"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "net_profit",
                "aliases": ["net profit", "profit after tax", "pat"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "tax_paid",
                "aliases": ["tax paid", "self assessment tax", "total tax paid"],
                "value_type": "amount",
                "currency": "INR",
            },
        ],
        "series_rules": [
            {
                "name": "annual_turnover",
                "label_aliases": ["turnover", "gross turnover", "sales"],
                "period_aliases": ["year", "assessment year", "financial year"],
                "value_aliases": ["turnover", "sales"],
                "value_type": "amount",
                "currency": "INR",
            }
        ],
        "keyword_flags": [],
    },
    {
        "name": "financial_statements",
        "document_type": "financial_statement",
        "category_hints": ["financial_statement", "annual_report"],
        "filename_keywords": ["financial", "p&l", "balance", "profit", "loss", "annual report"],
        "text_keywords": [
            "revenue from operations",
            "total income",
            "ebitda",
            "current assets",
            "current liabilities",
        ],
        "metric_rules": [
            {
                "name": "revenue_from_operations",
                "aliases": ["revenue from operations", "operating revenue", "revenue"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "ebitda",
                "aliases": ["ebitda", "operating profit"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "net_income",
                "aliases": ["net income", "profit after tax", "pat"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "total_debt",
                "aliases": ["total debt", "borrowings", "total borrowings"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "current_assets",
                "aliases": ["current assets"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "current_liabilities",
                "aliases": ["current liabilities"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "inventory",
                "aliases": ["inventory", "stock in trade"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "trade_receivables",
                "aliases": ["trade receivables", "sundry debtors", "receivables"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "finance_cost",
                "aliases": ["finance cost", "interest expense"],
                "value_type": "amount",
                "currency": "INR",
            },
        ],
        "series_rules": [
            {
                "name": "annual_revenue",
                "label_aliases": ["revenue", "revenue from operations", "sales"],
                "period_aliases": ["year", "fy", "financial year"],
                "value_aliases": ["revenue", "sales", "revenue from operations"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "annual_ebitda",
                "label_aliases": ["ebitda", "operating profit"],
                "period_aliases": ["year", "fy", "financial year"],
                "value_aliases": ["ebitda", "operating profit"],
                "value_type": "amount",
                "currency": "INR",
            },
        ],
        "keyword_flags": [
            {
                "flag": "auditor_qualification",
                "keywords": ["qualified opinion", "emphasis of matter", "material uncertainty"],
            }
        ],
    },
    {
        "name": "annual_reports",
        "document_type": "annual_report",
        "category_hints": ["annual_report", "general_document"],
        "filename_keywords": ["annual report", "board report"],
        "text_keywords": ["board's report", "management discussion", "contingent liabilities"],
        "metric_rules": [
            {
                "name": "contingent_liabilities",
                "aliases": ["contingent liabilities", "contingent liability"],
                "value_type": "amount",
                "currency": "INR",
            }
        ],
        "series_rules": [],
        "keyword_flags": [
            {"flag": "related_party_signal", "keywords": ["related party", "group company"]},
            {
                "flag": "statutory_dues_delay",
                "keywords": ["dues outstanding", "disputed statutory dues"],
            },
            {
                "flag": "auditor_qualification",
                "keywords": ["qualified opinion", "emphasis of matter"],
            },
        ],
    },
    {
        "name": "sanction_letters",
        "document_type": "sanction_letter",
        "category_hints": ["sanction_letter", "general_document"],
        "filename_keywords": ["sanction", "facility letter", "loan sanction"],
        "text_keywords": ["sanctioned amount", "facility", "interest rate", "tenor", "security"],
        "metric_rules": [
            {
                "name": "sanctioned_amount",
                "aliases": ["sanctioned amount", "facility amount", "limit"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "interest_rate",
                "aliases": ["interest rate", "roi", "rate of interest"],
                "value_type": "percent",
            },
            {
                "name": "tenor_months",
                "aliases": ["tenor", "repayment tenor", "tenure"],
                "value_type": "number",
            },
            {"name": "lender_name", "aliases": ["lender", "sanctioned by"], "value_type": "text"},
            {
                "name": "facility_type",
                "aliases": ["facility", "facility type", "product"],
                "value_type": "text",
            },
            {"name": "security_summary", "aliases": ["security", "collateral"], "value_type": "text"},
        ],
        "series_rules": [],
        "keyword_flags": [
            {
                "flag": "tight_covenant_signal",
                "keywords": ["escrow", "personal guarantee", "drawing power", "stock statement"],
            }
        ],
    },
    {
        "name": "legal_notices",
        "document_type": "legal_notice",
        "category_hints": ["legal_notice", "general_document"],
        "filename_keywords": ["legal notice", "demand notice", "sarfaesi", "recovery notice"],
        "text_keywords": ["outstanding amount", "default", "due within", "notice"],
        "metric_rules": [
            {
                "name": "claim_amount",
                "aliases": ["claim amount", "outstanding amount", "amount due"],
                "value_type": "amount",
                "currency": "INR",
            },
            {
                "name": "days_to_cure",
                "aliases": ["within days", "days to cure", "pay within"],
                "value_type": "number",
            },
            {"name": "issuer_name", "aliases": ["issued by", "issuer", "notice from"], "value_type": "text"},
            {"name": "notice_date", "aliases": ["notice date", "dated"], "value_type": "text"},
        ],
        "series_rules": [],
        "keyword_flags": [
            {"flag": "default_notice", "keywords": ["default", "overdue", "wilful defaulter"]},
            {
                "flag": "recovery_action",
                "keywords": ["section 13(2)", "sarfaesi", "arbitration", "legal proceedings"],
            },
        ],
    },
]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_parser_configs(override_path: str | Path | None = None) -> list[dict[str, Any]]:
    config_map = {config["name"]: copy.deepcopy(config) for config in DEFAULT_PARSER_CONFIGS}

    configured_path = override_path or os.getenv("CREDIT_PARSER_CONFIG_PATH")
    if not configured_path:
        return list(config_map.values())

    override_file = Path(configured_path)
    if not override_file.exists():
        return list(config_map.values())

    try:
        overrides = json.loads(override_file.read_text(encoding="utf-8"))
    except Exception:
        return list(config_map.values())

    if isinstance(overrides, dict):
        overrides = overrides.get("parsers", [])

    if not isinstance(overrides, list):
        return list(config_map.values())

    for override in overrides:
        if not isinstance(override, dict) or "name" not in override:
            continue
        name = override["name"]
        if name in config_map:
            config_map[name] = _deep_merge(config_map[name], override)
        else:
            config_map[name] = copy.deepcopy(override)

    return list(config_map.values())
