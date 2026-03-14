from __future__ import annotations

import json
import sys
import urllib.parse
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
from credit.appraisal.generator import CreditAppraisalMemoGenerator
from credit.providers.base import SecondaryResearchProvider
from credit.feature_extraction.engine import CreditFeatureEngine
from credit.ingestion.parser import StructuredDocumentInterpreter, build_financial_snapshot
from credit.document_pipeline import classify_document
from credit.diligence_research import SecondaryResearchService
from credit.diligence_research.merge import build_dossier_risk_flags
from credit.recommendations.engine import CreditRecommendationEngine
from credit.case_models import (
    BorrowerCase,
    BorrowerDossier,
    ExtractedBorrowerDocument,
    QualitativeCreditOfficerNotes,
    ResearchAvailability,
    SecondaryResearchEvidence,
    SecondaryResearchFinding,
    SecondaryResearchSection,
    SecondaryResearchTopic,
    utc_now,
)
from credit.case_service import CreditCaseService
from credit.case_store import CreditCaseStore


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


def _base_case() -> BorrowerCase:
    filenames = [
        "synthetic_gst_return.txt",
        "synthetic_bank_statement.txt",
        "synthetic_financial_statement.txt",
        "synthetic_itr.txt",
        "synthetic_sanction_letter.txt",
        "synthetic_annual_report.txt",
        "synthetic_legal_notice.txt",
    ]
    documents = [_make_document(filename) for filename in filenames]
    now = utc_now()
    case = BorrowerCase(
        case_id="case_phase5_synthetic",
        borrower_name="Synthetic Components Private Limited",
        created_at=now,
        updated_at=now,
        dossier=BorrowerDossier(
            case_id="case_phase5_synthetic",
            borrower={"legal_name": "Synthetic Components Private Limited"},
            documents=documents,
        ),
    )
    structured_documents = StructuredDocumentInterpreter().parse_documents(documents)
    case.dossier.structured_documents = structured_documents
    case.dossier.financial_snapshot = build_financial_snapshot(structured_documents)
    case.dossier.credit_features = CreditFeatureEngine().compute(case, structured_documents)
    return case


def _available_research_section() -> SecondaryResearchSection:
    evidence = [
        SecondaryResearchEvidence(
            evidence_id="evidence_company_1",
            topic=SecondaryResearchTopic.COMPANY.value,
            title="Capacity expansion delayed by utility connection",
            summary="Public-web coverage indicates the borrower delayed a new line due to utility approvals.",
            source_url="https://example.com/company-update",
            source_title="Example Company Update",
            source_type="news",
            provider="fake_provider",
            extracted_risk_flags=["execution_delay"],
        ),
        SecondaryResearchEvidence(
            evidence_id="evidence_litigation_1",
            topic=SecondaryResearchTopic.LITIGATION.value,
            title="Vendor recovery petition referenced in tribunal update",
            summary="A tribunal tracker references a vendor recovery filing against the borrower group.",
            source_url="https://example.com/tribunal-update",
            source_title="Example Tribunal Update",
            source_type="regulatory",
            provider="fake_provider",
            extracted_risk_flags=["litigation_signal"],
        ),
    ]
    findings = [
        SecondaryResearchFinding(
            topic=SecondaryResearchTopic.COMPANY.value,
            status=ResearchAvailability.AVAILABLE.value,
            summary="Recent operating updates indicate moderate execution pressure.",
            evidence=[evidence[0]],
            source_urls=["https://example.com/company-update"],
            risk_flags=["execution_delay"],
        ),
        SecondaryResearchFinding(
            topic=SecondaryResearchTopic.SECTOR_HEADWINDS.value,
            status=ResearchAvailability.AVAILABLE.value,
            summary="Input cost volatility and slower industrial demand remain active sector headwinds.",
            source_urls=["https://example.com/company-update"],
            risk_flags=["margin_pressure"],
        ),
        SecondaryResearchFinding(
            topic=SecondaryResearchTopic.LITIGATION.value,
            status=ResearchAvailability.AVAILABLE.value,
            summary="At least one public recovery-related reference was captured and should be checked manually.",
            evidence=[evidence[1]],
            source_urls=["https://example.com/tribunal-update"],
            risk_flags=["litigation_signal"],
        ),
    ]
    return SecondaryResearchSection(
        status=ResearchAvailability.AVAILABLE.value,
        provider="fake_provider",
        query="synthetic borrower diligence",
        executed_at=utc_now(),
        evidence=evidence,
        findings=findings,
        source_urls=["https://example.com/company-update", "https://example.com/tribunal-update"],
        extracted_risk_flags=["execution_delay", "margin_pressure", "litigation_signal"],
        coverage_note="Synthetic research result for CAM verification.",
    )


def _rebuild_flags(case: BorrowerCase) -> None:
    case.dossier.risk_flags = build_dossier_risk_flags(
        case.dossier.documents,
        case.dossier.structured_documents,
        case.dossier.secondary_research,
        case.dossier.qualitative_credit_officer_notes,
    )


def _reject_case() -> BorrowerCase:
    case = _base_case()
    case.dossier.secondary_research = _available_research_section()
    case.dossier.qualitative_credit_officer_notes = QualitativeCreditOfficerNotes(
        factory_operating_capacity="Observed at roughly 68% utilization with one idle line awaiting repair.",
        management_quality="Second line management appears capable and responsive during diligence meetings.",
        governance_concerns="Related-party procurement approvals were not fully documented on site.",
        collateral_observations="Charged machinery appears installed and tagged, though insurance copies were pending.",
        site_visit_comments="Inventory movement was lower than monthly sales run-rate and needs reconciliation.",
    )
    _rebuild_flags(case)
    case.dossier.credit_recommendation = CreditRecommendationEngine().score(case)
    return case


