from __future__ import annotations

from typing import Any

from ..ingestion.models import StructuredDocumentRecord
from ..case_models import QualitativeCreditOfficerNotes, SecondaryResearchSection


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def build_dossier_risk_flags(
    documents,
    structured_documents: list[StructuredDocumentRecord],
    secondary_research: SecondaryResearchSection | None = None,
    qualitative_notes: QualitativeCreditOfficerNotes | None = None,
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for document in documents:
        if document.placeholder:
            flags.append(
                {
                    "severity": "medium",
                    "category": "data_quality",
                    "document_id": document.document_id,
                    "description": f"{document.filename} is currently backed by placeholder extraction output.",
                }
            )

    for document in structured_documents:
        for flag in document.flags:
            severity = "medium"
            if flag in {"default_notice", "recovery_action", "auditor_qualification"}:
                severity = "high"
            flags.append(
                {
                    "severity": severity,
                    "category": "structured_signal",
                    "document_id": document.source_document_id,
                    "description": f"{document.document_type} triggered structured flag `{flag}`.",
                }
            )

    if secondary_research:
        for finding in secondary_research.findings:
            for risk_flag in finding.risk_flags:
                flags.append(
                    {
                        "severity": "medium",
                        "category": "secondary_research",
                        "topic": finding.topic,
                        "description": f"Secondary research flagged `{risk_flag}` under {finding.topic}.",
                    }
                )

    if qualitative_notes:
        notes_map = {
            "factory_operating_capacity": qualitative_notes.factory_operating_capacity,
            "management_quality": qualitative_notes.management_quality,
            "governance_concerns": qualitative_notes.governance_concerns,
            "collateral_observations": qualitative_notes.collateral_observations,
            "site_visit_comments": qualitative_notes.site_visit_comments,
            "additional_comments": qualitative_notes.additional_comments,
        }
        concern_keywords = (
            "delay",
            "stress",
            "weak",
            "concern",
            "issue",
            "non-compliant",
            "irregular",
            "dispute",
            "shortfall",
            "underutilized",
            "shutdown",
        )
        for field_name, note in notes_map.items():
            if not note:
                continue
            lowered = note.lower()
            if field_name == "governance_concerns" or any(keyword in lowered for keyword in concern_keywords):
                flags.append(
                    {
                        "severity": "medium" if field_name != "governance_concerns" else "high",
                        "category": "qualitative_note",
                        "topic": field_name,
                        "description": f"Credit officer note on {field_name.replace('_', ' ')} requires review.",
                    }
                )

    return _dedupe_flag_dicts(flags)


def _dedupe_flag_dicts(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for flag in flags:
        key = (
            flag.get("severity"),
            flag.get("category"),
            flag.get("document_id"),
            flag.get("topic"),
            flag.get("description"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(flag)
    return deduped
