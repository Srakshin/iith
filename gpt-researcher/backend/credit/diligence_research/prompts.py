from __future__ import annotations

from .models import SecondaryResearchJob


TOPIC_GUIDANCE = {
    "company": "Company profile, operating momentum, business model resilience, and borrower-specific developments.",
    "promoters": "Promoter background, group reputation, governance track record, and management stewardship.",
    "sector_headwinds": "India-specific sector pressures such as input inflation, policy changes, cyclicality, customer concentration, exports, or demand weakness.",
    "litigation": "Litigation, insolvency, enforcement, recovery actions, disputed dues, tribunal matters, or material legal notices.",
    "mca_regulatory_evidence": "Public MCA or regulatory-style evidence such as ministry pages, stock exchange disclosures, ROC-style references, regulator notices, or other filing-oriented records that are publicly visible on the web.",
}


def build_secondary_research_query(job: SecondaryResearchJob) -> str:
    borrower = job.legal_name or job.borrower_name
    industry_hint = (
        f" Industry hint: {job.industry}."
        if job.industry
        else " Industry is not confirmed yet, so infer cautiously from public sources."
    )
    return (
        f"Indian corporate credit diligence for {borrower}.{industry_hint} "
        "Gather public-web evidence for company background, promoter reputation, sector headwinds, "
        "litigation or enforcement, and MCA or regulatory-style records. "
        "Prioritize source-backed facts that a credit officer can cite in a borrower dossier. "
        "Do not imply direct integration with MCA, e-Courts, or CIBIL systems unless the source explicitly proves it."
    )


def build_structuring_prompt(
    job: SecondaryResearchJob,
    query: str,
    source_catalog: str,
    schema_json: str,
) -> str:
    borrower = job.legal_name or job.borrower_name
    return f"""
You are a senior Indian corporate credit analyst preparing structured secondary research for a borrower dossier.

Borrower: {borrower}
Research query: {query}
Industry hint: {job.industry or "Unknown"}

Task:
1. Read the source snippets below.
2. Extract only source-backed evidence relevant to these topics:
   - company
   - promoters
   - sector_headwinds
   - litigation
   - mca_regulatory_evidence
3. Summarize findings for a credit officer.

Hard rules:
- Use only the supplied snippets and URLs.
- If evidence is weak or indirect, say so in caveats or the message field.
- Never claim direct MCA, e-Courts, or CIBIL integration. Treat this as public-web research only.
- Keep risk flags short and machine-readable, such as governance_concern, litigation_signal, leverage_pressure.
- Prefer 0-3 evidence items per topic.
- If a topic has no reliable evidence, mark it unavailable and explain briefly.

Return JSON only that matches this schema:
{schema_json}

Source snippets:
{source_catalog}
""".strip()
