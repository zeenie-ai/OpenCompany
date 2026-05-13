"""Document Parser — Wave 11.D.7 inlined."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


def _parse_file_sync(path: Path, parser: str) -> str:
    """Synchronous file parsing — runs in thread pool."""
    if parser == 'pypdf':
        from pypdf import PdfReader
        return "\n\n".join(p.extract_text() or '' for p in PdfReader(str(path)).pages)
    if parser == 'marker':
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        converter = PdfConverter(artifact_dict=create_model_dict())
        result = converter(str(path))
        return result.markdown if hasattr(result, 'markdown') else str(result)
    if parser == 'unstructured':
        from unstructured.partition.auto import partition
        return "\n\n".join(str(el) for el in partition(str(path)))
    if parser == 'beautifulsoup':
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(path.read_text(errors='ignore'), 'html.parser')
        for s in soup(["script", "style"]):
            s.decompose()
        return soup.get_text(separator='\n')
    raise ValueError(f"Unknown parser: {parser}")


class DocumentParserParams(BaseModel):
    parser: Literal["pypdf", "marker", "unstructured", "beautifulsoup"] = Field(
        default="pypdf",
        description="Parser backend. pypdf is fast; marker uses GPU OCR; unstructured handles multi-format; beautifulsoup for HTML.",
    )
    file_path: str = Field(
        default="",
        description="Single file path (takes precedence over input_dir).",
    )
    input_dir: str = Field(
        default="",
        description="Directory to scan for files matching file_pattern.",
    )
    file_pattern: str = Field(
        default="*.pdf",
        description="Glob pattern for input_dir scan (e.g. *.pdf, *.html).",
    )

    model_config = ConfigDict(extra="ignore")


class DocumentParserOutput(BaseModel):
    documents: Optional[list] = None
    parsed_count: Optional[int] = None
    failed: Optional[list] = None

    model_config = ConfigDict(extra="allow")


class DocumentParserNode(ActionNode):
    type = "documentParser"
    display_name = "Document Parser"
    subtitle = "Parse to Text"
    group = ("document",)
    description = "Parse documents to text using configurable parsers"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = DocumentParserParams
    Output = DocumentParserOutput

    @Operation("parse")
    async def parse(self, ctx: NodeContext, params: DocumentParserParams) -> DocumentParserOutput:
        parser = params.parser
        explicit_path = params.file_path
        input_dir = params.input_dir
        file_pattern = params.file_pattern or '*.pdf'

        paths: list[Path] = []
        if explicit_path:
            paths.append(Path(explicit_path))
        if input_dir and Path(input_dir).exists():
            paths.extend(Path(input_dir).glob(file_pattern))

        if not paths:
            return DocumentParserOutput(documents=[], parsed_count=0, failed=[])

        documents, failed = [], []
        for path in paths:
            try:
                content = await asyncio.to_thread(_parse_file_sync, path, parser)
                documents.append({
                    'source': str(path), 'filename': path.name,
                    'content': content, 'length': len(content), 'parser': parser,
                })
            except Exception as e:
                failed.append({'file': str(path), 'error': str(e)})

        return DocumentParserOutput(
            documents=documents, parsed_count=len(documents), failed=failed,
        )
