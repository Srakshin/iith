from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from utils import write_md_to_pdf, write_md_to_word, write_text_to_md

from .appraisal.generator import CreditAppraisalMemoGenerator
from .feature_extraction.engine import CreditFeatureEngine
from .ingestion.parser import StructuredDocumentInterpreter, build_financial_snapshot
from .document_pipeline import CreditIngestionPipeline
from .diligence_research import SecondaryResearchService, build_dossier_risk_flags, dedupe_strings
from .recommendations.engine import CreditRecommendationEngine
from .case_models import (
    BorrowerCase,
    BorrowerDossier,
    BorrowerFileRecord,
    CaseStatus,
    CreateBorrowerCaseRequest,
    QualitativeCreditOfficerNotes,
    RunSecondaryResearchRequest,
    UpsertQualitativeCreditOfficerNotesRequest,
    default_ingestion_summary,
    utc_now,
)
from .case_store import CreditCaseStore


class CreditCaseService:
    def __init__(
        self,
        store: CreditCaseStore,
        backend_root: Path,
        secondary_research_service: SecondaryResearchService | None = None,
    ):
        self._store = store
        self._backend_root = backend_root
        self._outputs_root = backend_root / "outputs"
        self._pipeline = CreditIngestionPipeline(backend_root)
        self._structured_interpreter = StructuredDocumentInterpreter()
        self._feature_engine = CreditFeatureEngine()
        self._recommendation_engine = CreditRecommendationEngine()
        self._cam_generator = CreditAppraisalMemoGenerator()
        self._secondary_research_service = secondary_research_service

    async def list_cases(self) -> list[BorrowerCase]:
        return await self._store.list_cases()

    async def get_case(self, case_id: str) -> BorrowerCase | None:
        return await self._store.get_case(case_id)

    async def create_case(self, request: CreateBorrowerCaseRequest) -> BorrowerCase:
        case_id = self._generate_case_id(request.borrower_name)
        now = utc_now()
        case = BorrowerCase(
            case_id=case_id,
            borrower_name=request.borrower_name.strip(),
            external_reference=request.external_reference,
            status=CaseStatus.CREATED.value,
            created_at=now,
            updated_at=now,
            dossier=BorrowerDossier(
                case_id=case_id,
                borrower={"legal_name": request.borrower_name.strip()},
                ingestion_summary=default_ingestion_summary(),
            ),
            timeline=[
                {
                    "timestamp": now,
                    "event": "case_created",
                    "details": {"borrower_name": request.borrower_name.strip()},
                }
            ],
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def upload_files(self, case_id: str, files: list[UploadFile]) -> BorrowerCase:
        case = await self._require_case(case_id)
        uploaded_count = 0
        for file in files:
            contents = await file.read()
            self._store_uploaded_file(
                case,
                original_name=file.filename or "borrower-file",
                media_type=file.content_type or "application/octet-stream",
                contents=contents,
            )
            uploaded_count += 1

        self._finalize_uploaded_files(case, uploaded_count)
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def upload_local_files(self, case_id: str, file_paths: list[Path]) -> BorrowerCase:
        case = await self._require_case(case_id)
        uploaded_count = 0
        for path in file_paths:
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"Demo fixture not found: {path.as_posix()}")
            media_type = "text/plain" if path.suffix.lower() == ".txt" else "application/octet-stream"
            self._store_uploaded_file(
                case,
                original_name=path.name,
                media_type=media_type,
                contents=path.read_bytes(),
            )
            uploaded_count += 1

        self._finalize_uploaded_files(case, uploaded_count)
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def ingest_case(self, case_id: str) -> BorrowerCase:
        case = await self._require_case(case_id)
        now = utc_now()
        case.status = CaseStatus.INGESTION_IN_PROGRESS.value
        case.updated_at = now
        case.timeline.append({"timestamp": now, "event": "ingestion_started", "details": {}})
        await self._store.save_case(case)

        case = await self._pipeline.ingest_case(case, now)
        case.dossier.credit_recommendation = None
        self._invalidate_cam(case)
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def compute_case_features(self, case_id: str):
        case = await self._require_case(case_id)

        if not case.dossier.structured_documents and case.dossier.documents:
            case.dossier.structured_documents = self._structured_interpreter.parse_documents(
                case.dossier.documents
            )
            case.dossier.financial_snapshot = build_financial_snapshot(
                case.dossier.structured_documents
            )
            case.dossier.ingestion_summary = {
                **case.dossier.ingestion_summary,
                "structured_documents": len(case.dossier.structured_documents),
                "structured_document_types": sorted(
                    {document.document_type for document in case.dossier.structured_documents}
                ),
                "structured_parsers": sorted(
                    {document.parser_name for document in case.dossier.structured_documents}
                ),
            }

        features = self._feature_engine.compute(case, case.dossier.structured_documents)
        case.dossier.credit_features = features
        case.dossier.credit_recommendation = None
        self._invalidate_cam(case)
        case.updated_at = features.computed_at
        case.timeline.append(
            {
                "timestamp": case.updated_at,
                "event": "credit_features_computed",
                "details": {
                    "structured_documents": len(case.dossier.structured_documents),
                    "feature_sections": [
                        "turnover_revenue",
                        "gst_bank_consistency",
                        "circular_trading",
                        "revenue_inflation",
                        "liquidity_leverage",
                        "document_quality",
                    ],
                },
            }
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return features

    async def save_qualitative_notes(
        self,
        case_id: str,
        request: UpsertQualitativeCreditOfficerNotesRequest,
    ) -> BorrowerCase:
        case = await self._require_case(case_id)
        updates = request.model_dump(exclude_unset=True)
        now = utc_now()

        merged_notes = case.dossier.qualitative_credit_officer_notes.model_copy(update=updates)
        merged_notes.updated_at = now
        if "updated_by" not in updates:
            merged_notes.updated_by = case.dossier.qualitative_credit_officer_notes.updated_by

        case.dossier.qualitative_credit_officer_notes = QualitativeCreditOfficerNotes.model_validate(
            merged_notes
        )
        case.dossier.risk_flags = build_dossier_risk_flags(
            case.dossier.documents,
            case.dossier.structured_documents,
            case.dossier.secondary_research,
            case.dossier.qualitative_credit_officer_notes,
        )
        case.dossier.notes = dedupe_strings(
            [
                *case.dossier.notes,
                "Qualitative due-diligence notes captured for the borrower dossier.",
            ]
        )
        case.dossier.credit_recommendation = None
        self._invalidate_cam(case)
        case.updated_at = now
        case.timeline.append(
            {
                "timestamp": now,
                "event": "qualitative_notes_saved",
                "details": {
                    "updated_fields": sorted(
                        key for key in updates.keys() if key != "updated_by"
                    ),
                },
            }
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def run_secondary_research(
        self,
        case_id: str,
        request: RunSecondaryResearchRequest | None = None,
    ) -> BorrowerCase:
        case = await self._require_case(case_id)
        research = await self._get_secondary_research_service().run(case, request)

        case.dossier.secondary_research = research
        case.dossier.risk_flags = build_dossier_risk_flags(
            case.dossier.documents,
            case.dossier.structured_documents,
            research,
            case.dossier.qualitative_credit_officer_notes,
        )
        case.dossier.notes = dedupe_strings(
            [
                *case.dossier.notes,
                "Secondary research evidence has been merged into the borrower dossier.",
            ]
        )
        case.dossier.credit_recommendation = None
        self._invalidate_cam(case)
        case.updated_at = research.executed_at or utc_now()
        case.timeline.append(
            {
                "timestamp": case.updated_at,
                "event": "secondary_research_completed",
                "details": {
                    "status": research.status,
                    "provider": research.provider,
                    "evidence_items": len(research.evidence),
                    "source_urls": len(research.source_urls),
                },
            }
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return case

    async def compute_case_recommendation(self, case_id: str):
        case = await self._require_case(case_id)

        if case.dossier.credit_features is None:
            await self.compute_case_features(case_id)
            case = await self._require_case(case_id)

        case.dossier.risk_flags = build_dossier_risk_flags(
            case.dossier.documents,
            case.dossier.structured_documents,
            case.dossier.secondary_research,
            case.dossier.qualitative_credit_officer_notes,
        )
        recommendation = self._recommendation_engine.score(case)
        case.dossier.credit_recommendation = recommendation
        self._invalidate_cam(case)
        case.dossier.notes = dedupe_strings(
            [
                *case.dossier.notes,
                "Recommendation generated by the deterministic explainable scorecard.",
            ]
        )
        case.updated_at = recommendation.generated_at
        case.timeline.append(
            {
                "timestamp": recommendation.generated_at,
                "event": "credit_recommendation_generated",
                "details": {
                    "decision": recommendation.decision,
                    "risk_score": recommendation.overall_risk_score,
                    "engine_version": recommendation.engine_version,
                },
            }
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return recommendation

    async def generate_case_cam(self, case_id: str):
        case = await self._require_case(case_id)

        if case.dossier.credit_recommendation is None:
            await self.compute_case_recommendation(case_id)
            case = await self._require_case(case_id)

        memo = self._cam_generator.generate(case)
        markdown = self._cam_generator.render_markdown(memo)
        filename = f"credit_case_{case.case_id}_cam"
        markdown_path = urllib.parse.unquote(await write_text_to_md(markdown, filename))
        pdf_path = urllib.parse.unquote(await write_md_to_pdf(markdown, filename))
        docx_path = urllib.parse.unquote(await write_md_to_word(markdown, filename))

        memo.artifacts = {
            "markdown": markdown_path.replace("\\", "/"),
            "pdf": pdf_path.replace("\\", "/"),
            "docx": docx_path.replace("\\", "/"),
        }
        case.dossier.credit_appraisal_memo = memo
        case.artifacts = {
            **case.artifacts,
            "cam_markdown": memo.artifacts["markdown"],
            "cam_pdf": memo.artifacts["pdf"],
            "cam_docx": memo.artifacts["docx"],
        }
        case.dossier.notes = dedupe_strings(
            [
                *case.dossier.notes,
                "Credit appraisal memo generated with structured narrative and exports.",
            ]
        )
        case.updated_at = memo.generated_at
        case.timeline.append(
            {
                "timestamp": memo.generated_at,
                "event": "credit_appraisal_memo_generated",
                "details": {
                    "decision": memo.decision,
                    "risk_score": memo.overall_risk_score,
                    "artifacts": memo.artifacts,
                },
            }
        )
        await self._write_artifacts(case)
        await self._store.save_case(case)
        return memo

    async def get_case_cam_export_path(self, case_id: str, export_format: str) -> Path:
        case = await self._require_case(case_id)
        memo = case.dossier.credit_appraisal_memo
        if memo is None:
            raise HTTPException(status_code=404, detail="Credit appraisal memo not found")

        artifact_key_map = {
            "md": "markdown",
            "markdown": "markdown",
            "pdf": "pdf",
            "docx": "docx",
        }
        artifact_key = artifact_key_map.get(export_format.lower())
        if artifact_key is None:
            raise HTTPException(status_code=400, detail="Unsupported CAM export format")
        artifact_path = memo.artifacts.get(artifact_key)
        if not artifact_path:
            raise HTTPException(status_code=404, detail=f"CAM {artifact_key} export not found")

        resolved_path = self._resolve_output_path(artifact_path)
        if resolved_path is None or not resolved_path.exists():
            raise HTTPException(status_code=404, detail=f"CAM {artifact_key} export missing on disk")
        return resolved_path

    async def _require_case(self, case_id: str) -> BorrowerCase:
        case = await self._store.get_case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Borrower case not found")
        return case

    def _get_secondary_research_service(self) -> SecondaryResearchService:
        if self._secondary_research_service is None:
            self._secondary_research_service = SecondaryResearchService()
        return self._secondary_research_service

    async def _write_artifacts(self, case: BorrowerCase) -> None:
        case_dir = self._case_dir(case.case_id)
        case_dir.mkdir(parents=True, exist_ok=True)

        dossier_json_path = case_dir / "normalized_dossier.json"
        dossier_json_path.write_text(
            json.dumps(case.dossier.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )

        markdown = self._render_dossier_markdown(case)
        markdown_path = urllib.parse.unquote(
            await write_text_to_md(markdown, f"credit_case_{case.case_id}_dossier")
        )

        case.artifacts = {
            **case.artifacts,
            "dossier_json": dossier_json_path.relative_to(self._backend_root).as_posix(),
            "dossier_markdown": markdown_path.replace("\\", "/"),
        }

    def _case_dir(self, case_id: str) -> Path:
        return self._outputs_root / "credit" / "cases" / case_id

    def _generate_case_id(self, borrower_name: str) -> str:
        slug = self._slugify(borrower_name)[:30] or "borrower"
        return f"case_{slug}_{uuid4().hex[:8]}"

    def _slugify(self, value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
        return normalized.strip("-")

    def _safe_filename(self, value: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip())
        return cleaned or "borrower-file"

    def _store_uploaded_file(
        self,
        case: BorrowerCase,
        original_name: str,
        media_type: str,
        contents: bytes,
    ) -> None:
        upload_dir = self._case_dir(case.case_id) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        safe_name = self._safe_filename(original_name)
        file_id = f"file_{uuid4().hex[:10]}"
        stored_name = f"{file_id}_{safe_name}"
        destination = upload_dir / stored_name
        destination.write_bytes(contents)

        relative_path = destination.relative_to(self._backend_root).as_posix()
        extension = destination.suffix.lower()
        adapter_hint = self._pipeline.route_file(extension, media_type)

        case.uploaded_files.append(
            BorrowerFileRecord(
                file_id=file_id,
                filename=stored_name,
                original_filename=original_name,
                extension=extension,
                media_type=media_type,
                size_bytes=len(contents),
                uploaded_at=utc_now(),
                storage_path=relative_path,
                adapter_hint=adapter_hint,
            )
        )

    def _finalize_uploaded_files(self, case: BorrowerCase, uploaded_count: int) -> None:
        case.status = CaseStatus.FILES_UPLOADED.value
        case.dossier.credit_features = None
        case.dossier.credit_recommendation = None
        self._invalidate_cam(case)
        case.updated_at = utc_now()
        case.timeline.append(
            {
                "timestamp": case.updated_at,
                "event": "files_uploaded",
                "details": {"file_count": uploaded_count},
            }
        )

    def _invalidate_cam(self, case: BorrowerCase) -> None:
        case.dossier.credit_appraisal_memo = None
        for artifact_key in ("cam_markdown", "cam_pdf", "cam_docx"):
            case.artifacts.pop(artifact_key, None)

    def _resolve_output_path(self, path_value: str) -> Path | None:
        decoded = urllib.parse.unquote(path_value)
        candidate = Path(decoded)
        candidates = [
            candidate,
            self._backend_root / decoded,
            self._backend_root.parent / decoded,
            Path.cwd() / decoded,
        ]
        for item in candidates:
            if item.exists():
                return item
        return None

    def _render_dossier_markdown(self, case: BorrowerCase) -> str:
        lines = [
            f"# Intelli-Credit Borrower Dossier: {case.borrower_name}",
            "",
            "## Case",
            f"- Case ID: `{case.case_id}`",
            f"- Status: `{case.status}`",
            f"- Uploaded files: {len(case.uploaded_files)}",
            "",
            "## Borrower",
            f"- Legal name: {case.dossier.borrower.legal_name or 'Unknown'}",
            "",
            "## Documents",
        ]

        if not case.dossier.documents:
            lines.append("- No documents have been ingested yet.")
        else:
            for document in case.dossier.documents:
                lines.extend(
                    [
                        f"### {document.filename}",
                        f"- Adapter: `{document.adapter}`",
                        f"- Category: `{document.category}`",
                        f"- Placeholder: `{document.placeholder}`",
                        f"- Preview: {document.extracted_fields.get('text_excerpt', 'No preview available')}",
                        "",
                    ]
                )

        lines.extend(["## Open Items"])
        if not case.dossier.open_items:
            lines.append("- None.")
        else:
            for item in case.dossier.open_items:
                lines.append(f"- {item.title}: {item.description}")

        lines.extend(["", "## Structured Documents"])
        if not case.dossier.structured_documents:
            lines.append("- No structured financial documents parsed yet.")
        else:
            for document in case.dossier.structured_documents:
                lines.append(
                    f"- `{document.document_type}` from `{document.source_filename}` "
                    f"(confidence {document.confidence:.2f}, metrics {len(document.metrics)})"
                )

        lines.extend(["", "## Credit Features"])
        if not case.dossier.credit_features:
            lines.append("- Credit features have not been computed yet.")
        else:
            features = case.dossier.credit_features
            lines.append(
                f"- Turnover trend: `{features.turnover_revenue.turnover_trend or 'unknown'}`"
            )
            lines.append(
                f"- GST vs bank: `{features.gst_bank_consistency.consistency_band}`"
            )
            lines.append(
                f"- Circular trading suspicion: `{features.circular_trading.suspicion_level}`"
            )
            lines.append(
                f"- Revenue inflation risk: `{features.revenue_inflation.inflation_risk}`"
            )
            lines.append(
                f"- Liquidity band: `{features.liquidity_leverage.liquidity_band}`"
            )

        lines.extend(["", "## Secondary Research"])
        research = case.dossier.secondary_research
        lines.append(f"- Status: `{research.status}`")
        lines.append(f"- Provider: `{research.provider}`")
        if research.coverage_note:
            lines.append(f"- Coverage note: {research.coverage_note}")
        if research.message:
            lines.append(f"- Message: {research.message}")
        if not research.findings:
            lines.append("- No secondary research findings available yet.")
        else:
            for finding in research.findings:
                lines.append(f"### {finding.topic.replace('_', ' ').title()}")
                lines.append(f"- Status: `{finding.status}`")
                if finding.summary:
                    lines.append(f"- Summary: {finding.summary}")
                if finding.message:
                    lines.append(f"- Message: {finding.message}")
                if finding.risk_flags:
                    lines.append(f"- Risk flags: {', '.join(finding.risk_flags)}")
                if finding.source_urls:
                    lines.append(f"- Source URLs: {', '.join(finding.source_urls)}")
                if not finding.evidence:
                    lines.append("- Evidence: none.")
                else:
                    for evidence in finding.evidence:
                        lines.append(
                            f"- Evidence: {evidence.title} ({evidence.source_url or 'source unavailable'})"
                        )

        lines.extend(["", "## Qualitative Credit Officer Notes"])
        notes = case.dossier.qualitative_credit_officer_notes
        note_fields = {
            "Factory operating capacity": notes.factory_operating_capacity,
            "Management quality": notes.management_quality,
            "Governance concerns": notes.governance_concerns,
            "Collateral observations": notes.collateral_observations,
            "Site visit comments": notes.site_visit_comments,
            "Additional comments": notes.additional_comments,
        }
        if not any(note_fields.values()):
            lines.append("- No qualitative notes captured yet.")
        else:
            for label, value in note_fields.items():
                lines.append(f"- {label}: {value or 'Not provided'}")

        lines.extend(["", "## Extracted Risk Flags"])
        if not case.dossier.risk_flags:
            lines.append("- None.")
        else:
            for flag in case.dossier.risk_flags:
                lines.append(
                    f"- [{flag.get('severity', 'unknown')}] {flag.get('category', 'general')}: {flag.get('description', 'No description')}"
                )

        lines.extend(["", "## Credit Recommendation"])
        recommendation = case.dossier.credit_recommendation
        if recommendation is None:
            lines.append("- Credit recommendation has not been generated yet.")
        else:
            lines.append(f"- Decision: `{recommendation.decision}`")
            lines.append(f"- Overall risk score: `{recommendation.overall_risk_score}`")
            lines.append(
                f"- Recommended loan limit: `{recommendation.recommended_loan_limit.currency} {recommendation.recommended_loan_limit.amount:,.2f}`"
            )
            lines.append(
                f"- Pricing premium: `{recommendation.pricing.risk_premium_bps} bps`"
            )
            lines.append(f"- Summary: {recommendation.explanation.executive_summary}")
            if recommendation.top_positive_drivers:
                lines.append("- Top positive drivers:")
                for driver in recommendation.top_positive_drivers:
                    lines.append(
                        f"  - {driver.label}: {driver.rationale} (impact {driver.impact_points:.1f})"
                    )
            if recommendation.top_negative_drivers:
                lines.append("- Top negative drivers:")
                for driver in recommendation.top_negative_drivers:
                    lines.append(
                        f"  - {driver.label}: {driver.rationale} (impact {driver.impact_points:.1f})"
                    )

        lines.extend(["", "## Credit Appraisal Memo"])
        memo = case.dossier.credit_appraisal_memo
        if memo is None:
            lines.append("- Credit appraisal memo has not been generated yet.")
        else:
            lines.append(f"- Decision: `{memo.decision}`")
            lines.append(f"- Overall risk score: `{memo.overall_risk_score:.1f}`")
            lines.append(
                f"- Recommended limit: `{memo.recommended_limit.currency} {memo.recommended_limit.amount:,.2f}`"
            )
            lines.append(f"- Risk premium: `{memo.pricing.risk_premium_bps} bps`")
            if memo.artifacts:
                lines.append(
                    "- Artifacts: "
                    + ", ".join(f"{label} -> {path}" for label, path in memo.artifacts.items())
                )

        return "\n".join(lines)
