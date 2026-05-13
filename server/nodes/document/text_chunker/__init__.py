"""Text Chunker — Wave 11.D.7 inlined."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class TextChunkerParams(BaseModel):
    text: str = Field(default="")
    strategy: Literal["recursive", "markdown", "token"] = "recursive"
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    overlap: int = Field(default=200, ge=0, le=1000)

    model_config = ConfigDict(extra="allow")


class TextChunkerOutput(BaseModel):
    chunks: Optional[list] = None
    chunk_count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class TextChunkerNode(ActionNode):
    type = "textChunker"
    display_name = "Text Chunker"
    subtitle = "Chunk Text"
    group = ("document",)
    description = "Split text into overlapping chunks for embedding"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = TextChunkerParams
    Output = TextChunkerOutput

    @Operation("chunk")
    async def chunk(self, ctx: NodeContext, params: TextChunkerParams) -> TextChunkerOutput:
        from langchain_text_splitters import (
            MarkdownTextSplitter, RecursiveCharacterTextSplitter,
        )

        p = params.model_dump()
        documents = p.get('documents') or ([{'content': p.get('text', '')}] if p.get('text') else [])
        chunk_size = int(p.get('chunkSize', 1000))
        chunk_overlap = int(p.get('chunkOverlap') or p.get('overlap', 200))
        strategy = p.get('strategy', 'recursive')

        if not documents:
            return TextChunkerOutput(chunks=[], chunk_count=0)

        if strategy == 'markdown':
            splitter = MarkdownTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        else:
            splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        chunks = []
        for doc in documents:
            content = doc.get('content', '') if isinstance(doc, dict) else str(doc)
            source = doc.get('source', 'input') if isinstance(doc, dict) else 'input'
            if not content:
                continue
            for i, chunk_text in enumerate(splitter.split_text(content)):
                chunks.append({
                    'source': source, 'chunk_index': i,
                    'content': chunk_text, 'length': len(chunk_text),
                })

        return TextChunkerOutput(chunks=chunks, chunk_count=len(chunks))
