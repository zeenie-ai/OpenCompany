"""Embedding Generator — Wave 11.D.7 inlined."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.memory.vector_store import (
    EmbeddingError,
    create_embedder,
)
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class EmbeddingGeneratorParams(BaseModel):
    chunks: List[Optional[dict]] = Field(default_factory=list)
    provider: Literal["huggingface", "openai", "ollama"] = Field(
        default="huggingface",
        description="Embedding provider. HuggingFace runs locally; OpenAI needs API key; Ollama connects to local server.",
    )
    model: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Model name (provider-specific). HuggingFace: BAAI/bge-*. OpenAI: text-embedding-3-small.",
    )
    batch_size: int = Field(
        default=32,
        ge=1,
        le=256,
        description="Number of texts per embedding batch.",
    )
    api_key: str = Field(
        default="",
        description="API key (OpenAI only).",
        json_schema_extra={
            "password": True,
            "displayOptions": {"show": {"provider": ["openai"]}},
        },
    )
    endpoint: str = Field(
        default="",
        description=(
            "Optional provider endpoint. OpenAI uses it as base_url; "
            "Ollama uses it as host."
        ),
        json_schema_extra={
            "displayOptions": {
                "show": {"provider": ["openai", "ollama"]}
            }
        },
    )

    model_config = ConfigDict(extra="ignore")


class EmbeddingGeneratorOutput(BaseModel):
    embeddings: Optional[list] = None
    embedding_count: Optional[int] = None
    dimensions: Optional[int] = None
    chunks: Optional[list] = None
    provider: Optional[str] = None
    model: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class EmbeddingGeneratorNode(ActionNode):
    type = "embeddingGenerator"
    display_name = "Embedding Generator"
    subtitle = "Vectorize"
    group = ("document",)
    description = "Generate vector embeddings from text chunks"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = EmbeddingGeneratorParams
    Output = EmbeddingGeneratorOutput

    @Operation("embed")
    async def embed(self, ctx: NodeContext, params: EmbeddingGeneratorParams) -> EmbeddingGeneratorOutput:
        chunks = params.chunks
        provider = params.provider
        model = params.model
        api_key = params.api_key

        if not chunks:
            return EmbeddingGeneratorOutput(
                embeddings=[],
                embedding_count=0,
                dimensions=0,
                chunks=[],
                provider=provider,
                model=model,
            )

        texts = [c.get("content", "") if isinstance(c, dict) else str(c) for c in chunks]
        embedder = None
        try:
            embedder = create_embedder(
                provider,
                model=model,
                api_key=api_key,
                endpoint=params.endpoint,
            )
            embeddings = await embedder.embed_documents(
                texts,
                batch_size=params.batch_size,
            )
        except (EmbeddingError, ValueError) as exc:
            raise NodeUserError(str(exc)) from exc
        finally:
            if embedder is not None:
                await embedder.aclose()

        dimensions = len(embeddings[0]) if embeddings else 0

        return EmbeddingGeneratorOutput(
            embeddings=embeddings,
            embedding_count=len(embeddings),
            dimensions=dimensions,
            chunks=chunks,
            provider=provider,
            model=model,
        )
