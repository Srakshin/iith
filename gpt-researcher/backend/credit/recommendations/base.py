from __future__ import annotations

from typing import Protocol

from ..case_models import BorrowerCase
from .models import CreditRecommendationResult


class TransparentRecommendationModel(Protocol):
    model_name: str
    model_version: str
    implementation_type: str

    def score(self, case: BorrowerCase) -> CreditRecommendationResult: ...
