from __future__ import annotations

from ..case_models import BorrowerCase
from .base import TransparentRecommendationModel
from .models import CreditRecommendationResult
from .scorecard import DeterministicScorecardModel


class CreditRecommendationEngine:
    def __init__(self, model: TransparentRecommendationModel | None = None) -> None:
        self._model = model or DeterministicScorecardModel()

    def score(self, case: BorrowerCase) -> CreditRecommendationResult:
        return self._model.score(case)
