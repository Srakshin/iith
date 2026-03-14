from __future__ import annotations

from abc import ABC, abstractmethod

from ..diligence_research.models import SecondaryResearchJob
from ..case_models import SecondaryResearchSection


class SecondaryResearchProvider(ABC):
    name = "secondary_research_provider"

    @abstractmethod
    def check_availability(self) -> tuple[bool, str | None]:
        raise NotImplementedError

    @abstractmethod
    async def research(self, job: SecondaryResearchJob) -> SecondaryResearchSection:
        raise NotImplementedError
