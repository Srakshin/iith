from __future__ import annotations

from pathlib import Path

from ..case_models import (
    CreateBorrowerCaseRequest,
    RunSecondaryResearchRequest,
    UpsertQualitativeCreditOfficerNotesRequest,
)


DEMO_BORROWER_NAME = "Synthetic Components Private Limited"
DEMO_EXTERNAL_REFERENCE = "INTELLI-CREDIT-PHASE5-DEMO"
DEMO_FIXTURE_FILENAMES = [
    "synthetic_gst_return.txt",
    "synthetic_bank_statement.txt",
    "synthetic_financial_statement.txt",
    "synthetic_itr.txt",
    "synthetic_sanction_letter.txt",
    "synthetic_annual_report.txt",
    "synthetic_legal_notice.txt",
]
DEMO_NOTE_PACK = UpsertQualitativeCreditOfficerNotesRequest(
    factory_operating_capacity="Observed at roughly 68% utilization with one idle line awaiting repair.",
    management_quality="Second line management appears capable and responsive during diligence meetings.",
    governance_concerns="Related-party procurement approvals were not fully documented on site.",
    collateral_observations="Charged machinery appears installed and tagged, though insurance copies were pending.",
    site_visit_comments="Inventory movement was lower than monthly sales run-rate and needs reconciliation.",
    additional_comments="Use the memo as a demo CAM and keep legal and collateral perfection checks as production follow-ups.",
    updated_by="phase5-demo",
)


def synthetic_fixture_paths(project_root: Path) -> list[Path]:
    fixture_root = project_root / "tests" / "fixtures" / "credit"
    return [fixture_root / filename for filename in DEMO_FIXTURE_FILENAMES]


async def run_synthetic_cam_demo(credit_service, project_root: Path):
    fixture_paths = synthetic_fixture_paths(project_root)
    missing = [path for path in fixture_paths if not path.exists()]
    if missing:
        missing_paths = ", ".join(path.as_posix() for path in missing)
        raise FileNotFoundError(f"Synthetic credit demo fixtures are missing: {missing_paths}")

    case = await credit_service.create_case(
        CreateBorrowerCaseRequest(
            borrower_name=DEMO_BORROWER_NAME,
            external_reference=DEMO_EXTERNAL_REFERENCE,
        )
    )
    await credit_service.upload_local_files(case.case_id, fixture_paths)
    await credit_service.ingest_case(case.case_id)
    await credit_service.compute_case_features(case.case_id)
    await credit_service.save_qualitative_notes(case.case_id, DEMO_NOTE_PACK)
    await credit_service.run_secondary_research(case.case_id, RunSecondaryResearchRequest())
    await credit_service.compute_case_recommendation(case.case_id)
    memo = await credit_service.generate_case_cam(case.case_id)
    updated_case = await credit_service.get_case(case.case_id)
    return updated_case, memo
