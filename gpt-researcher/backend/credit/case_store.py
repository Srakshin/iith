from __future__ import annotations

from pathlib import Path

from server.report_store import ReportStore

from .case_models import BorrowerCase


class CreditCaseStore:
    def __init__(self, path: Path):
        self._store = ReportStore(path)

    async def list_cases(self) -> list[BorrowerCase]:
        cases = await self._store.list_reports()
        validated = [BorrowerCase.model_validate(case) for case in cases]
        return sorted(validated, key=lambda case: case.updated_at, reverse=True)

    async def get_case(self, case_id: str) -> BorrowerCase | None:
        case = await self._store.get_report(case_id)
        if case is None:
            return None
        return BorrowerCase.model_validate(case)

    async def save_case(self, case: BorrowerCase) -> BorrowerCase:
        await self._store.upsert_report(case.case_id, case.model_dump(mode="json"))
        return case
