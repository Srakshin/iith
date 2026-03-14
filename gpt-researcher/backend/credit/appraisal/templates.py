from __future__ import annotations

from .models import CreditAppraisalMemo, CreditAppraisalMemoSection


def render_credit_appraisal_memo_markdown(memo: CreditAppraisalMemo) -> str:
    lines = [
        "# Credit Appraisal Memo",
        "",
        f"Prepared for **{memo.borrower_name}**",
        "",
        "## Recommendation Snapshot",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Borrower | {memo.borrower_name} |",
        f"| Case ID | `{memo.case_id}` |",
        f"| Decision | `{memo.decision}` |",
        f"| Overall risk score | `{memo.overall_risk_score:.1f}/100` |",
        f"| Risk band | `{memo.risk_band}` |",
        f"| Recommended limit | `{memo.recommended_limit.currency} {memo.recommended_limit.amount:,.2f}` |",
        f"| Risk premium | `{memo.pricing.risk_premium_bps} bps` |",
        f"| Generated at | `{memo.generated_at}` |",
        "",
    ]

    lines.extend(_render_section(memo.borrower_overview))
    lines.extend(["## Five Cs of Credit", ""])
    lines.extend(_render_section(memo.five_cs.character, level=3))
    lines.extend(_render_section(memo.five_cs.capacity, level=3))
    lines.extend(_render_section(memo.five_cs.capital, level=3))
    lines.extend(_render_section(memo.five_cs.collateral, level=3))
    lines.extend(_render_section(memo.five_cs.conditions, level=3))
    lines.extend(_render_section(memo.key_financial_findings))
    lines.extend(_render_section(memo.gst_bank_reconciliation_findings))
    lines.extend(_render_section(memo.research_findings_and_flags))
    lines.extend(_render_section(memo.primary_due_diligence_notes))
    lines.extend(_render_section(memo.final_recommendation))
    lines.extend(_render_section(memo.decision_rationale))

    if memo.assumptions:
        lines.extend(["## Assumptions", ""])
        for assumption in memo.assumptions:
            lines.append(f"- {assumption}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _render_section(section: CreditAppraisalMemoSection, level: int = 2) -> list[str]:
    header = "#" * level
    lines = [f"{header} {section.title}", ""]

    if section.assessment:
        lines.append(f"- Assessment: `{section.assessment}`")
    if section.summary:
        if section.assessment:
            lines.append("")
        lines.append(section.summary)
        lines.append("")

    for bullet in section.bullet_points:
        lines.append(f"- {bullet}")

    if section.evidence:
        if section.bullet_points:
            lines.append("")
        lines.append("**Evidence Trail**")
        lines.append("")
        for evidence in section.evidence:
            lines.append(f"- {_format_evidence_reference(evidence)}")

    lines.append("")
    return lines


def _format_evidence_reference(evidence) -> str:
    pieces = [evidence.label]
    if evidence.reference:
        pieces.append(evidence.reference)
    if evidence.source_path:
        pieces.append(f"Path: `{evidence.source_path}`")
    if evidence.source_url:
        pieces.append(f"[Source]({evidence.source_url})")
    return " | ".join(piece for piece in pieces if piece)