class FakeAvailableResearchProvider(SecondaryResearchProvider):
    name = "fake_provider"

    def check_availability(self) -> tuple[bool, str | None]:
        return True, None

    async def research(self, _job):
        return _available_research_section()


async def _fake_docling_extract(self, file_path: Path) -> ExtractionPayload:
    text = file_path.read_text(encoding="utf-8")
    return ExtractionPayload(
        adapter=self.name,
        placeholder=False,
        extracted_text=text,
        extracted_fields=build_text_signals(text),
        metadata={"engine_available": True, "engine": self.name, "format": file_path.suffix.lower()},
    )


def _make_client(tmp_path: Path) -> TestClient:
    service = CreditCaseService(
        CreditCaseStore(tmp_path / "data" / "credit_cases.json"),
        tmp_path,
        secondary_research_service=SecondaryResearchService(FakeAvailableResearchProvider()),
    )
    credit_routes.credit_service = service

    app = FastAPI()
    app.include_router(credit_routes.router)
    return TestClient(app)


def test_cam_generator_contains_required_sections_and_explicit_decision_reason():
    case = _reject_case()
    generator = CreditAppraisalMemoGenerator()

    memo = generator.generate(case)
    markdown = generator.render_markdown(memo)

    assert memo.borrower_overview.title == "Borrower Overview"
    assert memo.five_cs.character.title == "Character"
    assert memo.five_cs.capacity.title == "Capacity"
    assert memo.five_cs.capital.title == "Capital"
    assert memo.five_cs.collateral.title == "Collateral"
    assert memo.five_cs.conditions.title == "Conditions"
    assert memo.final_recommendation.title == "Final Recommendation"
    assert "because" in memo.decision_rationale.summary.lower()
    assert any(item.source_url for item in memo.research_findings_and_flags.evidence)
    assert "## Borrower Overview" in markdown
    assert "## Five Cs of Credit" in markdown
    assert "### Character" in markdown
    assert "## Key Financial Findings" in markdown
    assert "## GST and Bank Reconciliation Findings" in markdown
    assert "## Research Findings and Litigation or Regulatory Flags" in markdown
    assert "## Primary Due-Diligence Notes" in markdown
    assert "## Final Recommendation" in markdown
    assert "## Decision Rationale" in markdown


def test_cam_endpoint_generates_and_exports_markdown_pdf_and_docx(monkeypatch, tmp_path):
    monkeypatch.setattr(DoclingAdapter, "extract", _fake_docling_extract)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)

    client = _make_client(tmp_path)

    create_response = client.post(
        "/api/credit/cases",
        json={"borrower_name": "Synthetic Components Private Limited"},
    )
    assert create_response.status_code == 200
    case_id = create_response.json()["case"]["case_id"]

    files = [
        ("files", ("synthetic_gst_return.txt", _fixture_text("synthetic_gst_return.txt"), "text/plain")),
        ("files", ("synthetic_bank_statement.txt", _fixture_text("synthetic_bank_statement.txt"), "text/plain")),
        ("files", ("synthetic_financial_statement.txt", _fixture_text("synthetic_financial_statement.txt"), "text/plain")),
        ("files", ("synthetic_itr.txt", _fixture_text("synthetic_itr.txt"), "text/plain")),
        ("files", ("synthetic_sanction_letter.txt", _fixture_text("synthetic_sanction_letter.txt"), "text/plain")),
        ("files", ("synthetic_annual_report.txt", _fixture_text("synthetic_annual_report.txt"), "text/plain")),
        ("files", ("synthetic_legal_notice.txt", _fixture_text("synthetic_legal_notice.txt"), "text/plain")),
    ]
    assert client.post(f"/api/credit/cases/{case_id}/files", files=files).status_code == 200
    assert client.post(f"/api/credit/cases/{case_id}/ingest").status_code == 200
    assert client.post(f"/api/credit/cases/{case_id}/features").status_code == 200
    assert client.post(
        f"/api/credit/cases/{case_id}/notes",
        json={
            "factory_operating_capacity": "Observed at roughly 68% utilization with one idle line awaiting repair.",
            "management_quality": "Second line management appears capable and responsive during diligence meetings.",
            "governance_concerns": "Related-party procurement approvals were not fully documented on site.",
            "collateral_observations": "Charged machinery appears installed and tagged, though insurance copies were pending.",
            "site_visit_comments": "Inventory movement was lower than monthly sales run-rate and needs reconciliation.",
        },
    ).status_code == 200
    assert client.post(f"/api/credit/cases/{case_id}/research", json={}).status_code == 200
    assert client.post(f"/api/credit/cases/{case_id}/score").status_code == 200

    cam_response = client.post(f"/api/credit/cases/{case_id}/cam")
    assert cam_response.status_code == 200
    payload = cam_response.json()
    cam = payload["cam"]

    assert cam["five_cs"]["character"]["title"] == "Character"
    assert cam["final_recommendation"]["title"] == "Final Recommendation"
    assert cam["decision_rationale"]["title"] == "Decision Rationale"

    for artifact_path in cam["artifacts"].values():
        resolved = tmp_path / Path(urllib.parse.unquote(artifact_path))
        assert resolved.exists(), f"Expected CAM artifact to exist: {resolved}"

    for export_format in ("md", "pdf", "docx"):
        download_response = client.get(f"/api/credit/cases/{case_id}/cam/download/{export_format}")
        assert download_response.status_code == 200

    persisted_path = tmp_path / "outputs" / "credit" / "cases" / case_id / "normalized_dossier.json"
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["credit_appraisal_memo"]["borrower_overview"]["title"] == "Borrower Overview"
    assert persisted["credit_appraisal_memo"]["artifacts"]["pdf"].endswith(".pdf")
