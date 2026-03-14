from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import credit.routes as credit_routes
from credit.providers.base import SecondaryResearchProvider
from credit.diligence_research import SecondaryResearchService
from credit.case_models import (
    ResearchAvailability,
    SecondaryResearchEvidence,
    SecondaryResearchFinding,
    SecondaryResearchSection,
    SecondaryResearchTopic,
    utc_now,
)
from credit.case_service import CreditCaseService
from credit.case_store import CreditCaseStore


class FakeAvailableResearchProvider(SecondaryResearchProvider):
    name = "fake_provider"

    def check_availability(self) -> tuple[bool, str | None]:
        return True, None

    async def research(self, _job):
        executed_at = utc_now()
        evidence = [
            SecondaryResearchEvidence(
                evidence_id="evidence_company_1",
                topic=SecondaryResearchTopic.COMPANY.value,
                title="Capacity expansion delayed by utility connection",
                summary="Public-web coverage indicates the borrower delayed a new line due to utility approvals.",
                source_url="https://example.com/company-update",
                source_title="Example Company Update",
                source_type="news",
                provider=self.name,
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
                provider=self.name,
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
                topic=SecondaryResearchTopic.PROMOTERS.value,
                status=ResearchAvailability.AVAILABLE.value,
                summary="Promoter reputation appears mixed but no major fraud references were captured.",
                source_urls=["https://example.com/company-update"],
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
            SecondaryResearchFinding(
                topic=SecondaryResearchTopic.MCA_REGULATORY_EVIDENCE.value,
                status=ResearchAvailability.PARTIAL.value,
                summary="Public-web filing-style references exist, but no direct MCA system integration is implied.",
                source_urls=["https://example.com/company-update"],
                message="Use as public-web regulatory-style evidence only.",
            ),
        ]
        return SecondaryResearchSection(
            status=ResearchAvailability.AVAILABLE.value,
            provider=self.name,
            query="synthetic borrower diligence",
            executed_at=executed_at,
            evidence=evidence,
            findings=findings,
            source_urls=[
                "https://example.com/company-update",
                "https://example.com/tribunal-update",
            ],
            extracted_risk_flags=["execution_delay", "margin_pressure", "litigation_signal"],
            coverage_note="Synthetic research result for API verification.",
        )


class FakeUnavailableResearchProvider(SecondaryResearchProvider):
    name = "fake_provider"

    def check_availability(self) -> tuple[bool, str | None]:
        return False, "Missing research provider configuration."

    async def research(self, _job):
        return SecondaryResearchSection(
            status=ResearchAvailability.UNAVAILABLE.value,
            provider=self.name,
            query="synthetic borrower diligence",
            executed_at=utc_now(),
            findings=[
                SecondaryResearchFinding(
                    topic=topic.value,
                    status=ResearchAvailability.UNAVAILABLE.value,
                    message="Missing research provider configuration.",
                )
                for topic in SecondaryResearchTopic
            ],
            coverage_note="Public-web secondary research unavailable in this environment.",
            message="Missing research provider configuration.",
        )


def _make_client(tmp_path: Path, provider: SecondaryResearchProvider) -> TestClient:
    service = CreditCaseService(
        CreditCaseStore(tmp_path / "data" / "credit_cases.json"),
        tmp_path,
        secondary_research_service=SecondaryResearchService(provider),
    )
    credit_routes.credit_service = service

    app = FastAPI()
    app.include_router(credit_routes.router)
    return TestClient(app)


def test_qualitative_notes_persist_into_case_and_artifact(tmp_path):
    client = _make_client(tmp_path, FakeAvailableResearchProvider())

    create_response = client.post(
        "/api/credit/cases",
        json={"borrower_name": "Notes Private Limited"},
    )
    assert create_response.status_code == 200
    case_id = create_response.json()["case"]["case_id"]

    notes_payload = {
        "factory_operating_capacity": "Observed at roughly 68% utilization with one idle line awaiting repair.",
        "management_quality": "Second line management appears capable and responsive during diligence meetings.",
        "governance_concerns": "Related-party procurement approvals were not fully documented on site.",
        "collateral_observations": "Charged machinery appears installed and tagged, though insurance copies were pending.",
        "site_visit_comments": "Inventory movement was lower than monthly sales run-rate and needs reconciliation.",
    }
    notes_response = client.post(f"/api/credit/cases/{case_id}/notes", json=notes_payload)
    assert notes_response.status_code == 200
    saved_case = notes_response.json()["case"]
    assert (
        saved_case["dossier"]["qualitative_credit_officer_notes"]["factory_operating_capacity"]
        == notes_payload["factory_operating_capacity"]
    )
    assert saved_case["dossier"]["qualitative_credit_officer_notes"]["updated_at"] is not None
    assert any(
        flag["category"] == "qualitative_note" for flag in saved_case["dossier"]["risk_flags"]
    )

    case_artifact = (
        tmp_path / "outputs" / "credit" / "cases" / case_id / "normalized_dossier.json"
    )
    persisted = json.loads(case_artifact.read_text(encoding="utf-8"))
    assert (
        persisted["qualitative_credit_officer_notes"]["governance_concerns"]
        == notes_payload["governance_concerns"]
    )
    assert "Qualitative due-diligence notes captured" in persisted["notes"][-1]


def test_secondary_research_merges_structured_evidence_and_source_links(tmp_path):
    client = _make_client(tmp_path, FakeAvailableResearchProvider())

    create_response = client.post(
        "/api/credit/cases",
        json={"borrower_name": "Research Private Limited"},
    )
    assert create_response.status_code == 200
    case_id = create_response.json()["case"]["case_id"]

    client.post(
        f"/api/credit/cases/{case_id}/notes",
        json={
            "governance_concerns": "Board approvals for related-party transactions need tighter review.",
            "site_visit_comments": "Plant appears underutilized on the current shift pattern.",
        },
    )

    research_response = client.post(f"/api/credit/cases/{case_id}/research", json={})
    assert research_response.status_code == 200
    research_payload = research_response.json()["research"]
    case_payload = research_response.json()["case"]

    assert research_payload["status"] == ResearchAvailability.AVAILABLE.value
    assert research_payload["provider"] == "fake_provider"
    assert len(research_payload["evidence"]) == 2
    assert research_payload["source_urls"] == [
        "https://example.com/company-update",
        "https://example.com/tribunal-update",
    ]
    assert case_payload["dossier"]["secondary_research"]["findings"][3]["topic"] == "litigation"
    assert any(
        flag["category"] == "secondary_research"
        for flag in case_payload["dossier"]["risk_flags"]
    )
    assert any(
        flag["category"] == "qualitative_note"
        for flag in case_payload["dossier"]["risk_flags"]
    )

    stored_case_response = client.get(f"/api/credit/cases/{case_id}")
    assert stored_case_response.status_code == 200
    stored_case = stored_case_response.json()["case"]
    assert (
        stored_case["dossier"]["secondary_research"]["coverage_note"]
        == "Synthetic research result for API verification."
    )


def test_secondary_research_returns_structured_unavailable_result_when_provider_is_missing(tmp_path):
    client = _make_client(tmp_path, FakeUnavailableResearchProvider())

    create_response = client.post(
        "/api/credit/cases",
        json={"borrower_name": "Unavailable Provider Private Limited"},
    )
    assert create_response.status_code == 200
    case_id = create_response.json()["case"]["case_id"]

    research_response = client.post(f"/api/credit/cases/{case_id}/research", json={})
    assert research_response.status_code == 200
    research_payload = research_response.json()["research"]

    assert research_payload["status"] == ResearchAvailability.UNAVAILABLE.value
    assert research_payload["message"] == "Missing research provider configuration."
    assert len(research_payload["findings"]) == 5
    assert all(
        finding["status"] == ResearchAvailability.UNAVAILABLE.value
        for finding in research_payload["findings"]
    )
