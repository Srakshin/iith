from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import credit.routes as credit_routes
from credit.document_adapters.base import ExtractionPayload, build_text_signals
from credit.document_adapters.docling_adapter import DoclingAdapter
from credit.case_service import CreditCaseService
from credit.case_store import CreditCaseStore


FIXTURE_DIR = PROJECT_ROOT / "tests" / "credit" / "fixtures"


def _fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


async def _fake_docling_extract(self, file_path: Path) -> ExtractionPayload:
    text = file_path.read_text(encoding="utf-8")
    return ExtractionPayload(
        adapter=self.name,
        placeholder=False,
        extracted_text=text,
        extracted_fields=build_text_signals(text),
        metadata={"engine_available": True, "engine": self.name, "format": file_path.suffix.lower()},
    )


def test_credit_feature_endpoint_returns_populated_synthetic_case(monkeypatch, tmp_path):
    monkeypatch.setattr(DoclingAdapter, "extract", _fake_docling_extract)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)

    service = CreditCaseService(CreditCaseStore(tmp_path / "data" / "credit_cases.json"), tmp_path)
    monkeypatch.setattr(credit_routes, "credit_service", service)

    app = FastAPI()
    app.include_router(credit_routes.router)
    client = TestClient(app)

    create_response = client.post(
        "/api/credit/cases",
        json={"borrower_name": "Synthetic Components Private Limited"},
    )
    assert create_response.status_code == 200
    case_id = create_response.json()["case"]["case_id"]

    files = [
        ("files", ("synthetic_gst_return.txt", _fixture_text("synthetic_gst_return.txt"), "text/plain")),
        (
            "files",
            ("synthetic_bank_statement.txt", _fixture_text("synthetic_bank_statement.txt"), "text/plain"),
        ),
        (
            "files",
            (
                "synthetic_financial_statement.txt",
                _fixture_text("synthetic_financial_statement.txt"),
                "text/plain",
            ),
        ),
        ("files", ("synthetic_itr.txt", _fixture_text("synthetic_itr.txt"), "text/plain")),
        (
            "files",
            (
                "synthetic_sanction_letter.txt",
                _fixture_text("synthetic_sanction_letter.txt"),
                "text/plain",
            ),
        ),
        (
            "files",
            (
                "synthetic_annual_report.txt",
                _fixture_text("synthetic_annual_report.txt"),
                "text/plain",
            ),
        ),
        (
            "files",
            ("synthetic_legal_notice.txt", _fixture_text("synthetic_legal_notice.txt"), "text/plain"),
        ),
    ]
    upload_response = client.post(f"/api/credit/cases/{case_id}/files", files=files)
    assert upload_response.status_code == 200
    assert len(upload_response.json()["case"]["uploaded_files"]) == 7

    ingest_response = client.post(f"/api/credit/cases/{case_id}/ingest")
    assert ingest_response.status_code == 200
    ingested_case = ingest_response.json()["case"]
    assert len(ingested_case["dossier"]["structured_documents"]) >= 7

    features_response = client.post(f"/api/credit/cases/{case_id}/features")
    assert features_response.status_code == 200
    features = features_response.json()["features"]
    assert features["turnover_revenue"]["available"] is True
    assert features["circular_trading"]["suspicion_level"] == "high"
    assert features["gst_bank_consistency"]["consistency_band"] == "aligned"

    case_response = client.get(f"/api/credit/cases/{case_id}")
    assert case_response.status_code == 200
    stored_case = case_response.json()["case"]
    assert stored_case["dossier"]["credit_features"]["revenue_inflation"]["inflation_risk"] == "low"
    assert stored_case["dossier"]["financial_snapshot"]["annual_revenue"]["amount"] == 39000000.0
