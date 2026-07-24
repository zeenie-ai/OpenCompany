"""Plugin-shape invariants — locks the modular architecture.

The chat-model service layer follows the project's plugin pattern
(Wave 11.H): each LLM provider self-registers into the registry and the
unifier dispatches by name. These tests fail loudly if anyone bolts
per-provider Python back into ``services/ai.py``.

Lifecycle: the tests run via ``pytest`` (no server start needed). They
introspect source files with ``ast`` + regex — same shape as
``tests/test_credential_broadcasts.py`` and ``tests/test_plugin_self_containment.py``.
"""

import ast
import re
from pathlib import Path

import pytest

import services.llm  # noqa: F401 — populate registry
from services.llm.registry import all_providers, get_provider


SERVER_ROOT = Path(__file__).resolve().parent.parent.parent
AI_PY = SERVER_ROOT / "services" / "ai.py"
PROVIDERS_DIR = SERVER_ROOT / "services" / "llm" / "providers"


# ---------------------------------------------------------------------------
# 1. Every registered provider has a corresponding module under providers/
# ---------------------------------------------------------------------------


def test_every_provider_has_a_module_or_is_registered_by_compat():
    """Plugin-folder invariant: registry membership implies an on-disk module.

    Dedicated providers (anthropic / openai / gemini / openrouter) live
    in their own file. OpenAI-compat providers (xai / deepseek / kimi /
    mistral / ollama / lmstudio / groq / cerebras) all register through
    ``_compat.py`` using the same ``OpenAIProvider`` factory.
    """
    dedicated_files = {p.stem for p in PROVIDERS_DIR.glob("*.py") if not p.name.startswith("_")}
    for provider in all_providers():
        spec = get_provider(provider)
        # Either the provider has its own file, OR it's a compat
        # provider registered by ``_compat.py`` with ``OpenAIProvider``.
        if provider in dedicated_files:
            continue
        # Otherwise the factory must be ``OpenAIProvider`` (the compat shape)
        from services.llm.providers.openai import OpenAIProvider

        assert spec.factory is OpenAIProvider, (
            f"provider {provider!r} has no dedicated module and doesn't use "
            f"OpenAIProvider — registered factory is {spec.factory!r}"
        )


# ---------------------------------------------------------------------------
# 2. No per-provider Python inside execute_chat / fetch_models bodies
# ---------------------------------------------------------------------------
#
# Chat and agent entry points all delegate through the native unifier.
# AST-walking each method body avoids false positives from the isolated
# pre-cutover Temporal compatibility factory that remains elsewhere.


def _method_source(class_name: str, method_name: str, source: str) -> str:
    """Return the source text of one method inside one class."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method_name:
                    return ast.get_source_segment(source, item) or ""
    pytest.fail(f"{class_name}.{method_name} not found in services/ai.py")


_FORBIDDEN_PATTERNS_IN_UNIFIED_METHODS = (
    # Per-provider except blocks (unifier owns these now)
    r"except\s+openai\.OpenAIError",
    r"except\s+anthropic\.",
    r"except\s+google\.genai\.",
    # Per-provider equality checks (registry membership is the gate)
    r'if\s+provider\s*==\s*[\'\"]openai[\'\"]\b',
    r'if\s+provider\s*==\s*[\'\"]anthropic[\'\"]\b',
    r'if\s+provider\s*==\s*[\'\"]gemini[\'\"]\b',
    r'if\s+provider\s*==\s*[\'\"]openrouter[\'\"]\b',
    r'if\s+provider\s*==\s*[\'\"]groq[\'\"]\b',
    r'if\s+provider\s*==\s*[\'\"]cerebras[\'\"]\b',
    # Direct calls to legacy factory — the unifier owns provider instantiation
    r"create_provider\s*\(",
    # Native-vs-LangChain gating — Phase D moved every provider into
    # the unifier so this branch is gone. Phase G removes it from the
    # agent paths too; until then this contract only covers the
    # chat-model methods.
    r"is_native_provider\s*\(",
    # LangChain ChatModel construction — Phase D removed this from
    # the chat-model methods. Phase G removes it from the agent paths.
    r"self\.create_model\s*\(",
    r"chat_model\.invoke\s*\(",
    r"chat_model\.ainvoke\s*\(",
)


@pytest.mark.parametrize(
    "method_name",
    ["execute_chat", "fetch_models", "execute_agent", "execute_chat_agent"],
)
def test_unified_methods_have_no_per_provider_python(method_name):
    """Every new-run LLM entry point must delegate to the native unifier."""
    source = AI_PY.read_text(encoding="utf-8")
    method_src = _method_source("AIService", method_name, source)
    for pattern in _FORBIDDEN_PATTERNS_IN_UNIFIED_METHODS:
        matches = re.findall(pattern, method_src)
        assert not matches, (
            f"AIService.{method_name} contains forbidden per-provider pattern "
            f"{pattern!r}: {matches!r}. The unifier owns this responsibility — "
            f"per-provider logic belongs in the provider's plugin folder."
        )


# ---------------------------------------------------------------------------
# 3. ProviderSpec.sdk_exception_types is non-empty for every registered provider
# ---------------------------------------------------------------------------


def test_every_provider_declares_sdk_exception_types():
    """The unifier needs the typed-error tuple to translate SDK errors.

    An empty tuple would silently fall through to the generic
    ``except Exception`` in ``BaseNode.execute()`` and emit a full
    traceback for what should be a user-correctable error.
    """
    for provider in all_providers():
        spec = get_provider(provider)
        assert spec.sdk_exception_types, (
            f"provider {provider!r} has empty sdk_exception_types — typed "
            f"SDK errors would surface as bare RuntimeErrors with tracebacks "
                f"instead of clean NodeUserError envelopes."
            )
        # And every tuple member must be an exception class
        for exc in spec.sdk_exception_types:
            assert isinstance(exc, type) and issubclass(exc, BaseException), (
                f"provider {provider!r} sdk_exception_types contains a non-exception: {exc!r}"
            )


def test_all_registered_providers_are_agent_selectable():
    """Agent surfaces must not silently omit a native provider."""

    from typing import get_args

    from nodes.agent._specialized import SpecializedAgentParams
    from nodes.agent.ai_agent import AIAgentParams
    from nodes.agent.chat_agent import ChatAgentParams

    expected = set(all_providers())
    for params_type in (AIAgentParams, ChatAgentParams, SpecializedAgentParams):
        annotation = params_type.model_fields["provider"].annotation
        assert set(get_args(annotation)) == expected


# ---------------------------------------------------------------------------
# 4. ai.py imports ChatUnifier somewhere (delegation contract)
# ---------------------------------------------------------------------------


def test_ai_service_init_accepts_chat_unifier_kwarg():
    """``AIService.__init__`` must accept ``chat_unifier`` so DI can wire it."""
    source = AI_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AIService":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    arg_names = {a.arg for a in item.args.args}
                    assert "chat_unifier" in arg_names, (
                        "AIService.__init__ does not accept chat_unifier — "
                        "the DI container can't inject the unifier."
                    )
                    return
            pytest.fail("AIService class found but has no __init__")
    pytest.fail("AIService class not found in services/ai.py")
