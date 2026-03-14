from __future__ import annotations

from ..case_models import (
    BorrowerCase,
    ResearchAvailability,
    RunSecondaryResearchRequest,
    SecondaryResearchFinding,
    SecondaryResearchSection,
    SecondaryResearchTopic,
    utc_now,
)
from .models import SecondaryResearchJob
from ..providers.base import SecondaryResearchProvider


class UnavailableSecondaryResearchProvider(SecondaryResearchProvider):
    name = "gpt_researcher"

    def __init__(self, reason: str):
        self._reason = reason

    def check_availability(self) -> tuple[bool, str | None]:
        return False, self._reason

    async def research(self, job: SecondaryResearchJob) -> SecondaryResearchSection:
        return SecondaryResearchSection(
            status=ResearchAvailability.UNAVAILABLE.value,
            provider=self.name,
            query=job.legal_name or job.borrower_name,
            executed_at=utc_now(),
            findings=[
                SecondaryResearchFinding(
                    topic=topic.value,
                    status=ResearchAvailability.UNAVAILABLE.value,
                    message=self._reason,
                )
                for topic in SecondaryResearchTopic
            ],
            coverage_note=(
                "Public-web secondary research is unavailable in this environment. "
                "No direct MCA, e-Courts, or CIBIL integration is implied."
            ),
            message=self._reason,
        )


class SecondaryResearchService:
    def __init__(self, provider: SecondaryResearchProvider | None = None):
        if provider is None:
            try:
                from ..providers.gpt_researcher_provider import GPTResearcherSecondaryResearchProvider

                provider = GPTResearcherSecondaryResearchProvider()
            except Exception as exc:
                provider = UnavailableSecondaryResearchProvider(
                    "Secondary research provider dependencies are unavailable: "
                    f"{type(exc).__name__}: {exc}"
                )
        self._provider = provider

    async def run(
        self,
        case: BorrowerCase,
        request: RunSecondaryResearchRequest | None = None,
    ) -> SecondaryResearchSection:
        job = SecondaryResearchJob(
            borrower_name=case.borrower_name,
            legal_name=case.dossier.borrower.legal_name,
            industry=case.dossier.borrower.industry,
            source_urls=request.source_urls if request else [],
            query_domains=request.query_domains if request else [],
        )
        return await self._provider.research(job)
