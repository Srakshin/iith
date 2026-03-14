from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

from gpt_researcher import GPTResearcher
from gpt_researcher.config.config import Config
from gpt_researcher.utils.llm import create_chat_completion

from ..diligence_research.models import (
    ResearchExtractionEvidence,
    ResearchExtractionFinding,
    ResearchExtractionPayload,
    SecondaryResearchJob,
)
from ..diligence_research.prompts import build_secondary_research_query, build_structuring_prompt
from ..case_models import (
    ResearchAvailability,
    SecondaryResearchEvidence,
    SecondaryResearchFinding,
    SecondaryResearchSection,
    SecondaryResearchTopic,
    utc_now,
)
from .base import SecondaryResearchProvider


RETRIEVER_REQUIREMENTS = {
    "tavily": "TAVILY_API_KEY",
    "serper": "SERPER_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "searchapi": "SEARCHAPI_API_KEY",
    "custom": "RETRIEVER_ENDPOINT",
}

LLM_REQUIREMENTS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
}

TOPIC_ORDER = [topic.value for topic in SecondaryResearchTopic]


class GPTResearcherSecondaryResearchProvider(SecondaryResearchProvider):
    name = "gpt_researcher"

    def check_availability(self) -> tuple[bool, str | None]:
        cfg = Config()
        missing: list[str] = []

        llm_requirement = LLM_REQUIREMENTS.get(cfg.smart_llm_provider)
        if llm_requirement and not self._has_env(llm_requirement):
            missing.append(llm_requirement)

        for retriever in cfg.retrievers:
            requirement = RETRIEVER_REQUIREMENTS.get(retriever)
            if requirement and not self._has_env(requirement):
                missing.append(requirement)

        missing = sorted(set(missing))
        if missing:
            return (
                False,
                "Secondary research provider configuration is incomplete. "
                f"Missing: {', '.join(missing)}.",
            )
        return True, None

    async def research(self, job: SecondaryResearchJob) -> SecondaryResearchSection:
        query = build_secondary_research_query(job)
        available, reason = self.check_availability()
        if not available:
            return self._build_unavailable_section(query, reason or "Provider configuration missing.")

        try:
            researcher = GPTResearcher(
                query=query,
                report_source="web",
                source_urls=job.source_urls or None,
                complement_source_urls=bool(job.source_urls),
                query_domains=job.query_domains or None,
                verbose=False,
            )
            await researcher.conduct_research()
            source_urls = self._dedupe_strings([*job.source_urls, *researcher.get_source_urls()])
            sources = researcher.get_research_sources()
            context = researcher.get_research_context()
        except Exception as exc:
            return self._build_unavailable_section(
                query,
                f"Research collection failed: {type(exc).__name__}: {exc}",
            )

        if not sources and not source_urls and not context:
            return self._build_unavailable_section(
                query,
                "No public-web evidence was collected for the requested borrower research topics.",
            )

        try:
            payload = await self._structure_research(job, query, sources, source_urls, context)
        except Exception as exc:
            payload = self._fallback_payload(sources, source_urls, exc)

        return self._build_section(query, payload, source_urls)

    async def _structure_research(
        self,
        job: SecondaryResearchJob,
        query: str,
        sources: list[dict[str, Any]],
        source_urls: list[str],
        context: list[Any] | str,
    ) -> ResearchExtractionPayload:
        cfg = Config()
        source_catalog = self._build_source_catalog(sources, source_urls, context)
        prompt = build_structuring_prompt(
            job,
            query,
            source_catalog,
            json.dumps(ResearchExtractionPayload.model_json_schema(), indent=2),
        )
        raw = await create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=cfg.smart_llm_model,
            llm_provider=cfg.smart_llm_provider,
            max_tokens=min(cfg.smart_token_limit, 6000),
            llm_kwargs=cfg.llm_kwargs,
            reasoning_effort=cfg.reasoning_effort,
        )
        json_payload = self._extract_json(raw)
        return ResearchExtractionPayload.model_validate(json_payload)

    def _fallback_payload(
        self,
        sources: list[dict[str, Any]],
        source_urls: list[str],
        error: Exception,
    ) -> ResearchExtractionPayload:
        findings: dict[str, ResearchExtractionFinding] = {
            topic: ResearchExtractionFinding(
                topic=topic,
                status=ResearchAvailability.UNAVAILABLE.value,
                message="No corroborated evidence captured for this topic.",
            )
            for topic in TOPIC_ORDER
        }

        normalized_sources = self._normalize_sources(sources, source_urls, [])
        for source in normalized_sources[:8]:
            topic = self._guess_topic(source["title"], source["content"])
            evidence = ResearchExtractionEvidence(
                topic=topic,
                title=source["title"] or "Public-web source",
                summary=source["content"][:320] or "Source captured without a usable summary.",
                source_url=source["url"],
                source_title=source["title"] or None,
                source_type=source["source_type"],
                risk_flags=[],
            )
            findings[topic].status = ResearchAvailability.PARTIAL.value
            findings[topic].summary = "Fallback extraction based on captured source snippets."
            findings[topic].message = (
                f"Structured extraction fallback used after provider parsing failed: {type(error).__name__}."
            )
            findings[topic].evidence.append(evidence)
            findings[topic].risk_flags = []

        return ResearchExtractionPayload(
            executive_summary=(
                "Secondary research completed with fallback extraction from public-web sources. "
                "Structured topic summarization was only partially available."
            ),
            findings=list(findings.values()),
        )

    def _build_section(
        self,
        query: str,
        payload: ResearchExtractionPayload,
        source_urls: list[str],
    ) -> SecondaryResearchSection:
        evidence_items: list[SecondaryResearchEvidence] = []
        findings: list[SecondaryResearchFinding] = []

        findings_by_topic = {finding.topic: finding for finding in payload.findings}
        for topic in TOPIC_ORDER:
            extracted = findings_by_topic.get(topic)
            if not extracted:
                findings.append(
                    SecondaryResearchFinding(
                        topic=topic,
                        status=ResearchAvailability.UNAVAILABLE.value,
                        message="No corroborated public-web evidence was extracted for this topic.",
                    )
                )
                continue

            topic_evidence: list[SecondaryResearchEvidence] = []
            for item in extracted.evidence:
                evidence = SecondaryResearchEvidence(
                    evidence_id=f"evidence_{uuid4().hex[:10]}",
                    topic=topic,
                    title=item.title,
                    summary=item.summary,
                    source_url=item.source_url,
                    source_title=item.source_title,
                    source_type=item.source_type or "web",
                    provider=self.name,
                    confidence=item.confidence,
                    observed_at=item.observed_at,
                    extracted_risk_flags=self._dedupe_strings(item.risk_flags),
                )
                evidence_items.append(evidence)
                topic_evidence.append(evidence)

            finding_source_urls = self._dedupe_strings(
                [*(item.source_url or "" for item in extracted.evidence), *source_urls]
            )
            findings.append(
                SecondaryResearchFinding(
                    topic=topic,
                    status=self._normalize_status(extracted.status),
                    summary=extracted.summary,
                    evidence=topic_evidence,
                    source_urls=finding_source_urls,
                    risk_flags=self._dedupe_strings(
                        [*extracted.risk_flags, *self._risk_flags_from_evidence(extracted.evidence)]
                    ),
                    caveats=self._dedupe_strings(extracted.caveats),
                    message=extracted.message,
                )
            )

        extracted_risk_flags = self._dedupe_strings(
            [
                *self._risk_flags_from_findings(findings),
                *(flag for evidence in evidence_items for flag in evidence.extracted_risk_flags),
            ]
        )
        deduped_urls = self._dedupe_strings(
            [*source_urls, *(url for finding in findings for url in finding.source_urls)]
        )
        statuses = {finding.status for finding in findings}
        if statuses == {ResearchAvailability.AVAILABLE.value}:
            overall_status = ResearchAvailability.AVAILABLE.value
        elif statuses == {ResearchAvailability.UNAVAILABLE.value}:
            overall_status = ResearchAvailability.UNAVAILABLE.value
        else:
            overall_status = ResearchAvailability.PARTIAL.value

        return SecondaryResearchSection(
            status=overall_status,
            provider=self.name,
            query=query,
            executed_at=utc_now(),
            evidence=evidence_items,
            findings=findings,
            source_urls=deduped_urls,
            extracted_risk_flags=extracted_risk_flags,
            coverage_note=payload.executive_summary,
            message=None if overall_status != ResearchAvailability.UNAVAILABLE.value else "Research unavailable.",
        )

    def _build_unavailable_section(self, query: str, reason: str) -> SecondaryResearchSection:
        return SecondaryResearchSection(
            status=ResearchAvailability.UNAVAILABLE.value,
            provider=self.name,
            query=query,
            executed_at=utc_now(),
            findings=[
                SecondaryResearchFinding(
                    topic=topic,
                    status=ResearchAvailability.UNAVAILABLE.value,
                    message=reason,
                )
                for topic in TOPIC_ORDER
            ],
            coverage_note=(
                "Public-web secondary research is unavailable in this environment. "
                "No direct MCA, e-Courts, or CIBIL integration is claimed."
            ),
            message=reason,
        )

    def _build_source_catalog(
        self,
        sources: list[dict[str, Any]],
        source_urls: list[str],
        context: list[Any] | str,
    ) -> str:
        normalized_sources = self._normalize_sources(sources, source_urls, context)
        lines: list[str] = []
        for index, source in enumerate(normalized_sources[:8], start=1):
            lines.extend(
                [
                    f"Source {index}",
                    f"Title: {source['title'] or 'Unknown'}",
                    f"URL: {source['url'] or 'Unavailable'}",
                    f"Type: {source['source_type']}",
                    f"Snippet: {source['content'][:1200]}",
                    "",
                ]
            )
        if not lines:
            lines.append("No scraped sources were captured. Use the supplied URLs and context cautiously.")
        return "\n".join(lines)

    def _normalize_sources(
        self,
        sources: list[dict[str, Any]],
        source_urls: list[str],
        context: list[Any] | str,
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for source in sources:
            url = (source.get("url") or source.get("href") or "").strip()
            title = (source.get("title") or source.get("name") or "").strip()
            content = (
                source.get("content")
                or source.get("body")
                or source.get("raw_content")
                or source.get("snippet")
                or ""
            )
            content = str(content).strip()
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            normalized.append(
                {
                    "url": url,
                    "title": title,
                    "content": content[:2000],
                    "source_type": "web",
                }
            )

        if not normalized and source_urls:
            context_text = self._context_to_text(context)[:1600]
            for url in source_urls:
                normalized.append(
                    {
                        "url": url,
                        "title": "",
                        "content": context_text,
                        "source_type": "web",
                    }
                )
        return normalized

    def _context_to_text(self, context: list[Any] | str) -> str:
        if isinstance(context, str):
            return context
        return "\n\n".join(str(item) for item in context)

    def _extract_json(self, raw: str) -> dict[str, Any]:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("Model did not return a JSON object.")
        return json.loads(raw[start : end + 1])

    def _risk_flags_from_findings(self, findings: list[SecondaryResearchFinding]) -> list[str]:
        return [flag for finding in findings for flag in finding.risk_flags]

    def _risk_flags_from_evidence(
        self, evidence: list[ResearchExtractionEvidence]
    ) -> list[str]:
        return [flag for item in evidence for flag in item.risk_flags]

    def _normalize_status(self, status: str) -> str:
        if status in {
            ResearchAvailability.AVAILABLE.value,
            ResearchAvailability.PARTIAL.value,
            ResearchAvailability.UNAVAILABLE.value,
        }:
            return status
        return ResearchAvailability.PARTIAL.value

    def _guess_topic(self, title: str, content: str) -> str:
        text = f"{title} {content}".lower()
        if any(keyword in text for keyword in ("promoter", "director", "founder", "group company")):
            return SecondaryResearchTopic.PROMOTERS.value
        if any(
            keyword in text
            for keyword in ("court", "litigation", "nclt", "sarfaesi", "tribunal", "dispute", "recovery")
        ):
            return SecondaryResearchTopic.LITIGATION.value
        if any(
            keyword in text
            for keyword in ("mca", "roc", "registrar", "regulatory", "sebi", "rbi", "ministry", "filing")
        ):
            return SecondaryResearchTopic.MCA_REGULATORY_EVIDENCE.value
        if any(
            keyword in text
            for keyword in ("sector", "industry", "headwind", "demand", "inflation", "raw material", "cyclical")
        ):
            return SecondaryResearchTopic.SECTOR_HEADWINDS.value
        return SecondaryResearchTopic.COMPANY.value

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            cleaned = value.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            deduped.append(cleaned)
        return deduped

    def _has_env(self, env_name: str) -> bool:
        if env_name == "OPENAI_API_KEY" and os.getenv("OPENAI_BASE_URL"):
            return True
        return bool(os.getenv(env_name))
