from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from PIL import Image

from .base import BaseDocumentAdapter, ExtractionPayload, build_text_signals


_SURYA_RUNTIME: dict[str, Any] | None = None


class SuryaAdapter(BaseDocumentAdapter):
    name = "surya"

    def _ensure_import_path(self) -> None:
        workspace_root = Path(__file__).resolve().parents[4]
        repo_root = workspace_root / "surya"
        repo_path = str(repo_root)
        if repo_root.exists() and repo_path not in sys.path:
            sys.path.insert(0, repo_path)

    def _get_runtime(self) -> dict[str, Any]:
        global _SURYA_RUNTIME

        if _SURYA_RUNTIME is not None:
            return _SURYA_RUNTIME

        self._ensure_import_path()
        foundation_module = importlib.import_module("surya.foundation")
        recognition_module = importlib.import_module("surya.recognition")
        detection_module = importlib.import_module("surya.detection")

        foundation_predictor = foundation_module.FoundationPredictor()
        _SURYA_RUNTIME = {
            "recognition_predictor": recognition_module.RecognitionPredictor(foundation_predictor),
            "detection_predictor": detection_module.DetectionPredictor(),
        }

        try:
            layout_module = importlib.import_module("surya.layout")
            settings_module = importlib.import_module("surya.settings")
            _SURYA_RUNTIME["layout_predictor"] = layout_module.LayoutPredictor(
                foundation_module.FoundationPredictor(
                    checkpoint=settings_module.settings.LAYOUT_MODEL_CHECKPOINT
                )
            )
        except Exception:
            _SURYA_RUNTIME["layout_predictor"] = None

        return _SURYA_RUNTIME

    async def extract(self, file_path: Path) -> ExtractionPayload:
        warnings: list[str] = []

        try:
            runtime = self._get_runtime()
            with Image.open(file_path) as image:
                page = image.convert("RGB")

            ocr_results = runtime["recognition_predictor"](
                [page],
                det_predictor=runtime["detection_predictor"],
                return_words=True,
            )
            ocr_result = ocr_results[0]
            text = "\n".join(line.text for line in ocr_result.text_lines)

            pages = [
                {
                    "page": 1,
                    "image_bbox": ocr_result.image_bbox,
                    "text_line_count": len(ocr_result.text_lines),
                }
            ]

            layout_labels: list[str] = []
            layout_predictor = runtime.get("layout_predictor")
            if layout_predictor is not None:
                try:
                    layout_result = layout_predictor([page])[0]
                    pages[0]["layout_blocks"] = [
                        box.model_dump(mode="json") for box in layout_result.bboxes
                    ]
                    layout_labels = sorted({box.label for box in layout_result.bboxes})
                except Exception as layout_exc:
                    warnings.append(f"Surya layout analysis skipped: {layout_exc}")

            payload = ExtractionPayload(
                adapter=self.name,
                placeholder=False,
                extracted_text=text,
                extracted_fields={
                    **build_text_signals(text),
                    "text_line_count": len(ocr_result.text_lines),
                    "layout_labels": layout_labels,
                },
                pages=pages,
                metadata={
                    "engine_available": True,
                    "engine": self.name,
                    "format": file_path.suffix.lower(),
                },
                warnings=warnings,
            )

            if not text.strip():
                payload.placeholder = True
                payload.warnings.append(
                    "Surya completed, but OCR text was empty. Returning a structured placeholder."
                )

            return payload
        except Exception as exc:
            placeholder_text = (
                f"Placeholder OCR for {file_path.name}. "
                "This image is queued for local Surya processing when models are available."
            )
            return ExtractionPayload(
                adapter=self.name,
                placeholder=True,
                extracted_text=placeholder_text,
                extracted_fields={
                    **build_text_signals(placeholder_text),
                    "text_line_count": 0,
                    "fallback_used": True,
                },
                pages=[{"page": 1, "image_bbox": []}],
                warnings=[
                    f"Surya extraction was unavailable for {file_path.name}: {exc}. Returned placeholder content."
                ],
                metadata={
                    "engine_available": False,
                    "engine": self.name,
                    "error": str(exc),
                    "format": file_path.suffix.lower(),
                },
            )
