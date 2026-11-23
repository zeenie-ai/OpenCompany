"""Judgment plugin: the Classify node, and the providers that answer System
One judgments for ``services/judgment``.

Layout::

    classify.py        the Classify node (choice, score and noul about a text)
    _providers/        systemone (TypeSafe's Jev, also through OpenRouter)
                       and llm (the owner's chat model, as JSON)
    _credentials.py    TypeSafe; OpenRouter's key is the chat-model one

The service never imports this package: importing it registers the
providers, and the service tries whichever are registered. Triage, review
auto-accept and the Mobile element finder ask the service too.
"""

from __future__ import annotations

from services.node_output_schemas import register_output_schema

from .classify import ClassifyNode, ClassifyOutput

register_output_schema(ClassifyNode.type, ClassifyOutput)

__all__ = ["ClassifyNode", "ClassifyOutput"]
