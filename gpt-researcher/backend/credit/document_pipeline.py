from __future__ import annotations

from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .document_adapters.base import ExtractionPayload
from .document_adapters.docling_adapter import DoclingAdapter
from .document_adapters.surya_adapter import SuryaAdapter
from .ingestion.models import StructuredDocumentRecord
from .ingestion.parser import StructuredDocumentInterpreter, build_financial_snapshot
from .diligence_research.merge import build_dossier_risk_flags
from .case_models import (
    AdapterName,
    BorrowerCase,
    BorrowerDossier,
    CaseStatus,
    DossierOpenItem,
    ExtractedBorrowerDocument,
    FileStatus,
    default_ingestion_summary,
)


DOCUMENT_ADAPTERS = {
    ".pdf": AdapterName.DOCLING.value,
    ".doc": AdapterName.DOCLING.value,
    ".docx": AdapterName.DOCLING.value,
    ".txt": AdapterName.DOCLING.value,
    ".md": AdapterName.DOCLING.value,
    ".csv": AdapterName.DOCLING.value,
    ".xls": AdapterName.DOCLING.value,
    ".xlsx": AdapterName.DOCLING.value,
    ".html": AdapterName.DOCLING.value,
    ".png": AdapterName.SURYA.value,
    ".jpg": AdapterName.SURYA.value,
    ".jpeg": AdapterName.SURYA.value,
    ".tif": AdapterName.SURYA.value,
    ".tiff": AdapterName.SURYA.value,
    ".bmp": AdapterName.SURYA.value,
    ".webp": AdapterName.SURYA.value,
}


class CreditIngestionPipeline:
    def __init__(self, backend_root: Path) -> None:
        self._backend_root = backend_root
        self._docling_adapter = DoclingAdapter()
        self._surya_adapter = SuryaAdapter()
        self._structured_interpreter = StructuredDocumentInterpreter()

    def route_file(self, extension: str, media_type: str) -> str:
        if media_type.startswith("image/"):
            return AdapterName.SURYA.value
        return DOCUMENT_ADAPTERS.get(extension.lower(), AdapterName.PLACEHOLDER.value)

    async def ingest_case(self, case: BorrowerCase, timestamp: str) -> BorrowerCase:
        documents: list[ExtractedBorrowerDocument] = []
        engines_used: list[str] = []

        for file_record in case.uploaded_files:
            file_path = self._backend_root / file_record.storage_path
            adapter_name = self.route_file(file_record.extension, file_record.media_type)
            payload = await self._extract(file_path, adapter_name)
            document_id = f"doc_{uuid4().hex[:10]}"
            category = classify_document(file_record.original_filename)

            document = ExtractedBorrowerDocument(
                document_id=document_id,
                file_id=file_record.file_id,
                filename=file_record.original_filename,
                category=category,
                adapter=payload.adapter,
                status=FileStatus.PLACEHOLDER.value if payload.placeholder else FileStatus.INGESTED.value,
                placeholder=payload.placeholder,
                extracted_text=payload.extracted_text,
                extracted_fields=payload.extracted_fields,
                tables=payload.tables,
                pages=payload.pages,
                warnings=payload.warnings,
                metadata=payload.metadata,
            )
            documents.append(document)

            file_record.adapter_hint = adapter_name
            file_record.document_id = document_id
            file_record.status = document.status
            file_record.warnings = list(dict.fromkeys([*file_record.warnings, *payload.warnings]))
            if payload.adapter not in engines_used:
                engines_used.append(payload.adapter)

        case.dossier = self._build_dossier(case, documents, engines_used, timestamp)
        case.last_ingested_at = timestamp
        case.updated_at = timestamp
        if not case.uploaded_files:
            case.status = CaseStatus.ATTENTION_REQUIRED.value
        elif any(document.placeholder for document in documents):
            case.status = CaseStatus.ATTENTION_REQUIRED.value
        else:
            case.status = CaseStatus.READY.value

        case.timeline.append(
            {
                "timestamp": timestamp,
                "event": "ingestion_completed",
                "details": {
                    "documents": len(documents),
                    "placeholders": sum(1 for document in documents if document.placeholder),
                },
            }
        )
        return case

    async def _extract(self, file_path: Path, adapter_name: str) -> ExtractionPayload:
        if adapter_name == AdapterName.DOCLING.value:
            return await self._docling_adapter.extract(file_path)
        if adapter_name == AdapterName.SURYA.value:
            return await self._surya_adapter.extract(file_path)

        placeholder_text = (
            f"Unsupported file type for {file_path.name}. "
            "Stored as a placeholder so the dossier remains structurally complete."
        )
        return ExtractionPayload(
            adapter=AdapterName.PLACEHOLDER.value,
            placeholder=True,
            extracted_text=placeholder_text,
            extracted_fields={
                "text_excerpt": placeholder_text,
                "character_count": len(placeholder_text),
                "line_count": 1,
                "candidate_amounts": [],
                "candidate_dates": [],
                "candidate_percentages": [],
                "candidate_identifiers": [],
            },
            warnings=[f"No adapter is configured for {file_path.suffix.lower() or 'this file type'}."],
            metadata={"engine_available": False, "engine": AdapterName.PLACEHOLDER.value},
        )

    def _build_dossier(
        self,
        case: BorrowerCase,
        documents: list[ExtractedBorrowerDocument],
        engines_used: Iterable[str],
        timestamp: str,
    ) -> BorrowerDossier:
        structured_documents = self._structured_interpreter.parse_documents(documents)
        dossier = BorrowerDossier(
            case_id=case.case_id,
            borrower={
                "legal_name": case.borrower_name,
                "aliases": [],
            },
            financial_snapshot=build_financial_snapshot(structured_documents),
            documents=documents,
            structured_documents=structured_documents,
            secondary_research=case.dossier.secondary_research,
            qualitative_credit_officer_notes=case.dossier.qualitative_credit_officer_notes,
        )

        categories = sorted({document.category for document in documents})
        placeholder_docs = [document for document in documents if document.placeholder]
        parser_names = sorted({document.parser_name for document in structured_documents})
        dossier.ingestion_summary = {
            **default_ingestion_summary(),
            "files_total": len(case.uploaded_files),
            "files_processed": len(documents),
            "files_with_placeholders": len(placeholder_docs),
            "engines_used": list(engines_used),
            "document_categories": categories,
            "structured_documents": len(structured_documents),
            "structured_document_types": sorted({doc.document_type for doc in structured_documents}),
            "structured_parsers": parser_names,
            "last_run_at": timestamp,
        }

        dossier.open_items = build_open_items(case, documents, structured_documents)
        dossier.risk_flags = build_dossier_risk_flags(
            documents,
            structured_documents,
            dossier.secondary_research,
            dossier.qualitative_credit_officer_notes,
        )
        dossier.notes = [
            "Ingestion uses config-driven structured parsers and transparent rule-based feature inputs."
        ]
        return dossier


