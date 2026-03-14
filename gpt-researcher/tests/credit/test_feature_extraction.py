from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from credit.feature_extraction.engine import CreditFeatureEngine
from credit.ingestion.parser import StructuredDocumentInterpreter, build_financial_snapshot
from credit.document_pipeline import classify_document
from credit.case_models import BorrowerCase, BorrowerDossier, ExtractedBorrowerDocument, utc_now


FIXTURE_DIR = PROJECT_ROOT / "tests" / "credit" / "fixtures"


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _make_document(filename: str) -> ExtractedBorrowerDocument:
    return ExtractedBorrowerDocument(
        document_id=f"doc_{filename.replace('.', '_')}",
        file_id=f"file_{filename.replace('.', '_')}",
        filename=filename,
        category=classify_document(filename),
        adapter="synthetic",
        status="ingested",
        placeholder=False,
        extracted_text=_fixture_text(filename),
    )


def _build_case(documents: list[ExtractedBorrowerDocument]) -> BorrowerCase:
    now = utc_now()
    return BorrowerCase(
        case_id="case_synthetic_phase2",
        borrower_name="Synthetic Components Private Limited",
        created_at=now,
        updated_at=now,
        dossier=BorrowerDossier(
            case_id="case_synthetic_phase2",
            borrower={"legal_name": "Synthetic Components Private Limited"},
            documents=documents,
        ),
    )


def test_config_driven_parsers_extract_supported_document_types():
    interpreter = StructuredDocumentInterpreter()
    documents = [
        _make_document("synthetic_gst_return.txt"),
        _make_document("synthetic_bank_statement.txt"),
        _make_document("synthetic_financial_statement.txt"),
        _make_document("synthetic_itr.txt"),
        _make_document("synthetic_sanction_letter.txt"),
        _make_document("synthetic_annual_report.txt"),
        _make_document("synthetic_legal_notice.txt"),
    ]

    structured_documents = interpreter.parse_documents(documents)
    structured_types = {document.document_type for document in structured_documents}

    assert structured_types >= {
        "gst_return",
        "bank_statement",
        "financial_statement",
        "itr",
        "sanction_letter",
        "annual_report",
        "legal_notice",
    }

    gst_document = next(document for document in structured_documents if document.document_type == "gst_return")
    assert any(metric.name == "taxable_turnover" and metric.value == 8_700_000 for metric in gst_document.metrics)
    assert len(gst_document.series["monthly_taxable_turnover"]) == 3

    annual_report = next(
        document for document in structured_documents if document.document_type == "annual_report"
    )
    assert "auditor_qualification" in annual_report.flags

    snapshot = build_financial_snapshot(structured_documents)
    assert snapshot.annual_revenue["amount"] == 39_000_000
    assert snapshot.total_debt["amount"] == 18_000_000


def test_credit_feature_engine_computes_populated_rule_based_bundle():
    interpreter = StructuredDocumentInterpreter()
    documents = [
        _make_document("synthetic_gst_return.txt"),
        _make_document("synthetic_bank_statement.txt"),
        _make_document("synthetic_financial_statement.txt"),
        _make_document("synthetic_itr.txt"),
        _make_document("synthetic_sanction_letter.txt"),
        _make_document("synthetic_annual_report.txt"),
        _make_document("synthetic_legal_notice.txt"),
    ]
    structured_documents = interpreter.parse_documents(documents)
    case = _build_case(documents)
    case.dossier.structured_documents = structured_documents
    case.dossier.financial_snapshot = build_financial_snapshot(structured_documents)

    features = CreditFeatureEngine().compute(case, structured_documents)

    assert features.turnover_revenue.available is True
    assert features.turnover_revenue.latest_annual_revenue == 39_000_000
    assert features.turnover_revenue.turnover_trend == "increasing"
    assert features.gst_bank_consistency.consistency_band == "aligned"
    assert features.gst_bank_consistency.overlap_periods == ["Apr 2024", "May 2024", "Jun 2024"]
    assert features.circular_trading.suspicion_level == "high"
    assert "same_day_in_out_ratio_high" in features.circular_trading.triggered_rules
    assert features.revenue_inflation.inflation_risk == "low"
    assert features.liquidity_leverage.liquidity_band == "healthy"
    assert features.document_quality.missing_core_documents == []
    assert features.document_quality.parsed_structured_documents >= 7
