from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .appraisal.models import CreditAppraisalMemo
from .feature_extraction.models import CreditFeatureBundle
from .ingestion.models import StructuredDocumentRecord
from .recommendations.models import CreditRecommendationResult


DOSSIER_SCHEMA_VERSION = "intelli-credit.borrower-dossier.v4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_ingestion_summary() -> dict[str, Any]:
    return {
        "files_total": 0,
        "files_processed": 0,
        "files_with_placeholders": 0,
        "engines_used": [],
        "document_categories": [],
        "structured_documents": 0,
        "structured_document_types": [],
        "structured_parsers": [],
        "last_run_at": None,
    }


class CaseStatus(str, Enum):
    CREATED = "created"
    FILES_UPLOADED = "files_uploaded"
    INGESTION_IN_PROGRESS = "ingestion_in_progress"
    READY = "ready"
    ATTENTION_REQUIRED = "attention_required"


class FileStatus(str, Enum):
    UPLOADED = "uploaded"
    INGESTED = "ingested"
    PLACEHOLDER = "placeholder"
    FAILED = "failed"


class AdapterName(str, Enum):
    DOCLING = "docling"
    SURYA = "surya"
    PLACEHOLDER = "placeholder"


class ResearchAvailability(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class SecondaryResearchTopic(str, Enum):
    COMPANY = "company"
    PROMOTERS = "promoters"
    SECTOR_HEADWINDS = "sector_headwinds"
    LITIGATION = "litigation"
    MCA_REGULATORY_EVIDENCE = "mca_regulatory_evidence"


class BorrowerParty(BaseModel):
    model_config = ConfigDict(extra="allow")

    legal_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    industry: str | None = None
    registration_id: str | None = None
    tax_id: str | None = None
    headquarters: str | None = None


class CreditRequestSummary(BaseModel):
    requested_amount: dict[str, Any] = Field(
        default_factory=lambda: {"amount": None, "currency": None, "raw": None}
    )
    purpose: str | None = None
    product_type: str | None = None
    tenor_months: int | None = None
    collateral_summary: str | None = None


class FinancialSnapshot(BaseModel):
    as_of_date: str | None = None
    annual_revenue: dict[str, Any] = Field(default_factory=dict)
    ebitda: dict[str, Any] = Field(default_factory=dict)
    net_income: dict[str, Any] = Field(default_factory=dict)
    total_debt: dict[str, Any] = Field(default_factory=dict)
    liquidity: dict[str, Any] = Field(default_factory=dict)
    currency: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SecondaryResearchEvidence(BaseModel):
    evidence_id: str
    topic: str
    title: str
    summary: str
    source_url: str | None = None
    source_title: str | None = None
    source_type: str = "web"
    provider: str | None = None
    confidence: str | None = None
    observed_at: str | None = None
    extracted_risk_flags: list[str] = Field(default_factory=list)


class SecondaryResearchFinding(BaseModel):
    topic: str
    status: str = ResearchAvailability.UNAVAILABLE.value
    summary: str | None = None
    evidence: list[SecondaryResearchEvidence] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    message: str | None = None


class SecondaryResearchSection(BaseModel):
    status: str = ResearchAvailability.UNAVAILABLE.value
    provider: str = "gpt_researcher"
    query: str | None = None
    executed_at: str | None = None
    evidence: list[SecondaryResearchEvidence] = Field(default_factory=list)
    findings: list[SecondaryResearchFinding] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    extracted_risk_flags: list[str] = Field(default_factory=list)
    coverage_note: str | None = None
    message: str | None = None


class QualitativeCreditOfficerNotes(BaseModel):
    factory_operating_capacity: str | None = None
    management_quality: str | None = None
    governance_concerns: str | None = None
    collateral_observations: str | None = None
    site_visit_comments: str | None = None
    additional_comments: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


class BorrowerFileRecord(BaseModel):
    file_id: str
    filename: str
    original_filename: str
    extension: str
    media_type: str
    size_bytes: int
    uploaded_at: str
    storage_path: str
    adapter_hint: str
    status: str = FileStatus.UPLOADED.value
    document_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ExtractedBorrowerDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    document_id: str
    file_id: str
    filename: str
    category: str
    adapter: str
    status: str
    placeholder: bool = False
    extracted_text: str = ""
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    pages: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DossierOpenItem(BaseModel):
    code: str
    title: str
    description: str
    severity: str = "warning"
    status: str = "open"
    related_document_ids: list[str] = Field(default_factory=list)


class BorrowerDossier(BaseModel):
    schema_version: str = DOSSIER_SCHEMA_VERSION
    case_id: str
    borrower: BorrowerParty = Field(default_factory=BorrowerParty)
    credit_request: CreditRequestSummary = Field(default_factory=CreditRequestSummary)
    financial_snapshot: FinancialSnapshot = Field(default_factory=FinancialSnapshot)
    documents: list[ExtractedBorrowerDocument] = Field(default_factory=list)
    structured_documents: list[StructuredDocumentRecord] = Field(default_factory=list)
    credit_features: CreditFeatureBundle | None = None
    credit_recommendation: CreditRecommendationResult | None = None
    credit_appraisal_memo: CreditAppraisalMemo | None = None
    secondary_research: SecondaryResearchSection = Field(default_factory=SecondaryResearchSection)
    qualitative_credit_officer_notes: QualitativeCreditOfficerNotes = Field(
        default_factory=QualitativeCreditOfficerNotes
    )
    risk_flags: list[dict[str, Any]] = Field(default_factory=list)
    open_items: list[DossierOpenItem] = Field(default_factory=list)
    ingestion_summary: dict[str, Any] = Field(default_factory=default_ingestion_summary)
    notes: list[str] = Field(default_factory=list)


class BorrowerCase(BaseModel):
    case_id: str
    borrower_name: str
    external_reference: str | None = None
    status: str = CaseStatus.CREATED.value
    created_at: str
    updated_at: str
    last_ingested_at: str | None = None
    uploaded_files: list[BorrowerFileRecord] = Field(default_factory=list)
    dossier: BorrowerDossier
    artifacts: dict[str, str] = Field(default_factory=dict)
    timeline: list[dict[str, Any]] = Field(default_factory=list)


class CreateBorrowerCaseRequest(BaseModel):
    borrower_name: str
    external_reference: str | None = None


class RunSecondaryResearchRequest(BaseModel):
    source_urls: list[str] = Field(default_factory=list)
    query_domains: list[str] = Field(default_factory=list)


class UpsertQualitativeCreditOfficerNotesRequest(BaseModel):
    factory_operating_capacity: str | None = None
    management_quality: str | None = None
    governance_concerns: str | None = None
    collateral_observations: str | None = None
    site_visit_comments: str | None = None
    additional_comments: str | None = None
    updated_by: str | None = None