def classify_document(filename: str) -> str:
    lowered = filename.lower()
    if "bank" in lowered and "statement" in lowered:
        return "bank_statement"
    if "gst" in lowered or "gstr" in lowered:
        return "gst_return"
    if "itr" in lowered or "income tax" in lowered:
        return "tax_document"
    if "financial" in lowered or "p&l" in lowered or "balance" in lowered:
        return "financial_statement"
    if "annual" in lowered and "report" in lowered:
        return "annual_report"
    if "sanction" in lowered or "facility letter" in lowered:
        return "sanction_letter"
    if "legal notice" in lowered or "demand notice" in lowered or "sarfaesi" in lowered:
        return "legal_notice"
    if "tax" in lowered:
        return "tax_document"
    if "id" in lowered or "aadhaar" in lowered or "passport" in lowered:
        return "identity_document"
    if "invoice" in lowered:
        return "invoice"
    if "collateral" in lowered:
        return "collateral_document"
    return "general_document"


def build_open_items(
    case: BorrowerCase,
    documents: list[ExtractedBorrowerDocument],
    structured_documents: list[StructuredDocumentRecord],
) -> list[DossierOpenItem]:
    items: list[DossierOpenItem] = []

    if not case.uploaded_files:
        items.append(
            DossierOpenItem(
                code="documents_missing",
                title="Borrower documents pending",
                description="No borrower files have been uploaded yet, so the dossier only contains placeholders.",
                severity="warning",
            )
        )

    placeholder_docs = [document for document in documents if document.placeholder]
    if placeholder_docs:
        items.append(
            DossierOpenItem(
                code="placeholder_review",
                title="Review placeholder extractions",
                description="One or more files were stored with placeholder extraction output. Re-run once the relevant local engine is available.",
                severity="warning",
                related_document_ids=[document.document_id for document in placeholder_docs],
            )
        )

    if not any(document.extracted_fields.get("candidate_amounts") for document in documents):
        items.append(
            DossierOpenItem(
                code="financial_fields_missing",
                title="Financial figures still missing",
                description="No candidate financial amounts were detected yet. Later phases can reconcile financial statements and bank data.",
                severity="info",
            )
        )

    structured_types = {document.document_type for document in structured_documents}
    missing_core_documents = [
        document_type
        for document_type in ("gst_return", "bank_statement", "itr", "financial_statement")
        if document_type not in structured_types
    ]
    if missing_core_documents:
        items.append(
            DossierOpenItem(
                code="structured_docs_missing",
                title="Core financial documents missing",
                description=(
                    "Structured parsing did not detect all core document types. "
                    f"Missing: {', '.join(missing_core_documents)}."
                ),
                severity="warning",
            )
        )

    return items


def build_risk_flags(
    documents: list[ExtractedBorrowerDocument],
    structured_documents: list[StructuredDocumentRecord],
) -> list[dict[str, str]]:
    return build_dossier_risk_flags(documents, structured_documents)
