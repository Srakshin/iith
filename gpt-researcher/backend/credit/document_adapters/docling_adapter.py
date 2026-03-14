from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from .base import BaseDocumentAdapter, ExtractionPayload, build_text_signals, read_plaintext_fallback


_DOCLING_CONVERTER: Any | None = None


class DoclingAdapter(BaseDocumentAdapter):
    name = "docling"

    def _ensure_import_path(self) -> None:
        workspace_root = Path(__file__).resolve().parents[4]
        repo_root = workspace_root / "docling"
        repo_path = str(repo_root)
        if repo_root.exists() and repo_path not in sys.path:
            sys.path.insert(0, repo_path)

    def _get_converter(self) -> Any:
        global _DOCLING_CONVERTER

        if _DOCLING_CONVERTER is not None:
            return _DOCLING_CONVERTER

        self._ensure_import_path()
        document_converter = importlib.import_module("docling.document_converter")
        _DOCLING_CONVERTER = document_converter.DocumentConverter()
        return _DOCLING_CONVERTER

    async def extract(self, file_path: Path) -> ExtractionPayload:
        warnings: list[str] = []

        try:
            converter = self._get_converter()
            result = converter.convert(str(file_path))
            document = result.document
            markdown = ""
            if hasattr(document, "export_to_markdown"):
                markdown = document.export_to_markdown()

            exported = document.export_to_dict() if hasattr(document, "export_to_dict") else {}
            page_count = len(getattr(result, "pages", []) or [])

            payload = ExtractionPayload(
                adapter=self.name,
                placeholder=False,
                extracted_text=markdown,
                extracted_fields={
                    **build_text_signals(markdown),
                    "page_count": page_count,
                    "docling_root_keys": sorted(exported.keys()) if isinstance(exported, dict) else [],
                },
                pages=[{"page": index + 1} for index in range(page_count)],
                metadata={
                    "engine_available": True,
                    "engine": self.name,
                    "format": file_path.suffix.lower(),
                },
            )

            if not markdown.strip():
                payload.placeholder = True
                payload.warnings.append(
                    "Docling completed, but no text was extracted. Returning a structured placeholder."
                )

            return payload
        except Exception as exc:
            fallback_text = read_plaintext_fallback(file_path)
            warnings.append(
                f"Docling extraction was unavailable for {file_path.name}: {exc}. Returned placeholder content."
            )
            placeholder_text = fallback_text or (
                f"Placeholder extraction for {file_path.name}. "
                "This document is queued for deeper parsing when Docling is available."
            )
            return ExtractionPayload(
                adapter=self.name,
                placeholder=True,
                extracted_text=placeholder_text,
                extracted_fields={
                    **build_text_signals(placeholder_text),
                    "page_count": 0,
                    "fallback_used": True,
                },
                warnings=warnings,
                metadata={
                    "engine_available": False,
                    "engine": self.name,
                    "error": str(exc),
                    "format": file_path.suffix.lower(),
                },
            )
