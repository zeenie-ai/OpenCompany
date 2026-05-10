"""Per-session ``InMemoryVectorStore`` for long-term memory archival.

Note: a duplicate dict ``_memory_vector_stores`` lives in
:mod:`services.ai`. ``services.memory.state.clear_agent_session_state``
clears the live (``services.ai``) one — this module's dict is an
intentional second copy that future cleanup will consolidate. Keep both
for now; the documented duplication is preserved verbatim.
"""

from typing import Any, Dict

from core.logging import get_logger

logger = get_logger(__name__)


# Per-``session_id`` cache. The live cache used by aiAgent etc. lives
# in ``services.ai``; this dict is the package-private counterpart for
# JSONL-bridge consumers and is referenced by state-clear orchestration.
_memory_vector_stores: Dict[str, Any] = {}


def get_memory_vector_store(session_id: str):
    """Get or create an ``InMemoryVectorStore`` for a session.

    Returns ``None`` (with a warning) when the optional LangChain
    HuggingFace dep is missing — long-term archival is best-effort.
    """
    if session_id not in _memory_vector_stores:
        try:
            from langchain_core.vectorstores import InMemoryVectorStore
            from langchain_huggingface import HuggingFaceEmbeddings

            embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
            _memory_vector_stores[session_id] = InMemoryVectorStore(embeddings)
            logger.debug(f"[Memory] Created vector store for session '{session_id}'")
        except ImportError as exc:
            logger.warning(f"[Memory] Vector store not available: {exc}")
            return None
    return _memory_vector_stores[session_id]
