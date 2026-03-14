from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .appraisal.demo import run_synthetic_cam_demo
from .case_models import (
    CreateBorrowerCaseRequest,
    RunSecondaryResearchRequest,
    UpsertQualitativeCreditOfficerNotesRequest,
)
from .case_service import CreditCaseService
from .case_store import CreditCaseStore


backend_root = Path(__file__).resolve().parents[1]
store_path = Path(
    os.getenv("CREDIT_CASE_STORE_PATH", backend_root / "data" / "credit_cases.json")
)
credit_service = CreditCaseService(CreditCaseStore(store_path), backend_root)

router = APIRouter(prefix="/api/credit", tags=["credit"])


@router.get("/cases")
async def list_cases():
    cases = await credit_service.list_cases()
    return {"cases": [case.model_dump(mode="json") for case in cases]}


@router.post("/cases")
async def create_case(request: CreateBorrowerCaseRequest):
    case = await credit_service.create_case(request)
    return {"case": case.model_dump(mode="json")}


@router.get("/cases/{case_id}")
async def get_case(case_id: str):
    case = await credit_service.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Borrower case not found")
    return {"case": case.model_dump(mode="json")}


@router.post("/cases/{case_id}/files")
async def upload_case_files(case_id: str, files: list[UploadFile] = File(...)):
    case = await credit_service.upload_files(case_id, files)
    return {"case": case.model_dump(mode="json")}


@router.post("/cases/{case_id}/ingest")
async def ingest_case(case_id: str):
    case = await credit_service.ingest_case(case_id)
    return {"case": case.model_dump(mode="json")}


@router.post("/cases/{case_id}/features")
async def compute_case_features(case_id: str):
    features = await credit_service.compute_case_features(case_id)
    return {"case_id": case_id, "features": features.model_dump(mode="json")}


@router.post("/cases/{case_id}/notes")
async def save_qualitative_notes(
    case_id: str,
    request: UpsertQualitativeCreditOfficerNotesRequest,
):
    case = await credit_service.save_qualitative_notes(case_id, request)
    return {
        "case": case.model_dump(mode="json"),
        "notes": case.dossier.qualitative_credit_officer_notes.model_dump(mode="json"),
    }


@router.post("/cases/{case_id}/research")
async def run_secondary_research(
    case_id: str,
    request: RunSecondaryResearchRequest | None = None,
):
    case = await credit_service.run_secondary_research(
        case_id,
        request or RunSecondaryResearchRequest(),
    )
    return {
        "case": case.model_dump(mode="json"),
        "research": case.dossier.secondary_research.model_dump(mode="json"),
    }


@router.post("/cases/{case_id}/score")
async def compute_case_recommendation(case_id: str):
    recommendation = await credit_service.compute_case_recommendation(case_id)
    case = await credit_service.get_case(case_id)
    return {
        "case": case.model_dump(mode="json") if case else None,
        "recommendation": recommendation.model_dump(mode="json"),
    }


@router.get("/cases/{case_id}/cam")
async def get_case_cam(case_id: str):
    case = await credit_service.get_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Borrower case not found")
    if case.dossier.credit_appraisal_memo is None:
        raise HTTPException(status_code=404, detail="Credit appraisal memo not found")
    return {
        "case": case.model_dump(mode="json"),
        "cam": case.dossier.credit_appraisal_memo.model_dump(mode="json"),
    }


@router.post("/cases/{case_id}/cam")
async def generate_case_cam(case_id: str):
    cam = await credit_service.generate_case_cam(case_id)
    case = await credit_service.get_case(case_id)
    return {
        "case": case.model_dump(mode="json") if case else None,
        "cam": cam.model_dump(mode="json"),
    }


@router.get("/cases/{case_id}/cam/download/{export_format}")
async def download_case_cam(case_id: str, export_format: str):
    export_path = await credit_service.get_case_cam_export_path(case_id, export_format)
    return FileResponse(export_path)


@router.post("/demo/run")
async def run_credit_demo():
    project_root = backend_root.parent
    try:
        case, cam = await run_synthetic_cam_demo(credit_service, project_root)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "case": case.model_dump(mode="json") if case else None,
        "cam": cam.model_dump(mode="json"),
    }
